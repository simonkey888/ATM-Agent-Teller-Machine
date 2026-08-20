#!/usr/bin/env bash
set -euo pipefail
set +x

REPO="simonkey888/ATM-Agent-Teller-Machine"
SHA="${ATM_SOURCE_SHA:-}"
PAYOUT="${ATM_BASE_WALLET_ADDRESS:-}"
CFG="${OCI_CLI_CONFIG_FILE:-}"
PROFILE="${OCI_CLI_PROFILE:-}"

fail(){ echo "ORDER_002C_REMOTE_STATUS=BLOCKED_REAL reason=$1" >&2; exit "${2:-1}"; }

[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || fail ATM_SOURCE_SHA_MUST_BE_EXACT_40HEX 2
[[ "$PAYOUT" =~ ^0x[0-9a-fA-F]{40}$ ]] || fail ATM_BASE_WALLET_ADDRESS_MUST_BE_CANONICAL_PUBLIC_BASE_ADDRESS 2
[ "${ATM_OCI_REMOTE_EXECUTOR:-}" = "github_actions" ] || fail REMOTE_EXECUTOR_MARKER_REQUIRED 3
[ -n "$CFG" ] && [ -f "$CFG" ] || fail OCI_API_CONFIG_MISSING 3
[ -n "$PROFILE" ] || fail OCI_API_PROFILE_MISSING 3

for x in oci curl jq python3 ssh ssh-keygen; do
  command -v "$x" >/dev/null || fail "MISSING_$x" 4
done

oci iam region-subscription list --config-file "$CFG" --profile "$PROFILE" --output json >/dev/null 2>&1 || fail OCI_API_AUTH_FAILED 3

echo "OCI_REMOTE_AUTH=PASS profile=$PROFILE sha=$SHA"

D="$(mktemp -d)"
cleanup(){
  if [ -f "$D/infra.env" ]; then
    set +u
    # shellcheck disable=SC1090
    source "$D/infra.env"
    set -u
    if [ -n "${SECURITY_LIST_ID:-}" ] && [ -n "${REGION:-}" ] && [ -n "${EGRESS_JSON:-}" ]; then
      oci network security-list update \
        --config-file "$CFG" --profile "$PROFILE" \
        --region "$REGION" \
        --security-list-id "$SECURITY_LIST_ID" \
        --egress-security-rules "$EGRESS_JSON" \
        --ingress-security-rules '[]' --force >/dev/null 2>&1 || true
    fi
  fi
  rm -rf "$D"
}
trap cleanup EXIT INT TERM

RAW="https://raw.githubusercontent.com/$REPO/$SHA"
for f in oci-provision.sh oci-provision-runner.sh oci-normalize-bv.py oci-normalize-compute.py oci-compute-usage-sdk.py oci-authority.py oci-configure.sh; do
  curl -fsSL "$RAW/scripts/$f" -o "$D/$f"
  chmod 700 "$D/$f"
done

export ATM_SOURCE_SHA="$SHA"
export ATM_BASE_WALLET_ADDRESS="$PAYOUT"
export ATM_BOOT_DIR="$D"
export ATM_REPO="$REPO"
export ATM_RAW_BASE="$RAW"
export OCI_CLI_CONFIG_FILE="$CFG"
export OCI_CLI_PROFILE="$PROFILE"
export OCI_CLI_AUTH=api_key

"$D/oci-provision-runner.sh" "$D/infra.env"
# A prior failed promotion may have deliberately stopped the canonical A1.
# Reuse it instead of allocating a second instance, and never start any non-A1 shape.
# shellcheck disable=SC1090
source "$D/infra.env"
LIFECYCLE="$(oci compute instance get --config-file "$CFG" --profile "$PROFILE" --region "$REGION" --instance-id "$INSTANCE_ID" --query 'data."lifecycle-state"' --raw-output)" || fail OCI_INSTANCE_STATE_LOOKUP
case "$LIFECYCLE" in
  RUNNING) ;;
  STOPPED)
    echo 'OCI_CANONICAL_A1=RESTARTING_FROM_SAFE_ROLLBACK'
    oci compute instance action --config-file "$CFG" --profile "$PROFILE" --region "$REGION" --instance-id "$INSTANCE_ID" --action START --wait-for-state RUNNING --max-wait-seconds 900 >/dev/null || fail OCI_CANONICAL_A1_RESTART
    ;;
  *) fail "OCI_CANONICAL_A1_UNEXPECTED_STATE_$LIFECYCLE" ;;
esac
"$D/oci-configure.sh" "$D/infra.env"

#!/usr/bin/env bash
set -euo pipefail
set +x
REPO="simonkey888/ATM-Agent-Teller-Machine"
SHA="${ATM_SOURCE_SHA:-}"
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "ORDER_002C_BOOTSTRAP_STATUS=BLOCKED_REAL reason=ATM_SOURCE_SHA_MUST_BE_EXACT_40HEX" >&2; exit 2; }
[ "${OCI_CLI_AUTH:-}" = "instance_obo_user" ] || { echo "ORDER_002C_BOOTSTRAP_STATUS=BLOCKED_REAL reason=RUN_FROM_OCI_CLOUD_SHELL" >&2; exit 3; }
for x in oci curl jq python3 ssh ssh-keygen; do command -v "$x" >/dev/null || { echo "ORDER_002C_BOOTSTRAP_STATUS=BLOCKED_REAL reason=MISSING_$x" >&2; exit 4; }; done
D="$(mktemp -d)"; trap 'rm -rf "$D"' EXIT
RAW="https://raw.githubusercontent.com/$REPO/$SHA"
for f in oci-provision.sh oci-configure.sh; do curl -fsSL "$RAW/scripts/$f" -o "$D/$f"; chmod 700 "$D/$f"; done
export ATM_SOURCE_SHA="$SHA" ATM_BOOT_DIR="$D" ATM_REPO="$REPO" ATM_RAW_BASE="$RAW"
"$D/oci-provision.sh" "$D/infra.env"
"$D/oci-configure.sh" "$D/infra.env"

#!/usr/bin/env bash
set -euo pipefail
set +x

REPO="simonkey888/ATM-Agent-Teller-Machine"
SHA="${ATM_SOURCE_SHA:-}"
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "ORDER_002C_BOOTSTRAP_STATUS=BLOCKED_REAL reason=ATM_SOURCE_SHA_MUST_BE_EXACT_40HEX" >&2; exit 2; }

command -v oci >/dev/null 2>&1 || { echo "ORDER_002C_BOOTSTRAP_STATUS=BLOCKED_REAL reason=OCI_CLI_NOT_FOUND_USE_ORACLE_CLOUD_SHELL" >&2; exit 2; }
[ "${OCI_CLI_AUTH:-}" = "instance_obo_user" ] || { echo "ORDER_002C_BOOTSTRAP_STATUS=BLOCKED_REAL reason=OCI_CLOUD_SHELL_PREAUTH_REQUIRED" >&2; exit 2; }
oci iam region-subscription list --all >/dev/null 2>&1 || { echo "ORDER_002C_BOOTSTRAP_STATUS=BLOCKED_REAL reason=OCI_CLOUD_SHELL_PREAUTH_FAILED" >&2; exit 2; }

# Fresh-session one-shot: public payout + the two required secrets are entered
# once via hidden stdin, never placed on the command line or shell history.
read -rsp 'Paste WALLET|GitHubPAT|GOOGLE_API_KEY once (not echoed): ' BUNDLE
echo
case "$BUNDLE" in
  *'|'*'|'*) ;;
  *) echo "ORDER_002C_BOOTSTRAP_STATUS=BLOCKED_REAL reason=BUNDLE_FORMAT_WALLET_PIPE_PAT_PIPE_GOOGLE_KEY" >&2; exit 2 ;;
esac
PAYOUT="${BUNDLE%%|*}"
REST="${BUNDLE#*|}"
GH_ONCE="${REST%%|*}"
MODEL_ONCE="${REST#*|}"
unset BUNDLE REST
[[ "$PAYOUT" =~ ^0x[0-9a-fA-F]{40}$ ]] || { echo "ORDER_002C_BOOTSTRAP_STATUS=BLOCKED_REAL reason=ATM_BASE_WALLET_ADDRESS_MUST_BE_CANONICAL_PUBLIC_BASE_ADDRESS" >&2; exit 2; }
[ -n "$GH_ONCE" ] || { echo "ORDER_002C_BOOTSTRAP_STATUS=BLOCKED_REAL reason=GITHUB_TOKEN_EMPTY" >&2; exit 2; }
[ -n "$MODEL_ONCE" ] || { echo "ORDER_002C_BOOTSTRAP_STATUS=BLOCKED_REAL reason=MODEL_KEY_EMPTY" >&2; exit 2; }

umask 077
ATM_BOOTSTRAP_CRED_FILE="$(mktemp)"
trap 'rm -f "$ATM_BOOTSTRAP_CRED_FILE"' EXIT INT TERM
printf '%s\n%s\n' "$GH_ONCE" "$MODEL_ONCE" >"$ATM_BOOTSTRAP_CRED_FILE"
unset GH_ONCE MODEL_ONCE
export ATM_BOOTSTRAP_CRED_FILE

# oci-configure.sh uses Bash read -rsp for GH and MODELKEY. Intercept exactly
# those two variables and delegate every other read call to the Bash builtin.
read() {
  local target="${!#}" value=""
  case "$target" in
    GH)
      value="$(sed -n '1p' "$ATM_BOOTSTRAP_CRED_FILE")"
      printf -v GH '%s' "$value"
      return 0
      ;;
    MODELKEY)
      value="$(sed -n '2p' "$ATM_BOOTSTRAP_CRED_FILE")"
      printf -v MODELKEY '%s' "$value"
      rm -f "$ATM_BOOTSTRAP_CRED_FILE"
      return 0
      ;;
    *)
      builtin read "$@"
      ;;
  esac
}
export -f read

RAW="https://raw.githubusercontent.com/$REPO/$SHA"
ATM_BASE_WALLET_ADDRESS="$PAYOUT" ATM_SOURCE_SHA="$SHA" bash <(curl -fsSL "$RAW/scripts/bootstrap-oci-atm.sh")

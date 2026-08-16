#!/usr/bin/env bash
set -euo pipefail
set +x

REPO="simonkey888/ATM-Agent-Teller-Machine"
SHA="${ATM_SOURCE_SHA:-}"
BUNDLE="${ATM_REMOTE_BUNDLE:-}"

fail(){ echo "ORDER_002C_REMOTE_STATUS=BLOCKED_REAL reason=$1" >&2; exit "${2:-1}"; }

[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || fail ATM_SOURCE_SHA_MUST_BE_EXACT_40HEX 2
case "$BUNDLE" in
  *'|'*'|'*) ;;
  *) fail BUNDLE_FORMAT_WALLET_PIPE_PAT_PIPE_GOOGLE_KEY 2 ;;
esac

PAYOUT="${BUNDLE%%|*}"
REST="${BUNDLE#*|}"
GH_ONCE="${REST%%|*}"
MODEL_ONCE="${REST#*|}"
unset BUNDLE REST ATM_REMOTE_BUNDLE

[[ "$PAYOUT" =~ ^0x[0-9a-fA-F]{40}$ ]] || fail ATM_BASE_WALLET_ADDRESS_MUST_BE_CANONICAL_PUBLIC_BASE_ADDRESS 2
[ -n "$GH_ONCE" ] || fail GITHUB_TOKEN_EMPTY 2
[ -n "$MODEL_ONCE" ] || fail MODEL_KEY_EMPTY 2

umask 077
ATM_BOOTSTRAP_CRED_FILE="$(mktemp)"
cleanup_secret(){ rm -f "$ATM_BOOTSTRAP_CRED_FILE"; }
trap cleanup_secret EXIT INT TERM
printf '%s\n%s\n' "$GH_ONCE" "$MODEL_ONCE" >"$ATM_BOOTSTRAP_CRED_FILE"
unset GH_ONCE MODEL_ONCE
export ATM_BOOTSTRAP_CRED_FILE

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
    *) builtin read "$@" ;;
  esac
}
export -f read

RAW="https://raw.githubusercontent.com/$REPO/$SHA"
export ATM_OCI_REMOTE_EXECUTOR=github_actions
ATM_BASE_WALLET_ADDRESS="$PAYOUT" ATM_SOURCE_SHA="$SHA" bash <(curl -fsSL "$RAW/scripts/bootstrap-oci-atm-api.sh")

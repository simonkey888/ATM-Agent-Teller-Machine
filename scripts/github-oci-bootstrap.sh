#!/usr/bin/env bash
set -euo pipefail
set +x

REPO="simonkey888/ATM-Agent-Teller-Machine"
SHA="${ATM_SOURCE_SHA:-}"
BUNDLE="${ATM_REMOTE_BUNDLE:-}"

fail(){ echo "ORDER_002C_REMOTE_STATUS=BLOCKED_REAL reason=$1" >&2; exit "${2:-1}"; }

[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || fail ATM_SOURCE_SHA_MUST_BE_EXACT_40HEX 2
[ -n "$BUNDLE" ] || fail ATM_AGENT_TELLER_MACHINE_EMPTY 2

umask 077
PARSED_FILE="$(mktemp)"
export PARSED_FILE
python3 - <<'PY'
import json, os, re, sys
from pathlib import Path

raw = os.environ.get("ATM_REMOTE_BUNDLE", "").replace("\r", "").strip()
out = Path(os.environ["PARSED_FILE"])

wallet = gh = model = ""

# 1) canonical single-line pipe form.
parts = raw.split("|")
if len(parts) == 3:
    wallet, gh, model = [p.strip() for p in parts]

# 2) JSON object form.
if not (wallet and gh and model) and raw.startswith("{"):
    try:
        d = json.loads(raw)
    except Exception:
        d = None
    if isinstance(d, dict):
        def pick(*keys):
            for k in keys:
                v = d.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
            return ""
        wallet = pick("wallet", "payout", "ATM_BASE_WALLET_ADDRESS", "CANONICAL_ATM_PAYOUT_ADDRESS")
        gh = pick("github_pat", "github_token", "GITHUB_PAT", "GITHUB_TOKEN", "GH_TOKEN", "pat")
        model = pick("google_api_key", "GOOGLE_API_KEY", "gemini_api_key", "GEMINI_API_KEY", "model_key")

# 3) key=value / key: value multiline form.
if not (wallet and gh and model):
    kv = {}
    plain = []
    for line in [x.strip() for x in raw.splitlines() if x.strip()]:
        m = re.match(r"^([A-Za-z0-9_ -]+)\s*[:=]\s*(.+)$", line)
        if m:
            kv[m.group(1).strip().upper().replace(" ", "_").replace("-", "_")] = m.group(2).strip().strip('"\'')
        else:
            plain.append(line.strip().strip('"\''))
    def pickkv(*keys):
        for k in keys:
            v = kv.get(k)
            if v:
                return v
        return ""
    wallet = wallet or pickkv("WALLET", "PAYOUT", "ATM_BASE_WALLET_ADDRESS", "CANONICAL_ATM_PAYOUT_ADDRESS")
    gh = gh or pickkv("GITHUB_PAT", "GITHUB_TOKEN", "GH_TOKEN", "PAT")
    model = model or pickkv("GOOGLE_API_KEY", "GEMINI_API_KEY", "MODEL_KEY", "MODELKEY")
    if not (wallet and gh and model) and len(plain) == 3:
        wallet, gh, model = plain

# 4) three non-empty lines in order, even if labels were partially present.
if not (wallet and gh and model):
    lines = [x.strip().strip('"\'') for x in raw.splitlines() if x.strip()]
    if len(lines) == 3 and all(("=" not in x and ":" not in x) for x in lines):
        wallet, gh, model = lines

if not re.fullmatch(r"0x[0-9a-fA-F]{40}", wallet or ""):
    raise SystemExit("ATM_AGENT_TELLER_MACHINE_WALLET_UNRESOLVED")
if not gh:
    raise SystemExit("ATM_AGENT_TELLER_MACHINE_GITHUB_TOKEN_UNRESOLVED")
if not model:
    raise SystemExit("ATM_AGENT_TELLER_MACHINE_GOOGLE_KEY_UNRESOLVED")

out.write_text(wallet + "\n" + gh + "\n" + model + "\n")
os.chmod(out, 0o600)
PY
parse_rc=$?
if [ "$parse_rc" -ne 0 ]; then
  rm -f "$PARSED_FILE"
  fail ATM_AGENT_TELLER_MACHINE_FORMAT_UNRESOLVED 2
fi
mapfile -t FIELDS <"$PARSED_FILE"
rm -f "$PARSED_FILE"
unset PARSED_FILE BUNDLE ATM_REMOTE_BUNDLE

PAYOUT="${FIELDS[0]:-}"
GH_ONCE="${FIELDS[1]:-}"
MODEL_ONCE="${FIELDS[2]:-}"
unset FIELDS

[[ "$PAYOUT" =~ ^0x[0-9a-fA-F]{40}$ ]] || fail ATM_BASE_WALLET_ADDRESS_MUST_BE_CANONICAL_PUBLIC_BASE_ADDRESS 2
[ -n "$GH_ONCE" ] || fail GITHUB_TOKEN_EMPTY 2
[ -n "$MODEL_ONCE" ] || fail MODEL_KEY_EMPTY 2

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

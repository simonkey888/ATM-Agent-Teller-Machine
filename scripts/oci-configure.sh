#!/usr/bin/env bash
set -euo pipefail
set +x
source "${1:?infra env required}"
SHA="${ATM_SOURCE_SHA:?}"; REPO="${ATM_REPO:?}"; CONTROL=4; OBS=7
CFG="${OCI_CLI_CONFIG_FILE:-}"; PROFILE="${OCI_CLI_PROFILE:-ATM_REMOTE}"
PROMOTED=0; READY=0; AUTH_EPOCH=""; STATE_PAR=""; AUTH_PAR=""
fail(){ echo "ORDER_002C_BOOTSTRAP_STATUS=BLOCKED_REAL reason=$1" >&2; exit "${2:-1}"; }
o(){ oci "$@" --region "$REGION"; }
cleanup_ingress(){ o network security-list update --security-list-id "$SECURITY_LIST_ID" --egress-security-rules "$EGRESS_JSON" --ingress-security-rules '[]' --force >/dev/null 2>&1 || true; }
rollback_promotion(){
  [ "$PROMOTED" -eq 1 ] && [ "$READY" -eq 0 ] || return 0
  echo 'OCI_AUTHORITY_ROLLBACK=START reason=POST_PROMOTION_FAILURE' >&2
  "${SSH[@]}" 'sudo systemctl disable --now atm-supervisor.service atm-controller.service >/dev/null 2>&1 || true' >/dev/null 2>&1 || true
  set +e
  o compute instance action --instance-id "$INSTANCE_ID" --action STOP --wait-for-state STOPPED --max-wait-seconds 600 >/dev/null 2>&1
  stop_rc=$?
  set -e
  if [ "$stop_rc" -ne 0 ]; then
    echo 'OCI_AUTHORITY_ROLLBACK=FENCED reason=INSTANCE_STOP_UNPROVEN' >&2
    return 0
  fi
  if python3 "$ATM_BOOT_DIR/oci-authority.py" --config-file "$CFG" --profile "$PROFILE" rollback --instance-id "$INSTANCE_ID" --source-sha "$SHA" >/dev/null; then
    echo 'OCI_AUTHORITY_ROLLBACK=PASS holder=GITHUB_ACTIONS instance_state=STOPPED' >&2
  else
    echo 'OCI_AUTHORITY_ROLLBACK=FENCED reason=CAS_ROLLBACK_FAILED' >&2
  fi
}
on_exit(){ rc=$?; rollback_promotion || true; cleanup_ingress; exit "$rc"; }
trap on_exit EXIT INT TERM
SSH=(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -i "$SSH_KEY" "ubuntu@$PUBLIC_IP")

ready=0
for _ in $(seq 1 90); do "${SSH[@]}" 'test -f /var/lib/atm/cloud-init-ready' >/dev/null 2>&1 && { ready=1; break; }; sleep 10; done
[ "$ready" -eq 1 ] || fail CLOUD_INIT_NOT_READY
[ "$("${SSH[@]}" 'git -C /opt/atm rev-parse HEAD')" = "$SHA" ] || fail REMOTE_SHA_MISMATCH
"${SSH[@]}" 'test -x /var/lib/atm/.local/bin/hermes' || fail HERMES_NOT_INSTALLED

# Resume-safe secret sourcing. Secrets remain local/remote files only and are never printed.
GH="${GITHUB_TOKEN:-${GH_TOKEN:-}}"
if [ -z "$GH" ]; then
  GH="$("${SSH[@]}" 'sudo sh -c '\''[ -f /etc/atm/control.env ] || exit 0; set -a; . /etc/atm/control.env >/dev/null 2>&1; printf %s "${GITHUB_TOKEN:-}"'\''' 2>/dev/null || true)"
fi
if [ -z "$GH" ] && command -v gh >/dev/null 2>&1; then GH="$(gh auth token 2>/dev/null || true)"; fi
if [ -z "$GH" ]; then read -rsp 'GitHub classic PAT with public_repo scope (not echoed): ' GH; echo; fi
[ -n "$GH" ] || fail GITHUB_TOKEN_EMPTY
[ "$(curl -fsS -H "Authorization: Bearer $GH" https://api.github.com/user|jq -r .login)" = simonkey888 ] || fail GITHUB_TOKEN_INVALID

MODELKEY="${GOOGLE_API_KEY:-}"
if [ -z "$MODELKEY" ]; then
  MODELKEY="$("${SSH[@]}" 'sudo sh -c '\''[ -f /etc/atm/runtime.env ] || exit 0; set -a; . /etc/atm/runtime.env >/dev/null 2>&1; printf %s "${GOOGLE_API_KEY:-}"'\''' 2>/dev/null || true)"
fi
if [ -z "$MODELKEY" ]; then read -rsp 'GOOGLE_API_KEY for Hermes (not echoed): ' MODELKEY; echo; fi
[ -n "$MODELKEY" ] || fail MODEL_KEY_EMPTY
PAYOUT="${ATM_BASE_WALLET_ADDRESS:-}"
[[ "$PAYOUT" =~ ^0x[0-9a-fA-F]{40}$ ]] || fail ATM_BASE_WALLET_ADDRESS_INVALID

# Cloud base must exist before OCI can become economic authority.
NS="$(o os ns get --query data --raw-output)" || fail OBJECT_STORAGE_NAMESPACE
BUCKET="atm-state-$(printf %s "$TENANCY"|sha256sum|cut -c1-12)"
o os bucket get --namespace-name "$NS" --bucket-name "$BUCKET" >/dev/null || fail CLOUD_STATE_BUCKET_MISSING
o os object head --namespace-name "$NS" --bucket-name "$BUCKET" --name atm-cloud-state.tgz >/dev/null || fail CLOUD_BASE_STATE_OBJECT_MISSING
o os object head --namespace-name "$NS" --bucket-name "$BUCKET" --name atm-authority.json >/dev/null || fail CLOUD_BASE_AUTHORITY_OBJECT_MISSING
EXP="$(date -u -d '+365 days' +%Y-%m-%dT%H:%M:%SZ)"
STATE_URI="$(o os preauth-request create --namespace-name "$NS" --bucket-name "$BUCKET" --name "atm-cloud-state-rw-$(date +%s)" --access-type ObjectReadWrite --object-name atm-cloud-state.tgz --time-expires "$EXP" --query 'data."access-uri"' --raw-output)" || fail CLOUD_STATE_PAR_CREATE
AUTH_URI="$(o os preauth-request create --namespace-name "$NS" --bucket-name "$BUCKET" --name "atm-authority-read-$(date +%s)" --access-type ObjectRead --object-name atm-authority.json --time-expires "$EXP" --query 'data."access-uri"' --raw-output)" || fail AUTHORITY_READ_PAR_CREATE
STATE_PAR="https://objectstorage.$REGION.oraclecloud.com$STATE_URI"
AUTH_PAR="https://objectstorage.$REGION.oraclecloud.com$AUTH_URI"

# Ensure no OCI economic process is alive before restore/promotion.
"${SSH[@]}" 'sudo systemctl disable --now atm-supervisor.service atm-controller.service >/dev/null 2>&1 || true'
{
 printf 'GITHUB_TOKEN=%s\n' "$GH"
 printf 'ATM_HOST_CLASS=OCI\nATM_SOURCE_SHA=%s\n' "$SHA"
}|"${SSH[@]}" 'sudo sh -c "umask 077; mkdir -p /etc/atm; cat >/etc/atm/control.env; chmod 600 /etc/atm/control.env"'
{
 printf 'GOOGLE_API_KEY=%s\n' "$MODELKEY"
 printf 'ATM_HOST_CLASS=OCI\nATM_SOURCE_SHA=%s\n' "$SHA"
}|"${SSH[@]}" 'sudo sh -c "umask 077; cat >/etc/atm/runtime.env; chown root:atm /etc/atm/runtime.env; chmod 640 /etc/atm/runtime.env"'
printf "ATM_STATE_PAR_URL='%s'\nATM_SOURCE_SHA='%s'\n" "$STATE_PAR" "$SHA"|"${SSH[@]}" 'sudo sh -c "umask 077; cat >/etc/atm/state-backup.env; chmod 600 /etc/atm/state-backup.env"'
printf '%s\n' "$PAYOUT"|"${SSH[@]}" 'sudo python3 -c '\''import json,sys;p="/var/lib/atm/atm.json";d=json.load(open(p));d["payment_recipient_public_identifier"]=sys.stdin.readline().strip();open(p,"w").write(json.dumps(d,indent=2)+"\n")'\''; sudo chown atm:atm /var/lib/atm/atm.json; sudo chmod 600 /var/lib/atm/atm.json'
unset MODELKEY

# Restore the last cloud checkpoint before any authority mutation.
"${SSH[@]}" 'sudo bash -c "set -a; source /etc/atm/state-backup.env; set +a; /opt/atm/scripts/atm-state-backup.sh restore"' >/dev/null || fail CLOUD_STATE_RESTORE
"${SSH[@]}" 'sudo -u atm /opt/atm/.venv/bin/python /opt/atm/src/atm_v2.py --status >/dev/null' || fail OCI_PREPROMOTION_STATUS
"${SSH[@]}" 'sudo systemctl is-active --quiet atm-supervisor.service && exit 9 || exit 0' || fail PREPROMOTION_SUPERVISOR_NOT_STOPPED

# Establish a new anti-replay floor before the OCI controller is allowed to start.
FLOOR="$(curl -fsS -H "Authorization: Bearer $GH" "https://api.github.com/repos/$REPO/issues/$CONTROL/comments?per_page=100"|jq '[.[].id]|max//0')" || fail CONTROL_FLOOR_LOOKUP
[[ "$FLOOR" =~ ^[0-9]+$ ]] || fail CONTROL_FLOOR_INVALID
{
 printf 'GITHUB_TOKEN=%s\n' "$GH"
 printf 'ATM_HOST_CLASS=OCI\nATM_SOURCE_SHA=%s\nATM_IGNORE_COMMENT_ID=%s\n' "$SHA" "$FLOOR"
}|"${SSH[@]}" 'sudo sh -c "umask 077; cat >/etc/atm/control.env; chmod 600 /etc/atm/control.env"'

# Atomic cloud handover. Shared GitHub concurrency serializes this against GHA cycles;
# the CAS helper additionally refuses a live GHA lease.
PROMO_JSON="$(python3 "$ATM_BOOT_DIR/oci-authority.py" --config-file "$CFG" --profile "$PROFILE" promote --instance-id "$INSTANCE_ID" --source-sha "$SHA")" || fail AUTHORITY_PROMOTION_CAS
AUTH_EPOCH="$(jq -r '.epoch // empty' <<<"$PROMO_JSON")"
[[ "$AUTH_EPOCH" =~ ^[1-9][0-9]*$ ]] || fail AUTHORITY_PROMOTION_EPOCH_INVALID
PROMOTED=1
{
 printf 'ATM_AUTHORITY_READ_URL=%s\n' "$AUTH_PAR"
 printf 'ATM_CANONICAL_OCI_INSTANCE_OCID=%s\n' "$INSTANCE_ID"
 printf 'ATM_AUTHORITY_EPOCH=%s\n' "$AUTH_EPOCH"
 printf 'ATM_SOURCE_SHA=%s\n' "$SHA"
}|"${SSH[@]}" 'sudo sh -c "umask 077; cat >/etc/atm/authority.env; chown root:atm /etc/atm/authority.env; chmod 640 /etc/atm/authority.env"'
"${SSH[@]}" 'sudo -u atm /opt/atm/.venv/bin/python /opt/atm/scripts/atm-authority-fence.py' >/dev/null || fail AUTHORITY_FENCE_REMOTE
python3 "$ATM_BOOT_DIR/oci-authority.py" --config-file "$CFG" --profile "$PROFILE" verify --instance-id "$INSTANCE_ID" --source-sha "$SHA" --epoch "$AUTH_EPOCH" >/dev/null || fail AUTHORITY_VERIFY_API

post(){ local cmd="$1" id="$2" args="$3" body; body="ATM_CMD_V1
COMMAND=$cmd
COMMAND_ID=$id
ARGS=$args"; curl -fsS -X POST -H "Authorization: Bearer $GH" -H 'Accept: application/vnd.github+json' -H 'Content-Type: application/json' "https://api.github.com/repos/$REPO/issues/$CONTROL/comments" -d "$(jq -nc --arg body "$body" '{body:$body}')"|jq -r .id; }
waitres(){ local id="$1" b=""; for _ in $(seq 1 45); do b="$(curl -fsS -H "Authorization: Bearer $GH" "https://api.github.com/repos/$REPO/issues/$CONTROL/comments?per_page=100"|jq -r --arg id "$id" '.[]|select(.body|startswith("ATM_RESULT_V1"))|select(.body|contains("COMMAND_ID="+$id+"\n"))|.body'|tail -n1)"; if [ -n "$b" ]; then grep -q 'HOST_CLASS=OCI'<<<"$b" && grep -q "SOURCE_SHA=$SHA"<<<"$b" && grep -q 'STATUS=SUCCESS'<<<"$b"; return; fi; sleep 4; done; return 1; }

# Controller/publisher come online only after the durable OCI authority fence passes.
"${SSH[@]}" 'sudo systemctl daemon-reload; sudo systemctl enable --now atm-publisher.service atm-controller.service atm-state-backup.timer'
sleep 3
"${SSH[@]}" 'sudo systemctl is-active --quiet atm-publisher.service && sudo systemctl is-active --quiet atm-controller.service && test -S /run/atm-publisher/publisher.sock' || fail CONTROL_OR_PUBLISHER_NOT_ACTIVE
ID="master-oci-status-$SHA"; post STATUS "$ID" '{"target_host":"OCI"}'>/dev/null; waitres "$ID" || fail STATUS_E2E
ID="master-oci-doctor-$SHA"; post DOCTOR "$ID" '{"target_host":"OCI"}'>/dev/null; waitres "$ID" || fail DOCTOR_E2E
ID="master-oci-runonce-$SHA"; post RUN_ONCE "$ID" '{"target_host":"OCI"}'>/dev/null; waitres "$ID" || fail RUN_ONCE_E2E
ID="master-oci-start-$SHA"; post START "$ID" '{"target_host":"OCI"}'>/dev/null; waitres "$ID" || fail START_E2E
ID="master-oci-final-$SHA"; post STATUS "$ID" '{"target_host":"OCI"}'>/dev/null; waitres "$ID" || fail FINAL_STATUS_E2E
"${SSH[@]}" 'sudo systemctl is-active --quiet atm-publisher.service && sudo systemctl is-active --quiet atm-controller.service && sudo systemctl is-active --quiet atm-supervisor.service' || fail SYSTEMD_FINAL
"${SSH[@]}" 'sudo -u atm /opt/atm/.venv/bin/python /opt/atm/scripts/atm-authority-fence.py >/dev/null' || fail AUTHORITY_FENCE_FINAL
"${SSH[@]}" 'sudo bash -c "set -a; source /etc/atm/state-backup.env; set +a; /opt/atm/scripts/atm-state-backup.sh backup"' >/dev/null || fail CLOUD_STATE_FINAL_BACKUP
READY=1
cleanup_ingress
trap - EXIT INT TERM
unset GH STATE_PAR AUTH_PAR PROMO_JSON

echo "{\"status\":\"READY_FOR_AUD_HOST\",\"authority\":\"OCI\",\"authority_epoch\":$AUTH_EPOCH,\"instance_ocid\":\"$INSTANCE_ID\",\"shape\":\"VM.Standard.A1.Flex\",\"ocpus\":\"$ACTUAL_OCPU\",\"memory_gb\":\"$ACTUAL_MEM_GB\",\"region\":\"$REGION\",\"availability_domain\":\"$AVAILABILITY_DOMAIN\",\"source_sha\":\"$SHA\",\"controller_systemd\":\"ACTIVE\",\"publisher_systemd\":\"ACTIVE\",\"supervisor_systemd\":\"ACTIVE\",\"state_persistence\":\"ATM_CLOUD_STATE_V1\",\"owner_pc_in_production_graph\":0,\"windows_authority\":0,\"control_issue\":$CONTROL,\"observatory_issue\":$OBS,\"ssh_ingress_after_bootstrap\":\"NONE\",\"secrets_printed\":false}"
echo ORDER_002C_BOOTSTRAP_STATUS=READY_FOR_AUD_HOST

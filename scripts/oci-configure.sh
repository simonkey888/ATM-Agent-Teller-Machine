#!/usr/bin/env bash
set -euo pipefail
set +x
source "${1:?infra env required}"
SHA="${ATM_SOURCE_SHA:?}"; REPO="${ATM_REPO:?}"; CONTROL=4; OBS=7
fail(){ echo "ORDER_002C_BOOTSTRAP_STATUS=BLOCKED_REAL reason=$1" >&2; exit "${2:-1}"; }
o(){ oci "$@" --region "$REGION"; }
cleanup(){ o network security-list update --security-list-id "$SECURITY_LIST_ID" --egress-security-rules "$EGRESS_JSON" --ingress-security-rules '[]' --force >/dev/null 2>&1 || true; }
trap cleanup EXIT
SSH=(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -i "$SSH_KEY" "ubuntu@$PUBLIC_IP")
ready=0
for _ in $(seq 1 90); do "${SSH[@]}" 'test -f /var/lib/atm/cloud-init-ready' >/dev/null 2>&1 && { ready=1; break; }; sleep 10; done
[ "$ready" -eq 1 ] || fail CLOUD_INIT_NOT_READY
[ "$("${SSH[@]}" 'git -C /opt/atm rev-parse HEAD')" = "$SHA" ] || fail REMOTE_SHA_MISMATCH
"${SSH[@]}" 'test -x /var/lib/atm/.local/bin/hermes' || fail HERMES_NOT_INSTALLED

read -rsp 'GitHub classic PAT with public_repo scope (control + isolated publisher; not echoed): ' GH; echo
[ -n "$GH" ] || fail GITHUB_TOKEN_EMPTY
[ "$(curl -fsS -H "Authorization: Bearer $GH" https://api.github.com/user|jq -r .login)" = simonkey888 ] || fail GITHUB_TOKEN_INVALID
read -rsp 'GOOGLE_API_KEY for Hermes (not echoed): ' MODELKEY; echo
[ -n "$MODELKEY" ] || fail MODEL_KEY_EMPTY
PAYOUT="${ATM_BASE_WALLET_ADDRESS:-}"
[[ "$PAYOUT" =~ ^0x[0-9a-fA-F]{40}$ ]] || fail ATM_BASE_WALLET_ADDRESS_INVALID

post(){ local cmd="$1" id="$2" args="$3" body; body="ATM_CMD_V1
COMMAND=$cmd
COMMAND_ID=$id
ARGS=$args"; curl -fsS -X POST -H "Authorization: Bearer $GH" -H 'Accept: application/vnd.github+json' -H 'Content-Type: application/json' "https://api.github.com/repos/$REPO/issues/$CONTROL/comments" -d "$(jq -nc --arg body "$body" '{body:$body}')"|jq -r .id; }
waitres(){ local id="$1" b=""; for _ in $(seq 1 30); do b="$(curl -fsS -H "Authorization: Bearer $GH" "https://api.github.com/repos/$REPO/issues/$CONTROL/comments?per_page=100"|jq -r --arg id "$id" '.[]|select(.body|startswith("ATM_RESULT_V1"))|select(.body|contains("COMMAND_ID="+$id+"\n"))|.body'|tail -n1)"; if [ -n "$b" ]; then grep -q 'HOST_CLASS=OCI'<<<"$b" && grep -q "SOURCE_SHA=$SHA"<<<"$b" && grep -q 'STATUS=SUCCESS'<<<"$b"; return; fi; sleep 4; done; return 1; }

# Ask an existing Windows control plane to stop first. OCI will ignore this pre-floor command.
STOPID=order002c-cutover-stop-windows
post STOP "$STOPID" '{}' >/dev/null || fail WINDOWS_STOP_POST
sleep 5
FLOOR="$(curl -fsS -H "Authorization: Bearer $GH" "https://api.github.com/repos/$REPO/issues/$CONTROL/comments?per_page=100"|jq '[.[].id]|max//0')"
{
 printf 'GITHUB_TOKEN=%s\n' "$GH"
 printf 'ATM_HOST_CLASS=OCI\nATM_SOURCE_SHA=%s\nATM_IGNORE_COMMENT_ID=%s\n' "$SHA" "$FLOOR"
}|"${SSH[@]}" 'sudo sh -c "umask 077; mkdir -p /etc/atm; cat >/etc/atm/control.env; chmod 600 /etc/atm/control.env"'
{
 printf 'GOOGLE_API_KEY=%s\n' "$MODELKEY"
 printf 'ATM_HOST_CLASS=OCI\nATM_SOURCE_SHA=%s\n' "$SHA"
}|"${SSH[@]}" 'sudo sh -c "umask 077; cat >/etc/atm/runtime.env; chown root:atm /etc/atm/runtime.env; chmod 640 /etc/atm/runtime.env"'
printf "ATM_STATE_PAR_URL='%s'\n" "$STATE_PAR_URL"|"${SSH[@]}" 'sudo sh -c "umask 077; cat >/etc/atm/state-backup.env; chmod 600 /etc/atm/state-backup.env"'
printf '%s\n' "$PAYOUT"|"${SSH[@]}" 'sudo python3 -c '\''import json,sys;p="/var/lib/atm/atm.json";d=json.load(open(p));d["payment_recipient_public_identifier"]=sys.stdin.readline().strip();open(p,"w").write(json.dumps(d,indent=2)+"\n")'\''; sudo chown atm:atm /var/lib/atm/atm.json; sudo chmod 600 /var/lib/atm/atm.json'
unset MODELKEY
"${SSH[@]}" 'sudo bash -c "set -a; source /etc/atm/state-backup.env; set +a; /opt/atm/scripts/atm-state-backup.sh restore"'
"${SSH[@]}" 'sudo systemctl daemon-reload; sudo systemctl enable --now atm-publisher.service atm-controller.service atm-state-backup.timer'
sleep 3
"${SSH[@]}" 'sudo systemctl is-active --quiet atm-publisher.service && sudo systemctl is-active --quiet atm-controller.service && test -S /run/atm-publisher/publisher.sock' || fail CONTROL_OR_PUBLISHER_NOT_ACTIVE

ID="order002c-oci-status-$SHA"; post STATUS "$ID" '{"target_host":"OCI"}'>/dev/null; waitres "$ID" || fail STATUS_E2E
ID="order002c-oci-doctor-$SHA"; post DOCTOR "$ID" '{"target_host":"OCI"}'>/dev/null; waitres "$ID" || fail DOCTOR_E2E

WIN="$(curl -fsS -H "Authorization: Bearer $GH" "https://api.github.com/repos/$REPO/issues/$CONTROL/comments?per_page=100"|jq -r --arg id "$STOPID" '.[]|select(.body|startswith("ATM_RESULT_V1"))|select(.body|contains("COMMAND_ID="+$id+"\n"))|.body'|tail -n1)"
if [ -z "$WIN" ]; then
  cleanup
  echo "{\"status\":\"CUTOVER_BLOCKED_WINDOWS_STOP_UNPROVEN\",\"instance_ocid\":\"$INSTANCE_ID\",\"source_sha\":\"$SHA\",\"controller_systemd\":\"ACTIVE\",\"publisher_systemd\":\"ACTIVE\",\"supervisor_systemd\":\"STOPPED\",\"observatory_issue\":$OBS,\"secrets_printed\":false}"
  fail WINDOWS_STOP_UNPROVEN_OCI_SUPERVISOR_NOT_STARTED 30
fi

ID="order002c-oci-runonce-$SHA"; post RUN_ONCE "$ID" '{"target_host":"OCI"}'>/dev/null; waitres "$ID" || fail RUN_ONCE_E2E
ID="order002c-oci-start-$SHA"; post START "$ID" '{"target_host":"OCI"}'>/dev/null; waitres "$ID" || fail START_E2E
ID="order002c-oci-final-$SHA"; post STATUS "$ID" '{"target_host":"OCI"}'>/dev/null; waitres "$ID" || fail FINAL_STATUS_E2E
"${SSH[@]}" 'sudo systemctl is-active --quiet atm-publisher.service && sudo systemctl is-active --quiet atm-controller.service && sudo systemctl is-active --quiet atm-supervisor.service' || fail SYSTEMD_FINAL
"${SSH[@]}" 'sudo bash -c "set -a; source /etc/atm/state-backup.env; set +a; /opt/atm/scripts/atm-state-backup.sh backup"' >/dev/null
cleanup
echo "{\"status\":\"READY_FOR_AUD_HOST\",\"instance_ocid\":\"$INSTANCE_ID\",\"shape\":\"VM.Standard.A1.Flex\",\"ocpus\":\"$ACTUAL_OCPU\",\"memory_gb\":\"$ACTUAL_MEM_GB\",\"region\":\"$REGION\",\"availability_domain\":\"$AVAILABILITY_DOMAIN\",\"public_ip\":\"$PUBLIC_IP\",\"source_sha\":\"$SHA\",\"controller_systemd\":\"ACTIVE\",\"publisher_systemd\":\"ACTIVE\",\"supervisor_systemd\":\"ACTIVE\",\"state_persistence\":\"boot_volume_plus_allowlisted_object_backup\",\"control_issue\":$CONTROL,\"observatory_issue\":$OBS,\"ssh_ingress_after_bootstrap\":\"NONE\",\"secrets_printed\":false}"
echo ORDER_002C_BOOTSTRAP_STATUS=READY_FOR_AUD_HOST

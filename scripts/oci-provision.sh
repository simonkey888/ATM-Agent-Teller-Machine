#!/usr/bin/env bash
set -euo pipefail
set +x
OUT="${1:?infra env path required}"
SHA="${ATM_SOURCE_SHA:?}"; REPO="${ATM_REPO:?}"; RAW="${ATM_RAW_BASE:?}"; D="${ATM_BOOT_DIR:?}"
fail(){ echo "ORDER_002C_BOOTSTRAP_STATUS=BLOCKED_REAL reason=$1" >&2; exit "${2:-1}"; }
CFG="${OCI_CLI_CONFIG_FILE:-/etc/oci/config}"; PROFILE="${OCI_CLI_PROFILE:-}"
TENANCY="$(python3 - "$CFG" "$PROFILE" <<'PY'
import configparser,sys
c=configparser.RawConfigParser(); c.read(sys.argv[1]); p=sys.argv[2]
if not p or not c.has_section(p): raise SystemExit(2)
print(c.get(p,"tenancy"))
PY
)" || fail CANNOT_DISCOVER_TENANCY
REGION="$(oci iam region-subscription list --all --query 'data[?"is-home-region"==`true`]."region-name"|[0]' --raw-output)" || fail CANNOT_DISCOVER_HOME_REGION
[ -n "$REGION" ] && [ "$REGION" != null ] || fail EMPTY_HOME_REGION
COMP="${ATM_OCI_COMPARTMENT_ID:-$TENANCY}"; o(){ oci "$@" --region "$REGION"; }
SHAPE=VM.Standard.A1.Flex; OCPU=1; MEM=6; BOOT=50
echo "OCI_PREFLIGHT=START region=$REGION sha=$SHA"
mapfile -t ADS < <(o iam availability-domain list --compartment-id "$TENANCY" --all | jq -r '.data[].name')
[ "${#ADS[@]}" -gt 0 ] || fail NO_ADS
o compute instance list --compartment-id "$COMP" --all >/dev/null || fail COMPUTE_READ_DENIED
o network vcn list --compartment-id "$COMP" --all >/dev/null || fail NETWORK_READ_DENIED
# Conservative Always Free guard: 2 OCPU / 12 GB A1 and 200 GB block total.
# Inventory every accessible active compartment, not only the target compartment.
mapfile -t COMPS < <({ printf '%s\n' "$TENANCY"; o iam compartment list --compartment-id "$TENANCY" --compartment-id-in-subtree true --access-level ACCESSIBLE --all | jq -r '.data[]|select(."lifecycle-state"=="ACTIVE")|.id'; } | awk '!seen[$0]++')
CPU_USED=0; MEM_USED=0; VOL_USED=0
for c in "${COMPS[@]}"; do
  inst="$(o compute instance list --compartment-id "$c" --all 2>/dev/null || echo '{"data":[]}')"
  read -r cpu mem <<<"$(jq -r '[.data[]|select(.shape=="VM.Standard.A1.Flex" and (."lifecycle-state"!="TERMINATED"))|[(."shape-config".ocpus//0),(."shape-config"."memory-in-gbs"//0)]]|[map(.[0])|add//0,map(.[1])|add//0]|@tsv' <<<"$inst")"
  CPU_USED="$(python3 -c "print(float('$CPU_USED')+float('$cpu'))")"; MEM_USED="$(python3 -c "print(float('$MEM_USED')+float('$mem'))")"
  for ad in "${ADS[@]}"; do
    for kind in boot-volume volume; do
      n="$(o bv "$kind" list --compartment-id "$c" --availability-domain "$ad" --all 2>/dev/null | jq '[.data[]|select(."lifecycle-state"!="TERMINATED")|."size-in-gbs"]|add//0' || echo 0)"
      VOL_USED="$(python3 -c "print(float('$VOL_USED')+float('$n'))")"
    done
  done
done
IID="$(o compute instance list --compartment-id "$COMP" --display-name atm-oci --all --query 'data[?"lifecycle-state"!=`TERMINATED`].id|[0]' --raw-output 2>/dev/null || true)"
if [ -z "$IID" ] || [ "$IID" = null ]; then
  python3 - "$CPU_USED" "$MEM_USED" "$VOL_USED" <<'PY' || fail ALWAYS_FREE_HEADROOM_EXCEEDED
import sys
c,m,v=map(float,sys.argv[1:]); assert c+1<=2 and m+6<=12 and v+50<=200
PY
fi
GOOD_ADS=()
for ad in "${ADS[@]}"; do
  c="$(o limits resource-availability get --compartment-id "$COMP" --service-name compute-core --limit-name standard-a1-core-count --availability-domain "$ad" 2>/dev/null | jq -r '.data."fractional-available"//.data.available//0' || true)"
  m="$(o limits resource-availability get --compartment-id "$COMP" --service-name compute-core --limit-name standard-a1-memory-count --availability-domain "$ad" 2>/dev/null | jq -r '.data."fractional-available"//.data.available//0' || true)"
  python3 - "$c" "$m" -c 'import sys; raise SystemExit(0 if float(sys.argv[1] or 0)>=1 and float(sys.argv[2] or 0)>=6 else 1)' && GOOD_ADS+=("$ad") || true
done
if { [ -z "$IID" ] || [ "$IID" = null ]; } && [ "${#GOOD_ADS[@]}" -eq 0 ]; then fail NO_A1_FREE_LIMIT_HEADROOM 20; fi

VCN="$(o network vcn list --compartment-id "$COMP" --all --query 'data[?"display-name"==`atm-vcn`].id|[0]' --raw-output)"
[ -n "$VCN" ] && [ "$VCN" != null ] || VCN="$(o network vcn create --compartment-id "$COMP" --cidr-block 10.77.0.0/16 --display-name atm-vcn --dns-label atmnet --wait-for-state AVAILABLE --query data.id --raw-output)"
IGW="$(o network internet-gateway list --compartment-id "$COMP" --vcn-id "$VCN" --all --query 'data[?"display-name"==`atm-igw`].id|[0]' --raw-output)"
[ -n "$IGW" ] && [ "$IGW" != null ] || IGW="$(o network internet-gateway create --compartment-id "$COMP" --vcn-id "$VCN" --is-enabled true --display-name atm-igw --wait-for-state AVAILABLE --query data.id --raw-output)"
RR="[{\"cidrBlock\":\"0.0.0.0/0\",\"networkEntityId\":\"$IGW\"}]"
RT="$(o network route-table list --compartment-id "$COMP" --vcn-id "$VCN" --all --query 'data[?"display-name"==`atm-route`].id|[0]' --raw-output)"
if [ -z "$RT" ] || [ "$RT" = null ]; then RT="$(o network route-table create --compartment-id "$COMP" --vcn-id "$VCN" --route-rules "$RR" --display-name atm-route --wait-for-state AVAILABLE --query data.id --raw-output)"; else o network route-table update --rt-id "$RT" --route-rules "$RR" --force >/dev/null; fi
CSIP="$(curl -4fsS --max-time 10 https://api.ipify.org)" || fail CLOUD_SHELL_EGRESS_IP_FAILED
[[ "$CSIP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || fail CLOUD_SHELL_EGRESS_IP_INVALID
EG='[{"destination":"0.0.0.0/0","protocol":"all","isStateless":false}]'; IN="[{\"source\":\"$CSIP/32\",\"protocol\":\"6\",\"isStateless\":false,\"tcpOptions\":{\"destinationPortRange\":{\"min\":22,\"max\":22}}}]"
SL="$(o network security-list list --compartment-id "$COMP" --vcn-id "$VCN" --all --query 'data[?"display-name"==`atm-security`].id|[0]' --raw-output)"
if [ -z "$SL" ] || [ "$SL" = null ]; then SL="$(o network security-list create --compartment-id "$COMP" --vcn-id "$VCN" --display-name atm-security --egress-security-rules "$EG" --ingress-security-rules "$IN" --wait-for-state AVAILABLE --query data.id --raw-output)"; else o network security-list update --security-list-id "$SL" --egress-security-rules "$EG" --ingress-security-rules "$IN" --force >/dev/null; fi
SUB="$(o network subnet list --compartment-id "$COMP" --vcn-id "$VCN" --all --query 'data[?"display-name"==`atm-subnet`].id|[0]' --raw-output)"
[ -n "$SUB" ] && [ "$SUB" != null ] || SUB="$(o network subnet create --compartment-id "$COMP" --vcn-id "$VCN" --cidr-block 10.77.1.0/24 --display-name atm-subnet --dns-label atm --route-table-id "$RT" --security-list-ids "[\"$SL\"]" --prohibit-public-ip-on-vnic false --wait-for-state AVAILABLE --query data.id --raw-output)"

mkdir -p "$HOME/.atm"; chmod 700 "$HOME/.atm"; KEY="$HOME/.atm/oci-atm-ed25519"
[ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N '' -f "$KEY" -C atm-oci-breakglass
chmod 600 "$KEY"; chmod 644 "$KEY.pub"

# Tiny private external state object. Never overwrite an existing state object on bootstrap reruns.
NS="$(o os ns get --query data --raw-output)" || fail OBJECT_STORAGE_DENIED
for c in "${COMPS[@]}"; do o os bucket list --namespace-name "$NS" --compartment-id "$c" --all >/dev/null 2>&1 || true; done
B="atm-state-$(printf %s "$TENANCY"|sha256sum|cut -c1-12)"
o os bucket get --namespace-name "$NS" --bucket-name "$B" >/dev/null 2>&1 || o os bucket create --namespace-name "$NS" --compartment-id "$COMP" --name "$B" --public-access-type NoPublicAccess --storage-tier Standard >/dev/null || fail STATE_BUCKET_CREATE
if ! o os object head --namespace-name "$NS" --bucket-name "$B" --name atm-state.tgz >/dev/null 2>&1; then
  : >"$D/empty"
  o os object put --namespace-name "$NS" --bucket-name "$B" --name atm-state.tgz --file "$D/empty" --force >/dev/null || fail STATE_OBJECT_CREATE
else
  echo 'OCI_STATE_OBJECT=PRESERVED_EXISTING'
fi
EXP="$(date -u -d '+365 days' +%Y-%m-%dT%H:%M:%SZ)"
URI="$(o os preauth-request create --namespace-name "$NS" --bucket-name "$B" --name "atm-state-rw-$(date +%s)" --access-type ObjectReadWrite --object-name atm-state.tgz --time-expires "$EXP" --query 'data."access-uri"' --raw-output)" || fail STATE_PAR_CREATE
PAR="https://objectstorage.$REGION.oraclecloud.com$URI"

if [ -z "$IID" ] || [ "$IID" = null ]; then
  IMG="$(o compute image list --compartment-id "$TENANCY" --shape "$SHAPE" --operating-system 'Canonical Ubuntu' --all --sort-by TIMECREATED --sort-order DESC --query 'data[0].id' --raw-output)" || fail IMAGE_LOOKUP
  [ -n "$IMG" ] && [ "$IMG" != null ] || fail NO_A1_UBUNTU_IMAGE
  cat >"$D/user-data.sh" <<EOF
#!/bin/bash
set -euo pipefail
export ATM_SOURCE_SHA="$SHA"
curl -fsSL "$RAW/deploy/oci/cloud-init.sh" | bash
EOF
  ok=0; err=""
  for ad in "${GOOD_ADS[@]}"; do
    set +e
    x="$(o compute instance launch --availability-domain "$ad" --compartment-id "$COMP" --shape "$SHAPE" --shape-config "{\"ocpus\":1,\"memoryInGBs\":6}" --subnet-id "$SUB" --image-id "$IMG" --display-name atm-oci --assign-public-ip true --boot-volume-size-in-gbs 50 --ssh-authorized-keys-file "$KEY.pub" --user-data-file "$D/user-data.sh" --freeform-tags "{\"ATM\":\"true\",\"source_sha\":\"$SHA\",\"zero_upfront\":\"true\"}" --wait-for-state RUNNING --max-wait-seconds 900 --query data.id --raw-output 2>&1)"; rc=$?
    set -e
    if [ "$rc" -eq 0 ]; then IID="$x"; ok=1; break; fi
    err="$x"; grep -Eqi 'capacity|OutOfHostCapacity' <<<"$x" || fail INSTANCE_LAUNCH_NON_CAPACITY_ERROR
  done
  [ "$ok" -eq 1 ] || { echo "OCI_CAPACITY_CONDITION=$(tail -c 300 <<<"$err"|tr '\n' ' ')"; fail A1_CAPACITY_UNAVAILABLE_ALL_ADS 20; }
fi

[ "$(o compute instance get --instance-id "$IID" --query data.shape --raw-output)" = "$SHAPE" ] || fail EXISTING_INSTANCE_NOT_A1
AD="$(o compute instance get --instance-id "$IID" --query 'data."availability-domain"' --raw-output)"
ACT_OCPU="$(o compute instance get --instance-id "$IID" --query 'data."shape-config".ocpus' --raw-output)"
ACT_MEM="$(o compute instance get --instance-id "$IID" --query 'data."shape-config"."memory-in-gbs"' --raw-output)"
VNIC="$(o compute vnic-attachment list --compartment-id "$COMP" --instance-id "$IID" --query 'data[0]."vnic-id"' --raw-output)" || fail VNIC_LOOKUP
IP="$(o network vnic get --vnic-id "$VNIC" --query 'data."public-ip"' --raw-output)" || fail PUBLIC_IP_LOOKUP
[ -n "$IP" ] && [ "$IP" != null ] || fail PUBLIC_IP_MISSING

cat >"$OUT" <<EOF
TENANCY='$TENANCY'
REGION='$REGION'
COMPARTMENT='$COMP'
INSTANCE_ID='$IID'
AVAILABILITY_DOMAIN='$AD'
ACTUAL_OCPU='$ACT_OCPU'
ACTUAL_MEM_GB='$ACT_MEM'
PUBLIC_IP='$IP'
SECURITY_LIST_ID='$SL'
EGRESS_JSON='$EG'
SSH_KEY='$KEY'
STATE_PAR_URL='$PAR'
EOF
chmod 600 "$OUT"
echo "OCI_PROVISION=OK instance=$IID shape=$SHAPE ocpu=$ACT_OCPU memory_gb=$ACT_MEM boot_gb=50 ad=$AD"

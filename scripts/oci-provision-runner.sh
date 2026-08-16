#!/usr/bin/env bash
set -euo pipefail
set +x

D="${ATM_BOOT_DIR:?}"
REAL_OCI="$(command -v oci)"

oci() {
  if [ "${1:-}" = "bv" ] && [ "${3:-}" = "list" ] && { [ "${2:-}" = "boot-volume" ] || [ "${2:-}" = "volume" ]; }; then
    # Do not enumerate Block Volume objects here. For ORDER-002C we only need
    # authoritative combined boot+block usage to enforce the 200-GB free cap.
    # OCI exposes that directly via Limits/ResourceAvailability as
    # block-storage / total-storage-gb (AD-scoped).
    if [ "${2:-}" = "volume" ]; then
      printf '%s\n' '{"data":[]}'
      return 0
    fi

    local comp="" ad="" region="" out rc used cls err i
    local -a argv=("$@")
    for ((i=0; i<${#argv[@]}; i++)); do
      case "${argv[$i]}" in
        --compartment-id)
          [ $((i+1)) -lt ${#argv[@]} ] && comp="${argv[$((i+1))]}"
          ;;
        --availability-domain)
          [ $((i+1)) -lt ${#argv[@]} ] && ad="${argv[$((i+1))]}"
          ;;
        --region)
          [ $((i+1)) -lt ${#argv[@]} ] && region="${argv[$((i+1))]}"
          ;;
      esac
    done

    [ -n "$comp" ] || { echo 'OCI_BLOCK_STORAGE_LIMIT=FAIL class=MISSING_COMPARTMENT' >&2; return 41; }
    [ -n "$ad" ] || { echo 'OCI_BLOCK_STORAGE_LIMIT=FAIL class=MISSING_AVAILABILITY_DOMAIN' >&2; return 41; }
    [ -n "$region" ] || { echo 'OCI_BLOCK_STORAGE_LIMIT=FAIL class=MISSING_REGION' >&2; return 41; }

    err="$D/oci-block-storage-limit.$$.err"
    set +e
    out="$("$REAL_OCI" limits resource-availability get \
      --compartment-id "$comp" \
      --service-name block-storage \
      --limit-name total-storage-gb \
      --availability-domain "$ad" \
      --region "$region" \
      --output json 2>"$err")"
    rc=$?
    set -e

    if [ "$rc" -ne 0 ]; then
      cls=COMMAND_FAILED
      grep -Eqi 'NotAuthorizedOrNotFound|NotAuthorized|Forbidden|(^|[^0-9])(401|403)([^0-9]|$)' "$err" && cls=NOT_AUTHORIZED_OR_NOT_FOUND
      grep -Eqi '(^|[^0-9])404([^0-9]|$)|not supported|Unsupported' "$err" && cls=RESOURCE_AVAILABILITY_UNSUPPORTED
      grep -Eqi 'InvalidParameter|(^|[^0-9])400([^0-9]|$)' "$err" && cls=INVALID_SCOPE_OR_PARAMETER
      grep -Eqi 'TooManyRequests|(^|[^0-9])429([^0-9]|$)' "$err" && cls=RATE_LIMITED
      rm -f "$err"
      echo "OCI_BLOCK_STORAGE_LIMIT=FAIL class=$cls" >&2
      return 42
    fi
    rm -f "$err"

    used="$(jq -er '.data as $d | ($d."fractional-usage" // $d.fractionalUsage // $d.fractional_usage // $d.used) | select(type=="number" and .>=0)' <<<"$out")" || {
      echo 'OCI_BLOCK_STORAGE_LIMIT=FAIL class=USAGE_FIELD_MISSING_OR_INVALID' >&2
      return 43
    }

    # oci-provision.sh already sums one result per compartment/AD and applies
    # the independent ORDER-002C hard cap: current_usage + 50 GB <= 200 GB.
    jq -nc --argjson used "$used" '{"data":[{"lifecycle-state":"AVAILABLE","size-in-gbs":$used}]}'
    return 0
  fi

  "$REAL_OCI" "$@"
}

export REAL_OCI
export -f oci
exec "$D/oci-provision.sh" "$@"

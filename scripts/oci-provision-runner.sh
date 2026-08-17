#!/usr/bin/env bash
set -euo pipefail
set +x

D="${ATM_BOOT_DIR:?}"
REAL_OCI="$(command -v oci)"
REAL_SSH_KEYGEN="$(command -v ssh-keygen)"

# OCI Cloud Shell can run with a FIPS crypto policy that rejects Ed25519 key
# generation. The provisioner asks for a local break-glass key only; preserve
# the same private/public paths but translate that exact request to RSA-3072.
ssh-keygen() {
  local -a argv=("$@") out=()
  local i transformed=0
  for ((i=0; i<${#argv[@]}; i++)); do
    if [ "${argv[$i]}" = "-t" ] && [ $((i+1)) -lt ${#argv[@]} ] && [ "${argv[$((i+1))]}" = "ed25519" ]; then
      out+=("-t" "rsa" "-b" "3072")
      i=$((i+1))
      transformed=1
    else
      out+=("${argv[$i]}")
    fi
  done
  if [ "$transformed" -eq 1 ]; then
    echo 'OCI_BREAKGLASS_KEY_ALGORITHM=RSA3072 reason=FIPS_COMPATIBILITY'
  fi
  "$REAL_SSH_KEYGEN" "${out[@]}"
}

oci() {
  # General compute inventory must not inherit Cloud Shell/user output/query
  # defaults. The GitHub API-key executor uses the OCI Python SDK directly,
  # avoiding a proven CLI formatter/JMESPath path that can return rc=0 with
  # unusable stdout. Cloud Shell retains the conservative CLI path below.
  # Display-name lookup remains direct OCI service truth because it recovers
  # the actual atm-oci instance OCID.
  if [ "${1:-}" = "compute" ] && [ "${2:-}" = "instance" ] && [ "${3:-}" = "list" ]; then
    local has_display=0 arg out rc totals cpu mem err count countq cpuq memq comp region i
    local -a argv=("$@")
    for arg in "$@"; do
      [ "$arg" = "--display-name" ] && has_display=1
    done
    if [ "$has_display" -eq 0 ]; then
      if [ "${OCI_CLI_AUTH:-}" = "api_key" ]; then
        comp=""
        region=""
        for ((i=0; i<${#argv[@]}; i++)); do
          case "${argv[$i]}" in
            --compartment-id)
              [ $((i+1)) -lt ${#argv[@]} ] && comp="${argv[$((i+1))]}"
              ;;
            --region)
              [ $((i+1)) -lt ${#argv[@]} ] && region="${argv[$((i+1))]}"
              ;;
          esac
        done
        [ -n "$comp" ] || { echo 'OCI_COMPUTE_INVENTORY=FAIL class=SDK_COMPARTMENT_MISSING' >&2; return 45; }
        [ -n "$region" ] || { echo 'OCI_COMPUTE_INVENTORY=FAIL class=SDK_REGION_MISSING' >&2; return 45; }
        set +e
        totals="$(ATM_OCI_COMPARTMENT_ID="$comp" ATM_OCI_REGION="$region" python3 "$D/oci-compute-usage-sdk.py")"
        rc=$?
        set -e
        [ "$rc" -eq 0 ] || { echo 'OCI_COMPUTE_INVENTORY=FAIL class=SDK_QUERY_FAILED' >&2; return "$rc"; }
        IFS=$'\t' read -r cpu mem <<<"$totals"
        [[ "$cpu" =~ ^[0-9]+([.][0-9]+)?$ ]] || { echo 'OCI_COMPUTE_INVENTORY=FAIL class=SDK_OCPU_INVALID' >&2; return 45; }
        [[ "$mem" =~ ^[0-9]+([.][0-9]+)?$ ]] || { echo 'OCI_COMPUTE_INVENTORY=FAIL class=SDK_MEMORY_INVALID' >&2; return 45; }
        jq -nc --argjson cpu "$cpu" --argjson mem "$mem" '
          if ($cpu == 0 and $mem == 0) then {data:[]}
          else {data:[{shape:"VM.Standard.A1.Flex","lifecycle-state":"RUNNING","shape-config":{ocpus:$cpu,"memory-in-gbs":$mem}}]}
          end'
        return 0
      fi

      # Cloud Shell fallback path. Never reduce empty formatter output to zero
      # without a separate exact zero count.
      err="$D/oci-compute-inventory.$$.err"
      set +e
      out="$("$REAL_OCI" "$@" --output json --query data 2>"$err")"
      rc=$?
      set -e
      if [ "$rc" -ne 0 ]; then
        rm -f "$err"
        echo 'OCI_COMPUTE_INVENTORY=FAIL class=COMMAND_FAILED' >&2
        return "$rc"
      fi

      if [ -n "${out//[[:space:]]/}" ]; then
        rm -f "$err"
        totals="$(python3 "$D/oci-normalize-compute.py" <<<"$out")" || {
          echo 'OCI_COMPUTE_INVENTORY=FAIL class=NORMALIZATION_FAILED' >&2
          return 44
        }
        IFS=$'\t' read -r cpu mem <<<"$totals"
      else
        echo 'OCI_COMPUTE_INVENTORY=FALLBACK class=EMPTY_JSON_STDOUT mode=COUNT_THEN_SCALAR_SUMS' >&2
        countq='length(data[?shape==`VM.Standard.A1.Flex` && "lifecycle-state"!=`TERMINATED`])'
        cpuq='sum(data[?shape==`VM.Standard.A1.Flex` && "lifecycle-state"!=`TERMINATED`]."shape-config".ocpus)'
        memq='sum(data[?shape==`VM.Standard.A1.Flex` && "lifecycle-state"!=`TERMINATED`]."shape-config"."memory-in-gbs")'

        : >"$err"
        set +e
        count="$("$REAL_OCI" "$@" --query "$countq" --raw-output 2>"$err")"; rc=$?
        set -e
        if [ "$rc" -ne 0 ]; then
          rm -f "$err"
          echo 'OCI_COMPUTE_INVENTORY=FAIL class=SCALAR_COUNT_QUERY_FAILED' >&2
          return "$rc"
        fi
        count="$(tr -d '[:space:]' <<<"$count")"
        [[ "$count" =~ ^[0-9]+$ ]] || { rm -f "$err"; echo 'OCI_COMPUTE_INVENTORY=FAIL class=SCALAR_COUNT_INVALID' >&2; return 44; }

        if [ "$count" -eq 0 ]; then
          rm -f "$err"
          cpu=0
          mem=0
          echo 'OCI_COMPUTE_INVENTORY=EMPTY_A1_CONFIRMED count=0'
        else
          : >"$err"
          set +e
          cpu="$("$REAL_OCI" "$@" --query "$cpuq" --raw-output 2>"$err")"; rc=$?
          set -e
          if [ "$rc" -ne 0 ]; then
            rm -f "$err"
            echo 'OCI_COMPUTE_INVENTORY=FAIL class=SCALAR_OCPU_QUERY_FAILED' >&2
            return "$rc"
          fi
          : >"$err"
          set +e
          mem="$("$REAL_OCI" "$@" --query "$memq" --raw-output 2>"$err")"; rc=$?
          set -e
          if [ "$rc" -ne 0 ]; then
            rm -f "$err"
            echo 'OCI_COMPUTE_INVENTORY=FAIL class=SCALAR_MEMORY_QUERY_FAILED' >&2
            return "$rc"
          fi
          rm -f "$err"
          cpu="$(tr -d '[:space:]' <<<"$cpu")"
          mem="$(tr -d '[:space:]' <<<"$mem")"
          [[ "$cpu" =~ ^[0-9]+([.][0-9]+)?$ ]] || { echo 'OCI_COMPUTE_INVENTORY=FAIL class=SCALAR_OCPU_INVALID' >&2; return 44; }
          [[ "$mem" =~ ^[0-9]+([.][0-9]+)?$ ]] || { echo 'OCI_COMPUTE_INVENTORY=FAIL class=SCALAR_MEMORY_INVALID' >&2; return 44; }
        fi
      fi

      jq -nc --argjson cpu "$cpu" --argjson mem "$mem" '
        if ($cpu == 0 and $mem == 0) then {data:[]}
        else {data:[{shape:"VM.Standard.A1.Flex","lifecycle-state":"RUNNING","shape-config":{ocpus:$cpu,"memory-in-gbs":$mem}}]}
        end'
      return 0
    fi
  fi

  # Current OCI CLI returns list-object-versions as {data:{items:[...]}}.
  # The provisioner consumes the ordinary list shape {data:[...]}; normalize
  # this command only so storage accounting is deterministic across CLI shapes.
  if [ "${1:-}" = "os" ] && [ "${2:-}" = "object" ] && [ "${3:-}" = "list-object-versions" ]; then
    local out rc
    set +e
    out="$("$REAL_OCI" "$@")"
    rc=$?
    set -e
    [ "$rc" -eq 0 ] || return "$rc"
    jq -e 'if ((.data // null) | type) == "object" then {data:(.data.items // [])} elif ((.data // null) | type) == "array" then . else error("OBJECT_VERSION_INVENTORY_SHAPE_INVALID") end' <<<"$out"
    return $?
  fi

  if [ "${1:-}" = "bv" ] && [ "${3:-}" = "list" ] && { [ "${2:-}" = "boot-volume" ] || [ "${2:-}" = "volume" ]; }; then
    # Do not enumerate Block Volume objects here. For ORDER-002C we only need
    # authoritative combined boot+block usage to enforce the 200-GB free cap.
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

    jq -nc --argjson used "$used" '{"data":[{"lifecycle-state":"AVAILABLE","size-in-gbs":$used}]}'
    return 0
  fi

  "$REAL_OCI" "$@"
}

export REAL_OCI REAL_SSH_KEYGEN
export -f oci ssh-keygen
exec "$D/oci-provision.sh" "$@"

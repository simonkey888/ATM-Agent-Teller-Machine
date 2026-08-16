#!/usr/bin/env bash
set -euo pipefail
set +x

D="${ATM_BOOT_DIR:?}"
REAL_OCI="$(command -v oci)"
OCI_NORMALIZER="$D/oci-normalize-bv.py"
[ -x "$OCI_NORMALIZER" ] || { echo "ORDER_002C_BOOTSTRAP_STATUS=BLOCKED_REAL reason=OCI_BV_NORMALIZER_MISSING" >&2; exit 11; }

oci() {
  if [ "${1:-}" = "bv" ] && [ "${3:-}" = "list" ] && { [ "${2:-}" = "boot-volume" ] || [ "${2:-}" = "volume" ]; }; then
    local out rc
    set +e
    out="$("$REAL_OCI" "$@" --output json)"
    rc=$?
    set -e
    [ "$rc" -eq 0 ] || return "$rc"
    printf '%s\n' "$out" | python3 "$OCI_NORMALIZER"
    return $?
  fi
  "$REAL_OCI" "$@"
}

export REAL_OCI OCI_NORMALIZER
export -f oci
exec "$D/oci-provision.sh" "$@"

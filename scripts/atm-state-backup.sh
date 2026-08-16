#!/usr/bin/env bash
set -euo pipefail
set +x

MODE="${1:-backup}"
STATE_DIR="${ATM_STATE_DIR:-/var/lib/atm/state}"
PAR_URL="${ATM_STATE_PAR_URL:-}"
[ -n "$PAR_URL" ] || { echo 'ATM_STATE_BACKUP=SKIP reason=PAR_URL_MISSING'; exit 0; }
mkdir -p "$STATE_DIR"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
ARCHIVE="$WORK/atm-state.tgz"
FILES=(state.json validated-payment-proofs.jsonl monthly-targets.json human-gates.jsonl degraded-opportunities.json controller-state-oci.json discovery-cache.json)

case "$MODE" in
  backup)
    present=()
    for f in "${FILES[@]}"; do [ -f "$STATE_DIR/$f" ] && present+=("$f"); done
    if [ "${#present[@]}" -eq 0 ]; then echo 'ATM_STATE_BACKUP=SKIP reason=NO_STATE'; exit 0; fi
    tar -C "$STATE_DIR" -czf "$ARCHIVE" "${present[@]}"
    curl -fsS --retry 3 --retry-all-errors -X PUT --data-binary "@$ARCHIVE" "$PAR_URL" >/dev/null
    echo "ATM_STATE_BACKUP=OK files=${#present[@]}"
    ;;
  restore)
    if ! curl -fsS --retry 2 --retry-all-errors "$PAR_URL" -o "$ARCHIVE"; then
      echo 'ATM_STATE_RESTORE=SKIP reason=NO_REMOTE_STATE'; exit 0
    fi
    while IFS= read -r entry; do
      allowed=0
      for f in "${FILES[@]}"; do [ "$entry" = "$f" ] && allowed=1 && break; done
      [ "$allowed" -eq 1 ] || { echo 'ATM_STATE_RESTORE=FAIL reason=UNEXPECTED_ARCHIVE_PATH'; exit 9; }
    done < <(tar -tzf "$ARCHIVE")
    tar -C "$STATE_DIR" -xzf "$ARCHIVE"
    chown -R atm:atm "$STATE_DIR"
    chmod 700 "$STATE_DIR"
    find "$STATE_DIR" -maxdepth 1 -type f -exec chmod 600 {} +
    echo 'ATM_STATE_RESTORE=OK'
    ;;
  *) echo 'usage: atm-state-backup.sh backup|restore' >&2; exit 2;;
esac

#!/usr/bin/env bash
set -euo pipefail
set +x

MODE="${1:-backup}"
STATE_DIR="${ATM_STATE_DIR:-/var/lib/atm/state}"
PAR_URL="${ATM_STATE_PAR_URL:-}"
[ -n "$PAR_URL" ] || { echo 'ATM_CLOUD_STATE=FAIL reason=PAR_URL_MISSING' >&2; exit 9; }
mkdir -p "$STATE_DIR"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
ARCHIVE="$WORK/atm-cloud-state.tgz"
FILES=(state.json state.json.lkg validated-payment-proofs.jsonl monthly-targets.json human-gates.jsonl degraded-opportunities.json discovery-cache.json cloud-control.json cloud-command-state.json money-board.sqlite3 oci-capacity.json)
export STATE_DIR ARCHIVE

case "$MODE" in
  backup)
    python3 - <<'PY'
import hashlib, io, json, os, sqlite3, tarfile, time
from datetime import datetime, timezone
from pathlib import Path
state=Path(os.environ['STATE_DIR']); archive=Path(os.environ['ARCHIVE'])
allowed={"state.json","state.json.lkg","validated-payment-proofs.jsonl","monthly-targets.json","human-gates.jsonl","degraded-opportunities.json","discovery-cache.json","cloud-control.json","cloud-command-state.json","money-board.sqlite3","oci-capacity.json"}
board=state/'money-board.sqlite3'
if board.exists():
    c=sqlite3.connect(board); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()
files={p.name:p.read_bytes() for p in state.iterdir() if p.is_file() and p.name in allowed}
if not files:
    raise SystemExit('ATM_CLOUD_STATE_BACKUP_NO_STATE')
sha=os.environ.get('ATM_SOURCE_SHA','')
if len(sha)!=40:
    try:
        import subprocess
        sha=subprocess.check_output(['git','-C','/opt/atm','rev-parse','HEAD'],text=True).strip()
    except Exception:
        sha='UNKNOWN'
manifest={
 'schema':'ATM_CLOUD_STATE_V1','source_sha':sha,
 'created_at':datetime.now(timezone.utc).isoformat(),
 'files':{n:{'size':len(b),'sha256':hashlib.sha256(b).hexdigest()} for n,b in files.items()},
}
with tarfile.open(archive,'w:gz',format=tarfile.PAX_FORMAT) as tf:
    for name,data in sorted(files.items()):
        i=tarfile.TarInfo(name); i.size=len(data); i.mode=0o600; i.mtime=int(time.time()); tf.addfile(i,io.BytesIO(data))
    raw=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
    i=tarfile.TarInfo('manifest.json'); i.size=len(raw); i.mode=0o600; i.mtime=int(time.time()); tf.addfile(i,io.BytesIO(raw))
PY
    curl -fsS --retry 3 --retry-all-errors -X PUT --data-binary "@$ARCHIVE" "$PAR_URL" >/dev/null
    echo 'ATM_CLOUD_STATE_BACKUP=OK schema=ATM_CLOUD_STATE_V1'
    ;;
  restore)
    curl -fsS --retry 2 --retry-all-errors "$PAR_URL" -o "$ARCHIVE" || { echo 'ATM_CLOUD_STATE_RESTORE=FAIL reason=NO_REMOTE_STATE' >&2; exit 9; }
    python3 - <<'PY'
import hashlib, io, json, os, sqlite3, tarfile
from pathlib import Path
state=Path(os.environ['STATE_DIR']); archive=Path(os.environ['ARCHIVE'])
allowed={"state.json","state.json.lkg","validated-payment-proofs.jsonl","monthly-targets.json","human-gates.jsonl","degraded-opportunities.json","discovery-cache.json","cloud-control.json","cloud-command-state.json","money-board.sqlite3","oci-capacity.json"}
try: tf=tarfile.open(archive,'r:gz')
except tarfile.TarError as e: raise SystemExit('ATM_CLOUD_STATE_RESTORE_CORRUPT') from e
with tf:
    members={m.name:m for m in tf.getmembers() if m.isfile()}
    if set(members)-allowed-{'manifest.json'}: raise SystemExit('ATM_CLOUD_STATE_RESTORE_UNEXPECTED_PATH')
    m=members.get('manifest.json')
    if not m: raise SystemExit('ATM_CLOUD_STATE_RESTORE_MANIFEST_MISSING')
    manifest=json.loads(tf.extractfile(m).read())
    if manifest.get('schema')!='ATM_CLOUD_STATE_V1' or not isinstance(manifest.get('files'),dict): raise SystemExit('ATM_CLOUD_STATE_RESTORE_SCHEMA_INVALID')
    if set(manifest['files'])-allowed: raise SystemExit('ATM_CLOUD_STATE_RESTORE_MANIFEST_UNEXPECTED')
    state.mkdir(parents=True,exist_ok=True)
    for name,meta in manifest['files'].items():
        member=members.get(name)
        if not member: raise SystemExit('ATM_CLOUD_STATE_RESTORE_MEMBER_MISSING')
        data=tf.extractfile(member).read()
        if len(data)!=int(meta.get('size',-1)) or hashlib.sha256(data).hexdigest()!=meta.get('sha256'): raise SystemExit('ATM_CLOUD_STATE_RESTORE_CHECKSUM_MISMATCH')
        (state/name).write_bytes(data)
js=state/'state.json'
if js.exists() and not isinstance(json.loads(js.read_text()),dict): raise SystemExit('ATM_CLOUD_STATE_RESTORE_STATE_INVALID')
ledger=state/'validated-payment-proofs.jsonl'
if ledger.exists():
    for line in ledger.read_text().splitlines():
        if line.strip() and not isinstance(json.loads(line),dict): raise SystemExit('ATM_CLOUD_STATE_RESTORE_LEDGER_INVALID')
board=state/'money-board.sqlite3'
if board.exists():
    c=sqlite3.connect(f'file:{board}?mode=ro',uri=True); row=c.execute('PRAGMA integrity_check').fetchone(); c.close()
    if not row or row[0]!='ok': raise SystemExit('ATM_CLOUD_STATE_RESTORE_BOARD_INVALID')
PY
    chown -R atm:atm "$STATE_DIR"
    chmod 700 "$STATE_DIR"
    find "$STATE_DIR" -maxdepth 1 -type f -exec chmod 600 {} +
    echo 'ATM_CLOUD_STATE_RESTORE=OK schema=ATM_CLOUD_STATE_V1'
    ;;
  *) echo 'usage: atm-state-backup.sh backup|restore' >&2; exit 2;;
esac

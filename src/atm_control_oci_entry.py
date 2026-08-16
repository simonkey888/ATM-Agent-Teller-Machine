from __future__ import annotations

import json

import atm_control_oci as ctl
from atm_core.runtime import ProcessLock, SingletonLockError


def recover() -> int:
    cfg = json.loads(ctl.CONFIG_PATH.read_text(encoding="utf-8"))
    lock = ProcessLock(ctl.LOCK)
    try:
        lock.acquire()
    except SingletonLockError:
        return 3
    try:
        bus = ctl.GitHubBus(cfg)
        state = ctl.load_json(ctl.STATE_FILE, {})
        comments = state.setdefault("processed_comments", {})
        commands = state.setdefault("processed_commands", {})
        changed = False
        for comment_id, record in list(comments.items()):
            phase = record.get("phase")
            if phase == "RESULT_PENDING" and record.get("result_body"):
                result_id = bus.post_result(str(record["result_body"]))
                record.update({"phase": "DONE", "result_comment_id": result_id, "result_body": None})
                comments[comment_id] = record
                commands[record["command_id"]] = record
                changed = True
            elif phase == "EXECUTING":
                body = "\n".join([
                    ctl.RESULT_PREFIX,
                    f"COMMAND_ID={record.get('command_id')}",
                    f"COMMENT_ID={comment_id}",
                    "STATUS=INDETERMINATE_AFTER_CRASH",
                    f"HOST_CLASS={ctl.HOST_CLASS}",
                    f"SOURCE_SHA={ctl.SOURCE_SHA}",
                    f"STARTED_AT={record.get('started_at')}",
                    f"ENDED_AT={ctl.now()}",
                    f"ATM_ACTIVE={'YES' if ctl.atm_lock_summary().get('active') else 'NO'}",
                    'RESULT={"rc":1,"output":"AT_MOST_ONCE_REPLAY_BLOCKED"}',
                ])
                result_id = bus.post_result(body)
                record.update({"phase": "DONE", "result_comment_id": result_id, "result_body": None, "result_status": "INDETERMINATE_AFTER_CRASH"})
                comments[comment_id] = record
                commands[record["command_id"]] = record
                changed = True
        if changed:
            ctl.atomic_json(ctl.STATE_FILE, state)
    finally:
        lock.release()
    return 0


def main() -> int:
    rc = recover()
    if rc != 0:
        return rc
    return ctl.main()


if __name__ == "__main__":
    raise SystemExit(main())

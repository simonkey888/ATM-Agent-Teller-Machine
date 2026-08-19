#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

TASK_ID = "0x9e5a614a80fa0d0b802c21101cedb05ab06a0412edf7c4b874613f8c19f7d68c"
ARTIFACT = Path("deliverables/taskmarket_whack_protocol/index.html")
DIR = ARTIFACT.parent

text = ARTIFACT.read_text(encoding="utf-8")
files = [p for p in DIR.iterdir() if p.is_file()]
checks = {
    "single_final_file": len(files) == 1 and files[0].name == "index.html",
    "html_document": "<!doctype html>" in text.lower() and "</html>" in text.lower(),
    "threejs": "three" in text.lower() and "THREE.Scene" in text,
    "grid_3x3": "positions=[[-3,0,-3]" in text and "Number(e.key)" in text,
    "keyboard": "keydown" in text and "n>=1&&n<=9" in text,
    "touch_pointer": "pointerdown" in text and "touch-action:none" in text,
    "score": "id=\"score\"" in text and "score+=" in text,
    "local_high_score": "localStorage" in text and "whackProtocolHigh" in text,
    "fail_state": "Protocol Breached" in text and "endRun(" in text,
    "instant_restart": "RESTART RUN" in text and "function reset()" in text,
    "protected_targets": "safe:{" in text and "PROTECTED" in text,
    "delayed_rule": "wait:{" in text and "TOO EARLY" in text and "ARMED" in text,
    "ordered_rule": "order1:{" in text and "order2:{" in text and "WRONG ORDER" in text,
    "jackpot_chain": "jackpot:{" in text and "combo" in text and "JACKPOT" in text,
    "incorrect_cost": "function penalty(" in text and "lives--" in text,
    "protocol_changes": "protocols=[" in text and "setProtocol(protocolIndex+1)" in text,
    "limited_focus": "focus=2" in text and "useFocus" in text and "focus--" in text,
    "escalation": "wave++" in text and "1550-wave*16" in text,
    "no_external_assets": not re.search(r"<(?:img|audio|video)\b", text, re.I),
    "no_build_step": "type=\"module\"" not in text,
    "no_secrets": not re.search(r"(?:BEGIN [A-Z ]*PRIVATE KEY|sk-[A-Za-z0-9]{16,}|TASKMARKET_KEYSTORE|PRIVATE_KEY|API_TOKEN)", text),
}
failed = [name for name, ok in checks.items() if not ok]
sha = hashlib.sha256(ARTIFACT.read_bytes()).hexdigest()
result = {
    "schema": "ATM_ORDER009_R3_ARCADE_CHECK_V1",
    "task_id": TASK_ID,
    "artifact": str(ARTIFACT),
    "artifact_sha256": sha,
    "size_bytes": ARTIFACT.stat().st_size,
    "checks": checks,
    "pass": not failed,
    "failed": failed,
}
Path("order009-r3-arcade-check.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
for name, ok in checks.items():
    print(f"{name.upper()}={'PASS' if ok else 'FAIL'}")
print("ARTIFACT_SHA256=" + sha)
print("INDEPENDENT_CHECK=" + ("PASS" if not failed else "FAIL"))
if failed:
    raise SystemExit("ARCADE_CHECK_FAILED:" + ",".join(failed))

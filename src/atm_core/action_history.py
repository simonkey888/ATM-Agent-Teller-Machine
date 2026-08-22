from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


NON_ACTIONABLE_BOARD_STATES = {
    "CLAIM_LEASED", "CLAIMED", "WORK_LEASED", "WORKING", "CHECKING",
    "SUBMIT_LEASED", "SUBMITTED", "MONITORING", "PAYMENT_VERIFY", "PAID",
}
NON_ACTIONABLE_EPISODE_STATES = {
    "COMMITTED", "WAITING_EXTERNAL", "MONITORING", "PAYMENT_PENDING", "PAID",
}


def canonical_non_actionable_ids(board: Any, *, episodes_path: Path, state_path: Path | None = None) -> set[str]:
    ids: set[str] = set()
    rows = board._conn.execute("SELECT canonical_id,status FROM opportunities").fetchall()  # canonical durable board
    for row in rows:
        if str(row["status"] or "").upper() in NON_ACTIONABLE_BOARD_STATES:
            ids.add(str(row["canonical_id"]))
    if Path(episodes_path).exists():
        try:
            conn = sqlite3.connect(Path(episodes_path))
            for raw, in conn.execute("SELECT episode_json FROM episodes"):
                try:
                    episode = json.loads(str(raw))
                except Exception:
                    continue
                if str(episode.get("economic_state") or "").upper() in NON_ACTIONABLE_EPISODE_STATES:
                    cid = str(episode.get("canonical_opportunity_id") or "")
                    if cid:
                        ids.add(cid)
            conn.close()
        except sqlite3.Error:
            pass
    if state_path and Path(state_path).exists():
        try:
            state = json.loads(Path(state_path).read_text(encoding="utf-8"))
            active = state.get("active_opportunity") if isinstance(state, dict) else None
            phase = str(state.get("phase") or "").upper() if isinstance(state, dict) else ""
            if isinstance(active, dict) and (active.get("submission_id") or phase in {"CLAIM", "WORK", "CHECK", "SUBMIT", "MONITOR", "PAYMENT_VERIFY"}):
                cid = str(active.get("canonical_opportunity_id") or "")
                if cid:
                    ids.add(cid)
        except Exception:
            pass
    return ids


def install_money_board_history_guard(board: Any) -> None:
    """Prevent discovery refreshes from resetting durable in-flight/submitted states."""
    if getattr(board, "_order016_history_guard", False):
        return
    original = board.upsert_candidate

    def guarded(payload: dict[str, Any], **kwargs: Any):
        cid = str(payload.get("canonical_opportunity_id") or "")
        existing = board.get(cid) if cid else None
        if existing and str(existing.get("status") or "").upper() in NON_ACTIONABLE_BOARD_STATES:
            return existing
        return original(payload, **kwargs)

    board.upsert_candidate = guarded
    board._order016_history_guard = True


def record_duplicate_kill_without_reopening(board: Any, canonical_id: str, reason: str = "ALREADY_SUBMITTED_OR_IN_FLIGHT") -> None:
    row = board.get(canonical_id)
    if not row:
        return
    current = str(row.get("status") or "").upper()
    if current in NON_ACTIONABLE_BOARD_STATES:
        board._conn.execute(
            "UPDATE opportunities SET falsifier_verdict='KILL',rejection_reason=?,touched_at=datetime('now') WHERE canonical_id=?",
            (reason, canonical_id),
        )

from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from .models import RuntimeState


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.lkg = path.with_suffix(path.suffix + ".lkg")

    def _migrate(self, data: dict) -> RuntimeState:
        if data.get("schema_version") == 2:
            return RuntimeState.model_validate(data)
        # v1 had model-controlled paid/accepted/claimed counters. Never migrate those values.
        phase = str(data.get("phase") or "DISCOVER").upper()
        if phase not in {p.value for p in __import__("atm_core.models", fromlist=["Phase"]).Phase}:
            phase = "DISCOVER"
        active = data.get("active_opportunity")
        migrated = {
            "schema_version": 2,
            "phase": phase,
            "cycle": int(data.get("cycle") or 0),
            "target_paid_usd": data.get("target_paid_usd", 200),
            "active_opportunity": active,
            "last_result": data.get("last_result"),
            "last_error": "Migrated legacy v1 state; legacy monetary counters were deliberately discarded.",
            "human_gate": data.get("human_gate"),
            "provider_failures": [],
            "provider_circuits": {},
            "created_at": data.get("created_at") or datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            return RuntimeState.model_validate(migrated)
        except ValidationError:
            migrated["active_opportunity"] = None
            migrated["human_gate"] = None
            migrated["phase"] = "DISCOVER"
            return RuntimeState.model_validate(migrated)

    def _read_valid(self, path: Path) -> RuntimeState:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("state must be a JSON object")
        return self._migrate(data)

    def load(self, target_paid_usd: float | int | str = 200) -> RuntimeState:
        if not self.path.exists():
            state = RuntimeState(target_paid_usd=target_paid_usd)
            self.save(state)
            return state
        try:
            return self._read_valid(self.path)
        except (json.JSONDecodeError, ValidationError, OSError, ValueError):
            quarantine = self.path.with_name(
                self.path.name + f".corrupt-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex}"
            )
            os.replace(self.path, quarantine)
            if self.lkg.exists():
                try:
                    state = self._read_valid(self.lkg)
                    self.save(state)
                    return state
                except Exception:
                    pass
            state = RuntimeState(target_paid_usd=target_paid_usd)
            self.save(state)
            return state

    def save(self, state: RuntimeState) -> None:
        state.updated_at = datetime.now(timezone.utc)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            shutil.copy2(self.path, self.lkg)
        tmp = self.path.with_suffix(self.path.suffix + f".tmp-{uuid.uuid4().hex}")
        payload = state.model_dump(mode="json")
        with tmp.open("w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)
        shutil.copy2(self.path, self.lkg)

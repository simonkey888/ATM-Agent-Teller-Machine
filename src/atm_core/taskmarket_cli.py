from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .payments import HttpJsonClient

CANONICAL_TASKMARKET_WALLET = "0xd89Ef03bC3105C538529AC2657Bc4488c94ff4E4"
TASKMARKET_PACKAGE = "@lucid-agents/taskmarket@latest"


class TaskmarketCliError(RuntimeError):
    pass


class TaskmarketCapabilityBlocked(TaskmarketCliError):
    pass


class TaskmarketDuplicateSubmission(TaskmarketCliError):
    def __init__(self, submission_id: str):
        super().__init__(f"canonical wallet already submitted task: {submission_id}")
        self.submission_id = submission_id


@dataclass(frozen=True)
class TaskmarketSubmitReceipt:
    task_id: str
    wallet: str
    submission_id: str
    idempotency_key: str
    artifact_sha256: str
    manifest_verified: bool
    outgoing_spend_usd: str = "0"


Runner = Callable[[list[str], Path, dict[str, str]], subprocess.CompletedProcess[str]]


def _default_runner(args: list[str], home: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    cmd = ["npx", "-y", TASKMARKET_PACKAGE, *args]
    return subprocess.run(
        cmd,
        cwd=home,
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
        encoding="utf-8",
        errors="replace",
    )


def _json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    if not raw:
        raise TaskmarketCliError("first-party CLI returned empty output")
    try:
        value = json.loads(raw)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(raw):
        if ch != "{":
            continue
        try:
            value, _ = decoder.raw_decode(raw[idx:])
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            continue
    raise TaskmarketCliError("first-party CLI did not return a JSON object")


def _data(obj: dict[str, Any]) -> dict[str, Any]:
    value = obj.get("data")
    return value if isinstance(value, dict) else obj


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _extract_address(obj: dict[str, Any]) -> str:
    for row in _walk_dicts(obj):
        for key in ("address", "walletAddress", "workerAddress"):
            value = row.get(key)
            if isinstance(value, str) and re.fullmatch(r"0x[0-9a-fA-F]{40}", value):
                return value
    return ""


class TaskmarketCliLane:
    """Supervisor-owned first-party CLI signer boundary."""

    def __init__(
        self,
        *,
        signer_home: Path | None = None,
        staging_root: Path | None = None,
        canonical_wallet: str = CANONICAL_TASKMARKET_WALLET,
        runner: Runner | None = None,
        http: HttpJsonClient | None = None,
    ):
        self.signer_home = (signer_home or Path(os.getenv("ATM_TASKMARKET_SIGNER_HOME") or Path.home())).resolve()
        self.staging_root = (staging_root or Path(os.getenv("ATM_TASKMARKET_STAGING_ROOT") or ".atm/staging")).resolve()
        self.canonical_wallet = canonical_wallet
        self.runner = runner or _default_runner
        self.http = http or HttpJsonClient()

    @property
    def keystore_path(self) -> Path:
        return self.signer_home / ".taskmarket" / "keystore.json"

    def signer_present(self) -> bool:
        return self.keystore_path.is_file()

    def _cli_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["HOME"] = str(self.signer_home)
        env["USERPROFILE"] = str(self.signer_home)
        return env

    def _run(self, args: list[str]) -> dict[str, Any]:
        if not args or args[0].lower() == "init":
            raise TaskmarketCliError("forbidden TaskMarket CLI operation")
        cp = self.runner(args, self.signer_home, self._cli_env())
        if cp.returncode != 0:
            detail = (cp.stderr or "").strip()[-500:]
            raise TaskmarketCliError(f"first-party CLI failed rc={cp.returncode}: {detail}")
        return _json_object(cp.stdout or "")

    def address(self) -> str:
        if not self.signer_present():
            raise TaskmarketCapabilityBlocked("encrypted TaskMarket keystore is absent from signer runtime")
        address = _extract_address(self._run(["address"]))
        if not address:
            raise TaskmarketCliError("TaskMarket CLI address unavailable")
        if address.lower() != self.canonical_wallet.lower():
            raise TaskmarketCliError("TaskMarket signer wallet mismatch")
        return address

    def legal_allows_write(self) -> bool:
        data = _data(self._run(["legal", "status"]))
        accepted = data.get("accepted") is True
        enforcement_value = None
        for key in ("enforced", "enforcementEnabled", "acceptanceRequired", "legalEnforced"):
            if key in data:
                enforcement_value = data.get(key)
                break
        if enforcement_value is False:
            return True
        if enforcement_value is True:
            if not accepted:
                raise TaskmarketCliError("TaskMarket legal acceptance is currently enforced and not accepted")
            return True
        if not accepted:
            raise TaskmarketCliError("TaskMarket legal enforcement state is unknown and acceptance is not current")
        return True

    def preflight_signer(self) -> dict[str, Any]:
        """Resolve trusted signer identity/legal state before spending maker compute."""
        address = self.address()
        legal = self.legal_allows_write()
        return {"wallet": address, "legal_allows_write": legal, "signer_present": True}

    def task_get(self, task_id: str) -> dict[str, Any]:
        return _data(self._run(["task", "get", task_id]))

    def actions(self) -> dict[str, Any]:
        """Use TaskMarket's first-party action feed as the preferred event hint source."""
        if not self.signer_present():
            raise TaskmarketCapabilityBlocked("encrypted TaskMarket keystore is absent from signer runtime")
        return _data(self._run(["actions"]))

    def _submit_action(self, task: dict[str, Any]) -> dict[str, Any]:
        actions = [
            row
            for row in (task.get("pendingActions") or [])
            if isinstance(row, dict) and row.get("role") == "worker" and row.get("action") == "submit"
        ]
        if len(actions) != 1:
            raise TaskmarketCliError("exact worker submit pendingAction is unavailable")
        action = actions[0]
        eligible = action.get("eligibleAddress")
        if eligible not in (None, "") and str(eligible).lower() != self.canonical_wallet.lower():
            raise TaskmarketCliError("worker submit pendingAction is not eligible for canonical wallet")
        if action.get("requiresPayment") is not False or action.get("paymentAmount") not in (None, "", 0, "0"):
            raise TaskmarketCliError("TaskMarket submit requires outgoing payment")
        available_until = action.get("availableUntil")
        if available_until:
            try:
                deadline = datetime.fromisoformat(str(available_until).replace("Z", "+00:00"))
            except ValueError as exc:
                raise TaskmarketCliError("invalid submit availableUntil") from exc
            if deadline <= datetime.now(timezone.utc):
                raise TaskmarketCliError("TaskMarket submit pendingAction expired")
        return action

    def assert_side_effect_gate(self, task: dict[str, Any]) -> None:
        if str(task.get("status") or "").lower() != "open":
            raise TaskmarketCliError("TaskMarket task is not open")
        if task.get("submissionWindowOpen") is not True:
            raise TaskmarketCliError("TaskMarket submission window is closed")
        if task.get("stakeRequired") is not False:
            raise TaskmarketCliError("TaskMarket task requires stake")
        mode = str(task.get("mode") or "bounty").lower()
        if mode != "bounty":
            raise TaskmarketCliError("TaskMarket autonomous lane currently permits bounty mode only")
        self._submit_action(task)

    def submissions(self, task_id: str) -> list[dict[str, Any]]:
        data = self.http.get(f"https://api.taskmarket.dev/api/tasks/{task_id}/submissions")
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        if isinstance(data, dict):
            rows = data.get("submissions") or data.get("items") or data.get("data") or []
            return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
        return []

    def existing_submission(self, task_id: str) -> dict[str, Any] | None:
        for row in self.submissions(task_id):
            if str(row.get("workerAddress") or "").lower() == self.canonical_wallet.lower():
                return row
        return None

    def _artifact(self, artifact_path: str | Path) -> tuple[Path, str]:
        path = Path(artifact_path).resolve()
        try:
            path.relative_to(self.staging_root)
        except ValueError as exc:
            raise TaskmarketCliError("artifact is outside supervisor staging root") from exc
        if not path.is_file() or path.is_symlink():
            raise TaskmarketCliError("staged artifact must be one regular file")
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def _verify_manifest(self, task_id: str, submission_id: str, artifact_sha256: str) -> bool:
        manifest = self.http.get(
            f"https://api.taskmarket.dev/api/tasks/{task_id}/submissions/{submission_id}/manifest"
        )
        artifacts = manifest.get("artifacts") or [] if isinstance(manifest, dict) else []
        return any(
            isinstance(row, dict) and str(row.get("sha256Hash") or "").lower() == artifact_sha256.lower()
            for row in artifacts
        )

    def submit_checked(self, task_id: str, artifact_path: str | Path, *, checker_passed: bool) -> TaskmarketSubmitReceipt:
        if not checker_passed:
            raise TaskmarketCliError("independent checker did not pass")
        artifact, sha256 = self._artifact(artifact_path)
        preflight = self.preflight_signer()
        address = str(preflight["wallet"])

        duplicate = self.existing_submission(task_id)
        if duplicate:
            sid = str(duplicate.get("id") or duplicate.get("submissionId") or "")
            raise TaskmarketDuplicateSubmission(sid or "UNKNOWN")

        first = self.task_get(task_id)
        self.assert_side_effect_gate(first)
        final = self.task_get(task_id)
        self.assert_side_effect_gate(final)

        try:
            raw = _data(self._run(["task", "submit", task_id, "--file", str(artifact)]))
        except TaskmarketCliError:
            recovered = self.existing_submission(task_id)
            if recovered:
                sid = str(recovered.get("id") or recovered.get("submissionId") or "")
                if sid and self._verify_manifest(task_id, sid, sha256):
                    return TaskmarketSubmitReceipt(task_id, address, sid, "RECOVERED_FROM_AUTHORITATIVE_STATE", sha256, True)
            raise

        ok = raw.get("ok") is True
        submission_id = str(raw.get("submissionId") or raw.get("id") or "")
        idempotency_key = str(raw.get("idempotencyKey") or raw.get("idempotency_key") or "")
        if not ok or not submission_id or not idempotency_key:
            recovered = self.existing_submission(task_id)
            if recovered:
                recovered_id = str(recovered.get("id") or recovered.get("submissionId") or "")
                if recovered_id and self._verify_manifest(task_id, recovered_id, sha256):
                    return TaskmarketSubmitReceipt(task_id, address, recovered_id, idempotency_key or "RECOVERED_FROM_AUTHORITATIVE_STATE", sha256, True)
            raise TaskmarketCliError("TaskMarket submit response lacks ok/submissionId/idempotencyKey")

        authoritative = self.existing_submission(task_id)
        authoritative_id = str((authoritative or {}).get("id") or (authoritative or {}).get("submissionId") or "")
        if authoritative_id != submission_id:
            raise TaskmarketCliError("submitted ID is not authoritative for canonical wallet")
        if not self._verify_manifest(task_id, submission_id, sha256):
            raise TaskmarketCliError("TaskMarket manifest SHA256 does not match staged artifact")
        return TaskmarketSubmitReceipt(task_id, address, submission_id, idempotency_key, sha256, True)

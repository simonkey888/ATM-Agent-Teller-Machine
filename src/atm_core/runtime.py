from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psutil

from .models import ProviderFailure, ProviderFailureClass


class SingletonLockError(RuntimeError):
    pass


class ProcessLock:
    def __init__(self, path: Path):
        self.path = path
        self.instance_id = str(uuid.uuid4())
        self.acquired = False

    @staticmethod
    def _process_start(pid: int) -> float | None:
        try:
            return psutil.Process(pid).create_time()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return None

    def _is_active(self, payload: dict) -> bool:
        try:
            pid = int(payload["pid"])
            expected = float(payload["process_start_time"])
        except (KeyError, TypeError, ValueError):
            return False
        actual = self._process_start(pid)
        return actual is not None and abs(actual - expected) < 1.0

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    payload = json.loads(self.path.read_text(encoding="utf-8"))
                except Exception:
                    payload = {}
                if self._is_active(payload):
                    raise SingletonLockError(
                        f"ATM already running pid={payload.get('pid')} instance={payload.get('instance_id')}"
                    )
                stale = self.path.with_name(self.path.name + f".stale-{uuid.uuid4().hex}")
                try:
                    os.replace(self.path, stale)
                except FileNotFoundError:
                    pass
                continue
            else:
                try:
                    payload = {
                        "pid": os.getpid(),
                        "process_start_time": psutil.Process(os.getpid()).create_time(),
                        "instance_id": self.instance_id,
                        "acquired_at": datetime.now(timezone.utc).isoformat(),
                    }
                    os.write(fd, (json.dumps(payload) + "\n").encode("utf-8"))
                    os.fsync(fd)
                finally:
                    os.close(fd)
                self.acquired = True
                return
        raise SingletonLockError("could not acquire ATM singleton lock")

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("instance_id") == self.instance_id:
                self.path.unlink(missing_ok=True)
        finally:
            self.acquired = False

    def __enter__(self) -> "ProcessLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


_HTTP_RE = re.compile(r"HTTP\s+(\d{3})", re.IGNORECASE)
_SESSION_RE = re.compile(r"session_id\s*[:=]\s*([A-Za-z0-9_-]+)", re.IGNORECASE)
_RETRY_RE = re.compile(r"retry[- ]after\s*[:=]?\s*(\d+)", re.IGNORECASE)


def classify_provider_failure(provider: str, model: str | None, message: str) -> ProviderFailure:
    status_match = _HTTP_RE.search(message)
    status = int(status_match.group(1)) if status_match else None
    session_match = _SESSION_RE.search(message)
    retry_match = _RETRY_RE.search(message)
    lower = message.lower()

    if status == 429:
        cls = ProviderFailureClass.QUOTA if "usage limit" in lower or "quota" in lower else ProviderFailureClass.RATE_LIMIT
    elif status in {401, 403}:
        cls = ProviderFailureClass.AUTH
    elif status == 400:
        cls = ProviderFailureClass.BAD_REQUEST
    elif "timed out" in lower or "timeout" in lower:
        cls = ProviderFailureClass.TIMEOUT
    elif "connection" in lower or "network" in lower or "dns" in lower:
        cls = ProviderFailureClass.NETWORK
    else:
        cls = ProviderFailureClass.UNKNOWN

    return ProviderFailure(
        provider=provider,
        model=model,
        session_id=session_match.group(1) if session_match else None,
        error_class=cls,
        http_status=status,
        retry_after_seconds=int(retry_match.group(1)) if retry_match else None,
        message=message[-4000:],
    )


def circuit_reopen_at(failure: ProviderFailure, default_429_seconds: int = 900) -> datetime | None:
    if failure.error_class not in {ProviderFailureClass.RATE_LIMIT, ProviderFailureClass.QUOTA}:
        return None
    delay = failure.retry_after_seconds or default_429_seconds
    return datetime.now(timezone.utc) + timedelta(seconds=max(60, delay))

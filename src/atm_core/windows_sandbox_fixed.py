from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from . import sandbox_boundary as legacy
from .sandbox_boundary_v2 import SandboxError, SandboxLimitExceeded, SandboxUnavailable

NORMALIZED_SCHEMA = "ATM_STRUCTURAL_SANDBOX_V2"


class WindowsStructuralSandbox(legacy.StructuralSandbox):
    """ORDER-018 Windows compatibility backend with the real AppContainer cwd binding fixed."""

    def _run_windows(self, command, env, sandbox_root, write_paths, policy):
        # P0-RT1: signature is (command, env, cwd, sandbox_root, write_paths, limits).
        win = legacy._launch_windows_appcontainer(command, env, sandbox_root, sandbox_root, write_paths, policy.limits)
        kernel32 = legacy.ctypes.WinDLL("kernel32", use_last_error=True)
        deadline = legacy.time.monotonic() + policy.limits.wall_seconds
        try:
            ps = legacy.psutil.Process(win.pid)
            while True:
                wait = kernel32.WaitForSingleObject(legacy.ctypes.c_void_p(win.process_handle), 20)
                if wait == 0: break
                limit_class = None
                if legacy.time.monotonic() >= deadline: limit_class = "WALL_TIME"
                else:
                    try:
                        if ps.memory_info().rss > policy.limits.memory_mb * 1024 * 1024: limit_class = "MEMORY"
                        elif ps.num_handles() > policy.limits.max_open_files: limit_class = "OPEN_HANDLES"
                        elif len(ps.children(recursive=True)) + 1 > policy.limits.max_processes: limit_class = "PROCESS_COUNT"
                        elif legacy._tree_bytes(sandbox_root, ceiling=policy.limits.max_file_bytes * 2) > policy.limits.max_file_bytes * 2: limit_class = "FILE_OUTPUT"
                    except (legacy.psutil.NoSuchProcess, legacy.psutil.AccessDenied): pass
                if limit_class:
                    kernel32.TerminateJobObject(legacy.ctypes.c_void_p(win.job_handle), 72)
                    kernel32.WaitForSingleObject(legacy.ctypes.c_void_p(win.process_handle), 5000)
                    raise legacy.SandboxLimitExceeded(limit_class)
            code = legacy.ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(legacy.ctypes.c_void_p(win.process_handle), legacy.ctypes.byref(code)):
                raise legacy.SandboxError("SANDBOX_WINDOWS_EXIT_CODE_FAILED")
            if int(code.value) != 0: raise legacy.SandboxError(f"SANDBOX_CHILD_FAILED:{int(code.value)}")
        finally: legacy._cleanup_windows_process(win)

    def run(self, *, worker_name: str, request: Mapping[str, Any], policy, writable_paths: Iterable[Path] = ()) -> dict[str, Any]:
        try:
            envelope = super().run(worker_name=worker_name, request=request, policy=policy, writable_paths=writable_paths)
        except legacy.SandboxUnavailable as exc:
            raise SandboxUnavailable(str(exc)) from exc
        except legacy.SandboxLimitExceeded as exc:
            raise SandboxLimitExceeded(exc.limit_class, getattr(exc, "detail", "")) from exc
        except legacy.SandboxError as exc:
            raise SandboxError(str(exc)) from exc
        proof = dict(envelope.get("sandbox") or {})
        proof["schema"] = NORMALIZED_SCHEMA; proof["backend"] = "WINDOWS_APPCONTAINER_JOB"; proof["owner_home_mounted"] = False
        envelope["sandbox"] = proof
        envelope["sandbox_receipt_digest"] = hashlib.sha256(json.dumps(proof, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
        return envelope

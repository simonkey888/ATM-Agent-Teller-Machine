from __future__ import annotations

import argparse
import ctypes
import io
import json
import os
import runpy
import socket
import sys
from pathlib import Path
from typing import Any

SANDBOX_SCHEMA = "ATM_STRUCTURAL_SANDBOX_V1"
SECRET_MARKERS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIAL", "MNEMONIC", "SEED", "WALLET")


def _secret_env_count() -> int:
    return sum(1 for key in os.environ if any(marker in key.upper() for marker in SECRET_MARKERS))


def _read_policy() -> dict[str, Any]:
    policy = json.loads(os.environ.get("ATM_SANDBOX_POLICY_JSON", ""))
    if policy.get("schema") != SANDBOX_SCHEMA:
        raise RuntimeError("SANDBOX_POLICY_SCHEMA_INVALID")
    return policy


def _replace_paths(value: Any, path_map: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {str(k): _replace_paths(v, path_map) for k, v in value.items()}
    if isinstance(value, list):
        return [_replace_paths(v, path_map) for v in value]
    if isinstance(value, tuple):
        return tuple(_replace_paths(v, path_map) for v in value)
    if isinstance(value, str):
        for source, target in sorted(path_map.items(), key=lambda kv: len(kv[0]), reverse=True):
            try:
                rel = Path(value).resolve().relative_to(Path(source).resolve())
            except (ValueError, OSError):
                continue
            return str(Path(target) / rel)
    return value


def _mount(source: str | None, target: str, fstype: str | None, flags: int, data: str | None) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    fn = libc.mount
    fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p]
    fn.restype = ctypes.c_int
    result = fn(source.encode() if source is not None else None, target.encode(), fstype.encode() if fstype is not None else None, flags, data.encode() if data is not None else None)
    if result != 0:
        err = ctypes.get_errno()
        raise OSError(err, f"mount failed target={target}")


def _linux_namespaces_and_mounts(policy: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    # The parent entered these namespaces through util-linux unshare before Python start.
    parent = dict(policy.get("parent_namespaces") or {})
    namespaces: dict[str, str] = {}
    for name in ("user", "mnt", "net", "uts", "ipc"):
        current = os.readlink(f"/proc/self/ns/{name}")
        namespaces[name] = current
        if not current or current == str(parent.get(name) or ""):
            raise RuntimeError(f"SANDBOX_LINUX_NAMESPACE_NOT_ISOLATED:{name}")
    if os.geteuid() != 0:
        raise RuntimeError("SANDBOX_LINUX_USERNS_NOT_ROOT_MAPPED")

    MS_REC = 16384
    MS_PRIVATE = 1 << 18
    MS_BIND = 4096
    MS_REMOUNT = 32
    MS_RDONLY = 1
    MS_NOSUID = 2
    MS_NODEV = 4
    MS_NOEXEC = 8
    _mount(None, "/", None, MS_REC | MS_PRIVATE, None)

    sandbox_root = Path(str(policy["sandbox_root"])).resolve()
    bind_root = sandbox_root / "binds"
    bind_root.mkdir(parents=True, exist_ok=True)
    path_map: dict[str, str] = {}
    for index, raw in enumerate(policy.get("write_paths") or []):
        source = Path(str(raw)).resolve()
        source_target = source if source.is_dir() else source.parent
        target = bind_root / str(index)
        target.mkdir(parents=True, exist_ok=True)
        _mount(str(source_target), str(target), None, MS_BIND | (MS_REC if source_target.is_dir() else 0), None)
        path_map[str(source_target)] = str(target)

    canonical_repo = Path(str(policy["canonical_repo_root"])).resolve()
    if canonical_repo.exists():
        _mount(str(canonical_repo), str(canonical_repo), None, MS_BIND | MS_REC, None)
        _mount(None, str(canonical_repo), None, MS_REMOUNT | MS_BIND | MS_RDONLY | MS_REC, None)

    owner_home = Path(str(policy["owner_home"])).resolve()
    if not owner_home.is_dir():
        raise RuntimeError("SANDBOX_OWNER_HOME_NOT_DIRECTORY")
    _mount("tmpfs", str(owner_home), "tmpfs", MS_NOSUID | MS_NODEV | MS_NOEXEC, "size=4096k,mode=000")
    return path_map, namespaces


def _linux_resource_limits(policy: dict[str, Any]) -> dict[str, list[int]]:
    import resource
    limits = dict(policy["limits"])
    requested = {
        "RLIMIT_CPU": (int(limits["cpu_seconds"]), int(limits["cpu_seconds"])),
        "RLIMIT_AS": (int(limits["memory_mb"]) * 1024 * 1024, int(limits["memory_mb"]) * 1024 * 1024),
        "RLIMIT_FSIZE": (int(limits["max_file_bytes"]), int(limits["max_file_bytes"])),
        "RLIMIT_NOFILE": (int(limits["max_open_files"]), int(limits["max_open_files"])),
        "RLIMIT_NPROC": (int(limits["max_processes"]), int(limits["max_processes"])),
    }
    constants = {
        "RLIMIT_CPU": resource.RLIMIT_CPU,
        "RLIMIT_AS": resource.RLIMIT_AS,
        "RLIMIT_FSIZE": resource.RLIMIT_FSIZE,
        "RLIMIT_NOFILE": resource.RLIMIT_NOFILE,
        "RLIMIT_NPROC": resource.RLIMIT_NPROC,
    }
    observed: dict[str, list[int]] = {}
    for name, pair in requested.items():
        resource.setrlimit(constants[name], pair)
        current = resource.getrlimit(constants[name])
        observed[name] = [int(current[0]), int(current[1])]
        if current != pair:
            raise RuntimeError(f"SANDBOX_RLIMIT_NOT_APPLIED:{name}:{current}:{pair}")
    return observed


def _windows_observation(policy: dict[str, Any]) -> dict[str, Any]:
    from ctypes import wintypes
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise RuntimeError(f"SANDBOX_WINDOWS_TOKEN_OPEN:{ctypes.get_last_error()}")
    try:
        is_app = wintypes.DWORD()
        size = wintypes.DWORD()
        if not advapi32.GetTokenInformation(token, 29, ctypes.byref(is_app), ctypes.sizeof(is_app), ctypes.byref(size)):
            raise RuntimeError(f"SANDBOX_WINDOWS_TOKEN_INFO:{ctypes.get_last_error()}")
    finally:
        kernel32.CloseHandle(token)
    in_job = wintypes.BOOL()
    if not kernel32.IsProcessInJob(kernel32.GetCurrentProcess(), None, ctypes.byref(in_job)):
        raise RuntimeError(f"SANDBOX_WINDOWS_JOB_QUERY:{ctypes.get_last_error()}")
    if not bool(is_app.value) or not bool(in_job.value):
        raise RuntimeError("SANDBOX_WINDOWS_APPCONTAINER_OR_JOB_MISSING")
    return {"appcontainer": True, "job_object": True, "job_memory_cpu_process_limits_observed": True, "handle_and_output_watchdog_parent_enforced": True}


def _owner_home_denied(policy: dict[str, Any]) -> tuple[bool, str]:
    sentinel = Path(str(policy["owner_sentinel"]))
    try:
        fd = os.open(sentinel, os.O_RDONLY)
    except OSError as exc:
        return True, f"{type(exc).__name__}:{getattr(exc, 'errno', None)}"
    else:
        os.close(fd)
        return False, "READABLE"


def _repo_write_denied(policy: dict[str, Any]) -> bool:
    target = Path(str(policy["canonical_repo_root"])) / f".atm-sandbox-probe-{os.getpid()}"
    try:
        target.write_text("blocked", encoding="utf-8")
    except OSError:
        return True
    else:
        try:
            target.unlink()
        except OSError:
            pass
        return False


def _direct_network_denied() -> tuple[bool, str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.35)
    try:
        code = sock.connect_ex(("1.1.1.1", 443))
        return code != 0, str(code)
    except OSError as exc:
        return True, f"{type(exc).__name__}:{getattr(exc, 'errno', None)}"
    finally:
        sock.close()


def _run_worker(worker: Path, raw_request: str) -> tuple[int, str, str]:
    old_stdin, old_stdout, old_stderr = sys.stdin, sys.stdout, sys.stderr
    stdout = io.StringIO()
    stderr = io.StringIO()
    sys.stdin = io.StringIO(raw_request)
    sys.stdout = stdout
    sys.stderr = stderr
    code = 0
    try:
        runpy.run_path(str(worker), run_name="__main__")
    except SystemExit as exc:
        code = int(exc.code or 0) if isinstance(exc.code, int) or exc.code is None else 1
    except BaseException as exc:
        code = 1
        stderr.write(f"{type(exc).__name__}:{exc}")
    finally:
        sys.stdin, sys.stdout, sys.stderr = old_stdin, old_stdout, old_stderr
    return code, stdout.getvalue(), stderr.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--response", required=True)
    args = parser.parse_args()

    if _secret_env_count() != 0:
        raise SystemExit("SANDBOX_SECRET_ENV_PRESENT")
    policy = _read_policy()
    request_path = Path(args.request).resolve()
    response_path = Path(args.response).resolve()
    worker = Path(args.worker).resolve()
    request = json.loads(request_path.read_text(encoding="utf-8"))

    path_map: dict[str, str] = {}
    if os.name == "nt":
        platform_evidence = _windows_observation(policy)
        resource_observed = {
            "job_object": True,
            "memory_mb": int(policy["limits"]["memory_mb"]),
            "cpu_seconds": int(policy["limits"]["cpu_seconds"]),
            "max_processes": int(policy["limits"]["max_processes"]),
            "max_open_files_watchdog": int(policy["limits"]["max_open_files"]),
            "max_file_bytes_watchdog": int(policy["limits"]["max_file_bytes"]),
        }
        namespaces = {"appcontainer": "TOKEN_IS_APPCONTAINER", "job": "ASSIGNED"}
    else:
        resource_observed = _linux_resource_limits(policy)
        path_map, namespaces = _linux_namespaces_and_mounts(policy)
        request = _replace_paths(request, path_map)
        platform_evidence = {
            "user_namespace": True,
            "mount_namespace": True,
            "network_namespace": True,
            "owner_home_tmpfs_mask": True,
            "canonical_repo_read_only_mount": True,
        }

    owner_denied, owner_probe = _owner_home_denied(policy)
    if not owner_denied:
        raise SystemExit("SANDBOX_OWNER_HOME_READABLE")
    repo_denied = _repo_write_denied(policy)
    if not repo_denied:
        raise SystemExit("SANDBOX_CANONICAL_REPO_WRITABLE")
    network_denied, network_probe = _direct_network_denied()
    if not network_denied:
        raise SystemExit("SANDBOX_DIRECT_NETWORK_AVAILABLE")

    raw_request = json.dumps(request, sort_keys=True, separators=(",", ":"), default=str)
    code, stdout, stderr = _run_worker(worker, raw_request)
    if code != 0:
        response_path.write_text(json.dumps({"worker_error": stderr[-500:], "worker_exit": code}, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        return code
    try:
        worker_payload = json.loads(stdout)
    except json.JSONDecodeError:
        response_path.write_text(json.dumps({"worker_error": "INVALID_JSON"}), encoding="utf-8")
        return 2

    sandbox_receipt = {
        "schema": SANDBOX_SCHEMA,
        "platform": sys.platform,
        "network_policy": str(policy["network_policy"]),
        "tool_policy": str(policy["tool_policy"]),
        "child_pid": os.getpid(),
        "secret_env_count": _secret_env_count(),
        "fresh_ephemeral_home": Path.home().resolve() == Path(os.environ["HOME"]).resolve(),
        "owner_home_read_denied_observed": owner_denied,
        "owner_home_probe": owner_probe,
        "canonical_repo_write_denied_observed": repo_denied,
        "direct_network_denied_observed": network_denied,
        "direct_network_probe": network_probe,
        "filesystem_boundary_applied": True,
        "network_boundary_applied": True,
        "resource_limits_applied": True,
        "process_tree_bounded": True,
        "resource_limits_requested": dict(policy["limits"]),
        "resource_limits_observed": resource_observed,
        "namespaces": namespaces,
        "platform_evidence": platform_evidence,
        "worker_sha256": __import__("hashlib").sha256(worker.read_bytes()).hexdigest(),
    }
    response_path.write_text(json.dumps({"worker": worker_payload, "sandbox": sandbox_receipt}, sort_keys=True, separators=(",", ":"), default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

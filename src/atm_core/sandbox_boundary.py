from __future__ import annotations

import base64
import ctypes
import hashlib
import ipaddress
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import psutil

SANDBOX_SCHEMA = "ATM_STRUCTURAL_SANDBOX_V1"
ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
SECRET_MARKERS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIAL", "MNEMONIC", "SEED", "WALLET")
MAX_PROXY_BODY_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class SandboxLimits:
    cpu_seconds: int = 30
    memory_mb: int = 512
    max_file_bytes: int = 8 * 1024 * 1024
    max_open_files: int = 64
    max_processes: int = 16
    wall_seconds: int = 120

    def normalized(self) -> "SandboxLimits":
        return SandboxLimits(
            cpu_seconds=max(1, min(120, int(self.cpu_seconds))),
            memory_mb=max(128, min(2048, int(self.memory_mb))),
            max_file_bytes=max(65536, min(64 * 1024 * 1024, int(self.max_file_bytes))),
            max_open_files=max(32, min(256, int(self.max_open_files))),
            max_processes=max(2, min(64, int(self.max_processes))),
            wall_seconds=max(2, min(300, int(self.wall_seconds))),
        )


@dataclass(frozen=True)
class SandboxPolicy:
    network_policy: str
    tool_policy: str
    limits: SandboxLimits = SandboxLimits()
    repo_write: bool = False

    def normalized(self) -> "SandboxPolicy":
        network = str(self.network_policy).upper()
        if network not in {"DENY", "READ_ONLY_PUBLIC_HTTPS"}:
            raise ValueError("SANDBOX_NETWORK_POLICY_INVALID")
        return SandboxPolicy(network, str(self.tool_policy), self.limits.normalized(), bool(self.repo_write))


class SandboxError(RuntimeError):
    pass


class SandboxUnavailable(SandboxError):
    pass


class SandboxLimitExceeded(SandboxError):
    def __init__(self, limit_class: str, detail: str = ""):
        super().__init__(f"SANDBOX_LIMIT_EXCEEDED:{limit_class}:{detail}")
        self.limit_class = limit_class
        self.detail = detail


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def _safe_temp_base(owner_home: Path) -> Path:
    candidates: list[Path] = []
    for raw in (os.environ.get("RUNNER_TEMP"), tempfile.gettempdir()):
        if raw:
            candidates.append(Path(raw))
    candidates.extend((Path(r"C:\Windows\Temp"), Path(r"C:\ProgramData")) if os.name == "nt" else (Path("/tmp"), Path("/var/tmp")))
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            resolved.mkdir(parents=True, exist_ok=True)
            if _inside(resolved, owner_home):
                continue
            probe = resolved / f".atm-sandbox-write-{uuid.uuid4().hex}"
            probe.write_text("1", encoding="utf-8")
            probe.unlink()
            return resolved
        except OSError:
            continue
    raise SandboxUnavailable("SANDBOX_NO_TEMP_OUTSIDE_OWNER_HOME")


def _public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for info in infos:
        raw = str(info[4][0]).split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            return False
        if not ip.is_global:
            return False
    return True


class ReadOnlyHttpsFileProxy:
    """Parent-owned HTTPS bridge; sandbox children never receive IP network authority."""

    def __init__(self, root: Path, *, body_limit: int = MAX_PROXY_BODY_BYTES):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.body_limit = int(body_limit)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="atm-sandbox-https-proxy", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.is_set():
            for req_path in list(self.root.glob("*.request.json")):
                response_path = req_path.with_name(req_path.name.replace(".request.json", ".response.json"))
                if response_path.exists():
                    continue
                try:
                    request = json.loads(req_path.read_text(encoding="utf-8"))
                    response = self._handle(request)
                except Exception as exc:
                    response = {"ok": False, "error_class": type(exc).__name__}
                tmp = response_path.with_suffix(response_path.suffix + ".tmp")
                try:
                    tmp.write_text(json.dumps(response, sort_keys=True, separators=(",", ":")), encoding="utf-8")
                    os.replace(tmp, response_path)
                except OSError:
                    pass
            self._stop.wait(0.01)

    def _handle(self, request: Mapping[str, Any]) -> dict[str, Any]:
        url = str(request.get("url") or "")
        method = str(request.get("method") or "GET").upper()
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.port not in (None, 443) or not parsed.hostname:
            raise PermissionError("SANDBOX_PROXY_HTTPS_ONLY")
        if parsed.username or parsed.password or method not in {"GET", "HEAD"}:
            raise PermissionError("SANDBOX_PROXY_READ_ONLY")
        if not _public_host(parsed.hostname):
            raise PermissionError("SANDBOX_PROXY_PUBLIC_HOST_REQUIRED")
        headers: dict[str, str] = {}
        for key, value in dict(request.get("headers") or {}).items():
            upper = str(key).upper()
            if any(marker in upper for marker in SECRET_MARKERS) or upper in {"AUTHORIZATION", "COOKIE", "PROXY-AUTHORIZATION"}:
                continue
            headers[str(key)] = str(value)
        headers["User-Agent"] = headers.get("User-Agent", "ATM-Structural-Sandbox/1.0")
        upstream = urllib.request.Request(url, headers=headers, method=method)
        with urllib.request.urlopen(upstream, timeout=min(20, int(request.get("timeout") or 20))) as response:
            body = response.read(self.body_limit + 1)
            if len(body) > self.body_limit:
                raise RuntimeError("SANDBOX_PROXY_BODY_LIMIT")
            safe_headers = {str(k): str(v) for k, v in response.headers.items() if str(k).lower() not in {"set-cookie", "authorization", "proxy-authenticate"}}
            return {
                "ok": True,
                "status": int(getattr(response, "status", response.getcode())),
                "url": str(getattr(response, "url", url)),
                "headers": safe_headers,
                "body_b64": base64.b64encode(body).decode("ascii"),
            }


def _clean_env(home: Path, policy: SandboxPolicy, sandbox_root: Path, proxy_dir: Path, owner_home: Path, sentinel: Path, runtime_root: Path, write_paths: Iterable[Path]) -> dict[str, str]:
    parent_ns = {}
    if sys.platform.startswith("linux"):
        for name in ("user", "mnt", "net", "uts", "ipc"):
            parent_ns[name] = os.readlink(f"/proc/self/ns/{name}")
    env = {
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": str(home),
        "USERPROFILE": str(home),
        "TMP": str(home),
        "TEMP": str(home),
        "ATM_SANDBOX_SCHEMA": SANDBOX_SCHEMA,
        "ATM_SANDBOX_HTTPS_PROXY_DIR": str(proxy_dir),
        "ATM_SANDBOX_POLICY_JSON": json.dumps({
            "schema": SANDBOX_SCHEMA,
            "network_policy": policy.network_policy,
            "tool_policy": policy.tool_policy,
            "limits": asdict(policy.limits),
            "sandbox_root": str(sandbox_root),
            "runtime_root": str(runtime_root),
            "canonical_repo_root": str(ROOT),
            "owner_home": str(owner_home),
            "owner_sentinel": str(sentinel),
            "write_paths": [str(Path(p).resolve()) for p in write_paths],
            "parent_namespaces": parent_ns,
        }, sort_keys=True, separators=(",", ":")),
    }
    for key in ("SYSTEMROOT", "WINDIR", "SSL_CERT_FILE", "SSL_CERT_DIR"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    if any(any(marker in key.upper() for marker in SECRET_MARKERS) for key in env):
        raise SandboxError("SANDBOX_ENV_SECRET_FILTER_FAILED")
    return env


def _tree_bytes(path: Path, *, ceiling: int) -> int:
    total = 0
    try:
        for item in path.rglob("*"):
            if item.is_file():
                try:
                    total += item.stat().st_size
                except OSError:
                    continue
                if total > ceiling:
                    return total
    except OSError:
        pass
    return total


class _WindowsProcess:
    def __init__(self, process_handle: int, thread_handle: int, job_handle: int, pid: int, attr_buf: Any, profile_name: str, sid_string: str, granted: list[Path]):
        self.process_handle = process_handle
        self.thread_handle = thread_handle
        self.job_handle = job_handle
        self.pid = pid
        self.attr_buf = attr_buf
        self.profile_name = profile_name
        self.sid_string = sid_string
        self.granted = granted


def _windows_sid_string(sid: int) -> str:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    out = ctypes.c_wchar_p()
    if not advapi32.ConvertSidToStringSidW(ctypes.c_void_p(sid), ctypes.byref(out)):
        raise SandboxUnavailable(f"SANDBOX_WINDOWS_SID_STRING:{ctypes.get_last_error()}")
    try:
        return str(out.value)
    finally:
        kernel32.LocalFree(ctypes.cast(out, ctypes.c_void_p))


def _windows_acl(path: Path, sid_string: str, permission: str, *, remove: bool = False) -> None:
    args = ["icacls", str(path), "/remove:g", f"*{sid_string}", "/T", "/C", "/Q"] if remove else ["icacls", str(path), "/grant", f"*{sid_string}:(OI)(CI){permission}", "/T", "/C", "/Q"]
    cp = subprocess.run(args, capture_output=True, text=True, check=False, timeout=90)
    if cp.returncode != 0:
        raise SandboxUnavailable("SANDBOX_WINDOWS_ACL_FAILED")


def _launch_windows_appcontainer(command: list[str], env: Mapping[str, str], cwd: Path, sandbox_root: Path, write_paths: tuple[Path, ...], limits: SandboxLimits) -> _WindowsProcess:
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    userenv = ctypes.WinDLL("userenv", use_last_error=True)

    class SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]
    class SECURITY_CAPABILITIES(ctypes.Structure):
        _fields_ = [("AppContainerSid", ctypes.c_void_p), ("Capabilities", ctypes.POINTER(SID_AND_ATTRIBUTES)), ("CapabilityCount", wintypes.DWORD), ("Reserved", wintypes.DWORD)]
    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD), ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD), ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD), ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)), ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]
    class STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]
    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE), ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]
    class BASIC_LIMIT(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong), ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]
    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [("ReadOperationCount", ctypes.c_ulonglong), ("WriteOperationCount", ctypes.c_ulonglong), ("OtherOperationCount", ctypes.c_ulonglong), ("ReadTransferCount", ctypes.c_ulonglong), ("WriteTransferCount", ctypes.c_ulonglong), ("OtherTransferCount", ctypes.c_ulonglong)]
    class EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    userenv.CreateAppContainerProfile.restype = ctypes.c_long
    profile_name = f"ATM.AgentTeller.Sandbox.{uuid.uuid4().hex}"
    app_sid = ctypes.c_void_p()
    hr = userenv.CreateAppContainerProfile(profile_name, "ATM Sandbox", "Ephemeral ATM structural sandbox", None, 0, ctypes.byref(app_sid))
    if int(hr) < 0 or not app_sid.value:
        raise SandboxUnavailable(f"SANDBOX_WINDOWS_APPCONTAINER_CREATE:{int(hr)}")
    sid_string = _windows_sid_string(int(app_sid.value))
    granted: list[Path] = []
    job = None
    attr_buf = None
    try:
        _windows_acl(sandbox_root, sid_string, "M")
        granted.append(sandbox_root)
        # Python itself is outside the sandbox on GitHub-hosted runners. Grant this
        # AppContainer read/execute only to the interpreter runtime, never owner HOME.
        python_root = Path(sys.base_prefix).resolve()
        _windows_acl(python_root, sid_string, "RX")
        granted.append(python_root)
        for path in write_paths:
            target = path if path.is_dir() else path.parent
            if _inside(target, Path.home().resolve()):
                # Explicit durable state subdirectory is allowed; the rest of HOME remains inaccessible.
                pass
            _windows_acl(target, sid_string, "M")
            granted.append(target)

        size = ctypes.c_size_t()
        kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        attr_buf = ctypes.create_string_buffer(size.value)
        attr_list = ctypes.cast(attr_buf, ctypes.c_void_p)
        if not kernel32.InitializeProcThreadAttributeList(attr_list, 1, 0, ctypes.byref(size)):
            raise SandboxUnavailable(f"SANDBOX_WINDOWS_ATTR_INIT:{ctypes.get_last_error()}")
        caps = SECURITY_CAPABILITIES(app_sid, None, 0, 0)
        if not kernel32.UpdateProcThreadAttribute(attr_list, 0, ctypes.c_size_t(0x00020009), ctypes.byref(caps), ctypes.sizeof(caps), None, None):
            raise SandboxUnavailable(f"SANDBOX_WINDOWS_ATTR_SECURITY:{ctypes.get_last_error()}")

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise SandboxUnavailable(f"SANDBOX_WINDOWS_JOB_CREATE:{ctypes.get_last_error()}")
        info = EXTENDED_LIMIT()
        info.BasicLimitInformation.LimitFlags = 0x2 | 0x8 | 0x100 | 0x2000
        info.BasicLimitInformation.PerProcessUserTimeLimit = int(limits.cpu_seconds * 10_000_000)
        info.BasicLimitInformation.ActiveProcessLimit = int(limits.max_processes)
        info.ProcessMemoryLimit = int(limits.memory_mb * 1024 * 1024)
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise SandboxUnavailable(f"SANDBOX_WINDOWS_JOB_LIMITS:{ctypes.get_last_error()}")

        si = STARTUPINFOEXW()
        si.StartupInfo.cb = ctypes.sizeof(si)
        si.lpAttributeList = attr_list
        pi = PROCESS_INFORMATION()
        cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
        env_block = "\0".join(f"{k}={v}" for k, v in sorted(env.items(), key=lambda kv: kv[0].upper())) + "\0\0"
        env_buf = ctypes.create_unicode_buffer(env_block)
        flags = 0x00080000 | 0x00000400 | 0x00000004 | 0x08000000
        if not kernel32.CreateProcessW(command[0], cmdline, None, None, False, flags, env_buf, str(cwd), ctypes.byref(si), ctypes.byref(pi)):
            raise SandboxUnavailable(f"SANDBOX_WINDOWS_CREATE_PROCESS:{ctypes.get_last_error()}")
        if not kernel32.AssignProcessToJobObject(job, pi.hProcess):
            kernel32.TerminateProcess(pi.hProcess, 70)
            raise SandboxUnavailable(f"SANDBOX_WINDOWS_ASSIGN_JOB:{ctypes.get_last_error()}")
        if kernel32.ResumeThread(pi.hThread) == 0xFFFFFFFF:
            kernel32.TerminateJobObject(job, 71)
            raise SandboxUnavailable(f"SANDBOX_WINDOWS_RESUME:{ctypes.get_last_error()}")
        return _WindowsProcess(int(pi.hProcess), int(pi.hThread), int(job), int(pi.dwProcessId), attr_buf, profile_name, sid_string, granted)
    except Exception:
        for path in reversed(granted):
            try:
                _windows_acl(path, sid_string, "", remove=True)
            except Exception:
                pass
        if job:
            kernel32.CloseHandle(job)
        userenv.DeleteAppContainerProfile(profile_name)
        raise


def _cleanup_windows_process(proc: _WindowsProcess) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    try:
        kernel32.CloseHandle(ctypes.c_void_p(proc.thread_handle))
        kernel32.CloseHandle(ctypes.c_void_p(proc.process_handle))
        kernel32.CloseHandle(ctypes.c_void_p(proc.job_handle))
    finally:
        for path in reversed(proc.granted):
            try:
                _windows_acl(path, proc.sid_string, "", remove=True)
            except Exception:
                pass
        try:
            userenv.DeleteAppContainerProfile(proc.profile_name)
        except Exception:
            pass


class StructuralSandbox:
    """Shared structural process boundary for role workers and Forge."""

    def __init__(self, *, source_root: Path = SRC_ROOT):
        self.source_root = Path(source_root).resolve()

    def run(self, *, worker_name: str, request: Mapping[str, Any], policy: SandboxPolicy, writable_paths: Iterable[Path] = ()) -> dict[str, Any]:
        policy = policy.normalized()
        owner_home = Path.home().resolve()
        base = _safe_temp_base(owner_home)
        sandbox_root = Path(tempfile.mkdtemp(prefix="atm-structural-sandbox-", dir=str(base))).resolve()
        home = sandbox_root / "home"
        runtime = sandbox_root / "runtime"
        proxy_dir = sandbox_root / "proxy"
        home.mkdir(mode=0o700)
        proxy_dir.mkdir(mode=0o700)
        shutil.copytree(self.source_root, runtime / "src", dirs_exist_ok=False)
        worker = runtime / "src" / worker_name
        entry = runtime / "src" / "atm_sandbox_entry.py"
        if not worker.is_file() or not entry.is_file():
            shutil.rmtree(sandbox_root, ignore_errors=True)
            raise SandboxError("SANDBOX_RUNTIME_WORKER_MISSING")
        if _inside(Path(sys.base_prefix).resolve(), owner_home) or _inside(Path(sys.executable).resolve(), owner_home):
            shutil.rmtree(sandbox_root, ignore_errors=True)
            raise SandboxUnavailable("SANDBOX_PYTHON_RUNTIME_INSIDE_OWNER_HOME")
        write_paths = tuple(Path(p).resolve() for p in writable_paths)
        sentinel = owner_home / f".atm-order017-sentinel-{uuid.uuid4().hex}"
        sentinel.write_text(uuid.uuid4().hex, encoding="utf-8")
        try:
            try:
                sentinel.chmod(0o600)
            except OSError:
                pass
            initial_home_entries = list(home.iterdir())
            if initial_home_entries:
                raise SandboxError("SANDBOX_HOME_NOT_INITIAL_EMPTY")
            req_path = home / "request.json"
            resp_path = home / "response.json"
            request_payload = dict(request)
            request_payload["structural_sandbox_expected"] = True
            req_path.write_text(json.dumps(request_payload, sort_keys=True, separators=(",", ":"), default=str), encoding="utf-8")
            env = _clean_env(home, policy, sandbox_root, proxy_dir, owner_home, sentinel, runtime, write_paths)
            command = [sys.executable, "-I", str(entry), "--worker", str(worker), "--request", str(req_path), "--response", str(resp_path)]
            proxy = ReadOnlyHttpsFileProxy(proxy_dir)
            if policy.network_policy == "READ_ONLY_PUBLIC_HTTPS":
                proxy.start()
            try:
                if os.name == "nt":
                    self._run_windows(command, env, sandbox_root, write_paths, policy)
                else:
                    self._run_posix(command, env, sandbox_root, policy)
            finally:
                proxy.close()
            if not resp_path.is_file():
                raise SandboxError("SANDBOX_RESPONSE_MISSING")
            if resp_path.stat().st_size > policy.limits.max_file_bytes:
                raise SandboxLimitExceeded("FILE_OUTPUT", "response")
            envelope = json.loads(resp_path.read_text(encoding="utf-8"))
            if not isinstance(envelope, dict) or not isinstance(envelope.get("sandbox"), dict):
                raise SandboxError("SANDBOX_ENVELOPE_INVALID")
            proof = dict(envelope["sandbox"])
            proof["child_home_initial_entry_count"] = len(initial_home_entries)
            proof["child_home_resolved"] = str(home)
            envelope["sandbox"] = proof
            required = {
                "schema": SANDBOX_SCHEMA,
                "owner_home_read_denied_observed": True,
                "canonical_repo_write_denied_observed": True,
                "direct_network_denied_observed": True,
                "secret_env_count": 0,
                "filesystem_boundary_applied": True,
                "network_boundary_applied": True,
                "resource_limits_applied": True,
                "process_tree_bounded": True,
            }
            for key, expected in required.items():
                if proof.get(key) != expected:
                    raise SandboxError(f"SANDBOX_PROOF_FAILED:{key}")
            if proof.get("network_policy") != policy.network_policy:
                raise SandboxError("SANDBOX_NETWORK_PROOF_MISMATCH")
            if proof.get("resource_limits_requested") != asdict(policy.limits):
                raise SandboxError("SANDBOX_LIMIT_REQUEST_MISMATCH")
            envelope["sandbox_receipt_digest"] = hashlib.sha256(json.dumps(proof, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
            return envelope
        finally:
            try:
                sentinel.unlink(missing_ok=True)
            except OSError:
                pass
            shutil.rmtree(sandbox_root, ignore_errors=True)

    def _run_posix(self, command: list[str], env: Mapping[str, str], sandbox_root: Path, policy: SandboxPolicy) -> None:
        if not sys.platform.startswith("linux"):
            raise SandboxUnavailable("SANDBOX_POSIX_BACKEND_REQUIRES_LINUX")
        unshare = shutil.which("unshare")
        if not unshare:
            raise SandboxUnavailable("SANDBOX_LINUX_UNSHARE_BINARY_MISSING")
        wrapped = [unshare, "--user", "--map-root-user", "--mount", "--net", "--uts", "--ipc", "--fork", "--kill-child", "--", *command]
        proc = subprocess.Popen(wrapped, cwd=str(sandbox_root), env=dict(env), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        deadline = time.monotonic() + policy.limits.wall_seconds
        root_process = psutil.Process(proc.pid)
        while proc.poll() is None:
            limit_class: str | None = None
            if time.monotonic() >= deadline:
                limit_class = "WALL_TIME"
            elif _tree_bytes(sandbox_root, ceiling=policy.limits.max_file_bytes * 2) > policy.limits.max_file_bytes * 2:
                limit_class = "FILE_OUTPUT"
            else:
                try:
                    tree = [root_process, *root_process.children(recursive=True)]
                    rss = sum(p.memory_info().rss for p in tree if p.is_running())
                    fds = sum(p.num_fds() for p in tree if p.is_running())
                    if rss > policy.limits.memory_mb * 1024 * 1024:
                        limit_class = "MEMORY"
                    elif fds > policy.limits.max_open_files * max(1, len(tree)):
                        limit_class = "OPEN_FILES"
                    elif len(tree) > policy.limits.max_processes + 1:
                        limit_class = "PROCESS_COUNT"
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            if limit_class:
                try:
                    os.killpg(proc.pid, 9)
                except OSError:
                    proc.kill()
                raise SandboxLimitExceeded(limit_class)
            time.sleep(0.02)
        stdout, stderr = proc.communicate(timeout=5)
        if proc.returncode != 0:
            tail = (stderr or stdout or "")[-800:].replace("\n", " ")
            raise SandboxError(f"SANDBOX_CHILD_FAILED:{proc.returncode}:{tail}")

    def _run_windows(self, command: list[str], env: Mapping[str, str], sandbox_root: Path, write_paths: tuple[Path, ...], policy: SandboxPolicy) -> None:
        win = _launch_windows_appcontainer(command, env, sandbox_root, write_paths, policy.limits)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        deadline = time.monotonic() + policy.limits.wall_seconds
        try:
            ps = psutil.Process(win.pid)
            while True:
                wait = kernel32.WaitForSingleObject(ctypes.c_void_p(win.process_handle), 20)
                if wait == 0:
                    break
                limit_class = None
                if time.monotonic() >= deadline:
                    limit_class = "WALL_TIME"
                else:
                    try:
                        if ps.memory_info().rss > policy.limits.memory_mb * 1024 * 1024:
                            limit_class = "MEMORY"
                        elif ps.num_handles() > policy.limits.max_open_files:
                            limit_class = "OPEN_HANDLES"
                        elif len(ps.children(recursive=True)) + 1 > policy.limits.max_processes:
                            limit_class = "PROCESS_COUNT"
                        elif _tree_bytes(sandbox_root, ceiling=policy.limits.max_file_bytes * 2) > policy.limits.max_file_bytes * 2:
                            limit_class = "FILE_OUTPUT"
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                if limit_class:
                    kernel32.TerminateJobObject(ctypes.c_void_p(win.job_handle), 72)
                    kernel32.WaitForSingleObject(ctypes.c_void_p(win.process_handle), 5000)
                    raise SandboxLimitExceeded(limit_class)
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(ctypes.c_void_p(win.process_handle), ctypes.byref(code)):
                raise SandboxError("SANDBOX_WINDOWS_EXIT_CODE_FAILED")
            if int(code.value) != 0:
                raise SandboxError(f"SANDBOX_CHILD_FAILED:{int(code.value)}")
        finally:
            _cleanup_windows_process(win)

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import shutil
import site
import socket
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

SANDBOX_SCHEMA = "ATM_STRUCTURAL_SANDBOX_V2"
ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
SECRET_MARKERS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIAL", "MNEMONIC", "SEED", "WALLET")
MAX_PROXY_BODY_BYTES = 4 * 1024 * 1024
PINNED_IMAGE = "python:3.11.15-slim-bookworm@sha256:d29f48a31a8b408ed19272ca1e7b10ebae13b240a27e862d3d4217c528e2e0c3"


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
    allowed_hosts: tuple[str, ...] = ()

    def normalized(self) -> "SandboxPolicy":
        network = str(self.network_policy).upper()
        if network not in {"DENY", "READ_ONLY_PUBLIC_HTTPS"}:
            raise ValueError("SANDBOX_NETWORK_POLICY_INVALID")
        hosts = tuple(sorted({str(h).lower().rstrip(".") for h in self.allowed_hosts if str(h).strip()}))
        return SandboxPolicy(network, str(self.tool_policy), self.limits.normalized(), bool(self.repo_write), hosts)


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
    candidates = [Path(tempfile.gettempdir())]
    if os.name == "nt":
        candidates.extend((Path(r"C:\Windows\Temp"), Path(r"C:\ProgramData")))
    else:
        candidates.extend((Path("/tmp"), Path("/var/tmp")))
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
    """Parent-owned allowlisted HTTPS broker; sandbox children have Docker network=none."""

    def __init__(self, root: Path, *, allowed_hosts: tuple[str, ...], body_limit: int = MAX_PROXY_BODY_BYTES):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.allowed_hosts = {h.lower().rstrip(".") for h in allowed_hosts}
        self.body_limit = int(body_limit)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="atm-sandbox-https-broker", daemon=True)

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
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or parsed.port not in (None, 443) or not host:
            raise PermissionError("SANDBOX_BROKER_HTTPS_ONLY")
        if not self.allowed_hosts or host not in self.allowed_hosts:
            raise PermissionError("SANDBOX_BROKER_HOST_NOT_ALLOWLISTED")
        if parsed.username or parsed.password or method not in {"GET", "HEAD"}:
            raise PermissionError("SANDBOX_BROKER_READ_ONLY")
        if not _public_host(host):
            raise PermissionError("SANDBOX_BROKER_PUBLIC_HOST_REQUIRED")
        headers: dict[str, str] = {}
        for key, value in dict(request.get("headers") or {}).items():
            upper = str(key).upper()
            if any(marker in upper for marker in SECRET_MARKERS) or upper in {"AUTHORIZATION", "COOKIE", "PROXY-AUTHORIZATION"}:
                continue
            headers[str(key)] = str(value)
        headers["User-Agent"] = headers.get("User-Agent", "ATM-Structural-Sandbox/2.0")
        upstream = urllib.request.Request(url, headers=headers, method=method)
        with urllib.request.urlopen(upstream, timeout=min(20, int(request.get("timeout") or 20))) as response:
            body = response.read(self.body_limit + 1)
            if len(body) > self.body_limit:
                raise RuntimeError("SANDBOX_BROKER_BODY_LIMIT")
            safe_headers = {str(k): str(v) for k, v in response.headers.items() if str(k).lower() not in {"set-cookie", "authorization", "proxy-authenticate"}}
            return {"ok": True, "status": int(getattr(response, "status", response.getcode())), "url": str(getattr(response, "url", url)), "headers": safe_headers, "body_b64": base64.b64encode(body).decode("ascii")}


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


def _clean_env(policy: SandboxPolicy, owner_home: Path, sentinel: Path) -> dict[str, str]:
    env = {
        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": "/home/atm", "USERPROFILE": "/home/atm", "TMP": "/tmp", "TEMP": "/tmp",
        "ATM_SANDBOX_SCHEMA": SANDBOX_SCHEMA,
        "ATM_SANDBOX_POLICY_JSON": json.dumps({
            "schema": SANDBOX_SCHEMA, "network_policy": policy.network_policy, "tool_policy": policy.tool_policy,
            "limits": asdict(policy.limits), "canonical_repo_root": "/opt/atm/src",
            "owner_home_host_path": str(owner_home), "owner_sentinel_host_path": str(sentinel),
            "allowed_hosts": list(policy.allowed_hosts),
        }, sort_keys=True, separators=(",", ":")),
        "ATM_SANDBOX_HTTPS_PROXY_DIR": "/sandbox/proxy",
    }
    if any(any(marker in key.upper() for marker in SECRET_MARKERS) for key in env):
        raise SandboxError("SANDBOX_ENV_SECRET_FILTER_FAILED")
    return env


def _site_packages() -> Path:
    for raw in site.getsitepackages():
        path = Path(raw).resolve()
        if path.is_dir():
            return path
    raise SandboxUnavailable("SANDBOX_SITE_PACKAGES_MISSING")


def _docker() -> str:
    if not sys_platform_linux():
        raise SandboxUnavailable("SANDBOX_BACKEND_UNAVAILABLE_NON_LINUX")
    path = shutil.which("docker")
    if not path:
        raise SandboxUnavailable("SANDBOX_DOCKER_BINARY_MISSING")
    cp = subprocess.run([path, "info", "--format", "{{json .ServerVersion}}"], capture_output=True, text=True, timeout=20, check=False)
    if cp.returncode != 0:
        raise SandboxUnavailable("SANDBOX_DOCKER_DAEMON_UNAVAILABLE")
    return path


def sys_platform_linux() -> bool:
    import sys
    return sys.platform.startswith("linux")


def _ensure_image(docker: str) -> str:
    cp = subprocess.run([docker, "image", "inspect", PINNED_IMAGE, "--format", "{{.Id}}"], capture_output=True, text=True, timeout=20, check=False)
    if cp.returncode != 0:
        pull = subprocess.run([docker, "pull", PINNED_IMAGE], capture_output=True, text=True, timeout=120, check=False)
        if pull.returncode != 0:
            raise SandboxUnavailable("SANDBOX_PINNED_IMAGE_UNAVAILABLE")
        cp = subprocess.run([docker, "image", "inspect", PINNED_IMAGE, "--format", "{{.Id}}"], capture_output=True, text=True, timeout=20, check=False)
    image_id = cp.stdout.strip()
    if cp.returncode != 0 or not image_id.startswith("sha256:"):
        raise SandboxUnavailable("SANDBOX_PINNED_IMAGE_NOT_VERIFIED")
    return image_id


def _writeback_staging(staging: list[tuple[Path, Path]]) -> None:
    for original, stage in staging:
        if original.is_dir():
            original.mkdir(parents=True, exist_ok=True)
            for src in stage.iterdir():
                dst = original / src.name
                if src.is_file():
                    tmp = dst.with_suffix(dst.suffix + ".atm-tmp")
                    shutil.copy2(src, tmp)
                    os.replace(tmp, dst)
            continue
        candidate = stage / original.name
        if candidate.exists():
            original.parent.mkdir(parents=True, exist_ok=True)
            tmp = original.with_suffix(original.suffix + ".atm-tmp")
            shutil.copy2(candidate, tmp)
            os.replace(tmp, original)
        for suffix in ("-wal", "-shm"):
            side = stage / (original.name + suffix)
            if side.exists():
                dst = Path(str(original) + suffix)
                shutil.copy2(side, dst)


class StructuralSandbox:
    """Linux economic sandbox: host-managed Docker boundary with parent-derived receipts."""

    def __init__(self, *, source_root: Path = SRC_ROOT):
        self.source_root = Path(source_root).resolve()

    def run(self, *, worker_name: str, request: Mapping[str, Any], policy: SandboxPolicy, writable_paths: Iterable[Path] = ()) -> dict[str, Any]:
        policy = policy.normalized()
        if not sys_platform_linux():
            raise SandboxUnavailable("SANDBOX_BACKEND_UNAVAILABLE_NON_LINUX")
        owner_home = Path.home().resolve()
        base = _safe_temp_base(owner_home)
        sandbox_root = Path(tempfile.mkdtemp(prefix="atm-structural-sandbox-v2-", dir=str(base))).resolve()
        runtime_src = sandbox_root / "runtime" / "src"
        proxy_dir = sandbox_root / "proxy"
        request_path = sandbox_root / "request.json"
        response_path = sandbox_root / "response.json"
        proxy_dir.mkdir(parents=True, mode=0o700)
        shutil.copytree(self.source_root, runtime_src)
        worker = runtime_src / worker_name
        entry = runtime_src / "atm_container_entry.py"
        if not worker.is_file() or not entry.is_file():
            shutil.rmtree(sandbox_root, ignore_errors=True)
            raise SandboxError("SANDBOX_RUNTIME_WORKER_MISSING")
        sentinel = owner_home / f".atm-order018-sentinel-{uuid.uuid4().hex}"
        sentinel.write_text(uuid.uuid4().hex, encoding="utf-8")
        try:
            sentinel.chmod(0o600)
        except OSError:
            pass
        staging: list[tuple[Path, Path]] = []
        mounts: list[tuple[Path, str, bool]] = []
        path_map: dict[str, str] = {}
        cid = ""
        docker = ""
        try:
            for idx, raw in enumerate(writable_paths):
                original = Path(raw).resolve()
                stage_dir = sandbox_root / "write" / str(idx)
                stage_dir.mkdir(parents=True, exist_ok=True)
                file_target = original.is_file() or (not original.exists() and bool(original.suffix))
                if file_target:
                    if original.is_file():
                        shutil.copy2(original, stage_dir / original.name)
                        for suffix in ("-wal", "-shm"):
                            side = Path(str(original) + suffix)
                            if side.exists():
                                shutil.copy2(side, stage_dir / side.name)
                    path_map[str(original)] = f"/sandbox/write/{idx}/{original.name}"
                else:
                    path_map[str(original)] = f"/sandbox/write/{idx}"
                staging.append((original, stage_dir))
                mounts.append((stage_dir, f"/sandbox/write/{idx}", False))
            payload = _replace_paths(dict(request), path_map)
            payload["structural_sandbox_expected"] = True
            payload["isolated_home"] = "/home/atm"
            request_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str), encoding="utf-8")
            response_path.write_text("", encoding="utf-8")
            request_path.chmod(0o644); response_path.chmod(0o666); proxy_dir.chmod(0o777)
            docker = _docker()
            image_id = _ensure_image(docker)
            site_packages = _site_packages()
            if _inside(site_packages, owner_home):
                raise SandboxUnavailable("SANDBOX_SITE_PACKAGES_INSIDE_OWNER_HOME")
            env = _clean_env(policy, owner_home, sentinel)
            uid, gid = os.getuid(), os.getgid()
            name = "atm-sandbox-" + uuid.uuid4().hex[:20]
            args = [
                docker, "create", "--name", name, "--network", "none", "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                "--pids-limit", str(policy.limits.max_processes), "--memory", f"{policy.limits.memory_mb}m",
                "--memory-swap", f"{policy.limits.memory_mb}m", "--cpus", "1.0",
                "--ulimit", f"cpu={policy.limits.cpu_seconds}:{policy.limits.cpu_seconds}",
                "--ulimit", f"fsize={policy.limits.max_file_bytes}:{policy.limits.max_file_bytes}",
                "--ulimit", f"nofile={policy.limits.max_open_files}:{policy.limits.max_open_files}",
                "--ulimit", f"nproc={policy.limits.max_processes}:{policy.limits.max_processes}",
                "--user", f"{uid}:{gid}",
                "--tmpfs", "/home/atm:rw,noexec,nosuid,nodev,size=8m,mode=700",
                "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777",
                "--mount", f"type=bind,src={runtime_src},dst=/opt/atm/src,readonly",
                "--mount", f"type=bind,src={site_packages},dst=/opt/atm/site-packages,readonly",
                "--mount", f"type=bind,src={request_path},dst=/sandbox/request.json,readonly",
                "--mount", f"type=bind,src={response_path},dst=/sandbox/response.json",
                "--mount", f"type=bind,src={proxy_dir},dst=/sandbox/proxy",
            ]
            for source, target, readonly in mounts:
                args.extend(("--mount", f"type=bind,src={source},dst={target}" + (",readonly" if readonly else "")))
            for key, value in env.items(): args.extend(("--env", f"{key}={value}"))
            args.extend(("--env", "PYTHONPATH=/opt/atm/src:/opt/atm/site-packages", PINNED_IMAGE, "python", "/opt/atm/src/atm_container_entry.py", "--worker", f"/opt/atm/src/{worker_name}", "--request", "/sandbox/request.json", "--response", "/sandbox/response.json"))
            created = subprocess.run(args, capture_output=True, text=True, timeout=60, check=False)
            if created.returncode != 0: raise SandboxUnavailable("SANDBOX_DOCKER_CREATE_FAILED:" + (created.stderr or "")[-300:])
            cid = created.stdout.strip()
            proxy = ReadOnlyHttpsFileProxy(proxy_dir, allowed_hosts=policy.allowed_hosts)
            if policy.network_policy == "READ_ONLY_PUBLIC_HTTPS": proxy.start()
            try:
                start = subprocess.Popen([docker, "start", "-a", cid], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try: stdout, stderr = start.communicate(timeout=policy.limits.wall_seconds)
                except subprocess.TimeoutExpired:
                    subprocess.run([docker, "kill", cid], capture_output=True, timeout=10, check=False); start.communicate(timeout=10)
                    raise SandboxLimitExceeded("WALL_TIME")
            finally: proxy.close()
            inspect_cp = subprocess.run([docker, "inspect", cid], capture_output=True, text=True, timeout=20, check=False)
            if inspect_cp.returncode != 0: raise SandboxError("SANDBOX_DOCKER_INSPECT_FAILED")
            inspected = json.loads(inspect_cp.stdout)[0]; state = inspected.get("State") or {}; host = inspected.get("HostConfig") or {}; mounts_observed = inspected.get("Mounts") or []
            if state.get("OOMKilled"): raise SandboxLimitExceeded("MEMORY")
            if int(state.get("ExitCode") or 0) != 0: raise SandboxError(f"SANDBOX_CHILD_FAILED:{state.get('ExitCode')}:{(stderr or stdout or '')[-500:].replace(chr(10),' ')}")
            if not response_path.read_text(encoding="utf-8").strip(): raise SandboxError("SANDBOX_RESPONSE_MISSING")
            if response_path.stat().st_size > policy.limits.max_file_bytes: raise SandboxLimitExceeded("FILE_OUTPUT")
            envelope = json.loads(response_path.read_text(encoding="utf-8")); proof = dict(envelope.get("sandbox") or {})
            if proof.get("schema") != SANDBOX_SCHEMA: raise SandboxError("SANDBOX_SCHEMA_MISMATCH")
            ulimits = {str(row.get("Name")): (int(row.get("Soft", -1)), int(row.get("Hard", -1))) for row in host.get("Ulimits") or []}
            sources = [str(row.get("Source") or "") for row in mounts_observed]
            owner_home_mounted = any(src and _inside(Path(src), owner_home) for src in sources)
            checks = {
                "network_mode": host.get("NetworkMode") == "none", "readonly_rootfs": bool(host.get("ReadonlyRootfs")),
                "cap_drop": "ALL" in {str(x).upper() for x in host.get("CapDrop") or []},
                "no_new_privileges": any("no-new-privileges" in str(x) for x in host.get("SecurityOpt") or []),
                "pids_limit": int(host.get("PidsLimit") or 0) == policy.limits.max_processes,
                "memory": int(host.get("Memory") or 0) == policy.limits.memory_mb * 1024 * 1024,
                "memory_swap": int(host.get("MemorySwap") or 0) == policy.limits.memory_mb * 1024 * 1024,
                "cpu_ulimit": ulimits.get("cpu") == (policy.limits.cpu_seconds, policy.limits.cpu_seconds),
                "fsize_ulimit": ulimits.get("fsize") == (policy.limits.max_file_bytes, policy.limits.max_file_bytes),
                "nofile_ulimit": ulimits.get("nofile") == (policy.limits.max_open_files, policy.limits.max_open_files),
                "nproc_ulimit": ulimits.get("nproc") == (policy.limits.max_processes, policy.limits.max_processes),
                "owner_home_not_mounted": not owner_home_mounted,
            }
            if not all(checks.values()): raise SandboxError("SANDBOX_DOCKER_POLICY_NOT_OBSERVED:" + json.dumps(checks, sort_keys=True))
            proof["docker_observed"] = {"backend":"DOCKER_HOST_MANAGED","image_ref":PINNED_IMAGE,"image_id":image_id,"container_id":cid,"network_mode":host.get("NetworkMode"),"readonly_rootfs":bool(host.get("ReadonlyRootfs")),"cap_drop":sorted(host.get("CapDrop") or []),"security_opt":sorted(host.get("SecurityOpt") or []),"pids_limit":int(host.get("PidsLimit") or 0),"memory_bytes":int(host.get("Memory") or 0),"memory_swap_bytes":int(host.get("MemorySwap") or 0),"nano_cpus":int(host.get("NanoCpus") or 0),"ulimits":{k:list(v) for k,v in ulimits.items()},"owner_home_mounted":owner_home_mounted,"mount_sources_sha256":hashlib.sha256(json.dumps(sorted(sources)).encode()).hexdigest()}
            proof["owner_home_mounted"] = owner_home_mounted
            proof["filesystem_boundary_applied"] = bool(proof.get("filesystem_boundary_applied")) and checks["readonly_rootfs"] and checks["owner_home_not_mounted"]
            proof["network_boundary_applied"] = bool(proof.get("network_boundary_applied")) and checks["network_mode"]
            proof["resource_limits_applied"] = bool(proof.get("resource_limits_applied")) and all(checks[k] for k in ("pids_limit","memory","memory_swap","cpu_ulimit","fsize_ulimit","nofile_ulimit","nproc_ulimit"))
            proof["process_tree_bounded"] = bool(proof.get("process_tree_bounded")) and checks["pids_limit"]
            proof["container_policy_checks"] = checks
            envelope["sandbox"] = proof
            required = {"owner_home_read_denied_observed":True,"canonical_repo_write_denied_observed":True,"direct_network_denied_observed":True,"secret_env_count":0,"filesystem_boundary_applied":True,"network_boundary_applied":True,"resource_limits_applied":True,"process_tree_bounded":True,"owner_home_mounted":False}
            for key, value in required.items():
                if proof.get(key) != value: raise SandboxError(f"SANDBOX_PROOF_FAILED:{key}")
            if proof.get("network_policy") != policy.network_policy: raise SandboxError("SANDBOX_NETWORK_PROOF_MISMATCH")
            envelope["sandbox_receipt_digest"] = hashlib.sha256(json.dumps(proof, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
            _writeback_staging(staging)
            return envelope
        finally:
            if docker and cid:
                try: subprocess.run([docker, "rm", "-f", cid], capture_output=True, timeout=10, check=False)
                except Exception: pass
            try: sentinel.unlink(missing_ok=True)
            except OSError: pass
            shutil.rmtree(sandbox_root, ignore_errors=True)

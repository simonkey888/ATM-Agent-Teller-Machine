from __future__ import annotations

import base64
import grp
import hashlib
import json
import os
import socket
import stat
import struct
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

OWNER = "simonkey888"
SOCKET_PATH = Path("/run/atm-publisher/publisher.sock")
WORKSPACES = Path("/var/lib/atm/state/workspaces")
MAX_FILES = 300
MAX_FILE_BYTES = 750_000
MAX_TOTAL_BYTES = 6_000_000
EXCLUDED_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__"}
EXCLUDED_FILES = {".env", ".env.local", "id_rsa", "id_ed25519", "secrets.env"}
SECRET_FILES = [Path("/etc/atm/runtime.env"), Path("/var/lib/atm/state/secrets.env")]


class PublishError(RuntimeError):
    pass


def api(token: str, method: str, path: str, payload: Any | None = None, allow_404: bool = False) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10", "User-Agent": "atm-oci-publisher/1"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request("https://api.github.com" + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = resp.read(); return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        if allow_404 and exc.code == 404:
            return None
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise PublishError(f"GitHub HTTP {exc.code}: {detail}") from exc


def known_secrets(token: str) -> list[bytes]:
    values = [token.encode("utf-8")]
    for path in SECRET_FILES:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            _, value = line.split("=", 1)
            value = value.strip().strip("'\"")
            if len(value) >= 12:
                values.append(value.encode("utf-8"))
    return values


def validate_workspace(raw: str) -> Path:
    path = Path(raw).resolve(strict=True); root = WORKSPACES.resolve(strict=True)
    if not path.is_relative_to(root): raise PublishError("workspace outside ATM workspaces")
    if path.name != "worker" or len(path.parent.name) != 20 or any(c not in "0123456789abcdef" for c in path.parent.name): raise PublishError("workspace shape invalid")
    return path


def collect_files(workspace: Path, secrets: list[bytes]) -> list[tuple[str, bytes, str]]:
    result: list[tuple[str, bytes, str]] = []; total = 0
    for current, dirs, files in os.walk(workspace, followlinks=False):
        current_path = Path(current); dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS and not (current_path / d).is_symlink()]
        for name in sorted(files):
            path = current_path / name
            if name in EXCLUDED_FILES or name.endswith((".pem", ".key", ".p12", ".pfx")): continue
            if path.is_symlink(): raise PublishError(f"symlink file rejected: {path.name}")
            rel = path.relative_to(workspace).as_posix(); st = path.stat()
            if not stat.S_ISREG(st.st_mode): continue
            if st.st_size > MAX_FILE_BYTES: raise PublishError(f"file too large: {rel}")
            data = path.read_bytes(); total += len(data)
            if total > MAX_TOTAL_BYTES: raise PublishError("deliverable exceeds total byte cap")
            if any(secret and secret in data for secret in secrets): raise PublishError(f"known secret detected in deliverable: {rel}")
            mode = "100755" if st.st_mode & stat.S_IXUSR else "100644"
            result.append((rel, data, mode))
            if len(result) > MAX_FILES: raise PublishError("deliverable exceeds file-count cap")
    if not result: raise PublishError("deliverable workspace is empty")
    return result


def repo_name(opportunity_id: str) -> str:
    return "atm-deliverable-" + hashlib.sha256(opportunity_id.encode("utf-8")).hexdigest()[:16]


def ensure_repo(token: str, opportunity_id: str) -> str:
    name = repo_name(opportunity_id); marker = f"ATM deliverable for {opportunity_id}"
    repo = api(token, "GET", f"/repos/{OWNER}/{name}", allow_404=True)
    if repo is None:
        repo = api(token, "POST", "/user/repos", {"name": name, "description": marker, "private": False, "auto_init": True, "has_issues": False, "has_projects": False, "has_wiki": False})
    if repo.get("full_name") != f"{OWNER}/{name}" or str(repo.get("description") or "") != marker: raise PublishError("deterministic repository name collision")
    return name


def publish(token: str, opportunity_id: str, workspace_raw: str) -> str:
    if not opportunity_id or len(opportunity_id) > 300: raise PublishError("opportunity id invalid")
    workspace = validate_workspace(workspace_raw); files = collect_files(workspace, known_secrets(token)); name = ensure_repo(token, opportunity_id)
    ref = None
    for _ in range(6):
        ref = api(token, "GET", f"/repos/{OWNER}/{name}/git/ref/heads/main", allow_404=True)
        if ref: break
        time.sleep(1)
    if not ref: raise PublishError("repository main branch not initialized")
    parent = str(ref["object"]["sha"]); entries = []
    for rel, data, mode in files:
        blob = api(token, "POST", f"/repos/{OWNER}/{name}/git/blobs", {"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"})
        entries.append({"path": rel, "mode": mode, "type": "blob", "sha": blob["sha"]})
    tree = api(token, "POST", f"/repos/{OWNER}/{name}/git/trees", {"tree": entries})
    commit = api(token, "POST", f"/repos/{OWNER}/{name}/git/commits", {"message": f"ATM deliverable for {opportunity_id}", "tree": tree["sha"], "parents": [parent]})
    api(token, "PATCH", f"/repos/{OWNER}/{name}/git/refs/heads/main", {"sha": commit["sha"], "force": False})
    return f"https://github.com/{OWNER}/{name}"


def serve_one(conn: socket.socket, token: str) -> None:
    raw = b""
    while len(raw) < 32768:
        chunk = conn.recv(8192)
        if not chunk: break
        raw += chunk
        if b"\n" in raw: break
    try:
        peer_pid, _, _ = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
        service = subprocess.run(
            ["systemctl", "show", "--property=MainPID", "--value", "atm-supervisor.service"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        supervisor_pid = int((service.stdout or "0").strip() or "0")
        if service.returncode != 0 or supervisor_pid <= 1 or peer_pid != supervisor_pid:
            raise PublishError("publisher caller is not the live supervisor MainPID")
        request = json.loads(raw.split(b"\n", 1)[0].decode("utf-8"))
        if set(request) != {"opportunity_id", "workspace"}: raise PublishError("invalid request fields")
        response = {"ok": True, "url": publish(token, str(request["opportunity_id"]), str(request["workspace"]))}
    except Exception as exc:
        response = {"ok": False, "error": str(exc)[:800]}
    conn.sendall((json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8"))


def main() -> int:
    token = os.getenv("GITHUB_TOKEN") or ""
    if not token: raise SystemExit("GITHUB_TOKEN missing")
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True); SOCKET_PATH.unlink(missing_ok=True)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); sock.bind(str(SOCKET_PATH))
    os.chown(SOCKET_PATH, 0, grp.getgrnam("atm").gr_gid); os.chmod(SOCKET_PATH, 0o660); sock.listen(8)
    try:
        while True:
            conn, _ = sock.accept()
            with conn: serve_one(conn, token)
    finally:
        sock.close(); SOCKET_PATH.unlink(missing_ok=True)
    return 0


if __name__ == "__main__": raise SystemExit(main())

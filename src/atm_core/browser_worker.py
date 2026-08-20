from __future__ import annotations

import ipaddress
import shutil
import socket
import subprocess
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


OBSCURA_VERSION = "0.2.0"
OBSCURA_ARCHIVE_SHA256 = "d601f4f542319c3b9fa8dca9f5ccfc134a2ca001648da528db5f03c9e6c2599b"


class BrowserPolicyError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        raise BrowserPolicyError("browser HTTP redirects are rejected")


@dataclass(frozen=True)
class BrowserJob:
    url: str
    allowed_domains: tuple[str, ...]
    javascript_required: bool
    mode: str = "read_only"
    credential_scope: str = "NONE"
    max_pages: int = 1
    max_runtime_seconds: int = 60
    max_download_bytes: int = 5_000_000
    capture_policy: str = "DOM_TEXT_ONLY"

    def validate(self) -> None:
        parsed = urllib.parse.urlparse(self.url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise BrowserPolicyError("public browser jobs require a plain HTTPS URL")
        if parsed.hostname.lower() not in {item.lower() for item in self.allowed_domains}:
            raise BrowserPolicyError("URL host is outside allowed_domains")
        if self.mode != "read_only" or self.credential_scope != "NONE":
            raise BrowserPolicyError("R2 browser worker is public read-only and credentialless")
        if not 1 <= self.max_pages <= 10 or not 1 <= self.max_runtime_seconds <= 180:
            raise BrowserPolicyError("browser resource budget is invalid")
        for address in {row[4][0] for row in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)}:
            if not ipaddress.ip_address(address).is_global:
                raise BrowserPolicyError("private/link-local browser destination rejected")


@dataclass(frozen=True)
class BrowserResult:
    backend: str
    content: str
    source_url: str
    read_only: bool


class BrowserWorker:
    """Ordered HTTP -> Playwright -> pinned Obscura public-read abstraction."""

    def __init__(self, *, obscura_binary: Path | None = None, playwright_binary: str | None = None):
        self.obscura_binary = Path(obscura_binary).resolve() if obscura_binary else None
        self.playwright_binary = playwright_binary or shutil.which("playwright")

    def execute(self, job: BrowserJob) -> BrowserResult:
        job.validate()
        if not job.javascript_required:
            request = urllib.request.Request(job.url, headers={"User-Agent": "ATM-BrowserWorker/2.0"})
            opener = urllib.request.build_opener(_NoRedirect())
            with opener.open(request, timeout=job.max_runtime_seconds) as response:
                raw = response.read(job.max_download_bytes + 1)
            if len(raw) > job.max_download_bytes:
                raise BrowserPolicyError("browser response exceeded max_download_bytes")
            return BrowserResult("HTTP", raw.decode("utf-8", errors="replace"), job.url, True)
        if self.playwright_binary:
            raise BrowserPolicyError("Playwright CLI is present but no pinned R2 harness is qualified")
        if not self.obscura_binary or not self.obscura_binary.is_file():
            raise BrowserPolicyError("no benchmark-qualified dynamic browser backend available")
        with tempfile.TemporaryDirectory(prefix="atm-obscura-") as home:
            completed = subprocess.run(
                [
                    str(self.obscura_binary),
                    "fetch",
                    "--eval", "document.documentElement.outerHTML",
                    "--timeout", str(job.max_runtime_seconds),
                    "--wait", "1",
                    job.url,
                ],
                text=True,
                capture_output=True,
                timeout=job.max_runtime_seconds + 10,
                env={"PATH": "/usr/bin:/bin", "HOME": home},
            )
        if completed.returncode != 0:
            raise BrowserPolicyError("Obscura public-read execution failed")
        if len(completed.stdout.encode()) > job.max_download_bytes:
            raise BrowserPolicyError("browser result exceeded max_download_bytes")
        return BrowserResult(f"OBSCURA_{OBSCURA_VERSION}", completed.stdout, job.url, True)

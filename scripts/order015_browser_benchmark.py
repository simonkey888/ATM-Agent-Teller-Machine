#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
import re
from pathlib import Path

import psutil


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "browser_dynamic.html"
RECEIPT = ROOT / "order015-browser-benchmark.json"


def run_measured(command: list[str], timeout: int) -> tuple[subprocess.CompletedProcess[str], float, int]:
    started = time.monotonic()
    time_binary = shutil.which("time")
    invoked = [time_binary, "-f", "__ATM_MAX_RSS_KB=%M", *command] if time_binary else command
    process = subprocess.Popen(invoked, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    peak = 0
    try:
        wrapped = psutil.Process(process.pid)
    except psutil.NoSuchProcess:
        wrapped = None
    while process.poll() is None:
        if wrapped is not None:
            try:
                peak = max(peak, wrapped.memory_info().rss, *(child.memory_info().rss for child in wrapped.children(recursive=True)))
            except (psutil.Error, ValueError):
                pass
        if time.monotonic() - started > timeout:
            process.kill()
            raise RuntimeError("browser benchmark timeout")
        time.sleep(0.01)
    stdout, stderr = process.communicate()
    measured = re.search(r"__ATM_MAX_RSS_KB=(\d+)", stderr)
    if measured:
        peak = max(peak, int(measured.group(1)) * 1024)
        stderr = re.sub(r"\n?__ATM_MAX_RSS_KB=\d+\n?", "\n", stderr)
    completed = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    return completed, time.monotonic() - started, peak


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--obscura", type=Path, required=True)
    args = parser.parse_args()
    binary = args.obscura.resolve()
    if not binary.is_file():
        raise SystemExit("OBSCURA_BINARY_MISSING")

    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(*a, directory=str(FIXTURE.parent), **kw)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/{FIXTURE.name}"
    try:
        started = time.monotonic()
        raw = urllib.request.urlopen(url, timeout=5).read().decode()
        http_ms = round((time.monotonic() - started) * 1000, 3)
        with tempfile.TemporaryDirectory() as td:
            screenshot = Path(td) / "fixture.png"
            command = [
                str(binary), "fetch", "--allow-private-network", "--wait", "1", "--timeout", "20",
                "--eval", "document.getElementById('result').textContent", "--screenshot", str(screenshot), url,
            ]
            completed, elapsed, peak = run_measured(command, 30)
            rendered = completed.stdout
            screenshot_ok = screenshot.is_file() and screenshot.stat().st_size > 100
    finally:
        server.shutdown()
        server.server_close()

    version, startup, startup_peak = run_measured([str(binary), "--version"], 10)
    rendered_dom_ready = "ATM_JS_READY" in rendered
    raw_dom_ready = ">ATM_JS_READY<" in raw.replace(" ", "")
    obscura_ok = completed.returncode == 0 and rendered_dom_ready and not raw_dom_ready and screenshot_ok
    receipt = {
        "schema": "ATM_BROWSER_BENCHMARK_V1",
        "fixture_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        "http": {"elapsed_ms": http_ms, "javascript_rendered": raw_dom_ready},
        "playwright": {"available": shutil.which("playwright") is not None, "qualified_pinned_harness": False},
        "obscura": {
            "version": version.stdout.strip(),
            "elapsed_ms": round(elapsed * 1000, 3),
            "startup_ms": round(startup * 1000, 3),
            "peak_rss_bytes": peak,
            "startup_peak_rss_bytes": startup_peak,
            "javascript_rendered": rendered_dom_ready,
            "screenshot_ok": screenshot_ok,
            "returncode": completed.returncode,
            "stdout_tail": rendered[-500:],
            "stderr_tail": completed.stderr[-500:],
        },
        "winner": "OBSCURA" if obscura_ok else "NONE",
        "adoption_basis": "ADDS_PINNED_DYNAMIC_JS_AND_CAPTURE_CAPABILITY" if obscura_ok else "BENCHMARK_REJECTED",
        "stealth_used": False,
        "private_network_used_only_for_local_fixture": True,
        "outgoing_spend_usd": "0",
    }
    RECEIPT.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, sort_keys=True))
    return 0 if obscura_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

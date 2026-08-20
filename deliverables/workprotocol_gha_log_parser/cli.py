"""GitHub Actions failure log parser for WorkProtocol shadow deliverable."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

API_ROOT = "https://api.github.com"
RUN_URL_RE = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)/actions/runs/(?P<run_id>[0-9]+)/*$"
)
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T[0-9:.+-]+Z?\s+")


class GitHubApiError(RuntimeError):
    """Raised when GitHub returns a non-success response."""


@dataclass(frozen=True)
class RunTarget:
    """Parsed GitHub Actions run identity."""

    owner: str
    repo: str
    run_id: int

    @property
    def canonical_url(self) -> str:
        """Return the canonical browser URL for the run."""
        return f"https://github.com/{self.owner}/{self.repo}/actions/runs/{self.run_id}"


def parse_run_url(value: str) -> RunTarget:
    """Validate and parse a canonical GitHub Actions run URL."""
    match = RUN_URL_RE.fullmatch(value.strip())
    if not match:
        raise ValueError("expected https://github.com/<owner>/<repo>/actions/runs/<run_id>")
    return RunTarget(match["owner"], match["repo"], int(match["run_id"]))


def _headers(token: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "workprotocol-gha-log-parser/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def request_json(url: str, token: str | None = None) -> dict[str, Any]:
    """Fetch one GitHub REST JSON object."""
    request = urllib.request.Request(url, headers=_headers(token))
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise GitHubApiError(f"GitHub API HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise GitHubApiError(f"GitHub API network error: {exc.reason}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GitHubApiError("GitHub API returned malformed JSON") from exc
    if not isinstance(payload, dict):
        raise GitHubApiError("GitHub API returned non-object JSON")
    return payload


def request_text(url: str, token: str | None = None) -> str:
    """Fetch text from a GitHub REST endpoint such as job logs."""
    request = urllib.request.Request(url, headers=_headers(token))
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise GitHubApiError(f"GitHub API HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise GitHubApiError(f"GitHub API network error: {exc.reason}") from exc


def classify_failure(log_text: str) -> str:
    """Classify a failing log into one bounded fix category."""
    lowered = log_text.lower()
    if "pytest" in lowered or "assertionerror" in lowered or "=== failures ===" in lowered:
        return "test_pytest"
    if "jest" in lowered or re.search(r"(?m)^FAIL\s+\S+", log_text):
        return "test_jest"
    if re.search(r"\berror\s+TS\d{4}\b", log_text, re.IGNORECASE) or "typescript" in lowered:
        return "build_typescript"
    if "compileerror" in lowered or "compilation failed" in lowered or "build failed" in lowered:
        return "build_compilation"
    if "pylint" in lowered or "eslint" in lowered or "ruff" in lowered or "lint failed" in lowered:
        return "lint"
    return "unknown"


def _clean_line(line: str) -> str:
    line = TIMESTAMP_RE.sub("", line.strip())
    if line.startswith("##[error]"):
        line = line[len("##[error]"):].strip()
    return line


def extract_error_message(log_text: str) -> str:
    """Extract a concise primary error message from a failing log."""
    patterns = (
        re.compile(r"error\s+TS\d{4}:.*", re.IGNORECASE),
        re.compile(r"AssertionError(?::.*)?", re.IGNORECASE),
        re.compile(r"(?:TypeError|ValueError|RuntimeError|SyntaxError|ReferenceError|Error):\s+.*"),
        re.compile(r"FAILED\s+\S+.*"),
        re.compile(r"(?:pylint|eslint|ruff).*", re.IGNORECASE),
        re.compile(r"error:\s+.*", re.IGNORECASE),
    )
    lines = [_clean_line(line) for line in log_text.splitlines() if _clean_line(line)]
    for line in lines:
        for pattern in patterns:
            match = pattern.search(line)
            if match:
                return match.group(0)[:1000]
    return lines[-1][:1000] if lines else "failure detected; no error message parsed"


def extract_stack_trace(log_text: str) -> list[str]:
    """Extract a bounded multiline stack trace without returning the entire log."""
    lines = [_clean_line(line) for line in log_text.splitlines()]
    trace: list[str] = []
    collecting = False
    for line in lines:
        if "Traceback (most recent call last)" in line:
            collecting = True
        if collecting and line:
            trace.append(line)
            if len(trace) >= 20:
                break
            continue
        if re.match(r"^(?:at\s+|File\s+\")", line) or re.search(r"\s+at\s+\S+", line):
            trace.append(line)
            if len(trace) >= 20:
                break
    if not trace:
        candidates = [
            line for line in lines
            if line and (
                re.search(r"\.(?:py|ts|tsx|js|jsx):\d+", line)
                or re.match(r"^\s*at\s+", line)
            )
        ]
        trace = candidates[:20]
    return trace


def parse_failure(log_text: str, *, step_name: str, job_name: str) -> dict[str, Any]:
    """Normalize one failed step and its job log into structured failure JSON."""
    category = classify_failure(log_text)
    return {
        "job_name": job_name,
        "failing_step_name": step_name,
        "error_message": extract_error_message(log_text),
        "stack_trace": extract_stack_trace(log_text),
        "suggested_fix_category": category,
    }


def analyze_run(
    run_url: str,
    *,
    token: str | None = None,
    json_getter: Any = request_json,
    text_getter: Any = request_text,
) -> dict[str, Any]:
    """Fetch a GitHub Actions run and return deterministic structured failure data."""
    target = parse_run_url(run_url)
    jobs_url = f"{API_ROOT}/repos/{target.owner}/{target.repo}/actions/runs/{target.run_id}/jobs?per_page=100"
    payload = json_getter(jobs_url, token)
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise GitHubApiError("jobs response missing jobs list")

    failures: list[dict[str, Any]] = []
    for job in sorted((row for row in jobs if isinstance(row, dict)), key=lambda row: int(row.get("id") or 0)):
        if str(job.get("conclusion") or "").lower() != "failure":
            continue
        job_id = int(job.get("id") or 0)
        if not job_id:
            continue
        log_text = text_getter(f"{API_ROOT}/repos/{target.owner}/{target.repo}/actions/jobs/{job_id}/logs", token)
        steps = job.get("steps") if isinstance(job.get("steps"), list) else []
        failed_steps = [step for step in steps if isinstance(step, dict) and str(step.get("conclusion") or "").lower() == "failure"]
        if not failed_steps:
            failed_steps = [{"name": "unknown failing step", "number": 0}]
        for step in sorted(failed_steps, key=lambda row: int(row.get("number") or 0)):
            failures.append(
                parse_failure(
                    log_text,
                    step_name=str(step.get("name") or "unknown failing step"),
                    job_name=str(job.get("name") or f"job-{job_id}"),
                )
            )

    first = failures[0] if failures else {
        "failing_step_name": None,
        "error_message": None,
        "stack_trace": [],
        "suggested_fix_category": None,
    }
    return {
        "schema": "workprotocol.github_actions_failure.v1",
        "run_url": target.canonical_url,
        "status": "failure_found" if failures else "no_failing_job",
        "failing_step_name": first["failing_step_name"],
        "error_message": first["error_message"],
        "stack_trace": first["stack_trace"],
        "suggested_fix_category": first["suggested_fix_category"],
        "failures": failures,
    }


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(description="Extract structured failure data from a GitHub Actions run.")
    parser.add_argument("run_url", help="https://github.com/<owner>/<repo>/actions/runs/<run_id>")
    parser.add_argument("--token-env", default="GITHUB_TOKEN", help="Environment variable containing an optional GitHub token")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = build_parser().parse_args(argv)
    token = os.getenv(args.token_env, "").strip() or None
    try:
        result = analyze_run(args.run_url, token=token)
    except (ValueError, GitHubApiError) as exc:
        result = {
            "schema": "workprotocol.github_actions_failure.v1",
            "status": "error",
            "error": str(exc),
        }
        print(json.dumps(result, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

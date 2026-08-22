"""Independent falsifier for the ORDER-009-R1 WorkProtocol shadow deliverable."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deliverables.workprotocol_gha_log_parser import cli

OUT = ROOT / "order009-r1-check-evidence.json"

PYTEST = """=== FAILURES ===
Traceback (most recent call last):
  File "tests/test_api.py", line 9, in test_x
    assert x == 2
AssertionError: x was 1
FAILED tests/test_api.py::test_x
"""
JEST = """FAIL src/a.test.js
Error: expected true received false
    at Object.<anonymous> (src/a.test.js:7:3)
Jest test failed
"""
BUILD = """src/a.ts(4,2): error TS2322: Type 'string' is not assignable to type 'number'.
Build failed
"""
LINT = """pylint app.py
app.py:1:0: C0114: Missing module docstring
lint failed
"""


def _failed_payload() -> dict:
    return {
        "jobs": [
            {
                "id": 9,
                "name": "ci",
                "conclusion": "failure",
                "steps": [
                    {"number": 2, "name": "tests", "conclusion": "failure"},
                    {"number": 3, "name": "lint", "conclusion": "failure"},
                ],
            }
        ]
    }


def main() -> int:
    results: dict[str, object] = {}
    try:
        cli.parse_run_url("https://not-github.invalid/a/b/actions/runs/1")
    except ValueError:
        results["malformed_url"] = "PASS"
    else:
        raise AssertionError("malformed URL accepted")

    categories = {
        "pytest": cli.classify_failure(PYTEST),
        "jest": cli.classify_failure(JEST),
        "build": cli.classify_failure(BUILD),
        "lint": cli.classify_failure(LINT),
    }
    assert categories == {
        "pytest": "test_pytest",
        "jest": "test_jest",
        "build": "build_typescript",
        "lint": "lint",
    }
    results["failure_fixtures"] = categories

    no_fail = cli.analyze_run(
        "https://github.com/acme/repo/actions/runs/1",
        json_getter=lambda *_: {"jobs": [{"id": 1, "conclusion": "success", "steps": []}]},
        text_getter=lambda *_: "",
    )
    assert no_fail["status"] == "no_failing_job"
    results["no_failing_job"] = "PASS"

    multi = cli.analyze_run(
        "https://github.com/acme/repo/actions/runs/2",
        json_getter=lambda *_: _failed_payload(),
        text_getter=lambda *_: PYTEST + "\n" + LINT,
    )
    assert len(multi["failures"]) == 2
    results["multiple_failed_steps"] = "PASS"

    first_json = json.dumps(multi, sort_keys=True, separators=(",", ":"))
    second_json = json.dumps(
        cli.analyze_run(
            "https://github.com/acme/repo/actions/runs/2",
            json_getter=lambda *_: _failed_payload(),
            text_getter=lambda *_: PYTEST + "\n" + LINT,
        ),
        sort_keys=True,
        separators=(",", ":"),
    )
    assert first_json == second_json
    results["deterministic_json"] = "PASS"

    trace = cli.extract_stack_trace(PYTEST)
    assert len(trace) >= 3 and any("test_api.py" in line for line in trace)
    results["multiline_stack_trace"] = "PASS"

    try:
        raise cli.GitHubApiError("GitHub API HTTP 403")
    except cli.GitHubApiError as exc:
        assert "403" in str(exc)
    results["github_api_error"] = "PASS"

    results["representative_json"] = multi
    results["representative_json_sha256"] = hashlib.sha256(first_json.encode()).hexdigest()
    OUT.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(results, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

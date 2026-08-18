from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from deliverables.workprotocol_gha_log_parser import cli


PYTEST_LOG = """2026-08-18T00:00:00Z === FAILURES ===
Traceback (most recent call last):
  File "tests/test_api.py", line 42, in test_value
    assert actual == expected
AssertionError: expected 2 got 1
FAILED tests/test_api.py::test_value
"""
JEST_LOG = """FAIL src/math.test.js
Error: Expected: 2 Received: 1
    at Object.<anonymous> (src/math.test.js:14:9)
Jest did not exit one second after the test run
"""
TS_LOG = """src/index.ts(12,3): error TS2322: Type 'string' is not assignable to type 'number'.
Build failed
"""
LINT_LOG = """************* Module app
app.py:4:0: C0116: Missing function or method docstring (missing-function-docstring)
pylint score 7.5/10
"""


class ParserTests(unittest.TestCase):
    def test_valid_run_url(self):
        target = cli.parse_run_url("https://github.com/acme/widget/actions/runs/123")
        self.assertEqual((target.owner, target.repo, target.run_id), ("acme", "widget", 123))

    def test_rejects_non_actions_url(self):
        with self.assertRaises(ValueError):
            cli.parse_run_url("https://example.com/acme/widget/actions/runs/123")

    def test_pytest_failure(self):
        parsed = cli.parse_failure(PYTEST_LOG, step_name="pytest", job_name="tests")
        self.assertEqual(parsed["suggested_fix_category"], "test_pytest")
        self.assertIn("AssertionError", parsed["error_message"])
        self.assertGreaterEqual(len(parsed["stack_trace"]), 3)

    def test_jest_failure(self):
        parsed = cli.parse_failure(JEST_LOG, step_name="jest", job_name="tests")
        self.assertEqual(parsed["suggested_fix_category"], "test_jest")
        self.assertTrue(parsed["stack_trace"])

    def test_typescript_build_failure(self):
        parsed = cli.parse_failure(TS_LOG, step_name="build", job_name="build")
        self.assertEqual(parsed["suggested_fix_category"], "build_typescript")
        self.assertIn("TS2322", parsed["error_message"])

    def test_lint_failure(self):
        parsed = cli.parse_failure(LINT_LOG, step_name="lint", job_name="lint")
        self.assertEqual(parsed["suggested_fix_category"], "lint")

    def test_no_failing_job(self):
        def fake_json(_url, _token):
            return {"jobs": [{"id": 1, "name": "tests", "conclusion": "success", "steps": []}]}

        result = cli.analyze_run(
            "https://github.com/acme/widget/actions/runs/123",
            json_getter=fake_json,
            text_getter=lambda *_: "",
        )
        self.assertEqual(result["status"], "no_failing_job")
        self.assertEqual(result["failures"], [])

    def test_multiple_failed_steps_are_preserved(self):
        def fake_json(_url, _token):
            return {"jobs": [{
                "id": 7,
                "name": "ci",
                "conclusion": "failure",
                "steps": [
                    {"number": 3, "name": "build", "conclusion": "failure"},
                    {"number": 4, "name": "lint", "conclusion": "failure"},
                ],
            }]}

        result = cli.analyze_run(
            "https://github.com/acme/widget/actions/runs/123",
            json_getter=fake_json,
            text_getter=lambda *_: TS_LOG + "\n" + LINT_LOG,
        )
        self.assertEqual(len(result["failures"]), 2)
        self.assertEqual([row["failing_step_name"] for row in result["failures"]], ["build", "lint"])

    def test_api_error_path_is_structured(self):
        with patch.object(cli, "analyze_run", side_effect=cli.GitHubApiError("GitHub API HTTP 403")):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = cli.main(["https://github.com/acme/widget/actions/runs/123"])
        self.assertEqual(code, 2)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["status"], "error")
        self.assertIn("403", payload["error"])

    def test_output_schema_contains_required_top_level_fields(self):
        def fake_json(_url, _token):
            return {"jobs": [{
                "id": 8,
                "name": "tests",
                "conclusion": "failure",
                "steps": [{"number": 2, "name": "pytest", "conclusion": "failure"}],
            }]}

        result = cli.analyze_run(
            "https://github.com/acme/widget/actions/runs/999",
            json_getter=fake_json,
            text_getter=lambda *_: PYTEST_LOG,
        )
        for key in ("failing_step_name", "error_message", "stack_trace", "suggested_fix_category"):
            self.assertIn(key, result)


if __name__ == "__main__":
    unittest.main()

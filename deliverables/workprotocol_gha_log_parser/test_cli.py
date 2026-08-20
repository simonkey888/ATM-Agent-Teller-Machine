from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import cli

PYTEST_LOG = """=== FAILURES ===
Traceback (most recent call last):
  File "tests/test_api.py", line 42, in test_value
    assert actual == expected
AssertionError: expected 2 got 1
FAILED tests/test_api.py::test_value
"""
JEST_LOG = """FAIL src/math.test.js
Error: Expected: 2 Received: 1
    at Object.<anonymous> (src/math.test.js:14:9)
"""
TS_LOG = "src/index.ts(12,3): error TS2322: Type 'string' is not assignable to type 'number'.\nBuild failed\n"
LINT_LOG = """************* Module app
app.py:4:0: C0116: Missing function or method docstring (missing-function-docstring)
pylint score 7.5/10
"""


class AcceptanceTests(unittest.TestCase):
    def test_actions_url(self):
        target = cli.parse_run_url("https://github.com/acme/widget/actions/runs/123")
        self.assertEqual((target.owner, target.repo, target.run_id), ("acme", "widget", 123))

    def test_rejects_wrong_host(self):
        with self.assertRaises(ValueError):
            cli.parse_run_url("https://example.com/acme/widget/actions/runs/123")

    def test_pytest(self):
        row = cli.parse_failure(PYTEST_LOG, step_name="pytest", job_name="tests")
        self.assertEqual(row["suggested_fix_category"], "test_pytest")
        self.assertIn("AssertionError", row["error_message"])
        self.assertTrue(row["stack_trace"])

    def test_jest(self):
        row = cli.parse_failure(JEST_LOG, step_name="jest", job_name="tests")
        self.assertEqual(row["suggested_fix_category"], "test_jest")

    def test_typescript(self):
        row = cli.parse_failure(TS_LOG, step_name="build", job_name="build")
        self.assertEqual(row["suggested_fix_category"], "build_typescript")
        self.assertIn("TS2322", row["error_message"])

    def test_lint(self):
        row = cli.parse_failure(LINT_LOG, step_name="lint", job_name="lint")
        self.assertEqual(row["suggested_fix_category"], "lint")

    def test_multiple_failed_steps(self):
        def fake_json(_url, _token):
            return {"jobs": [{"id": 7, "name": "ci", "conclusion": "failure", "steps": [
                {"number": 3, "name": "build", "conclusion": "failure"},
                {"number": 4, "name": "lint", "conclusion": "failure"},
            ]}]}
        result = cli.analyze_run(
            "https://github.com/acme/widget/actions/runs/123",
            json_getter=fake_json,
            text_getter=lambda *_: TS_LOG + "\n" + LINT_LOG,
        )
        self.assertEqual(len(result["failures"]), 2)

    def test_api_error_is_json(self):
        with patch.object(cli, "analyze_run", side_effect=cli.GitHubApiError("GitHub API HTTP 403")):
            out = io.StringIO()
            with redirect_stdout(out):
                code = cli.main(["https://github.com/acme/widget/actions/runs/123"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out.getvalue())["status"], "error")


if __name__ == "__main__":
    unittest.main()

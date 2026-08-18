# GitHub Actions failure log parser

Shadow deliverable for WorkProtocol job `f82a9ca9-4b7f-4bdf-91c1-b5ae0516b4eb`.

This tool performs read-only GitHub Actions triage. It does not claim WorkProtocol jobs, submit deliverables, mutate GitHub runs, or spend funds.

## Installation

Python 3.11+ is required. From the repository root, no additional runtime dependency is needed:

```bash
python -m deliverables.workprotocol_gha_log_parser.cli --help
```

## Usage

```bash
export GITHUB_TOKEN=ghp_optional_read_token
python -m deliverables.workprotocol_gha_log_parser.cli \
  https://github.com/OWNER/REPO/actions/runs/RUN_ID
```

`GITHUB_TOKEN` is optional for public repositories and recommended for rate limits/private repositories. The token is only sent to `api.github.com` and is never emitted in output.

## Output

The CLI prints deterministic JSON. Example:

```json
{
  "error_message": "AssertionError: expected 2 got 1",
  "failing_step_name": "pytest",
  "failures": [
    {
      "error_message": "AssertionError: expected 2 got 1",
      "failing_step_name": "pytest",
      "job_name": "tests",
      "stack_trace": [
        "Traceback (most recent call last):",
        "File \"tests/test_api.py\", line 42, in test_value",
        "assert actual == expected",
        "AssertionError: expected 2 got 1"
      ],
      "suggested_fix_category": "test_pytest"
    }
  ],
  "run_url": "https://github.com/OWNER/REPO/actions/runs/RUN_ID",
  "schema": "workprotocol.github_actions_failure.v1",
  "stack_trace": [
    "Traceback (most recent call last):"
  ],
  "status": "failure_found",
  "suggested_fix_category": "test_pytest"
}
```

Supported fix categories include pytest and Jest test failures, TypeScript/general compilation failures, lint failures, and `unknown` when the log cannot be classified safely.

## Tests

```bash
python -m unittest tests.test_order009_r1_deliverable -v
```

The suite mocks GitHub API responses and covers malformed URLs, API errors, runs with no failing job, multiple failed steps, multiline stack traces, pytest/Jest, TypeScript compilation, linting, and deterministic schema behavior.

## Quality gate

```bash
python -m pylint deliverables/workprotocol_gha_log_parser/cli.py
```

The WorkProtocol acceptance threshold is `pylint >= 8.0`.

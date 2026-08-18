from __future__ import annotations

import base64
import json
import os
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

from .universal_radar import JsonHttpClient, UniversalOpportunity


class GitHubExecutionGuardError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExternalPrAuthorization:
    opportunity_id: str
    bounty_live: bool
    contribution_allowed: bool
    no_accepted_competing_solution: bool
    payout_linked: bool
    independent_check_passed: bool
    outgoing_spend_usd: int = 0

    @property
    def ready(self) -> bool:
        return (
            self.bounty_live
            and self.contribution_allowed
            and self.no_accepted_competing_solution
            and self.payout_linked
            and self.independent_check_passed
            and self.outgoing_spend_usd == 0
        )


class BoundedGitHubExecutor:
    """Official-API executor with explicit economic/write gates and no arbitrary CI compute."""

    arbitrary_ci_compute_allowed = False
    spam_pr_allowed = False
    max_patch_files = 20
    max_patch_bytes = 500_000

    def __init__(self, http: JsonHttpClient | None = None, token: str | None = None):
        self.http = http or JsonHttpClient()
        self.token = (token if token is not None else (os.getenv("BUNDLE_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN") or "")).strip()

    def _headers(self, *, require_write: bool = False) -> dict[str, str]:
        if require_write and not self.token:
            raise GitHubExecutionGuardError("external GitHub write credential absent")
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def read_repository(self, owner: str, repo: str) -> dict[str, Any]:
        data = self.http.get_json(f"https://api.github.com/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}", headers=self._headers(), timeout=10)
        if not isinstance(data, dict):
            raise GitHubExecutionGuardError("repository response malformed")
        return data

    def inspect_issue(self, owner: str, repo: str, issue_number: int) -> dict[str, Any]:
        data = self.http.get_json(f"https://api.github.com/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}/issues/{int(issue_number)}", headers=self._headers(), timeout=10)
        if not isinstance(data, dict):
            raise GitHubExecutionGuardError("issue response malformed")
        return data

    def inspect_file(self, owner: str, repo: str, path: str, ref: str) -> bytes:
        if ".." in path.split("/"):
            raise GitHubExecutionGuardError("unsafe repository path")
        url = f"https://api.github.com/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}/contents/{urllib.parse.quote(path)}?ref={urllib.parse.quote(ref)}"
        data = self.http.get_json(url, headers=self._headers(), timeout=10)
        if not isinstance(data, dict) or data.get("encoding") != "base64" or not data.get("content"):
            raise GitHubExecutionGuardError("file response missing base64 content")
        return base64.b64decode(str(data["content"]).replace("\n", ""), validate=True)

    def capability_preflight(self) -> dict[str, bool | str]:
        if not self.token:
            return {"read_public": True, "write_credential_present": False, "fork_branch_pr_possible": False, "reason": "NO_WRITE_CREDENTIAL"}
        try:
            data, headers, status = self.http.request_json("GET", "https://api.github.com/user", headers=self._headers(), timeout=8)
        except Exception as exc:
            return {"read_public": True, "write_credential_present": True, "fork_branch_pr_possible": False, "reason": type(exc).__name__}
        scopes = {part.strip() for part in str(headers.get("x-oauth-scopes") or "").split(",") if part.strip()}
        classic_ok = bool({"repo", "public_repo"} & scopes)
        fine_grained_possible = status == 200 and isinstance(data, dict) and bool(data.get("login")) and not scopes
        return {
            "read_public": True,
            "write_credential_present": True,
            "fork_branch_pr_possible": bool(classic_ok or fine_grained_possible),
            "reason": "SCOPE_HEADERS_ALLOW" if classic_ok else ("FINE_GRAINED_PERMISSION_MUST_BE_REPO_CHECKED" if fine_grained_possible else "INSUFFICIENT_SCOPE"),
        }

    @staticmethod
    def preclaim_guard(opportunity: UniversalOpportunity) -> None:
        if opportunity.source != "github-direct" and opportunity.executor_class.value != "GITHUB_BOUNDED_PATCH":
            raise GitHubExecutionGuardError("not a bounded GitHub patch opportunity")
        if opportunity.disposition.value == "REJECT":
            raise GitHubExecutionGuardError("rejected opportunity cannot reach GitHub write path")
        if opportunity.execution_cost_usd != 0:
            raise GitHubExecutionGuardError("outgoing spend prohibited")
        if opportunity.funding_status.value != "VERIFIED":
            raise GitHubExecutionGuardError("payout/funding linkage not verified")

    @staticmethod
    def validate_patch(files: dict[str, str | bytes]) -> None:
        if not files or len(files) > BoundedGitHubExecutor.max_patch_files:
            raise GitHubExecutionGuardError("patch file count outside bound")
        total = 0
        for path, value in files.items():
            if path.startswith("/") or ".." in path.split("/") or path.startswith(".github/workflows/"):
                raise GitHubExecutionGuardError("patch path forbidden")
            total += len(value.encode("utf-8") if isinstance(value, str) else value)
        if total > BoundedGitHubExecutor.max_patch_bytes:
            raise GitHubExecutionGuardError("patch too large")

    def fork_repository(self, owner: str, repo: str, authorization: ExternalPrAuthorization) -> dict[str, Any]:
        if not authorization.ready:
            raise GitHubExecutionGuardError("external PR authorization incomplete")
        data, _, _ = self.http.request_json(
            "POST",
            f"https://api.github.com/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}/forks",
            headers=self._headers(require_write=True),
            body={},
            timeout=20,
        )
        if not isinstance(data, dict):
            raise GitHubExecutionGuardError("fork response malformed")
        return data

    def create_branch(self, owner: str, repo: str, branch: str, base_sha: str, authorization: ExternalPrAuthorization) -> dict[str, Any]:
        if not authorization.ready or not re.fullmatch(r"[0-9a-f]{40}", base_sha):
            raise GitHubExecutionGuardError("branch authorization/base SHA invalid")
        data, _, _ = self.http.request_json(
            "POST",
            f"https://api.github.com/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}/git/refs",
            headers=self._headers(require_write=True),
            body={"ref": f"refs/heads/{branch}", "sha": base_sha},
            timeout=12,
        )
        return data if isinstance(data, dict) else {}

    def open_pull_request(
        self,
        owner: str,
        repo: str,
        *,
        head: str,
        base: str,
        title: str,
        body: str,
        authorization: ExternalPrAuthorization,
    ) -> dict[str, Any]:
        if not authorization.ready:
            raise GitHubExecutionGuardError("external PR authorization incomplete")
        data, _, _ = self.http.request_json(
            "POST",
            f"https://api.github.com/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}/pulls",
            headers=self._headers(require_write=True),
            body={"head": head, "base": base, "title": title[:200], "body": body[:20_000]},
            timeout=15,
        )
        return data if isinstance(data, dict) else {}

    def observe_ci(self, owner: str, repo: str, ref: str) -> dict[str, Any]:
        data = self.http.get_json(
            f"https://api.github.com/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}/commits/{urllib.parse.quote(ref)}/check-runs",
            headers={**self._headers(), "Accept": "application/vnd.github+json"},
            timeout=10,
        )
        return data if isinstance(data, dict) else {}

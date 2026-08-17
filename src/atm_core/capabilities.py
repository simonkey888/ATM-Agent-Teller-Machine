from __future__ import annotations

from typing import Iterable

CANONICAL_CAPABILITIES = {
    "model",
    "git",
    "github",
    "filesystem",
    "shell",
    "browser",
    "python",
    "node",
    "web3_readonly",
    "benchmark",
    "evidence",
}

RESERVED_DISABLED = {"cuda", "web3_signer", "payment_writer"}
HARD_DENY = {"web3_signer", "payment_writer"}


def _norm(values: Iterable[str]) -> list[str]:
    return sorted({str(value).strip().lower() for value in values if str(value).strip()})


class CapabilityResolver:
    """Deterministic capability matching; never grants financial/signing authority."""

    @staticmethod
    def assert_manifest_safe(capabilities: Iterable[str]) -> None:
        caps = set(_norm(capabilities))
        forbidden = caps & HARD_DENY
        if forbidden:
            raise ValueError("financial/signing capabilities are disabled: " + ",".join(sorted(forbidden)))

    @staticmethod
    def assert_job_safe(capabilities: Iterable[str]) -> None:
        caps = set(_norm(capabilities))
        forbidden = caps & RESERVED_DISABLED
        if forbidden:
            raise ValueError("job requested reserved disabled capabilities: " + ",".join(sorted(forbidden)))

    @staticmethod
    def can_handle(manifest_capabilities: Iterable[str], required_capabilities: Iterable[str]) -> dict[str, object]:
        offered = set(_norm(manifest_capabilities))
        required = set(_norm(required_capabilities))
        CapabilityResolver.assert_manifest_safe(offered)
        CapabilityResolver.assert_job_safe(required)
        missing = sorted(required - offered)
        if missing:
            return {"can_handle": False, "score": 0, "reason": "missing:" + ",".join(missing)}
        # Stable score rewards tight useful coverage instead of indiscriminate tool bundles.
        extra = len(offered - required)
        return {"can_handle": True, "score": max(1, 100 - min(50, extra)), "reason": "capability_match"}

from __future__ import annotations

import re
from typing import Iterable


BLOCKED_EXTERNAL_REQUEST_PATTERNS = [
    r"system\s+prompt",
    r"developer\s+prompt",
    r"boot\s+context",
    r"initialization\s+payload",
    r"hidden\s+instructions?",
    r"conversation\s+context",
    r"(?:api[ _-]?keys?|private[ _-]?keys?|seed\s+phrase|mnemonic)",
    r"(?:environment|env)\s+dump",
    r"home\s+directory",
    r"credential\s+files?",
    r"authorization\s*:\s*bearer",
]


class PromptInjectionRisk(ValueError):
    pass


def detect_untrusted_instruction(text: str) -> list[str]:
    hits: list[str] = []
    for pattern in BLOCKED_EXTERNAL_REQUEST_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            hits.append(pattern)
    return hits


def assert_external_task_safe(text: str) -> None:
    hits = detect_untrusted_instruction(text)
    if hits:
        raise PromptInjectionRisk("external task requests protected context/secrets")


def redact_text(text: str, known_secret_values: Iterable[str] = ()) -> str:
    result = text
    for secret in known_secret_values:
        if secret:
            result = result.replace(secret, "***REDACTED***")

    patterns = [
        (r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+", r"\1***REDACTED***"),
        (r"(?i)([?&](?:token|key|api_key|access_token|signature)=)[^&#\s]+", r"\1***REDACTED***"),
        (r"(?i)(\b(?:api[_-]?key|token|secret|password|private[_-]?key)\s*[=:]\s*)[^\s,;]+", r"\1***REDACTED***"),
        (r"\bgh[opusr]_[A-Za-z0-9_]{20,}\b", "***REDACTED_GITHUB_TOKEN***"),
        (r"\bAIza[0-9A-Za-z_-]{20,}\b", "***REDACTED_GOOGLE_KEY***"),
    ]
    for pattern, replacement in patterns:
        result = re.sub(pattern, replacement, result)
    return result

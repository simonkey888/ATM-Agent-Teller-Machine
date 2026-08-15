from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "atm.json"
EXAMPLE_CONFIG = ROOT / "config" / "atm.example.json"
STATE_DIR = ROOT / ".atm"
STATE_FILE = STATE_DIR / "state.json"
LOG_DIR = STATE_DIR / "logs"

PHASES = ("DISCOVER", "CLAIM", "WORK", "CHECK", "SUBMIT", "MONITOR")
PROMPT_FILES = {
    "DISCOVER": "SCOUT.md",
    "CLAIM": "CLAIM.md",
    "WORK": "WORKER.md",
    "CHECK": "CHECKER.md",
    "SUBMIT": "SUBMIT.md",
    "MONITOR": "MONITOR.md",
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def default_state() -> dict[str, Any]:
    return {
        "version": 1,
        "phase": "DISCOVER",
        "cycle": 0,
        "target_paid_usd": 200.0,
        "paid_usd": 0.0,
        "accepted_usd": 0.0,
        "claimed_usd": 0.0,
        "active_opportunity": None,
        "last_result": None,
        "last_error": None,
        "consecutive_failures": 0,
        "human_gate": None,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }


def load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        return read_json(STATE_FILE)
    state = default_state()
    atomic_write_json(STATE_FILE, state)
    return state


def save_state(state: dict[str, Any]) -> None:
    state["updated_at"] = utcnow()
    atomic_write_json(STATE_FILE, state)


def ensure_config() -> Path:
    if DEFAULT_CONFIG.exists():
        return DEFAULT_CONFIG
    DEFAULT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(EXAMPLE_CONFIG, DEFAULT_CONFIG)
    return DEFAULT_CONFIG


def load_config() -> dict[str, Any]:
    return read_json(ensure_config())


def load_prompt(phase: str) -> str:
    name = PROMPT_FILES[phase]
    return (ROOT / "prompts" / name).read_text(encoding="utf-8")


def redact(obj: Any) -> Any:
    secret_re = re.compile(r"(token|secret|password|private[_-]?key|api[_-]?key)", re.I)
    if isinstance(obj, dict):
        return {k: ("***REDACTED***" if secret_re.search(k) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if not text:
        raise ValueError("Hermes returned empty output")
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S | re.I)
    if fenced:
        return json.loads(fenced.group(1))

    decoder = json.JSONDecoder()
    for idx, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[idx:])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    raise ValueError("Could not extract a JSON object from Hermes output")


def hermes_env_path() -> Path:
    local_appdata = os.getenv("LOCALAPPDATA")
    if local_appdata:
        return Path(local_appdata) / "hermes" / ".env"
    return Path.home() / ".hermes" / ".env"


@dataclass(frozen=True)
class Provider:
    provider: str
    model: str | None = None
    requires_env: str | None = None
    enabled: bool = True

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Provider":
        return cls(
            provider=raw["provider"],
            model=raw.get("model"),
            requires_env=raw.get("requires_env"),
            enabled=raw.get("enabled", True),
        )

    def available(self) -> bool:
        if not self.enabled or not self.requires_env:
            return self.enabled
        if os.getenv(self.requires_env):
            return True
        env_file = hermes_env_path()
        if env_file.exists():
            prefix = self.requires_env + "="
            for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.strip().startswith(prefix):
                    return bool(line.split("=", 1)[1].strip())
        return False


def providers(config: dict[str, Any], phase: str) -> list[Provider]:
    phase_map = config.get("providers_by_phase", {})
    raw = phase_map.get(phase)
    if not raw:
        raw = [config["provider"]["primary"], *config["provider"].get("fallbacks", [])]
    return [Provider.from_dict(item) for item in raw]


def build_prompt(phase: str, config: dict[str, Any], state: dict[str, Any]) -> str:
    role = load_prompt(phase)
    cfg_for_agent = {
        "target_paid_usd": config["target_paid_usd"],
        "min_reward_usd": config["min_reward_usd"],
        "ideal_reward_usd": config["ideal_reward_usd"],
        "max_task_hours": config["max_task_hours"],
        "max_competition": config["max_competition"],
        "allow_auto_claim": config["allow_auto_claim"],
        "allow_auto_pr": config["allow_auto_pr"],
        "dry_run": config["dry_run"],
        "sources": config["sources"],
    }
    return f"""\
{role}

RUNTIME_CONFIG:
{json.dumps(cfg_for_agent, indent=2)}

PERSISTED_STATE:
{json.dumps(redact(state), indent=2)}

ATM_ROOT={ROOT}

Return ONE JSON object only. No markdown fences.
"""


def run_hermes(prompt: str, config: dict[str, Any], phase: str) -> tuple[dict[str, Any], str]:
    hermes = shutil.which("hermes")
    if not hermes:
        raise RuntimeError("hermes command not found. Run scripts/install-atm.ps1 first.")

    failures: list[str] = []
    max_turns = str(config["hermes"]["max_turns"])
    timeout = int(config["hermes"]["process_timeout_seconds"])

    for p in providers(config, phase):
        if not p.available():
            failures.append(f"{p.provider}: unavailable ({p.requires_env or 'disabled'})")
            continue

        cmd = [
            hermes,
            "chat",
            "--quiet",
            "--provider",
            p.provider,
            "--max-turns",
            max_turns,
            "--toolsets",
            "web,terminal,skills",
            "-q",
            prompt,
        ]
        if p.model:
            cmd[5:5] = ["--model", p.model]

        try:
            cp = subprocess.run(
                cmd,
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            failures.append(f"{p.provider}: process timeout")
            continue

        output = (cp.stdout or "").strip()
        if cp.returncode != 0:
            tail = (cp.stderr or output)[-1200:]
            failures.append(f"{p.provider}: exit={cp.returncode}: {tail}")
            continue
        try:
            return extract_json(output), p.provider
        except Exception as exc:
            failures.append(f"{p.provider}: invalid JSON: {exc}; tail={output[-800:]}")
            continue

    raise RuntimeError("All configured providers failed:\n" + "\n".join(failures))


def log_event(kind: str, payload: Any) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = LOG_DIR / f"{stamp}-{kind}.json"
    path.write_text(json.dumps(redact(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def apply_result(state: dict[str, Any], phase: str, result: dict[str, Any]) -> None:
    status = str(result.get("status", "")).upper()
    state["last_result"] = redact(result)
    state["last_error"] = None
    state["consecutive_failures"] = 0

    if status == "HUMAN_GATE_REAL":
        state["human_gate"] = redact(result)
        return

    state["human_gate"] = None

    if result.get("active_opportunity") is not None:
        state["active_opportunity"] = result["active_opportunity"]

    for key in ("paid_usd", "accepted_usd", "claimed_usd"):
        if key in result:
            state[key] = float(result[key])

    next_phase = str(result.get("next_phase", "")).upper()
    if next_phase in PHASES:
        state["phase"] = next_phase
        return

    defaults = {
        "DISCOVER": "CLAIM" if state.get("active_opportunity") else "DISCOVER",
        "CLAIM": "WORK" if status in {"CLAIMED", "READY_TO_WORK"} else "DISCOVER",
        "WORK": "CHECK",
        "CHECK": "SUBMIT" if status in {"PASS", "READY_TO_SUBMIT"} else "WORK",
        "SUBMIT": "MONITOR" if status in {"SUBMITTED", "PR_OPEN"} else "SUBMIT",
        "MONITOR": "DISCOVER" if status in {"PAID", "REJECTED", "STALE", "MERGED"} else "MONITOR",
    }
    state["phase"] = defaults[phase]


def should_stop(config: dict[str, Any], state: dict[str, Any]) -> bool:
    return float(state.get("paid_usd", 0)) >= float(config["target_paid_usd"])


def run_cycle(config: dict[str, Any], state: dict[str, Any]) -> None:
    phase = state["phase"]
    prompt = build_prompt(phase, config, state)
    result, provider = run_hermes(prompt, config, phase)
    result["_provider"] = provider
    result["_phase"] = phase
    result["_at"] = utcnow()
    log_event(phase.lower(), result)
    apply_result(state, phase, result)
    state["cycle"] = int(state.get("cycle", 0)) + 1
    save_state(state)


def main() -> int:
    parser = argparse.ArgumentParser(description="ATM: persistent bounty-agent supervisor")
    parser.add_argument("--once", action="store_true", help="Run one state-machine cycle and exit")
    parser.add_argument("--status", action="store_true", help="Print state and exit")
    parser.add_argument("--reset", action="store_true", help="Reset local ATM state")
    args = parser.parse_args()

    config = load_config()
    state = load_state()

    if args.reset:
        state = default_state()
        state["target_paid_usd"] = config["target_paid_usd"]
        save_state(state)

    if args.status:
        print(json.dumps(redact(state), indent=2, ensure_ascii=False))
        return 0

    state["target_paid_usd"] = config["target_paid_usd"]
    save_state(state)

    backoff = int(config["loop"]["failure_backoff_seconds"])
    max_backoff = int(config["loop"]["max_failure_backoff_seconds"])

    while True:
        if should_stop(config, state):
            print(f"TARGET_REACHED paid_usd={state['paid_usd']}")
            return 0

        if state.get("human_gate"):
            gate = state["human_gate"]
            print("HUMAN_GATE_REAL")
            print(json.dumps(gate, indent=2, ensure_ascii=False))
            return 2

        try:
            run_cycle(config, state)
            backoff = int(config["loop"]["failure_backoff_seconds"])
        except KeyboardInterrupt:
            save_state(state)
            print("Interrupted; state persisted.")
            return 130
        except Exception as exc:
            state["last_error"] = str(exc)
            state["consecutive_failures"] = int(state.get("consecutive_failures", 0)) + 1
            save_state(state)
            log_event("error", {"error": str(exc), "state": state})
            if args.once:
                raise
            print(f"cycle failed: {exc}", file=sys.stderr)
            time.sleep(backoff)
            backoff = min(max_backoff, max(backoff * 2, 1))

        if args.once:
            return 0

        delay = int(config["loop"]["phase_delay_seconds"].get(state["phase"], 10))
        time.sleep(delay)


if __name__ == "__main__":
    raise SystemExit(main())

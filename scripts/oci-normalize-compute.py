#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from decimal import Decimal, InvalidOperation

SHAPE = "VM.Standard.A1.Flex"


def decimal_field(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{field}_MISSING_OR_INVALID")
    if not isinstance(value, (int, float, str)):
        raise ValueError(f"{field}_TYPE_INVALID")
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field}_NOT_NUMERIC") from None
    if not out.is_finite() or out < 0:
        raise ValueError(f"{field}_NOT_NONNEGATIVE_FINITE")
    return out


def canonical(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def extract_rows(payload: object) -> list[object]:
    # The runner deliberately requests `--query data`, which yields the data
    # array directly. Keep support for the ordinary OCI envelope as well so the
    # normalizer can be tested/reused independently. Anything else is ambiguous.
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if "data" not in payload:
            raise ValueError("DATA_FIELD_MISSING")
        rows = payload["data"]
        if not isinstance(rows, list):
            raise ValueError("DATA_NOT_ARRAY")
        return rows
    raise ValueError("ROOT_NOT_ARRAY_OR_OCI_ENVELOPE")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        rows = extract_rows(payload)

        cpu = Decimal(0)
        memory = Decimal(0)
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("INSTANCE_ROW_NOT_OBJECT")
            if row.get("shape") != SHAPE or row.get("lifecycle-state") == "TERMINATED":
                continue
            cfg = row.get("shape-config")
            if not isinstance(cfg, dict):
                raise ValueError("A1_SHAPE_CONFIG_MISSING_OR_INVALID")
            cpu += decimal_field(cfg.get("ocpus"), "A1_OCPUS")
            memory += decimal_field(cfg.get("memory-in-gbs"), "A1_MEMORY_IN_GBS")

        sys.stdout.write(f"{canonical(cpu)}\t{canonical(memory)}\n")
        return 0
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"OCI_COMPUTE_NORMALIZER=FAIL reason={exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

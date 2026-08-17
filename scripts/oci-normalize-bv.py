#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
from decimal import Decimal, InvalidOperation


def _to_nonnegative_integral(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value >= 0 and value.is_integer() else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = Decimal(text)
        except InvalidOperation:
            return None
        if not number.is_finite() or number < 0 or number != number.to_integral_value():
            return None
        return int(number)
    return None


def normalize(document: object) -> dict[str, object]:
    if not isinstance(document, dict):
        raise ValueError("TOP_LEVEL_NOT_OBJECT")
    rows = document.get("data")
    if not isinstance(rows, list):
        raise ValueError("DATA_NOT_ARRAY")

    normalized: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("ROW_NOT_OBJECT")
        item = dict(row)
        if item.get("lifecycle-state") != "TERMINATED":
            gbs = _to_nonnegative_integral(item.get("size-in-gbs"))
            if gbs is None:
                mbs = _to_nonnegative_integral(item.get("size-in-mbs"))
                if mbs is None or mbs % 1024 != 0:
                    raise ValueError("ACTIVE_VOLUME_SIZE_UNRESOLVABLE")
                gbs = mbs // 1024
            item["size-in-gbs"] = gbs
        normalized.append(item)

    result = dict(document)
    result["data"] = normalized
    return result


def main() -> int:
    try:
        document = json.load(sys.stdin)
        normalized = normalize(document)
    except (json.JSONDecodeError, ValueError) as exc:
        detail = exc.args[0] if exc.args else exc.__class__.__name__
        print(f"OCI_BV_NORMALIZE=FAIL detail={detail}", file=sys.stderr)
        return 3
    json.dump(normalized, sys.stdout, separators=(",", ":"), sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import os
import sys

import oci


def fail(reason: str, exc: BaseException | None = None) -> None:
    suffix = ""
    if exc is not None:
        status = getattr(exc, "status", None)
        code = getattr(exc, "code", None)
        parts = [f"type={type(exc).__name__}"]
        if status is not None:
            parts.append(f"status={status}")
        if code:
            parts.append(f"code={code}")
        suffix = " " + " ".join(parts)
    print(f"OCI_SDK_COMPUTE_INVENTORY=FAIL class={reason}{suffix}", file=sys.stderr)
    raise SystemExit(45)


def dec(value: object, field: str) -> Decimal:
    if value is None:
        fail(f"{field}_MISSING")
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        fail(f"{field}_INVALID")
    if not out.is_finite() or out < 0:
        fail(f"{field}_INVALID")
    return out


def canon(value: Decimal) -> str:
    if value == value.to_integral_value():
        return str(value.quantize(Decimal("1")))
    return format(value.normalize(), "f")


def main() -> int:
    cfg_file = os.environ.get("OCI_CLI_CONFIG_FILE", "").strip()
    profile = os.environ.get("OCI_CLI_PROFILE", "").strip()
    compartment = os.environ.get("ATM_OCI_COMPARTMENT_ID", "").strip()
    region = os.environ.get("ATM_OCI_REGION", "").strip()
    if not cfg_file or not profile or not compartment or not region:
        fail("SDK_INPUT_MISSING")

    try:
        config = oci.config.from_file(file_location=cfg_file, profile_name=profile)
        config["region"] = region
        client = oci.core.ComputeClient(config)
        response = oci.pagination.list_call_get_all_results(
            client.list_instances,
            compartment_id=compartment,
        )
    except BaseException as exc:  # fail closed and sanitize the exception
        fail("SDK_LIST_FAILED", exc)

    cpu = Decimal("0")
    mem = Decimal("0")
    count = 0
    for instance in response.data:
        if getattr(instance, "shape", None) != "VM.Standard.A1.Flex":
            continue
        if str(getattr(instance, "lifecycle_state", "") or "").upper() == "TERMINATED":
            continue
        shape_config = getattr(instance, "shape_config", None)
        if shape_config is None:
            fail("A1_SHAPE_CONFIG_MISSING")
        cpu += dec(getattr(shape_config, "ocpus", None), "A1_OCPUS")
        mem += dec(getattr(shape_config, "memory_in_gbs", None), "A1_MEMORY")
        count += 1

    print(f"OCI_SDK_COMPUTE_INVENTORY=PASS active_a1={count}", file=sys.stderr)
    print(f"{canon(cpu)}\t{canon(mem)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

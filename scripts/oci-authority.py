#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone

import oci

AUTHORITY_OBJECT = "atm-authority.json"


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None = None) -> str:
    return (value or now()).astimezone(timezone.utc).isoformat()


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def clients(config_file: str, profile: str):
    cfg = oci.config.from_file(config_file, profile)
    os_client = oci.object_storage.ObjectStorageClient(cfg)
    compute = oci.core.ComputeClient(cfg)
    namespace = str(os_client.get_namespace().data)
    bucket = os.getenv("ATM_STATE_BUCKET") or "atm-state-" + hashlib.sha256(str(cfg["tenancy"]).encode()).hexdigest()[:12]
    os_client.get_bucket(namespace, bucket)
    return cfg, os_client, compute, namespace, bucket


def load(client, namespace: str, bucket: str):
    try:
        response = client.get_object(namespace, bucket, AUTHORITY_OBJECT)
    except oci.exceptions.ServiceError as exc:
        if int(exc.status or 0) == 404:
            return None, None
        raise
    data = response.data.content if hasattr(response.data, "content") else response.data.raw.read()
    record = json.loads(data)
    etag = response.headers.get("etag") or response.headers.get("ETag")
    if not etag:
        raise RuntimeError("AUTHORITY_ETAG_MISSING")
    return record, str(etag)


def validate(record: dict) -> None:
    if record.get("schema") != "ATM_AUTHORITY_V1":
        raise RuntimeError("AUTHORITY_SCHEMA_INVALID")
    if record.get("holder") not in {"GITHUB_ACTIONS", "OCI"}:
        raise RuntimeError("AUTHORITY_HOLDER_INVALID")
    if not isinstance(record.get("epoch"), int) or int(record["epoch"]) <= 0:
        raise RuntimeError("AUTHORITY_EPOCH_INVALID")
    if not re.fullmatch(r"[0-9a-f]{40}", str(record.get("source_sha") or "")):
        raise RuntimeError("AUTHORITY_SOURCE_SHA_INVALID")


def cas_put(client, namespace: str, bucket: str, record: dict, etag: str) -> str:
    raw = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    try:
        response = client.put_object(namespace, bucket, AUTHORITY_OBJECT, raw, if_match=etag)
    except oci.exceptions.ServiceError as exc:
        if int(exc.status or 0) in {409, 412}:
            raise RuntimeError("AUTHORITY_CAS_CONFLICT") from exc
        raise
    return str(response.headers.get("etag") or response.headers.get("ETag") or "")


def promote(args) -> int:
    cfg, client, compute, ns, bucket = clients(args.config_file, args.profile)
    record, etag = load(client, ns, bucket)
    if record is None or etag is None:
        raise RuntimeError("CLOUD_BASE_AUTHORITY_RECORD_MISSING")
    validate(record)
    state = str(compute.get_instance(args.instance_id).data.lifecycle_state).upper()
    if state != "RUNNING":
        raise RuntimeError("CANONICAL_OCI_INSTANCE_NOT_RUNNING")
    if record["holder"] == "OCI":
        if record.get("oci_instance_ocid") == args.instance_id and record.get("source_sha") == args.source_sha:
            print(json.dumps({"status":"ALREADY_OCI","epoch":record["epoch"],"instance_id":args.instance_id,"source_sha":args.source_sha}, sort_keys=True))
            return 0
        raise RuntimeError("OTHER_OCI_AUTHORITY_ALREADY_PRESENT")
    expires = parse_dt(record.get("lease_expires_at"))
    if expires and expires > now():
        raise RuntimeError("GITHUB_ACTIONS_LEASE_STILL_LIVE")
    new = {
        "schema":"ATM_AUTHORITY_V1",
        "epoch":int(record["epoch"])+1,
        "holder":"OCI",
        "lease_id":uuid.uuid4().hex,
        "lease_expires_at":iso(now()+timedelta(days=3650)),
        "heartbeat_at":iso(),
        "oci_instance_ocid":args.instance_id,
        "source_sha":args.source_sha,
    }
    cas_put(client, ns, bucket, new, etag)
    print(json.dumps({"status":"PROMOTED","epoch":new["epoch"],"instance_id":args.instance_id,"source_sha":args.source_sha,"bucket":bucket}, sort_keys=True))
    return 0


def verify(args) -> int:
    _, client, compute, ns, bucket = clients(args.config_file, args.profile)
    record, _ = load(client, ns, bucket)
    if record is None:
        raise RuntimeError("AUTHORITY_RECORD_MISSING")
    validate(record)
    if record.get("holder") != "OCI" or record.get("oci_instance_ocid") != args.instance_id or record.get("source_sha") != args.source_sha:
        raise RuntimeError("OCI_AUTHORITY_MISMATCH")
    if args.epoch is not None and int(record["epoch"]) != int(args.epoch):
        raise RuntimeError("OCI_AUTHORITY_EPOCH_MISMATCH")
    state = str(compute.get_instance(args.instance_id).data.lifecycle_state).upper()
    if state != "RUNNING":
        raise RuntimeError("OCI_AUTHORITY_INSTANCE_NOT_RUNNING")
    print(json.dumps({"status":"VERIFIED","epoch":record["epoch"],"instance_state":state,"source_sha":args.source_sha}, sort_keys=True))
    return 0


def rollback(args) -> int:
    _, client, compute, ns, bucket = clients(args.config_file, args.profile)
    record, etag = load(client, ns, bucket)
    if record is None or etag is None:
        raise RuntimeError("AUTHORITY_RECORD_MISSING")
    validate(record)
    if record.get("holder") != "OCI" or record.get("oci_instance_ocid") != args.instance_id:
        raise RuntimeError("ROLLBACK_NOT_CURRENT_OCI_AUTHORITY")
    state = str(compute.get_instance(args.instance_id).data.lifecycle_state).upper()
    if state not in {"STOPPED", "TERMINATED"}:
        raise RuntimeError("ROLLBACK_REFUSED_INSTANCE_NOT_PROVEN_STOPPED")
    new = {
        "schema":"ATM_AUTHORITY_V1",
        "epoch":int(record["epoch"])+1,
        "holder":"GITHUB_ACTIONS",
        "lease_id":uuid.uuid4().hex,
        "lease_expires_at":iso(now()-timedelta(seconds=1)),
        "heartbeat_at":iso(),
        "oci_instance_ocid":None,
        "source_sha":args.source_sha,
    }
    cas_put(client, ns, bucket, new, etag)
    print(json.dumps({"status":"ROLLED_BACK","epoch":new["epoch"],"instance_state":state,"source_sha":args.source_sha}, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-file", default=os.getenv("OCI_CLI_CONFIG_FILE", os.path.expanduser("~/.oci/config")))
    parser.add_argument("--profile", default=os.getenv("OCI_CLI_PROFILE", "ATM_REMOTE"))
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("promote", "verify", "rollback"):
        p = sub.add_parser(name)
        p.add_argument("--instance-id", required=True)
        p.add_argument("--source-sha", required=True)
        if name == "verify": p.add_argument("--epoch", type=int)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_sha):
        raise SystemExit("SOURCE_SHA_INVALID")
    try:
        return {"promote":promote,"verify":verify,"rollback":rollback}[args.cmd](args)
    except Exception as exc:
        print(f"OCI_AUTHORITY_{args.cmd.upper()}=FAIL reason={type(exc).__name__}:{exc}", file=sys.stderr)
        return 24


if __name__ == "__main__":
    raise SystemExit(main())

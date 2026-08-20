from pathlib import Path


def test_a1_limit_gate_is_noninteractive_and_fail_closed():
    root = Path(__file__).resolve().parents[1]
    provision = (root / "scripts" / "oci-provision.sh").read_text()

    assert "python3 - \"$c\" \"$m\" -c" not in provision
    assert "python3 -c 'import sys; c=float(sys.argv[1]); m=float(sys.argv[2])" in provision
    assert "query_a1_limit" in provision
    assert "A1_CORE_LIMIT_QUERY_FAILED" in provision
    assert "A1_MEMORY_LIMIT_QUERY_FAILED" in provision
    assert "OCI_A1_LIMIT=FAIL" in provision
    assert "AVAILABLE_FIELD_MISSING_OR_INVALID" in provision
    assert "OCI_A1_LIMIT=OK" in provision

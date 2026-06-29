"""Core engines: categorize, baselines, crosswalk, auth, audit, conmon, assessment."""
from __future__ import annotations

import pytest


def test_categorize_high_water_mark(conn):
    from comply_forge import categorize
    r = categorize.categorize_and_select(
        conn, confidentiality="moderate", integrity="low",
        availability="high", model="high_water_mark")
    assert r["overall_impact"] == "high"
    assert r["baseline_id"] == "nist_800_53b@high"


def test_categorize_per_cia(conn):
    from comply_forge import categorize
    r = categorize.categorize_and_select(
        conn, confidentiality="moderate", integrity="low",
        availability="high", model="per_cia")
    # per-CIA keeps the three levels distinct rather than rolling up
    pd = r["per_dimension"]
    assert (pd["C"]["impact"], pd["I"]["impact"], pd["A"]["impact"]) == \
        ("moderate", "low", "high")


def test_baseline_control_ids(conn):
    from comply_forge import baselines
    ids = baselines.baseline_control_ids(conn, "nist_800_53b@moderate")
    assert isinstance(ids, list) and ids


def test_crosswalk_neighbors(conn):
    from comply_forge import crosswalk
    n = crosswalk.neighbors(conn, "nist_800_53", "ac-2")
    assert isinstance(n, list)


def test_auth_roundtrip(conn):
    from comply_forge import auth
    h = auth.hash_password("s3cret") if hasattr(auth, "hash_password") else None
    u = auth.authenticate(conn, "admin", "admin")
    assert u and u["username"] == "admin"
    assert auth.authenticate(conn, "admin", "wrong") is None


def test_tenant_isolation(conn, tenant_id):
    from comply_forge import auth
    t2 = auth.create_tenant(conn, "Second Org")
    assert t2 != tenant_id
    tenants = {t["tenant_id"] for t in auth.list_tenants(conn)}
    assert tenant_id in tenants and t2 in tenants


def test_audit_log(conn, tenant_id):
    from comply_forge import audit
    audit.log(conn, tenant_id=tenant_id, username="admin",
              action="test.action", target="x", detail="d")
    recent = audit.recent(conn, tenant_id) if hasattr(audit, "recent") else []
    assert any(r["action"] == "test.action" for r in recent)


def test_conmon_summary(conn, tenant_id):
    from comply_forge import conmon
    s = conmon.summary(conn, tenant_id)
    assert isinstance(s, dict)


def test_assessment_objectives_real(real_conn):
    """800-53A objectives + methods extract from the real catalog."""
    from comply_forge import assessment
    s = assessment.summary(real_conn, "ac-2")
    if not s["objective_count"]:
        pytest.skip("full 800-53 not loaded locally")
    assert s["objective_count"] > 10
    assert "Examine" in s["method_labels"]
    # param placeholders are resolved, not left raw
    assert not any("{{ insert" in o.prose for o in s["objectives"])

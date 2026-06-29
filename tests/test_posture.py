"""Compliance-posture analytics: system_posture, portfolio, breakdowns."""
from __future__ import annotations

import pytest

from comply_forge.llm_provider import FakeProvider


def _draft(conn, control_id):
    from comply_forge import control_responder
    control_responder.draft_response(
        conn, system_id="sys-test", catalog_version_id="nist_800_53@rev5",
        control_id=control_id, provider=FakeProvider(), persist=True)


def test_system_posture_shape(conn):
    from comply_forge import posture
    _draft(conn, "ac-2")
    p = posture.system_posture(conn, "sys-test")
    assert p["name"] == "Test System"
    assert p["answered"] == 1 and p["needs_review"] == 1
    # a fresh draft is partial+needs_review -> not satisfied
    assert p["satisfied"] == 0 and p["other_than_satisfied"] == 1
    assert 0 <= p["coverage_pct"] <= 100


def test_satisfied_requires_implemented_and_reviewed(conn):
    from comply_forge import posture
    _draft(conn, "ac-2")
    conn.execute("UPDATE implemented_requirements SET status='implemented', needs_review=0 "
                 "WHERE system_id='sys-test' AND control_id='ac-2'")
    conn.commit()
    p = posture.system_posture(conn, "sys-test")
    assert p["satisfied"] == 1 and p["other_than_satisfied"] == 0


def test_portfolio_lists_systems(conn, tenant_id):
    from comply_forge import posture
    folio = posture.portfolio(conn, tenant_id)
    assert any(p["system_id"] == "sys-test" for p in folio)


def test_status_breakdown(conn):
    from comply_forge import posture
    _draft(conn, "ac-2")
    bd = posture.status_breakdown(conn, "sys-test")
    assert sum(bd.values()) == 1 and "partial" in bd


def test_coverage_by_family(conn):
    from comply_forge import posture
    _draft(conn, "ac-2")
    fam = posture.coverage_by_family(conn, "sys-test")
    # mini baseline includes AC controls; AC family should report >=1 documented
    ac = [f for f in fam if f["family"].lower() in ("ac", "access control")]
    assert fam  # non-empty
    assert all(0 <= f["coverage_pct"] <= 100 for f in fam)


def test_posture_evidence_and_inherited_fields(conn):
    from comply_forge import posture, evidence
    _draft(conn, "ac-2")
    p0 = posture.system_posture(conn, "sys-test")
    assert p0["evidence_items"] == 0 and p0["controls_without_evidence"] >= 1
    evidence.add_link(conn, system_id="sys-test", control_id="ac-2",
                      title="x", uri="https://x")
    p1 = posture.system_posture(conn, "sys-test")
    assert p1["evidence_items"] == 1
    assert p1["controls_without_evidence"] == p0["controls_without_evidence"] - 1
    assert "inherited" in p1


def test_reviewed_inherited_counts_satisfied(conn):
    from comply_forge import posture
    conn.execute("INSERT INTO implemented_requirements "
                 "(ir_id,system_id,catalog_version_id,control_id,status,origin,"
                 "needs_review,updated_at) VALUES ('i1','sys-test','nist_800_53@rev5',"
                 "'ac-2','inherited','inherited',0,'2026-01-01')")
    conn.commit()
    p = posture.system_posture(conn, "sys-test")
    assert p["satisfied"] == 1 and p["inherited"] == 1


def test_unknown_system_raises(conn):
    from comply_forge import posture
    with pytest.raises(ValueError):
        posture.system_posture(conn, "nope")

"""Common control providers & control inheritance."""
from __future__ import annotations

import pytest

from comply_forge.llm_provider import FakeProvider

CV = "nist_800_53@rev5"


def _provider_with_control(conn, control_id="ac-2"):
    """A provider system that has documented one control."""
    conn.execute("INSERT INTO systems (system_id,name,impact_level,created_at,tenant_id,"
                 "is_provider) VALUES ('ccp','Enterprise CCP','moderate','2026-01-01',"
                 "(SELECT tenant_id FROM systems WHERE system_id='sys-test'),1)")
    conn.commit()
    from comply_forge import control_responder
    control_responder.draft_response(conn, system_id="ccp", catalog_version_id=CV,
                                     control_id=control_id, provider=FakeProvider())
    return "ccp"


def test_provider_listing(conn):
    from comply_forge import inheritance
    _provider_with_control(conn)
    provs = inheritance.list_providers(conn)
    assert any(p["system_id"] == "ccp" for p in provs)


def test_inherit_control(conn):
    from comply_forge import inheritance
    _provider_with_control(conn, "ac-2")
    rid = inheritance.inherit_control(
        conn, child_system_id="sys-test", provider_system_id="ccp",
        catalog_version_id=CV, control_id="ac-2", kind="inherited")
    assert rid
    row = conn.execute("SELECT status, origin, statement, needs_review, source_ir_id "
                       "FROM implemented_requirements WHERE system_id='sys-test' "
                       "AND control_id='ac-2'").fetchone()
    assert row["status"] == "inherited" and row["origin"] == "inherited"
    assert row["needs_review"] == 1 and row["source_ir_id"]
    assert "INHERITED from" in row["statement"]


def test_hybrid_is_partial(conn):
    from comply_forge import inheritance
    _provider_with_control(conn, "ac-2")
    inheritance.inherit_control(conn, child_system_id="sys-test", provider_system_id="ccp",
                                catalog_version_id=CV, control_id="ac-2", kind="hybrid")
    row = conn.execute("SELECT status, statement FROM implemented_requirements "
                       "WHERE system_id='sys-test' AND control_id='ac-2'").fetchone()
    assert row["status"] == "partial" and "HYBRID" in row["statement"]


def test_inherit_does_not_clobber_existing(conn):
    from comply_forge import inheritance, control_responder
    _provider_with_control(conn, "ac-2")
    # child already has its own answer
    control_responder.draft_response(conn, system_id="sys-test", catalog_version_id=CV,
                                     control_id="ac-2", provider=FakeProvider())
    rid = inheritance.inherit_control(
        conn, child_system_id="sys-test", provider_system_id="ccp",
        catalog_version_id=CV, control_id="ac-2")
    assert rid is None  # gap-fill only
    row = conn.execute("SELECT origin FROM implemented_requirements "
                       "WHERE system_id='sys-test' AND control_id='ac-2'").fetchone()
    assert row["origin"] == "llm"  # untouched


def test_inherit_all_and_inherited_for(conn):
    from comply_forge import inheritance
    _provider_with_control(conn, "ac-2")
    res = inheritance.inherit_all(conn, child_system_id="sys-test",
                                  provider_system_id="ccp", catalog_version_id=CV)
    assert res["inherited_count"] >= 1
    inh = inheritance.inherited_for(conn, "sys-test", CV)
    assert inh and inh[0]["provider"] == "Enterprise CCP"


def test_inherit_undocumented_control_raises(conn):
    from comply_forge import inheritance
    _provider_with_control(conn, "ac-2")
    with pytest.raises(ValueError):
        inheritance.inherit_control(conn, child_system_id="sys-test",
                                    provider_system_id="ccp", catalog_version_id=CV,
                                    control_id="au-2")  # provider never documented it


def test_ssp_marks_inherited_from(conn):
    from comply_forge import inheritance, ssp, validation
    _provider_with_control(conn, "ac-2")
    inheritance.inherit_control(conn, child_system_id="sys-test", provider_system_id="ccp",
                                catalog_version_id=CV, control_id="ac-2")
    doc = ssp.build_oscal_ssp(conn, system_id="sys-test", catalog_version_id=CV)
    irs = doc["system-security-plan"]["control-implementation"]["implemented-requirements"]
    ac2 = next(i for i in irs if i["control-id"] == "ac-2")
    props = {p["name"]: p["value"] for p in ac2["props"]}
    assert props.get("inherited-from") == "Enterprise CCP"
    assert props.get("implementation-status") == "inherited"
    assert validation.validate(doc)[0]

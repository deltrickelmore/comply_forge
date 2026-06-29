"""Artifact generators: SSP/SAR/POA&M (+OSCAL validation), FISCAM, family plan, responder."""
from __future__ import annotations

import zipfile

from comply_forge.llm_provider import FakeProvider


def _document_one(conn):
    """Draft a response so SSP/SAR have an implemented requirement to include."""
    from comply_forge import control_responder
    control_responder.draft_response(
        conn, system_id="sys-test", catalog_version_id="nist_800_53@rev5",
        control_id="ac-2", provider=FakeProvider(), persist=True)


def test_control_responder_fake(conn):
    from comply_forge import control_responder
    r = control_responder.draft_response(
        conn, system_id="sys-test", catalog_version_id="nist_800_53@rev5",
        control_id="ac-2", provider=FakeProvider(), persist=True)
    assert r["control_id"] == "ac-2"
    row = conn.execute(
        "SELECT status, needs_review FROM implemented_requirements "
        "WHERE system_id=? AND control_id=?", ("sys-test", "ac-2")).fetchone()
    # LLM drafts are always needs_review=1 until a human signs off
    assert row and row["needs_review"] == 1


def test_ssp_builds_and_validates(conn):
    from comply_forge import ssp, validation
    _document_one(conn)
    doc = ssp.build_oscal_ssp(
        conn, system_id="sys-test", catalog_version_id="nist_800_53@rev5",
        baseline_impact="moderate")
    assert "system-security-plan" in doc
    ok, errors = validation.validate(doc)  # auto-detect model from root key
    assert ok, errors[:3]


def test_sar_builds_and_validates(conn):
    from comply_forge import sar, validation
    _document_one(conn)
    doc = sar.build_oscal_sar(
        conn, system_id="sys-test", catalog_version_id="nist_800_53@rev5")
    ok, errors = validation.validate(doc)
    assert ok, errors[:3]


def test_poam_builds_and_validates(conn):
    from comply_forge import poam, validation
    _document_one(conn)  # an open/needs-review item makes poam-items non-empty
    doc = poam.build_oscal_poam(
        conn, system_id="sys-test", catalog_version_id="nist_800_53@rev5",
        include_stig=False)
    assert doc["plan-of-action-and-milestones"]["poam-items"]
    ok, errors = validation.validate(doc)
    assert ok, errors[:3]


def test_fiscam_test_plan_workbook(conn, tmp_path):
    from comply_forge import fiscam_test_plan
    plan = fiscam_test_plan.draft_test_plan(
        conn, control_id="AC-1", control_title="Access Control Policy",
        control_text="The org develops an access control policy.",
        provider=FakeProvider())
    out = tmp_path / "tp.xlsx"
    fiscam_test_plan.write_workbook(plan, out)
    assert out.exists() and zipfile.is_zipfile(out)


def test_test_plan_uses_800_53a(real_conn):
    """When the control is an 800-53 control, TOD/TOE come from authoritative 800-53A."""
    import pytest
    from comply_forge import assessment, fiscam_test_plan
    if not assessment.objectives(real_conn, "ac-2"):
        pytest.skip("full 800-53 catalog not loaded locally")
    plan = fiscam_test_plan.draft_test_plan(
        real_conn, control_id="ac-2", provider=FakeProvider(), use_800_53a=True)
    assert plan.provenance["authoritative_800_53a"] is True
    assert "determine that" in plan.fields["tod_procedures"].lower()
    # opting out falls back to LLM/deterministic prose
    plan2 = fiscam_test_plan.draft_test_plan(
        real_conn, control_id="ac-2", provider=FakeProvider(), use_800_53a=False)
    assert plan2.provenance["authoritative_800_53a"] is False


def test_family_plan_docx(conn, tmp_path):
    from comply_forge import control_family_plan
    out = tmp_path / "ac_plan.docx"
    path = control_family_plan.generate_family_plan(
        conn, family="AC", baseline_id="nist_800_53b@moderate",
        provider=FakeProvider(), out_path=out)
    assert path.exists() and zipfile.is_zipfile(path)

"""PDF rendering of SSP / SAR / POA&M from their OSCAL dicts."""
from __future__ import annotations

from comply_forge.llm_provider import FakeProvider


def _setup(conn):
    from comply_forge import control_responder
    control_responder.draft_response(
        conn, system_id="sys-test", catalog_version_id="nist_800_53@rev5",
        control_id="ac-2", provider=FakeProvider(), persist=True)


def _is_pdf(path):
    with open(path, "rb") as f:
        return f.read(5) == b"%PDF-"


def test_ssp_pdf(conn, tmp_path):
    from comply_forge import ssp, pdf_export
    _setup(conn)
    doc = ssp.build_oscal_ssp(conn, system_id="sys-test",
                              catalog_version_id="nist_800_53@rev5")
    out = pdf_export.ssp_to_pdf(doc, tmp_path / "ssp.pdf",
                                prepared_by="CRC", prepared_for="Agency X",
                                brand_color="#1d4ed8")
    assert out.exists() and _is_pdf(out) and out.stat().st_size > 800


def test_sar_pdf(conn, tmp_path):
    from comply_forge import sar, pdf_export
    _setup(conn)
    doc = sar.build_oscal_sar(conn, system_id="sys-test",
                              catalog_version_id="nist_800_53@rev5")
    out = pdf_export.sar_to_pdf(doc, tmp_path / "sar.pdf")
    assert out.exists() and _is_pdf(out)


def test_poam_pdf(conn, tmp_path):
    from comply_forge import poam, pdf_export
    _setup(conn)
    doc = poam.build_oscal_poam(conn, system_id="sys-test",
                                catalog_version_id="nist_800_53@rev5",
                                include_stig=False)
    out = pdf_export.poam_to_pdf(doc, tmp_path / "poam.pdf")
    assert out.exists() and _is_pdf(out)


def test_pdf_escapes_markup(conn, tmp_path):
    """Angle-bracket content in a statement must not break reportlab markup."""
    from comply_forge import ssp, pdf_export
    conn.execute(
        "INSERT INTO implemented_requirements "
        "(ir_id,system_id,catalog_version_id,control_id,status,statement,origin,"
        "needs_review,updated_at) VALUES ('x','sys-test','nist_800_53@rev5','au-2',"
        "'partial','Uses <script> & <b> tags',?, 1, '2026-01-01')", ("llm",))
    conn.commit()
    doc = ssp.build_oscal_ssp(conn, system_id="sys-test",
                              catalog_version_id="nist_800_53@rev5")
    out = pdf_export.ssp_to_pdf(doc, tmp_path / "esc.pdf")
    assert _is_pdf(out)

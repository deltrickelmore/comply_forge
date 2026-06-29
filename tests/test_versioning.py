"""Revision diff + migration-impact engine (rev5 -> rev6 mini samples)."""
from __future__ import annotations

from pathlib import Path

from comply_forge.llm_provider import FakeProvider

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "data" / "samples"


def _load_rev6(conn):
    from comply_forge import catalog_loader
    return catalog_loader.load_oscal_catalog_file(
        conn, SAMPLES / "mini_800-53_rev6.json", framework_id="nist_800_53",
        framework_name="NIST SP 800-53", authority="NIST", version_label="Rev 6",
        make_current=False)


def test_diff_versions(conn):
    from comply_forge import versioning
    to_cv = _load_rev6(conn)
    diff = versioning.diff_versions(conn, "nist_800_53@rev5", to_cv)
    # mini rev6 adds ia-2, withdraws au-2 relative to rev5
    assert "ia-2" in diff["added"]
    assert "au-2" in diff["removed"]


def test_revision_changes_recorded(conn):
    from comply_forge import versioning
    to_cv = _load_rev6(conn)
    versioning.diff_versions(conn, "nist_800_53@rev5", to_cv)
    n = conn.execute(
        "SELECT COUNT(*) FROM revision_changes WHERE from_version_id='nist_800_53@rev5'"
    ).fetchone()[0]
    assert n >= 2


def test_flag_affected_answers(conn):
    """Answers on a withdrawn/modified control get needs_review=1."""
    from comply_forge import versioning, control_responder
    # document au-2 (withdrawn in rev6) on rev5, then approve it (needs_review=0)
    control_responder.draft_response(
        conn, system_id="sys-test", catalog_version_id="nist_800_53@rev5",
        control_id="au-2", provider=FakeProvider(), persist=True)
    conn.execute("UPDATE implemented_requirements SET needs_review=0 "
                 "WHERE system_id='sys-test' AND control_id='au-2'")
    conn.commit()
    to_cv = _load_rev6(conn)
    res = versioning.flag_affected_answers(conn, "nist_800_53@rev5", to_cv)
    assert res["flagged"] >= 1
    nr = conn.execute("SELECT needs_review FROM implemented_requirements "
                      "WHERE system_id='sys-test' AND control_id='au-2'").fetchone()[0]
    assert nr == 1


def test_migration_report(conn):
    from comply_forge import versioning
    to_cv = _load_rev6(conn)
    rpt = versioning.migration_report(conn, "nist_800_53", "nist_800_53@rev5", to_cv)
    assert "added" in rpt and "withdrawn" in rpt

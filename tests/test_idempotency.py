"""Re-drafting a control is idempotent — one answer per (system, catalog, control)."""
from __future__ import annotations

from comply_forge.llm_provider import FakeProvider


def _count(conn, control_id="ac-2"):
    return conn.execute(
        "SELECT COUNT(*) FROM implemented_requirements "
        "WHERE system_id='sys-test' AND catalog_version_id='nist_800_53@rev5' "
        "AND control_id=?", (control_id,)).fetchone()[0]


def test_redraft_keeps_single_row(conn):
    from comply_forge import control_responder as cr
    cr.draft_response(conn, system_id="sys-test", catalog_version_id="nist_800_53@rev5",
                      control_id="ac-2", provider=FakeProvider(), persist=True)
    cr.draft_response(conn, system_id="sys-test", catalog_version_id="nist_800_53@rev5",
                      control_id="ac-2", provider=FakeProvider(), persist=True)
    assert _count(conn) == 1


def test_redraft_resets_review_state(conn):
    from comply_forge import control_responder as cr
    cr.draft_response(conn, system_id="sys-test", catalog_version_id="nist_800_53@rev5",
                      control_id="ac-2", provider=FakeProvider(), persist=True)
    # simulate a human sign-off, then re-draft
    conn.execute("UPDATE implemented_requirements SET needs_review=0, "
                 "reviewed_by='alice', reviewed_at='2026-01-01' "
                 "WHERE system_id='sys-test' AND control_id='ac-2'")
    conn.commit()
    cr.draft_response(conn, system_id="sys-test", catalog_version_id="nist_800_53@rev5",
                      control_id="ac-2", provider=FakeProvider(), persist=True)
    row = conn.execute("SELECT needs_review, reviewed_by FROM implemented_requirements "
                       "WHERE system_id='sys-test' AND control_id='ac-2'").fetchone()
    assert row["needs_review"] == 1 and row["reviewed_by"] is None


def test_bulk_overwrite_no_duplicates(conn):
    from comply_forge import control_responder as cr, baselines
    bid = "nist_800_53b@moderate"
    cr.draft_baseline_responses(conn, system_id="sys-test",
                                catalog_version_id="nist_800_53@rev5",
                                baseline_id=bid, provider=FakeProvider())
    cr.draft_baseline_responses(conn, system_id="sys-test",
                                catalog_version_id="nist_800_53@rev5",
                                baseline_id=bid, provider=FakeProvider(), overwrite=True)
    # no control should have more than one row
    dups = conn.execute(
        "SELECT control_id, COUNT(*) c FROM implemented_requirements "
        "WHERE system_id='sys-test' GROUP BY control_id HAVING c > 1").fetchall()
    assert dups == []


def test_unique_index_exists(conn):
    rows = conn.execute("PRAGMA index_list(implemented_requirements)").fetchall()
    assert any(r["name"] == "idx_ir_unique" and r["unique"] for r in rows)

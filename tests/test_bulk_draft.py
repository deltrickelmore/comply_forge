"""Bulk baseline drafting: skip-existing, limit, error-tolerance, progress."""
from __future__ import annotations

import pytest

from comply_forge.llm_provider import FakeProvider


def test_draft_baseline_skips_existing(conn):
    from comply_forge import control_responder as cr
    # pre-document one baseline control
    ids = __import__("comply_forge.baselines", fromlist=["x"]).baseline_control_ids(
        conn, "nist_800_53b@moderate")
    assert ids
    cr.draft_response(conn, system_id="sys-test", catalog_version_id="nist_800_53@rev5",
                      control_id=ids[0], provider=FakeProvider(), persist=True)
    res = cr.draft_baseline_responses(
        conn, system_id="sys-test", catalog_version_id="nist_800_53@rev5",
        baseline_id="nist_800_53b@moderate", provider=FakeProvider())
    assert res["already_documented"] == 1
    assert ids[0] not in res["drafted"]
    # every drafted control persisted as needs_review
    nr = conn.execute("SELECT COUNT(*) FROM implemented_requirements "
                      "WHERE system_id='sys-test' AND needs_review=1").fetchone()[0]
    assert nr == res["drafted_count"] + 1


def test_draft_baseline_limit_and_progress(conn):
    from comply_forge import control_responder as cr
    seen = []
    res = cr.draft_baseline_responses(
        conn, system_id="sys-test", catalog_version_id="nist_800_53@rev5",
        baseline_id="nist_800_53b@moderate", provider=FakeProvider(),
        limit=1, progress=lambda d, t, c: seen.append((d, t, c)))
    assert res["drafted_count"] <= 1
    assert seen and seen[0][0] == 1


def test_draft_baseline_overwrite_redrafts_existing(conn):
    """overwrite=True re-drafts already-documented controls in place (no dupes)."""
    from comply_forge import control_responder as cr, baselines
    bid = "nist_800_53b@moderate"
    in_cat = {r[0] for r in conn.execute(
        "SELECT control_id FROM controls WHERE catalog_version_id='nist_800_53@rev5'")}
    target = next(c for c in baselines.baseline_control_ids(conn, bid) if c in in_cat)
    cr.draft_response(conn, system_id="sys-test", catalog_version_id="nist_800_53@rev5",
                      control_id=target, provider=FakeProvider(), persist=True)
    res = cr.draft_baseline_responses(
        conn, system_id="sys-test", catalog_version_id="nist_800_53@rev5",
        baseline_id=bid, provider=FakeProvider(), overwrite=True)
    assert target in res["drafted"]                    # re-drafted, not skipped
    n = conn.execute("SELECT COUNT(*) FROM implemented_requirements "
                     "WHERE system_id='sys-test' AND control_id=?", (target,)).fetchone()[0]
    assert n == 1                                       # still one row


def test_draft_baseline_missing_baseline(conn):
    from comply_forge import control_responder as cr
    with pytest.raises(ValueError):
        cr.draft_baseline_responses(
            conn, system_id="sys-test", catalog_version_id="nist_800_53@rev5",
            baseline_id="nope@none", provider=FakeProvider())


def test_draft_baseline_controls_not_in_catalog_are_skipped(conn):
    """Baseline ids absent from the (mini) catalog are skipped, not errored."""
    from comply_forge import control_responder as cr, baselines
    ids = baselines.baseline_control_ids(conn, "nist_800_53b@moderate")
    in_catalog = {r[0] for r in conn.execute(
        "SELECT control_id FROM controls WHERE catalog_version_id='nist_800_53@rev5'")}
    res = cr.draft_baseline_responses(
        conn, system_id="sys-test", catalog_version_id="nist_800_53@rev5",
        baseline_id="nist_800_53b@moderate", provider=FakeProvider())
    # anything in the baseline but not the mini catalog lands in skipped, never errors
    assert all(c not in in_catalog for c in res["skipped"])
    assert res["errors"] == []

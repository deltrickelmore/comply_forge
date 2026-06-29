"""GRC export adapters: eMASS control implementation, eMASS test results, GRC CSV."""
from __future__ import annotations

import csv
import io

from comply_forge.llm_provider import FakeProvider


def _document(conn):
    from comply_forge import control_responder
    control_responder.draft_response(
        conn, system_id="sys-test", catalog_version_id="nist_800_53@rev5",
        control_id="ac-2", provider=FakeProvider(), persist=True)


def _rows(text):
    return list(csv.DictReader(io.StringIO(text)))


def test_emass_controls_export(conn):
    from comply_forge import integrations
    _document(conn)
    rows = _rows(integrations.export_emass_controls_csv(
        conn, "sys-test", "nist_800_53@rev5"))
    assert rows
    r = rows[0]
    assert r["Control Acronym"] == "AC-2"
    # AC-2 was drafted partial + needs_review -> Planned, with a DRAFT comment
    assert r["Implementation Status"] in {"Planned", "Implemented"}
    assert "DRAFT" in r["Comments"]


def test_emass_test_results_export(conn):
    from comply_forge import integrations
    _document(conn)
    text = integrations.export_emass_test_results_csv(conn, "sys-test", "nist_800_53@rev5")
    rows = _rows(text)
    assert rows
    assert set(rows[0]) >= {"CCI", "Compliance Status", "Assessment Procedure"}


def test_grc_csv_export(conn):
    from comply_forge import integrations
    _document(conn)
    rows = _rows(integrations.export_grc_csv(conn, "sys-test", "nist_800_53@rev5",
                                             platform="Xacta"))
    assert rows
    assert rows[0]["Platform Hint"] == "Xacta"
    assert rows[0]["Review State"] in {"Needs Review", "Reviewed"}


def test_export_registry_shape():
    from comply_forge import integrations
    assert len(integrations.EXPORTS) == 3
    for label, (slug, fn) in integrations.EXPORTS.items():
        assert callable(fn) and isinstance(slug, str)

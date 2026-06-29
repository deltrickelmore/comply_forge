"""Framework loaders: catalogs, 800-171, generic OSCAL, registry, mappings."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOGS = ROOT / "data" / "catalogs"


def test_catalog_loaded(conn):
    n = conn.execute(
        "SELECT COUNT(*) FROM controls WHERE catalog_version_id='nist_800_53@rev5'"
    ).fetchone()[0]
    assert n >= 3
    cv = conn.execute(
        "SELECT catalog_version_id FROM catalog_versions WHERE is_current=1 "
        "AND catalog_version_id LIKE 'nist_800_53@%'").fetchone()
    assert cv and cv[0] == "nist_800_53@rev5"


def test_800171_loaded(conn):
    rows = conn.execute(
        "SELECT control_id, family FROM controls "
        "WHERE catalog_version_id='nist_800_171@rev3'").fetchall()
    assert rows
    assert any(r[0] == "3.1.1" for r in rows)


def test_mappings_loaded(conn):
    n = conn.execute("SELECT COUNT(*) FROM control_mappings").fetchone()[0]
    assert n >= 1


def test_generic_oscal_171_offline(conn):
    """The generic loader handles a real catalog (uses cached 800-171 file)."""
    from comply_forge import adapters
    f = CATALOGS / "nist_800_171_rev3.json"
    if not f.exists():
        import pytest
        pytest.skip("cached 800-171 catalog not present")
    res = adapters.load_generic_oscal(
        conn, f, framework_id="probe_171", name="probe", authority="NIST",
        version_label="t", id_mode="sortid", family_mode="dotnum",
        make_current=False)
    assert res["controls"] > 100


def test_framework_kinds_taxonomy():
    from comply_forge.db import FRAMEWORK_KINDS
    assert "control_catalog" in FRAMEWORK_KINDS
    assert len(FRAMEWORK_KINDS) >= 10

"""Shared fixtures. Seeds a temp DB offline from data/samples — no network."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "data" / "samples"


def _seed(conn):
    from comply_forge import catalog_loader, baselines, adapters, auth
    catalog_loader.load_oscal_catalog_file(
        conn, SAMPLES / "mini_800-53_rev5.json", framework_id="nist_800_53",
        framework_name="NIST SP 800-53", authority="NIST", version_label="Rev 5",
        make_current=True)
    # Seed low/moderate/high from the one mini profile so categorize can resolve
    # every impact level offline; then derive the CNSSI 1253 per-CIA sets.
    for impact in ("low", "moderate", "high"):
        baselines.load_baseline_profile_oscal(
            conn, SAMPLES / "mini_800-53b_moderate.json",
            baseline_id=f"nist_800_53b@{impact}", framework_id="nist_800_53",
            label=impact.title(), impact=impact)
    baselines.derive_cnssi_1253_from_800_53b(conn)
    adapters.load_controls_csv(
        conn, SAMPLES / "mini_800-171.csv", framework_id="nist_800_171",
        framework_name="NIST SP 800-171", authority="NIST", version_label="Rev 3")
    adapters.load_mappings_csv(conn, SAMPLES / "mappings_sample.csv")
    tenant_id = auth.ensure_seed(conn)
    conn.execute(
        "INSERT INTO systems (system_id,name,description,impact_level,created_at,tenant_id) "
        "VALUES (?,?,?,?,?,?)",
        ("sys-test", "Test System", "Fixture system", "moderate", "2026-01-01", tenant_id))
    conn.commit()
    return tenant_id


@pytest.fixture
def conn(tmp_path):
    from comply_forge.db import connect
    c = connect(tmp_path / "test.db")
    _seed(c)
    yield c
    c.close()


@pytest.fixture
def tenant_id(conn):
    return conn.execute(
        "SELECT tenant_id FROM tenants ORDER BY rowid LIMIT 1").fetchone()[0]


@pytest.fixture(scope="session")
def app_db(tmp_path_factory):
    """A seeded DB file the Streamlit AppTest can open via COMPLYFORGE_DB."""
    from comply_forge.db import connect
    p = tmp_path_factory.mktemp("appdb") / "app.db"
    c = connect(p)
    _seed(c)
    c.close()
    return p


@pytest.fixture
def real_conn():
    """The developer's real local DB (full 800-53). Skips if unavailable/empty."""
    from comply_forge.db import connect, DEFAULT_DB
    if not Path(DEFAULT_DB).exists():
        pytest.skip("no local DB")
    c = connect(check_same_thread=False)
    yield c
    c.close()

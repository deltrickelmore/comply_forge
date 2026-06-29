"""eMASS push client: payload builders, dry-run vs live seam, env-gated factory."""
from __future__ import annotations

from comply_forge.llm_provider import FakeProvider


def _document(conn):
    from comply_forge import control_responder
    control_responder.draft_response(
        conn, system_id="sys-test", catalog_version_id="nist_800_53@rev5",
        control_id="ac-2", provider=FakeProvider(), persist=True)


def test_controls_payload_shape(conn):
    from comply_forge import emass_client
    _document(conn)
    body = emass_client.build_controls_payload(conn, "sys-test", "nist_800_53@rev5")
    assert body
    item = body[0]
    assert item["acronym"] == "AC-2"
    assert item["implementationStatus"] in {"Planned", "Implemented", "Not Applicable"}
    assert "implementationNarrative" in item


def test_test_results_payload_empty_without_ccis(conn):
    # offline fixture has no CCI list loaded -> builder yields nothing (no crash)
    from comply_forge import emass_client
    _document(conn)
    body = emass_client.build_test_results_payload(conn, "sys-test", "nist_800_53@rev5")
    assert body == []


def test_test_results_payload_per_cci(real_conn):
    """Per-CCI test results build from the real DISA CCI list when available."""
    import pytest
    from comply_forge import emass_client, cci, control_responder
    if not cci.controls_ccis(real_conn, "ac-2"):
        pytest.skip("CCI list not loaded locally")
    real_conn.execute(
        "INSERT OR IGNORE INTO systems (system_id,name,impact_level,created_at) "
        "VALUES ('em-probe','eMASS Probe','moderate','2026-01-01')")
    control_responder.draft_response(
        real_conn, system_id="em-probe", catalog_version_id="nist_800_53@rev5",
        control_id="ac-2", provider=FakeProvider(), persist=True)
    try:
        body = emass_client.build_test_results_payload(
            real_conn, "em-probe", "nist_800_53@rev5")
        assert body and all("cci" in r and "complianceStatus" in r for r in body)
    finally:
        real_conn.execute("DELETE FROM implemented_requirements WHERE system_id='em-probe'")
        real_conn.execute("DELETE FROM systems WHERE system_id='em-probe'")
        real_conn.commit()


def test_dry_run_does_not_send(conn):
    from comply_forge import emass_client
    _document(conn)
    fake = emass_client.FakeEmassClient()
    res = fake.push_controls("sys-test", conn, "nist_800_53@rev5", dry_run=True)
    assert res["dry_run"] is True and res["count"] >= 1
    assert fake.calls == []  # nothing transmitted on a dry run


def test_live_push_uses_transport(conn):
    from comply_forge import emass_client
    _document(conn)
    fake = emass_client.FakeEmassClient()
    res = fake.push_controls("sys-test", conn, "nist_800_53@rev5", dry_run=False)
    assert res["meta"]["code"] == 200
    assert len(fake.calls) == 1
    method, path, body = fake.calls[0]
    assert method == "PUT" and path.endswith("/controls") and body


def test_get_client_unconfigured(monkeypatch):
    from comply_forge import emass_client
    for var in ("EMASS_URL", "EMASS_API_KEY", "EMASS_USER_UID"):
        monkeypatch.delenv(var, raising=False)
    assert emass_client.get_emass_client() is None
    assert emass_client.is_configured() is False


def test_get_client_configured(monkeypatch):
    from comply_forge import emass_client
    monkeypatch.setenv("EMASS_URL", "https://emass.example.mil")
    monkeypatch.setenv("EMASS_API_KEY", "key")
    monkeypatch.setenv("EMASS_USER_UID", "uid")
    client = emass_client.get_emass_client()
    assert client is not None and client.config.base_url.endswith("mil")

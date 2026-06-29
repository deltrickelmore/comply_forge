"""Xacta/Archer push seams: payload builders, dry-run vs live, env-gated factories."""
from __future__ import annotations

from comply_forge.llm_provider import FakeProvider


def _document(conn):
    from comply_forge import control_responder
    control_responder.draft_response(
        conn, system_id="sys-test", catalog_version_id="nist_800_53@rev5",
        control_id="ac-2", provider=FakeProvider(), persist=True)


def test_xacta_payload_shape(conn):
    from comply_forge import grc_push
    _document(conn)
    body = grc_push.build_xacta_payload(conn, "sys-test", "nist_800_53@rev5")
    assert body and body[0]["controlId"] == "AC-2"
    assert {"status", "implementationStatement", "reviewState"} <= set(body[0])


def test_archer_payload_shape(conn):
    from comply_forge import grc_push
    _document(conn)
    body = grc_push.build_archer_payload(conn, "sys-test", "nist_800_53@rev5")
    assert body and body[0]["Control ID"] == "AC-2"


def test_dry_run_does_not_send(conn):
    from comply_forge import grc_push
    _document(conn)
    fake = grc_push.FakePush(name="Xacta", builder=grc_push.build_xacta_payload)
    res = fake.push(conn, "sys-test", "nist_800_53@rev5", dry_run=True)
    assert res["dry_run"] is True and res["count"] >= 1 and res["target"] == "Xacta"
    assert fake.calls == []


def test_live_push_uses_transport(conn):
    from comply_forge import grc_push
    _document(conn)
    fake = grc_push.FakePush(name="Archer", builder=grc_push.build_archer_payload,
                             endpoint="/api/core/content")
    res = fake.push(conn, "sys-test", "nist_800_53@rev5", dry_run=False)
    assert res["status"] == "ok" and len(fake.calls) == 1
    method, path, body = fake.calls[0]
    assert method == "POST" and path == "/api/core/content" and body


def test_factories_unconfigured(monkeypatch):
    from comply_forge import grc_push
    for v in ("XACTA_URL", "XACTA_API_TOKEN", "ARCHER_URL", "ARCHER_SESSION_TOKEN"):
        monkeypatch.delenv(v, raising=False)
    assert grc_push.get_xacta_client() is None
    assert grc_push.get_archer_client() is None
    assert grc_push.is_configured("Xacta") is False
    # client_for still returns a usable FakePush for preview
    assert isinstance(grc_push.client_for("Archer"), grc_push.FakePush)


def test_xacta_factory_configured(monkeypatch):
    from comply_forge import grc_push
    monkeypatch.setenv("XACTA_URL", "https://xacta.example.mil")
    monkeypatch.setenv("XACTA_API_TOKEN", "tok")
    c = grc_push.get_xacta_client()
    assert c is not None and grc_push.is_configured("Xacta") is True
    assert c._auth_headers()["Authorization"].startswith("Bearer ")

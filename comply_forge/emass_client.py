"""
eMASS REST API push client — the live-API extension of integrations.py exports.

Design (mirrors llm_provider's seam):
  * Payload builders turn ComplyForge data into the documented eMASS REST JSON
    shapes (controls / test_results / poam). These are pure + fully testable.
  * EmassClient sends them over mutual-TLS (api-key + user-uid headers + client
    cert), via an injectable transport so tests never touch the network.
  * FakeEmassClient records calls and returns an eMASS-style success envelope.
  * get_emass_client() reads env (EMASS_URL / EMASS_API_KEY / EMASS_USER_UID /
    EMASS_CERT[/_KEY]) and returns a live client, or None if unconfigured.

Guardrails:
  * dry_run is the DEFAULT everywhere — build + preview payloads without sending.
  * Nothing here attests compliance; rows carry ComplyForge DRAFT status as-is and
    a human must confirm an actual push.

HONESTY: the request shapes follow DISA's published eMASS REST API field names,
but they are developed WITHOUT access to a live eMASS instance here. The builders
and payloads are unit-tested; the live HTTP path must be validated against a real
(or test) eMASS endpoint before production use. It is not marked "verified".
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.request
from dataclasses import dataclass

from . import integrations

_IMPL_STATUS = integrations._EMASS_STATUS
_COMPLIANCE = integrations._EMASS_COMPLIANCE


class EmassError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Payload builders (pure, testable)
# --------------------------------------------------------------------------- #
def build_controls_payload(conn, system_id: str, catalog_version_id: str) -> list[dict]:
    """eMASS PUT /api/systems/{systemId}/controls body items."""
    out = []
    for row in integrations.control_status_rows(conn, system_id, catalog_version_id):
        status = _IMPL_STATUS.get(row["status"], "Planned")
        item = {
            "acronym": row["control_id"].upper(),
            "implementationStatus": status,
            "implementationNarrative": row["narrative"],
        }
        if status == "Not Applicable":
            item["naJustification"] = "[provide justification]"
        out.append(item)
    return out


def build_test_results_payload(conn, system_id: str, catalog_version_id: str) -> list[dict]:
    """eMASS POST /api/systems/{systemId}/test_results body items (per CCI)."""
    from . import cci as _cci
    out = []
    for row in integrations.control_status_rows(conn, system_id, catalog_version_id):
        compliance = _COMPLIANCE.get(row["status"], "Non-Compliant")
        for cc in (_cci.controls_ccis(conn, row["control_id"]) or []):
            cid = cc.get("cci")
            if not cid:
                continue
            out.append({
                "cci": cid,
                "complianceStatus": compliance,
                "description": (cc.get("definition", "") or "")[:2000],
                # testedBy / testDate left for the human / eMASS to stamp
            })
    return out


def build_poam_payload(conn, system_id: str, catalog_version_id: str) -> list[dict]:
    """eMASS POST /api/systems/{systemId}/poam_items body items (from findings)."""
    from . import sar as _sar
    out = []
    for f in _sar.assess(conn, system_id, catalog_version_id)["findings"]:
        out.append({
            "status": "Ongoing",
            "vulnerabilityDescription": f["description"],
            "sourceIdentVuln": f.get("source", "control-response"),
            "controlAcronym": (f.get("control_id") or "").upper(),
        })
    return out


# --------------------------------------------------------------------------- #
# Client + transport seam
# --------------------------------------------------------------------------- #
@dataclass
class EmassConfig:
    base_url: str
    api_key: str
    user_uid: str
    cert_path: str | None = None
    key_path: str | None = None


class EmassClient:
    """Sends payloads to eMASS. transport(method, path, body) -> dict is injectable."""

    def __init__(self, config: EmassConfig, transport=None):
        self.config = config
        self._transport = transport or self._http

    def _http(self, method: str, path: str, body: list | dict) -> dict:
        url = self.config.base_url.rstrip("/") + path
        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method=method, headers={
            "api-key": self.config.api_key,
            "user-uid": self.config.user_uid,
            "Content-Type": "application/json",
        })
        ctx = None
        if self.config.cert_path:
            ctx = ssl.create_default_context()
            ctx.load_cert_chain(certfile=self.config.cert_path,
                                keyfile=self.config.key_path or self.config.cert_path)
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
                return json.loads(resp.read().decode() or "{}")
        except Exception as e:  # noqa: BLE001 — surface a clean error to the UI
            raise EmassError(f"eMASS {method} {path} failed: {e}") from e

    def push_controls(self, system_id, conn, catalog_version_id, *, dry_run=True) -> dict:
        body = build_controls_payload(conn, system_id, catalog_version_id)
        if dry_run:
            return {"dry_run": True, "endpoint": f"PUT /api/systems/{system_id}/controls",
                    "count": len(body), "payload": body}
        return self._transport("PUT", f"/api/systems/{system_id}/controls", body)

    def push_test_results(self, system_id, conn, catalog_version_id, *, dry_run=True) -> dict:
        body = build_test_results_payload(conn, system_id, catalog_version_id)
        if dry_run:
            return {"dry_run": True,
                    "endpoint": f"POST /api/systems/{system_id}/test_results",
                    "count": len(body), "payload": body}
        return self._transport("POST", f"/api/systems/{system_id}/test_results", body)

    def push_poam(self, system_id, conn, catalog_version_id, *, dry_run=True) -> dict:
        body = build_poam_payload(conn, system_id, catalog_version_id)
        if dry_run:
            return {"dry_run": True,
                    "endpoint": f"POST /api/systems/{system_id}/poam_items",
                    "count": len(body), "payload": body}
        return self._transport("POST", f"/api/systems/{system_id}/poam_items", body)


class FakeEmassClient(EmassClient):
    """Records pushes; returns an eMASS-style success envelope. For tests/preview."""

    def __init__(self):
        self.calls: list[tuple[str, str, list | dict]] = []
        super().__init__(EmassConfig("https://fake.emass.local", "k", "u"),
                         transport=self._record)

    def _record(self, method, path, body):
        self.calls.append((method, path, body))
        return {"meta": {"code": 200}, "data": [{"systemId": 0, "success": True}]}


def get_emass_client() -> EmassClient | None:
    """Live client from env, or None if eMASS isn't configured."""
    url = os.environ.get("EMASS_URL")
    key = os.environ.get("EMASS_API_KEY")
    uid = os.environ.get("EMASS_USER_UID")
    if not (url and key and uid):
        return None
    return EmassClient(EmassConfig(
        base_url=url, api_key=key, user_uid=uid,
        cert_path=os.environ.get("EMASS_CERT"),
        key_path=os.environ.get("EMASS_KEY")))


def is_configured() -> bool:
    return get_emass_client() is not None

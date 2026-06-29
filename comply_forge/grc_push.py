"""
Xacta & Archer REST push seams — same Fake/real, env-gated, dry-run-first pattern
as emass_client.py. Completes the "mesh with Archer, Xacta, MCCAST" story for the
API path (the file-export path lives in integrations.py).

Each target = a payload builder (pure, testable) + a thin client over an injectable
transport. Live HTTP is gated on env config; dry-run preview always works.

HONESTY: request shapes follow each vendor's published REST conventions but are
developed WITHOUT a live Xacta/Archer instance here — unit-tested, not live-verified.
Archer's content API in particular keys fields by instance-specific numeric IDs, so
the flat records below are a mapping starting point a human aligns at import time.
Nothing here attests; dry_run is the default and rows carry ComplyForge DRAFT status.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.request
from dataclasses import dataclass

from . import integrations


class GrcPushError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Payload builders (pure, testable)
# --------------------------------------------------------------------------- #
def build_xacta_payload(conn, system_id: str, catalog_version_id: str) -> list[dict]:
    """Xacta control-assessment records."""
    out = []
    for r in integrations.control_status_rows(conn, system_id, catalog_version_id):
        out.append({
            "controlId": r["control_id"].upper(),
            "status": integrations._EMASS_STATUS.get(r["status"], "Planned"),
            "implementationStatement": r["narrative"],
            "reviewState": "Needs Review" if r["needs_review"] else "Reviewed",
        })
    return out


def build_archer_payload(conn, system_id: str, catalog_version_id: str) -> list[dict]:
    """Archer content records (flat field map; align to instance field IDs at import)."""
    out = []
    for r in integrations.control_status_rows(conn, system_id, catalog_version_id):
        out.append({
            "Control ID": r["control_id"].upper(),
            "Control Status": r["status"].title(),
            "Implementation": r["narrative"],
            "Review State": "Needs Review" if r["needs_review"] else "Reviewed",
        })
    return out


# --------------------------------------------------------------------------- #
# Client base + transport seam
# --------------------------------------------------------------------------- #
@dataclass
class GrcConfig:
    base_url: str
    token: str = ""
    cert_path: str | None = None
    key_path: str | None = None
    extra_headers: dict | None = None


class _BasePush:
    name = "grc"

    def __init__(self, config: GrcConfig, transport=None):
        self.config = config
        self._transport = transport or self._http

    # subclasses define these
    def _auth_headers(self) -> dict:
        raise NotImplementedError

    def _endpoint(self, system_id: str) -> str:
        raise NotImplementedError

    def _build(self, conn, system_id, catalog_version_id) -> list[dict]:
        raise NotImplementedError

    def _http(self, method: str, path: str, body: list | dict, headers: dict) -> dict:
        url = self.config.base_url.rstrip("/") + path
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     method=method,
                                     headers={"Content-Type": "application/json", **headers})
        ctx = None
        if self.config.cert_path:
            ctx = ssl.create_default_context()
            ctx.load_cert_chain(certfile=self.config.cert_path,
                                keyfile=self.config.key_path or self.config.cert_path)
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
                return json.loads(resp.read().decode() or "{}")
        except Exception as e:  # noqa: BLE001
            raise GrcPushError(f"{self.name} {method} {path} failed: {e}") from e

    def push(self, conn, system_id: str, catalog_version_id: str, *, dry_run: bool = True) -> dict:
        body = self._build(conn, system_id, catalog_version_id)
        endpoint = self._endpoint(system_id)
        if dry_run:
            return {"dry_run": True, "target": self.name, "endpoint": endpoint,
                    "count": len(body), "payload": body}
        return self._transport("POST", endpoint, body, self._auth_headers())


class XactaClient(_BasePush):
    name = "Xacta"

    def _auth_headers(self):
        return {"Authorization": f"Bearer {self.config.token}"}

    def _endpoint(self, system_id):
        return f"/api/v2/systems/{system_id}/controls"

    def _build(self, conn, system_id, cv):
        return build_xacta_payload(conn, system_id, cv)


class ArcherClient(_BasePush):
    name = "Archer"

    def _auth_headers(self):
        # Archer REST uses a session token obtained from /api/core/security/login.
        return {"Authorization": f"Archer session-id={self.config.token}"}

    def _endpoint(self, system_id):
        return "/api/core/content"

    def _build(self, conn, system_id, cv):
        return build_archer_payload(conn, system_id, cv)


class FakePush(_BasePush):
    """Records pushes; returns a generic success envelope. For tests/preview."""

    def __init__(self, name: str = "Fake", builder=build_xacta_payload,
                 endpoint: str = "/api/fake"):
        self.name = name
        self._builder = builder
        self._ep = endpoint
        self.calls: list[tuple[str, str, list | dict]] = []
        super().__init__(GrcConfig("https://fake.local", "t"), transport=self._record)

    def _auth_headers(self):
        return {"Authorization": "Bearer t"}

    def _endpoint(self, system_id):
        return self._ep

    def _build(self, conn, system_id, cv):
        return self._builder(conn, system_id, cv)

    def _record(self, method, path, body, headers):
        self.calls.append((method, path, body))
        return {"status": "ok", "received": len(body)}


# --------------------------------------------------------------------------- #
# Env-gated factories
# --------------------------------------------------------------------------- #
def get_xacta_client() -> XactaClient | None:
    url, tok = os.environ.get("XACTA_URL"), os.environ.get("XACTA_API_TOKEN")
    if not (url and tok):
        return None
    return XactaClient(GrcConfig(base_url=url, token=tok,
                                 cert_path=os.environ.get("XACTA_CERT"),
                                 key_path=os.environ.get("XACTA_KEY")))


def get_archer_client() -> ArcherClient | None:
    url, tok = os.environ.get("ARCHER_URL"), os.environ.get("ARCHER_SESSION_TOKEN")
    if not (url and tok):
        return None
    return ArcherClient(GrcConfig(base_url=url, token=tok))


# UI registry: label -> (env factory, fake-builder, fake-endpoint)
TARGETS = {
    "Xacta": (get_xacta_client, build_xacta_payload, "/api/v2/systems/{}/controls"),
    "Archer": (get_archer_client, build_archer_payload, "/api/core/content"),
}


def client_for(label: str) -> _BasePush:
    """Live client if configured, else a FakePush so dry-run preview always works."""
    factory, builder, ep = TARGETS[label]
    return factory() or FakePush(name=label, builder=builder, endpoint=ep)


def is_configured(label: str) -> bool:
    return TARGETS[label][0]() is not None

"""
Authentication + multi-tenancy for ComplyForge.

Pure-Python (stdlib pbkdf2 hashing — no extra deps). The Streamlit login gate
lives in app.py and calls authenticate()/ensure_seed() here.

Model: a tenant is an organization; users belong to one tenant; systems (and all
their downstream artifacts) belong to a tenant via systems.tenant_id. Reference
data (controls/baselines/CCIs/STIG catalog) is shared across tenants.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import os
import uuid

_ITERS = 200_000


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERS)
    return f"{salt.hex()}:{dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, hash_hex = stored.split(":", 1)
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), _ITERS)
    return hmac.compare_digest(dk.hex(), hash_hex)


def create_tenant(conn, name: str) -> str:
    tid = "t_" + uuid.uuid4().hex[:12]
    conn.execute("INSERT INTO tenants (tenant_id, name, created_at) VALUES (?,?,?)",
                 (tid, name, _now()))
    conn.commit()
    return tid


def create_user(conn, username: str, password: str, tenant_id: str, role: str = "user") -> None:
    conn.execute("INSERT INTO users (username, password_hash, tenant_id, role, created_at) "
                 "VALUES (?,?,?,?,?)",
                 (username, hash_password(password), tenant_id, role, _now()))
    conn.commit()


def get_user(conn, username: str) -> dict | None:
    r = conn.execute("SELECT u.username, u.password_hash, u.tenant_id, u.role, t.name tenant_name "
                     "FROM users u JOIN tenants t ON t.tenant_id=u.tenant_id "
                     "WHERE u.username=?", (username,)).fetchone()
    return dict(r) if r else None


def authenticate(conn, username: str, password: str) -> dict | None:
    u = get_user(conn, username)
    if u and verify_password(password, u["password_hash"]):
        return {"username": u["username"], "tenant_id": u["tenant_id"],
                "tenant_name": u["tenant_name"], "role": u["role"]}
    return None


def list_tenants(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT tenant_id, name FROM tenants ORDER BY name")]


def ensure_seed(conn) -> str:
    """Create a default tenant + admin if none exist; backfill tenant-less systems.
    Returns the default tenant_id. Default creds: admin / admin (change on first use)."""
    has_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    tid = conn.execute("SELECT tenant_id FROM tenants ORDER BY created_at LIMIT 1").fetchone()
    if not tid:
        tid = create_tenant(conn, "Demo Org")
    else:
        tid = tid[0]
    if not has_users:
        create_user(conn, "admin", "admin", tid, role="admin")
    # backfill any system without a tenant to the default tenant
    conn.execute("UPDATE systems SET tenant_id=? WHERE tenant_id IS NULL", (tid,))
    conn.commit()
    return tid

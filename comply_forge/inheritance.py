"""
Common control providers & control inheritance (RMF).

A common control provider (CCP) — an enterprise, data center, or cloud platform —
documents a control once; many systems inherit it. Inheritance can be:
  * inherited : the provider fully satisfies the control for the child system.
  * hybrid    : the provider satisfies part; the child must document the rest.

Inheriting writes an implemented_requirements row in the CHILD with
origin='inherited', source_ir_id pointing at the provider's answer, and a
statement that names the provider. It never clobbers an existing child answer
(ON CONFLICT DO NOTHING). Hybrid rows are needs_review=1 (the child owes the
system-specific portion); a fully-inherited row is also needs_review=1 until a
human confirms the inheritance is appropriate — code never auto-attests.
"""

from __future__ import annotations

import datetime as _dt
import uuid


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def set_provider(conn, system_id: str, is_provider: bool = True) -> None:
    conn.execute("UPDATE systems SET is_provider=? WHERE system_id=?",
                 (1 if is_provider else 0, system_id))
    conn.commit()


def list_providers(conn, tenant_id: str | None = None) -> list[dict]:
    if tenant_id is None:
        rows = conn.execute(
            "SELECT system_id, name FROM systems WHERE is_provider=1 ORDER BY name").fetchall()
    else:
        rows = conn.execute(
            "SELECT system_id, name FROM systems WHERE is_provider=1 AND tenant_id=? "
            "ORDER BY name", (tenant_id,)).fetchall()
    return [dict(r) for r in rows]


def provider_controls(conn, provider_system_id: str, catalog_version_id: str) -> list[dict]:
    """Controls the provider has documented (available to inherit)."""
    rows = conn.execute(
        """SELECT ir_id, control_id, status, statement FROM implemented_requirements
            WHERE system_id=? AND catalog_version_id=? ORDER BY control_id""",
        (provider_system_id, catalog_version_id)).fetchall()
    return [dict(r) for r in rows]


def inherit_control(conn, *, child_system_id: str, provider_system_id: str,
                    catalog_version_id: str, control_id: str,
                    kind: str = "inherited") -> str | None:
    """Inherit one control from a provider. Returns the new ir_id, or None if the
    child already has an answer for that control (inheritance fills gaps only)."""
    if kind not in ("inherited", "hybrid"):
        raise ValueError("kind must be 'inherited' or 'hybrid'")
    control_id = control_id.lower()
    prov = conn.execute(
        """SELECT ir_id, name, statement FROM implemented_requirements ir
             JOIN systems s ON s.system_id=ir.system_id
            WHERE ir.system_id=? AND ir.catalog_version_id=? AND ir.control_id=?""",
        (provider_system_id, catalog_version_id, control_id)).fetchone()
    if prov is None:
        raise ValueError(f"provider has not documented {control_id.upper()}")

    pname = prov["name"]
    if kind == "inherited":
        status = "inherited"
        stmt = (f"[INHERITED from common control provider: {pname}] "
                + (prov["statement"] or ""))
    else:
        status = "partial"
        stmt = (f"[HYBRID — provider {pname} satisfies the common portion; "
                f"document the {control_id.upper()}-specific implementation for this "
                f"system] " + (prov["statement"] or ""))

    ir_id = str(uuid.uuid4())
    cur = conn.execute(
        """INSERT INTO implemented_requirements
             (ir_id, system_id, catalog_version_id, control_id, status, statement,
              origin, source_ir_id, needs_review, updated_at)
           VALUES (?,?,?,?,?,?,?,?,1,?)
           ON CONFLICT(system_id, catalog_version_id, control_id) DO NOTHING""",
        (ir_id, child_system_id, catalog_version_id, control_id, status, stmt,
         "inherited", prov["ir_id"], _now()))
    conn.commit()
    return ir_id if cur.rowcount else None


def inherit_all(conn, *, child_system_id: str, provider_system_id: str,
                catalog_version_id: str, kind: str = "inherited") -> dict:
    """Inherit every control the provider has documented that the child lacks."""
    created, skipped = [], []
    for c in provider_controls(conn, provider_system_id, catalog_version_id):
        rid = inherit_control(
            conn, child_system_id=child_system_id, provider_system_id=provider_system_id,
            catalog_version_id=catalog_version_id, control_id=c["control_id"], kind=kind)
        (created if rid else skipped).append(c["control_id"])
    return {"inherited": created, "skipped_existing": skipped,
            "inherited_count": len(created)}


def inherited_for(conn, child_system_id: str, catalog_version_id: str) -> list[dict]:
    """Controls the child inherits, with the provider they came from."""
    rows = conn.execute(
        """SELECT ir.control_id, ir.status, ps.name AS provider, ir.source_ir_id
             FROM implemented_requirements ir
             LEFT JOIN implemented_requirements src ON src.ir_id=ir.source_ir_id
             LEFT JOIN systems ps ON ps.system_id=src.system_id
            WHERE ir.system_id=? AND ir.catalog_version_id=? AND ir.origin='inherited'
            ORDER BY ir.control_id""",
        (child_system_id, catalog_version_id)).fetchall()
    return [dict(r) for r in rows]

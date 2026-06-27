"""
Audit logging. Records who did what, when — tenant-scoped. For a GRC tool this is
itself a control (accountability / non-repudiation of compliance actions).
"""

from __future__ import annotations

import csv
import datetime as _dt
import io


def log(conn, *, tenant_id: str | None, username: str | None,
        action: str, target: str = "", detail: str = "") -> None:
    conn.execute(
        "INSERT INTO audit_log (ts, tenant_id, username, action, target, detail) "
        "VALUES (?,?,?,?,?,?)",
        (_dt.datetime.now(_dt.timezone.utc).isoformat(), tenant_id, username,
         action, target, detail))
    conn.commit()


def recent(conn, tenant_id: str, limit: int = 200) -> list[dict]:
    rows = conn.execute(
        "SELECT ts, username, action, target, detail FROM audit_log "
        "WHERE tenant_id=? ORDER BY id DESC LIMIT ?", (tenant_id, limit)).fetchall()
    return [dict(r) for r in rows]


def export_csv(conn, tenant_id: str) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["timestamp", "user", "action", "target", "detail"])
    for r in conn.execute(
        "SELECT ts, username, action, target, detail FROM audit_log "
        "WHERE tenant_id=? ORDER BY id DESC", (tenant_id,)):
        w.writerow(r)
    return buf.getvalue()

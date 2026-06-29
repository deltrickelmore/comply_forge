"""
Evidence artifacts — files, links, or notes attached to a control for a system.

The audit answer to "where's the evidence?". Stored per (system, control); files
live as blobs in SQLite (small artifacts: screenshots, configs, signed memos).
Surfaced on the Draft Control Response page and cited as OSCAL links in the SSP.

Nothing here attests; evidence supports a human's review, it doesn't replace it.
"""

from __future__ import annotations

import datetime as _dt
import uuid


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def add_link(conn, *, system_id: str, control_id: str, title: str, uri: str,
             description: str = "", added_by: str = "") -> str:
    eid = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO evidence
             (evidence_id, system_id, control_id, kind, title, uri, description,
              added_by, added_at)
           VALUES (?,?,?,'link',?,?,?,?,?)""",
        (eid, system_id, control_id.lower(), title, uri, description, added_by, _now()))
    conn.commit()
    return eid


def add_note(conn, *, system_id: str, control_id: str, title: str,
             description: str = "", added_by: str = "") -> str:
    eid = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO evidence
             (evidence_id, system_id, control_id, kind, title, description,
              added_by, added_at)
           VALUES (?,?,?,'note',?,?,?,?)""",
        (eid, system_id, control_id.lower(), title, description, added_by, _now()))
    conn.commit()
    return eid


def add_file(conn, *, system_id: str, control_id: str, title: str, filename: str,
             blob: bytes, mime: str = "application/octet-stream",
             description: str = "", added_by: str = "") -> str:
    eid = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO evidence
             (evidence_id, system_id, control_id, kind, title, filename, mime, blob,
              description, added_by, added_at)
           VALUES (?,?,?,'file',?,?,?,?,?,?,?)""",
        (eid, system_id, control_id.lower(), title, filename, mime, blob,
         description, added_by, _now()))
    conn.commit()
    return eid


def list_for_control(conn, system_id: str, control_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT evidence_id, kind, title, uri, filename, mime, description,
                  added_by, added_at
             FROM evidence WHERE system_id=? AND control_id=? ORDER BY added_at""",
        (system_id, control_id.lower())).fetchall()
    return [dict(r) for r in rows]


def counts_for_system(conn, system_id: str) -> dict[str, int]:
    """control_id -> evidence count, for badges on the system's controls."""
    rows = conn.execute(
        "SELECT control_id, COUNT(*) n FROM evidence WHERE system_id=? GROUP BY control_id",
        (system_id,)).fetchall()
    return {r[0]: r[1] for r in rows}


def get_blob(conn, evidence_id: str) -> tuple[str, str, bytes] | None:
    row = conn.execute(
        "SELECT filename, mime, blob FROM evidence WHERE evidence_id=? AND kind='file'",
        (evidence_id,)).fetchone()
    return (row["filename"], row["mime"], row["blob"]) if row else None


def delete(conn, evidence_id: str) -> None:
    conn.execute("DELETE FROM evidence WHERE evidence_id=?", (evidence_id,))
    conn.commit()


def oscal_links(conn, system_id: str, control_id: str) -> list[dict]:
    """Evidence as OSCAL link objects for an SSP by-component."""
    links = []
    for e in list_for_control(conn, system_id, control_id):
        if e["kind"] == "link" and e["uri"]:
            href = e["uri"]
        else:  # file or note -> internal reference (blob lives in ComplyForge)
            href = f"#evidence-{e['evidence_id']}"
        link = {"href": href, "rel": "evidence", "text": e["title"]}
        if e.get("mime"):
            link["media-type"] = e["mime"]
        links.append(link)
    return links

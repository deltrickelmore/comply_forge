"""
Version-aware OSCAL catalog loader.

Ingests a NIST OSCAL catalog JSON (e.g. SP 800-53 Rev 5, SP 800-171) into the
ComplyForge DB. Each load creates a new `catalog_versions` row so revisions
coexist -- loading Rev 6 next year does NOT clobber Rev 5.

Usage:
    python -m comply_forge.catalog_loader \
        --framework nist_800_53 --name "NIST SP 800-53" --authority NIST \
        --version "Rev 5" --file path/to/NIST_SP-800-53_rev5_catalog.json \
        --make-current

Non-OSCAL frameworks (FISCAM 2024) load via comply_forge.adapters instead.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
from pathlib import Path
from typing import Any, Iterable

from .db import connect
from .models import Control


# --------------------------------------------------------------------------- #
# OSCAL parsing helpers
# --------------------------------------------------------------------------- #
def _iter_controls(group_or_catalog: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Recursively yield every control object from an OSCAL catalog/group tree."""
    for ctrl in group_or_catalog.get("controls", []) or []:
        yield ctrl
        # controls can nest (enhancements live under their base control)
        yield from _iter_controls(ctrl)
    for grp in group_or_catalog.get("groups", []) or []:
        yield from _iter_controls(grp)


def _flatten_prose(parts: list[dict[str, Any]] | None, want: str) -> str:
    """Flatten OSCAL `parts` of a given name ('statement' or 'guidance') to text."""
    if not parts:
        return ""
    chunks: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        if part.get("name") == want or want == "*":
            prose = part.get("prose")
            if prose:
                chunks.append(prose.strip())
        for sub in part.get("parts", []) or []:
            walk(sub)

    for p in parts:
        if p.get("name") == want:
            walk(p)
    return "\n".join(chunks).strip()


def _family_from_id(control_id: str) -> str:
    # 'ac-2' / 'ac-2.1' -> 'AC'
    return control_id.split("-", 1)[0].upper()


def _parse_control(raw: dict[str, Any]) -> Control:
    cid = (raw.get("id") or "").lower()
    parts = raw.get("parts", [])
    statement = _flatten_prose(parts, "statement")
    guidance = _flatten_prose(parts, "guidance")
    return Control(
        control_id=cid,
        family=_family_from_id(cid),
        title=raw.get("title", ""),
        statement=statement,
        guidance=guidance,
        params=raw.get("params", []) or [],
        oscal=raw,
    )


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def upsert_framework(conn, framework_id, name, authority, kind="control_catalog", notes=""):
    conn.execute(
        """INSERT INTO frameworks (framework_id, name, authority, kind, notes)
           VALUES (?,?,?,?,?)
           ON CONFLICT(framework_id) DO UPDATE SET
             name=excluded.name, authority=excluded.authority,
             kind=excluded.kind, notes=excluded.notes""",
        (framework_id, name, authority, kind, notes),
    )


def load_controls(
    conn,
    *,
    framework_id: str,
    framework_name: str,
    authority: str,
    version_label: str,
    controls: list[Control],
    oscal_uuid: str | None = None,
    source_uri: str | None = None,
    published: str | None = None,
    make_current: bool = False,
) -> str:
    """Insert a catalog version + its controls. Returns the catalog_version_id."""
    cv_id = f"{framework_id}@{version_label.lower().replace(' ', '')}"
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()

    upsert_framework(conn, framework_id, framework_name, authority)

    conn.execute(
        """INSERT INTO catalog_versions
             (catalog_version_id, framework_id, version_label, oscal_uuid,
              source_uri, published, loaded_at, is_current)
           VALUES (?,?,?,?,?,?,?,0)
           ON CONFLICT(catalog_version_id) DO UPDATE SET
             oscal_uuid=excluded.oscal_uuid, source_uri=excluded.source_uri,
             published=excluded.published, loaded_at=excluded.loaded_at""",
        (cv_id, framework_id, version_label, oscal_uuid, source_uri, published, now),
    )

    # Replace controls for this version (idempotent re-load).
    conn.execute("DELETE FROM controls WHERE catalog_version_id=?", (cv_id,))
    rows = [
        (cv_id, c.control_id, c.family, c.title, c.statement, c.guidance,
         json.dumps(c.params), json.dumps(c.oscal), c.content_hash())
        for c in controls
    ]
    conn.executemany(
        """INSERT INTO controls
             (catalog_version_id, control_id, family, title, statement,
              guidance, params_json, oscal_json, content_hash)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        rows,
    )

    if make_current:
        set_current(conn, framework_id, cv_id)

    conn.commit()
    return cv_id


def set_current(conn, framework_id: str, catalog_version_id: str) -> None:
    conn.execute("UPDATE catalog_versions SET is_current=0 WHERE framework_id=?", (framework_id,))
    conn.execute("UPDATE catalog_versions SET is_current=1 WHERE catalog_version_id=?",
                 (catalog_version_id,))
    conn.commit()


def load_oscal_catalog_file(
    conn,
    path: str | Path,
    *,
    framework_id: str,
    framework_name: str,
    authority: str,
    version_label: str,
    make_current: bool = False,
) -> str:
    """Load a standard OSCAL catalog JSON file."""
    path = Path(path)
    doc = json.loads(path.read_text())
    catalog = doc.get("catalog", doc)  # tolerate bare catalog or wrapped
    meta = catalog.get("metadata", {})
    controls = [_parse_control(c) for c in _iter_controls(catalog)]
    controls = [c for c in controls if c.control_id]  # drop malformed

    return load_controls(
        conn,
        framework_id=framework_id,
        framework_name=framework_name,
        authority=authority,
        version_label=version_label,
        controls=controls,
        oscal_uuid=catalog.get("uuid"),
        source_uri=str(path),
        published=meta.get("published") or meta.get("last-modified"),
        make_current=make_current,
    )


def _cli() -> None:
    ap = argparse.ArgumentParser(description="Load an OSCAL catalog into ComplyForge.")
    ap.add_argument("--framework", required=True, help="framework_id, e.g. nist_800_53")
    ap.add_argument("--name", required=True, help="human name, e.g. 'NIST SP 800-53'")
    ap.add_argument("--authority", default="NIST")
    ap.add_argument("--version", required=True, help="version label, e.g. 'Rev 5'")
    ap.add_argument("--file", required=True, help="path to OSCAL catalog JSON")
    ap.add_argument("--make-current", action="store_true")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    conn = connect(args.db) if args.db else connect()
    cv_id = load_oscal_catalog_file(
        conn, args.file,
        framework_id=args.framework, framework_name=args.name,
        authority=args.authority, version_label=args.version,
        make_current=args.make_current,
    )
    n = conn.execute("SELECT COUNT(*) FROM controls WHERE catalog_version_id=?", (cv_id,)).fetchone()[0]
    print(f"Loaded {n} controls into {cv_id}" + ("  [current]" if args.make_current else ""))


if __name__ == "__main__":
    _cli()

"""
Revision diff + migration-impact engine.

When a new revision of a framework is loaded (e.g. NIST 800-53 Rev 6), you want
two answers:
  1. What changed between the old revision and the new one?  (added / removed /
     modified / withdrawn controls)
  2. Which of MY existing answers (implemented_requirements) are affected, so a
     human knows what to re-review?

`diff_versions` answers (1) by comparing content hashes. `flag_affected_answers`
answers (2) by setting needs_review=1 on implemented_requirements whose control
changed, and records the change set in `revision_changes` for the audit trail.
"""

from __future__ import annotations

import datetime as _dt


def _controls_map(conn, catalog_version_id: str) -> dict[str, str]:
    """control_id -> content_hash for a catalog version."""
    rows = conn.execute(
        "SELECT control_id, content_hash FROM controls WHERE catalog_version_id=?",
        (catalog_version_id,),
    ).fetchall()
    return {r["control_id"]: r["content_hash"] for r in rows}


def diff_versions(conn, from_version_id: str, to_version_id: str) -> dict[str, list[str]]:
    """Return {'added':[...], 'removed':[...], 'modified':[...]} control_ids."""
    old = _controls_map(conn, from_version_id)
    new = _controls_map(conn, to_version_id)
    old_ids, new_ids = set(old), set(new)

    added = sorted(new_ids - old_ids)
    removed = sorted(old_ids - new_ids)        # treat as withdrawn
    modified = sorted(cid for cid in (old_ids & new_ids) if old[cid] != new[cid])

    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    records = (
        [(cid, "added") for cid in added]
        + [(cid, "withdrawn") for cid in removed]
        + [(cid, "modified") for cid in modified]
    )
    conn.executemany(
        """INSERT OR REPLACE INTO revision_changes
             (from_version_id, to_version_id, control_id, change_type, detail, detected_at)
           VALUES (?,?,?,?,?,?)""",
        [(from_version_id, to_version_id, cid, ct, None, now) for cid, ct in records],
    )
    conn.commit()
    return {"added": added, "removed": removed, "modified": modified}


def flag_affected_answers(conn, from_version_id: str, to_version_id: str) -> dict[str, int]:
    """
    For every existing answer tied to the OLD version whose control was modified
    or withdrawn in the NEW version, flag needs_review=1. Returns counts.

    Note: this does NOT auto-migrate answers to the new catalog_version_id -- that
    is a human decision. It surfaces exactly what to look at.
    """
    diff = diff_versions(conn, from_version_id, to_version_id)
    changed = set(diff["modified"]) | set(diff["removed"])
    if not changed:
        return {"flagged": 0, **{k: len(v) for k, v in diff.items()}}

    placeholders = ",".join("?" for _ in changed)
    cur = conn.execute(
        f"""UPDATE implemented_requirements
              SET needs_review=1
            WHERE catalog_version_id=?
              AND control_id IN ({placeholders})""",
        (from_version_id, *sorted(changed)),
    )
    conn.commit()
    return {"flagged": cur.rowcount, **{k: len(v) for k, v in diff.items()}}


def migration_report(conn, framework_id: str, from_version_id: str, to_version_id: str) -> str:
    """Human-readable summary string for a revision migration."""
    diff = diff_versions(conn, from_version_id, to_version_id)
    lines = [
        f"Migration: {from_version_id}  ->  {to_version_id}",
        f"  added     : {len(diff['added'])}",
        f"  withdrawn : {len(diff['removed'])}",
        f"  modified  : {len(diff['modified'])}",
    ]
    if diff["modified"]:
        lines.append("  modified controls: " + ", ".join(diff["modified"][:20])
                     + (" ..." if len(diff["modified"]) > 20 else ""))
    return "\n".join(lines)

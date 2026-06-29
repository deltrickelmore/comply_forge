"""
Crosswalk query + prefill engine.

The payoff feature: answer a control once in one framework, then pre-populate the
equivalent control(s) in another framework. Relation semantics (see models.py)
govern whether a prefill is "full" or "partial / needs human completion".
"""

from __future__ import annotations

import datetime as _dt
import uuid

# Which relations justify auto-prefill, and at what confidence.
_PREFILL = {
    "equivalent": "full",
    "superset":   "full",      # src broader -> fully covers dst
    "subset":     "partial",   # src narrower -> only partially fills dst
    "intersects": "partial",
    # 'related' intentionally excluded: topical only, never auto-prefill.
}


def neighbors(conn, framework: str, control_id: str) -> list[dict]:
    """All controls mapped from (framework, control_id), in either direction."""
    control_id = control_id.lower()
    fwd = conn.execute(
        """SELECT dst_framework AS framework, dst_control AS control_id,
                  relation, authority, note FROM control_mappings
            WHERE src_framework=? AND src_control=?""",
        (framework, control_id),
    ).fetchall()
    rev = conn.execute(
        """SELECT src_framework AS framework, src_control AS control_id,
                  relation, authority, note FROM control_mappings
            WHERE dst_framework=? AND dst_control=?""",
        (framework, control_id),
    ).fetchall()
    return [dict(r) for r in (*fwd, *rev)]


def prefill_targets(conn, src_framework: str, src_control: str) -> list[dict]:
    """Mapped controls eligible for prefill, annotated with confidence."""
    out = []
    for n in neighbors(conn, src_framework, src_control):
        conf = _PREFILL.get(n["relation"])
        if conf:
            out.append({**n, "confidence": conf})
    return out


def prefill_from_answer(conn, source_ir_id: str) -> list[str]:
    """
    Given a reviewed answer, create crosswalk-prefilled draft answers for the
    mapped controls in OTHER frameworks for the SAME system.

    Prefilled answers are ALWAYS needs_review=1 and origin='crosswalk_prefill' --
    a human must confirm before they count. Returns the new ir_ids.
    """
    src = conn.execute(
        "SELECT * FROM implemented_requirements WHERE ir_id=?", (source_ir_id,)
    ).fetchone()
    if src is None:
        raise ValueError(f"no such answer: {source_ir_id}")

    src_cv = conn.execute(
        "SELECT framework_id FROM catalog_versions WHERE catalog_version_id=?",
        (src["catalog_version_id"],),
    ).fetchone()
    src_framework = src_cv["framework_id"]

    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    created: list[str] = []

    for tgt in prefill_targets(conn, src_framework, src["control_id"]):
        # find the CURRENT catalog version for the target framework
        cv = conn.execute(
            """SELECT catalog_version_id FROM catalog_versions
                WHERE framework_id=? AND is_current=1""",
            (tgt["framework"],),
        ).fetchone()
        if cv is None:
            continue  # target framework not loaded / no current revision

        prefix = "" if tgt["confidence"] == "full" else \
            "[PARTIAL via crosswalk -- complete the remaining scope] "
        ir_id = str(uuid.uuid4())
        # Never clobber an existing answer: a crosswalk prefill only fills gaps.
        cur = conn.execute(
            """INSERT INTO implemented_requirements
                 (ir_id, system_id, catalog_version_id, control_id, status,
                  statement, origin, source_ir_id, needs_review, updated_at)
               VALUES (?,?,?,?,?,?,?,?,1,?)
               ON CONFLICT(system_id, catalog_version_id, control_id) DO NOTHING""",
            (ir_id, src["system_id"], cv["catalog_version_id"], tgt["control_id"],
             "partial" if tgt["confidence"] == "partial" else src["status"],
             prefix + (src["statement"] or ""),
             "crosswalk_prefill", source_ir_id, now),
        )
        if cur.rowcount:
            created.append(ir_id)

    conn.commit()
    return created

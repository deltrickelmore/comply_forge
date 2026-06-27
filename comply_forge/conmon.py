"""
Continuous Monitoring (RMF Monitor step).

Tracks each reviewed control's monitoring cadence and computes when it's next due
for reassessment, surfacing overdue / due-soon items. Operates on reviewed
(authorized) control responses — drafts awaiting review live in the Review Queue.
"""

from __future__ import annotations

import datetime as _dt

# monitoring frequency -> interval in days
FREQUENCIES = {
    "monthly": 30, "quarterly": 91, "semiannual": 182,
    "annual": 365, "triennial": 1095,
}
DUE_SOON_DAYS = 30


def _today() -> _dt.date:
    return _dt.datetime.now(_dt.timezone.utc).date()


def _parse_date(ts: str | None) -> _dt.date | None:
    if not ts:
        return None
    try:
        return _dt.date.fromisoformat(ts[:10])
    except ValueError:
        return None


def _next_review(reviewed_at: str | None, frequency: str) -> str | None:
    d = _parse_date(reviewed_at)
    if not d:
        return None
    return (d + _dt.timedelta(days=FREQUENCIES.get(frequency, 365))).isoformat()


def set_frequency(conn, ir_id: str, frequency: str) -> None:
    row = conn.execute("SELECT reviewed_at FROM implemented_requirements WHERE ir_id=?",
                       (ir_id,)).fetchone()
    nr = _next_review(row["reviewed_at"] if row else None, frequency)
    conn.execute("UPDATE implemented_requirements SET monitor_frequency=?, next_review=? WHERE ir_id=?",
                 (frequency, nr, ir_id))
    conn.commit()


def mark_reassessed(conn, ir_id: str, reviewer: str) -> None:
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    row = conn.execute("SELECT monitor_frequency FROM implemented_requirements WHERE ir_id=?",
                       (ir_id,)).fetchone()
    freq = (row["monitor_frequency"] if row else None) or "annual"
    conn.execute("UPDATE implemented_requirements SET reviewed_at=?, reviewed_by=?, "
                 "needs_review=0, status='implemented', next_review=?, updated_at=? WHERE ir_id=?",
                 (now, reviewer, _next_review(now, freq), now, ir_id))
    conn.commit()


def status_of(next_review: str | None) -> str:
    d = _parse_date(next_review)
    if not d:
        return "unscheduled"
    today = _today()
    if d < today:
        return "overdue"
    if (d - today).days <= DUE_SOON_DAYS:
        return "due_soon"
    return "current"


def items(conn, system_id: str) -> list[dict]:
    """Reviewed controls for a system with monitoring status."""
    rows = conn.execute(
        """SELECT ir_id, control_id, monitor_frequency, reviewed_at, reviewed_by, next_review
             FROM implemented_requirements
            WHERE system_id=? AND needs_review=0 AND reviewed_at IS NOT NULL
            ORDER BY control_id""", (system_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # backfill next_review if missing
        if not d["next_review"]:
            d["next_review"] = _next_review(d["reviewed_at"], d["monitor_frequency"] or "annual")
        d["status"] = status_of(d["next_review"])
        out.append(d)
    return out


def summary(conn, tenant_id: str) -> dict:
    """Counts of overdue / due-soon / current across a tenant's systems."""
    rows = conn.execute(
        """SELECT ir.monitor_frequency, ir.reviewed_at, ir.next_review
             FROM implemented_requirements ir JOIN systems s ON s.system_id=ir.system_id
            WHERE s.tenant_id=? AND ir.needs_review=0 AND ir.reviewed_at IS NOT NULL""",
        (tenant_id,)).fetchall()
    c = {"overdue": 0, "due_soon": 0, "current": 0, "unscheduled": 0}
    for r in rows:
        nr = r["next_review"] or _next_review(r["reviewed_at"], r["monitor_frequency"] or "annual")
        c[status_of(nr)] += 1
    return c

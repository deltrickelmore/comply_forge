"""
Compliance-posture analytics — pure read-only aggregations over the system's
control responses, baseline, and STIG findings. Powers the Dashboard's portfolio
view and per-system breakdowns; kept out of the UI so it's unit-testable.

A control counts as SATISFIED only when status='implemented' AND needs_review=0 —
the same gate the SAR uses. Everything else is other-than-satisfied.
"""

from __future__ import annotations


def _impact_baseline(impact: str) -> str:
    return f"nist_800_53b@{(impact or 'moderate').lower()}"


def system_posture(conn, system_id: str, impact: str | None = None) -> dict:
    """Coverage + assessment posture for one system."""
    row = conn.execute("SELECT name, impact_level FROM systems WHERE system_id=?",
                       (system_id,)).fetchone()
    if row is None:
        raise ValueError(f"no such system: {system_id}")
    impact = (impact or row["impact_level"] or "moderate").lower()
    bid = _impact_baseline(impact)

    total = conn.execute("SELECT COUNT(*) FROM baseline_controls WHERE baseline_id=?",
                         (bid,)).fetchone()[0]
    documented = conn.execute(
        """SELECT COUNT(DISTINCT ir.control_id) FROM implemented_requirements ir
             JOIN baseline_controls b ON b.control_id=ir.control_id AND b.baseline_id=?
            WHERE ir.system_id=?""", (bid, system_id)).fetchone()[0]
    satisfied = conn.execute(
        "SELECT COUNT(*) FROM implemented_requirements "
        "WHERE system_id=? AND status='implemented' AND needs_review=0",
        (system_id,)).fetchone()[0]
    answered = conn.execute(
        "SELECT COUNT(*) FROM implemented_requirements WHERE system_id=?",
        (system_id,)).fetchone()[0]
    needs_review = conn.execute(
        "SELECT COUNT(*) FROM implemented_requirements "
        "WHERE system_id=? AND needs_review=1", (system_id,)).fetchone()[0]

    open_findings = 0
    try:
        from . import stig as _stig
        open_findings = len(_stig.open_findings(conn, system_id))
    except Exception:
        pass

    return {
        "system_id": system_id, "name": row["name"], "impact": impact,
        "baseline_id": bid, "baseline_total": total, "documented": documented,
        "coverage_pct": round(100 * documented / total, 1) if total else 0.0,
        "answered": answered, "satisfied": satisfied,
        "other_than_satisfied": answered - satisfied,
        "needs_review": needs_review, "open_findings": open_findings,
    }


def portfolio(conn, tenant_id: str | None = None) -> list[dict]:
    """system_posture for every system in the tenant (or all if tenant_id is None)."""
    if tenant_id is None:
        rows = conn.execute("SELECT system_id FROM systems ORDER BY name").fetchall()
    else:
        rows = conn.execute("SELECT system_id FROM systems WHERE tenant_id=? ORDER BY name",
                            (tenant_id,)).fetchall()
    return [system_posture(conn, r[0]) for r in rows]


def status_breakdown(conn, system_id: str) -> dict[str, int]:
    """Counts of implemented_requirements by status for one system."""
    rows = conn.execute(
        "SELECT COALESCE(status,'planned') s, COUNT(*) n FROM implemented_requirements "
        "WHERE system_id=? GROUP BY s", (system_id,)).fetchall()
    return {r[0]: r[1] for r in rows}


def coverage_by_family(conn, system_id: str, impact: str | None = None) -> list[dict]:
    """Per-family baseline coverage for one system (which families have gaps)."""
    row = conn.execute("SELECT impact_level FROM systems WHERE system_id=?",
                       (system_id,)).fetchone()
    if row is None:
        raise ValueError(f"no such system: {system_id}")
    bid = _impact_baseline(impact or row["impact_level"])
    # current 800-53 catalog, for control->family
    cv = conn.execute("SELECT catalog_version_id FROM catalog_versions "
                      "WHERE framework_id='nist_800_53' AND is_current=1").fetchone()
    if cv is None:
        return []
    rows = conn.execute(
        """SELECT c.family,
                  COUNT(*) AS in_baseline,
                  COUNT(ir.control_id) AS documented
             FROM baseline_controls b
             JOIN controls c ON c.control_id=b.control_id AND c.catalog_version_id=?
             LEFT JOIN implemented_requirements ir
                    ON ir.control_id=b.control_id AND ir.system_id=?
            WHERE b.baseline_id=?
            GROUP BY c.family ORDER BY c.family""",
        (cv[0], system_id, bid)).fetchall()
    out = []
    for r in rows:
        out.append({"family": r["family"], "in_baseline": r["in_baseline"],
                    "documented": r["documented"],
                    "coverage_pct": round(100 * r["documented"] / r["in_baseline"], 1)
                    if r["in_baseline"] else 0.0})
    return out

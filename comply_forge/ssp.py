"""
System Security Plan (SSP) generator -- OSCAL JSON + Word.

Assembles a system's implemented_requirements (the control responses) into an SSP.
This is the #1 RMF output. Two renderings:
  * build_oscal_ssp()  -> OSCAL system-security-plan dict (JSON-serializable)
  * write_word_ssp()   -> a human-readable .docx

OSCAL NOTE (honest debt): this emits the CORE OSCAL SSP structure (metadata,
import-profile, system-characteristics, system-implementation, control-implementation
with implemented-requirements/by-components). It is structurally correct but is NOT
yet validated against the OSCAL JSON schema. Before relying on eMASS/Xacta import,
add schema validation via oscal-pydantic or compliance-trestle (validate_oscal()
hooks that in if installed).

AUDIT GUARDRAIL: by default the SSP includes all requirements but flags how many are
unreviewed. Pass require_reviewed=True to refuse export while any draft is unreviewed
(the tool drafts; a human authorizes).

OSCAL version targeted: 1.1.2
"""

from __future__ import annotations

import datetime as _dt
import json
import uuid
from pathlib import Path

OSCAL_VERSION = "1.1.2"
_OBJ = ("confidentiality", "integrity", "availability")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _system(conn, system_id: str) -> dict:
    row = conn.execute(
        "SELECT system_id, name, description, impact_level FROM systems WHERE system_id=?",
        (system_id,)).fetchone()
    if row is None:
        raise ValueError(f"no such system: {system_id}")
    return dict(row)


def _requirements(conn, system_id: str, catalog_version_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT control_id, status, statement, needs_review, reviewed_by
             FROM implemented_requirements
            WHERE system_id=? AND catalog_version_id=? ORDER BY control_id""",
        (system_id, catalog_version_id)).fetchall()
    return [dict(r) for r in rows]


def build_oscal_ssp(conn, *, system_id: str, catalog_version_id: str,
                    baseline_impact: str = "moderate",
                    require_reviewed: bool = False) -> dict:
    sysrow = _system(conn, system_id)
    reqs = _requirements(conn, system_id, catalog_version_id)
    unreviewed = sum(1 for r in reqs if r["needs_review"])
    if require_reviewed and unreviewed:
        raise ValueError(f"refusing SSP export: {unreviewed} requirement(s) unreviewed. "
                         "A human must approve them (Review Queue) first.")

    impact = (sysrow["impact_level"] or baseline_impact).lower()
    this_system_uuid = str(uuid.uuid4())

    implemented = []
    for r in reqs:
        implemented.append({
            "uuid": str(uuid.uuid4()),
            "control-id": r["control_id"],
            "props": [
                {"name": "implementation-status",
                 "value": r["status"] or "planned",
                 "ns": "https://complyforge.local/ns/oscal"},
                {"name": "review-status",
                 "value": "reviewed" if not r["needs_review"] else "draft-needs-review",
                 "ns": "https://complyforge.local/ns/oscal"},
            ],
            "by-components": [{
                "component-uuid": this_system_uuid,
                "uuid": str(uuid.uuid4()),
                "description": r["statement"] or "",
            }],
        })

    ssp = {"system-security-plan": {
        "uuid": str(uuid.uuid4()),
        "metadata": {
            "title": f"System Security Plan — {sysrow['name']}",
            "last-modified": _now(),
            "version": "0.1-draft",
            "oscal-version": OSCAL_VERSION,
            "props": [{"name": "generated-by", "value": "ComplyForge"}],
        },
        "import-profile": {"href": f"#{catalog_version_id.replace('@','_')}_{impact}_baseline"},
        "system-characteristics": {
            "system-ids": [{"id": sysrow["system_id"]}],
            "system-name": sysrow["name"],
            "description": sysrow["description"] or "",
            "security-sensitivity-level": impact,
            "system-information": {"information-types": [{
                "uuid": str(uuid.uuid4()),
                "title": "System information",
                "description": "Information processed, stored, and transmitted by the system.",
                "confidentiality-impact": {"base": impact},
                "integrity-impact": {"base": impact},
                "availability-impact": {"base": impact},
            }]},
            "security-impact-level": {
                "security-objective-confidentiality": impact,
                "security-objective-integrity": impact,
                "security-objective-availability": impact,
            },
            "status": {"state": "operational"},
        },
        "system-implementation": {
            "users": [{"uuid": str(uuid.uuid4()), "title": "System users",
                       "role-ids": ["user"]}],
            "components": [{
                "uuid": this_system_uuid,
                "type": "this-system",
                "title": sysrow["name"],
                "description": sysrow["description"] or "",
                "status": {"state": "operational"},
            }],
        },
        "control-implementation": {
            "description": f"Control implementation for {sysrow['name']} "
                           f"({len(implemented)} controls; {unreviewed} unreviewed).",
            "implemented-requirements": implemented,
        },
    }}
    return ssp


def write_oscal_ssp(ssp: dict, path: str | Path) -> Path:
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ssp, indent=2))
    return path


def validate_oscal(ssp: dict) -> tuple[bool, str]:
    """Best-effort OSCAL validation if oscal-pydantic is installed; else a skip note."""
    try:
        import oscal_pydantic  # noqa: F401
    except ImportError:
        return False, "oscal-pydantic not installed — structural emit only (validation skipped)."
    try:
        from oscal_pydantic.document import Document  # type: ignore
        Document.model_validate(ssp)
        return True, "OSCAL schema validation passed."
    except Exception as e:  # pragma: no cover
        return False, f"OSCAL validation error: {e}"


def write_word_ssp(conn, *, system_id: str, catalog_version_id: str,
                   path: str | Path, baseline_impact: str = "moderate",
                   org_name: str = "") -> Path:
    import docx
    from docx.shared import Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    sysrow = _system(conn, system_id)
    reqs = _requirements(conn, system_id, catalog_version_id)
    impact = (sysrow["impact_level"] or baseline_impact).lower()

    doc = docx.Document()
    doc.styles["Normal"].font.name = "Arial"; doc.styles["Normal"].font.size = Pt(11)

    t = doc.add_paragraph(); t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = t.add_run(f"System Security Plan\n{sysrow['name']}"); r.bold = True; r.font.size = Pt(18)
    sub = doc.add_paragraph(); sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub.add_run(f"Impact Level: {impact.title()}   ·   Version 0.1 (DRAFT — needs review)\n"
                f"{_dt.date.today():%d %b %Y}")
    if org_name:
        o = doc.add_paragraph(); o.alignment = WD_ALIGN_PARAGRAPH.CENTER
        o.add_run(f"Prepared for: {org_name}").italic = True
    doc.add_page_break()

    doc.add_paragraph("1. System Characteristics", style="Heading 1")
    tbl = doc.add_table(rows=0, cols=2); tbl.style = "Table Grid"
    for k, v in [("System Name", sysrow["name"]), ("System ID", sysrow["system_id"]),
                 ("Description", sysrow["description"] or ""),
                 ("Security Impact Level", impact.title()),
                 ("Confidentiality / Integrity / Availability", f"{impact.title()} / {impact.title()} / {impact.title()}")]:
        cells = tbl.add_row().cells; cells[0].text = k; cells[1].text = v

    doc.add_paragraph("2. Control Implementation", style="Heading 1")
    unreviewed = sum(1 for x in reqs if x["needs_review"])
    doc.add_paragraph(f"{len(reqs)} controls documented; {unreviewed} awaiting human review.")
    for req in reqs:
        doc.add_paragraph(f"{req['control_id'].upper()}", style="Heading 2")
        flag = "  [DRAFT — NEEDS REVIEW]" if req["needs_review"] else f"  [reviewed by {req['reviewed_by']}]"
        doc.add_paragraph(f"Status: {req['status'] or 'planned'}{flag}")
        doc.add_paragraph(req["statement"] or "[no statement drafted]")

    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path

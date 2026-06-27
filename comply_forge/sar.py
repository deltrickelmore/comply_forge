"""
Security Assessment Report (SAR) generator -- OSCAL assessment-results + Word.

Summarizes the assessment of a system's controls: which are satisfied vs other-
than-satisfied, with findings drawn from (a) control responses not fully
implemented / unreviewed and (b) open STIG findings. Completes the core RMF set
alongside the SSP, SAP (test plans), and POA&M.

Same OSCAL caveat as ssp/poam: emits the core structure; validate with
oscal-pydantic before relying on platform import.

OSCAL version targeted: 1.1.2
"""

from __future__ import annotations

import datetime as _dt
import json
import uuid
from pathlib import Path

OSCAL_VERSION = "1.1.2"
_SATISFIED = {"implemented"}


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def assess(conn, system_id: str, catalog_version_id: str) -> dict:
    """Return assessment summary + findings for a system."""
    sysrow = conn.execute("SELECT system_id, name FROM systems WHERE system_id=?",
                          (system_id,)).fetchone()
    if sysrow is None:
        raise ValueError(f"no such system: {system_id}")
    reqs = conn.execute(
        """SELECT control_id, status, needs_review, statement FROM implemented_requirements
            WHERE system_id=? AND catalog_version_id=? ORDER BY control_id""",
        (system_id, catalog_version_id)).fetchall()

    findings = []
    satisfied = 0
    for r in reqs:
        ok = (r["status"] in _SATISFIED) and not r["needs_review"]
        if ok:
            satisfied += 1
        else:
            reason = "awaiting review" if r["needs_review"] else f"status: {r['status'] or 'planned'}"
            findings.append({
                "control_id": r["control_id"], "result": "other-than-satisfied",
                "source": "control-response",
                "description": f"{r['control_id'].upper()} not satisfied ({reason})."})

    # STIG open findings
    try:
        from . import stig as _stig
        for f in _stig.open_findings(conn, system_id):
            findings.append({"control_id": f.get("control_id", ""),
                             "result": "other-than-satisfied", "source": "stig",
                             "description": f["weakness"]})
    except Exception:
        pass

    return {"system_id": sysrow["system_id"], "system_name": sysrow["name"],
            "assessed": len(reqs), "satisfied": satisfied,
            "other_than_satisfied": len(reqs) - satisfied, "findings": findings}


def build_oscal_sar(conn, *, system_id: str, catalog_version_id: str) -> dict:
    a = assess(conn, system_id, catalog_version_id)
    result_uuid = str(uuid.uuid4())
    oscal_findings = [{
        "uuid": str(uuid.uuid4()),
        "title": f"{(f['control_id'] or 'finding').upper()} — {f['result']}",
        "description": f["description"],
        "target": {
            "type": "objective-id",
            "target-id": f"{(f['control_id'] or 'finding').lower()}_obj",
            "status": {"state": "not-satisfied", "reason": "fail"},
        },
        "props": [
            {"name": "control-id", "value": (f["control_id"] or "").lower(),
             "ns": "https://complyforge.local/ns/oscal"},
            {"name": "finding-source", "value": f["source"],
             "ns": "https://complyforge.local/ns/oscal"},
        ],
    } for f in a["findings"]]

    return {"assessment-results": {
        "uuid": str(uuid.uuid4()),
        "metadata": {
            "title": f"Security Assessment Report — {a['system_name']}",
            "last-modified": _now(), "version": "0.1-draft",
            "oscal-version": OSCAL_VERSION,
            "props": [{"name": "generated-by", "value": "ComplyForge"}],
        },
        "import-ap": {"href": f"#sap_{system_id}"},
        "results": [{
            "uuid": result_uuid,
            "title": "Control Assessment Results",
            "description": (f"{a['assessed']} controls assessed; {a['satisfied']} satisfied, "
                            f"{a['other_than_satisfied']} other-than-satisfied."),
            "start": _now(),
            "reviewed-controls": {
                "control-selections": [{"include-all": {}}],
            },
            "findings": oscal_findings,
        }],
    }}


def write_oscal_sar(sar: dict, path: str | Path) -> Path:
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sar, indent=2))
    return path


def write_word_sar(conn, *, system_id: str, catalog_version_id: str, path: str | Path,
                   prepared_by: str = "", prepared_for: str = "", brand_color: str = "") -> Path:
    import docx
    from docx.shared import Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from ._docx_util import color_run, prepared_block

    a = assess(conn, system_id, catalog_version_id)
    doc = docx.Document()
    doc.styles["Normal"].font.name = "Arial"; doc.styles["Normal"].font.size = Pt(10)
    t = doc.add_paragraph(); t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = t.add_run(f"Security Assessment Report\n{a['system_name']}")
    r.bold = True; r.font.size = Pt(16); color_run(r, brand_color)
    doc.add_paragraph(f"Version 0.1 (DRAFT)   ·   {_dt.date.today():%d %b %Y}")
    prepared_block(doc, prepared_by, prepared_for)

    doc.add_paragraph("1. Executive Summary", style="Heading 1")
    doc.add_paragraph(f"{a['assessed']} controls assessed. {a['satisfied']} satisfied; "
                      f"{a['other_than_satisfied']} other-than-satisfied. "
                      f"{len(a['findings'])} finding(s) below feed the POA&M.")

    doc.add_paragraph("2. Findings", style="Heading 1")
    if not a["findings"]:
        doc.add_paragraph("No findings — all assessed controls satisfied.")
    else:
        tbl = doc.add_table(rows=1, cols=4); tbl.style = "Table Grid"
        for i, h in enumerate(("#", "Control", "Source", "Finding")):
            tbl.rows[0].cells[i].text = h
        for n, f in enumerate(a["findings"], 1):
            c = tbl.add_row().cells
            c[0].text = str(n); c[1].text = (f["control_id"] or "").upper()
            c[2].text = f["source"]; c[3].text = f["description"]

    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path

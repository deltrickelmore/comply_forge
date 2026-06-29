"""
GRC platform export adapters — file-based "mesh" with eMASS, Xacta, Archer, MCCAST.

OSCAL (ssp/sar/poam) is the lossless interchange and is emitted elsewhere; this
module produces the *tabular* control-status and test-result exports those
platforms ingest when OSCAL import isn't used. The eMASS columns follow the
documented eMASS REST control fields (implementationStatus, responsibleEntities,
implementationNarrative, SLCM…, and the test-result compliance fields keyed by
CCI). The Xacta/Archer export is a vendor-neutral control-status CSV that maps
cleanly onto either platform's import mapping step.

Honesty: exact column headers vary by platform instance/version. These are
faithful, documented starting points — not a claim of byte-exact proprietary
format fidelity. Nothing here pushes to a live API (that path is credential-gated);
these are downloadable files a human imports.
"""

from __future__ import annotations

import csv
import io

# implemented_requirements.status  ->  eMASS implementationStatus
_EMASS_STATUS = {
    "implemented": "Implemented",
    "partial": "Planned",
    "planned": "Planned",
    "not-applicable": "Not Applicable",
    "na": "Not Applicable",
    "inherited": "Inherited",
}
# eMASS test-result complianceStatus
_EMASS_COMPLIANCE = {
    "implemented": "Compliant",
    "partial": "Non-Compliant",
    "planned": "Non-Compliant",
    "not-applicable": "Not Applicable",
}


def _system(conn, system_id: str) -> dict:
    row = conn.execute("SELECT system_id, name FROM systems WHERE system_id=?",
                       (system_id,)).fetchone()
    if row is None:
        raise ValueError(f"no such system: {system_id}")
    return {"system_id": row["system_id"], "name": row["name"]}


def control_status_rows(conn, system_id: str, catalog_version_id: str) -> list[dict]:
    """Canonical per-control status used by every export below."""
    reqs = conn.execute(
        """SELECT control_id, status, statement, needs_review
             FROM implemented_requirements
            WHERE system_id=? AND catalog_version_id=? ORDER BY control_id""",
        (system_id, catalog_version_id)).fetchall()
    out = []
    for r in reqs:
        out.append({
            "control_id": r["control_id"],
            "status": (r["status"] or "planned").lower(),
            "narrative": r["statement"] or "",
            "needs_review": bool(r["needs_review"]),
        })
    return out


def _writer(headers: list[str]):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
    w.writeheader()
    return buf, w


def export_emass_controls_csv(conn, system_id: str, catalog_version_id: str) -> str:
    """eMASS Control Implementation import (one row per control)."""
    sysrow = _system(conn, system_id)
    headers = ["System Name", "Control Acronym", "Implementation Status",
               "Responsible Entities", "Implementation Narrative",
               "SLCM Frequency", "SLCM Method", "SLCM Reporting", "SLCM Tracking",
               "N/A Justification", "Comments"]
    buf, w = _writer(headers)
    for row in control_status_rows(conn, system_id, catalog_version_id):
        status = _EMASS_STATUS.get(row["status"], "Planned")
        comments = ("DRAFT — pending ComplyForge review; do not attest until approved."
                    if row["needs_review"] else "")
        w.writerow({
            "System Name": sysrow["name"],
            "Control Acronym": row["control_id"].upper(),
            "Implementation Status": status,
            "Responsible Entities": "",
            "Implementation Narrative": row["narrative"],
            "N/A Justification": "" if status != "Not Applicable" else "[provide justification]",
            "Comments": comments,
        })
    return buf.getvalue()


def export_emass_test_results_csv(conn, system_id: str, catalog_version_id: str) -> str:
    """eMASS Test Result import (one row per CCI / assessment procedure)."""
    from . import cci as _cci
    sysrow = _system(conn, system_id)
    headers = ["System Name", "Control Acronym", "CCI", "AP Acronym",
               "Compliance Status", "Assessed By", "Tested By", "Test Date",
               "Assessment Procedure"]
    buf, w = _writer(headers)
    for row in control_status_rows(conn, system_id, catalog_version_id):
        cid = row["control_id"]
        compliance = _EMASS_COMPLIANCE.get(row["status"], "Non-Compliant")
        ccis = _cci.controls_ccis(conn, cid) or [{}]
        for cc in ccis:
            w.writerow({
                "System Name": sysrow["name"],
                "Control Acronym": cid.upper(),
                "CCI": cc.get("cci", ""),
                "AP Acronym": cc.get("ap_acronym", ""),
                "Compliance Status": compliance,
                "Assessed By": "", "Tested By": "", "Test Date": "",
                "Assessment Procedure": (cc.get("definition", "") or "")[:1000],
            })
    return buf.getvalue()


def export_grc_csv(conn, system_id: str, catalog_version_id: str,
                   platform: str = "Xacta") -> str:
    """Vendor-neutral control-status CSV for Xacta / Archer / MCCAST import mapping."""
    sysrow = _system(conn, system_id)
    headers = ["System", "Control ID", "Control Status", "Implementation Statement",
               "Assessment Objectives", "Open Findings", "Review State", "Platform Hint"]
    # objective + finding counts (best-effort; authoritative where available)
    objc, findc = {}, {}
    try:
        from . import assessment
        for row in control_status_rows(conn, system_id, catalog_version_id):
            objc[row["control_id"]] = len(
                assessment.objectives(conn, row["control_id"], catalog_version_id))
    except Exception:
        pass
    try:
        from . import sar
        for f in sar.assess(conn, system_id, catalog_version_id)["findings"]:
            cid = (f.get("control_id") or "").lower()
            findc[cid] = findc.get(cid, 0) + 1
    except Exception:
        pass

    buf, w = _writer(headers)
    for row in control_status_rows(conn, system_id, catalog_version_id):
        cid = row["control_id"]
        w.writerow({
            "System": sysrow["name"],
            "Control ID": cid.upper(),
            "Control Status": row["status"].title(),
            "Implementation Statement": row["narrative"],
            "Assessment Objectives": objc.get(cid, ""),
            "Open Findings": findc.get(cid, 0),
            "Review State": "Needs Review" if row["needs_review"] else "Reviewed",
            "Platform Hint": platform,
        })
    return buf.getvalue()


EXPORTS = {
    "eMASS — Control Implementation (.csv)": ("emass_controls", export_emass_controls_csv),
    "eMASS — Test Results by CCI (.csv)": ("emass_test_results", export_emass_test_results_csv),
    "Xacta / Archer / MCCAST — Control Status (.csv)": ("grc_control_status", export_grc_csv),
}

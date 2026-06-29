"""
NIST SP 800-53A assessment objectives & methods.

The authoritative 800-53A content ships *inside* the 800-53 Rev 5 OSCAL catalog
as nested `assessment-objective` parts plus `assessment-method` parts (EXAMINE /
INTERVIEW / TEST) per control. This module extracts that content from the OSCAL
we already store per control (controls.oscal_json), resolves the
`{{ insert: param, X }}` placeholders against the control's parameters, and
returns clean structures the SAP / test-plan generators and UI can consume.

No fabrication: every objective and method comes straight from NIST's catalog.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

_PARAM_RE = re.compile(r"\{\{\s*insert:\s*param,\s*([A-Za-z0-9_.\-]+)\s*\}\}")
_METHOD_LABEL = {"EXAMINE": "Examine", "INTERVIEW": "Interview", "TEST": "Test"}


@dataclass
class Objective:
    id: str
    prose: str
    depth: int


@dataclass
class Method:
    method: str          # EXAMINE | INTERVIEW | TEST
    objects: list = field(default_factory=list)


def _param_map(oscal: dict) -> dict:
    """control param id -> human label (falls back to the 800-53A label)."""
    out = {}
    for p in oscal.get("params", []) or []:
        pid = p.get("id")
        if not pid:
            continue
        human = p.get("label")
        a_label = next((pr.get("value") for pr in p.get("props", []) or []
                        if pr.get("name") == "label"), None)
        out[pid] = human or a_label or pid
    return out


def _resolve(prose: str, params: dict) -> str:
    return _PARAM_RE.sub(
        lambda m: f"[{params.get(m.group(1), m.group(1))}]", prose or "").strip()


def _load_oscal(conn, control_id: str, catalog_version_id: str | None) -> dict | None:
    cid = control_id.lower()
    if catalog_version_id:
        row = conn.execute(
            "SELECT oscal_json FROM controls WHERE catalog_version_id=? AND control_id=?",
            (catalog_version_id, cid)).fetchone()
    else:
        row = conn.execute(
            "SELECT oscal_json FROM controls WHERE control_id=? "
            "AND catalog_version_id LIKE 'nist_800_53@%' LIMIT 1", (cid,)).fetchone()
    return json.loads(row[0]) if row and row[0] else None


def objectives(conn, control_id: str, catalog_version_id: str | None = None) -> list[Objective]:
    """Flattened 'determine that' objectives (depth-ordered) for a control."""
    oscal = _load_oscal(conn, control_id, catalog_version_id)
    if not oscal:
        return []
    params = _param_map(oscal)
    out: list[Objective] = []

    def walk(parts, depth):
        for p in parts or []:
            if p.get("name") == "assessment-objective":
                prose = _resolve(p.get("prose", ""), params)
                if prose:
                    out.append(Objective(id=p.get("id", ""), prose=prose, depth=depth))
                walk(p.get("parts"), depth + 1)
            else:
                walk(p.get("parts"), depth)

    walk(oscal.get("parts"), 0)
    return out


def methods(conn, control_id: str, catalog_version_id: str | None = None) -> list[Method]:
    """Assessment methods (EXAMINE/INTERVIEW/TEST) with their assessment objects."""
    oscal = _load_oscal(conn, control_id, catalog_version_id)
    if not oscal:
        return []
    found: dict[str, Method] = {}

    def walk(parts):
        for p in parts or []:
            if p.get("name") == "assessment-method":
                m = next((pr.get("value") for pr in p.get("props", []) or []
                          if pr.get("name") == "method"), "")
                if m:
                    meth = found.setdefault(m, Method(method=m))
                    for sub in p.get("parts", []) or []:
                        if sub.get("name") == "assessment-objects" and sub.get("prose"):
                            meth.objects.append(sub["prose"].strip())
            walk(p.get("parts"))

    walk(oscal.get("parts"))
    return [found[k] for k in ("EXAMINE", "INTERVIEW", "TEST") if k in found]


def summary(conn, control_id: str, catalog_version_id: str | None = None) -> dict:
    objs = objectives(conn, control_id, catalog_version_id)
    meths = methods(conn, control_id, catalog_version_id)
    return {
        "control_id": control_id.lower(),
        "objective_count": len(objs),
        "objectives": objs,
        "methods": meths,
        "method_labels": [_METHOD_LABEL.get(m.method, m.method) for m in meths],
    }

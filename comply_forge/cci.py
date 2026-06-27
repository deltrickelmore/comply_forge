"""
DISA CCI (Control Correlation Identifier) ingester.

Parses the DISA U_CCI_List.xml (the DoD source mapping CCIs -> NIST 800-53
controls + assessment objectives) and loads it so control-family plans can fill
their per-control CCI assessment tables (AP Acronym | CCI | CCI Definition | Response).

Each <cci_item> has an id, a definition, and one or more <reference> entries.
We map each CCI to a NIST 800-53 control via the highest-version 800-53 reference
(Rev 5 > Rev 4 > Rev 3), and capture the 800-53A reference index as the AP acronym.
Reference indexes like "AC-2 (1) a" are normalized to OSCAL control ids ("ac-2.1").
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

_CTRL_RE = re.compile(r"^([A-Za-z]{2})-(\d+)(?:\s*\((\d+)\))?")


def _local(tag: str) -> str:
    return tag.split("}")[-1]


def _control_id(index_text: str) -> str | None:
    """'AC-2 (1) a' -> 'ac-2.1'; 'AC-1 a 1' -> 'ac-1'."""
    m = _CTRL_RE.match(index_text.strip())
    if not m:
        return None
    fam, num, enh = m.group(1).lower(), m.group(2), m.group(3)
    return f"{fam}-{num}.{enh}" if enh else f"{fam}-{num}"


def _best_refs(refs: list[ET.Element]) -> tuple[str | None, str | None, str | None]:
    """Return (control_id, nist_version, ap_acronym) from a CCI's references."""
    best_ctrl = best_ver = best_idx = ap = None
    best_rank = -1
    for r in refs:
        title = (r.get("title") or "")
        ver = (r.get("version") or "")
        idx = (r.get("index") or "")
        if "800-53A" in title:
            ap = idx or ap
            continue
        if "800-53" in title:
            try:
                rank = int(ver)
            except ValueError:
                rank = 0
            if rank > best_rank:
                best_rank, best_ver, best_idx = rank, ver, idx
                best_ctrl = _control_id(idx)
    return best_ctrl, best_ver, ap or best_idx


def load_cci_xml(conn, path: str | Path) -> dict[str, int]:
    """Load U_CCI_List.xml into cci_items + cci_control_map. Idempotent."""
    root = ET.fromstring(Path(path).read_bytes())
    items = [e for e in root.iter() if _local(e.tag) == "cci_item"]

    conn.execute("DELETE FROM cci_control_map")
    conn.execute("DELETE FROM cci_items")

    n_items = n_maps = 0
    for it in items:
        cci_id = it.get("id")
        if not cci_id:
            continue
        defn = status = typ = ""
        refs: list[ET.Element] = []
        for ch in it:
            tag = _local(ch.tag)
            if tag == "definition":
                defn = (ch.text or "").strip()
            elif tag == "status":
                status = (ch.text or "").strip()
            elif tag == "type":
                typ = (ch.text or "").strip()
            elif tag == "references":
                refs = list(ch)
        ctrl, ver, ap = _best_refs(refs)
        conn.execute(
            "INSERT OR REPLACE INTO cci_items (cci_id, definition, status, type, ap_acronym) "
            "VALUES (?,?,?,?,?)", (cci_id, defn, status, typ, ap))
        n_items += 1
        if ctrl:
            conn.execute(
                "INSERT OR REPLACE INTO cci_control_map (cci_id, control_id, nist_version, index_text) "
                "VALUES (?,?,?,?)", (cci_id, ctrl, ver, ap))
            n_maps += 1
    conn.commit()
    return {"cci_items": n_items, "mappings": n_maps}


def controls_ccis(conn, control_id: str) -> list[dict]:
    """CCI rows for a control: [{cci, definition, ap_acronym}, ...]."""
    rows = conn.execute(
        """SELECT m.cci_id, i.definition, i.ap_acronym
             FROM cci_control_map m JOIN cci_items i ON i.cci_id=m.cci_id
            WHERE m.control_id=? ORDER BY m.cci_id""",
        (control_id.lower(),)).fetchall()
    return [{"cci": r[0], "definition": r[1] or "", "ap_acronym": r[2] or ""} for r in rows]

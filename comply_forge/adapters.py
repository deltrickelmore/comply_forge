"""
Adapters for frameworks that do NOT ship as OSCAL catalogs.

FISCAM 2024
-----------
GAO's Federal Information System Controls Audit Manual (2024 update) is not
published as OSCAL. It is control-shaped, though: control categories/areas ->
control activities -> audit techniques, and GAO provides a mapping to NIST
800-53. We ingest it from a normalized CSV into the SAME internal `controls`
shape, then load GAO's crosswalk into `control_mappings`.

CMMC
----
CMMC assesses against NIST SP 800-171. We load 800-171 as a normal OSCAL catalog
and represent CMMC *levels* as profiles (which 800-171 requirements are in scope
at L1 / L2 / L3) via a simple membership CSV.

CSV formats (headers required):
  controls CSV  : control_id,family,title,statement,guidance
  mappings CSV  : src_framework,src_control,dst_framework,dst_control,relation,authority,note
  cmmc CSV      : level,control_id          (control_id = an 800-171 id, e.g. 3.1.1)
"""

from __future__ import annotations

import csv
from pathlib import Path

from .catalog_loader import load_controls, upsert_framework
from .models import Control, Mapping


# --------------------------------------------------------------------------- #
# Generic control-CSV loader (used by FISCAM and any future non-OSCAL source)
# --------------------------------------------------------------------------- #
def load_controls_csv(
    conn,
    csv_path: str | Path,
    *,
    framework_id: str,
    framework_name: str,
    authority: str,
    version_label: str,
    make_current: bool = True,
) -> str:
    rows = list(csv.DictReader(Path(csv_path).read_text().splitlines()))
    controls = [
        Control(
            control_id=(r["control_id"] or "").strip().lower(),
            family=(r.get("family") or r["control_id"].split("-")[0]).strip().upper(),
            title=(r.get("title") or "").strip(),
            statement=(r.get("statement") or "").strip(),
            guidance=(r.get("guidance") or "").strip(),
            oscal={"id": r["control_id"], "title": r.get("title", ""),
                   "_source": framework_id},  # synthesize a minimal OSCAL-ish object
        )
        for r in rows if r.get("control_id")
    ]
    return load_controls(
        conn,
        framework_id=framework_id, framework_name=framework_name,
        authority=authority, version_label=version_label,
        controls=controls, source_uri=str(csv_path), make_current=make_current,
    )


def load_fiscam_csv(conn, csv_path: str | Path, version_label: str = "2024") -> str:
    """Load FISCAM (default 2024 edition) from a normalized control CSV."""
    return load_controls_csv(
        conn, csv_path,
        framework_id="fiscam", framework_name="GAO FISCAM",
        authority="GAO", version_label=version_label, make_current=True,
    )


# --------------------------------------------------------------------------- #
# Crosswalk / mapping loader
# --------------------------------------------------------------------------- #
def load_mappings_csv(conn, csv_path: str | Path) -> int:
    rows = list(csv.DictReader(Path(csv_path).read_text().splitlines()))
    mappings = [
        Mapping(
            src_framework=r["src_framework"].strip(),
            src_control=r["src_control"].strip().lower(),
            dst_framework=r["dst_framework"].strip(),
            dst_control=r["dst_control"].strip().lower(),
            relation=r["relation"].strip(),
            authority=(r.get("authority") or "manual").strip(),
            note=(r.get("note") or "").strip(),
        )
        for r in rows if r.get("src_control") and r.get("dst_control")
    ]
    conn.executemany(
        """INSERT OR REPLACE INTO control_mappings
             (src_framework, src_version, src_control, dst_framework, dst_version,
              dst_control, relation, authority, note)
           VALUES (:src_framework, :src_version, :src_control, :dst_framework,
                   :dst_version, :dst_control, :relation, :authority, :note)""",
        [m.as_row() for m in mappings],
    )
    conn.commit()
    return len(mappings)


# --------------------------------------------------------------------------- #
# CMMC level membership (profile over 800-171)
# --------------------------------------------------------------------------- #
def load_cmmc_levels_csv(conn, csv_path: str | Path) -> int:
    """
    Register CMMC as a framework and load level membership as mappings of the form
    cmmc 'L<level>' -> 800-171 control (relation 'equivalent', authority 'DoD CIO').
    A CMMC profile for level N is then: SELECT dst_control WHERE src_control='l<n>'.
    """
    upsert_framework(conn, "cmmc", "CMMC", "DoD CIO", notes="Assessed against NIST SP 800-171")
    rows = list(csv.DictReader(Path(csv_path).read_text().splitlines()))
    payload = [
        {
            "src_framework": "cmmc", "src_version": None,
            "src_control": f"l{r['level'].strip()}",
            "dst_framework": "nist_800_171", "dst_version": None,
            "dst_control": r["control_id"].strip().lower(),
            "relation": "equivalent", "authority": "DoD CIO", "note": "CMMC level membership",
        }
        for r in rows if r.get("level") and r.get("control_id")
    ]
    conn.executemany(
        """INSERT OR REPLACE INTO control_mappings
             (src_framework, src_version, src_control, dst_framework, dst_version,
              dst_control, relation, authority, note)
           VALUES (:src_framework, :src_version, :src_control, :dst_framework,
                   :dst_version, :dst_control, :relation, :authority, :note)""",
        payload,
    )
    conn.commit()
    return len(payload)


def load_generic_oscal(conn, path, *, framework_id: str, name: str, authority: str,
                       version_label: str, id_mode: str = "rawid",
                       family_mode: str = "prefix", make_current: bool = True) -> dict:
    """Load an OSCAL catalog with no embedded 800-53 mapping (browsable framework).
    id_mode: 'rawid' (e.g. po.1) or 'sortid' (e.g. 03.01.01E).
    family_mode: 'prefix' (chars before '.') or 'dotnum' (3.N from 03.NN.*)."""
    import json
    from .catalog_loader import _iter_controls, _flatten_prose, load_controls
    from .models import Control

    def sort_id(c):
        return next((p["value"] for p in c.get("props", []) or []
                     if p.get("name") == "sort-id"), "")

    doc = json.loads(Path(path).read_text())
    cat = doc.get("catalog", doc)
    controls = []
    for raw in _iter_controls(cat):
        cid = (sort_id(raw) if id_mode == "sortid" else (raw.get("id") or "")).lower()
        if not cid:
            continue
        if family_mode == "dotnum":
            segs = cid.split(".")
            fam = f"3.{int(segs[1])}" if len(segs) >= 2 and segs[0] in ("03", "3") else cid
        else:
            fam = cid.split(".")[0].upper()
        stmt = (_flatten_prose(raw.get("parts", []), "statement")
                or _flatten_prose(raw.get("parts", []), "*"))
        title = raw.get("title", "") or (stmt[:120] or cid.upper())
        controls.append(Control(control_id=cid, family=fam, title=title,
                                statement=stmt, guidance="", oscal=raw))
    cv = load_controls(conn, framework_id=framework_id, framework_name=name,
                       authority=authority, version_label=version_label,
                       controls=controls, source_uri=str(path), make_current=make_current)
    return {"controls": len(controls), "catalog_version": cv}


def load_csf_oscal(conn, path, version_label: str = "2.0", make_current: bool = True) -> dict:
    """Load the NIST CSF 2.0 OSCAL catalog (functions/categories/subcategories).
    CSF carries no embedded 800-53 mapping (NIST publishes those as separate OLIR
    informative references) — load a crosswalk CSV to link it to 800-53."""
    import json
    from .catalog_loader import _iter_controls, _flatten_prose, load_controls
    from .models import Control

    doc = json.loads(Path(path).read_text())
    cat = doc.get("catalog", doc)
    controls = []
    for raw in _iter_controls(cat):
        cid = (raw.get("id") or "").lower()          # e.g. 'gv.oc-01'
        if not cid:
            continue
        family = cid.split(".")[0].upper()           # function: GV/ID/PR/DE/RS/RC
        stmt = (_flatten_prose(raw.get("parts", []), "statement")
                or _flatten_prose(raw.get("parts", []), "*"))
        title = raw.get("title", "")
        if not title or title.lower() == cid:        # CSF uses the id as title; use the text
            title = (stmt[:120] + ("…" if len(stmt) > 120 else "")) or cid.upper()
        controls.append(Control(control_id=cid, family=family, title=title,
                                statement=stmt, guidance="", oscal=raw))
    cv = load_controls(conn, framework_id="nist_csf", framework_name="NIST Cybersecurity Framework",
                       authority="NIST", version_label=version_label, controls=controls,
                       source_uri=str(path), make_current=make_current)
    return {"controls": len(controls), "catalog_version": cv}


def load_800171_oscal(conn, path, version_label: str = "Rev 3",
                      make_current: bool = True) -> dict:
    """Load the NIST 800-171 Rev 3 OSCAL catalog AND derive the authoritative
    800-171 -> 800-53 crosswalk from each control's back-matter references."""
    import json
    import re
    from .catalog_loader import _iter_controls, _flatten_prose, load_controls
    from .models import Control, Mapping

    re53 = re.compile(r"^([A-Za-z]{2})-(\d{1,2})(?:\((\d{1,2})\))?$")

    def norm53(title: str) -> str | None:
        m = re53.match((title or "").strip())
        if not m:
            return None
        fam, num, enh = m.group(1).lower(), str(int(m.group(2))), m.group(3)
        return f"{fam}-{num}.{int(enh)}" if enh else f"{fam}-{num}"

    def sort_id(ctrl) -> str:
        for p in ctrl.get("props", []) or []:
            if p.get("name") == "sort-id":
                return p.get("value", "")
        return (ctrl.get("id") or "").replace("SP_800_171_", "")

    doc = json.loads(Path(path).read_text())
    cat = doc.get("catalog", doc)
    bm = {r["uuid"]: (r.get("title") or "")
          for r in cat.get("back-matter", {}).get("resources", []) or []}

    controls, mappings = [], []
    for raw in _iter_controls(cat):
        cid = sort_id(raw)
        if not cid:
            continue
        segs = cid.split(".")
        family = f"3.{int(segs[1])}" if len(segs) >= 2 and segs[0] in ("03", "3") else cid
        controls.append(Control(
            control_id=cid, family=family, title=raw.get("title", ""),
            statement=_flatten_prose(raw.get("parts", []), "statement")
                      or _flatten_prose(raw.get("parts", []), "item"),
            guidance=_flatten_prose(raw.get("parts", []), "guidance"), oscal=raw))
        for ln in raw.get("links", []) or []:
            if ln.get("rel") == "reference":
                dst = norm53(bm.get((ln.get("href") or "").lstrip("#"), ""))
                if dst:
                    mappings.append(Mapping(
                        src_framework="nist_800_171", src_control=cid,
                        dst_framework="nist_800_53", dst_control=dst,
                        relation="intersects", authority="NIST SP 800-171 Rev 3",
                        note="derived from 800-171 r3 catalog back-matter"))

    cv = load_controls(conn, framework_id="nist_800_171", framework_name="NIST SP 800-171",
                       authority="NIST", version_label=version_label, controls=controls,
                       source_uri=str(path), make_current=make_current)
    seen = set()
    rows = []
    for m in mappings:
        k = (m.src_control, m.dst_control)
        if k not in seen:
            seen.add(k); rows.append(m.as_row())
    if rows:
        conn.executemany(
            """INSERT OR REPLACE INTO control_mappings
                 (src_framework, src_version, src_control, dst_framework, dst_version,
                  dst_control, relation, authority, note)
               VALUES (:src_framework, :src_version, :src_control, :dst_framework,
                       :dst_version, :dst_control, :relation, :authority, :note)""", rows)
        conn.commit()
    return {"controls": len(controls), "mappings": len(rows), "catalog_version": cv}


# CMMC Level 1 (foundational) = these NIST SP 800-171 requirements (FAR 52.204-21).
_CMMC_L1_REV3 = ["03.01.01", "03.01.02", "03.01.20", "03.01.22", "03.05.01", "03.05.02",
                 "03.08.03", "03.10.01", "03.10.03", "03.10.04", "03.10.05", "03.13.01",
                 "03.13.05", "03.14.01", "03.14.02", "03.14.04", "03.14.05"]


def seed_cmmc_from_800171(conn) -> dict:
    """Seed CMMC levels over the loaded 800-171 catalog. L2 = all 800-171 reqs;
    L1 = the foundational subset (those that exist in the loaded catalog)."""
    upsert_framework(conn, "cmmc", "CMMC", "DoD CIO", kind="maturity_model",
                     notes="Assessed against NIST SP 800-171")
    cv = conn.execute("SELECT catalog_version_id FROM catalog_versions "
                      "WHERE framework_id='nist_800_171' AND is_current=1").fetchone()
    if not cv:
        return {"l1": 0, "l2": 0, "note": "load 800-171 first"}
    all_ids = [r[0] for r in conn.execute(
        "SELECT control_id FROM controls WHERE catalog_version_id=? ORDER BY control_id", (cv[0],))]
    l1 = [c for c in _CMMC_L1_REV3 if c in set(all_ids)]
    rows = ([{"level": "1", "control_id": c} for c in l1]
            + [{"level": "2", "control_id": c} for c in all_ids])
    conn.executemany(
        """INSERT OR REPLACE INTO control_mappings
             (src_framework, src_version, src_control, dst_framework, dst_version,
              dst_control, relation, authority, note)
           VALUES ('cmmc', NULL, :src, 'nist_800_171', NULL, :dst, 'equivalent',
                   'DoD CIO', 'CMMC level membership')""",
        [{"src": f"l{r['level']}", "dst": r["control_id"]} for r in rows])
    conn.commit()
    return {"l1": len(l1), "l2": len(all_ids)}


def cmmc_profile(conn, level: int) -> list[str]:
    """Return the 800-171 control_ids in scope for a CMMC level."""
    rows = conn.execute(
        """SELECT dst_control FROM control_mappings
            WHERE src_framework='cmmc' AND src_control=? ORDER BY dst_control""",
        (f"l{level}",),
    ).fetchall()
    return [r[0] for r in rows]

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


def cmmc_profile(conn, level: int) -> list[str]:
    """Return the 800-171 control_ids in scope for a CMMC level."""
    rows = conn.execute(
        """SELECT dst_control FROM control_mappings
            WHERE src_framework='cmmc' AND src_control=? ORDER BY dst_control""",
        (f"l{level}",),
    ).fetchall()
    return [r[0] for r in rows]

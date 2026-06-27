"""
Baseline loaders for the Categorize -> Select step.

Two sources:
  * NIST 800-53B baselines (Low/Moderate/High) ship as OSCAL *profiles* -- load
    the resolved control-id list via load_baseline_profile_oscal().
  * CNSSI 1253 per-CIA assignments (DoD/NSS) are not standard OSCAL; load from a
    CSV via load_cnssi1253_csv(). cnssi CSV cols: dimension,impact,control_id
    where dimension in {C,I,A}, impact in {low,moderate,high}.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable


def _upsert_baseline(conn, baseline_id, framework_id, label, dimension, impact,
                     authority, source, control_ids: Iterable[str]) -> str:
    conn.execute(
        """INSERT INTO baselines
             (baseline_id, framework_id, label, dimension, impact, authority, source)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(baseline_id) DO UPDATE SET
             label=excluded.label, dimension=excluded.dimension, impact=excluded.impact,
             authority=excluded.authority, source=excluded.source""",
        (baseline_id, framework_id, label, dimension, impact, authority, source),
    )
    conn.execute("DELETE FROM baseline_controls WHERE baseline_id=?", (baseline_id,))
    conn.executemany(
        "INSERT OR IGNORE INTO baseline_controls (baseline_id, control_id) VALUES (?,?)",
        [(baseline_id, c.lower()) for c in control_ids],
    )
    conn.commit()
    return baseline_id


def _iter_profile_ids(profile: dict) -> Iterable[str]:
    """Pull control ids from an OSCAL profile's import/include-controls/with-ids."""
    for imp in profile.get("imports", []) or []:
        inc = imp.get("include-controls", []) or []
        for sel in inc:
            for cid in sel.get("with-ids", []) or []:
                yield cid


def load_baseline_profile_oscal(
    conn, path: str | Path, *, baseline_id: str, framework_id: str,
    label: str, impact: str, source: str | None = None,
) -> str:
    """Load a NIST 800-53B baseline (OSCAL profile) as an 'overall' baseline."""
    path = Path(path)
    doc = json.loads(path.read_text())
    profile = doc.get("profile", doc)
    ids = list(_iter_profile_ids(profile))
    return _upsert_baseline(
        conn, baseline_id, framework_id, label, "overall", impact,
        "NIST 800-53B", source or str(path), ids)


def load_cnssi1253_rows(conn, rows, framework_id="nist_800_53", source="upload") -> int:
    """Load authoritative CNSSI 1253 per-CIA baselines from rows with keys
    dimension (C/I/A), impact (low/moderate/high), control_id. Overrides any
    derived (approximated) baselines. Returns rows loaded."""
    _IMP = {"l": "low", "low": "low", "m": "moderate", "mod": "moderate",
            "moderate": "moderate", "h": "high", "high": "high"}
    groups: dict[tuple[str, str], list[str]] = {}
    n = 0
    for r in rows:
        dim = str(r.get("dimension", "")).strip().upper()[:1]
        imp = _IMP.get(str(r.get("impact", "")).strip().lower())
        cid = str(r.get("control_id", "")).strip().lower()
        if dim in ("C", "I", "A") and imp and cid:
            groups.setdefault((dim, imp), []).append(cid)
            n += 1
    for (dim, imp), ids in groups.items():
        _upsert_baseline(conn, f"cnssi_1253@{dim}-{imp}", framework_id,
                         f"{dim} {imp.title()}", dim, imp,
                         "CNSSI 1253 (authoritative, uploaded)", source, ids)
    return n


def load_cnssi1253_csv(conn, csv_path: str | Path, framework_id="nist_800_53") -> int:
    """Load CNSSI 1253 per-CIA baselines from a CSV (dimension,impact,control_id)."""
    rows = list(csv.DictReader(Path(csv_path).read_text().splitlines()))
    return load_cnssi1253_rows(conn, rows, framework_id, source=str(csv_path))


def baseline_control_ids(conn, baseline_id: str) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT control_id FROM baseline_controls WHERE baseline_id=? ORDER BY control_id",
        (baseline_id,))]


def derive_cnssi_1253_from_800_53b(conn, framework_id: str = "nist_800_53") -> int:
    """Create per-CIA (CNSSI 1253) baselines from the loaded 800-53B Low/Mod/High sets.

    APPROXIMATION: real CNSSI 1253 tags each control by security objective (C/I/A) in
    its Annex tables, which are not a clean public dataset. Here each objective's set
    at impact level X = the 800-53B baseline at level X. The per-CIA UNION is therefore
    exact for the overall control set; objective-level precision needs authoritative
    CNSSI 1253 data loaded via load_cnssi1253_csv() (which overrides these)."""
    made = 0
    note = ("Derived from NIST 800-53B (approximation; load authoritative CNSSI 1253 "
            "tables for objective-level precision)")
    for impact in ("low", "moderate", "high"):
        ids = baseline_control_ids(conn, f"nist_800_53b@{impact}")
        if not ids:
            continue
        for dim in ("C", "I", "A"):
            _upsert_baseline(conn, f"cnssi_1253@{dim}-{impact}", framework_id,
                             f"{dim} {impact.title()}", dim, impact, note,
                             "derived:nist_800_53b", ids)
            made += 1
    return made

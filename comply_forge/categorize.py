"""
FIPS 199 categorization + control selection -- the "generate controls from the
CIA triad" button (the RMF Categorize -> Select step, like rmfks.osd.mil).

Two selection models:
  * high_water_mark (FIPS 200 / NIST 800-53B): overall impact = max(C, I, A);
    select the single Low/Moderate/High baseline. Federal-civilian default.
  * per_cia (CNSSI 1253): C, I, A each get their own baseline independently; the
    selected set is the UNION across the three dimensions. DoD/NSS default -- this
    is why rmfks lets you pick C/I/A separately.

Returns the in-scope control_ids; the caller can then create answers / an SSP.
The output is a recommendation for human confirmation, never an auto-attestation.
"""

from __future__ import annotations

from dataclasses import dataclass

from .baselines import baseline_control_ids

_LEVELS = {"low": 1, "moderate": 2, "high": 3}


@dataclass
class Categorization:
    confidentiality: str   # low|moderate|high
    integrity: str
    availability: str

    def __post_init__(self):
        for v in (self.confidentiality, self.integrity, self.availability):
            if v.lower() not in _LEVELS:
                raise ValueError(f"impact must be low|moderate|high, got {v!r}")

    @property
    def overall(self) -> str:
        """FIPS 199 high-water mark across C, I, A."""
        return max((self.confidentiality, self.integrity, self.availability),
                   key=lambda v: _LEVELS[v.lower()]).lower()


def select_high_water_mark(conn, cat: Categorization,
                           framework_id: str = "nist_800_53") -> dict:
    """FIPS 200 / 800-53B: one baseline at the high-water-mark impact level."""
    overall = cat.overall
    bid = f"nist_800_53b@{overall}"
    controls = baseline_control_ids(conn, bid)
    if not controls:
        raise ValueError(f"baseline {bid} not loaded -- run baselines.load_baseline_profile_oscal")
    return {
        "model": "high_water_mark",
        "categorization": vars(cat),
        "overall_impact": overall,
        "baseline_id": bid,
        "control_ids": controls,
        "control_count": len(controls),
        "note": "FIPS 200 / NIST 800-53B high-water-mark baseline. Human review required.",
    }


def select_per_cia(conn, cat: Categorization,
                   framework_id: str = "nist_800_53") -> dict:
    """CNSSI 1253: per-CIA baselines, union of the three. DoD/NSS model."""
    dims = {"C": cat.confidentiality.lower(),
            "I": cat.integrity.lower(),
            "A": cat.availability.lower()}
    per_dim = {}
    union: set[str] = set()
    for dim, impact in dims.items():
        bid = f"cnssi_1253@{dim}-{impact}"
        ids = baseline_control_ids(conn, bid)
        if not ids:
            raise ValueError(f"baseline {bid} not loaded -- run baselines.load_cnssi1253_csv")
        per_dim[dim] = {"impact": impact, "baseline_id": bid, "control_count": len(ids)}
        union.update(ids)
    return {
        "model": "per_cia (CNSSI 1253)",
        "categorization": vars(cat),
        "per_dimension": per_dim,
        "control_ids": sorted(union),
        "control_count": len(union),
        "note": "CNSSI 1253 per-CIA union (DoD/NSS); per-objective sets approximated from "
                "800-53B unless authoritative CNSSI 1253 data is loaded. Apply overlays; human review required.",
    }


def categorize_and_select(conn, *, confidentiality: str, integrity: str,
                          availability: str, model: str = "high_water_mark",
                          framework_id: str = "nist_800_53") -> dict:
    """One-call entry point for the CIA button. model = high_water_mark | per_cia."""
    cat = Categorization(confidentiality, integrity, availability)
    if model == "per_cia":
        return select_per_cia(conn, cat, framework_id)
    return select_high_water_mark(conn, cat, framework_id)

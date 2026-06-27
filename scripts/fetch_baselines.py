"""
Fetch NIST SP 800-53B Low/Moderate/High baselines (OSCAL profiles) and load them
so the CIA-triad button and control-family plans scope to the controls actually
selected at the system's impact level.

Run (needs network):  python scripts/fetch_baselines.py
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from comply_forge.db import connect
from comply_forge import baselines

BASE = ("https://raw.githubusercontent.com/usnistgov/oscal-content/main/"
        "nist.gov/SP800-53/rev5/json/")
PROFILES = {
    "low": "NIST_SP-800-53_rev5_LOW-baseline_profile.json",
    "moderate": "NIST_SP-800-53_rev5_MODERATE-baseline_profile.json",
    "high": "NIST_SP-800-53_rev5_HIGH-baseline_profile.json",
}
DEST = ROOT / "data" / "baselines"


def main() -> None:
    conn = connect()
    DEST.mkdir(parents=True, exist_ok=True)
    for impact, fname in PROFILES.items():
        path = DEST / fname
        if not path.exists():
            print(f"downloading {fname}")
            path.write_bytes(urllib.request.urlopen(BASE + fname, timeout=60).read())  # noqa: S310
        bid = f"nist_800_53b@{impact}"
        baselines.load_baseline_profile_oscal(
            conn, path, baseline_id=bid, framework_id="nist_800_53",
            label=impact.title(), impact=impact)
        n = len(baselines.baseline_control_ids(conn, bid))
        print(f"  loaded {bid}: {n} controls")


if __name__ == "__main__":
    main()

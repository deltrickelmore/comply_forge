"""
Fetch real OSCAL catalogs from NIST's public repo (usnistgov/oscal-content) and
load them into ComplyForge. Stdlib urllib only -- no deps.

These are the genuine, full catalogs (800-53 Rev 5 has 1000+ controls incl.
enhancements). Run when you have network access:

    python scripts/fetch_catalogs.py           # downloads + loads 800-53 Rev5

Note: NIST does not publish 800-171 or FISCAM as OSCAL. Load those via the CSV
adapters (see README). CMMC = 800-171 + level membership CSV.
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from comply_forge.db import connect
from comply_forge import catalog_loader

DEST = ROOT / "data" / "catalogs"

# Public raw URLs (NIST OSCAL content, main branch).
SOURCES = {
    "nist_800_53_rev5": {
        "url": "https://raw.githubusercontent.com/usnistgov/oscal-content/main/"
               "nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json",
        "framework_id": "nist_800_53",
        "name": "NIST SP 800-53",
        "authority": "NIST",
        "version": "Rev 5",
    },
}


def fetch(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  downloading {url}")
    with urllib.request.urlopen(url, timeout=60) as r:  # noqa: S310 (trusted host)
        dest.write_bytes(r.read())
    print(f"  -> {dest}  ({dest.stat().st_size // 1024} KB)")
    return dest


def main() -> None:
    conn = connect()
    for key, s in SOURCES.items():
        print(f"== {key} ==")
        path = fetch(s["url"], DEST / f"{key}.json")
        cv = catalog_loader.load_oscal_catalog_file(
            conn, path,
            framework_id=s["framework_id"], framework_name=s["name"],
            authority=s["authority"], version_label=s["version"],
            make_current=True)
        n = conn.execute("SELECT COUNT(*) FROM controls WHERE catalog_version_id=?",
                         (cv,)).fetchone()[0]
        print(f"  loaded {n} controls into {cv} [current]\n")


if __name__ == "__main__":
    main()

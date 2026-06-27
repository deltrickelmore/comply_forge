"""
Fetch NIST SP 800-171 Rev 3 (OSCAL) + derive the authoritative 800-171->800-53
crosswalk, and seed CMMC levels. Run after fetch_catalogs.py.

  python scripts/fetch_800171.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from comply_forge.db import connect
from comply_forge import bootstrap

if __name__ == "__main__":
    print(bootstrap.init_800171(connect()))

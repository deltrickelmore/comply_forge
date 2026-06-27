"""Fetch NIST CSF 2.0 (OSCAL) and load it.  python scripts/fetch_csf.py"""
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from comply_forge.db import connect
from comply_forge import bootstrap
if __name__ == "__main__":
    print(bootstrap.init_csf(connect()))

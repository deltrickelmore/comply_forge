"""
Bulk-ingest STIGs from a folder or a STIG Library Compilation zip.

  python scripts/bulk_ingest_stigs.py /path/to/folder_of_stigs/
  python scripts/bulk_ingest_stigs.py /path/to/U_SRG-STIG_Library_YYYY_MM.zip

Handles: loose .xml XCCDF, individual STIG .zip files, and the compilation zip
(which nests STIG .zip files inside). Recurses into nested zips. Each XCCDF Manual
benchmark found is loaded via stig.load_stig_xccdf.
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from comply_forge.db import connect
from comply_forge import stig


def _ingest_xccdf(conn, data: bytes, source: str, results: list):
    if b"<Benchmark" not in data[:4000] and b"<Benchmark" not in data:
        return
    try:
        res = stig.load_stig_xccdf(conn, data, source=source)
        results.append(res)
        print(f"  ✓ {res['title'][:55]:55} {res['version']:6} "
              f"{res['rules']:4} rules  {res['controls_mapped']:3} ctrls")
    except Exception as e:
        print(f"  ✗ {source[:60]}: {e}")


def _walk_zip(conn, data: bytes, source: str, results: list, depth: int = 0):
    if depth > 3:
        return
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return
    for name in z.namelist():
        low = name.lower()
        if low.endswith(".xml") and ("xccdf" in low or "manual" in low):
            _ingest_xccdf(conn, z.read(name), f"{source}::{name}", results)
        elif low.endswith(".zip"):
            _walk_zip(conn, z.read(name), f"{source}::{name}", results, depth + 1)


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: bulk_ingest_stigs.py <folder | compilation.zip>")
    target = Path(sys.argv[1])
    conn = connect()
    results: list = []

    if target.is_dir():
        for p in sorted(target.rglob("*")):
            if p.suffix.lower() == ".zip":
                _walk_zip(conn, p.read_bytes(), p.name, results)
            elif p.suffix.lower() == ".xml":
                _ingest_xccdf(conn, p.read_bytes(), p.name, results)
    elif target.suffix.lower() == ".zip":
        _walk_zip(conn, target.read_bytes(), target.name, results)
    elif target.suffix.lower() == ".xml":
        _ingest_xccdf(conn, target.read_bytes(), target.name, results)
    else:
        raise SystemExit("provide a folder, a .zip, or an .xml")

    print(f"\nLoaded {len(results)} STIG(s); "
          f"{sum(r['rules'] for r in results)} rules total.")


if __name__ == "__main__":
    main()

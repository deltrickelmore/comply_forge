"""
Fetch/ingest a DISA STIG.

  python scripts/fetch_stig.py <URL-to-STIG.zip>
  python scripts/fetch_stig.py /path/to/STIG.zip
  python scripts/fetch_stig.py /path/to/Manual-xccdf.xml

A STIG .zip contains a Manual XCCDF .xml; this extracts and loads it. The full DISA
STIG Library Compilation is ~1GB and its URL changes quarterly — ingest individual
STIG zips (from https://public.cyber.mil/stigs/downloads/) one at a time, or point
this at a local zip you downloaded.
"""

from __future__ import annotations

import io
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from comply_forge.db import connect
from comply_forge import stig


def _xccdf_from_zip(data: bytes) -> tuple[bytes, str]:
    z = zipfile.ZipFile(io.BytesIO(data))
    xmls = [n for n in z.namelist() if n.lower().endswith(".xml")]
    manual = [n for n in xmls if "manual" in n.lower() and "xccdf" in n.lower()] or \
             [n for n in xmls if "xccdf" in n.lower()] or xmls
    if not manual:
        raise SystemExit("no XCCDF .xml found in zip")
    return z.read(manual[0]), manual[0]


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: fetch_stig.py <URL.zip | path.zip | path.xml>")
    arg = sys.argv[1]
    if arg.startswith("http"):
        print(f"downloading {arg}")
        data = urllib.request.urlopen(
            urllib.request.Request(arg, headers={"User-Agent": "Mozilla/5.0"}), timeout=180).read()
        xml, name = _xccdf_from_zip(data)
        source = arg
    else:
        p = Path(arg)
        if p.suffix.lower() == ".zip":
            xml, name = _xccdf_from_zip(p.read_bytes())
        else:
            xml, name = p.read_bytes(), p.name
        source = str(p)

    conn = connect()
    res = stig.load_stig_xccdf(conn, xml, source=source)
    print(f"Loaded STIG '{res['title']}' ({res['version']})")
    print(f"  stig_id={res['stig_id']}  rules={res['rules']}  "
          f"cci_refs={res['cci_refs']}  controls_mapped={res['controls_mapped']}")


if __name__ == "__main__":
    main()

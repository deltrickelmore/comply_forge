"""
Download the DISA CCI List and load it so control-plan CCI tables populate.

Run (needs network):  python scripts/fetch_cci.py
If your environment can't reach dod.cyber.mil, download U_CCI_List.zip manually,
unzip it, and pass the XML:  python scripts/fetch_cci.py /path/to/U_CCI_List.xml
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
from comply_forge import cci

URL = "https://dl.dod.cyber.mil/wp-content/uploads/stigs/zip/U_CCI_List.zip"
DEST = ROOT / "data" / "cci" / "U_CCI_List.xml"


def main() -> None:
    if len(sys.argv) > 1:                       # local xml path provided
        xml_path = Path(sys.argv[1])
    else:
        DEST.parent.mkdir(parents=True, exist_ok=True)
        if not DEST.exists():
            print(f"downloading {URL}")
            data = urllib.request.urlopen(
                urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"}),
                timeout=120).read()
            z = zipfile.ZipFile(io.BytesIO(data))
            name = next(n for n in z.namelist() if n.lower().endswith(".xml"))
            DEST.write_bytes(z.read(name))
        xml_path = DEST

    conn = connect()
    stats = cci.load_cci_xml(conn, xml_path)
    print(f"Loaded {stats['cci_items']} CCIs; {stats['mappings']} control mappings.")
    sample = cci.controls_ccis(conn, "ca-2")
    print(f"  CA-2 has {len(sample)} CCIs; first: {sample[0] if sample else None}")


if __name__ == "__main__":
    main()

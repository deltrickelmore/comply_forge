"""
Data bootstrap for fresh deployments (e.g. Streamlit Cloud).

A deployed app starts with an empty DB. These functions download and load the
reference data on demand (from the Dashboard's "Initialize data" buttons), so the
deploy is code-only and the data is fetched at runtime. STIGs are ingested
separately via the STIG Library page.
"""

from __future__ import annotations

import io
import urllib.request
import zipfile
from pathlib import Path

from . import catalog_loader, baselines, cci

_UA = {"User-Agent": "Mozilla/5.0"}
_NIST = ("https://raw.githubusercontent.com/usnistgov/oscal-content/main/"
         "nist.gov/SP800-53/rev5/json/")
_CCI_URL = "https://dl.dod.cyber.mil/wp-content/uploads/stigs/zip/U_CCI_List.zip"


def _get(url: str) -> bytes:
    return urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=180).read()


def init_catalog(conn) -> str:
    data = _get(_NIST + "NIST_SP-800-53_rev5_catalog.json")
    tmp = Path("/tmp/cf_800-53.json"); tmp.write_bytes(data)
    cv = catalog_loader.load_oscal_catalog_file(
        conn, tmp, framework_id="nist_800_53", framework_name="NIST SP 800-53",
        authority="NIST", version_label="Rev 5", make_current=True)
    n = conn.execute("SELECT COUNT(*) FROM controls WHERE catalog_version_id=?", (cv,)).fetchone()[0]
    return f"Loaded 800-53 Rev 5: {n} controls"


def init_baselines(conn) -> str:
    out = []
    for impact, fname in (("low", "LOW"), ("moderate", "MODERATE"), ("high", "HIGH")):
        data = _get(_NIST + f"NIST_SP-800-53_rev5_{fname}-baseline_profile.json")
        tmp = Path(f"/tmp/cf_53b_{impact}.json"); tmp.write_bytes(data)
        baselines.load_baseline_profile_oscal(
            conn, tmp, baseline_id=f"nist_800_53b@{impact}", framework_id="nist_800_53",
            label=impact.title(), impact=impact)
        out.append(f"{impact}={len(baselines.baseline_control_ids(conn, f'nist_800_53b@{impact}'))}")
    return "Loaded baselines: " + ", ".join(out)


def init_cci(conn) -> str:
    z = zipfile.ZipFile(io.BytesIO(_get(_CCI_URL)))
    name = next(n for n in z.namelist() if n.lower().endswith(".xml"))
    tmp = Path("/tmp/cf_cci.xml"); tmp.write_bytes(z.read(name))
    stats = cci.load_cci_xml(conn, tmp)
    return f"Loaded {stats['cci_items']} CCIs ({stats['mappings']} control mappings)"


def init_all(conn) -> list[str]:
    return [init_catalog(conn), init_baselines(conn), init_cci(conn)]

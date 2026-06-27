"""
Data bootstrap for fresh deployments (e.g. Streamlit Cloud).

A deployed app starts with an empty DB. These functions download and load the
reference data on demand (from the Dashboard's "Initialize data" buttons), so the
deploy is code-only and the data is fetched at runtime. STIGs are ingested
separately via the STIG Library page.
"""

from __future__ import annotations

import csv
import io
import urllib.request
import zipfile
from pathlib import Path

from . import catalog_loader, baselines, cci
from .db import FRAMEWORK_KINDS

_REGISTRY = Path(__file__).resolve().parent.parent / "data" / "framework_registry.csv"

_UA = {"User-Agent": "Mozilla/5.0"}
_NIST = ("https://raw.githubusercontent.com/usnistgov/oscal-content/main/"
         "nist.gov/SP800-53/rev5/json/")
_NIST171 = ("https://raw.githubusercontent.com/usnistgov/oscal-content/main/"
            "nist.gov/SP800-171/rev3/json/NIST_SP800-171_rev3_catalog.json")
_NISTCSF = ("https://raw.githubusercontent.com/usnistgov/oscal-content/main/"
            "nist.gov/CSF/v2.0/json/NIST_CSF_v2.0_catalog.json")
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
    n = baselines.derive_cnssi_1253_from_800_53b(conn)
    return "Loaded baselines: " + ", ".join(out) + f"; derived {n} CNSSI 1253 per-CIA sets"


def init_cci(conn) -> str:
    z = zipfile.ZipFile(io.BytesIO(_get(_CCI_URL)))
    name = next(n for n in z.namelist() if n.lower().endswith(".xml"))
    tmp = Path("/tmp/cf_cci.xml"); tmp.write_bytes(z.read(name))
    stats = cci.load_cci_xml(conn, tmp)
    return f"Loaded {stats['cci_items']} CCIs ({stats['mappings']} control mappings)"


def init_registry(conn) -> str:
    """Seed the framework registry (local CSV, no network)."""
    rows = list(csv.DictReader(_REGISTRY.read_text().splitlines()))
    conn.executemany(
        """INSERT INTO frameworks
             (framework_id,name,authority,kind,category,ingest_method,anchor,notes)
           VALUES (:framework_id,:name,:authority,:kind,:category,:ingest_method,:anchor,:notes)
           ON CONFLICT(framework_id) DO UPDATE SET
             name=excluded.name, authority=excluded.authority, kind=excluded.kind,
             category=excluded.category, ingest_method=excluded.ingest_method,
             anchor=excluded.anchor, notes=excluded.notes""",
        [{**r, "anchor": int(r.get("anchor") or 0)} for r in rows])
    conn.commit()
    return f"Seeded {len(rows)} frameworks into the registry"


def init_800171(conn) -> str:
    from . import adapters
    tmp = Path("/tmp/cf_800-171.json"); tmp.write_bytes(_get(_NIST171))
    res = adapters.load_800171_oscal(conn, tmp)
    cm = adapters.seed_cmmc_from_800171(conn)
    return (f"Loaded 800-171 Rev 3: {res['controls']} controls, {res['mappings']} "
            f"800-53 mappings; CMMC L1={cm['l1']} L2={cm['l2']}")


def init_csf(conn) -> str:
    from . import adapters
    tmp = Path("/tmp/cf_csf.json"); tmp.write_bytes(_get(_NISTCSF))
    res = adapters.load_csf_oscal(conn, tmp)
    return f"Loaded NIST CSF 2.0: {res['controls']} subcategories"


def init_all(conn) -> list[str]:
    return [init_registry(conn), init_catalog(conn), init_baselines(conn),
            init_cci(conn), init_800171(conn), init_csf(conn)]

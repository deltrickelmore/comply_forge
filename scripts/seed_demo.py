"""
Seed a demo system so the dashboard renders with live data. Idempotent:
does nothing if any system already exists.

  python scripts/seed_demo.py
"""

from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from comply_forge.db import connect
from comply_forge import control_responder, stig, auth
from comply_forge.llm_provider import FakeProvider


def main() -> None:
    conn = connect()
    tenant = auth.ensure_seed(conn)  # default tenant + admin/admin
    if conn.execute("SELECT COUNT(*) FROM systems").fetchone()[0]:
        print("systems already exist — skipping demo seed")
        return
    cv = conn.execute("SELECT catalog_version_id FROM catalog_versions "
                      "WHERE framework_id='nist_800_53' AND is_current=1").fetchone()
    if not cv:
        print("load 800-53 first (scripts/fetch_catalogs.py)")
        return
    cv = cv[0]
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    conn.execute("INSERT INTO systems (system_id,name,description,impact_level,created_at,tenant_id) "
                 "VALUES (?,?,?,?,?,?)",
                 ("demo", "Demo Enclave (sample)",
                  "Linux web application behind an IdP (SAML SSO), centralized logging to Splunk, "
                  "hosted in a Moderate-impact enclave.", "moderate", now, tenant))
    conn.commit()

    fp = FakeProvider()
    drafted, reviewed = ["ac-1", "ac-2", "au-2", "ia-2", "cm-6"], {"ac-1", "ac-2", "au-2"}
    for cid in drafted:
        try:
            control_responder.draft_response(conn, system_id="demo",
                                              catalog_version_id=cv, control_id=cid, provider=fp)
        except ValueError:
            continue
    for cid in reviewed:
        conn.execute("UPDATE implemented_requirements SET needs_review=0, reviewed_by='demo-assessor', "
                     "reviewed_at=?, status='implemented' WHERE system_id='demo' AND control_id=?",
                     (now, cid))
    conn.commit()

    # apply a STIG if one is loaded, mark a couple CAT I findings Open
    stigs = stig.list_stigs(conn)
    if stigs:
        sid = stigs[0]["stig_id"]
        stig.assign(conn, "demo", sid)
        cat1 = stig.rules(conn, sid, "high")
        for r in cat1[:2]:
            stig.set_finding(conn, "demo", sid, r["rule_id"], "Open")
        for r in cat1[2:5]:
            stig.set_finding(conn, "demo", sid, r["rule_id"], "NotAFinding")

    print("seeded Demo Enclave (sample):", len(drafted), "controls,",
          "STIG applied" if stigs else "no STIG")


if __name__ == "__main__":
    main()

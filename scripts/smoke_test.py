"""
End-to-end smoke test against the sample data. Proves the foundation works:
  * load OSCAL catalog (800-53 Rev 5)
  * load non-OSCAL framework (FISCAM 2024) + 800-171 + CMMC levels
  * load crosswalk mappings
  * FTS search
  * record an answer + crosswalk-prefill into another framework
  * load Rev 6 and run a revision diff + migration-impact flagging

Run:  python scripts/smoke_test.py
Uses a throwaway DB so it never touches your real data.
"""

from __future__ import annotations

import datetime as _dt
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from comply_forge.db import connect
from comply_forge import catalog_loader, adapters, crosswalk, versioning

S = ROOT / "data" / "samples"


def main() -> None:
    db = Path(tempfile.mkdtemp()) / "smoke.db"
    conn = connect(db)
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()

    print("== Load catalogs ==")
    cv53 = catalog_loader.load_oscal_catalog_file(
        conn, S / "mini_800-53_rev5.json",
        framework_id="nist_800_53", framework_name="NIST SP 800-53",
        authority="NIST", version_label="Rev 5", make_current=True)
    n53 = conn.execute("SELECT COUNT(*) FROM controls WHERE catalog_version_id=?", (cv53,)).fetchone()[0]
    print(f"  800-53 Rev 5: {n53} controls  ({cv53})")

    adapters.load_controls_csv(
        conn, S / "mini_800-171.csv",
        framework_id="nist_800_171", framework_name="NIST SP 800-171",
        authority="NIST", version_label="Rev 2", make_current=True)
    adapters.load_fiscam_csv(conn, S / "fiscam_2024_sample.csv", version_label="2024")
    nmap = adapters.load_mappings_csv(conn, S / "mappings_sample.csv")
    ncmmc = adapters.load_cmmc_levels_csv(conn, S / "cmmc_levels_sample.csv")
    print(f"  FISCAM 2024 + 800-171 loaded; {nmap} mappings; {ncmmc} CMMC level rows")

    print("\n== FTS search for 'audit' ==")
    for r in conn.execute(
        """SELECT c.catalog_version_id, c.control_id, c.title
             FROM controls_fts f JOIN controls c ON c.rowid=f.rowid
            WHERE controls_fts MATCH 'audit OR logging' ORDER BY c.control_id"""):
        print(f"  {r['control_id']:8} {r['title']}  [{r['catalog_version_id']}]")

    print("\n== CMMC Level 2 profile (800-171 ids) ==")
    print("  ", adapters.cmmc_profile(conn, 2))

    print("\n== Record an answer for 800-53 ac-2, then crosswalk-prefill ==")
    conn.execute("INSERT INTO systems (system_id, name, created_at) VALUES (?,?,?)",
                 ("sys1", "Demo System", now))
    ir = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO implemented_requirements
             (ir_id, system_id, catalog_version_id, control_id, status, statement,
              origin, reviewed_by, reviewed_at, needs_review, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,0,?)""",
        (ir, "sys1", cv53, "ac-2", "implemented",
         "Accounts are provisioned via IdP with manager approval and quarterly review.",
         "human", "deltrick", now, now))
    conn.commit()
    created = crosswalk.prefill_from_answer(conn, ir)
    print(f"  created {len(created)} prefilled draft(s):")
    for cid in created:
        r = conn.execute(
            """SELECT ir.control_id, cv.framework_id, ir.status, ir.needs_review, ir.statement
                 FROM implemented_requirements ir
                 JOIN catalog_versions cv ON cv.catalog_version_id=ir.catalog_version_id
                WHERE ir.ir_id=?""", (cid,)).fetchone()
        print(f"    -> {r['framework_id']} {r['control_id']} status={r['status']} "
              f"needs_review={r['needs_review']}")
        print(f"       {r['statement'][:80]}...")

    print("\n== Load 800-53 Rev 6 and diff / migration impact ==")
    cv53r6 = catalog_loader.load_oscal_catalog_file(
        conn, S / "mini_800-53_rev6.json",
        framework_id="nist_800_53", framework_name="NIST SP 800-53",
        authority="NIST", version_label="Rev 6", make_current=False)
    print(versioning.migration_report(conn, "nist_800_53", cv53, cv53r6))
    impact = versioning.flag_affected_answers(conn, cv53, cv53r6)
    print(f"  migration impact: {impact}")
    flagged = conn.execute(
        """SELECT control_id FROM implemented_requirements
            WHERE catalog_version_id=? AND needs_review=1""", (cv53,)).fetchall()
    print(f"  answers now flagged needs_review: {[r[0] for r in flagged]}")

    print("\nSMOKE TEST OK")


if __name__ == "__main__":
    main()

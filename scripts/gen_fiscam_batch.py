"""
Batch-generate FISCAM 2024 Internal Control Test Plan workbooks.

Sources (pick one):
  --csv PATH        CSV with columns: control_id,title,text  (text optional)
  --from-db         every FISCAM control in the current loaded catalog
  CONTROL_ID ...    one or more control ids on the command line

Output: out/fiscam_test_plans/Enterprise_<CONTROL_ID>.xlsx (one per control).

Provider: uses the configured LLM provider (ANTHROPIC_API_KEY for dev, or
COMPLYFORGE_LLM_PROVIDER=bedrock for CUI). Without one, each workbook is a fully
populated deterministic draft (standard TOD/TOE boilerplate + control-derived
fields), every one needs_review.

Examples:
  python scripts/gen_fiscam_batch.py SM.04.02.01 SM.04.02.02
  python scripts/gen_fiscam_batch.py --csv controls.csv
  python scripts/gen_fiscam_batch.py --from-db
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from comply_forge.db import connect
from comply_forge import fiscam_test_plan as ftp
from comply_forge.llm_provider import get_provider


def _from_db(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT c.control_id, c.title, c.statement FROM controls c
             JOIN catalog_versions v ON v.catalog_version_id=c.catalog_version_id
            WHERE v.framework_id='fiscam' AND v.is_current=1
            ORDER BY c.control_id""").fetchall()
    return [{"control_id": r["control_id"].upper(), "title": r["title"],
             "text": r["statement"]} for r in rows]


def _from_csv(path: str) -> list[dict]:
    rows = list(csv.DictReader(Path(path).read_text().splitlines()))
    return [{"control_id": r["control_id"].strip(),
             "title": (r.get("title") or "").strip(),
             "text": (r.get("text") or "").strip()} for r in rows if r.get("control_id")]


def main() -> None:
    ap = argparse.ArgumentParser(description="Batch FISCAM test-plan generator")
    ap.add_argument("control_ids", nargs="*", help="control ids (if no --csv/--from-db)")
    ap.add_argument("--csv", help="CSV with control_id,title,text")
    ap.add_argument("--from-db", action="store_true", help="all current FISCAM controls")
    ap.add_argument("--outdir", default=str(ROOT / "out" / "fiscam_test_plans"))
    args = ap.parse_args()

    conn = connect()
    if args.csv:
        controls = _from_csv(args.csv)
    elif args.from_db:
        controls = _from_db(conn)
    elif args.control_ids:
        controls = [{"control_id": cid, "title": "", "text": ""} for cid in args.control_ids]
    else:
        raise SystemExit("provide control ids, --csv PATH, or --from-db")

    if not controls:
        raise SystemExit("no controls to generate")

    provider = get_provider()
    outdir = Path(args.outdir)
    print(f"Generating {len(controls)} test plan(s) with provider={provider.name} -> {outdir}")
    structured = 0
    for c in controls:
        plan = ftp.draft_test_plan(
            conn, control_id=c["control_id"],
            control_title=c["title"], control_text=c["text"], provider=provider)
        out = outdir / f"Enterprise_{c['control_id'].replace('.', '_')}.xlsx"
        ftp.write_workbook(plan, out)
        structured += int(plan.provenance.get("structured", False))
        print(f"  ✓ {c['control_id']:16} -> {out.name}  "
              f"[{plan.provenance.get('field_source')}]")
    print(f"Done. {structured}/{len(controls)} populated by LLM; "
          f"{len(controls)-structured} deterministic. All need human review.")


if __name__ == "__main__":
    main()

"""
Generate RMF Control-Family Plans (.docx) -- the "control plan per family" button.

Produces one Security Assessment & Authorization-style plan per 800-53 control
family, in the structure of the AFSV-AFLIS CA Plan sample.

Usage:
  python scripts/gen_control_plans.py CA                 # one family
  python scripts/gen_control_plans.py CA AC AU CM        # several
  python scripts/gen_control_plans.py --all              # every family in the catalog
  python scripts/gen_control_plans.py --all --system AFSV-AFLIS --enclave AFSV-BAN

Output: out/control_plans/<SYSTEM>_<FAMILY>_Plan.docx (one per family).
Uses the configured LLM provider for narratives; deterministic draft without one.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from comply_forge.db import connect
from comply_forge import control_family_plan as cfp
from comply_forge.llm_provider import get_provider


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate RMF control-family plans")
    ap.add_argument("families", nargs="*", help="family codes (e.g. CA AC AU)")
    ap.add_argument("--all", action="store_true", help="every family present in the catalog")
    ap.add_argument("--catalog", default="nist_800_53@rev5")
    ap.add_argument("--system", default="AFSV-AFLIS")
    ap.add_argument("--enclave", default="AFSV-BAN")
    ap.add_argument("--baseline", default="Moderate")
    args = ap.parse_args()

    conn = connect()
    profile = cfp.SystemProfile(system_name=args.system, enclave=args.enclave,
                                baseline=args.baseline, catalog_version_id=args.catalog)

    if args.all:
        present = [r[0].lower() for r in conn.execute(
            "SELECT DISTINCT family FROM controls WHERE catalog_version_id=? ORDER BY family",
            (args.catalog,))]
        families = [f for f in present if f in cfp.FAMILY_TITLES]
    else:
        families = [f.lower() for f in args.families]
    if not families:
        raise SystemExit("provide family codes or --all")

    provider = get_provider()
    print(f"Generating {len(families)} family plan(s) with provider={provider.name}")
    for fam in families:
        try:
            out = cfp.generate_family_plan(conn, family=fam, profile=profile, provider=provider)
            n = len(cfp._family_controls(conn, fam, args.catalog,
                    f"nist_800_53b@{args.baseline.lower()}"))
            print(f"  ✓ {fam.upper():3} {cfp.FAMILY_TITLES[fam]:42} {n:3} controls -> {out.name}")
        except ValueError as e:
            print(f"  ✗ {fam.upper():3} skipped: {e}")
    print(f"Done -> {ROOT/'out'/'control_plans'}  (all drafts need human review)")


if __name__ == "__main__":
    main()

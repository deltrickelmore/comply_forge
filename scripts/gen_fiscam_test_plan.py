"""
Generate a FISCAM 2024 Internal Control Test Plan workbook (.xlsx) for a control.

Uses the configured LLM provider (set ANTHROPIC_API_KEY, or
COMPLYFORGE_LLM_PROVIDER=bedrock for CUI) to draft the design/testing fields.
Without a provider it still produces the correct workbook skeleton with
[draft pending] placeholders for a human to complete.

Usage:
  python scripts/gen_fiscam_test_plan.py SM.04.02.01 "Security Categorization" \
      "Management categorizes systems per FIPS 199/CNSSI 1253; AO approves; annual review."
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from comply_forge.db import connect
from comply_forge import fiscam_test_plan as ftp


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: gen_fiscam_test_plan.py <CONTROL_ID> [title] [control_text]")
    control_id = sys.argv[1]
    title = sys.argv[2] if len(sys.argv) > 2 else ""
    text = sys.argv[3] if len(sys.argv) > 3 else ""

    conn = connect()
    plan = ftp.draft_test_plan(conn, control_id=control_id,
                               control_title=title, control_text=text)
    out = ROOT / "out" / f"Enterprise_{control_id.replace('.', '_')}.xlsx"
    ftp.write_workbook(plan, out)
    print(f"Wrote {out}")
    print(f"  provider={plan.provenance.get('llm_provider')} "
          f"model={plan.provenance.get('llm_model')} "
          f"structured={plan.provenance.get('structured')} needs_review={plan.needs_review}")
    if not plan.provenance.get("structured"):
        print("  NOTE: no structured LLM output — fields contain [draft pending] placeholders.")
        print("        Set ANTHROPIC_API_KEY (dev) or COMPLYFORGE_LLM_PROVIDER=bedrock (CUI) to fill them.")


if __name__ == "__main__":
    main()

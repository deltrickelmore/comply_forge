"""
Fill an UPLOADED FISCAM test-plan template (bring-your-own-template).

This is the backend for the "upload a template" button (the Streamlit
file-uploader lands in phase 5). It learns your workbook's layout, drafts the
control's design/testing fields, and writes them into a COPY of your template --
preserving your exact formatting, tabs, dropdowns, and any human-entered content.

Usage:
  python scripts/fill_from_template.py <TEMPLATE.xlsx> <CONTROL_ID> [title] [control_text]

Example:
  python scripts/fill_from_template.py ~/Downloads/SM/Enterprise_SM_04_02_01.xlsx \
      SM.04.02.05 "Account Management" "ISSM reviews and authorizes system accounts..."
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from comply_forge.db import connect
from comply_forge import fiscam_test_plan as ftp, template_engine as te


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("usage: fill_from_template.py <TEMPLATE.xlsx> <CONTROL_ID> [title] [text]")
    template = sys.argv[1]
    control_id = sys.argv[2]
    title = sys.argv[3] if len(sys.argv) > 3 else ""
    text = sys.argv[4] if len(sys.argv) > 4 else ""

    spec = te.learn_template(template)
    print(f"Learned template: {spec.summary()}")
    if spec.unmatched:
        print(f"  Unmatched labels (left untouched): {spec.unmatched[:5]}")

    conn = connect()
    plan = ftp.draft_test_plan(conn, control_id=control_id,
                               control_title=title, control_text=text)

    out = ROOT / "out" / f"FromTemplate_{control_id.replace('.', '_')}.xlsx"
    te.fill_template(template, plan.fields, out, spec=spec)
    print(f"Wrote {out}")
    print(f"  provider={plan.provenance.get('llm_provider')} "
          f"field_source={plan.provenance.get('field_source')} needs_review={plan.needs_review}")
    print("  Your formatting, tabs, and any human-entered fields are preserved.")


if __name__ == "__main__":
    main()

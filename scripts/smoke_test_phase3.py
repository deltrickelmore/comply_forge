"""
Phase-3 smoke test: LLM provider seam + control responder + CIA-triad baseline
selector + test-plan (SAP) generator. Runs entirely on the FakeProvider, so it
needs no API key and no network. Uses a throwaway DB.

Run:  python scripts/smoke_test_phase3.py
"""

from __future__ import annotations

import datetime as _dt
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from comply_forge.db import connect
from comply_forge import (catalog_loader, baselines, categorize,
                          control_responder, test_plan)
from comply_forge.llm_provider import FakeProvider, get_provider

S = ROOT / "data" / "samples"


def main() -> None:
    db = Path(tempfile.mkdtemp()) / "smoke3.db"
    conn = connect(db)
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()

    cv = catalog_loader.load_oscal_catalog_file(
        conn, S / "mini_800-53_rev5.json",
        framework_id="nist_800_53", framework_name="NIST SP 800-53",
        authority="NIST", version_label="Rev 5", make_current=True)

    print("== LLM provider seam ==")
    prov = get_provider("fake")
    print(f"  provider auto-resolves to: {get_provider().name} (no key set)")
    assert isinstance(prov, FakeProvider)

    print("\n== CIA-triad button: high-water-mark (800-53B) ==")
    baselines.load_baseline_profile_oscal(
        conn, S / "mini_800-53b_moderate.json",
        baseline_id="nist_800_53b@moderate", framework_id="nist_800_53",
        label="Moderate", impact="moderate")
    sel = categorize.categorize_and_select(
        conn, confidentiality="moderate", integrity="low", availability="low",
        model="high_water_mark")
    print(f"  C=mod I=low A=low -> overall={sel['overall_impact']} "
          f"baseline={sel['baseline_id']} -> {sel['control_count']} controls: {sel['control_ids']}")

    print("\n== CIA-triad button: per-CIA (CNSSI 1253, like rmfks.osd.mil) ==")
    baselines.load_cnssi1253_csv(conn, S / "cnssi_1253_sample.csv")
    selc = categorize.categorize_and_select(
        conn, confidentiality="high", integrity="low", availability="low",
        model="per_cia")
    print(f"  C=high I=low A=low -> union of {selc['control_count']} controls: {selc['control_ids']}")
    print(f"  per-dimension: { {k: v['control_count'] for k, v in selc['per_dimension'].items()} }")

    print("\n== Control responder (prose only, needs_review) ==")
    conn.execute("INSERT INTO systems (system_id, name, description, impact_level, created_at) "
                 "VALUES (?,?,?,?,?)",
                 ("sys1", "Demo Enclave",
                  "Linux web app behind an IdP with SAML SSO and centralized logging to Splunk.",
                  "moderate", now))
    conn.commit()
    ans = control_responder.draft_response(
        conn, system_id="sys1", catalog_version_id=cv, control_id="ac-2",
        provider=prov)
    print(f"  drafted ac-2: origin={ans['origin']} needs_review={ans['needs_review']} "
          f"reviewed_by={ans['reviewed_by']}")
    print(f"  provenance: { {k: ans['provenance'][k] for k in ('llm_provider','llm_model','evidence_used')} }")
    assert ans["needs_review"] == 1 and ans["reviewed_by"] is None  # audit guardrail

    print("\n== Test-plan button (SAP / 800-53A assessment procedures) ==")
    sap = test_plan.generate_sap(
        conn, system_id="sys1", catalog_version_id=cv,
        control_ids=sel["control_ids"], provider=prov)
    print(f"  SAP over {sap['control_count']} controls; first entry control = "
          f"{sap['procedures'][0]['control_id']}, needs_review="
          f"{sap['procedures'][0]['needs_review']}")
    print(f"  methods scaffolded: {list(sap['procedures'][0]['methods'].keys()) or '(fake provider: raw draft kept)'}")

    print("\nPHASE 3 SMOKE TEST OK")


if __name__ == "__main__":
    main()

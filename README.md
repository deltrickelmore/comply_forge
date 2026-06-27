# ComplyForge

Framework-agnostic GRC artifact + crosswalk engine. Handles RMF/NIST 800-53,
NIST 800-171, CMMC, FISCAM 2024, CIS — produces artifacts (SSP, POA&M), drafts
control responses with an LLM, and meshes with eMASS / Xacta / Archer via OSCAL.

**Design reference:** [`docs/oscal_model.md`](docs/oscal_model.md)

## Run the app

```bash
pip install -r requirements.txt        # streamlit, anthropic, python-docx, openpyxl
python3 scripts/fetch_catalogs.py      # NIST 800-53 Rev 5 (1,196 controls)
python3 scripts/fetch_baselines.py     # 800-53B Low/Moderate/High
python3 scripts/fetch_cci.py           # DISA CCI list (5,137 CCIs)
streamlit run app.py
```

The UI exposes every engine as a button: Dashboard, **Categorize (CIA)**, Controls
browser, **Draft Control Response**, **Authorization Package** (SSP + POA&M),
**FISCAM Test Plan** (generate / upload-your-template), **Control Family Plans**, and a
**Review Queue** (the human authorization gate). The active LLM provider is shown in
the sidebar.

## Status

Foundation layer (data model) is built and tested:

- ✅ Version-aware SQLite schema (revisions coexist) — `comply_forge/db.py`
- ✅ OSCAL catalog loader + FTS5 search — `comply_forge/catalog_loader.py`
- ✅ Non-OSCAL adapters: FISCAM 2024, 800-171, CMMC levels — `comply_forge/adapters.py`
- ✅ Crosswalk prefill engine (relation-aware) — `comply_forge/crosswalk.py`
- ✅ Revision diff + migration-impact — `comply_forge/versioning.py`
- ✅ Framework registry: 170+ frameworks classified into 14 *kinds* — `data/framework_registry.csv`, `scripts/seed_registry.py`

### Why a "kinds" taxonomy

The 150+ frameworks are not the same shape — a control catalog (800-53), a threat
taxonomy (ATT&CK), a law (GDPR), a scoring feed (CVSS), a process (ITIL), and an
architecture method (TOGAF) need different handling. Each framework is classified
into a `kind` (`FRAMEWORK_KINDS` in `db.py`); only `control_catalog` frameworks
get full control ingestion + answers. All are *registered* so the tool can tag,
cite, and crosswalk them. `nist_800_53` and `nist_csf` are the crosswalk anchors.
Run `python3 scripts/seed_registry.py` to load + see the breakdown.

Phase 3 (LLM capabilities) is built and tested on a fake provider (no key needed):

- ✅ Swappable LLM provider seam (fake / public Claude API / Bedrock GovCloud for CUI) — `comply_forge/llm_provider.py`
- ✅ Control responder — drafts implementation statements, prose-only, with provenance + review guardrail — `comply_forge/control_responder.py`
- ✅ **CIA-triad button** — FIPS 199 categorize → select; high-water-mark (800-53B) + per-CIA (CNSSI 1253, like rmfks.osd.mil) — `comply_forge/categorize.py`, `comply_forge/baselines.py`
- ✅ **Test-plan button (FISCAM 2024)** — generates the DLA/GAO Internal Control Test Plan .xlsx workbook (Instructions + Test Plan tabs; 44 line items across Risks/Internal Control Details, Test of Design, Test of Effectiveness) — `comply_forge/fiscam_test_plan.py`.
  - Single: `python3 scripts/gen_fiscam_test_plan.py SM.04.02.01 "<title>" "<control text>"`
  - Batch: `python3 scripts/gen_fiscam_batch.py SM.04.02.01 SM.04.02.02` (or `--csv controls.csv`, or `--from-db`) → one workbook per control in `out/fiscam_test_plans/`
  - With an LLM provider the design/testing fields are drafted from the control. Without one, each workbook is a **fully populated deterministic draft** (standard TOD/TOE boilerplate + control-derived fields); only the human sign-off/result/date fields stay blank. Every workbook is `needs_review`.
- ✅ **Upload-your-own-template** — point the tool at *your* workbook and it fills values into your existing cells, preserving your exact formatting, tabs, dropdowns, and any human-entered content — `comply_forge/template_engine.py`. Field matching is anchored on the line-number column (robust against repeated labels), with section-aware label fallback for unnumbered templates. `python3 scripts/fill_from_template.py <TEMPLATE.xlsx> <CONTROL_ID> "<title>" "<text>"`. (The actual upload **button** arrives with the Streamlit UI in phase 5; this is its backend.)
- ✅ **System Security Plan (SSP)** — OSCAL JSON + Word, assembled from a system's control responses; flags unreviewed drafts; `require_reviewed` mode refuses export until approved — `comply_forge/ssp.py`
- ✅ **POA&M** — OSCAL JSON + Word, derived from controls not fully implemented / awaiting review — `comply_forge/poam.py`
- ✅ **STIG library + reader + apply-to-system** — ingest DISA STIGs (XCCDF, by URL or upload), browse rules by severity (CAT I/II/III) with check/fix/discussion, and auto-map each rule to 800-53 controls via its CCIs; apply a STIG to a system (control coverage + checklist CSV export) — `comply_forge/stig.py`, `scripts/fetch_stig.py`. Verified on Windows 11 V2R3 (258 rules → 50 controls mapped).
- ✅ **Control plan per family (RMF)** — generates a Security Assessment & Authorization-style Word plan for each 800-53 control family (title page, revision history, Introduction with compliance matrix + roles/personnel tables, then a section + CCI table per control) — `comply_forge/control_family_plan.py`. One family: `python3 scripts/gen_control_plans.py CA`; every family: `python3 scripts/gen_control_plans.py --all`. Built with python-docx (no Node). Narratives are LLM-drafted (deterministic without a key); all drafts `needs_review`. Plans are scoped to the controls selected at the system's impact level via the loaded 800-53B baselines (`python3 scripts/fetch_baselines.py` loads Low/Moderate/High = 149/287/370 controls); falls back to the full family if no baseline is loaded. The per-control CCI assessment tables populate from the loaded **DISA CCI List** (`python3 scripts/fetch_cci.py` → 5,137 CCIs) — `comply_forge/cci.py`.
- ✅ **Test-plan (NIST RMF)** — Security Assessment Plan (SAP) / 800-53A assessment procedures — `comply_forge/test_plan.py`

Run `python3 scripts/smoke_test_phase3.py` to exercise all four.

Not yet built: SSP/POA&M document generators (Word + OSCAL export), Streamlit UI,
live platform integrations. See build order in the design doc §7.

### LLM provider & CUI

Develop on synthetic data with the fake/public provider. Before any real CUI,
set `COMPLYFORGE_LLM_PROVIDER=bedrock` (Claude on AWS Bedrock GovCloud, FedRAMP/
IL4-5) — no change to responder/test-plan code. The LLM writes prose only; code
owns IDs/status/structure; every draft is `needs_review` with provenance stamped.

## Quick start (stdlib only — no installs needed)

```bash
# 1. Prove the whole foundation works on sample data (throwaway DB):
python3 scripts/smoke_test.py

# 2. Load the REAL NIST 800-53 Rev 5 catalog (needs network; ~1,196 controls):
python3 scripts/fetch_catalogs.py

# 3. Load non-OSCAL frameworks into the real DB:
python3 -c "
from comply_forge.db import connect
from comply_forge import adapters
c = connect()
adapters.load_controls_csv(c, 'data/samples/mini_800-171.csv',
    framework_id='nist_800_171', framework_name='NIST SP 800-171',
    authority='NIST', version_label='Rev 2')
adapters.load_fiscam_csv(c, 'data/samples/fiscam_2024_sample.csv')
adapters.load_mappings_csv(c, 'data/samples/mappings_sample.csv')
adapters.load_cmmc_levels_csv(c, 'data/samples/cmmc_levels_sample.csv')
print('loaded')
"
```

## Loading your own data

| Source | How |
|--------|-----|
| Any OSCAL catalog (800-53, 800-171 if OSCAL) | `python3 -m comply_forge.catalog_loader --framework <id> --name <name> --version "<label>" --file <path> --make-current` |
| FISCAM 2024 | `adapters.load_fiscam_csv(conn, "fiscam.csv")` — CSV cols: `control_id,family,title,statement,guidance` |
| Crosswalk mappings | `adapters.load_mappings_csv(conn, "map.csv")` — cols incl. `relation` (equivalent\|subset\|superset\|intersects\|related) |
| CMMC levels | `adapters.load_cmmc_levels_csv(conn, "cmmc.csv")` — cols: `level,control_id` |

## When a new revision drops (e.g. 800-53 Rev 6)

```python
from comply_forge.db import connect
from comply_forge import catalog_loader, versioning
c = connect()
new = catalog_loader.load_oscal_catalog_file(c, "rev6.json",
        framework_id="nist_800_53", framework_name="NIST SP 800-53",
        authority="NIST", version_label="Rev 6")          # loads alongside Rev 5
print(versioning.migration_report(c, "nist_800_53", "nist_800_53@rev5", new))
versioning.flag_affected_answers(c, "nist_800_53@rev5", new)  # flags your answers to re-review
```

## Guardrails (do not remove)

- The LLM drafts prose only; code owns IDs/status/structure.
- `reviewed_by` must be set before export — no auto-attestation.
- SSP/POA&M content is CUI: keep the LLM provider swappable to FedRAMP-authorized
  hosting (Bedrock GovCloud) before processing real data.

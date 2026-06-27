# ComplyForge — OSCAL Data Model & Architecture

The foundational design doc. Everything (artifacts, crosswalk, integrations,
revision updates) sits on this model.

## 1. Why OSCAL is the backbone

[OSCAL](https://pages.nist.gov/OSCAL/) is NIST's machine-readable standard for
control catalogs, baselines, system security plans, assessments, and POA&Ms.
Making it the **internal data model** buys three things:

1. **Framework-agnosticism** — anything with controls is just another row source.
2. **Crosswalk** becomes mapping between control IDs (the killer feature).
3. **Integration** becomes "export valid OSCAL" — Xacta & eMASS consume it —
   instead of N bespoke API adapters.

### The OSCAL layer cake

| Tier | Model | What | We… |
|------|-------|------|-----|
| Control | Catalog | The raw controls (800-53 itself) | consume |
| | Profile | A baseline: controls selected + tailored | consume + author |
| Implementation | Component Definition | Reusable "how product X meets control Y" | author (reuse lib) |
| | **SSP** | System Security Plan | **author — #1 output** |
| Assessment | Assessment Plan (SAP) | What/how to assess | author (phase 2) |
| | Assessment Results (SAR) | Findings | author (phase 2) |
| | **POA&M** | Plan of Action & Milestones | **author — key output** |

MVP lives in **Catalog → Profile → SSP → POA&M**.

### The one structural fact

Every layer references the layer above **by control-id** (`ac-2`), it does not
copy control text. So the internal store is a **graph keyed by control-id** — and
that same key is what the crosswalk maps on. Nail the ID model and the rest
follows.

## 2. Storage model (see `comply_forge/db.py`)

Pragmatic middle ground: **full OSCAL object in a blob + query-critical fields
shredded into columns.**

- Export is lossless: `SELECT oscal_json`.
- Browse/search is fast: no JSON parsing per row; FTS5 over prose.

Key tables:

- `frameworks` — registry (800-53, 800-171, fiscam, cis, cmmc). `kind` =
  `control_catalog` or `process_library` (ITIL rides here — see §6).
- `catalog_versions` — **one row per revision**. This is what makes revision
  updates work; Rev 5 and Rev 6 coexist.
- `controls` — keyed by `(catalog_version_id, control_id)`; carries
  `content_hash` for diffing.
- `control_mappings` — the crosswalk (§4).
- `systems`, `implemented_requirements` — a system's answers (§3).
- `revision_changes` — audit trail of what changed between revisions (§5).

## 3. Where the LLM plugs in — and where it must not

The LLM touches **prose only**:

- **In:** control `statement` + `guidance` + the system description (+ relevant
  Component Definitions for reuse).
- **Out:** a draft `implemented_requirements.statement`.

The LLM **never** writes IDs, status, or structure — those come from
deterministic code. This keeps generated OSCAL schema-valid (LLMs are weak at
long structured JSON, strong at prose) and keeps the tool audit-defensible.

**Audit guardrail:** `reviewed_by` stays NULL until a human signs off; export
refuses unreviewed requirements. The tool *drafts and assembles*; a human
*authorizes*. Never auto-attest or auto-submit.

## 4. Crosswalk relation semantics

Mappings are **directional**: `relation` describes `src → dst`.

| relation | meaning | auto-prefill? |
|----------|---------|---------------|
| `equivalent` | same requirement; answering one satisfies the other | **full** |
| `superset` | src is broader than dst; src fully covers dst | **full** |
| `subset` | src is narrower; only partially fills dst | partial |
| `intersects` | partial overlap; informs but doesn't satisfy | partial |
| `related` | topical only, no coverage claim | **no** |

`comply_forge/crosswalk.py` uses these: `equivalent`/`superset` → full prefill;
`subset`/`intersects` → partial draft tagged for human completion; `related` →
never auto-prefilled. **Every prefill is `needs_review=1`** — a human confirms
before it counts.

This is where crosswalks get genuinely hard: most published mappings (NIST's own,
CIS's) are *undirected* and don't carry these semantics. Part of the work is
curating direction + relation as you ingest each mapping source.

## 5. Revision updates (Rev 5 → Rev 6)

See `comply_forge/versioning.py`. When a new revision loads as its own
`catalog_versions` row:

1. `diff_versions(old, new)` compares `content_hash` per control →
   **added / withdrawn / modified**, recorded in `revision_changes`.
2. `flag_affected_answers(old, new)` sets `needs_review=1` on every existing
   answer whose control was modified or withdrawn — so a human knows exactly
   what to re-review. It does **not** auto-migrate answers; that's a human call.

Demonstrated in the smoke test: loading a (fictional) Rev 6 flags the existing
`ac-2` answer because `ac-2`'s text changed.

## 6. The framework-kinds taxonomy (spanning 150+ frameworks)

The core insight for breadth: **the 150+ frameworks are not the same shape.** A
control catalog, a threat taxonomy, a law, a scoring feed, a process lifecycle,
and an architecture method cannot all be "controls you answer." Forcing them into
one table breaks the model. Instead every framework is classified into a **kind**
(see `FRAMEWORK_KINDS` in `db.py`), and each kind has its own handling strategy:

| kind | handling strategy | examples |
|------|-------------------|----------|
| `control_catalog` | full ingest; holds answers; SSP material | 800-53, 800-171, ISO 27002, CIS v8, CCM, PCI DSS, HITRUST |
| `benchmark_checklist` | config checks tied to platforms; SCAP-consumable | DISA STIGs, CIS Benchmarks |
| `process_lifecycle` | phases/activities feed procedures, not answers | RMF, ITIL 4, IR (800-61), SDLC, Agile, PMBOK |
| `risk_methodology` | drives risk assessment, not control answers | FAIR, OCTAVE, ISO 31000, 800-30 |
| `maturity_model` | leveled scoring overlay on a catalog | CMMC, CSF tiers, CIS IGs, SAMM, BSIMM |
| `threat_knowledge_base` | graph/taxonomy; map controls↔techniques | ATT&CK, D3FEND, CAPEC, CWE, ATLAS |
| `vuln_intel_feed` | data feed for POA&M/risk, not a catalog | CVE, CVSS, EPSS, CISA KEV, CPE |
| `regulation_law` | obligations crosswalked to controls | FISMA, GDPR, HIPAA, SOX, DFARS, EO 14028 |
| `guidance_reference` | narrative; tag + cite, no discrete controls | most SP 800 guides, AWS WAF, Zero Trust |
| `architecture_framework` | EA modeling method | TOGAF, Zachman, FEAF, DoDAF, UAF, Purdue |
| `attestation_program` | audit/cert program producing a report | SOC 1/2/3, FedRAMP, ISO 27001 cert, CSA STAR |
| `technical_standard` | protocol/spec, referenced by controls | SAML, OAuth2, FIDO2, PKI, STIX/TAXII, SBOM, SLSA |
| `artifact_type` | a tool OUTPUT, not an input framework | SSP, SAP, SAR, POA&M, ATO |
| `platform_tool` | external system = integration target | eMASS, Xacta, Archer, ACAS, ESS |

**Registration ≠ ingestion.** All ~150 are *registered* (`data/framework_registry.csv`,
loaded by `scripts/seed_registry.py`) so the tool knows them, can tag artifacts,
and can crosswalk. But only `control_catalog` frameworks get full control loading
+ answers. The rest get the treatment that fits their kind.

**Hub-and-spoke crosswalk.** `nist_800_53` and `nist_csf` are flagged `anchor=1`.
Most published mappings (ISO, CIS, CCM, PCI, FISCAM) target 800-53, so map every
spoke framework to an anchor once and you get N×N coverage transitively instead
of curating every pairwise mapping.

### Per-framework notes
- **800-53 / 800-171** — OSCAL catalog (800-53) / CSV (`catalog_loader`,
  `adapters`). Versioned by revision (§5).
- **FISCAM 2024 (GAO)** — not OSCAL; CSV adapter + GAO's 800-53 crosswalk.
- **CIS Controls v8** — CSV controls + CIS's published 800-53 mappings.
- **CMMC** — `maturity_model` over 800-171; levels via membership CSV →
  `cmmc_profile(level)`.
- **ITIL 4, RMF steps, Agile** — `process_lifecycle`: feed procedure text, not
  control answers.
- **ATT&CK / CWE / D3FEND** — `threat_knowledge_base`: future graph ingest, used
  to enrich controls with threat coverage, not as answerable controls.
- **CVE/CVSS/KEV** — `vuln_intel_feed`: feed POA&M items and risk scoring.

## 7. Build order

1. ✅ Version-aware schema + OSCAL loader + FTS  (this commit)
2. ✅ Non-OSCAL adapters (FISCAM, 800-171, CMMC) + crosswalk + revision diff
3. ▢ LLM `control_responder` (swappable provider: public API ↔ Bedrock GovCloud)
4. ▢ SSP/POA&M artifact generators (OSCAL JSON + Word via python-docx)
5. ▢ Streamlit UI
6. ▢ Integrations: OSCAL export → eMASS API → Xacta → Archer

## 8. Hard constraints to honor

- **Data sensitivity:** SSPs/POA&Ms are CUI. Develop on synthetic data; make the
  LLM call swappable to **Claude on AWS Bedrock GovCloud** (FedRAMP High / IL4-5)
  before real CUI touches it. Design for it now or rework later.
- **No auto-attestation.** Drafts and assembles; humans authorize.
- **Lossless OSCAL round-trip.** Keep the verbatim blob; never reconstruct from
  shredded columns alone.

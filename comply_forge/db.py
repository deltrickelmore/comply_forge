"""
SQLite schema and connection for ComplyForge.

Design notes
------------
* Catalogs are VERSION-AWARE from the start. A "catalog version" is one row in
  `catalog_versions` (e.g. NIST 800-53 Rev 5, then later Rev 6). Controls belong
  to a catalog_version, so an old revision is never overwritten -- it is retained
  so you can diff Rev5 -> Rev6 and assess migration impact on existing answers.
* We store the full OSCAL object verbatim in an `oscal_json` blob AND shred the
  query-critical fields into columns. Export is lossless (SELECT oscal_json);
  browse/search is fast (no JSON parsing per row).
* `frameworks` is the registry that makes the tool framework-agnostic: 800-53,
  800-171/CMMC, FISCAM 2024, CIS all live here. Anything with controls becomes
  rows; mappings between them live in `control_mappings`.

Stdlib only -- no external deps required to stand this up.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

# Override with COMPLYFORGE_DB (e.g. on Streamlit Cloud, point at a writable path).
DEFAULT_DB = Path(os.environ.get(
    "COMPLYFORGE_DB", str(Path.home() / "comply_forge" / "comply_forge.db")))

SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ---------------------------------------------------------------------------
-- Multi-tenancy + auth. Reference data (controls, baselines, CCIs, STIG catalog,
-- frameworks) is SHARED across tenants; tenant-scoped data hangs off `systems`
-- via systems.tenant_id, so filtering systems by tenant isolates everything
-- downstream (implemented_requirements, stig_assignments, stig_findings).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT
);
CREATE TABLE IF NOT EXISTS users (
    username      TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,         -- pbkdf2: salt_hex:hash_hex
    tenant_id     TEXT NOT NULL REFERENCES tenants(tenant_id),
    role          TEXT NOT NULL DEFAULT 'user',  -- user | admin
    created_at    TEXT
);

-- ---------------------------------------------------------------------------
-- Framework registry. One row per framework family (not per revision).
-- kind drives how the framework behaves:
--   'control_catalog' -> has selectable controls (800-53, 800-171, CIS, FISCAM)
--   'process_library' -> not control-shaped; feeds statements (ITIL)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS frameworks (
    framework_id   TEXT PRIMARY KEY,      -- 'nist_800_53', 'nist_800_171', 'fiscam', 'cis', 'cmmc'
    name           TEXT NOT NULL,         -- human label
    authority      TEXT,                  -- 'NIST', 'GAO', 'CIS', 'DoD CIO'
    kind           TEXT NOT NULL DEFAULT 'control_catalog',  -- see FRAMEWORK_KINDS
    category       TEXT,                  -- reference-library grouping, e.g. 'Federal Cyber'
    ingest_method  TEXT,                  -- oscal|csv|taxonomy|feed|reference|mapping|integration
    anchor         INTEGER NOT NULL DEFAULT 0,  -- 1 = crosswalk hub (800-53, CSF 2.0)
    notes          TEXT
);

-- ---------------------------------------------------------------------------
-- A specific revision/version of a framework's catalog.
-- This is what makes "update when Rev 6 drops" work: each revision is its own
-- row; controls hang off catalog_version_id.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS catalog_versions (
    catalog_version_id TEXT PRIMARY KEY,  -- 'nist_800_53@rev5'
    framework_id       TEXT NOT NULL REFERENCES frameworks(framework_id),
    version_label      TEXT NOT NULL,     -- 'Rev 5', '2024', 'v8'
    oscal_uuid         TEXT,              -- catalog uuid from the OSCAL doc, if any
    source_uri         TEXT,             -- where it was loaded from
    published          TEXT,             -- ISO date from OSCAL metadata
    loaded_at          TEXT NOT NULL,     -- when we ingested it
    is_current         INTEGER NOT NULL DEFAULT 0,  -- the active revision for this framework
    UNIQUE (framework_id, version_label)
);

-- ---------------------------------------------------------------------------
-- Controls, shredded for browse/search + full OSCAL kept verbatim.
-- Keyed by (catalog_version_id, control_id) so the same 'ac-2' can exist in
-- Rev5 and Rev6 side by side.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS controls (
    catalog_version_id TEXT NOT NULL REFERENCES catalog_versions(catalog_version_id) ON DELETE CASCADE,
    control_id         TEXT NOT NULL,     -- 'ac-2'  (OSCAL id, lowercased)
    family             TEXT,              -- 'AC'
    title              TEXT,
    statement          TEXT,              -- flattened prose, for FTS + LLM context
    guidance           TEXT,              -- supplemental guidance prose
    params_json        TEXT,              -- OSCAL parameters as JSON
    oscal_json         TEXT,              -- the full control object, verbatim
    content_hash       TEXT,              -- hash of (title+statement+guidance) for diffing
    PRIMARY KEY (catalog_version_id, control_id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS controls_fts USING fts5(
    control_id, title, statement, guidance,
    content='controls', content_rowid='rowid'
);

-- ---------------------------------------------------------------------------
-- Crosswalk: the money table. Maps a control in one framework to a control in
-- another. Relation semantics are detailed in docs/oscal_model.md.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS control_mappings (
    src_framework  TEXT NOT NULL,
    src_version    TEXT,                  -- version_label or NULL = any revision
    src_control    TEXT NOT NULL,
    dst_framework  TEXT NOT NULL,
    dst_version    TEXT,
    dst_control    TEXT NOT NULL,
    relation       TEXT NOT NULL,         -- equivalent|subset|superset|intersects|related
    authority      TEXT,                  -- 'NIST', 'GAO', 'CIS', 'manual'
    note           TEXT,
    PRIMARY KEY (src_framework, src_control, dst_framework, dst_control, relation)
);

-- ---------------------------------------------------------------------------
-- Systems under assessment.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS systems (
    system_id     TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    description   TEXT,
    impact_level  TEXT,                   -- 'low'|'moderate'|'high' or IL2..6
    created_at    TEXT
);

-- ---------------------------------------------------------------------------
-- The heart of the SSP: a system's answer to a control.
-- reviewed_by stays NULL until a human signs off; export refuses unreviewed
-- requirements (audit guardrail -- the tool drafts, a human authorizes).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS implemented_requirements (
    ir_id              TEXT PRIMARY KEY,
    system_id          TEXT NOT NULL REFERENCES systems(system_id) ON DELETE CASCADE,
    catalog_version_id TEXT NOT NULL REFERENCES catalog_versions(catalog_version_id),
    control_id         TEXT NOT NULL,
    status             TEXT,              -- implemented|partial|planned|na|inherited
    statement          TEXT,              -- LLM-drafted, human-reviewed prose
    origin             TEXT,              -- 'llm'|'human'|'crosswalk_prefill'
    source_ir_id       TEXT,             -- if prefilled via crosswalk, the answer it came from
    reviewed_by        TEXT,
    reviewed_at        TEXT,
    needs_review       INTEGER NOT NULL DEFAULT 1,  -- set when underlying control changes on revision update
    oscal_json         TEXT,
    updated_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_ir_system ON implemented_requirements(system_id);
CREATE INDEX IF NOT EXISTS idx_ir_control ON implemented_requirements(catalog_version_id, control_id);
CREATE INDEX IF NOT EXISTS idx_ctrl_family ON controls(catalog_version_id, family);

-- ---------------------------------------------------------------------------
-- Baselines: control selections used by the CIA-triad / Categorize-Select step.
-- A baseline is a named set of control_ids. dimension supports two models:
--   'overall'      -> FIPS 200 / 800-53B high-water-mark (Low/Mod/High)
--   'C' | 'I' | 'A'-> CNSSI 1253 per-CIA assignment (DoD/NSS), like rmfks.osd.mil
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS baselines (
    baseline_id   TEXT PRIMARY KEY,      -- 'nist_800_53b@moderate', 'cnssi_1253@C-high'
    framework_id  TEXT NOT NULL,
    label         TEXT NOT NULL,         -- 'Moderate', 'High'
    dimension     TEXT NOT NULL DEFAULT 'overall',  -- overall|C|I|A
    impact        TEXT NOT NULL,         -- low|moderate|high
    authority     TEXT,                  -- 'NIST 800-53B', 'CNSSI 1253'
    source        TEXT
);
CREATE TABLE IF NOT EXISTS baseline_controls (
    baseline_id   TEXT NOT NULL REFERENCES baselines(baseline_id) ON DELETE CASCADE,
    control_id    TEXT NOT NULL,
    PRIMARY KEY (baseline_id, control_id)
);

-- ---------------------------------------------------------------------------
-- DISA Control Correlation Identifiers (CCIs) -- the assessment-procedure rows
-- that populate control-plan CCI tables. cci_items = the CCI + its definition +
-- 800-53A assessment-objective acronym; cci_control_map = CCI -> 800-53 control.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cci_items (
    cci_id      TEXT PRIMARY KEY,      -- 'CCI-000001'
    definition  TEXT,
    status      TEXT,
    type        TEXT,
    ap_acronym  TEXT                   -- 800-53A objective, e.g. 'AC-1.1 (i and ii)'
);
CREATE TABLE IF NOT EXISTS cci_control_map (
    cci_id       TEXT NOT NULL REFERENCES cci_items(cci_id) ON DELETE CASCADE,
    control_id   TEXT NOT NULL,        -- 'ac-1', 'ac-2.1'
    nist_version TEXT,                 -- '5','4','3'
    index_text   TEXT,                 -- raw reference index, e.g. 'AC-1 a 1'
    PRIMARY KEY (cci_id, control_id, nist_version)
);
CREATE INDEX IF NOT EXISTS idx_cci_control ON cci_control_map(control_id);

-- ---------------------------------------------------------------------------
-- DISA STIGs (Security Technical Implementation Guides). Parsed from XCCDF.
-- Each rule references CCIs -> which join to cci_control_map -> 800-53 controls,
-- so applying a STIG to a system surfaces the controls it covers.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stigs (
    stig_id       TEXT PRIMARY KEY,    -- slug, e.g. 'ms_windows_11@V2R3'
    title         TEXT,
    version       TEXT,
    release_info  TEXT,                -- 'Release: 3 Benchmark Date: 02 Apr 2025'
    benchmark_id  TEXT,
    source        TEXT,
    loaded_at     TEXT
);
CREATE TABLE IF NOT EXISTS stig_rules (
    stig_id       TEXT NOT NULL REFERENCES stigs(stig_id) ON DELETE CASCADE,
    group_id      TEXT,                -- 'V-253254'
    rule_id       TEXT NOT NULL,       -- 'SV-253254r991589_rule'
    stig_ref      TEXT,                -- 'WN11-00-000005' (rule version / STIG ID)
    severity      TEXT,                -- high|medium|low
    cat           TEXT,                -- CAT I|CAT II|CAT III
    title         TEXT,
    discussion    TEXT,
    check_content TEXT,
    fix_text      TEXT,
    PRIMARY KEY (stig_id, rule_id)
);
CREATE TABLE IF NOT EXISTS stig_rule_cci (
    stig_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    cci     TEXT NOT NULL,
    PRIMARY KEY (stig_id, rule_id, cci)
);
CREATE TABLE IF NOT EXISTS stig_assignments (
    system_id   TEXT NOT NULL,
    stig_id     TEXT NOT NULL,
    assigned_at TEXT,
    PRIMARY KEY (system_id, stig_id)
);
-- Per-system STIG check results. status: Open|NotAFinding|Not_Applicable|Not_Reviewed.
-- 'Open' findings flow into the POA&M.
CREATE TABLE IF NOT EXISTS stig_findings (
    system_id  TEXT NOT NULL,
    stig_id    TEXT NOT NULL,
    rule_id    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'Not_Reviewed',
    comments   TEXT,
    updated_at TEXT,
    PRIMARY KEY (system_id, stig_id, rule_id)
);
CREATE INDEX IF NOT EXISTS idx_stig_rules_sev ON stig_rules(stig_id, severity);
CREATE INDEX IF NOT EXISTS idx_stig_rule_cci ON stig_rule_cci(cci);

-- ---------------------------------------------------------------------------
-- Revision migration log: records what a revision diff found, so the impact on
-- existing answers is auditable.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS revision_changes (
    from_version_id TEXT NOT NULL,
    to_version_id   TEXT NOT NULL,
    control_id      TEXT NOT NULL,
    change_type     TEXT NOT NULL,        -- added|removed|modified|withdrawn
    detail          TEXT,
    detected_at     TEXT,
    PRIMARY KEY (from_version_id, to_version_id, control_id, change_type)
);
"""

# Keep the FTS index in sync with the controls table.
FTS_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS controls_ai AFTER INSERT ON controls BEGIN
    INSERT INTO controls_fts(rowid, control_id, title, statement, guidance)
    VALUES (new.rowid, new.control_id, new.title, new.statement, new.guidance);
END;
CREATE TRIGGER IF NOT EXISTS controls_ad AFTER DELETE ON controls BEGIN
    INSERT INTO controls_fts(controls_fts, rowid, control_id, title, statement, guidance)
    VALUES ('delete', old.rowid, old.control_id, old.title, old.statement, old.guidance);
END;
CREATE TRIGGER IF NOT EXISTS controls_au AFTER UPDATE ON controls BEGIN
    INSERT INTO controls_fts(controls_fts, rowid, control_id, title, statement, guidance)
    VALUES ('delete', old.rowid, old.control_id, old.title, old.statement, old.guidance);
    INSERT INTO controls_fts(rowid, control_id, title, statement, guidance)
    VALUES (new.rowid, new.control_id, new.title, new.statement, new.guidance);
END;
"""


# The taxonomy of framework KINDS -- each has a distinct handling strategy.
# This is what lets one tool span 150+ frameworks without pretending they're all
# "controls you answer". See docs/oscal_model.md ��4.
FRAMEWORK_KINDS = {
    "control_catalog":      "Selectable controls you implement & answer (SSP material). 800-53, 800-171, ISO 27002, CIS v8, CCM, PCI DSS.",
    "benchmark_checklist":  "Config/hardening checklists tied to platforms. DISA STIGs, CIS Benchmarks, SCAP baselines.",
    "process_lifecycle":    "Phases/activities, not controls. RMF steps, ITIL 4, IR (800-61), SDLC, Agile, PMBOK.",
    "risk_methodology":     "Methods for assessing/quantifying risk. FAIR, OCTAVE, ISO 31000, 800-30, COSO ERM.",
    "maturity_model":       "Leveled capability assessment. CMMI, CMMC levels, CSF tiers, CIS IGs, BSIMM, SAMM.",
    "threat_knowledge_base":"Adversary/weakness taxonomies (graph-shaped). ATT&CK, D3FEND, CAPEC, CWE, ATLAS.",
    "vuln_intel_feed":      "Vulnerability enumeration/scoring data feeds. CVE, CVSS, CPE, EPSS, CISA KEV.",
    "regulation_law":       "Legal/regulatory obligations. FISMA, GDPR, HIPAA, SOX, DFARS clauses, OMB circulars, EO 14028.",
    "guidance_reference":   "Narrative guidance / reference architectures, no discrete controls. Most SP 800 guides, AWS WAF, Azure CAF, Zero Trust.",
    "architecture_framework":"Enterprise/solution architecture methods. TOGAF, Zachman, FEAF, DoDAF, UAF, Purdue.",
    "attestation_program":  "Audit/certification programs producing a report or cert. SOC 1/2/3, FedRAMP, ISO 27001 cert, HITRUST, CSA STAR.",
    "technical_standard":   "Protocols / technical specs. SAML, OAuth2, OIDC, FIDO2, PKI, STIX/TAXII, SBOM, SLSA, in-toto.",
    "artifact_type":        "An OUTPUT the tool produces, not an input framework. SSP, SAP, SAR, POA&M, ATO, ConMon strategy.",
    "platform_tool":        "External GRC/scanning platform = integration target, not a framework. eMASS, Xacta, Archer, ACAS, HBSS/ESS.",
}

# Additive migrations for DBs created before a column existed.
_MIGRATIONS = [
    ("frameworks", "category", "TEXT"),
    ("frameworks", "ingest_method", "TEXT"),
    ("frameworks", "anchor", "INTEGER NOT NULL DEFAULT 0"),
    ("systems", "tenant_id", "TEXT"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, col, decl in _MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    conn.commit()


def connect(db_path: Path | str = DEFAULT_DB,
            check_same_thread: bool = True) -> sqlite3.Connection:
    """Open (creating if needed) the ComplyForge database with schema applied.

    Pass check_same_thread=False for Streamlit, where reruns may land on different
    threads and the connection is shared via st.cache_resource."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.executescript(FTS_TRIGGERS)
    _migrate(conn)
    return conn


if __name__ == "__main__":
    c = connect()
    tables = [r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print("Initialized", DEFAULT_DB)
    print("Tables:", ", ".join(tables))

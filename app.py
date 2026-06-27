"""
ComplyForge — Streamlit UI.

Turns the engines into buttons: Categorize (CIA), browse Controls, Draft Control
Responses, FISCAM Test Plans (generate / upload-your-template / batch), Control
Plans per family, and a Review Queue. Run:

    streamlit run app.py
"""

from __future__ import annotations

import datetime as _dt
import io
import tempfile
import uuid
from pathlib import Path

import streamlit as st

from comply_forge.db import connect, FRAMEWORK_KINDS
from comply_forge import (categorize, control_responder, fiscam_test_plan,
                          template_engine, control_family_plan, cci)
from comply_forge.llm_provider import get_provider

st.set_page_config(page_title="ComplyForge", page_icon="🛡️", layout="wide")


@st.cache_resource
def db():
    return connect(check_same_thread=False)  # shared across Streamlit rerun threads


def _now():
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _read_bytes(path) -> bytes:
    return Path(path).read_bytes()


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
conn = db()
prov = get_provider()
from comply_forge import auth as _auth, audit as _audit
_auth.ensure_seed(conn)

# --- login gate (multi-tenant) ---
if "user" not in st.session_state:
    st.markdown("## 🛡️ ComplyForge")
    st.caption("RMF authorization & continuous-monitoring workbench")
    with st.form("login"):
        st.subheader("Sign in")
        _u = st.text_input("Username")
        _p = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in", type="primary"):
            _usr = _auth.authenticate(conn, _u.strip(), _p)
            if _usr:
                st.session_state["user"] = _usr
                _audit.log(conn, tenant_id=_usr["tenant_id"], username=_usr["username"],
                           action="login")
                st.rerun()
            else:
                st.error("Invalid username or password.")
    st.caption("First run: **admin / admin** — change it after signing in.")
    st.stop()

USER = st.session_state["user"]
TENANT = USER["tenant_id"]
BRAND = _auth.get_tenant(conn, TENANT)


def audit(action, target="", detail=""):
    _audit.log(conn, tenant_id=TENANT, username=USER["username"],
               action=action, target=target, detail=detail)

if BRAND.get("logo_blob"):
    st.sidebar.image(BRAND["logo_blob"], width=120)
    st.sidebar.markdown(f"### {BRAND['name']}")
else:
    st.sidebar.title(f"{BRAND['logo']} {BRAND['name']}")
st.sidebar.caption(f"powered by ComplyForge · {USER['username']} ({USER['role']})")
PAGE = st.sidebar.radio("Navigate", [
    "Dashboard", "Categorize (CIA)", "Controls",
    "Draft Control Response", "Authorization Package",
    "FISCAM Test Plan", "Control Family Plans",
    "STIG Library", "Review Queue",
] + (["Admin"] if USER["role"] == "admin" else []))
st.sidebar.divider()
st.sidebar.markdown(f"**LLM provider:** `{prov.name}`")
if prov.name == "fake":
    st.sidebar.info("No key set → deterministic drafts. Set `ANTHROPIC_API_KEY` "
                    "(dev) or `COMPLYFORGE_LLM_PROVIDER=bedrock` (CUI).")
st.sidebar.caption("All drafts require human review — nothing is auto-attested.")
if st.sidebar.button("Sign out"):
    audit("logout")
    del st.session_state["user"]
    st.rerun()


def _current_catalog(framework_id="nist_800_53") -> str | None:
    row = conn.execute("SELECT catalog_version_id FROM catalog_versions "
                       "WHERE framework_id=? AND is_current=1", (framework_id,)).fetchone()
    return row[0] if row else None


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
if PAGE == "Dashboard":
    import pandas as pd
    import altair as alt
    c = conn
    n = lambda s: c.execute(s).fetchone()[0]
    controls = n("SELECT COUNT(*) FROM controls")
    frameworks = n("SELECT COUNT(*) FROM frameworks")
    baselines_n = n("SELECT COUNT(*) FROM baselines")
    ccis = n("SELECT COUNT(*) FROM cci_items")
    stigs = n("SELECT COUNT(*) FROM stigs")
    systems = c.execute("SELECT COUNT(*) FROM systems WHERE tenant_id=?", (TENANT,)).fetchone()[0]
    review = n("SELECT COUNT(*) FROM implemented_requirements WHERE needs_review=1")
    stig_rules = n("SELECT COUNT(*) FROM stig_rules")

    st.markdown("""
    <style>
      .cf-head{display:flex;align-items:center;gap:14px;margin:2px 0 18px}
      .cf-logo{width:44px;height:44px;border-radius:13px;display:flex;align-items:center;justify-content:center;
               font-size:24px;background:linear-gradient(135deg,#4f46e5,#22d3ee);box-shadow:0 6px 18px rgba(79,70,229,.45)}
      .cf-title{font-size:22px;font-weight:800;color:#f3f5fb;line-height:1}
      .cf-tag{font-size:12px;color:#8b93a7;margin-top:3px}
      .cf-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin:6px 0 20px}
      .cf-card{border-radius:16px;padding:16px 18px;color:#fff;box-shadow:0 8px 24px rgba(0,0,0,.35)}
      .cf-card .ico{font-size:18px;opacity:.92}
      .cf-card .num{font-size:30px;font-weight:800;line-height:1.05;margin-top:8px}
      .cf-card .lbl{font-size:13px;font-weight:700;opacity:.97;margin-top:2px}
      .cf-card .sub{font-size:11px;opacity:.82;margin-top:1px}
      .cf-panel{background:#141a2c;border:1px solid #243049;border-radius:16px;padding:14px 16px;margin-bottom:14px}
      .cf-panel h4{margin:0 0 8px 0;font-size:14px;color:#cdd5e6}
      .cf-row{display:flex;justify-content:space-between;align-items:center;padding:7px 0;
              border-bottom:1px solid #232b42;font-size:13px;color:#cdd5e6}
      .cf-row:last-child{border-bottom:none}
      .pill{padding:2px 10px;border-radius:999px;font-size:11px;font-weight:700}
      .ok{background:#15331f;color:#56d98a}.no{background:#3a1d1d;color:#f87171}
      .warn{background:#3a2c12;color:#f5b13d}
      .cf-big{font-size:26px;font-weight:800;color:#eef1f8}
      .bar{height:9px;background:#243049;border-radius:999px;margin:9px 0 5px;overflow:hidden}
      .barfill{height:9px;background:linear-gradient(90deg,#7c5cff,#22d3ee);border-radius:999px}
      .act{display:flex;gap:9px;align-items:center;padding:6px 0;font-size:12.5px;color:#cdd5e6}
      .act .dot{width:7px;height:7px;border-radius:50%;background:#7c5cff;flex:none}
      .act .t{color:#7f8aa3;margin-left:auto;font-size:11px}
    </style>""", unsafe_allow_html=True)

    if BRAND.get("logo_blob"):
        import base64 as _b64
        _src = f'data:{BRAND.get("logo_mime") or "image/png"};base64,' + \
               _b64.b64encode(BRAND["logo_blob"]).decode()
        _logo = (f'<img src="{_src}" style="width:46px;height:46px;border-radius:12px;'
                 f'object-fit:cover;box-shadow:0 6px 18px rgba(0,0,0,.4)"/>')
    else:
        _logo = (f'<div class="cf-logo" style="background:linear-gradient(135deg,'
                 f'{BRAND["brand_color"]},{BRAND["accent_color"]})">{BRAND["logo"]}</div>')
    st.markdown(
        f'<div class="cf-head">{_logo}'
        f'<div><div class="cf-title">{BRAND["name"]}</div>'
        f'<div class="cf-tag">RMF authorization & continuous-monitoring · '
        f'powered by ComplyForge · DRAFT outputs require human review</div></div></div>',
        unsafe_allow_html=True)

    def card(grad, ico, num, lbl, sub):
        return (f'<div class="cf-card" style="background:linear-gradient(135deg,{grad})">'
                f'<div class="ico">{ico}</div><div class="num">{num:,}</div>'
                f'<div class="lbl">{lbl}</div><div class="sub">{sub}</div></div>')

    cards = [
        card("#4f46e5,#7c3aed", "📚", controls, "Controls", "NIST 800-53 Rev 5"),
        card("#7c3aed,#a855f7", "🗂️", frameworks, "Frameworks", "across 14 kinds"),
        card("#2563eb,#06b6d4", "🎯", baselines_n, "Baselines", "Low / Mod / High"),
        card("#0891b2,#22d3ee", "🔗", ccis, "CCIs", "DISA → 800-53"),
        card("#db2777,#f472b6", "🛡️", stigs, "STIGs", f"{stig_rules:,} rules"),
        card("#d97706,#f59e0b", "📝", review, "Awaiting review", "human gate"),
    ]
    st.markdown('<div class="cf-grid">' + "".join(cards) + "</div>", unsafe_allow_html=True)

    left, right = st.columns([2, 1], gap="large")
    with left:
        cv = c.execute("SELECT catalog_version_id FROM catalog_versions "
                       "WHERE framework_id='nist_800_53' AND is_current=1").fetchone()
        st.markdown("#### NIST 800-53 controls by family")
        if cv:
            fam = c.execute("SELECT family, COUNT(*) n FROM controls WHERE catalog_version_id=? "
                            "GROUP BY family ORDER BY n DESC", (cv[0],)).fetchall()
            df = pd.DataFrame(fam, columns=["family", "controls"]).set_index("family")
            st.bar_chart(df, color="#7c5cff", height=300)
        else:
            st.info("Load 800-53 below to populate.")
        if stig_rules:
            st.markdown("#### STIG rules by severity")
            sev = c.execute("SELECT cat, COUNT(*) n FROM stig_rules GROUP BY cat "
                            "ORDER BY cat").fetchall()
            st.bar_chart(pd.DataFrame(sev, columns=["severity", "rules"]).set_index("severity"),
                         color="#ec4899", height=220)

    with right:
        def row(label, ok, val):
            pill = "ok" if ok else "no"
            txt = val if val else ("loaded" if ok else "not loaded")
            return f'<div class="cf-row"><span>{label}</span><span class="pill {pill}">{txt}</span></div>'
        st.markdown('<div class="cf-panel"><h4>📡 Data sources</h4>'
                    + row("NIST 800-53", controls > 0, f"{controls:,} controls")
                    + row("800-53B baselines", baselines_n > 0, f"{baselines_n} loaded")
                    + row("DISA CCIs", ccis > 0, f"{ccis:,}")
                    + row("STIGs", stigs > 0, f"{stigs} loaded")
                    + "</div>", unsafe_allow_html=True)
        # recent activity
        acts = c.execute(
            """SELECT label, ts FROM (
                 SELECT 'Control '||upper(ir.control_id) label, ir.updated_at ts
                   FROM implemented_requirements ir JOIN systems s ON s.system_id=ir.system_id
                  WHERE ir.updated_at IS NOT NULL AND s.tenant_id=:t
                 UNION ALL SELECT 'STIG: '||title, loaded_at FROM stigs
                 UNION ALL SELECT 'System: '||name, created_at FROM systems WHERE tenant_id=:t
               ) WHERE ts IS NOT NULL ORDER BY ts DESC LIMIT 6""", {"t": TENANT}).fetchall()
        if acts:
            rows = "".join(f'<div class="act"><span class="dot"></span>'
                           f'<span>{a[0][:34]}</span><span class="t">{(a[1] or "")[:10]}</span></div>'
                           for a in acts)
            st.markdown(f'<div class="cf-panel"><h4>🕑 Recent activity</h4>{rows}</div>',
                        unsafe_allow_html=True)

    # ---- System focus: coverage + posture + STIG findings ----
    sys_rows = c.execute("SELECT system_id, name, impact_level FROM systems WHERE tenant_id=? "
                         "ORDER BY name", (TENANT,)).fetchall()
    if sys_rows:
        st.markdown("#### System posture")
        names = {r["name"]: r for r in sys_rows}
        pick = st.selectbox("System", list(names), label_visibility="collapsed")
        srow = names[pick]
        sid, impact = srow["system_id"], (srow["impact_level"] or "moderate").lower()
        bid = f"nist_800_53b@{impact}"
        total = n(f"SELECT COUNT(*) FROM baseline_controls WHERE baseline_id='{bid}'") or 0
        documented = c.execute(
            """SELECT COUNT(DISTINCT ir.control_id) FROM implemented_requirements ir
                 JOIN baseline_controls b ON b.control_id=ir.control_id AND b.baseline_id=?
                WHERE ir.system_id=?""", (bid, sid)).fetchone()[0]
        satisfied = c.execute(
            """SELECT COUNT(*) FROM implemented_requirements
                WHERE system_id=? AND status='implemented' AND needs_review=0""", (sid,)).fetchone()[0]
        other = c.execute("SELECT COUNT(*) FROM implemented_requirements WHERE system_id=?",
                          (sid,)).fetchone()[0] - satisfied
        open_findings = 0
        try:
            from comply_forge import stig as _stig
            open_findings = len(_stig.open_findings(conn, sid))
        except Exception:
            pass

        k1, k2, k3 = st.columns(3, gap="large")
        with k1:
            pct = int(round(100 * documented / total)) if total else 0
            st.markdown(
                f'<div class="cf-panel"><h4>🎯 Baseline coverage ({impact.title()})</h4>'
                f'<div class="cf-big">{documented}/{total}</div>'
                f'<div class="bar"><div class="barfill" style="width:{pct}%"></div></div>'
                f'<div class="cf-tag">{pct}% of the {impact.title()} baseline documented</div></div>',
                unsafe_allow_html=True)
        with k2:
            st.markdown('<div class="cf-panel"><h4>✅ Assessment posture</h4></div>', unsafe_allow_html=True)
            if satisfied or other:
                dfp = pd.DataFrame({"result": ["Satisfied", "Other-than-satisfied"],
                                    "n": [satisfied, other]})
                donut = (alt.Chart(dfp).mark_arc(innerRadius=42).encode(
                    theta="n:Q",
                    color=alt.Color("result:N", scale=alt.Scale(range=["#56d98a", "#f5b13d"]),
                                    legend=alt.Legend(orient="bottom", title=None)))
                    .properties(height=190))
                st.altair_chart(donut, width="stretch")
            else:
                st.caption("No control responses yet.")
        with k3:
            st.markdown(
                f'<div class="cf-panel"><h4>🔧 Open STIG findings</h4>'
                f'<div class="cf-big">{open_findings}</div>'
                f'<div class="cf-tag">Open items flow into the POA&amp;M and SAR</div>'
                f'<div class="cf-row" style="margin-top:8px"><span>Applied STIGs</span>'
                f'<span class="pill ok">{len(_stig.assigned_stigs(conn, sid)) if stigs else 0}</span></div></div>',
                unsafe_allow_html=True)

    with st.expander("⚙️ Initialize / update reference data", expanded=controls == 0):
        from comply_forge import bootstrap as _bs
        st.caption("Downloads NIST 800-53, 800-53B baselines, and the DISA CCI list. "
                   "Run once on a fresh deployment. STIGs load via the STIG Library page.")
        b1, b2, b3, b4 = st.columns(4)
        if b1.button("Load 800-53"):
            with st.spinner("Downloading 800-53…"): st.success(_bs.init_catalog(conn))
        if b2.button("Load baselines"):
            with st.spinner("Downloading baselines…"): st.success(_bs.init_baselines(conn))
        if b3.button("Load CCIs"):
            with st.spinner("Downloading CCIs…"): st.success(_bs.init_cci(conn))
        if b4.button("Load all", type="primary"):
            with st.spinner("Downloading all reference data…"):
                for msg in _bs.init_all(conn): st.success(msg)


# --------------------------------------------------------------------------- #
# Categorize (CIA)
# --------------------------------------------------------------------------- #
elif PAGE == "Categorize (CIA)":
    st.title("Categorize → Select Controls (CIA triad)")
    st.caption("FIPS 199 categorization → control baseline, like the RMF KS explorer.")
    c1, c2, c3, c4 = st.columns(4)
    conf = c1.selectbox("Confidentiality", ["low", "moderate", "high"], index=1)
    integ = c2.selectbox("Integrity", ["low", "moderate", "high"], index=1)
    avail = c3.selectbox("Availability", ["low", "moderate", "high"], index=0)
    model = c4.selectbox("Model", ["high_water_mark", "per_cia"],
                         help="high_water_mark = FIPS 200 / 800-53B. per_cia = CNSSI 1253 (DoD/NSS).")
    if st.button("Generate control set", type="primary"):
        try:
            sel = categorize.categorize_and_select(
                conn, confidentiality=conf, integrity=integ, availability=avail, model=model)
            audit("categorize", f"{model}", f"C={conf} I={integ} A={avail} -> {sel['control_count']}")
            st.success(f"{sel['control_count']} controls selected "
                       f"({sel.get('overall_impact', model)}). {sel['note']}")
            ids = sel["control_ids"]
            st.code(", ".join(ids[:60]) + (" ..." if len(ids) > 60 else ""))
            st.download_button("Download control list (.txt)",
                               "\n".join(ids), file_name=f"baseline_{model}.txt")
        except ValueError as e:
            st.error(f"{e}")
            st.info("Load baselines: `python3 scripts/fetch_baselines.py` "
                    "(per_cia also needs CNSSI 1253 data).")

    if model == "per_cia":
        st.caption("`per_cia` uses 800-53B-derived per-objective sets (approximation) "
                   "unless authoritative CNSSI 1253 data is loaded below.")
    with st.expander("Load authoritative CNSSI 1253 per-CIA baselines"):
        from comply_forge import baselines as _bl
        st.caption("Upload a table with columns **dimension** (C/I/A), **impact** "
                   "(low/moderate/high), **control_id** — e.g. exported from the CNSS "
                   "1253 baseline workbook. This overrides the approximation.")
        if conn.execute("SELECT COUNT(*) FROM baselines WHERE dimension IN ('C','I','A')").fetchone()[0]:
            st.download_button("⬇ Download current per-CIA baselines (editable CSV)",
                               _bl.export_cnssi1253_csv(conn),
                               file_name="cnssi_1253_per_cia.csv", mime="text/csv")
            st.caption("Edit this against the CNSSI 1253 PDF and re-upload to make it authoritative.")
        cf = st.file_uploader("CNSSI 1253 baselines (.csv or .xlsx)", type=["csv", "xlsx"])
        if cf and st.button("Load CNSSI 1253 data", type="primary"):
            try:
                if cf.name.lower().endswith(".xlsx"):
                    import openpyxl, io as _io
                    ws = openpyxl.load_workbook(_io.BytesIO(cf.getvalue()), data_only=True).worksheets[0]
                    it = ws.iter_rows(values_only=True)
                    hdr = [str(h).strip().lower() if h else "" for h in next(it)]
                    rows = [dict(zip(hdr, r)) for r in it]
                else:
                    import csv as _csv, io as _io
                    rows = list(_csv.DictReader(_io.StringIO(cf.getvalue().decode())))
                nrows = _bl.load_cnssi1253_rows(conn, rows)
                audit("load_cnssi1253", cf.name, f"{nrows} rows")
                st.success(f"Loaded {nrows} authoritative CNSSI 1253 rows — per_cia now uses them.")
            except Exception as e:
                st.error(f"Could not load: {e}")


# --------------------------------------------------------------------------- #
# Controls browser
# --------------------------------------------------------------------------- #
elif PAGE == "Controls":
    st.title("Controls")
    cvs = [r[0] for r in conn.execute(
        "SELECT catalog_version_id FROM catalog_versions ORDER BY catalog_version_id")]
    if not cvs:
        st.warning("No catalogs loaded.")
    else:
        cv = st.selectbox("Catalog version", cvs)
        q = st.text_input("Search (full-text)", placeholder="e.g. multi-factor authentication")
        if q:
            rows = conn.execute(
                """SELECT c.control_id, c.title FROM controls_fts f JOIN controls c ON c.rowid=f.rowid
                    WHERE controls_fts MATCH ? AND c.catalog_version_id=? LIMIT 100""",
                (q, cv)).fetchall()
        else:
            rows = conn.execute(
                "SELECT control_id, title FROM controls WHERE catalog_version_id=? "
                "ORDER BY control_id LIMIT 100", (cv,)).fetchall()
        st.caption(f"{len(rows)} shown")
        st.dataframe([{"control": r[0].upper(), "title": r[1]} for r in rows],
                     width='stretch', hide_index=True)


# --------------------------------------------------------------------------- #
# Draft Control Response
# --------------------------------------------------------------------------- #
elif PAGE == "Draft Control Response":
    st.title("Draft Control Response")
    cv = _current_catalog()
    if not cv:
        st.warning("Load 800-53 first (`scripts/fetch_catalogs.py`).")
    else:
        with st.expander("Systems", expanded=True):
            sys_rows = conn.execute("SELECT system_id, name FROM systems WHERE tenant_id=? ORDER BY name", (TENANT,)).fetchall()
            with st.form("new_sys"):
                sn = st.text_input("New system name")
                sd = st.text_area("Description (tech stack, IdP, logging, etc.)")
                si = st.selectbox("Impact", ["low", "moderate", "high"], index=1)
                if st.form_submit_button("Add system") and sn:
                    conn.execute("INSERT INTO systems (system_id,name,description,impact_level,created_at,tenant_id) "
                                 "VALUES (?,?,?,?,?,?)", (str(uuid.uuid4()), sn, sd, si, _now(), TENANT))
                    conn.commit(); st.rerun()
        sys_rows = conn.execute("SELECT system_id, name FROM systems WHERE tenant_id=? ORDER BY name", (TENANT,)).fetchall()
        if not sys_rows:
            st.info("Add a system above to begin.")
        else:
            sysmap = {f"{n}": sid for sid, n in sys_rows}
            sysname = st.selectbox("System", list(sysmap))
            control_id = st.text_input("Control ID", value="ac-2").strip().lower()
            if st.button("Draft response", type="primary"):
                try:
                    ans = control_responder.draft_response(
                        conn, system_id=sysmap[sysname], catalog_version_id=cv,
                        control_id=control_id, provider=prov)
                    audit("draft_control", control_id.upper(), sysname)
                    st.success(f"Drafted {control_id.upper()} — needs_review "
                               f"(provider={ans['provenance']['llm_provider']})")
                    st.text_area("Draft statement", ans["statement"], height=240)
                    st.json({k: ans["provenance"][k] for k in
                             ("llm_provider", "llm_model", "evidence_used")})
                except ValueError as e:
                    st.error(str(e))


# --------------------------------------------------------------------------- #
# Authorization Package (SSP + POA&M)
# --------------------------------------------------------------------------- #
elif PAGE == "Authorization Package":
    from comply_forge import ssp as _ssp, poam as _poam, sar as _sar
    st.title("Authorization Package — SSP & POA&M")
    cv = _current_catalog()
    sys_rows = conn.execute("SELECT system_id, name FROM systems WHERE tenant_id=? ORDER BY name", (TENANT,)).fetchall()
    if not cv:
        st.warning("Load 800-53 first.")
    elif not sys_rows:
        st.info("Add a system on the Draft Control Response page, then draft some controls.")
    else:
        sysmap = {n: sid for sid, n in sys_rows}
        sysname = st.selectbox("System", list(sysmap))
        apkg_agency = st.text_input("Agency / company (document is for)", value="",
                                    placeholder="client agency — printed as 'Prepared for'")
        sid = sysmap[sysname]
        total = conn.execute("SELECT COUNT(*) FROM implemented_requirements "
                             "WHERE system_id=? AND catalog_version_id=?", (sid, cv)).fetchone()[0]
        unrev = conn.execute("SELECT COUNT(*) FROM implemented_requirements "
                             "WHERE system_id=? AND catalog_version_id=? AND needs_review=1",
                             (sid, cv)).fetchone()[0]
        c1, c2 = st.columns(2)
        c1.metric("Controls documented", total)
        c2.metric("Awaiting review", unrev)
        if unrev:
            st.warning(f"{unrev} control response(s) still need review — the SSP will flag them "
                       "as DRAFT. Approve in the Review Queue before authorization.")

        st.subheader("System Security Plan (SSP)")
        a, b = st.columns(2)
        if a.button("Generate OSCAL SSP", key="ssp_oscal"):
            doc = _ssp.build_oscal_ssp(conn, system_id=sid, catalog_version_id=cv)
            audit("generate_ssp", sysname)
            ok, msg = _ssp.validate_oscal(doc)
            st.caption(msg)
            import json as _json
            a.download_button("⬇ SSP (OSCAL JSON)", _json.dumps(doc, indent=2),
                              file_name=f"{sysname}_SSP.json", mime="application/json", key="dl_ssp_j")
        if b.button("Generate Word SSP", key="ssp_word"):
            out = _ssp.write_word_ssp(conn, system_id=sid, catalog_version_id=cv,
                                      path=Path(tempfile.mkdtemp()) / f"{sysname}_SSP.docx",
                                      prepared_by=BRAND["name"], prepared_for=apkg_agency, brand_color=BRAND["brand_color"])
            b.download_button("⬇ SSP (.docx)", _read_bytes(out), file_name=out.name, key="dl_ssp_w",
                              mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

        st.subheader("Security Assessment Report (SAR)")
        summ = _sar.assess(conn, sid, cv)
        st.caption(f"{summ['assessed']} assessed · {summ['satisfied']} satisfied · "
                   f"{summ['other_than_satisfied']} other-than-satisfied · "
                   f"{len(summ['findings'])} finding(s)")
        f1, f2 = st.columns(2)
        if f1.button("Generate OSCAL SAR", key="sar_oscal"):
            import json as _json
            doc = _sar.build_oscal_sar(conn, system_id=sid, catalog_version_id=cv)
            audit("generate_sar", sysname)
            f1.download_button("⬇ SAR (OSCAL JSON)", _json.dumps(doc, indent=2),
                               file_name=f"{sysname}_SAR.json", mime="application/json", key="dl_sar_j")
        if f2.button("Generate Word SAR", key="sar_word"):
            out = _sar.write_word_sar(conn, system_id=sid, catalog_version_id=cv,
                                      path=Path(tempfile.mkdtemp()) / f"{sysname}_SAR.docx",
                                      prepared_by=BRAND["name"], prepared_for=apkg_agency, brand_color=BRAND["brand_color"])
            f2.download_button("⬇ SAR (.docx)", _read_bytes(out), file_name=out.name, key="dl_sar_w",
                               mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

        st.subheader("Plan of Action & Milestones (POA&M)")
        open_n = len(_poam.open_items(conn, sid, cv))
        st.caption(f"{open_n} open item(s) (controls not fully implemented or awaiting review).")
        d, e = st.columns(2)
        if d.button("Generate OSCAL POA&M", key="poam_oscal"):
            doc = _poam.build_oscal_poam(conn, system_id=sid, catalog_version_id=cv)
            audit("generate_poam", sysname)
            import json as _json
            d.download_button("⬇ POA&M (OSCAL JSON)", _json.dumps(doc, indent=2),
                              file_name=f"{sysname}_POAM.json", mime="application/json", key="dl_poam_j")
        if e.button("Generate Word POA&M", key="poam_word"):
            out = _poam.write_word_poam(conn, system_id=sid, catalog_version_id=cv,
                                        path=Path(tempfile.mkdtemp()) / f"{sysname}_POAM.docx",
                                        prepared_by=BRAND["name"], prepared_for=apkg_agency, brand_color=BRAND["brand_color"])
            e.download_button("⬇ POA&M (.docx)", _read_bytes(out), file_name=out.name, key="dl_poam_w",
                              mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


# --------------------------------------------------------------------------- #
# FISCAM Test Plan
# --------------------------------------------------------------------------- #
elif PAGE == "FISCAM Test Plan":
    st.title("FISCAM 2024 Test Plan")
    tab_gen, tab_upload = st.tabs(["Generate (built-in template)", "Upload your template"])

    with tab_gen:
        cid = st.text_input("Control ID", value="", placeholder="e.g. SM.04.02.01")
        title = st.text_input("Control title", value="", placeholder="e.g. Security Categorization")
        text = st.text_area("Control text", value="",
                            placeholder="Paste the FISCAM 2024 control activity language here.")
        with st.expander("Client / engagement details (optional — left blank otherwise)"):
            oc1, oc2 = st.columns(2)
            jd = oc1.text_input("J/D Code / MSC", value="")
            au = oc2.text_input("Assessable Unit", value="")
            aum = oc1.text_input("Assessable Unit Manager", value="")
            loc = oc2.text_input("Location of executed control", value="")
        org_ctx = {k: v for k, v in {"jd_code": jd, "assessable_unit": au,
                   "assessable_unit_manager": aum, "location_executed": loc}.items() if v}
        if st.button("Generate test plan", type="primary", key="gen_tp", disabled=not cid):
            plan = fiscam_test_plan.draft_test_plan(
                conn, control_id=cid, control_title=title, control_text=text,
                org_context=org_ctx, provider=prov)
            out = Path(tempfile.mkdtemp()) / f"Enterprise_{cid.replace('.', '_')}.xlsx"
            fiscam_test_plan.write_workbook(plan, out)
            audit("generate_test_plan", cid)
            st.success(f"Generated ({plan.provenance['field_source']}, needs_review).")
            st.download_button("Download .xlsx", _read_bytes(out),
                               file_name=out.name,
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    with tab_upload:
        st.caption("Upload your workbook; ComplyForge fills it in place, preserving "
                   "your formatting, tabs, and any human-entered content.")
        up = st.file_uploader("Template (.xlsx)", type=["xlsx"])
        ucid = st.text_input("Control ID", value="SM.04.02.05", key="ucid")
        utitle = st.text_input("Control title", value="Account Management", key="utitle")
        utext = st.text_area("Control text", value="", key="utext")
        if up and st.button("Fill my template", type="primary", key="fill_tp"):
            tmp_in = Path(tempfile.mkdtemp()) / up.name
            tmp_in.write_bytes(up.getvalue())
            spec = template_engine.learn_template(tmp_in)
            st.write(f"Learned: {spec.summary()}")
            plan = fiscam_test_plan.draft_test_plan(
                conn, control_id=ucid, control_title=utitle, control_text=utext, provider=prov)
            out = Path(tempfile.mkdtemp()) / f"Filled_{ucid.replace('.', '_')}.xlsx"
            template_engine.fill_template(tmp_in, plan.fields, out, spec=spec)
            st.success("Filled your template (human sign-off fields left blank).")
            st.download_button("Download filled .xlsx", _read_bytes(out), file_name=out.name,
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# --------------------------------------------------------------------------- #
# Control Family Plans
# --------------------------------------------------------------------------- #
elif PAGE == "Control Family Plans":
    st.title("RMF Control Family Plans")
    cv = _current_catalog()
    if not cv:
        st.warning("Load 800-53 first.")
    else:
        c1, c2 = st.columns(2)
        system = c1.text_input("Entity / system name", value="",
                               placeholder="e.g. the information system being documented")
        agency = c2.text_input("Agency / company (document is for)", value="",
                               placeholder="e.g. the client agency or organization")
        c3, c4 = st.columns(2)
        enclave = c3.text_input("Enclave / authorization boundary (optional)", value="")
        baseline = c4.selectbox("Baseline", ["Low", "Moderate", "High"], index=1)
        if not system:
            st.caption("Tip: enter the entity/system name — the document will otherwise show "
                       "a [Entity / System Name] placeholder.")
        fams = {f"{k.upper()} — {v}": k for k, v in control_family_plan.FAMILY_TITLES.items()}
        choice = st.multiselect("Families", list(fams), default=["CA — Assessment, Authorization, and Monitoring"])
        tmpl_up = st.file_uploader("Optional: clone styling from your .docx (fonts, headers/footers, "
                                   "CUI markings, heading styles)", type=["docx"])
        tmpl_path = None
        if tmpl_up is not None:
            tmpl_path = Path(tempfile.mkdtemp()) / tmpl_up.name
            tmpl_path.write_bytes(tmpl_up.getvalue())
            st.caption(f"Styling will be cloned from {tmpl_up.name}")
        if st.button("Generate plan(s)", type="primary"):
            profile = control_family_plan.SystemProfile(
                system_name=system, enclave=enclave, agency=agency,
                baseline=baseline, catalog_version_id=cv)
            for label in choice:
                fam = fams[label]
                try:
                    out = control_family_plan.generate_family_plan(
                        conn, family=fam, profile=profile, provider=prov,
                        template_path=tmpl_path,
                        out_path=Path(tempfile.mkdtemp()) /
                        f"{(system or agency or 'Entity').replace(' ', '_')}_{fam.upper()}_Plan.docx")
                    audit("generate_family_plan", fam.upper(), system)
                    n = len(control_family_plan._family_controls(
                        conn, fam, cv, f"nist_800_53b@{baseline.lower()}"))
                    st.download_button(f"⬇ {fam.upper()} plan ({n} controls)",
                                       _read_bytes(out), file_name=out.name, key=f"dl_{fam}",
                                       mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
                except ValueError as e:
                    st.error(f"{fam.upper()}: {e}")


# --------------------------------------------------------------------------- #
# STIG Library (ingest, reader, apply-to-system)
# --------------------------------------------------------------------------- #
elif PAGE == "STIG Library":
    from comply_forge import stig as _stig
    import io as _io, zipfile as _zip, urllib.request as _url
    st.title("STIG Library")
    if conn.execute("SELECT COUNT(*) FROM cci_items").fetchone()[0] == 0:
        st.warning("Load DISA CCIs first (`python3 scripts/fetch_cci.py`) so STIG rules "
                   "map to 800-53 controls.")

    with st.expander("Ingest a STIG (DISA .zip URL, or upload .zip/.xml)"):
        c1, c2 = st.columns([3, 1])
        url = c1.text_input("STIG .zip URL",
                            placeholder="https://dl.dod.cyber.mil/.../U_..._STIG.zip")
        up = st.file_uploader("…or upload STIG (.zip or XCCDF .xml)", type=["zip", "xml"])
        if c2.button("Ingest", type="primary"):
            try:
                if up is not None:
                    raw = up.getvalue()
                    if up.name.lower().endswith(".zip"):
                        z = _zip.ZipFile(_io.BytesIO(raw))
                        xmls = [n for n in z.namelist() if n.lower().endswith(".xml")]
                        manual = [n for n in xmls if "xccdf" in n.lower()] or xmls
                        xml = z.read(manual[0]); src = up.name
                    else:
                        xml = raw; src = up.name
                elif url:
                    data = _url.urlopen(_url.Request(url, headers={"User-Agent": "Mozilla/5.0"}),
                                        timeout=180).read()
                    z = _zip.ZipFile(_io.BytesIO(data))
                    xmls = [n for n in z.namelist() if n.lower().endswith(".xml")]
                    manual = [n for n in xmls if "xccdf" in n.lower()] or xmls
                    xml = z.read(manual[0]); src = url
                else:
                    st.stop()
                res = _stig.load_stig_xccdf(conn, xml, source=src)
                audit("ingest_stig", res["stig_id"], f"{res['rules']} rules")
                st.success(f"Loaded {res['title']} ({res['version']}): {res['rules']} rules, "
                           f"{res['controls_mapped']} 800-53 controls mapped.")
            except Exception as e:
                st.error(f"Ingest failed: {e}")

    stigs = _stig.list_stigs(conn)
    if not stigs:
        st.info("No STIGs loaded yet.")
    else:
        smap = {f"{s['title']} ({s['version']}) — {s['rules']} rules": s["stig_id"] for s in stigs}
        label = st.selectbox("STIG", list(smap))
        sid = smap[label]
        cov = _stig.coverage(conn, sid)
        rules_all = _stig.rules(conn, sid)
        m1, m2, m3 = st.columns(3)
        m1.metric("Rules", len(rules_all))
        m2.metric("CAT I (high)", sum(1 for r in rules_all if r["severity"] == "high"))
        m3.metric("800-53 controls covered", len(cov))

        tab_read, tab_apply = st.tabs(["📖 STIG Reader", "🧩 Apply to System"])
        with tab_read:
            sev = st.selectbox("Severity", ["all", "high", "medium", "low"],
                               format_func=lambda s: {"all": "All", "high": "CAT I",
                                                      "medium": "CAT II", "low": "CAT III"}[s])
            rs = _stig.rules(conn, sid, None if sev == "all" else sev)
            st.caption(f"{len(rs)} rules")
            pick = st.selectbox("Rule", [f"{r['stig_ref']} [{r['cat']}] {r['title'][:60]}" for r in rs]) if rs else None
            if pick:
                rid = rs[[f"{r['stig_ref']} [{r['cat']}] {r['title'][:60]}" for r in rs].index(pick)]["rule_id"]
                full = _stig.rule(conn, sid, rid)
                st.markdown(f"**{full['stig_ref']} — {full['title']}**  · {full['cat']} · "
                            f"`{full['group_id']}` / `{full['rule_id']}`")
                st.markdown(f"**Mapped 800-53:** {', '.join(c.upper() for c in full['controls']) or '—'}  "
                            f"·  **CCIs:** {', '.join(full['ccis']) or '—'}")
                if full["discussion"]:
                    st.markdown("**Discussion**"); st.write(full["discussion"])
                st.markdown("**Check**"); st.code(full["check_content"] or "—")
                st.markdown("**Fix**"); st.code(full["fix_text"] or "—")
        with tab_apply:
            sys_rows = conn.execute("SELECT system_id, name FROM systems WHERE tenant_id=? ORDER BY name", (TENANT,)).fetchall()
            if not sys_rows:
                st.info("Add a system (Draft Control Response page) to assign STIGs.")
            else:
                sysmap = {n: i for i, n in sys_rows}
                sn = st.selectbox("System", list(sysmap), key="stig_sys")
                system_id = sysmap[sn]
                if st.button("Apply STIG to system", type="primary"):
                    _stig.assign(conn, system_id, sid)
                    audit("apply_stig", sid, sn)
                    st.success(f"Applied {label} to {sn}. Covers {len(cov)} 800-53 controls.")
                st.caption("Currently applied: " +
                           (", ".join(_stig.assigned_stigs(conn, system_id)) or "none"))

                st.markdown("**Mark findings** — set each rule's status; `Open` items flow into the POA&M.")
                fsev = st.selectbox("Filter severity", ["high", "medium", "low", "all"],
                                    format_func=lambda s: {"high": "CAT I", "medium": "CAT II",
                                                           "low": "CAT III", "all": "All"}[s],
                                    key="find_sev")
                cur = _stig.finding_status(conn, system_id, sid)
                rs = _stig.rules(conn, sid, None if fsev == "all" else fsev)
                table = [{"rule": r["stig_ref"], "cat": r["cat"], "title": r["title"][:70],
                          "status": cur.get(r["rule_id"], "Not_Reviewed"), "_rid": r["rule_id"]}
                         for r in rs]
                edited = st.data_editor(
                    table, hide_index=True, key=f"ed_{sid}_{fsev}",
                    column_config={
                        "status": st.column_config.SelectboxColumn(
                            "Status", options=["Not_Reviewed", "Open", "NotAFinding", "Not_Applicable"]),
                        "_rid": None},
                    width='stretch')
                if st.button("Save findings", key="save_find"):
                    for row in edited:
                        _stig.set_finding(conn, system_id, sid, row["_rid"], row["status"])
                    st.success("Saved. Open items will appear in the POA&M (Authorization Package).")
                n_open = len(_stig.open_findings(conn, system_id))
                st.caption(f"Open findings for {sn} (all assigned STIGs): {n_open} → POA&M")
                st.download_button("⬇ STIG checklist (.csv)", _stig.checklist_csv(conn, sid),
                                   file_name=f"{sid.replace('@','_')}_checklist.csv", mime="text/csv")


# --------------------------------------------------------------------------- #
# Review Queue
# --------------------------------------------------------------------------- #
elif PAGE == "Review Queue":
    st.title("Review Queue")
    st.caption("Human authorization gate — drafts are not counted until approved.")
    rows = conn.execute(
        """SELECT ir.ir_id, s.name, ir.control_id, ir.origin, ir.statement
             FROM implemented_requirements ir JOIN systems s ON s.system_id=ir.system_id
            WHERE ir.needs_review=1 AND s.tenant_id=? ORDER BY ir.updated_at DESC LIMIT 50""",
        (TENANT,)).fetchall()
    if not rows:
        st.success("Nothing awaiting review.")
    for r in rows:
        with st.expander(f"{(r[1] or '—')} · {r[2].upper()} · {r[3]}"):
            st.text_area("Statement", r[4] or "", height=160, key=f"txt_{r[0]}")
            reviewer = st.text_input("Reviewer", key=f"rev_{r[0]}")
            if st.button("Approve", key=f"ap_{r[0]}") and reviewer:
                conn.execute("UPDATE implemented_requirements SET needs_review=0, "
                             "reviewed_by=?, reviewed_at=?, status='implemented' WHERE ir_id=?",
                             (reviewer, _now(), r[0]))
                conn.commit()
                audit("approve_control", (r[2] or "").upper(), f"reviewer={reviewer}")
                st.rerun()


# --------------------------------------------------------------------------- #
# Admin (tenant + user management; admins only)
# --------------------------------------------------------------------------- #
elif PAGE == "Admin":
    st.title("Admin")
    if USER["role"] != "admin":
        st.error("Admins only.")
        st.stop()

    st.subheader("Branding (this organization)")
    st.caption("White-label the dashboard header for " + BRAND["name"] + ".")
    with st.form("branding"):
        bc1, bc2, bc3 = st.columns([2, 1, 1])
        bname = bc1.text_input("Organization name", value=BRAND["name"])
        blogo = bc2.text_input("Logo (emoji/char)", value=BRAND["logo"], max_chars=2)
        bcol = bc3.color_picker("Brand color", value=BRAND["brand_color"])
        bacc = bc3.color_picker("Accent color", value=BRAND["accent_color"])
        st.markdown(
            f'<div style="display:flex;gap:12px;align-items:center;margin:6px 0">'
            f'<div style="width:42px;height:42px;border-radius:12px;display:flex;align-items:center;'
            f'justify-content:center;font-size:22px;background:linear-gradient(135deg,{bcol},{bacc})">'
            f'{blogo}</div><b style="font-size:18px">{bname}</b></div>', unsafe_allow_html=True)
        if st.form_submit_button("Save branding", type="primary"):
            _auth.update_branding(conn, TENANT, name=bname, logo=blogo,
                                  brand_color=bcol, accent_color=bacc)
            audit("update_branding", bname)
            st.success("Branding saved."); st.rerun()

    lc1, lc2 = st.columns([2, 1])
    logo_up = lc1.file_uploader("Logo image (PNG/JPG) — overrides the emoji",
                                type=["png", "jpg", "jpeg"])
    if BRAND.get("logo_blob"):
        lc2.image(BRAND["logo_blob"], width=80)
    bcol1, bcol2 = st.columns(2)
    if logo_up and bcol1.button("Set logo image", type="primary"):
        mime = "image/png" if logo_up.name.lower().endswith("png") else "image/jpeg"
        _auth.set_logo_image(conn, TENANT, logo_up.getvalue(), mime)
        audit("set_logo_image", logo_up.name); st.success("Logo image set."); st.rerun()
    if BRAND.get("logo_blob") and bcol2.button("Remove logo image"):
        _auth.set_logo_image(conn, TENANT, None); audit("remove_logo_image"); st.rerun()

    st.subheader("Organizations (tenants)")
    tl = _auth.list_tenants(conn)
    st.dataframe([{"tenant_id": t["tenant_id"], "name": t["name"],
                   "systems": conn.execute("SELECT COUNT(*) FROM systems WHERE tenant_id=?",
                                           (t["tenant_id"],)).fetchone()[0],
                   "users": conn.execute("SELECT COUNT(*) FROM users WHERE tenant_id=?",
                                         (t["tenant_id"],)).fetchone()[0]} for t in tl],
                 width="stretch", hide_index=True)
    with st.form("new_tenant"):
        tn = st.text_input("New organization name")
        if st.form_submit_button("Create organization") and tn.strip():
            _auth.create_tenant(conn, tn.strip()); audit("create_tenant", tn.strip())
            st.success(f"Created {tn}"); st.rerun()

    st.subheader("Users")
    urows = conn.execute("SELECT u.username, t.name tenant, u.role FROM users u "
                         "JOIN tenants t ON t.tenant_id=u.tenant_id ORDER BY t.name, u.username").fetchall()
    st.dataframe([{"username": r[0], "organization": r[1], "role": r[2]} for r in urows],
                 width="stretch", hide_index=True)
    with st.form("new_user"):
        nu = st.text_input("Username")
        npw = st.text_input("Temporary password", type="password")
        tmap = {t["name"]: t["tenant_id"] for t in tl}
        torg = st.selectbox("Organization", list(tmap)) if tmap else None
        nrole = st.selectbox("Role", ["user", "admin"])
        if st.form_submit_button("Create user") and nu.strip() and npw and torg:
            try:
                _auth.create_user(conn, nu.strip(), npw, tmap[torg], nrole)
                audit("create_user", nu.strip(), f"org={torg} role={nrole}")
                st.success(f"Created user {nu}"); st.rerun()
            except Exception as e:
                st.error(f"Could not create user: {e}")

    st.subheader("Change my password")
    with st.form("chpw"):
        p1 = st.text_input("New password", type="password")
        p2 = st.text_input("Confirm", type="password")
        if st.form_submit_button("Update password"):
            if p1 and p1 == p2:
                conn.execute("UPDATE users SET password_hash=? WHERE username=?",
                             (_auth.hash_password(p1), USER["username"]))
                conn.commit(); audit("change_password", USER["username"])
                st.success("Password updated.")
            else:
                st.error("Passwords don't match.")

    st.subheader("Audit log")
    log_rows = _audit.recent(conn, TENANT, limit=200)
    if log_rows:
        st.dataframe([{"timestamp": r["ts"][:19].replace("T", " "), "user": r["username"],
                       "action": r["action"], "target": r["target"], "detail": r["detail"]}
                      for r in log_rows], width="stretch", hide_index=True)
        st.download_button("⬇ Export audit log (.csv)", _audit.export_csv(conn, TENANT),
                           file_name=f"{USER['tenant_name']}_audit_log.csv", mime="text/csv")
    else:
        st.caption("No audit entries yet.")

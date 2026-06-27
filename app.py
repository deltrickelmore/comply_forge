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
st.sidebar.title("🛡️ ComplyForge")
st.sidebar.caption("Framework-agnostic GRC artifact engine")
PAGE = st.sidebar.radio("Navigate", [
    "Dashboard", "Categorize (CIA)", "Controls",
    "Draft Control Response", "Authorization Package",
    "FISCAM Test Plan", "Control Family Plans",
    "STIG Library", "Review Queue",
])
st.sidebar.divider()
st.sidebar.markdown(f"**LLM provider:** `{prov.name}`")
if prov.name == "fake":
    st.sidebar.info("No key set → deterministic drafts. Set `ANTHROPIC_API_KEY` "
                    "(dev) or `COMPLYFORGE_LLM_PROVIDER=bedrock` (CUI).")
st.sidebar.caption("All drafts require human review — nothing is auto-attested.")


def _current_catalog(framework_id="nist_800_53") -> str | None:
    row = conn.execute("SELECT catalog_version_id FROM catalog_versions "
                       "WHERE framework_id=? AND is_current=1", (framework_id,)).fetchone()
    return row[0] if row else None


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
if PAGE == "Dashboard":
    st.title("Dashboard")
    c = conn
    counts = {
        "Frameworks registered": c.execute("SELECT COUNT(*) FROM frameworks").fetchone()[0],
        "Catalog versions": c.execute("SELECT COUNT(*) FROM catalog_versions").fetchone()[0],
        "Controls loaded": c.execute("SELECT COUNT(*) FROM controls").fetchone()[0],
        "Baselines": c.execute("SELECT COUNT(*) FROM baselines").fetchone()[0],
        "CCIs": c.execute("SELECT COUNT(*) FROM cci_items").fetchone()[0],
        "STIGs": c.execute("SELECT COUNT(*) FROM stigs").fetchone()[0],
        "Systems": c.execute("SELECT COUNT(*) FROM systems").fetchone()[0],
        "Answers needing review": c.execute(
            "SELECT COUNT(*) FROM implemented_requirements WHERE needs_review=1").fetchone()[0],
    }
    cols = st.columns(len(counts))
    for col, (k, v) in zip(cols, counts.items()):
        col.metric(k, v)

    st.subheader("Framework library by kind")
    rows = c.execute("SELECT kind, COUNT(*) n FROM frameworks GROUP BY kind ORDER BY n DESC").fetchall()
    st.dataframe([{"kind": r[0], "count": r[1], "what it is": FRAMEWORK_KINDS.get(r[0], "")}
                  for r in rows], width='stretch', hide_index=True)

    with st.expander("⚙️ Initialize / update reference data",
                     expanded=counts["Controls loaded"] == 0):
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
            sys_rows = conn.execute("SELECT system_id, name FROM systems ORDER BY name").fetchall()
            with st.form("new_sys"):
                sn = st.text_input("New system name")
                sd = st.text_area("Description (tech stack, IdP, logging, etc.)")
                si = st.selectbox("Impact", ["low", "moderate", "high"], index=1)
                if st.form_submit_button("Add system") and sn:
                    conn.execute("INSERT INTO systems (system_id,name,description,impact_level,created_at) "
                                 "VALUES (?,?,?,?,?)", (str(uuid.uuid4()), sn, sd, si, _now()))
                    conn.commit(); st.rerun()
        sys_rows = conn.execute("SELECT system_id, name FROM systems ORDER BY name").fetchall()
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
    from comply_forge import ssp as _ssp, poam as _poam
    st.title("Authorization Package — SSP & POA&M")
    cv = _current_catalog()
    sys_rows = conn.execute("SELECT system_id, name FROM systems ORDER BY name").fetchall()
    if not cv:
        st.warning("Load 800-53 first.")
    elif not sys_rows:
        st.info("Add a system on the Draft Control Response page, then draft some controls.")
    else:
        sysmap = {n: sid for sid, n in sys_rows}
        sysname = st.selectbox("System", list(sysmap))
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
            ok, msg = _ssp.validate_oscal(doc)
            st.caption(msg)
            import json as _json
            a.download_button("⬇ SSP (OSCAL JSON)", _json.dumps(doc, indent=2),
                              file_name=f"{sysname}_SSP.json", mime="application/json", key="dl_ssp_j")
        if b.button("Generate Word SSP", key="ssp_word"):
            out = _ssp.write_word_ssp(conn, system_id=sid, catalog_version_id=cv,
                                      path=Path(tempfile.mkdtemp()) / f"{sysname}_SSP.docx")
            b.download_button("⬇ SSP (.docx)", _read_bytes(out), file_name=out.name, key="dl_ssp_w",
                              mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

        st.subheader("Plan of Action & Milestones (POA&M)")
        open_n = len(_poam.open_items(conn, sid, cv))
        st.caption(f"{open_n} open item(s) (controls not fully implemented or awaiting review).")
        d, e = st.columns(2)
        if d.button("Generate OSCAL POA&M", key="poam_oscal"):
            doc = _poam.build_oscal_poam(conn, system_id=sid, catalog_version_id=cv)
            import json as _json
            d.download_button("⬇ POA&M (OSCAL JSON)", _json.dumps(doc, indent=2),
                              file_name=f"{sysname}_POAM.json", mime="application/json", key="dl_poam_j")
        if e.button("Generate Word POA&M", key="poam_word"):
            out = _poam.write_word_poam(conn, system_id=sid, catalog_version_id=cv,
                                        path=Path(tempfile.mkdtemp()) / f"{sysname}_POAM.docx")
            e.download_button("⬇ POA&M (.docx)", _read_bytes(out), file_name=out.name, key="dl_poam_w",
                              mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


# --------------------------------------------------------------------------- #
# FISCAM Test Plan
# --------------------------------------------------------------------------- #
elif PAGE == "FISCAM Test Plan":
    st.title("FISCAM 2024 Test Plan")
    tab_gen, tab_upload = st.tabs(["Generate (built-in template)", "Upload your template"])

    with tab_gen:
        cid = st.text_input("Control ID", value="SM.04.02.01")
        title = st.text_input("Control title", value="Security Categorization")
        text = st.text_area("Control text",
                            value="The ISSM categorizes the system per FIPS 199 and CNSSI 1253; "
                                  "the AO approves; reviewed annually.")
        if st.button("Generate test plan", type="primary", key="gen_tp"):
            plan = fiscam_test_plan.draft_test_plan(
                conn, control_id=cid, control_title=title, control_text=text, provider=prov)
            out = Path(tempfile.mkdtemp()) / f"Enterprise_{cid.replace('.', '_')}.xlsx"
            fiscam_test_plan.write_workbook(plan, out)
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
        c1, c2, c3 = st.columns(3)
        system = c1.text_input("System name", value="AFSV-AFLIS")
        enclave = c2.text_input("Enclave", value="AFSV-BAN")
        baseline = c3.selectbox("Baseline", ["Low", "Moderate", "High"], index=1)
        fams = {f"{k.upper()} — {v}": k for k, v in control_family_plan.FAMILY_TITLES.items()}
        choice = st.multiselect("Families", list(fams), default=["CA — Assessment, Authorization, and Monitoring"])
        if st.button("Generate plan(s)", type="primary"):
            profile = control_family_plan.SystemProfile(
                system_name=system, enclave=enclave, baseline=baseline, catalog_version_id=cv)
            for label in choice:
                fam = fams[label]
                try:
                    out = control_family_plan.generate_family_plan(
                        conn, family=fam, profile=profile, provider=prov,
                        out_path=Path(tempfile.mkdtemp()) / f"{system}_{fam.upper()}_Plan.docx")
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
            sys_rows = conn.execute("SELECT system_id, name FROM systems ORDER BY name").fetchall()
            if not sys_rows:
                st.info("Add a system (Draft Control Response page) to assign STIGs.")
            else:
                sysmap = {n: i for i, n in sys_rows}
                sn = st.selectbox("System", list(sysmap), key="stig_sys")
                system_id = sysmap[sn]
                if st.button("Apply STIG to system", type="primary"):
                    _stig.assign(conn, system_id, sid)
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
             FROM implemented_requirements ir LEFT JOIN systems s ON s.system_id=ir.system_id
            WHERE ir.needs_review=1 ORDER BY ir.updated_at DESC LIMIT 50""").fetchall()
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
                conn.commit(); st.rerun()

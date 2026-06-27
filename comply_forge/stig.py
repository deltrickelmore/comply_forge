"""
DISA STIG ingester + reader + apply-to-system.

* load_stig_xccdf()  -- parse an XCCDF benchmark (STIG) into stigs/stig_rules/
                        stig_rule_cci.
* rules() / rule()   -- STIG reader: browse rules, filter by severity.
* rule_controls()    -- 800-53 controls a rule maps to (via CCI -> cci_control_map).
* coverage()         -- distinct controls a whole STIG covers.
* assign() / checklist_rows() -- apply a STIG to a system + produce a checklist.

Severity -> category: high=CAT I, medium=CAT II, low=CAT III.
STIG rules reference CCIs; load the CCI list (scripts/fetch_cci.py) first so control
mapping works.
"""

from __future__ import annotations

import datetime as _dt
import re
import xml.etree.ElementTree as ET
from pathlib import Path

_CAT = {"high": "CAT I", "medium": "CAT II", "low": "CAT III"}
_VULN_RE = re.compile(r"<VulnDiscussion>(.*?)</VulnDiscussion>", re.S)


def _lt(tag: str) -> str:
    return tag.split("}")[-1]


def _slug(title: str, version_label: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", (title or "stig").lower()).strip("_")
    base = base.replace("security_technical_implementation_guide", "").strip("_")
    return f"{base}@{version_label}" if version_label else base


def _discussion(desc: str | None) -> str:
    if not desc:
        return ""
    m = _VULN_RE.search(desc)
    return (m.group(1) if m else desc).strip()


def load_stig_xccdf(conn, xml: bytes | str | Path, source: str = "") -> dict:
    """Parse and load one XCCDF STIG. Returns {stig_id, title, rules, mapped}."""
    if isinstance(xml, (str, Path)) and Path(str(xml)).exists():
        source = source or str(xml)
        data = Path(xml).read_bytes()
    elif isinstance(xml, bytes):
        data = xml
    else:
        data = str(xml).encode()
    root = ET.fromstring(data)

    title = version = release = ""
    bench_id = root.get("id") or ""
    for ch in root:
        t = _lt(ch.tag)
        if t == "title":
            title = (ch.text or "").strip()
        elif t == "version":
            version = (ch.text or "").strip()
        elif t == "plain-text" and ch.get("id") == "release-info":
            release = (ch.text or "").strip()

    # version label like "V2R3" from release-info ("Release: 3 ...") + version
    relnum = re.search(r"Release:\s*(\d+)", release)
    vlabel = f"V{version}R{relnum.group(1)}" if (version and relnum) else (version or "V1")
    stig_id = _slug(title, vlabel)
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()

    conn.execute("DELETE FROM stig_rules WHERE stig_id=?", (stig_id,))
    conn.execute("DELETE FROM stig_rule_cci WHERE stig_id=?", (stig_id,))
    conn.execute(
        """INSERT OR REPLACE INTO stigs
             (stig_id,title,version,release_info,benchmark_id,source,loaded_at)
           VALUES (?,?,?,?,?,?,?)""",
        (stig_id, title, vlabel, release, bench_id, source, now))

    n_rules = n_cci = 0
    for grp in root.iter():
        if _lt(grp.tag) != "Group":
            continue
        gid = grp.get("id")
        rule = next((e for e in grp if _lt(e.tag) == "Rule"), None)
        if rule is None:
            continue
        rid = rule.get("id")
        sev = (rule.get("severity") or "").lower()
        stig_ref = rtitle = disc = check = fix = ""
        ccis: list[str] = []
        for ch in rule:
            t = _lt(ch.tag)
            if t == "version":
                stig_ref = (ch.text or "").strip()
            elif t == "title":
                rtitle = (ch.text or "").strip()
            elif t == "description":
                disc = _discussion(ch.text)
            elif t == "ident" and "cci" in (ch.get("system") or "").lower():
                if ch.text:
                    ccis.append(ch.text.strip())
            elif t == "check":
                cc = next((x for x in ch if _lt(x.tag) == "check-content"), None)
                if cc is not None:
                    check = (cc.text or "").strip()
            elif t == "fixtext":
                fix = (ch.text or "").strip()
        conn.execute(
            """INSERT OR REPLACE INTO stig_rules
                 (stig_id,group_id,rule_id,stig_ref,severity,cat,title,discussion,check_content,fix_text)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (stig_id, gid, rid, stig_ref, sev, _CAT.get(sev, sev),
             rtitle, disc, check, fix))
        n_rules += 1
        for cci in ccis:
            conn.execute("INSERT OR REPLACE INTO stig_rule_cci (stig_id,rule_id,cci) VALUES (?,?,?)",
                         (stig_id, rid, cci))
            n_cci += 1
    conn.commit()
    mapped = len(coverage(conn, stig_id))
    return {"stig_id": stig_id, "title": title, "version": vlabel,
            "rules": n_rules, "cci_refs": n_cci, "controls_mapped": mapped}


# --------------------------------------------------------------------------- #
# Reader
# --------------------------------------------------------------------------- #
def list_stigs(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT s.stig_id, s.title, s.version, s.release_info,
                  (SELECT COUNT(*) FROM stig_rules r WHERE r.stig_id=s.stig_id) AS rules
             FROM stigs s ORDER BY s.title""").fetchall()
    return [dict(r) for r in rows]


def rules(conn, stig_id: str, severity: str | None = None) -> list[dict]:
    q = ("SELECT group_id, rule_id, stig_ref, severity, cat, title FROM stig_rules "
         "WHERE stig_id=?")
    args = [stig_id]
    if severity:
        q += " AND severity=?"; args.append(severity)
    q += " ORDER BY stig_ref"
    return [dict(r) for r in conn.execute(q, args).fetchall()]


def rule(conn, stig_id: str, rule_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM stig_rules WHERE stig_id=? AND rule_id=?",
                     (stig_id, rule_id)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["ccis"] = [x[0] for x in conn.execute(
        "SELECT cci FROM stig_rule_cci WHERE stig_id=? AND rule_id=?", (stig_id, rule_id))]
    d["controls"] = rule_controls(conn, stig_id, rule_id)
    return d


def rule_controls(conn, stig_id: str, rule_id: str) -> list[str]:
    rows = conn.execute(
        """SELECT DISTINCT m.control_id FROM stig_rule_cci rc
             JOIN cci_control_map m ON m.cci_id=rc.cci
            WHERE rc.stig_id=? AND rc.rule_id=? ORDER BY m.control_id""",
        (stig_id, rule_id)).fetchall()
    return [r[0] for r in rows]


def coverage(conn, stig_id: str) -> list[str]:
    """Distinct 800-53 controls covered by a STIG (via CCIs)."""
    rows = conn.execute(
        """SELECT DISTINCT m.control_id FROM stig_rule_cci rc
             JOIN cci_control_map m ON m.cci_id=rc.cci
            WHERE rc.stig_id=? ORDER BY m.control_id""", (stig_id,)).fetchall()
    return [r[0] for r in rows]


# --------------------------------------------------------------------------- #
# Apply to system
# --------------------------------------------------------------------------- #
def assign(conn, system_id: str, stig_id: str) -> None:
    conn.execute("INSERT OR REPLACE INTO stig_assignments (system_id,stig_id,assigned_at) "
                 "VALUES (?,?,?)", (system_id, stig_id,
                                    _dt.datetime.now(_dt.timezone.utc).isoformat()))
    conn.commit()


def assigned_stigs(conn, system_id: str) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT stig_id FROM stig_assignments WHERE system_id=?", (system_id,))]


def checklist_rows(conn, stig_id: str) -> list[dict]:
    """A reviewable checklist: each rule + status (default Not_Reviewed) + mapped controls."""
    out = []
    for r in rules(conn, stig_id):
        ctrls = rule_controls(conn, stig_id, r["rule_id"])
        out.append({"stig_id": r["stig_ref"], "group_id": r["group_id"],
                    "rule_id": r["rule_id"], "severity": r["cat"], "title": r["title"],
                    "status": "Not_Reviewed", "controls": ", ".join(c.upper() for c in ctrls)})
    return out


_FINDING_STATUSES = ("Not_Reviewed", "Open", "NotAFinding", "Not_Applicable")


def set_finding(conn, system_id: str, stig_id: str, rule_id: str,
                status: str, comments: str = "") -> None:
    conn.execute(
        """INSERT OR REPLACE INTO stig_findings
             (system_id,stig_id,rule_id,status,comments,updated_at) VALUES (?,?,?,?,?,?)""",
        (system_id, stig_id, rule_id, status, comments,
         _dt.datetime.now(_dt.timezone.utc).isoformat()))
    conn.commit()


def finding_status(conn, system_id: str, stig_id: str) -> dict[str, str]:
    """rule_id -> status for a system's STIG (defaults Not_Reviewed if unset)."""
    rows = conn.execute(
        "SELECT rule_id, status FROM stig_findings WHERE system_id=? AND stig_id=?",
        (system_id, stig_id)).fetchall()
    return {r[0]: r[1] for r in rows}


def open_findings(conn, system_id: str) -> list[dict]:
    """Open STIG findings across all STIGs assigned to a system, as POA&M-ready dicts."""
    out: list[dict] = []
    for sid in assigned_stigs(conn, system_id):
        status = finding_status(conn, system_id, sid)
        for r in rules(conn, sid):
            if status.get(r["rule_id"]) == "Open":
                ctrls = rule_controls(conn, sid, r["rule_id"])
                out.append({
                    "control_id": ctrls[0] if ctrls else "",
                    "status": "open",
                    "weakness": f"STIG finding [{r['cat']}] {r['stig_ref']}: {r['title']}",
                    "statement": f"Source STIG: {sid}; rule {r['rule_id']}; "
                                 f"maps to {', '.join(c.upper() for c in ctrls) or 'n/a'}.",
                })
    return out


def checklist_csv(conn, stig_id: str) -> str:
    import csv, io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["stig_id", "group_id", "rule_id", "severity",
                                        "title", "status", "controls"])
    w.writeheader()
    for row in checklist_rows(conn, stig_id):
        w.writerow(row)
    return buf.getvalue()

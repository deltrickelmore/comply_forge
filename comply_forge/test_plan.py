"""
Test-plan / Security Assessment Plan (SAP) generator -- the "generate test plans"
button. Produces assessment procedures per control in the NIST 800-53A shape:

  control -> assessment objective(s)
          -> determination statements ("Determine that ...")
          -> assessment methods: EXAMINE / INTERVIEW / TEST
          -> assessment objects (what to examine / who to interview / what to test)

If the loaded catalog already has 800-53A assessment objectives, those are used
verbatim (authoritative). Otherwise the LLM drafts determination statements and
concrete test steps from the control text. As everywhere: LLM writes prose,
code owns structure, output is needs_review for a human assessor.
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Any

from .llm_provider import LLMProvider, get_provider

_METHODS = ("examine", "interview", "test")

SYSTEM_PROMPT = """\
You are a security control assessor writing assessment procedures for an RMF
Security Assessment Plan, in the style of NIST SP 800-53A.

For the given control, produce:
1. "determine_statements": a list of specific, independently checkable
   "Determine that ..." statements covering each part of the control.
2. For each assessment method (examine, interview, test): the concrete objects to
   assess -- documents/mechanisms to EXAMINE, roles to INTERVIEW, mechanisms/
   activities to TEST. Omit a method only if it genuinely does not apply.

Be specific and verifiable. Do not attest to compliance -- you are writing the
test plan an assessor will execute. Return ONLY valid JSON of the form:
{"determine_statements": [...],
 "examine": [...], "interview": [...], "test": [...]}
"""


def _parse_json(text: str) -> dict[str, Any]:
    """Tolerant JSON extraction from an LLM response."""
    text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}


def _control(conn, catalog_version_id: str, control_id: str) -> dict:
    row = conn.execute(
        "SELECT control_id, title, statement, guidance FROM controls "
        "WHERE catalog_version_id=? AND control_id=?",
        (catalog_version_id, control_id)).fetchone()
    if row is None:
        raise ValueError(f"control {control_id} not in {catalog_version_id}")
    return dict(row)


def generate_test_plan_for_control(
    conn, *, catalog_version_id: str, control_id: str,
    provider: LLMProvider | None = None,
) -> dict:
    """Assessment procedures for one control (SAP entry). needs_review always."""
    provider = provider or get_provider()
    ctrl = _control(conn, catalog_version_id, control_id)

    prompt = (
        f"Control {ctrl['control_id'].upper()} -- {ctrl['title']}\n"
        f"Requirement: {ctrl['statement']}\n"
        f"Guidance: {ctrl['guidance']}\n\n"
        "Write the assessment procedures as JSON."
    )
    result = provider.complete(system=SYSTEM_PROMPT, prompt=prompt, effort="high")
    parsed = _parse_json(result.text)

    procedures = {
        "control_id": ctrl["control_id"],
        "title": ctrl["title"],
        "determine_statements": parsed.get("determine_statements", []),
        "methods": {m: parsed.get(m, []) for m in _METHODS if parsed.get(m)},
        # if the LLM returned nothing parseable, keep the raw draft for the human
        "raw": None if parsed else result.text,
        "needs_review": True,
        "provenance": result.provenance(),
    }
    return procedures


def generate_sap(
    conn, *, system_id: str, catalog_version_id: str, control_ids: list[str],
    provider: LLMProvider | None = None,
) -> dict:
    """Assemble a full Security Assessment Plan over a list of controls."""
    provider = provider or get_provider()
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    sysrow = conn.execute("SELECT name FROM systems WHERE system_id=?",
                          (system_id,)).fetchone()
    procedures = [
        generate_test_plan_for_control(
            conn, catalog_version_id=catalog_version_id,
            control_id=cid, provider=provider)
        for cid in control_ids
    ]
    return {
        "artifact": "Security Assessment Plan (SAP)",
        "system_id": system_id,
        "system_name": sysrow["name"] if sysrow else system_id,
        "catalog_version_id": catalog_version_id,
        "generated_at": now,
        "control_count": len(procedures),
        "procedures": procedures,
        "guardrail": "Draft test plan for human assessor review. Not an attestation.",
    }

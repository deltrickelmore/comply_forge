"""
Control responder -- the core LLM capability.

Drafts an implementation statement for a control, given the control text and a
description of the system. Guardrails (non-negotiable, see docs/oscal_model.md):

  * LLM writes PROSE ONLY. Code owns control_id, status, structure, provenance.
  * Every draft is needs_review=1 with reviewed_by=NULL. A human authorizes.
  * Provenance (model, provider, evidence used) is stamped on every answer.

Retrieval hook: `evidence_fn(control)` lets you inject the org's own prior
answers / policies / Component Definitions so the draft cites how THIS org already
does things (RAG). The default pulls the org's existing reviewed answers for the
same control_id from other systems -- a cheap, real reuse signal.
"""

from __future__ import annotations

import datetime as _dt
import json
import uuid
from typing import Callable

from .llm_provider import LLMProvider, get_provider

SYSTEM_PROMPT = """\
You are a security controls author helping prepare an RMF System Security Plan.
Given a control and a system description, write a concise, concrete implementation
statement describing HOW the system satisfies the control.

Rules:
- Write only the implementation statement prose. No headings, no preamble, no IDs.
- Be specific and verifiable; reference the mechanisms/tools named in the system
  description. Do not invent capabilities the system description does not support.
- If the system description lacks information needed for part of the control, state
  plainly what additional information or implementation is required.
- Do NOT claim compliance or attest. You are drafting for human review.
"""


def _default_evidence(conn, control_id: str, system_id: str) -> list[str]:
    """Reuse signal: reviewed answers for the same control on OTHER systems."""
    rows = conn.execute(
        """SELECT statement FROM implemented_requirements
            WHERE control_id=? AND system_id<>? AND reviewed_by IS NOT NULL
              AND statement IS NOT NULL
            ORDER BY updated_at DESC LIMIT 3""",
        (control_id, system_id),
    ).fetchall()
    return [r[0] for r in rows if r[0]]


def _control_text(conn, catalog_version_id: str, control_id: str) -> dict:
    row = conn.execute(
        """SELECT control_id, title, statement, guidance FROM controls
            WHERE catalog_version_id=? AND control_id=?""",
        (catalog_version_id, control_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"control {control_id} not in {catalog_version_id}")
    return dict(row)


def draft_response(
    conn,
    *,
    system_id: str,
    catalog_version_id: str,
    control_id: str,
    provider: LLMProvider | None = None,
    evidence_fn: Callable[[object, str, str], list[str]] | None = None,
    persist: bool = True,
) -> dict:
    """Draft (and optionally persist) an implementation statement. Returns the
    answer dict including provenance. Always needs_review=1 until a human signs off."""
    provider = provider or get_provider()
    ctrl = _control_text(conn, catalog_version_id, control_id)

    sysrow = conn.execute(
        "SELECT name, description, impact_level FROM systems WHERE system_id=?",
        (system_id,)).fetchone()
    if sysrow is None:
        raise ValueError(f"no such system: {system_id}")
    system_desc = f"{sysrow['name']}: {sysrow['description'] or ''} (impact: {sysrow['impact_level'] or 'n/a'})"

    evidence = (evidence_fn or _default_evidence)(conn, control_id, system_id)
    evidence_block = ("\n\nThe organization has previously documented for this control:\n"
                      + "\n---\n".join(evidence)) if evidence else ""

    prompt = (
        f"Control {ctrl['control_id'].upper()} -- {ctrl['title']}\n"
        f"Requirement: {ctrl['statement']}\n"
        f"Guidance: {ctrl['guidance']}\n\n"
        f"System: {system_desc}"
        f"{evidence_block}\n\n"
        "Write the implementation statement."
    )

    result = provider.complete(system=SYSTEM_PROMPT, prompt=prompt)

    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    provenance = {**result.provenance(), "evidence_used": len(evidence), "drafted_at": now}
    answer = {
        "ir_id": str(uuid.uuid4()),
        "system_id": system_id,
        "catalog_version_id": catalog_version_id,
        "control_id": control_id,
        "status": "partial",          # human upgrades to implemented on review
        "statement": result.text,
        "origin": "llm",
        "needs_review": 1,
        "reviewed_by": None,
        "provenance": provenance,
        "updated_at": now,
    }

    if persist:
        conn.execute(
            """INSERT INTO implemented_requirements
                 (ir_id, system_id, catalog_version_id, control_id, status, statement,
                  origin, needs_review, oscal_json, updated_at)
               VALUES (?,?,?,?,?,?,?,1,?,?)""",
            (answer["ir_id"], system_id, catalog_version_id, control_id,
             answer["status"], answer["statement"], "llm",
             json.dumps({"provenance": provenance}), now),
        )
        conn.commit()
    return answer

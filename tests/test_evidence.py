"""Evidence artifacts: CRUD, counts, OSCAL links, and SSP citation."""
from __future__ import annotations

from comply_forge.llm_provider import FakeProvider


def test_add_and_list(conn):
    from comply_forge import evidence
    evidence.add_link(conn, system_id="sys-test", control_id="AC-2",
                      title="MFA wiki", uri="https://wiki/mfa", added_by="alice")
    evidence.add_note(conn, system_id="sys-test", control_id="ac-2",
                      title="Quarterly review note", description="reviewed 2026-Q1")
    items = evidence.list_for_control(conn, "sys-test", "ac-2")  # case-insensitive
    assert len(items) == 2
    assert {i["kind"] for i in items} == {"link", "note"}


def test_file_blob_roundtrip(conn):
    from comply_forge import evidence
    eid = evidence.add_file(conn, system_id="sys-test", control_id="ac-2",
                            title="screenshot", filename="mfa.png",
                            blob=b"\x89PNG fake", mime="image/png")
    fn, mime, blob = evidence.get_blob(conn, eid)
    assert fn == "mfa.png" and mime == "image/png" and blob == b"\x89PNG fake"


def test_counts_and_delete(conn):
    from comply_forge import evidence
    evidence.add_note(conn, system_id="sys-test", control_id="ac-2", title="n1")
    eid = evidence.add_note(conn, system_id="sys-test", control_id="ac-2", title="n2")
    assert evidence.counts_for_system(conn, "sys-test")["ac-2"] == 2
    evidence.delete(conn, eid)
    assert evidence.counts_for_system(conn, "sys-test")["ac-2"] == 1


def test_oscal_links_shape(conn):
    from comply_forge import evidence
    evidence.add_link(conn, system_id="sys-test", control_id="ac-2",
                      title="policy", uri="https://policy")
    links = evidence.oscal_links(conn, "sys-test", "ac-2")
    assert links and links[0]["rel"] == "evidence" and links[0]["href"] == "https://policy"


def test_ssp_cites_evidence_and_validates(conn):
    from comply_forge import evidence, control_responder, ssp, validation
    control_responder.draft_response(
        conn, system_id="sys-test", catalog_version_id="nist_800_53@rev5",
        control_id="ac-2", provider=FakeProvider(), persist=True)
    evidence.add_link(conn, system_id="sys-test", control_id="ac-2",
                      title="MFA config", uri="https://wiki/mfa")
    doc = ssp.build_oscal_ssp(conn, system_id="sys-test",
                              catalog_version_id="nist_800_53@rev5")
    irs = doc["system-security-plan"]["control-implementation"]["implemented-requirements"]
    ac2 = next(i for i in irs if i["control-id"] == "ac-2")
    assert ac2["by-components"][0]["links"][0]["rel"] == "evidence"
    ok, errors = validation.validate(doc)
    assert ok, errors[:3]

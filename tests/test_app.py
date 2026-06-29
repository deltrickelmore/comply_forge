"""Integration: every Streamlit page renders without raising (against a seeded DB)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

PAGES = [
    "Dashboard", "Categorize (CIA)", "Controls", "Crosswalk & Coverage",
    "Draft Control Response", "Authorization Package", "FISCAM Test Plan",
    "Control Family Plans", "STIG Library", "Review Queue",
    "Continuous Monitoring", "Framework Revisions", "Admin",
]


@pytest.fixture
def at_factory(app_db, monkeypatch):
    monkeypatch.setenv("COMPLYFORGE_DB", str(app_db))
    from streamlit.testing.v1 import AppTest
    from comply_forge.db import connect
    from comply_forge import auth
    tid = auth.ensure_seed(connect(app_db))

    def make(page):
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=90)
        at.session_state["user"] = {
            "username": "admin", "tenant_id": tid,
            "tenant_name": "Demo Org", "role": "admin"}
        at.run()
        # find the page radio and switch
        for r in at.sidebar.radio:
            if page in r.options:
                r.set_value(page).run()
                break
        return at

    return make


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(at_factory, page):
    at = at_factory(page)
    assert not at.exception, f"{page}: {at.exception}"

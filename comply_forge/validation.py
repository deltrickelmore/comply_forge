"""
OSCAL schema validation against the official NIST OSCAL JSON schemas (v1.1.2).

Validates generated SSP / POA&M / SAR documents for structural correctness
(required fields, types, enums, nesting) before platform import (eMASS/Xacta).

NOTE: NIST's schemas use `\\p{L}` Unicode-property regexes that Python's `re`
can't compile, so we strip string `pattern` constraints — token *format* (e.g.
UUID shape) is not checked here, but everything structural is. Schemas are fetched
once from the NIST OSCAL v1.1.2 release and cached under data/oscal_schemas/.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

_DIR = Path(__file__).resolve().parent.parent / "data" / "oscal_schemas"
_REL = "https://github.com/usnistgov/OSCAL/releases/download/v1.1.2/"
_FILES = {
    "ssp": "oscal_ssp_schema.json",
    "poam": "oscal_poam_schema.json",
    "assessment-results": "oscal_assessment-results_schema.json",
}
_ROOT_KEY = {
    "system-security-plan": "ssp",
    "plan-of-action-and-milestones": "poam",
    "assessment-results": "assessment-results",
}


def _strip_patterns(node):
    if isinstance(node, dict):
        node.pop("pattern", None)
        for v in node.values():
            _strip_patterns(v)
    elif isinstance(node, list):
        for v in node:
            _strip_patterns(v)
    return node


def _load_schema(model: str) -> dict:
    fp = _DIR / _FILES[model]
    if not fp.exists():
        _DIR.mkdir(parents=True, exist_ok=True)
        data = urllib.request.urlopen(
            urllib.request.Request(_REL + _FILES[model], headers={"User-Agent": "Mozilla/5.0"}),
            timeout=60).read()
        fp.write_bytes(data)
    return _strip_patterns(json.loads(fp.read_text()))


def detect_model(doc: dict) -> str | None:
    for k, m in _ROOT_KEY.items():
        if k in doc:
            return m
    return None


def validate(doc: dict, model: str | None = None) -> tuple[bool, list[str]]:
    """Return (ok, [issue strings]). ok=True means no structural schema errors."""
    model = model or detect_model(doc)
    if not model:
        return False, ["unrecognized OSCAL document root"]
    try:
        import jsonschema
        from jsonschema.validators import validator_for
    except ImportError:
        return False, ["jsonschema not installed — run: pip install jsonschema"]
    try:
        schema = _load_schema(model)
    except Exception as e:  # network/cache issue
        return False, [f"could not load NIST OSCAL schema ({model}): {e}"]
    validator = validator_for(schema)(schema)
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
    msgs = [(("/".join(map(str, e.path)) or "root") + ": " + e.message) for e in errors]
    return (len(errors) == 0), msgs

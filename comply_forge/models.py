"""
Lightweight internal model for the OSCAL subset ComplyForge needs.

We deliberately do NOT model all of OSCAL here -- the full OSCAL object is kept
verbatim in the DB blob. These dataclasses are the *shredded working view* used
by the loader, crosswalk, and artifact generator. If you later want full typed
OSCAL, add `oscal-pydantic` and parse the blob on demand; nothing here blocks
that.

Stdlib only.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Control:
    control_id: str                 # 'ac-2'
    family: str                     # 'AC'
    title: str
    statement: str = ""             # flattened prose
    guidance: str = ""
    params: list[dict[str, Any]] = field(default_factory=list)
    oscal: dict[str, Any] | None = None   # full verbatim control object

    def content_hash(self) -> str:
        """Stable hash of the substantive content, for revision diffing."""
        blob = "␟".join([self.title or "", self.statement or "", self.guidance or ""])
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass
class CatalogVersion:
    catalog_version_id: str         # 'nist_800_53@rev5'
    framework_id: str               # 'nist_800_53'
    version_label: str              # 'Rev 5'
    oscal_uuid: str | None = None
    source_uri: str | None = None
    published: str | None = None
    controls: list[Control] = field(default_factory=list)


@dataclass
class Framework:
    framework_id: str
    name: str
    authority: str
    kind: str = "control_catalog"   # or 'process_library'
    notes: str = ""


@dataclass
class Mapping:
    src_framework: str
    src_control: str
    dst_framework: str
    dst_control: str
    relation: str                   # equivalent|subset|superset|intersects|related
    authority: str = "manual"
    src_version: str | None = None
    dst_version: str | None = None
    note: str = ""

    def as_row(self) -> dict[str, Any]:
        return asdict(self)


# Crosswalk relation semantics (also documented in docs/oscal_model.md).
# Directional: relation describes src -> dst.
RELATIONS = {
    "equivalent":  "src and dst require the same thing; answering one satisfies the other.",
    "subset":      "src is fully covered BY dst (dst is broader); src answer partially fills dst.",
    "superset":    "src is broader THAN dst; answering src fully covers dst.",
    "intersects":  "partial overlap; answers inform each other but neither fully satisfies the other.",
    "related":     "topically related, no coverage claim (do not auto-prefill).",
}

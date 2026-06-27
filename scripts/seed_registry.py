"""
Seed the framework registry from data/framework_registry.csv.

This registers all ~150 frameworks the tool is AWARE of -- with their kind,
category, ingest method, and crosswalk-anchor flag. Registration != ingestion:
only `control_catalog` frameworks get full control loading + answers. The rest
are known so the tool can tag artifacts, crosswalk, and reference them.

Run:  python scripts/seed_registry.py
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from comply_forge.db import connect, FRAMEWORK_KINDS

REGISTRY = ROOT / "data" / "framework_registry.csv"


def main() -> None:
    conn = connect()
    rows = list(csv.DictReader(REGISTRY.read_text().splitlines()))

    bad_kinds = {r["kind"] for r in rows} - set(FRAMEWORK_KINDS)
    if bad_kinds:
        raise SystemExit(f"Unknown kind(s) in registry: {sorted(bad_kinds)}")

    conn.executemany(
        """INSERT INTO frameworks
             (framework_id, name, authority, kind, category, ingest_method, anchor, notes)
           VALUES (:framework_id, :name, :authority, :kind, :category,
                   :ingest_method, :anchor, :notes)
           ON CONFLICT(framework_id) DO UPDATE SET
             name=excluded.name, authority=excluded.authority, kind=excluded.kind,
             category=excluded.category, ingest_method=excluded.ingest_method,
             anchor=excluded.anchor, notes=excluded.notes""",
        [{**r, "anchor": int(r.get("anchor") or 0)} for r in rows],
    )
    conn.commit()

    print(f"Seeded {len(rows)} frameworks.\n")
    print("By kind:")
    for r in conn.execute(
        "SELECT kind, COUNT(*) n FROM frameworks GROUP BY kind ORDER BY n DESC"):
        print(f"  {r['kind']:24} {r['n']}")
    print("\nCrosswalk anchors:")
    for r in conn.execute("SELECT framework_id, name FROM frameworks WHERE anchor=1"):
        print(f"  {r['framework_id']:18} {r['name']}")
    print("\nControl catalogs (get full ingestion + answers):")
    cats = conn.execute(
        "SELECT framework_id FROM frameworks WHERE kind='control_catalog' ORDER BY framework_id"
    ).fetchall()
    print("  " + ", ".join(r[0] for r in cats))


if __name__ == "__main__":
    main()

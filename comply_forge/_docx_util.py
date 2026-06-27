"""Shared python-docx helpers: brand-color a run, and a Prepared by/for line."""

from __future__ import annotations


def color_run(run, hex_color: str) -> None:
    """Apply a #RRGGBB brand color to a run (no-op if blank/invalid)."""
    if not hex_color:
        return
    from docx.shared import RGBColor
    h = hex_color.lstrip("#")
    if len(h) == 6:
        try:
            run.font.color.rgb = RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        except ValueError:
            pass


def prepared_block(doc, prepared_by: str = "", prepared_for: str = "") -> None:
    """Centered 'Prepared by … · Prepared for …' line (only the parts provided)."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    parts = []
    if prepared_for:
        parts.append(f"Prepared for: {prepared_for}")
    if prepared_by:
        parts.append(f"Prepared by: {prepared_by}")
    if parts:
        p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run("   ·   ".join(parts)).italic = True

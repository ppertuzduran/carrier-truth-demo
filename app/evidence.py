"""Render the exact place on the PDF where a value came from (visual provenance)."""
from __future__ import annotations

import io
from pathlib import Path

import pypdfium2 as pdfium
from PIL import ImageDraw

SCALE = 2.0
COLORS = {"VERIFIED": "#16a34a", "VERIFIED_NORMALIZED": "#16a34a", "AMBIGUOUS": "#d97706",
          "PLACEHOLDER": "#d97706", "CONTRADICTED": "#dc2626", "UNVERIFIABLE_SOURCE": "#dc2626", "MISSING": "#dc2626"}


def render_highlight(pdf_path: Path, page: int, bboxes: list[list[float]], status: str) -> bytes:
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        img = pdf[page - 1].render(scale=SCALE).to_pil().convert("RGB")
    finally:
        pdf.close()
    draw = ImageDraw.Draw(img, "RGBA")
    color = COLORS.get(status, "#2563eb")
    rgb = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
    for x0, top, x1, bottom in bboxes:
        pad = 4
        box = [x0 * SCALE - pad, top * SCALE - pad, x1 * SCALE + pad, bottom * SCALE + pad]
        draw.rectangle(box, fill=rgb + (40,), outline=rgb + (255,), width=3)
    # Crop to the region around the evidence so it reads well in the UI.
    ys = [b[1] for b in bboxes] + [b[3] for b in bboxes]
    xs = [b[0] for b in bboxes] + [b[2] for b in bboxes]
    y0 = max(0, int(min(ys) * SCALE) - 110)
    y1 = min(img.height, int(max(ys) * SCALE) + 110)
    x0 = max(0, int(min(xs) * SCALE) - 60)
    x1 = min(img.width, int(max(xs) * SCALE) + 240)
    img = img.crop((x0, y0, x1, y1))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_page(pdf_path: Path, page: int, box: list[float], scale: float = 3.0) -> bytes:
    """Render one page cropped to `box` ([x0, top, x1, bottom] in points) for the document preview."""
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        img = pdf[page - 1].render(scale=scale).to_pil().convert("RGB")
    finally:
        pdf.close()
    x0, top, x1, bottom = box
    img = img.crop((int(x0 * scale), int(top * scale), int(x1 * scale), int(bottom * scale)))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

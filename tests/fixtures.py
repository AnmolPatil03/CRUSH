"""Deterministic synthetic documents for tests and the benchmark corpus.

Requires the dev-only packages ``python-docx`` and ``reportlab``.
"""

from __future__ import annotations

import io
import random

import numpy as np
from PIL import Image

WORDS = ("data compression entropy model context arithmetic coder probability "
         "mixer weight stretch squash table column record archive stream byte "
         "bit order hash predict update learning rate lossless").split()


def prose(n_words: int, seed: int = 1) -> str:
    rng = random.Random(seed)
    out, line = [], []
    for _ in range(n_words):
        line.append(rng.choice(WORDS))
        if len(line) >= rng.randint(8, 14):
            out.append(" ".join(line).capitalize() + ".")
            line = []
    return " ".join(out)


def fixed_width_table(rows: int, seed: int = 2) -> str:
    """A fixed-width text table: the record/column model's favourite input."""
    rng = random.Random(seed)
    lines = [f"{'ID':>6} {'NAME':<12} {'QTY':>6} {'PRICE':>10} {'DATE':<10}"]
    for i in range(rows):
        lines.append(f"{i:>6} {rng.choice(WORDS):<12} {rng.randint(1, 999):>6} "
                     f"{rng.randint(100, 99999) / 100:>10.2f} 2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}")
    return "\n".join(lines) + "\n"


def photo(w: int = 320, h: int = 240, seed: int = 3) -> Image.Image:
    """Smooth, noisy, photo-like RGB image."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w]
    r = 128 + 100 * np.sin(x / 23.0) * np.cos(y / 31.0)
    g = 128 + 90 * np.sin((x + y) / 41.0)
    b = 128 + 80 * np.cos(x / 17.0 - y / 29.0)
    img = np.stack([r, g, b], axis=-1) + rng.normal(0, 6, (h, w, 3))
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")


def chart(w: int = 300, h: int = 200) -> Image.Image:
    """Flat-colour, chart-like image (few colours, hard edges)."""
    img = np.full((h, w, 3), 255, np.uint8)
    for i, col in enumerate([(220, 40, 40), (40, 120, 220), (40, 180, 90), (240, 180, 20)]):
        x0 = 20 + i * 70
        img[h - 20 - (i + 1) * 35:h - 20, x0:x0 + 45] = col
    img[h - 20, 10:w - 10] = 0
    img[10:h - 20, 10] = 0
    return Image.fromarray(img, "RGB")


def image_bytes(img: Image.Image, fmt: str, **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, fmt, **kw)
    return buf.getvalue()


def make_docx(seed: int = 1, with_images: bool = True) -> bytes:
    """DOCX with headings, styled runs, a table and (optionally) JPEG + PNG images."""
    import docx
    from docx.shared import Inches, Pt, RGBColor

    d = docx.Document()
    d.add_heading("CRUSH synthetic report", 0)
    for k in range(4):
        p = d.add_paragraph(prose(120, seed + k))
        run = p.add_run(" Emphasised tail.")
        run.bold, run.italic = True, True
        run.font.size = Pt(13)
        run.font.color.rgb = RGBColor(0x20, 0x80, 0x20)
    rng = random.Random(seed)
    table = d.add_table(rows=12, cols=4)
    table.style = "Light Grid Accent 1"
    for r, row in enumerate(table.rows):
        for cell in row.cells:
            cell.text = "Header" if r == 0 else f"{rng.choice(WORDS)} {rng.randint(0, 999)}"
    if with_images:
        d.add_picture(io.BytesIO(image_bytes(photo(seed=seed), "JPEG", quality=90)), width=Inches(4))
        d.add_picture(io.BytesIO(image_bytes(chart(), "PNG")), width=Inches(3))
    out = io.BytesIO()
    d.save(out)
    return out.getvalue()


def make_pdf(seed: int = 1, with_images: bool = True) -> bytes:
    """PDF with an embedded TrueType font, text, a table-like grid and images."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    font = "Helvetica"
    try:
        import reportlab
        from pathlib import Path
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        ttf = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
        pdfmetrics.registerFont(TTFont("Vera", str(ttf)))
        font = "Vera"
    except Exception:
        pass
    text = prose(600, seed)
    for page in range(2):
        c.setFont(font, 10)
        y = 800
        for i in range(0, 1800, 95):
            c.drawString(40, y, text[page * 1800 + i: page * 1800 + i + 95])
            y -= 14
        for r in range(8):
            for col in range(4):
                c.rect(40 + col * 120, 300 - r * 18, 120, 18)
                c.drawString(44 + col * 120, 305 - r * 18, f"{WORDS[(r * 4 + col) % len(WORDS)]} {r * col}")
        if with_images:
            c.drawImage(ImageReader(io.BytesIO(image_bytes(photo(seed=seed + page), "JPEG", quality=88))),
                        40, 360, width=240, height=180)
            c.drawImage(ImageReader(chart()), 300, 360, width=240, height=160)
        c.showPage()
    c.save()
    return buf.getvalue()

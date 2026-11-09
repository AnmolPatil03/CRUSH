r"""Opt-in "optimised" (lossy) mode: cap the effective resolution of embedded images.

For every image we compute its *effective DPI* from how large it is displayed:

    dpi = pixel_width / display_width_inches

DOCX gives the display size in the drawing's ``<wp:extent cx= cy=>`` (EMU,
914400 per inch); in PDF it is the image's transformed unit square, i.e. the
current transformation matrix at the ``Do`` operator (1/72 inch units).

* photos are resampled (Lanczos) down to ``PHOTO_DPI`` and re-encoded as JPEG;
* screenshots / charts / text images (few distinct colours) are kept lossless
  unless above ``GRAPHIC_DPI``, then resampled and kept as PNG;
* images are never upsampled and the displayed size is never changed;
* an image is replaced only if the new encoding is smaller.

The result is *visually equivalent*, not byte-identical; it is then compressed
losslessly by the normal pipeline, so decompression returns the optimised file.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any

import numpy as np
from PIL import Image

from ..detect import Container, detect

PHOTO_DPI = 200
GRAPHIC_DPI = 300
JPEG_QUALITY = 82
EMU_PER_INCH = 914400


def is_graphic(img: Image.Image) -> bool:
    """Few distinct colours => chart/screenshot/text: keep lossless."""
    thumb = img.convert("RGB")
    thumb.thumbnail((160, 160))
    colours = thumb.getcolors(maxcolors=4096)
    return colours is not None and len(colours) < 1024


def psnr(a: Image.Image, b: Image.Image) -> float:
    x = np.asarray(a.convert("RGB"), dtype=np.float64)
    y = np.asarray(b.convert("RGB").resize(a.size, Image.Resampling.BICUBIC), dtype=np.float64)
    mse = float(((x - y) ** 2).mean())
    return 99.0 if mse == 0 else float(10 * np.log10(255 ** 2 / mse))


def _reencode(data: bytes, display_in: float, prefer_jpeg: bool) -> tuple[bytes, dict[str, Any]] | None:
    """Downsample/re-encode one image; returns (new bytes, info) or None to keep the original."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        return None
    if display_in <= 0:
        return None
    w, h = img.size
    dpi = w / display_in
    graphic = is_graphic(img)
    cap = GRAPHIC_DPI if graphic else PHOTO_DPI
    scale = min(1.0, cap / dpi)
    new = img
    if scale < 0.95:
        new = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    has_alpha = new.mode in ("RGBA", "LA") or (new.mode == "P" and "transparency" in new.info)
    if graphic or (has_alpha and not prefer_jpeg):
        if scale >= 0.95:
            return None  # graphic already within cap: keep it bit-exact
        new.save(buf, "PNG", optimize=True)
        fmt = "png"
    else:
        if has_alpha:
            return None
        new.convert("L" if new.mode == "L" else "RGB").save(buf, "JPEG", quality=JPEG_QUALITY,
                                                            optimize=True, progressive=True)
        fmt = "jpeg"
    out = buf.getvalue()
    if len(out) >= len(data):
        return None
    info = {"from": [w, h], "to": list(new.size), "dpi_before": round(dpi), "dpi_after": round(dpi * new.size[0] / w),
            "bytes_before": len(data), "bytes_after": len(out), "format": fmt,
            "kind": "graphic" if graphic else "photo", "psnr_db": round(psnr(img, Image.open(io.BytesIO(out))), 2)}
    return out, info


# ------------------------------------------------------------------ DOCX

def _docx_display_sizes(z: zipfile.ZipFile) -> dict[str, float]:
    """media path -> largest displayed width in inches over all its uses."""
    sizes: dict[str, float] = {}
    for part in z.namelist():
        if not (part.startswith("word/") and part.endswith(".xml") and "/_rels/" not in part):
            continue
        folder, fname = part.rsplit("/", 1)
        rels_name = f"{folder}/_rels/{fname}.rels"
        if rels_name not in z.namelist():
            continue
        rels = dict(re.findall(rb'Id="([^"]+)"[^>]*?Target="([^"]+)"', z.read(rels_name)))
        rels.update({k: v for v, k in re.findall(rb'Target="([^"]+)"[^>]*?Id="([^"]+)"', z.read(rels_name))})
        xml = z.read(part)
        for m in re.finditer(rb"<wp:extent cx=\"(\d+)\" cy=\"(\d+)\"/>(.*?)</wp:(?:inline|anchor)>", xml, re.S):
            emb = re.search(rb'r:embed="([^"]+)"', m.group(3))
            if not emb or emb.group(1) not in rels:
                continue
            target = rels[emb.group(1)].decode()
            path = target.lstrip("/") if target.startswith("/") else f"{folder}/{target}"
            path = re.sub(r"[^/]+/\.\./", "", path)
            sizes[path] = max(sizes.get(path, 0.0), int(m.group(1)) / EMU_PER_INCH)
    return sizes


def _optimise_docx(data: bytes) -> tuple[bytes, list[dict[str, Any]]]:
    z = zipfile.ZipFile(io.BytesIO(data))
    sizes = _docx_display_sizes(z)
    replaced: dict[str, tuple[str, bytes]] = {}
    infos = []
    for name, inches in sizes.items():
        if name not in z.namelist() or not name.lower().endswith((".png", ".jpg", ".jpeg")):
            continue
        res = _reencode(z.read(name), inches, prefer_jpeg=True)
        if res is None:
            continue
        out, info = res
        new_name = name
        if info["format"] == "jpeg" and name.lower().endswith(".png"):
            new_name = name[:-4] + ".jpeg"
        replaced[name] = (new_name, out)
        infos.append({"image": name, **info})
    if not replaced:
        return data, infos
    renames = {old.split("/")[-1]: new.split("/")[-1] for old, (new, _) in replaced.items() if old != new}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as out:
        for info in z.infolist():
            body = z.read(info)
            name = info.filename
            if name in replaced:
                name, body = replaced[name]
            elif name.endswith(".rels") and renames:
                for old, new in renames.items():
                    body = body.replace(f"media/{old}\"".encode(), f"media/{new}\"".encode())
            elif name == "[Content_Types].xml" and renames and b'Extension="jpeg"' not in body:
                body = body.replace(b"<Default ", b'<Default Extension="jpeg" ContentType="image/jpeg"/><Default ', 1)
            out.writestr(name, body, compress_type=zipfile.ZIP_STORED if name.endswith((".jpeg", ".jpg", ".png"))
                         else zipfile.ZIP_DEFLATED)
    return buf.getvalue(), infos


# ------------------------------------------------------------------ PDF

def _pdf_display_widths(pdf) -> dict[tuple[int, int], float]:
    """(objnum, gen) of image XObject -> largest displayed width in inches."""
    import pikepdf
    widths: dict[tuple[int, int], float] = {}
    for page in pdf.pages:
        xobjs = page.resources.get("/XObject", {})
        stack = [np.eye(3)]
        try:
            ops = pikepdf.parse_content_stream(page)
        except pikepdf.PdfError:
            continue
        for operands, op in ops:
            o = str(op)
            if o == "q":
                stack.append(stack[-1].copy())
            elif o == "Q" and len(stack) > 1:
                stack.pop()
            elif o == "cm" and len(operands) == 6:
                a, b, c, d, e, f = (float(v) for v in operands)
                stack[-1] = np.array([[a, b, 0], [c, d, 0], [e, f, 1]]) @ stack[-1]
            elif o == "Do" and operands:
                x = xobjs.get(str(operands[0]))
                if x is None or x.get("/Subtype") != "/Image" or not x.is_indirect:
                    continue
                m = stack[-1]
                width_pt = float(np.hypot(m[0, 0], m[0, 1]))
                key = (x.objgen[0], x.objgen[1])
                widths[key] = max(widths.get(key, 0.0), width_pt / 72.0)
    return widths


def _optimise_pdf(data: bytes) -> tuple[bytes, list[dict[str, Any]]]:
    import pikepdf
    from pikepdf import Name, PdfImage

    pdf = pikepdf.open(io.BytesIO(data))
    widths = _pdf_display_widths(pdf)
    infos = []
    for (num, gen), inches in widths.items():
        obj = pdf.get_object((num, gen))
        if "/SMask" in obj or "/Mask" in obj or obj.get("/ImageMask", False):
            continue
        try:
            pil = PdfImage(obj).as_pil_image()
        except Exception:
            continue
        if pil.mode not in ("RGB", "L"):
            continue
        src = io.BytesIO()
        pil.save(src, "PNG")
        res = _reencode(src.getvalue(), inches, prefer_jpeg=True)
        if res is None:
            continue
        out, info = res
        if info["bytes_after"] >= len(obj.read_raw_bytes()):
            continue
        new = Image.open(io.BytesIO(out))
        if info["format"] == "jpeg":
            obj.write(out, filter=Name.DCTDecode)
        else:
            obj.write(new.tobytes(), filter=None)
        obj.Width, obj.Height = new.size
        obj.ColorSpace = Name.DeviceGray if new.mode == "L" else Name.DeviceRGB
        obj.BitsPerComponent = 8
        for k in ("/DecodeParms", "/Decode"):
            if k in obj:
                del obj[k]
        infos.append({"image": f"obj {num} {gen}", **info})
    if not infos:
        return data, infos
    buf = io.BytesIO()
    pdf.save(buf, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)
    out = buf.getvalue()
    return (out, infos) if len(out) < len(data) else (data, [])


def optimise(name: str, data: bytes) -> tuple[bytes, dict[str, Any]]:
    """Return the optimised document (or the original if nothing improved) and a report."""
    kind = detect(data)
    try:
        if kind == Container.ZIP:
            out, images = _optimise_docx(data)
        elif kind == Container.PDF:
            out, images = _optimise_pdf(data)
        else:
            out, images = data, []
    except Exception as e:  # a document we cannot rewrite is passed through unchanged
        return data, {"file": name, "changed": False, "error": str(e), "images": []}
    return out, {"file": name, "changed": out != data, "before": len(data), "after": len(out), "images": images}

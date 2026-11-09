r"""PDF container: raw-byte stream scanning (the file is never rewritten).

Every ``<< dict >> stream EOL ... endstream`` is located in the raw bytes.  The
data between ``stream`` and ``endstream`` becomes a recipe; every other byte
(objects, dictionaries, xref tables, trailers, whitespace) is copied verbatim
into the PDF_OBJ stream.  Stream data is routed by its dictionary:

* ``/FlateDecode`` - inflated (with exact deflate rebuild) and routed by content:
  images -> pixel image stream when 8-bit Gray/RGB without predictor, fonts ->
  FONT, ``/XRef`` and ``/ObjStm`` -> PDF_OBJ, everything else (page content,
  form XObjects) -> PDF_OPS;
* ``/DCTDecode`` - a JPEG: JPEG XL lossless transcoding;
* no filter - routed the same way, uncompressed;
* any other filter chain (LZW, ASCII85, CCITT, JBIG2...) - verbatim.  Encrypted
  streams fail to inflate and are therefore kept verbatim too.
"""

from __future__ import annotations

import re

from ..archive.manifest import Builder, Recipe
from ..codecs import StreamType
from .common import is_jpeg, zlib_stream_recipe

_STREAM = re.compile(rb"stream(\r\n|\n|\r)")
_FILTER = re.compile(rb"/Filter\s*(?:\[\s*)?/(\w+)\s*(\])?")
_FILTER_ARRAY = re.compile(rb"/Filter\s*\[([^\]]*)\]")
_INT = {k: re.compile(rb"/" + k + rb"\s+(\d+)(?!\s+\d+\s+R)") for k in (b"Width", b"Height", b"BitsPerComponent", b"Length")}


def _dict_before(data: bytes, pos: int) -> bytes | None:
    """The dictionary text ending right before the ``stream`` keyword at ``pos``."""
    k = pos - 1
    while k >= 0 and data[k] in b" \t\r\n\f\x00":
        k -= 1
    if k < 1 or data[k - 1:k + 1] != b">>":
        return None
    start = max(data.rfind(b" obj", 0, k), data.rfind(b"\nobj", 0, k), data.rfind(b"\robj", 0, k))
    if start < 0 or k - start > 65536:
        start = max(0, k - 4096)
    return data[start:k + 1]


def _int(d: bytes, key: bytes) -> int | None:
    m = _INT[key].search(d)
    return int(m.group(1)) if m else None


def _filters(d: bytes) -> list[bytes]:
    m = _FILTER_ARRAY.search(d)
    if m:
        return re.findall(rb"/(\w+)", m.group(1))
    m = _FILTER.search(d)
    return [m.group(1)] if m else []


def _kind(d: bytes) -> str:
    if b"/Subtype/Image" in d.replace(b" ", b"") or b"/Subtype /Image" in d:
        return "image"
    if re.search(rb"/Length[123]\s|/Subtype\s*/(Type1C|CIDFontType0C|OpenType)", d):
        return "font"
    if re.search(rb"/Type\s*/(XRef|ObjStm)", d):
        return "obj"
    return "ops"


def _content_handler(b: Builder, d: bytes):
    kind = _kind(d)

    def handle(data: bytes) -> Recipe:
        if kind == "image":
            w, h, bpc = _int(d, b"Width"), _int(d, b"Height"), _int(d, b"BitsPerComponent")
            c = 3 if b"/DeviceRGB" in d else (1 if b"/DeviceGray" in d else (4 if b"/DeviceCMYK" in d else 0))
            if (w and h and bpc == 8 and c and b"/DecodeParms" not in d and b"/Predictor" not in d
                    and len(data) == w * h * c):
                b.count("pdf_image_pixels")
                return b.add_own(StreamType.IMAGE, data, {"kind": "pixels", "w": w, "h": h, "c": c})
            return b.add(StreamType.BINARY, data)
        if kind == "font":
            return b.add(StreamType.FONT, data)
        if kind == "obj":
            return b.add(StreamType.PDF_OBJ, data)
        return b.add(StreamType.PDF_OPS, data)

    return handle


def _stream_end(data: bytes, start: int, d: bytes) -> int | None:
    """Offset where the stream data ends (exclusive)."""
    ln = _int(d, b"Length")
    if ln is not None and start + ln <= len(data):
        tail = data[start + ln:start + ln + 12].lstrip(b"\r\n \t")
        if tail.startswith(b"endstream"):
            return start + ln
    e = data.find(b"endstream", start)
    return e if e >= 0 else None


def recipe(b: Builder, data: bytes) -> Recipe:
    parts: list[Recipe] = []
    cursor = 0
    for m in _STREAM.finditer(data):
        kw = m.start()
        if kw < cursor or (kw >= 3 and data[kw - 3:kw] == b"end"):
            continue
        d = _dict_before(data, kw)
        if d is None:
            continue
        start = m.end()
        end = _stream_end(data, start, d)
        if end is None:
            continue
        parts.append(b.add(StreamType.PDF_OBJ, data[cursor:start]))
        body = data[start:end]
        filters = _filters(d)
        handler = _content_handler(b, d)
        if filters == [b"FlateDecode"] or filters == [b"Fl"]:
            b.count("pdf_flate")
            parts.append(zlib_stream_recipe(b, body, handler, StreamType.PDF_OBJ))
        elif filters in ([b"DCTDecode"], [b"DCT"]) and is_jpeg(body):
            b.count("jpeg")
            parts.append(b.add_own(StreamType.IMAGE, body, {"kind": "jpeg"}))
        elif not filters:
            parts.append(handler(body))
        else:
            b.count("pdf_other_filter")
            parts.append(b.add(StreamType.BINARY, body))
        cursor = end
    parts.append(b.add(StreamType.PDF_OBJ, data[cursor:]))
    return ["c", parts]

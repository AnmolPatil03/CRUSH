"""ZIP / DOCX container: raw segmentation with exact rebuild.

The ZIP file is treated as a sequence of byte ranges.  Every member's
compressed data becomes a recipe (inflated content + deflate rebuild info);
**everything else** - local file headers, data descriptors, the central
directory, comments, gaps - is copied verbatim into the META stream.  Hence any
ZIP layout, including odd extra fields or padding, is rebuilt byte-for-byte.
``zipfile`` is used only to read the central directory.
"""

from __future__ import annotations

import io
import struct
import zipfile

from ..archive.manifest import Builder, Recipe
from ..codecs import StreamType
from .common import media_recipe, raw_deflate_recipe

_TEXTUAL = (".xml", ".rels", ".vml", ".svg", ".txt", ".htm", ".html", ".css", ".json")


def _content_handler(b: Builder, name: str):
    lower = name.lower()

    def handle(data: bytes) -> Recipe:
        if lower.endswith(_TEXTUAL) or data[:5] == b"<?xml":
            return b.add(StreamType.XML, data)
        if lower.endswith((".odttf", ".ttf", ".otf", ".fntdata")):
            return b.add(StreamType.FONT, data)
        if data[:4] == b"PK\x03\x04":  # embedded OOXML (e.g. an Excel chart's .xlsx): recurse
            b.count("nested_zip")
            return recipe(b, data)
        return media_recipe(b, data, StreamType.BINARY)

    return handle


def recipe(b: Builder, data: bytes) -> Recipe:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        infos = sorted(zf.infolist(), key=lambda i: i.header_offset)
    except (zipfile.BadZipFile, ValueError, OSError, RuntimeError):
        return b.add(StreamType.BINARY, data)
    parts: list[Recipe] = []
    cursor = 0
    for info in infos:
        off = info.header_offset
        if off < cursor or data[off:off + 4] != b"PK\x03\x04":
            return b.add(StreamType.BINARY, data)  # overlapping or unusual layout: keep verbatim
        name_len, extra_len = struct.unpack("<HH", data[off + 26:off + 30])
        start = off + 30 + name_len + extra_len
        end = start + info.compress_size
        if end > len(data):
            return b.add(StreamType.BINARY, data)
        parts.append(b.add(StreamType.META, data[cursor:start]))
        raw = data[start:end]
        handler = _content_handler(b, info.filename)
        if info.flag_bits & 1:  # encrypted member
            parts.append(b.add(StreamType.BINARY, raw))
        elif info.compress_type == zipfile.ZIP_DEFLATED:
            parts.append(raw_deflate_recipe(b, raw, handler))
        elif info.compress_type == zipfile.ZIP_STORED:
            parts.append(handler(raw))
        else:
            parts.append(b.add(StreamType.BINARY, raw))
        cursor = end
    parts.append(b.add(StreamType.META, data[cursor:]))
    return ["c", parts]

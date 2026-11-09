"""Shared container helpers: deflate/zlib recipes and content routing."""

from __future__ import annotations

import zlib
from typing import Callable

from ..archive.manifest import Builder, Recipe
from ..codecs import StreamType
from ..images import png
from . import deflate

Handler = Callable[[bytes], Recipe]


def raw_deflate_recipe(b: Builder, raw: bytes, handler: Handler, verbatim: StreamType = StreamType.BINARY) -> Recipe:
    """Recipe for a raw-deflate byte string: inflated content + exact rebuild information."""
    res = deflate.inflate_raw(raw)
    if res is None:
        b.count("deflate_verbatim")
        return b.add(verbatim, raw)
    data, used = res
    core = raw[:used]
    params = deflate.find_zlib_params(data, core)
    if params is not None:
        b.count("deflate_zlib")
        rec: Recipe = ["z", handler(data), *params]
    else:
        side = deflate.analyse(core, data)
        if side is None:
            b.count("deflate_verbatim")
            return b.add(verbatim, raw)
        b.count("deflate_tokens")
        rec = ["k", handler(data), b.add(StreamType.DEFLATE_SIDE, side), len(core)]
    if used < len(raw):
        rec = ["c", [rec, b.add(StreamType.META, raw[used:])]]
    return rec


def zlib_stream_recipe(b: Builder, z: bytes, handler: Handler, meta: StreamType = StreamType.META) -> Recipe:
    """Recipe for a zlib-wrapped stream (2-byte header, raw deflate, Adler-32, maybe trailing bytes)."""
    if len(z) < 6 or (z[0] & 0x0F) != 8 or ((z[0] << 8) | z[1]) % 31 != 0 or z[1] & 0x20:
        b.count("deflate_verbatim")
        return b.add(StreamType.BINARY, z)
    d = zlib.decompressobj(15)
    try:
        d.decompress(z)
    except zlib.error:
        b.count("deflate_verbatim")
        return b.add(StreamType.BINARY, z)
    if not d.eof:
        b.count("deflate_verbatim")
        return b.add(StreamType.BINARY, z)
    end = len(z) - len(d.unused_data)
    inner = raw_deflate_recipe(b, z[2:end - 4], handler)
    parts = [b.add(meta, z[:2]), inner, b.add(meta, z[end - 4:end])]
    if end < len(z):
        parts.append(b.add(meta, z[end:]))
    return ["c", parts]


def png_recipe(b: Builder, data: bytes) -> Recipe:
    """PNG -> chunks + pixels (own image stream) + filter types + deflate rebuild info."""
    parts = png.split(data)
    if parts is None:
        return b.add(StreamType.BINARY, data)
    w, h, c = parts.width, parts.height, parts.channels

    def content(filtered: bytes) -> Recipe:
        if c:
            got = png.to_pixels(filtered, w, h, c)
            if got is not None:
                px, ft = got
                b.count("png_pixels")
                return ["f", b.add_own(StreamType.IMAGE, px.tobytes(), {"kind": "pixels", "w": w, "h": h, "c": c}),
                        b.add(StreamType.META, ft), w, h, c]
        b.count("png_filtered")
        return b.add(StreamType.BINARY, filtered)

    z = zlib_stream_recipe(b, parts.zdata, content)
    return ["p", b.add(StreamType.META, parts.pre), b.add(StreamType.META, parts.post), parts.idat_sizes, z]


def is_jpeg(data: bytes) -> bool:
    return data[:3] == b"\xff\xd8\xff"


def media_recipe(b: Builder, data: bytes, fallback: StreamType) -> Recipe:
    """Route embedded image bytes by magic number."""
    if is_jpeg(data):
        b.count("jpeg")
        return b.add_own(StreamType.IMAGE, data, {"kind": "jpeg"})
    if data[:8] == png.SIG:
        return png_recipe(b, data)
    return b.add(fallback, data)

"""Stream types, codec IDs and the per-stream codec selection.

Each archive stream records its codec ID and parameters, so new codecs can be
added without breaking old archives (an unknown ID is a clear error).
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any

import numpy as np

from .archive.format import Reader, put_varint
from .cm import engine
from .errors import ToolMissing, UnknownCodec
from .images import jxl, pyramid


class StreamType(IntEnum):
    TEXT = 0          # plain text files
    XML = 1           # DOCX/OOXML markup, SVG
    PDF_OPS = 2       # PDF content streams (drawing operators)
    PDF_OBJ = 3       # PDF dictionaries, xref, object streams, verbatim structure
    FONT = 4          # embedded fonts
    BINARY = 5        # anything else, incl. un-reconstructable deflate data
    IMAGE = 6         # one image per stream (JPEG XL / pyramid / CM)
    META = 7          # ZIP local headers, central directory, PNG chunks
    DEFLATE_SIDE = 8  # deflate token-reconstruction side information


class Codec(IntEnum):
    STORE = 0       # raw bytes
    CM = 1          # context mixing: params = flags u8, table bits u8, record_len varint
    JXL_JPEG = 2    # JPEG XL lossless JPEG transcoding
    JXL_PIXELS = 3  # JPEG XL lossless pixels: params = w, h, c varints
    PYRAMID = 4     # residual pyramid + CM: params = w, h, c varints


ENTROPY_SKIP = 7.97  # bits/byte: above this CM cannot win, store directly


def order0_entropy(data: bytes) -> float:
    """H0 = -sum p_i log2 p_i over byte frequencies (bits per byte)."""
    if not data:
        return 0.0
    counts = np.bincount(np.frombuffer(data, dtype=np.uint8), minlength=256).astype(np.float64)
    p = counts[counts > 0] / len(data)
    return float(-(p * np.log2(p)).sum())


def _cm_params(flags: int, tb: int, record_len: int) -> bytes:
    out = bytearray([flags, tb])
    put_varint(out, record_len)
    return bytes(out)


def _whc(w: int, h: int, c: int) -> bytes:
    out = bytearray()
    for v in (w, h, c):
        put_varint(out, v)
    return bytes(out)


def encode_general(data: bytes, record_len: int = 0, flags: int = engine.FULL) -> tuple[int, bytes, bytes, str]:
    """CM unless the data is tiny or already maximally dense; stored mode as the fallback."""
    if len(data) < 16:
        return Codec.STORE, b"", data, "tiny"
    if len(data) > 4096 and order0_entropy(data) > ENTROPY_SKIP:
        return Codec.STORE, b"", data, "high-entropy"
    tb = engine.table_bits(len(data))
    payload = engine.compress(data, flags, record_len, tb)
    if payload is None:
        return Codec.STORE, b"", data, "cm-not-smaller"
    return Codec.CM, _cm_params(flags, tb, record_len), payload, "cm"


def encode_jpeg(data: bytes) -> tuple[int, bytes, bytes, str]:
    if not jxl.available():
        return Codec.STORE, b"", data, "jxl-tools-missing"
    out = jxl.jpeg_encode(data)
    if out is None or len(out) >= len(data):
        return Codec.STORE, b"", data, "jxl-refused"
    return Codec.JXL_JPEG, b"", out, "jxl"


def encode_pixels(data: bytes, w: int, h: int, c: int, try_pyramid: bool = True) -> tuple[int, bytes, bytes, str]:
    """Best of JPEG XL lossless, residual pyramid and plain CM (row stride as record length)."""
    px = np.frombuffer(data, dtype=np.uint8).reshape(h, w, c)
    best = encode_general(data, record_len=w * c)
    cands = [best]
    if jxl.available() and c <= 4:
        out = jxl.pixels_encode(px)
        if out is not None:
            cands.append((Codec.JXL_PIXELS, _whc(w, h, c), out, "jxl"))
    if try_pyramid and h >= 32 and w >= 32:
        out = pyramid.encode(px)
        if out is not None:
            cands.append((Codec.PYRAMID, _whc(w, h, c), out, "pyramid"))
    return min(cands, key=lambda t: len(t[2]))


def encode_job(job: tuple[int, bytes, dict[str, Any]]) -> tuple[int, bytes, bytes, str]:
    """Top-level (picklable) entry point used by the multiprocessing pool."""
    stype, data, hint = job
    kind = hint.get("kind")
    if kind == "jpeg":
        return encode_jpeg(data)
    if kind == "pixels":
        return encode_pixels(data, hint["w"], hint["h"], hint["c"], hint.get("pyramid", True))
    return encode_general(data, hint.get("record_len", 0), hint.get("flags", engine.FULL))


def decode(codec: int, params: bytes, payload: bytes, raw_len: int) -> bytes:
    if codec == Codec.STORE:
        return payload
    if codec == Codec.CM:
        rd = Reader(params)
        flags, tb = rd.u8(), rd.u8()
        return engine.decompress(payload, raw_len, flags, rd.varint(), tb)
    if codec in (Codec.JXL_JPEG, Codec.JXL_PIXELS) and not jxl.available():
        raise ToolMissing("this archive contains JPEG XL streams; install cjxl/djxl to decompress it")
    if codec == Codec.JXL_JPEG:
        return jxl.jpeg_decode(payload)
    if codec in (Codec.JXL_PIXELS, Codec.PYRAMID):
        rd = Reader(params)
        w, h, c = rd.varint(), rd.varint(), rd.varint()
        if codec == Codec.JXL_PIXELS:
            return jxl.pixels_decode(payload, h, w, c).tobytes()
        return pyramid.decode(payload, h, w, c).tobytes()
    raise UnknownCodec(codec)

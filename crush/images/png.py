r"""PNG decomposition into pixels + an exact rebuild recipe.

A PNG is ``signature, chunk*``; image data is the concatenation of all IDAT
payloads, a zlib stream of *filtered* scanlines: each row is a filter byte
``f in {0..4}`` followed by ``W * bpp`` bytes, where for byte ``x`` with left
neighbour ``a``, upper ``b`` and upper-left ``c``:

    0 None   F = x
    1 Sub    F = x - a
    2 Up     F = x - b
    3 Avg    F = x - floor((a + b) / 2)
    4 Paeth  F = x - paeth(a, b, c),  paeth = the one of a, b, c closest to a + b - c

(all mod 256).  Unfiltering recovers pixels; re-applying the *recorded* filter
type per row reproduces the filtered bytes exactly, the zlib stream is rebuilt
by :mod:`crush.containers.deflate`, and the IDAT chunking and CRCs are
regenerated.  Only 8-bit, non-interlaced grey/GA/RGB/RGBA images take the
pixel route; anything else is stored with its filtered bytes or verbatim.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

import numpy as np
from numba import njit

SIG = b"\x89PNG\r\n\x1a\n"
CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}


@dataclass
class PngParts:
    pre: bytes            # signature + every chunk before the first IDAT
    post: bytes           # every chunk after the last IDAT
    idat_sizes: list[int]
    zdata: bytes          # concatenated IDAT payloads (a zlib stream)
    width: int
    height: int
    channels: int         # 0 when the image is not eligible for the pixel route


def split(png: bytes) -> PngParts | None:
    """Split a PNG into its parts; None if malformed or IDATs are not contiguous."""
    if png[:8] != SIG:
        return None
    pos, sizes, z = 8, [], []
    first = last = -1
    w = h = ch = 0
    while pos + 12 <= len(png):
        ln = struct.unpack(">I", png[pos:pos + 4])[0]
        typ = png[pos + 4:pos + 8]
        end = pos + 12 + ln
        if end > len(png):
            return None
        if typ == b"IHDR" and ln == 13:
            w, h, depth, ctype, _, _, inter = struct.unpack(">IIBBBBB", png[pos + 8:pos + 21])
            ch = CHANNELS.get(ctype, 0) if depth == 8 and inter == 0 else 0
        if typ == b"IDAT":
            if first >= 0 and last != pos:
                return None  # IDAT chunks interleaved with others: keep verbatim
            if first < 0:
                first = pos
            payload = png[pos + 8:pos + 8 + ln]
            if zlib.crc32(b"IDAT" + payload) != struct.unpack(">I", png[end - 4:end])[0]:
                return None
            sizes.append(ln)
            z.append(payload)
            last = end
        pos = end
        if typ == b"IEND":
            break
    if first < 0:
        return None
    return PngParts(png[:first], png[last:], sizes, b"".join(z), w, h, ch)


def join(parts: PngParts, zdata: bytes) -> bytes:
    out = bytearray(parts.pre)
    pos = 0
    for ln in parts.idat_sizes:
        payload = zdata[pos:pos + ln]
        pos += ln
        out += struct.pack(">I", ln) + b"IDAT" + payload + struct.pack(">I", zlib.crc32(b"IDAT" + payload))
    out += parts.post
    return bytes(out)


@njit(cache=True)
def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


@njit(cache=True)
def unfilter(f, h, stride, bpp, px, ftypes):
    """Filtered rows -> raw pixel rows; returns False on an invalid filter byte."""
    for y in range(h):
        t = f[y * (stride + 1)]
        ftypes[y] = t
        if t > 4:
            return False
        for x in range(stride):
            v = np.int64(f[y * (stride + 1) + 1 + x])
            a = np.int64(px[y * stride + x - bpp]) if x >= bpp else 0
            b = np.int64(px[(y - 1) * stride + x]) if y > 0 else 0
            c = np.int64(px[(y - 1) * stride + x - bpp]) if (x >= bpp and y > 0) else 0
            if t == 1:
                v += a
            elif t == 2:
                v += b
            elif t == 3:
                v += (a + b) >> 1
            elif t == 4:
                v += _paeth(a, b, c)
            px[y * stride + x] = v & 255
    return True


@njit(cache=True)
def refilter(px, h, stride, bpp, ftypes, f):
    """Raw pixel rows + recorded filter types -> the original filtered bytes."""
    for y in range(h):
        t = ftypes[y]
        f[y * (stride + 1)] = t
        for x in range(stride):
            v = np.int64(px[y * stride + x])
            a = np.int64(px[y * stride + x - bpp]) if x >= bpp else 0
            b = np.int64(px[(y - 1) * stride + x]) if y > 0 else 0
            c = np.int64(px[(y - 1) * stride + x - bpp]) if (x >= bpp and y > 0) else 0
            if t == 1:
                v -= a
            elif t == 2:
                v -= b
            elif t == 3:
                v -= (a + b) >> 1
            elif t == 4:
                v -= _paeth(a, b, c)
            f[y * (stride + 1) + 1 + x] = v & 255


def to_pixels(filtered: bytes, w: int, h: int, c: int) -> tuple[np.ndarray, bytes] | None:
    """(H, W, C) pixels and the per-row filter types, or None if inconsistent."""
    stride = w * c
    if len(filtered) != h * (stride + 1):
        return None
    f = np.frombuffer(filtered, dtype=np.uint8)
    px = np.zeros(h * stride, dtype=np.uint8)
    ft = np.zeros(h, dtype=np.uint8)
    if not unfilter(f, h, stride, c, px, ft):
        return None
    return px.reshape(h, w, c), ft.tobytes()


def from_pixels(px: np.ndarray, ftypes: bytes) -> bytes:
    h, w, c = px.shape
    out = np.zeros(h * (w * c + 1), dtype=np.uint8)
    refilter(np.ascontiguousarray(px).ravel(), h, w * c, c, np.frombuffer(ftypes, dtype=np.uint8), out)
    return out.tobytes()

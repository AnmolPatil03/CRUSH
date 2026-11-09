r"""Experimental codec: downsample + residual pyramid, residuals coded with CM.

For each channel plane ``x`` (integers 0..255):

    d = down(x)          2x2 block mean, floor((a+b+c+d) / 4), edge pixels replicated
    u = up(d)            integer bilinear upsampling back to the size of x
    r = (x - u) mod 256  residual, small for smooth images

and recursively ``x = up(down(x)) + r`` until the plane is <= 16 pixels on a side.
``up`` uses only integer arithmetic (``(3a + b + 2) >> 2`` style weights), so the
decoder computes exactly the same prediction and recovers ``x = (u + r) mod 256``.
Residuals of all levels are mapped by zig-zag (``0,-1,1,-2 -> 0,1,2,3``) and
coded with the CM engine using the row width as record length.
"""

from __future__ import annotations

import numpy as np

from ..cm import engine


def down(x: np.ndarray) -> np.ndarray:
    h, w = x.shape
    p = np.pad(x.astype(np.int32), ((0, h & 1), (0, w & 1)), mode="edge")
    return ((p[0::2, 0::2] + p[1::2, 0::2] + p[0::2, 1::2] + p[1::2, 1::2]) >> 2).astype(np.int32)


def up(d: np.ndarray, h: int, w: int) -> np.ndarray:
    """Integer bilinear-ish upsampling (deterministic) of d to shape (h, w)."""
    d = d.astype(np.int32)
    p = np.pad(d, 1, mode="edge")
    c, n, s, wv, e = p[1:-1, 1:-1], p[:-2, 1:-1], p[2:, 1:-1], p[1:-1, :-2], p[1:-1, 2:]
    out = np.empty((d.shape[0] * 2, d.shape[1] * 2), np.int32)
    # each output pixel: 3/4 nearest coarse sample + 1/4 of the vertical and horizontal neighbours
    out[0::2, 0::2] = (2 * c + n + wv + 2) >> 2
    out[0::2, 1::2] = (2 * c + n + e + 2) >> 2
    out[1::2, 0::2] = (2 * c + s + wv + 2) >> 2
    out[1::2, 1::2] = (2 * c + s + e + 2) >> 2
    return out[:h, :w]


def _zig(r: np.ndarray) -> np.ndarray:
    r = ((r + 128) & 255) - 128  # residual in -128..127
    return np.where(r >= 0, 2 * r, -2 * r - 1).astype(np.uint8)


def _unzig(z: np.ndarray) -> np.ndarray:
    z = z.astype(np.int32)
    return np.where(z & 1, -(z + 1) // 2, z // 2)


def _levels(h: int, w: int) -> list[tuple[int, int]]:
    shapes = [(h, w)]
    while shapes[-1][0] > 16 and shapes[-1][1] > 16:
        a, b = shapes[-1]
        shapes.append(((a + 1) // 2, (b + 1) // 2))
    return shapes


def encode(px: np.ndarray) -> bytes | None:
    """px: (H, W, C) uint8.  Returns the CM payload of all planes, or None if not smaller."""
    h, w, c = px.shape
    parts = []
    for ch in range(c):
        x = px[:, :, ch].astype(np.int32)
        stack = []
        shapes = _levels(h, w)
        for (a, b) in shapes[:-1]:
            d = down(x)
            stack.append(_zig(x - up(d, a, b)).ravel())
            x = d
        parts.append(x.astype(np.uint8).ravel())  # coarsest level stored as-is
        parts.extend(reversed(stack))
    blob = b"".join(p.tobytes() for p in parts)
    return engine.compress(blob, record_len=w)


def decode(payload: bytes, h: int, w: int, c: int) -> np.ndarray:
    shapes = _levels(h, w)
    total = sum(a * b for a, b in shapes) * c
    blob = np.frombuffer(engine.decompress(payload, total, record_len=w), dtype=np.uint8)
    out = np.empty((h, w, c), np.uint8)
    pos = 0
    for ch in range(c):
        a, b = shapes[-1]
        x = blob[pos:pos + a * b].reshape(a, b).astype(np.int32)
        pos += a * b
        for (a, b) in reversed(shapes[:-1]):
            r = _unzig(blob[pos:pos + a * b].reshape(a, b))
            pos += a * b
            x = (up(x, a, b) + r) & 255
        out[:, :, ch] = x
    return out

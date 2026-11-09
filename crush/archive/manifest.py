"""Rebuild recipes and the stream builder.

A *recipe* is a small expression tree (JSON lists) that evaluates to the exact
bytes of an original file, given the decoded streams:

========================================  =====================================
``["r", sid, off, n]``                     ``n`` bytes of stream ``sid`` at ``off``
``["c", [r1, r2, ...]]``                   concatenation
``["z", src, level, mem, strategy]``       raw deflate of ``src`` with zlib params
``["k", src, side, raw_len]``              deflate token reconstruction
``["p", pre, post, [idat sizes], z]``      PNG: chunks around the IDATs, IDATs from ``z``
``["f", px, ftypes, w, h, c]``             PNG row filtering of pixels ``px``
========================================  =====================================

Streams of the same type are *solid*: data from every file in the archive is
appended to one buffer per type, so the CM model learns across files.  Images
get one stream each.  Identical byte strings (>= 64 bytes) are stored once:
the second occurrence becomes a reference to the first (SHA-256 dedup).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np

from ..codecs import StreamType
from ..containers import deflate
from ..errors import CorruptArchive
from ..images import png

Recipe = list[Any]
DEDUP_MIN = 64


class Builder:
    """Accumulates stream data while containers produce recipes."""

    def __init__(self) -> None:
        self.streams: list[tuple[int, bytearray | bytes, dict[str, Any]]] = []
        self._solid: dict[int, int] = {}
        self._seen: dict[bytes, Recipe] = {}
        self.dedup_bytes = 0
        self.stats: dict[str, int] = {}

    def count(self, key: str, k: int = 1) -> None:
        self.stats[key] = self.stats.get(key, 0) + k

    def _dedup(self, data: bytes) -> tuple[bytes | None, Recipe | None]:
        if len(data) < DEDUP_MIN:
            return None, None
        key = hashlib.sha256(data).digest()
        hit = self._seen.get(key)
        if hit is not None:
            self.dedup_bytes += len(data)
        return key, hit

    def add(self, stype: StreamType, data: bytes) -> Recipe:
        """Append to the solid stream of ``stype``; returns a reference recipe."""
        key, hit = self._dedup(data)
        if hit is not None:
            return hit
        sid = self._solid.get(stype)
        if sid is None:
            sid = len(self.streams)
            self._solid[stype] = sid
            self.streams.append((int(stype), bytearray(), {}))
        buf = self.streams[sid][1]
        assert isinstance(buf, bytearray)
        ref = ["r", sid, len(buf), len(data)]
        buf += data
        if key is not None:
            self._seen[key] = ref
        return ref

    def add_own(self, stype: StreamType, data: bytes, hint: dict[str, Any]) -> Recipe:
        """A stream of its own (images): coded independently with ``hint``."""
        key, hit = self._dedup(data)
        if hit is not None:
            return hit
        sid = len(self.streams)
        self.streams.append((int(stype), bytes(data), hint))
        ref = ["r", sid, 0, len(data)]
        if key is not None:
            self._seen[key] = ref
        return ref


def dumps(recipes: list[Recipe]) -> bytes:
    return json.dumps({"v": 1, "files": recipes}, separators=(",", ":")).encode()


def loads(blob: bytes) -> list[Recipe]:
    try:
        obj = json.loads(blob)
        return obj["files"]
    except (ValueError, KeyError, TypeError) as e:
        raise CorruptArchive(f"unreadable manifest ({e})") from None


def evaluate(r: Recipe, streams: list[bytes]) -> bytes:
    """Rebuild the bytes described by recipe ``r``."""
    try:
        op = r[0]
        if op == "r":
            _, sid, off, n = r
            s = streams[sid]
            if off + n > len(s):
                raise CorruptArchive("stream reference out of range")
            return s[off:off + n]
        if op == "c":
            return b"".join(evaluate(p, streams) for p in r[1])
        if op == "z":
            return deflate.zlib_deflate(evaluate(r[1], streams), (r[2], r[3], r[4]))
        if op == "k":
            out = deflate.rebuild(evaluate(r[1], streams), evaluate(r[2], streams), r[3])
            if out is None:
                raise CorruptArchive("deflate reconstruction failed")
            return out
        if op == "p":
            parts = png.PngParts(evaluate(r[1], streams), evaluate(r[2], streams), r[3], b"", 0, 0, 0)
            return png.join(parts, evaluate(r[4], streams))
        if op == "f":
            _, px, ft, w, h, c = r
            pix = np.frombuffer(evaluate(px, streams), dtype=np.uint8).reshape(h, w, c)
            return png.from_pixels(pix, evaluate(ft, streams))
    except (IndexError, ValueError, TypeError) as e:
        raise CorruptArchive(f"malformed recipe ({e})") from None
    raise CorruptArchive(f"unknown recipe operation {op!r}")

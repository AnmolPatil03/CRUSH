"""General-purpose baseline compressors used for comparison.

Each baseline is a ``(compress, decompress)`` pair at its strongest standard
setting, so CRUSH is compared against the best each library can do.
"""

from __future__ import annotations

import bz2
import lzma
import zlib
from typing import Callable

import zstandard

Codec = tuple[Callable[[bytes], bytes], Callable[[bytes], bytes]]

_ZSTD_C = zstandard.ZstdCompressor(level=19)
_ZSTD_D = zstandard.ZstdDecompressor()

BASELINES: dict[str, Codec] = {
    "zlib-9": (lambda b: zlib.compress(b, 9), zlib.decompress),
    "bz2-9": (lambda b: bz2.compress(b, 9), bz2.decompress),
    "lzma-9e": (lambda b: lzma.compress(b, preset=9 | lzma.PRESET_EXTREME), lzma.decompress),
    "zstd-19": (_ZSTD_C.compress, _ZSTD_D.decompress),
}

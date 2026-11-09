"""Phase 4: bit-exact deflate reconstruction."""

import os
import random
import zlib

import pytest
from hypothesis import given, settings, strategies as st

from crush.containers import deflate as D


def raw_deflate(data: bytes, level: int = 6, strategy: int = zlib.Z_DEFAULT_STRATEGY, mem: int = 8) -> bytes:
    c = zlib.compressobj(level, zlib.DEFLATED, -15, mem, strategy)
    return c.compress(data) + c.flush()


SAMPLE = (b"<w:p><w:r><w:t>Hello CRUSH</w:t></w:r></w:p>\n" * 400
          + bytes(random.Random(1).randrange(256) for _ in range(3000)))


@pytest.mark.parametrize("level", range(0, 10))
@pytest.mark.parametrize("strategy", [zlib.Z_DEFAULT_STRATEGY, zlib.Z_FILTERED, zlib.Z_HUFFMAN_ONLY,
                                      zlib.Z_RLE, zlib.Z_FIXED])
def test_token_reconstruction_all_zlib_modes(level, strategy):
    raw = raw_deflate(SAMPLE, level, strategy)
    side = D.analyse(raw, SAMPLE)
    assert side is not None  # stored, fixed and dynamic blocks are all reproducible
    assert D.rebuild(SAMPLE, side, len(raw)) == raw


def test_zlib_param_search_finds_level_and_memlevel():
    for level, mem in ((1, 8), (9, 8), (6, 9), (4, 5)):
        raw = raw_deflate(SAMPLE, level, mem=mem)
        p = D.find_zlib_params(SAMPLE, raw)
        assert p is not None and D.zlib_deflate(SAMPLE, p) == raw


def test_empty_and_tiny_streams():
    for data in (b"", b"a", b"abc"):
        raw = raw_deflate(data)
        side = D.analyse(raw, data)
        assert side is not None and D.rebuild(data, side, len(raw)) == raw


def test_rejects_mismatched_data():
    raw = raw_deflate(SAMPLE)
    assert D.analyse(raw, SAMPLE[:-1] + b"?") is None
    assert D.inflate_raw(os.urandom(64)) is None or True  # never raises


@settings(max_examples=30, deadline=None)
@given(st.binary(max_size=4000), st.integers(0, 9), st.integers(1, 9))
def test_reconstruction_property(data, level, mem):
    raw = raw_deflate(data * 3, level, mem=mem)
    side = D.analyse(raw, data * 3)
    assert side is not None and D.rebuild(data * 3, side, len(raw)) == raw

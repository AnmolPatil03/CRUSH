"""Phase 3: Numba context-mixing engine."""

import os
import random

import pytest
from hypothesis import given, settings, strategies as st

from crush.cm import engine as E
from crush.cm.tables import SQUASH, STRETCH


def rt(data: bytes, flags: int = E.FULL, record_len: int = 0) -> bytes | None:
    c = E.compress(data, flags, record_len)
    if c is not None:
        assert E.decompress(c, len(data), flags, record_len) == data
    return c


def test_tables_are_monotone_inverse():
    assert all(SQUASH[i] <= SQUASH[i + 1] for i in range(len(SQUASH) - 1))
    for p in range(64, 4032, 61):
        assert abs(int(SQUASH[STRETCH[p] + 2047]) - p * 16) < 700


@pytest.mark.parametrize("data", [b"", b"x", b"ab" * 3, bytes(range(256)) * 4,
                                  "Unicode — ünïcödé 日本語 текст ✓\n".encode() * 50, b"\x00" * 10000])
def test_roundtrip_edge_cases(data):
    c = rt(data)
    if data == b"":
        assert c == b""


def test_random_data_is_not_expanded():
    data = os.urandom(20000)
    assert E.compress(data) is None  # caller must store it


@settings(max_examples=40, deadline=None)
@given(st.binary(max_size=3000))
def test_roundtrip_property(data):
    rt(data)


@settings(max_examples=20, deadline=None)
@given(st.text(max_size=2000), st.sampled_from([0, 2, 6, 14, 30, E.FULL]))
def test_roundtrip_property_text_all_flag_sets(text, flags):
    rt(text.encode("utf-8"), flags)


def test_each_component_helps_on_text():
    rng = random.Random(5)
    words = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda".split()
    text = " ".join(rng.choice(words) for _ in range(20000)).encode()
    plain = len(rt(text, 0))
    full = len(rt(text, E.FULL))
    assert full < plain


def test_record_model_on_fixed_width():
    rows = "".join(f"{i:08d}|{(i * 7919) % 1000:05d}|{'X' * (i % 5):<5}\n" for i in range(3000)).encode()
    no_rec = len(rt(rows, E.FULL & ~E.F_RECORD))
    with_rec = len(rt(rows, E.FULL))
    assert with_rec < no_rec


def test_deterministic_output():
    data = ("determinism " * 500).encode()
    assert E.compress(data) == E.compress(data)

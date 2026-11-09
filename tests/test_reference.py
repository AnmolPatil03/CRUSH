"""Phase 2: pure-Python arithmetic coder and order-2 model."""

import math
import os

from hypothesis import given, settings, strategies as st

from crush.cm import reference as ref


@settings(max_examples=60, deadline=None)
@given(st.lists(st.tuples(st.integers(0, 1), st.integers(1, 65535)), max_size=3000))
def test_coder_roundtrip_any_bits_any_probabilities(seq):
    enc = ref.Encoder()
    for bit, p in seq:
        enc.encode(bit, p)
    dec = ref.Decoder(enc.finish())
    assert [dec.decode(p) for _, p in seq] == [b for b, _ in seq]


def test_coder_length_close_to_information_content():
    import random
    rng = random.Random(7)
    p = 60000  # P(1) ~ 0.915
    bits = [1 if rng.random() < p / 65536 else 0 for _ in range(50000)]
    enc = ref.Encoder()
    for b in bits:
        enc.encode(b, p)
    info = sum(-math.log2(p / 65536 if b else 1 - p / 65536) for b in bits) / 8
    assert len(enc.finish()) <= info * 1.01 + 8


@settings(max_examples=25, deadline=None)
@given(st.binary(max_size=400))
def test_order2_roundtrip(data):
    assert ref.decompress(ref.compress(data), len(data)) == data


def test_order2_edge_cases_and_gain():
    assert ref.decompress(ref.compress(b""), 0) == b""
    for data in (b"a", bytes(range(256)), os.urandom(500)):
        assert ref.decompress(ref.compress(data), len(data)) == data
    text = ("the quick brown fox jumps over the lazy dog. " * 60).encode()
    assert len(ref.compress(text)) < len(text) * 0.25

"""Phases 4-6: archive format, containers, images, dedup, parallelism, error handling."""

import hashlib
import io
import os
import zipfile

import numpy as np
import pytest

from crush import pipeline
from crush.archive import format as fmt
from crush.errors import BadMagic, ChecksumMismatch, CorruptArchive, UnknownCodec
from crush.images import jxl, png, pyramid
from tests import fixtures as fx
from tests.conftest import corpus_files


def roundtrip(*items, **kw):
    blob, rep = pipeline.compress(list(items), workers=kw.pop("workers", 1), **kw)
    out = pipeline.decompress(blob)
    assert out == list(items)
    assert rep.verified
    return blob, rep


@pytest.mark.parametrize("data", [b"", b"x", os.urandom(5000), "naïve café — 日本語\n".encode() * 200,
                                  fx.fixed_width_table(500).encode()])
def test_text_roundtrip(data):
    blob, _ = roundtrip(("f.txt", data))
    assert len(blob) <= len(data) + 140  # bounded header overhead, never meaningfully larger


def test_random_data_uses_stored_mode():
    data = os.urandom(100_000)
    blob, rep = roundtrip(("r.bin", data))
    assert rep.streams[0].codec == "STORE"
    assert len(blob) < len(data) + 140


def test_docx_with_tables_and_images():
    data = fx.make_docx(3)
    blob, rep = roundtrip(("r.docx", data))
    assert rep.stats.get("deflate_zlib", 0) + rep.stats.get("deflate_tokens", 0) > 5
    assert len(blob) < len(data)


def test_pdf_with_fonts_and_images():
    data = fx.make_pdf(3)
    blob, _ = roundtrip(("r.pdf", data))
    assert len(blob) < len(data)


@pytest.mark.parametrize("path", corpus_files("docx")[:4] + corpus_files("pdf")[:4], ids=lambda p: p.name)
def test_real_world_corpus_files(path):
    roundtrip((path.name, path.read_bytes()))


def test_zip_with_stored_and_odd_members():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.xml", "<a>" * 500, compress_type=zipfile.ZIP_STORED)
        z.writestr("b.xml", "<b>" * 500, compress_type=zipfile.ZIP_DEFLATED, compresslevel=1)
        z.writestr("c.bin", os.urandom(300), compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("empty", b"")
        z.comment = b"zip comment"
    roundtrip(("odd.docx", buf.getvalue() + b"trailing garbage"))


def test_truncated_or_broken_containers_still_roundtrip():
    d = fx.make_docx(1, with_images=False)
    roundtrip(("cut.docx", d[: len(d) // 2]))
    p = fx.make_pdf(1, with_images=False)
    roundtrip(("cut.pdf", p[: len(p) // 3]))


def test_solid_multi_file_archive_and_dedup():
    a = fx.make_docx(1)
    blob_one, _ = pipeline.compress([("a.docx", a)], workers=1)
    blob_two, rep = roundtrip(("a.docx", a), ("copy.docx", a))
    assert rep.dedup_bytes > 0
    assert len(blob_two) < len(blob_one) * 1.2  # the duplicate costs almost nothing


def test_parallel_matches_serial():
    items = [("t.txt", fx.prose(30000).encode()), ("d.docx", fx.make_docx(2))]
    serial, _ = pipeline.compress(items, workers=1)
    from crush import config
    old = config.settings.parallel_threshold
    object.__setattr__(config.settings, "parallel_threshold", 0)
    try:
        par, _ = pipeline.compress(items, workers=2)
    finally:
        object.__setattr__(config.settings, "parallel_threshold", old)
    assert par == serial  # deterministic regardless of scheduling


def test_png_filters_roundtrip_all_types():
    rng = np.random.default_rng(0)
    px = rng.integers(0, 256, (17, 13, 3), dtype=np.uint8)
    ft = bytes(i % 5 for i in range(17))
    f = png.from_pixels(px, ft)
    got = png.to_pixels(f, 13, 17, 3)
    assert got is not None and np.array_equal(got[0], px) and got[1] == ft


def test_pyramid_codec_roundtrip():
    px = np.asarray(fx.photo(97, 61))
    payload = pyramid.encode(px)
    assert payload is not None
    assert np.array_equal(pyramid.decode(payload, 61, 97, 3), px)


@pytest.mark.skipif(not jxl.available(), reason="JPEG XL tools not installed")
def test_jxl_jpeg_is_bit_exact():
    jpg = fx.image_bytes(fx.photo(), "JPEG", quality=85)
    out = jxl.jpeg_encode(jpg)
    assert out is not None and jxl.jpeg_decode(out) == jpg


# ------------------------------------------------------------------ error handling

def _blob():
    return pipeline.compress([("x.txt", b"hello hello hello hello " * 40)], workers=1)[0]


def test_bad_magic():
    with pytest.raises(BadMagic):
        pipeline.decompress(b"NOTCRUSH" + _blob())


def test_crc_and_truncation_detected():
    b = bytearray(_blob())
    b[len(b) // 2] ^= 0xFF
    with pytest.raises(CorruptArchive):
        pipeline.decompress(bytes(b))
    with pytest.raises(CorruptArchive):
        pipeline.decompress(_blob()[:-10])


def test_unknown_codec():
    arc = fmt.parse(_blob())
    arc.streams[0].codec = 99
    with pytest.raises(UnknownCodec):
        pipeline.decompress(fmt.serialise(arc))


def test_checksum_mismatch():
    arc = fmt.parse(_blob())
    arc.files[0].sha256 = hashlib.sha256(b"other").digest()
    with pytest.raises(ChecksumMismatch):
        pipeline.decompress(fmt.serialise(arc))

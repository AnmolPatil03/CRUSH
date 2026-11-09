"""JPEG XL reference tools (``cjxl`` / ``djxl``) driven through ``subprocess``.

* JPEG -> JXL uses *lossless JPEG transcoding*: the JPEG's DCT coefficients are
  re-entropy-coded and ``djxl`` reconstructs the original JPEG bit-for-bit.  The
  JPEG is never decoded to pixels.
* Raw pixels -> JXL uses modular lossless mode (``-d 0``).

Every encode is immediately decoded and compared, so a tool bug can never
produce a lossy archive.  When the tools are missing, callers fall back to
stored mode / CM and report it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

from ..config import settings
from ..errors import ToolMissing


@lru_cache(maxsize=1)
def tools() -> tuple[str, str] | None:
    """Absolute paths of (cjxl, djxl), or None if either is not installed."""
    c, d = shutil.which(settings.cjxl), shutil.which(settings.djxl)
    return (c, d) if c and d else None


def available() -> bool:
    return tools() is not None


def _run(args: list[str]) -> None:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    subprocess.run(args, check=True, capture_output=True, timeout=600, creationflags=flags)


def _need() -> tuple[str, str]:
    t = tools()
    if t is None:
        raise ToolMissing("JPEG XL tools (cjxl/djxl) not found; install libjxl or set CRUSH_CJXL/CRUSH_DJXL")
    return t


def jpeg_encode(jpeg: bytes) -> bytes | None:
    """Losslessly transcode a JPEG; returns None if cjxl refuses it or it does not round-trip."""
    cjxl, _ = _need()
    with tempfile.TemporaryDirectory(prefix="crush-jxl-") as tmp:
        src, dst = Path(tmp, "in.jpg"), Path(tmp, "out.jxl")
        src.write_bytes(jpeg)
        try:
            _run([cjxl, str(src), str(dst), "--lossless_jpeg=1", "-e", "7", "--quiet"])
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            return None
        out = dst.read_bytes()
    try:
        if jpeg_decode(out) != jpeg:
            return None
    except (subprocess.CalledProcessError, OSError):
        return None
    return out


def jpeg_decode(jxl: bytes) -> bytes:
    _, djxl = _need()
    with tempfile.TemporaryDirectory(prefix="crush-jxl-") as tmp:
        src, dst = Path(tmp, "in.jxl"), Path(tmp, "out.jpg")
        src.write_bytes(jxl)
        _run([djxl, str(src), str(dst), "--quiet"])
        return dst.read_bytes()


_MODES = {1: "L", 2: "LA", 3: "RGB", 4: "RGBA"}


def pixels_encode(px: np.ndarray) -> bytes | None:
    """Lossless JXL of an (H, W, C) uint8 array, C in 1..4; None if it does not round-trip."""
    cjxl, _ = _need()
    h, w, c = px.shape
    with tempfile.TemporaryDirectory(prefix="crush-jxl-") as tmp:
        src, dst = Path(tmp, "in.png"), Path(tmp, "out.jxl")
        Image.fromarray(px.reshape(h, w) if c == 1 else px, _MODES[c]).save(src, compress_level=1)
        try:
            _run([cjxl, str(src), str(dst), "-d", "0", "-e", "7", "--quiet"])
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            return None
        out = dst.read_bytes()
    try:
        back = pixels_decode(out, h, w, c)
    except (subprocess.CalledProcessError, OSError, ValueError):
        return None
    return out if np.array_equal(back, px) else None


def pixels_decode(jxl: bytes, h: int, w: int, c: int) -> np.ndarray:
    _, djxl = _need()
    with tempfile.TemporaryDirectory(prefix="crush-jxl-") as tmp:
        src, dst = Path(tmp, "in.jxl"), Path(tmp, "out.png")
        src.write_bytes(jxl)
        _run([djxl, str(src), str(dst), "--quiet"])
        with Image.open(dst) as im:
            im = im.convert(_MODES[c])
            arr = np.asarray(im, dtype=np.uint8)
    return arr.reshape(h, w, c)

"""Storage directories and collision-safe file naming.

Layout (root configurable via ``CRUSH_STORAGE``)::

    uploads/       originals received from the UI (temporary)
    compressed/    *.crush archives only
    decompressed/  restored files only, original filename preserved

Compressed and decompressed outputs never share a directory, and an existing
file is never overwritten: a ``" (1)"``, ``" (2)"`` ... suffix is appended.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

from .config import settings

Kind = Literal["uploads", "compressed", "decompressed"]

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def ensure_dirs() -> None:
    """Create the three storage directories if they are missing."""
    for d in (settings.uploads, settings.compressed, settings.decompressed):
        d.mkdir(parents=True, exist_ok=True)


def directory(kind: Kind) -> Path:
    return {"uploads": settings.uploads, "compressed": settings.compressed,
            "decompressed": settings.decompressed}[kind]


def safe_name(name: str) -> str:
    """Strip any directory part and characters that are illegal on Windows."""
    base = name.replace("\\", "/").split("/")[-1]
    base = _UNSAFE.sub("_", base).strip(" .")
    return base or "file"


def unique_path(folder: Path, name: str) -> Path:
    """Return ``folder/name``, or ``folder/stem (k)suffix`` for the first free k."""
    folder.mkdir(parents=True, exist_ok=True)
    name = safe_name(name)
    candidate = folder / name
    stem, suffix = os.path.splitext(name)
    k = 1
    while candidate.exists():
        candidate = folder / f"{stem} ({k}){suffix}"
        k += 1
    return candidate


def write_new(folder: Path, name: str, data: bytes) -> Path:
    """Atomically create a new file (exclusive open) with a collision-safe name."""
    while True:
        path = unique_path(folder, name)
        try:
            with open(path, "xb") as fh:
                fh.write(data)
            return path
        except FileExistsError:  # lost a race with another writer; try next suffix
            continue


def resolve_inside(kind: Kind, name: str) -> Path:
    """Map a user-supplied file name to a path inside a storage dir, refusing traversal."""
    folder = directory(kind).resolve()
    path = (folder / safe_name(name)).resolve()
    if path.parent != folder:
        raise ValueError("invalid file name")
    return path

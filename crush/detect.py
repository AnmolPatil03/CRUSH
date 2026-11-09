"""Container detection by magic bytes (never by file extension)."""

from __future__ import annotations

from enum import IntEnum


class Container(IntEnum):
    TEXT = 0   # anything that is not ZIP or PDF is modelled as a byte/text stream
    ZIP = 1    # DOCX (and any other OOXML / ZIP container)
    PDF = 2


def detect(data: bytes) -> Container:
    """``PK\\x03\\x04`` -> ZIP/DOCX, ``%PDF`` within the first 1 KiB -> PDF, else TEXT."""
    if data[:4] == b"PK\x03\x04":
        return Container.ZIP
    if b"%PDF-" in data[:1024]:
        return Container.PDF
    return Container.TEXT

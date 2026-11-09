"""Binary layout of a ``.crush`` archive (see docs/FORMAT.md for the full specification).

::

    magic      6  b"CRUSH\\x1a"
    version    u8
    mode       u8   0 = lossless, 1 = optimised (lossy images)
    flags      u8   reserved (0)
    n_files    varint
    file[i]    name (varint length + UTF-8), original size (varint),
               SHA-256 of the original (32 bytes), container type (u8)
    manifest   stream record (codec, params, raw length, payload) - JSON rebuild recipe
    n_streams  varint
    stream[j]  type u8, stream record, CRC-32 of the raw stream (u32)
    trailer    CRC-32 of every preceding byte (u32)

A stream record is ``codec u8, len(params) varint, params, raw_len varint,
len(payload) varint, payload``.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

from ..errors import BadMagic, CorruptArchive, UnsupportedVersion

MAGIC = b"CRUSH\x1a"
VERSION = 1
MODE_LOSSLESS = 0
MODE_OPTIMISED = 1


def put_varint(out: bytearray, v: int) -> None:
    """LEB128 unsigned varint."""
    if v < 0:
        raise ValueError("varint must be non-negative")
    while v >= 0x80:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v)


class Reader:
    """Bounds-checked reader: any overrun raises :class:`CorruptArchive`."""

    def __init__(self, data: bytes) -> None:
        self.data, self.pos = data, 0

    def take(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise CorruptArchive("unexpected end of data")
        b = self.data[self.pos:self.pos + n]
        self.pos += n
        return b

    def u8(self) -> int:
        return self.take(1)[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def varint(self) -> int:
        v = shift = 0
        while True:
            b = self.u8()
            v |= (b & 0x7F) << shift
            if b < 0x80:
                return v
            shift += 7
            if shift > 63:
                raise CorruptArchive("varint too long")


@dataclass
class StreamRecord:
    codec: int
    params: bytes
    raw_len: int
    payload: bytes
    type: int = 0
    crc: int = 0


@dataclass
class FileEntry:
    name: str
    size: int
    sha256: bytes
    container: int


@dataclass
class Archive:
    mode: int
    files: list[FileEntry]
    manifest: StreamRecord
    streams: list[StreamRecord] = field(default_factory=list)


def _write_record(out: bytearray, r: StreamRecord) -> None:
    out.append(r.codec)
    put_varint(out, len(r.params))
    out += r.params
    put_varint(out, r.raw_len)
    put_varint(out, len(r.payload))
    out += r.payload


def _read_record(rd: Reader) -> StreamRecord:
    codec = rd.u8()
    params = rd.take(rd.varint())
    raw_len = rd.varint()
    payload = rd.take(rd.varint())
    return StreamRecord(codec, params, raw_len, payload)


def serialise(a: Archive) -> bytes:
    out = bytearray(MAGIC)
    out += bytes([VERSION, a.mode, 0])
    put_varint(out, len(a.files))
    for f in a.files:
        name = f.name.encode("utf-8")
        put_varint(out, len(name))
        out += name
        put_varint(out, f.size)
        out += f.sha256
        out.append(f.container)
    _write_record(out, a.manifest)
    put_varint(out, len(a.streams))
    for s in a.streams:
        out.append(s.type)
        _write_record(out, s)
        out += struct.pack("<I", s.crc)
    out += struct.pack("<I", zlib.crc32(out))
    return bytes(out)


def read_header(data: bytes) -> tuple[Reader, int, list[FileEntry]]:
    """Parse and validate up to the file table (cheap: used for listings)."""
    if data[:6] != MAGIC:
        raise BadMagic()
    rd = Reader(data)
    rd.take(6)
    version = rd.u8()
    if version > VERSION:
        raise UnsupportedVersion(version)
    mode = rd.u8()
    rd.u8()
    files = []
    for _ in range(rd.varint()):
        name = rd.take(rd.varint()).decode("utf-8", errors="replace")
        size = rd.varint()
        sha = rd.take(32)
        files.append(FileEntry(name, size, sha, rd.u8()))
    return rd, mode, files


def parse(data: bytes) -> Archive:
    if len(data) < 4 or data[:6] != MAGIC:
        raise BadMagic()
    if zlib.crc32(data[:-4]) != struct.unpack("<I", data[-4:])[0]:
        raise CorruptArchive("archive CRC-32 mismatch (file truncated or damaged)")
    rd, mode, files = read_header(data)
    manifest = _read_record(rd)
    streams = []
    for _ in range(rd.varint()):
        t = rd.u8()
        r = _read_record(rd)
        r.type = t
        r.crc = rd.u32()
        streams.append(r)
    if rd.pos != len(data) - 4:
        raise CorruptArchive("trailing bytes after the last stream")
    return Archive(mode, files, manifest, streams)

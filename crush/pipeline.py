"""End-to-end compression / decompression of one or more files into a .crush archive.

compress:   detect -> container recipe (Builder fills typed streams) -> encode every
            stream (in parallel when worthwhile) -> serialise -> decompress again and
            check SHA-256 of every file (files that fail are re-packed verbatim).
decompress: parse -> decode streams -> evaluate each file's recipe -> check SHA-256.
"""

from __future__ import annotations

import hashlib
import multiprocessing as mp
import time
import zlib
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from . import codecs
from .archive import format as fmt
from .archive.manifest import Builder, Recipe, dumps, evaluate, loads
from .codecs import Codec, StreamType
from .config import settings
from .containers import pdf, zipc
from .detect import Container, detect
from .errors import ChecksumMismatch, CorruptArchive

Progress = Callable[[dict[str, Any]], None]


@dataclass
class StreamReport:
    type: str
    codec: str
    raw: int
    packed: int
    note: str


@dataclass
class FileReport:
    name: str
    container: str
    original: int
    sha256: str
    restored_ok: bool = False
    fallback: bool = False


@dataclass
class Report:
    mode: str
    files: list[FileReport]
    archive_size: int
    original_size: int
    streams: list[StreamReport] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
    dedup_bytes: int = 0
    seconds: float = 0.0
    verified: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def saved_pct(self) -> float:
        return 100.0 * (1 - self.archive_size / self.original_size) if self.original_size else 0.0

    @property
    def ratio(self) -> float:
        return self.original_size / self.archive_size if self.archive_size else 0.0

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.update(saved_pct=round(self.saved_pct, 3), ratio=round(self.ratio, 4))
        text = [f for f in self.files if f.container == "TEXT"]
        if text and len(text) == len(self.files):
            d["bits_per_char"] = round(8 * self.archive_size / max(1, self.original_size), 4)
        return d


def _recipe_for(b: Builder, data: bytes, safe: bool) -> tuple[Container, Recipe]:
    kind = detect(data)
    if safe:
        return kind, b.add(StreamType.BINARY, data)
    if kind == Container.ZIP:
        return kind, zipc.recipe(b, data)
    if kind == Container.PDF:
        return kind, pdf.recipe(b, data)
    return kind, b.add(StreamType.TEXT, data)


def _encode_all(jobs: list[tuple[int, bytes, dict[str, Any]]], workers: int, progress: Progress | None):
    total = sum(len(j[1]) for j in jobs)
    results: list[Any] = [None] * len(jobs)
    order = sorted(range(len(jobs)), key=lambda i: -len(jobs[i][1]))  # largest first
    if workers > 1 and len(jobs) > 1 and total >= settings.parallel_threshold:
        ctx = mp.get_context("spawn")  # identical behaviour on Windows, Linux and macOS
        with ProcessPoolExecutor(max_workers=min(workers, len(jobs)), mp_context=ctx) as ex:
            futs = {ex.submit(codecs.encode_job, jobs[i]): i for i in order}
            for done, fut in enumerate(as_completed(futs), 1):
                i = futs[fut]
                results[i] = fut.result()
                _emit(progress, jobs, i, done, results[i])
    else:
        for done, i in enumerate(order, 1):
            results[i] = codecs.encode_job(jobs[i])
            _emit(progress, jobs, i, done, results[i])
    return results


def _emit(progress: Progress | None, jobs, i: int, done: int, res) -> None:
    if progress:
        progress({"stage": "stream", "done": done, "total": len(jobs),
                  "stream": StreamType(jobs[i][0]).name, "raw": len(jobs[i][1]), "packed": len(res[2]),
                  "codec": Codec(res[0]).name})


def _pack(items: list[tuple[str, bytes]], mode: int, safe: set[int], flags: int | None,
          workers: int, progress: Progress | None) -> tuple[bytes, Report]:
    b = Builder()
    entries, recipes, reports = [], [], []
    for k, (name, data) in enumerate(items):
        if progress:
            progress({"stage": "parse", "file": name, "index": k, "total": len(items)})
        kind, rec = _recipe_for(b, data, k in safe)
        sha = hashlib.sha256(data).digest()
        entries.append(fmt.FileEntry(name, len(data), sha, int(kind)))
        recipes.append(rec)
        reports.append(FileReport(name, kind.name, len(data), sha.hex(), fallback=k in safe))
    jobs = []
    for stype, buf, hint in b.streams:
        h = dict(hint)
        if flags is not None and "kind" not in h:
            h["flags"] = flags
        jobs.append((stype, bytes(buf), h))
    results = _encode_all(jobs, workers, progress)
    streams = []
    srep = []
    for (stype, raw, _), (codec, params, payload, note) in zip(jobs, results, strict=True):
        streams.append(fmt.StreamRecord(int(codec), params, len(raw), payload, stype, zlib.crc32(raw)))
        srep.append(StreamReport(StreamType(stype).name, Codec(codec).name, len(raw), len(payload), note))
    mjson = dumps(recipes)
    mc, mp_, mpay, _ = codecs.encode_general(mjson)
    manifest = fmt.StreamRecord(int(mc), mp_, len(mjson), mpay)
    blob = fmt.serialise(fmt.Archive(mode, entries, manifest, streams))
    rep = Report("optimised" if mode == fmt.MODE_OPTIMISED else "lossless", reports, len(blob),
                 sum(len(d) for _, d in items), srep, dict(b.stats), b.dedup_bytes)
    srep.append(StreamReport("MANIFEST", Codec(mc).name, len(mjson), len(mpay), "recipe"))
    if any(s.note == "jxl-tools-missing" for s in srep):
        rep.notes.append("JPEG XL tools not installed: JPEG images were stored without transcoding")
    return blob, rep


def compress(items: list[tuple[str, bytes]], mode: int = fmt.MODE_LOSSLESS, verify: bool = True,
             workers: int | None = None, progress: Progress | None = None, flags: int | None = None) -> tuple[bytes, Report]:
    """Pack ``(name, bytes)`` items into one archive (same-type streams are solid across files)."""
    t0 = time.perf_counter()
    workers = settings.workers if workers is None else workers
    safe: set[int] = set()
    blob, rep = _pack(items, mode, safe, flags, workers, progress)
    if verify:
        if progress:
            progress({"stage": "verify"})
        bad = _verify(blob, items)
        if bad:
            # Defensive net: any file that does not round-trip is re-packed verbatim.
            safe |= bad
            blob, rep = _pack(items, mode, safe, flags, workers, progress)
            bad = _verify(blob, items)
            if bad:
                raise ChecksumMismatch(items[min(bad)][0])
            rep.notes.append(f"{len(safe)} file(s) failed structural round-trip and were stored verbatim")
        rep.verified = True
        for f in rep.files:
            f.restored_ok = True
    rep.seconds = time.perf_counter() - t0
    if progress:
        progress({"stage": "done"})
    return blob, rep


def _verify(blob: bytes, items: list[tuple[str, bytes]]) -> set[int]:
    try:
        out = decompress(blob, check=False)
    except Exception:
        return set(range(len(items)))
    return {k for k, ((_, a), (_, b)) in enumerate(zip(items, out, strict=True)) if a != b}


def decompress(blob: bytes, check: bool = True, progress: Progress | None = None) -> list[tuple[str, bytes]]:
    """Restore every file of an archive; raises on corruption or SHA-256 mismatch."""
    arc = fmt.parse(blob)
    streams = []
    for k, s in enumerate(arc.streams):
        raw = codecs.decode(s.codec, s.params, s.payload, s.raw_len)
        if len(raw) != s.raw_len or zlib.crc32(raw) != s.crc:
            raise CorruptArchive(f"stream {k} ({StreamType(s.type).name}) failed its CRC-32 check")
        streams.append(raw)
        if progress:
            progress({"stage": "stream", "done": k + 1, "total": len(arc.streams)})
    m = arc.manifest
    recipes = loads(codecs.decode(m.codec, m.params, m.payload, m.raw_len))
    if len(recipes) != len(arc.files):
        raise CorruptArchive("manifest and file table disagree")
    out = []
    for f, r in zip(arc.files, recipes, strict=True):
        data = evaluate(r, streams)
        if check and (len(data) != f.size or hashlib.sha256(data).digest() != f.sha256):
            raise ChecksumMismatch(f.name)
        out.append((f.name, data))
    return out


def info(blob: bytes) -> dict[str, Any]:
    """Header-only listing (no decoding)."""
    _, mode, files = fmt.read_header(blob)
    return {"mode": "optimised" if mode else "lossless",
            "files": [{"name": f.name, "size": f.size, "sha256": f.sha256.hex(),
                       "container": Container(f.container).name} for f in files]}

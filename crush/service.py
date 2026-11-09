"""File-level operations shared by the CLI and the web API (storage-aware)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import pipeline, storage
from .archive import format as fmt
from .pipeline import Progress

ALLOWED = (".txt", ".docx", ".pdf")


def compress_to_storage(items: list[tuple[str, bytes]], mode: str = "lossless",
                        progress: Progress | None = None, archive_name: str | None = None) -> tuple[Path, dict[str, Any]]:
    """Compress ``items`` into one archive in ``compressed/``; returns (path, report dict)."""
    storage.ensure_dirs()
    mode_id = fmt.MODE_OPTIMISED if mode == "optimised" else fmt.MODE_LOSSLESS
    lossy_info: list[dict[str, Any]] = []
    originals = {name: len(data) for name, data in items}
    if mode_id == fmt.MODE_OPTIMISED:
        from .lossy import optimise
        new_items = []
        for name, data in items:
            if progress:
                progress({"stage": "optimise", "file": name})
            out, info = optimise.optimise(name, data)
            lossy_info.append(info)
            new_items.append((name, out))
        items = new_items
    blob, rep = pipeline.compress(items, mode=mode_id, progress=progress)
    name = archive_name or (items[0][0] if len(items) == 1 else "bundle")
    path = storage.write_new(storage.directory("compressed"), name + ".crush", blob)
    d = rep.as_dict()
    if mode_id == fmt.MODE_OPTIMISED:
        # compare against what the user uploaded, not the optimised intermediate
        d["original_size"] = sum(originals.values())
        d["saved_pct"] = round(100 * (1 - rep.archive_size / d["original_size"]), 3) if d["original_size"] else 0
        d["ratio"] = round(d["original_size"] / rep.archive_size, 4) if rep.archive_size else 0
        d["optimised"] = lossy_info
        d["notes"].append("Optimised mode: images were re-encoded; output is visually equivalent, "
                          "not byte-identical to the upload. SHA-256 verifies the optimised document.")
    d["archive"] = path.name
    return path, d


def decompress_to_storage(archive: Path, progress: Progress | None = None) -> list[Path]:
    storage.ensure_dirs()
    out = []
    for name, data in pipeline.decompress(archive.read_bytes(), progress=progress):
        out.append(storage.write_new(storage.directory("decompressed"), name, data))
    return out


def listing(kind: storage.Kind) -> list[dict[str, Any]]:
    storage.ensure_dirs()
    rows = []
    for p in sorted(storage.directory(kind).iterdir(), key=lambda p: -p.stat().st_mtime):
        if not p.is_file():
            continue
        row: dict[str, Any] = {"name": p.name, "size": p.stat().st_size, "modified": p.stat().st_mtime}
        if kind == "compressed":
            try:
                row.update(pipeline.info(p.read_bytes()[:1 << 16]))
                row["original_size"] = sum(f["size"] for f in row["files"])
            except Exception as e:  # listing must not fail because of one damaged archive
                row["error"] = str(e)
        rows.append(row)
    return rows


def delete(kind: storage.Kind, name: str) -> None:
    os.remove(storage.resolve_inside(kind, name))


def report_text(d: dict[str, Any]) -> str:
    lines = [f"{d.get('archive', '')}  [{d['mode']}]",
             f"  {d['original_size']:,} B -> {d['archive_size']:,} B   saved {d['saved_pct']:.2f}%   "
             f"ratio {d['ratio']:.3f}   {d['seconds']:.2f}s   SHA-256 {'verified' if d['verified'] else 'NOT verified'}"]
    if "bits_per_char" in d:
        lines.append(f"  {d['bits_per_char']:.3f} bits/char")
    for s in d["streams"]:
        lines.append(f"    {s['type']:<13} {s['codec']:<10} {s['raw']:>10,} -> {s['packed']:>10,}  ({s['note']})")
    if d.get("stats"):
        lines.append("  " + ", ".join(f"{k}={v}" for k, v in sorted(d["stats"].items())))
    lines += [f"  note: {n}" for n in d.get("notes", [])]
    return "\n".join(lines)

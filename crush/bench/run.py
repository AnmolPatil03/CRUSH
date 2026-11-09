"""Benchmark CRUSH against zlib / bz2 / lzma / zstd, plus ablations and image codecs.

Every compressor's output is decompressed and compared, so a benchmark number
is never reported for a result that does not round-trip.  Peak memory is the
peak resident set size of a fresh worker process per format group (Numba
allocations are invisible to ``tracemalloc``, so the OS figure is used).
"""

from __future__ import annotations

import multiprocessing as mp
import sys
import time
from pathlib import Path
from typing import Any, Callable

from ..archive.manifest import Builder
from ..cm import engine
from ..detect import Container, detect
from .baselines import BASELINES

FORMATS = {".txt": "txt", ".docx": "docx", ".pdf": "pdf"}


def peak_rss_mb() -> float:
    """Peak resident memory of this process in MiB (Windows, Linux, macOS)."""
    if sys.platform == "win32":
        import psutil
        return psutil.Process().memory_info().peak_wset / 2**20
    import resource
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 2**20 if sys.platform == "darwin" else peak / 1024


def _timed(fn: Callable[[], Any]) -> tuple[Any, float]:
    t = time.perf_counter()
    out = fn()
    return out, time.perf_counter() - t


def _bench_group(paths: list[str]) -> dict[str, Any]:
    """Runs in a fresh process: every file through CRUSH and each baseline."""
    from .. import pipeline
    rows = []
    for p in paths:
        data = Path(p).read_bytes()
        row: dict[str, Any] = {"file": Path(p).name, "size": len(data), "results": {}}
        (blob, rep), tc = _timed(lambda: pipeline.compress([(Path(p).name, data)], verify=False, workers=1))
        out, td = _timed(lambda: pipeline.decompress(blob))
        assert out[0][1] == data, f"CRUSH round-trip failed for {p}"
        row["results"]["crush"] = {"size": len(blob), "enc_s": tc, "dec_s": td}
        row["stats"] = rep.stats
        for name, (c, d) in BASELINES.items():
            z, tc = _timed(lambda: c(data))
            back, td = _timed(lambda: d(z))
            assert back == data
            row["results"][name] = {"size": len(z), "enc_s": tc, "dec_s": td}
        rows.append(row)
    return {"rows": rows, "peak_rss_mb": peak_rss_mb()}


def _run_isolated(fn: Callable[..., dict[str, Any]], *args: Any) -> dict[str, Any]:
    ctx = mp.get_context("spawn")
    with ctx.Pool(1) as pool:
        return pool.apply(fn, args)


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = sum(r["size"] for r in rows)
    out: dict[str, Any] = {"files": len(rows), "bytes": total, "codecs": {}}
    for name in rows[0]["results"] if rows else []:
        size = sum(r["results"][name]["size"] for r in rows)
        enc = sum(r["results"][name]["enc_s"] for r in rows)
        dec = sum(r["results"][name]["dec_s"] for r in rows)
        out["codecs"][name] = {"size": size, "saved_pct": 100 * (1 - size / total) if total else 0,
                               "bits_per_byte": 8 * size / total if total else 0,
                               "enc_MBps": total / enc / 1e6 if enc else 0,
                               "dec_MBps": total / dec / 1e6 if dec else 0}
    return out


def ablation(paths: list[Path]) -> list[dict[str, Any]]:
    """Text-model ablation: each row adds one component (sizes are CM payload bytes)."""
    from .. import pipeline
    datas = [p.read_bytes() for p in paths]
    total = sum(len(d) for d in datas)
    steps = [("orders 0-6 only (fixed weights)", 0),
             ("+ learned mixer", engine.F_MIXER),
             ("+ APM/SSE", engine.F_MIXER | engine.F_SSE),
             ("+ word + column/record models", engine.F_MIXER | engine.F_SSE | engine.F_WORD | engine.F_RECORD),
             ("+ match model", engine.FULL)]
    rows = []
    for label, flags in steps:
        size = sum(len(pipeline.compress([("f", d)], verify=False, workers=1, flags=flags)[0]) for d in datas)
        rows.append({"step": label, "size": size, "bits_per_char": 8 * size / total})
    solid = len(pipeline.compress([(f"f{k}", d) for k, d in enumerate(datas)], verify=False, workers=1)[0])
    rows.append({"step": "+ solid mode (all files in one archive)", "size": solid, "bits_per_char": 8 * solid / total})
    return rows


def image_codecs(paths: list[Path]) -> list[dict[str, Any]]:
    """For every image found in the documents, the size under each candidate codec."""
    import numpy as np

    from .. import codecs
    from ..containers import pdf, zipc
    from ..images import jxl, pyramid

    rows = []
    for p in paths:
        data = p.read_bytes()
        kind = detect(data)
        if kind == Container.TEXT:
            continue
        b = Builder()
        (zipc.recipe if kind == Container.ZIP else pdf.recipe)(b, data)
        for _, img, hint in b.streams:
            if hint.get("kind") != "pixels" or len(img) < 4096:
                continue
            w, h, c = hint["w"], hint["h"], hint["c"]
            px = np.frombuffer(img, dtype=np.uint8).reshape(h, w, c)
            row = {"file": p.name, "w": w, "h": h, "c": c, "raw": len(img),
                   "cm": len(codecs.encode_general(bytes(img), record_len=w * c)[2])}
            pyr = pyramid.encode(px) if h >= 32 and w >= 32 else None
            row["pyramid"] = len(pyr) if pyr else None
            if jxl.available():
                j = jxl.pixels_encode(px)
                row["jxl"] = len(j) if j else None
            rows.append(row)
    return rows


def run(folder: Path, progress: Callable[[str], None] | None = None, ablate: bool = True) -> dict[str, Any]:
    groups: dict[str, list[str]] = {}
    for p in sorted(folder.rglob("*")):
        fmt = FORMATS.get(p.suffix.lower())
        if p.is_file() and fmt:
            groups.setdefault(fmt, []).append(str(p))
    result: dict[str, Any] = {"folder": str(folder), "formats": {}}
    for fmt, paths in groups.items():
        if progress:
            progress(f"benchmarking {len(paths)} {fmt} file(s)")
        g = _run_isolated(_bench_group, paths)
        result["formats"][fmt] = {"summary": summarise(g["rows"]), "rows": g["rows"], "peak_rss_mb": g["peak_rss_mb"]}
    all_rows = [r for f in result["formats"].values() for r in f["rows"]]
    if all_rows:
        result["overall"] = summarise(all_rows)
    if ablate and "txt" in groups:
        if progress:
            progress("ablation on text files")
        result["ablation"] = ablation([Path(p) for p in groups["txt"]])
    docs = [Path(p) for k in ("docx", "pdf") for p in groups.get(k, [])]
    if docs:
        if progress:
            progress("image codec comparison")
        result["images"] = image_codecs(docs)
    return result


def _n(v: int | None) -> str:
    return f"{v:,}" if isinstance(v, int) else "-"


def markdown(res: dict[str, Any]) -> str:
    """Render benchmark results as Markdown tables."""
    out = [f"# CRUSH benchmark\n\nCorpus: `{res['folder']}`\n"]
    for fmt, f in list(res["formats"].items()) + ([("ALL", {"summary": res["overall"]})] if "overall" in res else []):
        s = f["summary"]
        out.append(f"\n## {fmt} - {s['files']} files, {s['bytes']:,} bytes"
                   + (f" (CRUSH peak memory {f['peak_rss_mb']:.0f} MiB)" if "peak_rss_mb" in f else "") + "\n")
        out.append("| codec | size | saved | bits/byte | enc MB/s | dec MB/s |\n|---|---:|---:|---:|---:|---:|")
        for name, c in sorted(s["codecs"].items(), key=lambda kv: kv[1]["size"]):
            out.append(f"| {name} | {c['size']:,} | {c['saved_pct']:.1f}% | {c['bits_per_byte']:.3f} "
                       f"| {c['enc_MBps']:.2f} | {c['dec_MBps']:.2f} |")
        if "rows" in f:
            out.append("\n| file | size | CRUSH | best baseline |\n|---|---:|---:|---:|")
            for r in f["rows"]:
                base = min((v["size"], k) for k, v in r["results"].items() if k != "crush")
                cr = r["results"]["crush"]["size"]
                out.append(f"| {r['file']} | {r['size']:,} | {100 * (1 - cr / r['size']):.1f}% "
                           f"| {100 * (1 - base[0] / r['size']):.1f}% ({base[1]}) |")
    if res.get("ablation"):
        out.append("\n## Ablation (text corpus)\n\n| configuration | bytes | bits/char |\n|---|---:|---:|")
        out += [f"| {r['step']} | {r['size']:,} | {r['bits_per_char']:.3f} |" for r in res["ablation"]]
    if res.get("images"):
        out.append("\n## Image codecs (lossless pixels, bytes)\n\n| file | size | raw | CM | pyramid | JPEG XL |\n"
                   "|---|---|---:|---:|---:|---:|")
        for r in res["images"]:
            out.append(f"| {r['file']} | {r['w']}x{r['h']}x{r['c']} | {r['raw']:,} | {_n(r['cm'])} | "
                       f"{_n(r['pyramid'])} | {_n(r.get('jxl'))} |")
    return "\n".join(out) + "\n"

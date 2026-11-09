"""Command line interface.

    crush compress <file>... [--mode lossless|optimised] [--solid NAME]
    crush decompress <file.crush>...
    crush info <file.crush>
    crush bench <dir> [--out results.json] [--md BENCHMARKS.md] [--no-ablation]
    crush serve [--host H] [--port P]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .errors import CrushError


def _compress(args: argparse.Namespace) -> int:
    from . import service
    items = [(Path(f).name, Path(f).read_bytes()) for f in args.files]
    groups = [items] if args.solid else [[it] for it in items]
    for group in groups:
        path, rep = service.compress_to_storage(group, args.mode, archive_name=args.solid)
        print(service.report_text(rep))
        print(f"  -> {path}")
    return 0


def _decompress(args: argparse.Namespace) -> int:
    from . import service
    for f in args.files:
        for p in service.decompress_to_storage(Path(f)):
            print(f"restored {p}  (SHA-256 verified)")
    return 0


def _info(args: argparse.Namespace) -> int:
    from . import pipeline
    print(json.dumps(pipeline.info(Path(args.file).read_bytes()), indent=2))
    return 0


def _bench(args: argparse.Namespace) -> int:
    from .bench import run
    res = run.run(Path(args.dir), progress=lambda m: print("..", m, file=sys.stderr), ablate=not args.no_ablation)
    md = run.markdown(res)
    print(md)
    if args.out:
        Path(args.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
    if args.md:
        Path(args.md).write_text(md, encoding="utf-8")
    return 0


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .config import settings
    uvicorn.run("crush.api.app:app", host=args.host or settings.host, port=args.port or settings.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="crush", description="CRUSH context-mixing document compressor")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compress", help="compress files into storage/compressed/")
    c.add_argument("files", nargs="+")
    c.add_argument("--mode", choices=["lossless", "optimised"], default="lossless")
    c.add_argument("--solid", metavar="NAME", help="pack all files into one solid archive NAME.crush")
    c.set_defaults(fn=_compress)
    d = sub.add_parser("decompress", help="restore archives into storage/decompressed/")
    d.add_argument("files", nargs="+")
    d.set_defaults(fn=_decompress)
    i = sub.add_parser("info", help="list an archive's contents")
    i.add_argument("file")
    i.set_defaults(fn=_info)
    b = sub.add_parser("bench", help="benchmark against zlib/bz2/lzma/zstd")
    b.add_argument("dir")
    b.add_argument("--out")
    b.add_argument("--md")
    b.add_argument("--no-ablation", action="store_true")
    b.set_defaults(fn=_bench)
    s = sub.add_parser("serve", help="run the web UI + API")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.set_defaults(fn=_serve)
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except CrushError as e:
        print(f"crush: error: {e}", file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(f"crush: error: {e.filename}: no such file", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

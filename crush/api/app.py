"""FastAPI backend: upload/compress, decompress, vault listing, downloads, progress (SSE), benchmarks.

Long operations run as *jobs* on a small thread pool; their progress events are
streamed to the browser with Server-Sent Events at ``/api/jobs/{id}/events``.
The built React UI (``ui/dist``) is served from ``/``.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import service, storage
from ..archive.format import MAGIC
from ..config import PROJECT_ROOT, settings
from ..errors import CrushError
from ..images import jxl

@asynccontextmanager
async def _lifespan(_: FastAPI):
    storage.ensure_dirs()
    yield


app = FastAPI(title="CRUSH", version="1.0", lifespan=_lifespan)
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="crush-job")


@dataclass
class Job:
    id: str
    kind: str
    label: str
    state: str = "queued"          # queued | running | done | error
    events: list[dict[str, Any]] = field(default_factory=list)
    result: Any = None
    error: str | None = None
    created: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def emit(self, ev: dict[str, Any]) -> None:
        with self.lock:
            self.events.append({**ev, "t": round(time.time() - self.created, 3)})

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "label": self.label, "state": self.state,
                "result": self.result, "error": self.error, "events": len(self.events)}


JOBS: dict[str, Job] = {}


def _submit(kind: str, label: str, fn: Callable[[Job], Any]) -> Job:
    job = Job(uuid.uuid4().hex[:12], kind, label)
    JOBS[job.id] = job

    def run() -> None:
        job.state = "running"
        job.emit({"stage": "start"})
        try:
            job.result = fn(job)
            job.state = "done"
        except CrushError as e:
            job.error, job.state = str(e), "error"
        except Exception as e:  # surface unexpected errors to the UI instead of hanging
            job.error, job.state = f"{type(e).__name__}: {e}", "error"
        job.emit({"stage": "end", "state": job.state})

    _pool.submit(run)
    return job


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "jxl": jxl.available(), "storage": str(settings.storage_root)}


@app.post("/api/compress")
async def compress(files: list[UploadFile] = File(...), mode: Literal["lossless", "optimised"] = Form("lossless")):
    storage.ensure_dirs()
    jobs = []
    for up in files:
        name = storage.safe_name(up.filename or "file")
        if not name.lower().endswith(service.ALLOWED):
            raise HTTPException(400, f"{name}: only .txt, .docx and .pdf are accepted")
        data = await up.read()
        if len(data) > settings.max_upload_mb * 2**20:
            raise HTTPException(413, f"{name}: larger than {settings.max_upload_mb} MB")
        tmp = storage.write_new(storage.directory("uploads"), name, data)

        def work(job: Job, tmp: Path = tmp, name: str = name) -> Any:
            try:
                _, rep = service.compress_to_storage([(name, tmp.read_bytes())], mode, progress=job.emit)
                return rep
            finally:
                tmp.unlink(missing_ok=True)  # uploads/ is temporary

        jobs.append(_submit("compress", name, work).public())
    return {"jobs": jobs}


@app.post("/api/decompress/{name}")
def decompress(name: str):
    path = storage.resolve_inside("compressed", name)
    if not path.is_file():
        raise HTTPException(404, "archive not found")

    def work(job: Job) -> Any:
        return {"restored": [p.name for p in service.decompress_to_storage(path, progress=job.emit)]}

    return _submit("decompress", name, work).public()


@app.post("/api/decompress-upload")
async def decompress_upload(files: list[UploadFile] = File(...)):
    """Decompress .crush archives uploaded from the browser (the main page's Decompress mode)."""
    storage.ensure_dirs()
    jobs = []
    for up in files:
        name = storage.safe_name(up.filename or "archive.crush")
        data = await up.read()
        if not data.startswith(MAGIC):
            raise HTTPException(400, f"{name}: not a .crush archive")
        if len(data) > settings.max_upload_mb * 2**20:
            raise HTTPException(413, f"{name}: larger than {settings.max_upload_mb} MB")
        tmp = storage.write_new(storage.directory("uploads"), name, data)

        def work(job: Job, tmp: Path = tmp, name: str = name, size: int = len(data)) -> Any:
            try:
                out = service.decompress_to_storage(tmp, progress=job.emit)
            finally:
                tmp.unlink(missing_ok=True)  # uploads/ is temporary
            return {"archive": name, "archive_size": size, "restored": [p.name for p in out],
                    "files": [{"name": p.name, "size": p.stat().st_size} for p in out]}

        jobs.append(_submit("decompress", name, work).public())
    return {"jobs": jobs}


class BenchRequest(BaseModel):
    folder: str = "corpus"
    ablation: bool = True


@app.post("/api/bench")
def bench(req: BenchRequest):
    folder = Path(req.folder)
    folder = folder if folder.is_absolute() else PROJECT_ROOT / folder
    if not folder.is_dir():
        raise HTTPException(400, f"folder not found: {folder}")
    from ..bench import run

    def work(job: Job) -> Any:
        res = run.run(folder, progress=lambda m: job.emit({"stage": "bench", "message": m}), ablate=req.ablation)
        return res

    return _submit("bench", str(folder), work).public()


@app.get("/api/jobs/{job_id}")
def job_state(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    return job.public()


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")

    async def stream():
        sent = 0
        while True:
            with job.lock:
                new = job.events[sent:]
            for ev in new:
                yield f"data: {json.dumps(ev)}\n\n"
            sent += len(new)
            if job.state in ("done", "error") and sent >= len(job.events):
                yield f"event: final\ndata: {json.dumps(job.public())}\n\n"
                return
            await asyncio.sleep(0.15)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


Kind = Literal["compressed", "decompressed"]


@app.get("/api/files/{kind}")
def files(kind: Kind):
    return service.listing(kind)


@app.get("/api/files/{kind}/{name}")
def download(kind: Kind, name: str):
    path = storage.resolve_inside(kind, name)
    if not path.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(path, filename=path.name)


@app.delete("/api/files/{kind}/{name}")
def remove(kind: Kind, name: str):
    try:
        service.delete(kind, name)
    except FileNotFoundError:
        raise HTTPException(404, "file not found") from None
    return {"deleted": name}


_DIST = PROJECT_ROOT / "ui" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="ui")

"""Phases 7-8: storage rules, CLI, web API and optimised (lossy) mode."""

import io
import time

from fastapi.testclient import TestClient

from crush import cli, service, storage
from crush.lossy import optimise
from tests import fixtures as fx


def test_storage_never_overwrites_and_sanitises(tmp_path):
    a = storage.write_new(tmp_path, "report.docx", b"1")
    b = storage.write_new(tmp_path, "report.docx", b"2")
    c = storage.write_new(tmp_path, "../../evil:name?.txt", b"3")
    assert (a.name, b.name) == ("report.docx", "report (1).docx")
    assert a.read_bytes() == b"1" and c.parent == tmp_path and ":" not in c.name
    for evil in ("..", "../x", "..\\..\\x", "/etc/passwd"):
        assert storage.resolve_inside("compressed", evil).parent == storage.directory("compressed").resolve()


def test_compressed_and_decompressed_dirs_are_separate():
    path, rep = service.compress_to_storage([("a.txt", b"hello world " * 100)])
    assert path.parent == storage.directory("compressed") and path.suffix == ".crush"
    out = service.decompress_to_storage(path)
    assert out[0].parent == storage.directory("decompressed") and out[0].name == "a.txt"
    again = service.decompress_to_storage(path)
    assert again[0].name == "a (1).txt"  # collision-safe
    assert storage.directory("compressed") != storage.directory("decompressed")


def test_cli_roundtrip(tmp_path, capsys):
    src = tmp_path / "doc.txt"
    src.write_text(fx.prose(400), encoding="utf-8")
    assert cli.main(["compress", str(src)]) == 0
    arc = storage.directory("compressed") / "doc.txt.crush"
    assert cli.main(["decompress", str(arc)]) == 0
    assert (storage.directory("decompressed") / "doc.txt").read_bytes() == src.read_bytes()
    bad = tmp_path / "bad.crush"
    bad.write_bytes(b"garbage")
    assert cli.main(["decompress", str(bad)]) == 2
    assert "bad magic" in capsys.readouterr().err


def _wait(client, job_id):
    for _ in range(600):
        st = client.get(f"/api/jobs/{job_id}").json()
        if st["state"] in ("done", "error"):
            return st
        time.sleep(0.1)
    raise TimeoutError


def test_api_flow():
    from crush.api.app import app
    with TestClient(app) as client:
        assert client.get("/api/health").json()["ok"]
        r = client.post("/api/compress", data={"mode": "lossless"},
                        files=[("files", ("r.docx", fx.make_docx(4), "application/octet-stream"))])
        job = r.json()["jobs"][0]
        st = _wait(client, job["id"])
        assert st["state"] == "done" and st["result"]["verified"]
        events = client.get(f"/api/jobs/{job['id']}/events").text
        assert "stream" in events and "event: final" in events
        names = [f["name"] for f in client.get("/api/files/compressed").json()]
        assert "r.docx.crush" in names
        st = _wait(client, client.post("/api/decompress/r.docx.crush").json()["id"])
        assert st["result"]["restored"] == ["r.docx"]
        assert client.get("/api/files/decompressed/r.docx").content == client.get(
            "/api/files/decompressed/r.docx").content
        archive = client.get("/api/files/compressed/r.docx.crush").content
        up = client.post("/api/decompress-upload", files=[("files", ("r.docx.crush", archive, "application/octet-stream"))])
        st = _wait(client, up.json()["jobs"][0]["id"])
        assert st["state"] == "done" and st["result"]["restored"] == ["r (1).docx"]  # never overwrites
        assert client.get("/api/files/decompressed/r (1).docx").content == \
            client.get("/api/files/decompressed/r.docx").content
        bad = client.post("/api/decompress-upload", files=[("files", ("x.crush", b"nope", "application/octet-stream"))])
        assert bad.status_code == 400
        assert client.delete("/api/files/compressed/r.docx.crush").status_code == 200
        assert client.post("/api/compress", files=[("files", ("x.exe", b"MZ", "application/octet-stream"))]).status_code == 400
        assert client.get("/api/files/compressed/..%2F..%2Fsecret").status_code == 404


def _big_photo_docx() -> bytes:
    import docx
    from docx.shared import Inches
    d = docx.Document()
    d.add_paragraph("photo")
    d.add_picture(io.BytesIO(fx.image_bytes(fx.photo(2000, 1500), "JPEG", quality=95)), width=Inches(4))
    d.add_picture(io.BytesIO(fx.image_bytes(fx.chart(), "PNG")), width=Inches(3))
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_optimised_docx_downsamples_photos_keeps_graphics():
    import docx
    data = _big_photo_docx()
    out, info = optimise.optimise("p.docx", data)
    assert info["changed"] and len(out) < len(data) / 3
    imgs = info["images"]
    assert len(imgs) == 1 and imgs[0]["dpi_after"] <= 205 and imgs[0]["kind"] == "photo"
    assert imgs[0]["to"][0] < imgs[0]["from"][0]   # never upsampled
    docx.Document(io.BytesIO(out))                   # still a valid document


def test_optimised_mode_end_to_end_reports_against_original():
    data = _big_photo_docx()
    path, rep = service.compress_to_storage([("p.docx", data)], "optimised")
    assert rep["mode"] == "optimised" and rep["original_size"] == len(data)
    assert rep["archive_size"] < len(data) / 3
    restored = service.decompress_to_storage(path)[0].read_bytes()
    assert restored != data and restored[:2] == b"PK"  # visually equivalent, not identical


def test_optimised_pdf():
    import pikepdf
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawImage(ImageReader(io.BytesIO(fx.image_bytes(fx.photo(2000, 1500), "JPEG", quality=95))), 50, 400, width=288, height=216)
    c.showPage()
    c.save()
    out, info = optimise.optimise("p.pdf", buf.getvalue())
    assert info["changed"] and info["images"][0]["dpi_after"] <= 205
    pikepdf.open(io.BytesIO(out))


def test_text_is_untouched_in_optimised_mode():
    out, info = optimise.optimise("a.txt", b"plain text")
    assert out == b"plain text" and not info["changed"]

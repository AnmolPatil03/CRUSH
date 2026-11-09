"""Runtime configuration for CRUSH.

Values come from (in increasing priority) the defaults below, a ``.env`` file in
the project root, and real environment variables.  Everything is read once at
import time into a frozen :class:`Settings` instance, ``settings``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> dict[str, str]:
    """Parse a minimal ``KEY=VALUE`` .env file (no dependency on python-dotenv)."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = re.split(r"\s+#", value, maxsplit=1)[0]  # drop trailing "  # comment"
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


_DOTENV = _load_dotenv(PROJECT_ROOT / ".env")


def _get(key: str, default: str) -> str:
    return os.environ.get(key, _DOTENV.get(key, default))


@dataclass(frozen=True)
class Settings:
    """All tunable settings.  Paths are absolute."""

    storage_root: Path
    cjxl: str
    djxl: str
    workers: int
    max_upload_mb: int
    parallel_threshold: int
    host: str
    port: int

    @property
    def uploads(self) -> Path:
        return self.storage_root / "uploads"

    @property
    def compressed(self) -> Path:
        return self.storage_root / "compressed"

    @property
    def decompressed(self) -> Path:
        return self.storage_root / "decompressed"


def _resolve(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


settings = Settings(
    storage_root=_resolve(_get("CRUSH_STORAGE", "storage")),
    cjxl=_get("CRUSH_CJXL", "cjxl"),
    djxl=_get("CRUSH_DJXL", "djxl"),
    # each worker holds its own CM tables (~200-400 MiB), so the default is capped at 4
    workers=int(_get("CRUSH_WORKERS", str(max(1, min(4, (os.cpu_count() or 2) - 1))))),
    max_upload_mb=int(_get("CRUSH_MAX_UPLOAD_MB", "200")),
    # Streams smaller than this (bytes, total) are coded in-process: spawning
    # worker processes costs more than it saves.
    parallel_threshold=int(_get("CRUSH_PARALLEL_THRESHOLD", str(2 * 1024 * 1024))),
    host=_get("CRUSH_HOST", "127.0.0.1"),
    port=int(_get("CRUSH_PORT", "8000")),
)

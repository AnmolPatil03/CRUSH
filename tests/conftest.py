import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CORPUS = ROOT / "corpus"


def corpus_files(kind: str) -> list[Path]:
    d = CORPUS / kind
    return sorted(d.iterdir()) if d.is_dir() else []


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    """Point storage at a temp dir so tests never touch the real storage/ folder."""
    from crush import config
    s = config.settings
    monkeypatch.setattr(config, "settings", type(s)(**{**s.__dict__, "storage_root": tmp_path / "storage"}))
    for mod in ("crush.storage",):
        m = sys.modules.get(mod)
        if m is not None:
            monkeypatch.setattr(m, "settings", config.settings)

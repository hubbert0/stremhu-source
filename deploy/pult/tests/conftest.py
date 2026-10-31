import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("PULT_USER", "admin")
    monkeypatch.setenv("PULT_PASSWORD", "titok123")
    monkeypatch.setenv("TAILNET", "pelda.ts.net")
    monkeypatch.setenv("PULT_DATA_DIR", str(tmp_path))
    return tmp_path

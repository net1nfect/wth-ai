"""Isolasi test: tidak ada test yang boleh menulis ke dalam repo.

Sebelum ini, `pytest` meninggalkan `data/engagements/`, `logs/engagement_logs/`,
dan `reports/` di working tree, sehingga review diff tercampur artefak test.
Semua penulis legacy sekarang membaca env var di bawah; fixture ini
mengarahkannya ke satu direktori sementara per sesi.

Fixture-nya function-scoped dan autouse: beberapa test lama mempop atau
mengganti `os.environ` langsung, jadi kalau env hanya dipasang sekali di awal
sesi, test berikutnya kehilangan isolasi. Memasang ulang per test membuat
isolasi tidak bergantung pada urutan.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_VARS = ("WTH_HOME", "WTH_ENGAGEMENT_DIR", "WTH_LOG_DIR", "WTH_REPORT_DIR")


@pytest.fixture(scope="session")
def isolated_root(tmp_path_factory) -> Path:
    """Direktori tulis bersama untuk seluruh sesi test."""
    root = tmp_path_factory.mktemp("wth-session")
    for sub in ("home", "data/engagements", "logs/engagement_logs", "reports"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture(autouse=True)
def _isolate_writes(isolated_root: Path, monkeypatch) -> Path:
    monkeypatch.setenv("WTH_HOME", str(isolated_root / "home"))
    monkeypatch.setenv("WTH_ENGAGEMENT_DIR", str(isolated_root / "data" / "engagements"))
    monkeypatch.setenv("WTH_LOG_DIR", str(isolated_root / "logs" / "engagement_logs"))
    monkeypatch.setenv("WTH_REPORT_DIR", str(isolated_root / "reports"))
    return isolated_root


@pytest.fixture(autouse=True)
def _restore_cwd(tmp_path):
    """Test yang chdir tidak boleh membawa direktori kerja ke test berikutnya."""
    before = os.getcwd()
    try:
        yield
    finally:
        try:
            os.chdir(before)
        except OSError:  # direktori lama sudah dihapus
            os.chdir(str(tmp_path))
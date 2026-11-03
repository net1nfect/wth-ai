"""Regression tests — WU-208 (ARCH-02): `wth_ai.cli.entry` robust launcher
resolution.

Bug asli: `entry.main()` hard-code `runpy.run_path(root / "wth-main.py")` --
hanya ada di source checkout; `pip install` wheel atau layout container beda
→ FileNotFoundError kriptik dalam runpy. Fix: `_find_main_script()` memprobe
kandidat lokasi (source/Docker `/app`/cwd) dan error JELAS kalau tak ada.

Scope jujur (didokumentasikan): ini memperbaiki launch source/Docker/cwd +
error message, TIDAK membuat `pip install` penuh bekerja (butuh wth-main.py
ter-package ke wheel / CLI dipindah jadi modul importable — perubahan lebih
besar yang butuh verifikasi build wheel). WU-208 tetap SEBAGIAN.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.cli import entry


def test_find_main_script_locates_source_checkout():
    """Di repo ini, wth-main.py ADA di root -- _find_main_script harus
    menemukannya."""
    root = Path(__file__).resolve().parents[1]
    found = entry._find_main_script(root)
    assert found is not None
    assert found.name == "wth-main.py"
    assert found.is_file()


def test_find_main_script_returns_none_when_absent(tmp_path, monkeypatch):
    """Layout tanpa wth-main.py di kandidat MANAPUN -> None (bukan exception),
    supaya main() bisa memberi error JELAS. Deterministik: paksa semua
    kandidat 'tidak ada' via monkeypatch Path.is_file."""
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    assert entry._find_main_script(tmp_path / "nope") is None


def test_find_main_script_prefers_existing_candidate(tmp_path):
    """Kalau root punya wth-main.py, itu yang dikembalikan (kandidat pertama)."""
    fake = tmp_path / "wth-main.py"
    fake.write_text("# stub\n", encoding="utf-8")
    found = entry._find_main_script(tmp_path)
    assert found == fake


def test_main_raises_clear_error_when_launcher_missing(monkeypatch):
    """main() memberi SystemExit dengan pesan actionable (bukan runpy
    FileNotFoundError kriptik) kalau launcher tak ketemu."""
    import pytest

    monkeypatch.setattr(entry, "_bootstrap", lambda: (Path("/nonexistent-root"), Path("/tmp")))
    monkeypatch.setattr(entry, "_find_main_script", lambda root: None)
    with pytest.raises(SystemExit) as exc:
        entry.main()
    assert "wth-main.py" in str(exc.value)
    assert "WU-208" in str(exc.value) or "entry point" in str(exc.value)

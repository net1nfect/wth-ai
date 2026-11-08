"""P1-8: verifikasi entry point CLI dari wheel/instalasi.

Audit menemukan `[project.scripts]` tidak ada, sehingga `pip install` wheel
tidak menghasilkan command `wth-ai`. Sebelumnya sengaja dihilangkan karena
wheel tidak ikut `wth-ai-main.py`; sekarang file itu masuk package-data, jadi
entry point harus berfungsi. Test ini memverifikasi:

1. `wth-ai` ter-deklarasi di `[project.scripts]` (setuptools).
2. `wth_ai.cli.entry:main` ada dan bisa diimport.
3. `wth-ai-main.py` ikut package-data (`wth_ai.cli`), jadi `entry.main`
   menemukan target runpy-nya tanpa data repo eksternal (P0-1 parity).
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_cli_entry_main_is_importable():
    """`wth_ai.cli.entry:main` harus ada (target console script)."""
    from wth_ai.cli.entry import main

    assert callable(main)


def test_entry_main_sees_wth_ai_main_py_beside_it():
    """entry.main mengandalkan `wth-ai-main.py` di samping entry.py.

    Ini kontrak P1-8: di wheel maupun repo, file itu harus hadir — kalau
    tidak, `python -m wth_ai.cli.entry` gagal.
    """
    from wth_ai.cli import entry

    entry_dir = Path(entry.__file__).resolve().parent
    main_script = entry_dir / "wth-ai-main.py"
    assert main_script.is_file(), f"wth-ai-main.py tidak ada di {entry_dir}"


def test_console_script_declared_in_pyproject():
    """`[project.scripts]` harus mendeklarasikan `wth-ai`."""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "[project.scripts]" in text, "pyproject.toml tidak punya [project.scripts]"
    assert "wth-ai = \"wth_ai.cli.entry:main\"" in text, (
        "console script wth-ai harus menunjuk wth_ai.cli.entry:main"
    )

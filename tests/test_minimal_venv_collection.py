"""P1-7: suite tetap bisa di-collect di venv tanpa dependensi opsional.

Venv minimal (hanya dependensi inti `pyproject.toml`) dulu menabrak 6 modul
test TUI karena `textual`/`aiohttp` hilang -- `pytest tests` berhenti sebelum
satu test pun jalan. Gate-nya: `--collect-only` harus 0 error.

Dependensi opsional diblokir lewat meta_path di subprocess, bukan dengan
mencabut paket dari venv nyata.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

_SCRIPT = textwrap.dedent(
    """
    import sys, pytest

    BLOCKED = {"textual", "aiohttp", "nodriver", "lxml", "bs4", "dns", "mcp"}

    class _Block:
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in BLOCKED:
                # Persis seperti paket yang benar-benar tidak terpasang;
                # `pytest.importorskip` hanya melewati ModuleNotFoundError.
                raise ModuleNotFoundError(
                    f"No module named {name.split('.')[0]!r}", name=name)
            return None

    sys.meta_path.insert(0, _Block())
    raise SystemExit(pytest.main([
        "tests", "--collect-only", "-q", "-p", "no:cacheprovider",
    ]))
    """
)


def test_suite_collects_without_optional_dependencies():
    proc = subprocess.run([sys.executable, "-c", _SCRIPT], cwd=REPO,
                          capture_output=True, text=True, timeout=300)
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, combined[-4000:]
    assert "error" not in combined.lower().split("tests collected")[-1], combined[-2000:]
    assert "tests collected" in combined
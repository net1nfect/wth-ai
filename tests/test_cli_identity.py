"""P2-10 / D5: `wth-ai` satu-satunya nama perintah; `wth` dihapus.

Sebelumnya grup Click memakai `wth`, npm mencetak versi sendiri
(`wth-cli v1.0.0`), dan `--help` menampilkan `Usage: wth-ai-main.py`. Test ini
mengunci nama program, satu sumber versi (`wth_ai.__version__`), dan key `bin`
npm ke satu nilai.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _run(args, home):
    env = {**os.environ, "WTH_HOME": str(home), "WTH_SKIP_SETUP": "1",
           "WTH_PLAIN": "1"}
    return subprocess.run([sys.executable, "wth-ai-main.py", *args], cwd=REPO,
                          capture_output=True, text=True, timeout=120, env=env)


def test_version_prints_the_package_version(tmp_path):
    proc = _run(["--version"], tmp_path)
    assert proc.returncode == 0, proc.stderr
    from wth_ai import __version__

    out = proc.stdout.strip()
    assert __version__ in out
    assert "wth-cli" not in out


def test_help_shows_the_canonical_program_name(tmp_path):
    proc = _run(["--help"], tmp_path)
    assert proc.returncode == 0, proc.stderr
    first = proc.stdout.splitlines()[0]
    assert first.startswith("Usage: wth-ai "), first


def test_npm_launcher_delegates_version_to_python():
    """npm tidak boleh punya versi sendiri: `--version` diteruskan ke Python."""
    src = (REPO / "packages" / "wth-ai-cli" / "bin" / "wth-ai.js").read_text()
    assert "wth-cli v${" not in src


def test_npm_package_exposes_one_name():
    pkg = json.loads((REPO / "packages" / "wth-ai-cli" / "package.json").read_text())
    assert pkg["bin"] == {"wth-ai": "bin/wth-ai.js"}
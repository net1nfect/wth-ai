"""`wth interactive --redteam` terdaftar; TUI bisa di-import tanpa layar."""

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_redteam_flag_is_advertised():
    env = {**os.environ, "WTH_SKIP_SETUP": "1", "WTH_PLAIN": "1",
           "COLUMNS": "120"}
    out = subprocess.run([sys.executable, "wth-ai-main.py", "interactive", "--help"],
                         cwd=REPO, capture_output=True, text=True, timeout=120,
                         env=env)
    assert out.returncode == 0, out.stderr
    assert "--redteam" in out.stdout
    assert "--mode" in out.stdout


def test_tui_module_entrypoint_help():
    env = {**os.environ, "WTH_SKIP_SETUP": "1", "WTH_PLAIN": "1"}
    out = subprocess.run([sys.executable, "-m", "wth_ai.cli.tui.redteam", "--help"],
                         cwd=REPO, capture_output=True, text=True, timeout=120,
                         env=env)
    assert out.returncode == 0, out.stderr
    assert "--demo" in out.stdout
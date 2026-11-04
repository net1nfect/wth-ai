"""`wth kill` memasang flag dan keluar 0."""

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_kill_command_writes_flag(tmp_path):
    env = {**os.environ, "WTH_HOME": str(tmp_path), "WTH_SKIP_SETUP": "1",
           "WTH_PLAIN": "1"}
    out = subprocess.run([sys.executable, "wth-main.py", "kill"], cwd=REPO,
                         capture_output=True, text=True, timeout=90, env=env)
    assert out.returncode == 0, out.stderr
    assert "kill:" in (out.stdout + out.stderr)
    assert (tmp_path / "runtime" / "KILL").exists()
"""CLI: scope card ditolak tanpa izin, ditulis dengan izin."""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _run(args, home):
    env = {**os.environ, "WTH_HOME": str(home), "WTH_SKIP_SETUP": "1",
           "WTH_PLAIN": "1"}
    return subprocess.run([sys.executable, "wth-main.py", *args], cwd=REPO,
                          capture_output=True, text=True, timeout=120, env=env)


def test_scope_without_authorization_is_refused(tmp_path):
    proc = _run(["scope", "--target", "172.28.0.10", "--cidr", "172.28.0.0/16"],
                tmp_path)
    assert proc.returncode == 2
    assert "DITOLAK" in proc.stdout
    assert not (tmp_path / "scope_card.json").exists()


def test_scope_with_authorization_writes_card(tmp_path):
    proc = _run(["scope", "--target", "172.28.0.10", "--cidr", "172.28.0.0/16",
                 "--authorized", "--mode", "auto"], tmp_path)
    assert proc.returncode == 0, proc.stderr
    data = json.loads((tmp_path / "scope_card.json").read_text())
    assert data["cidr"] == ["172.28.0.0/16"]
    assert data["authorized"] is True
    assert data["mode"] == "auto"
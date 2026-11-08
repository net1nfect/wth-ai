"""P1-2: config rusak dilaporkan sebagai `path:line`, bukan traceback.

`WTHConfig.load` memanggil `yaml.safe_load` tanpa menangkap `YAMLError`, jadi
`user.yaml: [unclosed` membuat `wth doctor` mati dengan traceback.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from wth_ai.config import ConfigError, load_user_config

REPO = Path(__file__).resolve().parents[1]


def _corrupt(tmp_path: Path) -> Path:
    path = tmp_path / "user.yaml"
    path.write_text("safety:\n  require_authorized: true\nproviders: [unclosed\n",
                    encoding="utf-8")
    return path


def test_error_names_the_file_and_line(tmp_path):
    path = _corrupt(tmp_path)
    with pytest.raises(ConfigError) as excinfo:
        load_user_config(path)
    message = str(excinfo.value)
    assert str(path) in message
    # Bentuk `path:line: ...`; nomor barisnya dari PyYAML (di sini: tempat
    # parser menyadari input habis), bukan tebakan kita.
    assert re.search(r":\d+: config YAML", message), message


def test_doctor_reports_instead_of_crashing(tmp_path):
    home = tmp_path
    path = home / "config" / "user.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("providers: [unclosed\n", encoding="utf-8")

    env = {**os.environ, "WTH_HOME": str(home), "WTH_SKIP_SETUP": "1"}
    proc = subprocess.run([sys.executable, "wth-ai-main.py", "doctor"], cwd=REPO,
                          capture_output=True, text=True, timeout=120, env=env)
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 2, combined
    assert "user.yaml" in combined
    assert "Traceback" not in combined
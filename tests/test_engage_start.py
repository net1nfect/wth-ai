"""P0-4: `engage start` tidak boleh membuka ketiga dimensi pengaman sekaligus.

Sebelum ini perintahnya `--authorized` default True (izin diberikan tanpa
operator mengetik apa pun), lalu memanggil `set_unrestricted(True)` dan
`/permission unrestricted`. Satu perintah menggerakkan dimensi izin target,
capability ceiling, dan approval manusia sekaligus.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]


def _run(args, home: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "WTH_HOME": str(home), "WTH_SKIP_SETUP": "1",
           "WTH_PLAIN": "1", "WTH_TEST_MODE": "1"}
    for key in ("WTH_UNRESTRICTED", "WTH_AUTO_APPROVE", "WTH_REQUIRE_AUTHORIZED"):
        env.pop(key, None)
    return subprocess.run([sys.executable, "wth-ai-main.py", *args], cwd=REPO,
                          capture_output=True, text=True, timeout=120, env=env)


def test_engage_start_without_authorized_flag_is_refused(tmp_path):
    proc = _run(["engage", "start", "--mode", "ctf", "--target", "10.10.10.1"],
                tmp_path)
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert not (tmp_path / "scope_card.json").exists()
    in_dir = tmp_path / "data" / "engagements"
    assert not in_dir.exists() or not any(in_dir.rglob("*.json"))


def _saved_config(home: Path) -> dict:
    # Dibaca langsung dari YAML: proses pytest punya WTH_HOME sendiri, jadi
    # memanggil WTHConfig.load() di sini akan mencampur config mesin ini.
    return yaml.safe_load((home / "config" / "user.yaml").read_text())


def test_engage_start_with_authorized_keeps_require_authorized(tmp_path):
    proc = _run(["engage", "start", "--mode", "ctf", "--target", "10.10.10.1",
                 "--authorized"], tmp_path)
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)

    cfg = _saved_config(tmp_path)
    assert cfg["safety"]["require_authorized"] is True
    assert cfg["default_permission"] == "full-lab"


def test_engage_start_does_not_turn_on_unrestricted(tmp_path):
    """Perintah ini tidak menaikkan capability sendiri.

    Profil `ctf-aggressive` memang mendeklarasikan `unrestricted: True` (PLAN
    mempertahankannya); yang diuji di sini adalah engage start tidak menambah
    `set_unrestricted(True)` / `/permission unrestricted` di atasnya.
    """
    proc = _run(["engage", "start", "--mode", "ctf", "--target", "10.10.10.1",
                 "--authorized"], tmp_path)
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert "unrestricted" not in proc.stdout.lower()

    cfg = _saved_config(tmp_path)
    assert cfg["default_permission"] == "full-lab"


def test_engage_start_is_audited(tmp_path):
    proc = _run(["engage", "start", "--mode", "bug-bounty",
                 "--target", "example.com", "--authorized"], tmp_path)
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)

    lines = (tmp_path / "logs" / "audit.log").read_text().splitlines()
    events = [json.loads(line) for line in lines if line.strip()]
    started = [e for e in events if e["event"] == "engage.started"]
    assert len(started) == 1
    assert started[0]["target"] == "example.com"
    assert started[0]["authorized"] is True


def test_engage_start_no_authorized_flag_is_audited(tmp_path):
    proc = _run(["engage", "start", "--mode", "ctf", "--target", "10.10.10.1",
                 "--no-authorized"], tmp_path)
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)

    events = [json.loads(line) for line in
              (tmp_path / "logs" / "audit.log").read_text().splitlines() if line.strip()]
    started = [e for e in events if e["event"] == "engage.started"]
    assert started and started[0]["authorized"] is False
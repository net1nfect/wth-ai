"""CLI: scope card ditolak tanpa izin, ditulis dengan izin."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _run(args, home):
    env = {**os.environ, "WTH_HOME": str(home), "WTH_SKIP_SETUP": "1",
           "WTH_PLAIN": "1"}
    return subprocess.run([sys.executable, "wth-ai-main.py", *args], cwd=REPO,
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

def test_scope_source_writes_source_card(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "app.py").write_text("print(1)\n")
    proc = _run(["scope", "--target", "proj", "--source", str(project),
                 "--authorized"], tmp_path)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    data = json.loads((tmp_path / "scope_card.json").read_text())
    assert data["kind"] == "source"
    assert data["source_path"] == str(project)


def test_scope_source_without_authorization_is_refused(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    proc = _run(["scope", "--target", "proj", "--source", str(project)], tmp_path)
    assert proc.returncode == 2
    assert "DITOLAK" in proc.stdout
    assert not (tmp_path / "scope_card.json").exists()


def _command_names(path: Path) -> set[str]:
    return set(re.findall(r'@cli\.command\("([^"]+)"\)', path.read_text(encoding="utf-8")))


def test_launcher_root_hanya_shim() -> None:
    """Root `wth-ai-main.py` tidak boleh berisi perintah: sumbernya satu.

    Sebelumnya kedua file sama-sama mendefinisikan CLI, dan salinannya
    menyimpang (root kehilangan `resolve_image_digest` dan `chains=`).
    """
    root = _command_names(REPO / "wth-ai-main.py")
    assert root == set(), f"root masih mendefinisikan perintah: {sorted(root)}"

    packaged = _command_names(REPO / "wth_ai" / "cli" / "wth-ai-main.py")
    assert packaged, "sumber CLI tidak mendefinisikan perintah apa pun"


def test_shim_menjalankan_sumber_yang_sama(tmp_path) -> None:
    """`python wth-ai-main.py --version` melewati shim dan mencetak versi produk."""
    from wth_ai import __version__

    proc = _run(["--version"], tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert __version__ in proc.stdout

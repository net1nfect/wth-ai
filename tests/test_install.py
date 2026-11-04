"""Installer + `wth setup`: preflight, pemasangan, dan setup non-interaktif.

Yang dijaga di sini: preflight tidak menulis apa pun, pemasangan memakai argv
( bukan shell ), setup non-interaktif menyimpan key hanya di vault, dan skrip
install bisa diuji tanpa jaringan lewat `--dry-run`.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from wth_ai import config as cfg_mod
from wth_ai import install as inst
from wth_ai.config import WTHConfig
from wth_ai.vault import SecretVault

REPO = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO / "scripts" / "install.sh"


# --------------------------------------------------------------------------- #
# preflight
# --------------------------------------------------------------------------- #
def test_build_report_is_read_only(tmp_path):
    home = tmp_path / "home"
    report = inst.build_report(home=home)

    assert report.python_ok is True
    assert report.home == home
    assert home.exists() is False
    assert isinstance(report.missing_core, list)


def test_report_lists_missing_core_dependency(tmp_path):
    report = inst.build_report(home=tmp_path)
    # Modul yang pasti tidak ada di lingkungan mana pun.
    assert all(d.module != "definitely_not_a_module" for d in report.missing_core)

    report.missing_core.append(inst.DepStatus("x", "x>=1", False))
    text = inst.format_report(report)
    assert "x>=1" in text
    assert "WAJIB" in text


# --------------------------------------------------------------------------- #
# deteksi dependensi sistem (tanpa sudo)
# --------------------------------------------------------------------------- #
def test_package_hint_is_printed_not_run(monkeypatch):
    monkeypatch.setattr(inst.platform, "system", lambda: "Linux")
    hint = inst.package_hint("python3-venv")

    assert hint.startswith("sudo ") or hint.startswith("#")
    # Bentuk yang dieksekusi bukan string: install_missing memakai argv.
    assert "apt-get install" in hint or "dnf install" in hint or hint.startswith("#")


def test_package_hint_on_macos(monkeypatch):
    monkeypatch.setattr(inst.platform, "system", lambda: "Darwin")
    assert inst.package_hint("git") == "brew install git"


def test_venv_usable_detects_broken_interpreter(tmp_path):
    fake = tmp_path / "python3"
    fake.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    fake.chmod(0o755)
    assert inst.venv_usable(str(fake)) is False


def test_disk_free_mb_returns_int(tmp_path):
    value = inst.disk_free_mb(tmp_path)
    assert value is None or isinstance(value, int)


def test_report_marks_venv_broken_and_hints_package(monkeypatch, tmp_path):
    report = inst.build_report(home=tmp_path)
    report.venv_broken = True
    report.issues.append("`python -m venv` tidak bisa membuat environment")

    text = inst.format_report(report)
    assert "tidak bisa dibuat" in text
    assert inst.venv_package() in text


def test_report_hides_system_hints_when_everything_present(monkeypatch, tmp_path):
    report = inst.build_report(home=tmp_path)
    report.has_git = True
    report.has_curl = True
    report.venv_broken = False
    report.missing_core = []

    text = inst.format_report(report)
    assert "Installer tidak memakai sudo" not in text
    assert "apt-get install" not in text and "brew install" not in text


def test_requirements_for_includes_features_and_missing(monkeypatch, tmp_path):
    report = inst.build_report(home=tmp_path)
    report.missing_core = [inst.DepStatus("click", "click>=8.1", False)]

    reqs = inst.requirements_for(["tui"], report)
    assert "click>=8.1" in reqs
    assert any(r.startswith("textual") for r in reqs)


def test_requirements_for_ignores_unknown_feature(tmp_path):
    report = inst.build_report(home=tmp_path)
    report.missing_core = []
    assert inst.requirements_for(["tidak-ada"], report) == []


def test_install_missing_dry_run_does_not_spawn(monkeypatch, tmp_path):
    report = inst.build_report(home=tmp_path)
    report.missing_core = [inst.DepStatus("click", "click>=8.1", False)]
    called = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.append(a))

    code, commands = inst.install_missing(report, dry_run=True)

    assert code == 0
    assert called == []
    assert commands and "click>=8.1" in commands[0]


def test_install_missing_uses_argv_not_shell(tmp_path):
    report = inst.build_report(home=tmp_path)
    report.missing_core = [inst.DepStatus("click", "click>=8.1", False)]
    seen: list[tuple] = []

    class _Proc:
        returncode = 0

    def fake_run(argv, **kwargs):
        seen.append((argv, kwargs))
        return _Proc()

    code, _ = inst.install_missing(report, runner=fake_run)
    argv, kwargs = seen[0]

    assert code == 0
    assert isinstance(argv, list)
    assert "shell" not in kwargs
    assert argv[:3] == [inst.sys.executable, "-m", "pip"]


def test_pip_argv_adds_user_only_outside_venv(monkeypatch):
    monkeypatch.setattr(inst, "in_virtualenv", lambda: True)
    assert "--user" not in inst.pip_argv(["x"])
    monkeypatch.setattr(inst, "in_virtualenv", lambda: False)
    assert "--user" in inst.pip_argv(["x"], user=True)


def test_apply_setup_creates_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    report, _note = inst.apply_setup(home=tmp_path / "home", smoke=False)

    assert (report.home / "config").is_dir()
    assert (report.home / "data" / "sessions").is_dir()
    assert (report.home / "db.sqlite").exists()


# --------------------------------------------------------------------------- #
# setup non-interaktif
# --------------------------------------------------------------------------- #
@pytest.fixture
def user_cfg(tmp_path, monkeypatch):
    path = tmp_path / "user.yaml"
    monkeypatch.setattr(cfg_mod, "USER_CONFIG_PATH", path)
    return path


def test_build_payload_maps_provider_flags():
    from wth_ai.cli.setup_wizard import build_setup_payload

    payload = build_setup_payload(
        provider="9router",
        base_url="http://127.0.0.1:20128/v1",
        api_key="sk-router-123456",
        model="glm-5",
    )
    assert payload["provider_type"] == "9router"
    assert payload["router9_base_url"] == "http://127.0.0.1:20128/v1"
    assert payload["router9_api_key"] == "sk-router-123456"
    assert payload["model"] == "glm-5"


def test_build_payload_custom_provider_uses_custom_keys():
    from wth_ai.cli.setup_wizard import build_setup_payload

    payload = build_setup_payload(provider="custom", base_url="http://lab:4000/v1", api_key="sk-lab-123456")
    assert payload["custom_base_url"] == "http://lab:4000/v1"
    assert payload["custom_api_key"] == "sk-lab-123456"


def test_build_payload_rejects_unknown_provider():
    from wth_ai.cli.setup_wizard import build_setup_payload

    with pytest.raises(ValueError):
        build_setup_payload(provider="tidak-ada")


def test_non_interactive_setup_writes_vault_ref_only(user_cfg):
    from wth_ai.cli.setup_wizard import build_setup_payload, run_setup_wizard

    payload = build_setup_payload(
        provider="custom",
        base_url="http://lab:4000/v1",
        api_key="sk-never-plaintext-999",
        model="lab-large",
    )
    config = run_setup_wizard(non_interactive=payload)

    assert config.providers["planner"].type == "custom"
    assert config.providers["planner"].model == "lab-large"
    assert config.setup_complete is True

    raw = user_cfg.read_text(encoding="utf-8")
    assert "sk-never-plaintext-999" not in raw
    data = yaml.safe_load(raw)
    assert data["secrets"]["custom_base_url"] == "http://lab:4000/v1"
    assert data["secrets"]["custom_api_key"] == "vault:custom_api_key"
    assert SecretVault().get("custom_api_key") == "sk-never-plaintext-999"


def test_non_interactive_setup_keeps_9router_base_url(user_cfg):
    from wth_ai.cli.setup_wizard import build_setup_payload, run_setup_wizard

    payload = build_setup_payload(
        provider="9router", base_url="http://127.0.0.1:20128/v1", api_key="sk-r9-1234567", model="glm-5",
    )
    run_setup_wizard(non_interactive=payload)

    data = yaml.safe_load(user_cfg.read_text(encoding="utf-8"))
    assert data["secrets"]["router9_base_url"] == "http://127.0.0.1:20128/v1"
    assert data["secrets"]["router9_api_key"] == "vault:router9_api_key"


# --------------------------------------------------------------------------- #
# scripts/install.sh
# --------------------------------------------------------------------------- #
def test_install_script_parses():
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash tidak tersedia")
    result = subprocess.run([bash, "-n", str(INSTALL_SH)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


# --------------------------------------------------------------------------- #
# bootstrap satu baris (install.sh di root)
# --------------------------------------------------------------------------- #
BOOTSTRAP = REPO / "install.sh"


def test_bootstrap_parses_as_posix_sh():
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("sh tidak tersedia")
    result = subprocess.run([sh, "-n", str(BOOTSTRAP)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_bootstrap_defaults_to_the_branch_it_ships_from():
    """Default branch harus branch tempat berkas ini dipublikasikan, bukan `main`."""
    text = BOOTSTRAP.read_text(encoding="utf-8")
    readme = (REPO / "README.md").read_text(encoding="utf-8")

    assert 'WTH_REF="${WTH_REF:-big-dix}"' in text
    assert "net1nfect/wth-ai/big-dix/install.sh | bash" in readme


def test_installer_source_default_matches_bootstrap():
    installer = INSTALL_SH.read_text(encoding="utf-8")
    assert 'SRC_REF="${SRC_REF:-big-dix}"' in installer


def test_readme_documents_one_liner():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "net1nfect/wth-ai" in readme
    assert "install.sh | bash" in readme
    assert "--dry-run" in readme


def test_bootstrap_rejects_html_instead_of_running_it(tmp_path):
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("sh tidak tersedia")

    page = tmp_path / "page.html"
    page.write_text("<!doctype html><title>404</title>", encoding="utf-8")
    server = _serve(tmp_path)

    try:
        result = subprocess.run(
            [sh, str(BOOTSTRAP)],
            capture_output=True, text=True,
            env={**os.environ, "WTH_SRC_URL": f"http://127.0.0.1:{server.port}/page.html",
                 "WTH_HOME": str(tmp_path / "home")},
        )
    finally:
        server.stop()

    assert result.returncode != 0
    assert "bukan skrip shell" in result.stderr


def test_bootstrap_runs_piped_installer_and_forwards_args(tmp_path):
    sh = shutil.which("sh")
    bash = shutil.which("bash")
    if sh is None or bash is None:
        pytest.skip("sh/bash tidak tersedia")

    server = _serve(REPO)
    try:
        pipeline = (
            f"cd {tmp_path} && "
            f"WTH_SRC_URL=http://127.0.0.1:{server.port}/scripts/install.sh "
            f"WTH_HOME={tmp_path / 'home'} "
            f"sh {BOOTSTRAP} --dry-run --no-setup"
        )
        result = subprocess.run([bash, "-c", pipeline], capture_output=True, text=True)
    finally:
        server.stop()

    assert result.returncode == 0, result.stderr
    # Argumen harus sampai ke installer, bukan ditelan oleh bootstrap.
    assert "--dry-run" in result.stdout or "dry-run" in result.stdout
    assert "Pasang paket" in result.stdout


def test_installer_survives_being_piped(tmp_path):
    """Tidak ada BASH_SOURCE saat dipipa: skrip tidak boleh mati di set -u."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash tidak tersedia")

    result = subprocess.run(
        [bash, "-c", f"cd {tmp_path} && WTH_HOME={tmp_path / 'h'} cat {INSTALL_SH} | bash -s -- --dry-run --no-setup"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "unbound variable" not in result.stderr


class _Server:
    def __init__(self, root: Path):
        import http.server
        import threading

        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self.port

    def __exit__(self, *exc):
        self.stop()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _serve(root: Path) -> "_Server":
    server = _Server(root)
    server.__enter__()
    return server


def test_install_dry_run_changes_nothing(tmp_path):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash tidak tersedia")

    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    result = subprocess.run(
        [bash, str(INSTALL_SH), "--dry-run", "--no-setup", "--with", "tui",
         "--home", str(home), "--bin", str(bin_dir), "--source", str(REPO)],
        capture_output=True, text=True, env={**os.environ, "WTH_HOME": str(home)},
    )

    assert result.returncode == 0, result.stderr
    # Warnanya kosong saat stdout bukan tty, tetapi `$C_NC` tetap di
    # antara pemisah perintah — jadi periksa potongan yang stabil.
    assert "pip install" in result.stdout
    assert f"-e {REPO}" in result.stdout
    assert "textual" in result.stdout
    # Dry-run boleh membuat direktori log (bukti perintah), tetapi tidak boleh
    # membuat venv, workspace, atau command.
    assert not (home / "venv").exists()
    assert not (home / "config").exists()
    assert not bin_dir.exists()


def test_install_rejects_unknown_feature():
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash tidak tersedia")
    result = subprocess.run(
        [bash, str(INSTALL_SH), "--dry-run", "--no-setup", "--with", "ngawur"],
        capture_output=True, text=True,
    )
    # Ditolak setelah fase paket: pesannya harus menyebut pilihan yang valid.
    assert result.returncode != 0
    assert "fitur tidak dikenal" in (result.stdout + result.stderr)


def test_install_fails_clearly_when_python_is_too_old(tmp_path):
    """Interpreter eksplisit < 3.10 harus gagal dengan kelas + saran paket."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash tidak tersedia")

    fake = tmp_path / "python3.8"
    fake.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    fake.chmod(0o755)

    result = subprocess.run(
        [bash, str(INSTALL_SH), "--no-setup", "--python", str(fake)],
        capture_output=True, text=True,
        env={**os.environ, "WTH_HOME": str(tmp_path / "home"), "HOME": str(tmp_path)},
    )

    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "kelas: python_missing" in combined
    assert ">= 3.10" in combined
    # Tanpa sudo otomatis: perintah paket hanya DICETAK.
    assert "apt-get install" in combined or "brew install" in combined
    assert not (tmp_path / "home" / "venv").exists()


def test_install_does_not_run_sudo_by_default(tmp_path):
    """Tidak ada pemanggilan sudo pada jalur normal --dry-run sekalipun."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash tidak tersedia")

    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    marker = tmp_path / "sudo-called"
    sudo = fake_bin / "sudo"
    sudo.write_text(f"#!/bin/sh\ntouch {marker}\nexit 0\n", encoding="utf-8")
    sudo.chmod(0o755)

    result = subprocess.run(
        [bash, str(INSTALL_SH), "--dry-run", "--no-setup", "--source", str(REPO)],
        capture_output=True, text=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
            "WTH_HOME": str(tmp_path / "home"),
            "HOME": str(tmp_path),
        },
    )

    assert result.returncode == 0, result.stderr
    assert not marker.exists(), "installer memanggil sudo tanpa diminta"


def test_shell_path_block_is_idempotent_and_removed(tmp_path):
    """PATH ditulis sekali (marker) dan dihapus lagi oleh --uninstall."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash tidak tersedia")

    home = tmp_path / "home"
    home.mkdir()
    rc = home / ".bashrc"
    rc.write_text("export EDITOR=vim\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"

    env = {
        **os.environ,
        "HOME": str(home),
        "SHELL": "/bin/bash",
        "WTH_HOME": str(home / "wth"),
        "WTH_PYTHON": sys.executable,
    }
    args = [bash, str(INSTALL_SH), "--no-setup", "--bin", str(bin_dir)]

    first = subprocess.run(args, capture_output=True, text=True, env=env)
    assert first.returncode == 0, first.stderr
    text = rc.read_text(encoding="utf-8")
    assert text.count("# WTH-AI command") == 1
    assert str(bin_dir) in text
    assert "EDITOR=vim" in text

    second = subprocess.run(args, capture_output=True, text=True, env=env)
    assert second.returncode == 0, second.stderr
    assert rc.read_text(encoding="utf-8").count("# WTH-AI command") == 1

    remove = subprocess.run(
        [bash, str(INSTALL_SH), "--uninstall", "--bin", str(bin_dir)],
        capture_output=True, text=True, env=env,
    )
    assert remove.returncode == 0, remove.stderr
    assert "# WTH-AI command" not in rc.read_text(encoding="utf-8")
    assert "EDITOR=vim" in rc.read_text(encoding="utf-8")


def test_uninstall_dry_run_is_safe(tmp_path):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash tidak tersedia")
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    (home / "config" / "user.yaml").write_text("setup_complete: true\n", encoding="utf-8")

    result = subprocess.run(
        [bash, str(INSTALL_SH), "--uninstall", "--dry-run", "--home", str(home), "--bin", str(tmp_path / "bin")],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (home / "config" / "user.yaml").exists()


def test_pyproject_declares_entry_points():
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert 'wth = "wth_ai.cli.entry:main"' in text
    assert "requires-python" in text
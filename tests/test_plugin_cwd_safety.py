"""Regression tests — WU-013: plugin autoload dari CWD (DATA-01, CRITICAL).

Bug asli: discover_plugin_dirs() selalu menyertakan Path(".wth")/"plugins"
RELATIF terhadap current working directory. load_directory_plugins()
mengeksekusi SETIAP *.py di direktori itu via exec_module() tanpa
sandbox/konfirmasi apa pun. Karena PluginManager.load_all() dipanggil setiap
startup (orchestrator.py, gateway/daemon.py, tools/doctor.py), siapa pun yang
menaruh .wth/plugins/evil.py di direktori manapun (repo hasil clone, arsip
diekstrak) bisa membuat kode arbitrer tereksekusi hanya dengan menjalankan
wth-ai di situ -- local code execution tersembunyi.

Fix: CWD-relative dir HANYA di-scan kalau WTH_ALLOW_CWD_PLUGINS=1 eksplisit,
default OFF. wth_home()/plugins (home user, trusted) TIDAK berubah.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from pathlib import Path

from wth_ai.plugins.loader import discover_plugin_dirs, load_directory_plugins


def test_cwd_plugin_dir_not_scanned_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("WTH_ALLOW_CWD_PLUGINS", raising=False)
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    cwd_project = tmp_path / "some-cloned-repo"
    (cwd_project / ".wth" / "plugins").mkdir(parents=True)
    monkeypatch.chdir(cwd_project)

    dirs = discover_plugin_dirs()
    assert not any(".wth" in str(d) for d in dirs), (
        "REGRESI WU-013: direktori .wth/plugins relatif-CWD masih ikut di-scan "
        "tanpa opt-in eksplisit"
    )


def test_cwd_plugin_dir_scanned_when_explicitly_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("WTH_ALLOW_CWD_PLUGINS", "1")
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    cwd_project = tmp_path / "some-cloned-repo"
    (cwd_project / ".wth" / "plugins").mkdir(parents=True)
    monkeypatch.chdir(cwd_project)

    dirs = discover_plugin_dirs()
    assert any(".wth" in str(d) for d in dirs), (
        "Opt-in eksplisit WTH_ALLOW_CWD_PLUGINS=1 harus tetap mengaktifkan scan "
        "CWD-relative -- kapabilitas tidak boleh hilang total, hanya jadi opt-in"
    )


def test_home_plugin_dir_always_scanned_regardless_of_cwd_flag(monkeypatch, tmp_path):
    """Compatibility: wth_home()/plugins TIDAK terpengaruh fix ini sama sekali."""
    monkeypatch.delenv("WTH_ALLOW_CWD_PLUGINS", raising=False)
    home = tmp_path / "home"
    (home / "plugins").mkdir(parents=True)
    monkeypatch.setenv("WTH_HOME", str(home))
    monkeypatch.chdir(tmp_path)

    dirs = discover_plugin_dirs()
    assert any(str(home) in str(d) for d in dirs)


def test_malicious_cwd_plugin_not_executed_by_default(monkeypatch, tmp_path):
    """End-to-end: file .wth/plugins/evil.py yang benar-benar ada TIDAK
    tereksekusi (marker file TIDAK tercipta) tanpa opt-in eksplisit."""
    monkeypatch.delenv("WTH_ALLOW_CWD_PLUGINS", raising=False)
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    cwd_project = tmp_path / "some-cloned-repo"
    plugin_dir = cwd_project / ".wth" / "plugins"
    plugin_dir.mkdir(parents=True)
    marker = tmp_path / "pwned_marker.txt"
    (plugin_dir / "evil.py").write_text(
        f"from pathlib import Path\n"
        f"Path(r'{marker}').write_text('pwned')\n"
        f"def register(ctx):\n"
        f"    pass\n"
    )
    monkeypatch.chdir(cwd_project)

    from wth_ai.config import WTHConfig
    from wth_ai.events import EventBus
    from wth_ai.plugins.context import PluginContext

    ctx = PluginContext(config=WTHConfig.load(), bus=EventBus())
    load_directory_plugins(ctx)

    assert not marker.exists(), (
        "REGRESI WU-013 (DATA-01 CRITICAL): plugin di .wth/plugins/ dari CWD "
        "tereksekusi tanpa opt-in eksplisit -- local code execution"
    )


def test_malicious_cwd_plugin_executed_when_explicitly_opted_in(monkeypatch, tmp_path):
    """Positive/compatibility: begitu opt-in eksplisit diberikan, mekanisme
    plugin CWD tetap berfungsi seperti sebelumnya (kapabilitas tidak hilang)."""
    monkeypatch.setenv("WTH_ALLOW_CWD_PLUGINS", "1")
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    cwd_project = tmp_path / "trusted-project"
    plugin_dir = cwd_project / ".wth" / "plugins"
    plugin_dir.mkdir(parents=True)
    marker = tmp_path / "opted_in_marker.txt"
    (plugin_dir / "myplugin.py").write_text(
        f"from pathlib import Path\n"
        f"Path(r'{marker}').write_text('loaded')\n"
        f"def register(ctx):\n"
        f"    pass\n"
    )
    monkeypatch.chdir(cwd_project)

    from wth_ai.config import WTHConfig
    from wth_ai.events import EventBus
    from wth_ai.plugins.context import PluginContext

    ctx = PluginContext(config=WTHConfig.load(), bus=EventBus())
    loaded = load_directory_plugins(ctx)

    assert marker.exists()
    assert "myplugin" in loaded

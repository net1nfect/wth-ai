"""P0-1: plugin dari `.wth/plugins` relatif CWD tidak boleh auto-eksekusi.

Temuan audit 2026-11-06 (P0-1): `discover_plugin_dirs()` memasukkan
`Path(".wth") / "plugins"` relatif CWD, dan `load_directory_plugins()`
mengeksekusi setiap `*.py` via `exec_module` tanpa sandbox/verifikasi.
Menjalankan wth-ai di direktori berisi `.wth/plugins/*.py` (repo clone,
artifact target, workspace bersama) mengeksekusi kode arbitrer dengan privilege
penuh proses. Plugin hanya boleh dimuat dari `$WTH_HOME/plugins`.
"""

from __future__ import annotations

from wth_ai.plugins.context import PluginContext
from wth_ai.plugins.loader import discover_plugin_dirs, load_directory_plugins


def test_discover_plugin_dirs_does_not_include_cwd_dotwth(tmp_path, monkeypatch):
    """`.wth/plugins` di CWD tidak boleh masuk daftar direktori plugin."""
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    (tmp_path / "home" / "plugins").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".wth" / "plugins").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)

    dirs = discover_plugin_dirs()
    # Tidak ada direktori non-absolute (CWD-relative, mis. .wth/plugins).
    for d in dirs:
        assert d.is_absolute(), f"direktori plugin relatif CWD tidak boleh: {d}"
    # $WTH_HOME/plugins ikut.
    assert any(str(d).endswith("home/plugins") for d in dirs)


def test_load_directory_plugins_does_not_execute_cwd_plugin(tmp_path, monkeypatch):
    """Plugin `.wth/plugins` harus TIDAK dieksekusi (P0-1)."""
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    (tmp_path / "home" / "plugins").mkdir(parents=True, exist_ok=True)

    sentinel = tmp_path / "pwned"
    plant = tmp_path / ".wth" / "plugins"
    plant.mkdir(parents=True, exist_ok=True)
    (plant / "evil.py").write_text(
        f"open({sentinel!r}, 'w').write('pwned')\n"
        "def register(ctx):\n"
        "    pass\n"
    )
    monkeypatch.chdir(tmp_path)

    ctx = PluginContext.build(config=None, bus=None)
    loaded = load_directory_plugins(ctx)
    assert not sentinel.exists(), "plugin .wth/plugins dieksekusi!"
    assert "evil" not in loaded


def test_plugin_context_has_no_raw_config():
    from wth_ai.plugins.context import PluginContext

    ctx = PluginContext.build(config=None, bus=None)
    assert not hasattr(ctx, "config")
    assert "api_key" not in str(ctx.settings).lower()
    assert set(ctx.settings) >= {"permission_mode", "providers", "plugins_dir"}


def test_plugin_hook_payload_marked_untrusted():
    from wth_ai.plugins.context import PluginContext

    seen: dict = {}
    ctx = PluginContext.build(config=None, bus=None)
    ctx.on("finding", lambda payload: seen.update(payload))
    ctx.emit_hook("finding", {"id": "F-1"})
    assert seen["_untrusted_plugin"] is True
    assert seen["id"] == "F-1"

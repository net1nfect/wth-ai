"""P1-4: state penting tidak boleh pernah setengah jadi di disk.

`SessionStore.save` dan `WTHConfig.save_user_config` menulis langsung ke path
tujuan. Kalau proses mati di tengah tulis (atau Ctrl-C), file yang tersisa
adalah JSON/YAML terpotong — dan `load_latest()` / config load berikutnya gagal,
atau lebih buruk: config lama hilang total.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from wth_ai import config as config_mod
from wth_ai.config import WTHConfig, load_user_config
from wth_ai.models import SessionState
from wth_ai.session_store import SessionStore


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    monkeypatch.setattr(config_mod, "USER_CONFIG_PATH", tmp_path / "config" / "user.yaml")
    monkeypatch.setattr(config_mod, "USER_ENV_PATH", tmp_path / ".env")
    return tmp_path


@pytest.mark.parametrize("target", ["session", "latest", "user.yaml"])
def test_interrupted_write_leaves_the_previous_file_intact(home, target, monkeypatch):
    """Simulasi proses mati saat menulis: file lama harus tetap utuh & valid."""
    store = SessionStore(WTHConfig())
    if target in {"session", "latest"}:
        store.save(SessionState(session_id="s1", scope=SessionState().scope))
        path = store.dir / ("latest.json" if target == "latest" else "s1.json")
        before = json.loads(path.read_text())
    else:
        config_mod.USER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        config_mod.USER_CONFIG_PATH.write_text("safety:\n  require_authorized: true\n")
        path = config_mod.USER_CONFIG_PATH
        before = load_user_config(path)

    real_replace = os.replace

    def _die(src, dst, *a, **k):
        raise KeyboardInterrupt("dibunuh sebelum rename")

    monkeypatch.setattr(os, "replace", _die)
    with pytest.raises(KeyboardInterrupt):
        if target in {"session", "latest"}:
            store.save(SessionState(session_id="s2", scope=SessionState().scope))
        else:
            WTHConfig().save_user_config()
    monkeypatch.setattr(os, "replace", real_replace)

    # File tujuan tidak pernah ditulis sebagian; isi lama masih terbaca.
    if target in {"session", "latest"}:
        assert json.loads(path.read_text()) == before
    else:
        assert load_user_config(path) == before
    # Tidak ada file sementara yang tertinggal di direktori itu.
    assert not [p for p in path.parent.iterdir() if p.name.endswith(".tmp")], \
        sorted(p.name for p in path.parent.iterdir())


def test_config_save_is_readable_by_a_fresh_loader(home):
    """Jalur normal tetap menulis lewat rename: hasilnya valid & pm-nya 600."""
    cfg = WTHConfig()
    out = cfg.save_user_config()
    assert out == config_mod.USER_CONFIG_PATH
    assert load_user_config(out)["safety"]["require_authorized"] is True
    assert not list(Path(out).parent.glob("*.tmp"))


def test_no_plaintext_env_writer_remains():
    """P2-8: `write_env_file()` tidak punya pemanggil tapi menulis secret
    plaintext ke `~/.wth-ai/.env`. Dihapus, bukan diperbaiki."""
    assert not hasattr(WTHConfig, "write_env_file")

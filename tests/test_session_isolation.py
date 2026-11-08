"""P1-3: sesi menempel ke `WTH_HOME`, bukan ke direktori kerja.

`SessionConfig.state_dir` default-nya path relatif ("data/sessions"), jadi
`SessionStore` menulis ke `<cwd>/data/sessions` dan `load_latest()` bisa
me-resume sesi milik home lain. Dua `WTH_HOME` harus berdiri sendiri-sendiri.
"""

from __future__ import annotations

import json
from pathlib import Path

from wth_ai.config import WTHConfig
from wth_ai.models import SessionState
from wth_ai.session_store import SessionStore


def _config() -> WTHConfig:
    return WTHConfig()


def test_sessions_live_under_wth_home(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home-a"))
    store = SessionStore(_config())
    assert store.dir == tmp_path / "home-a" / "data" / "sessions"


def test_two_homes_do_not_share_latest(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home-a"))
    a = SessionStore(_config())
    state_a = SessionState(session_id="sess-a", scope=SessionState().scope)
    a.save(state_a)

    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home-b"))
    b = SessionStore(_config())
    assert b.load_latest() is None, "home baru tidak boleh me-resume sesi home lain"

    state_b = SessionState(session_id="sess-b", scope=SessionState().scope)
    b.save(state_b)
    assert b.load_latest().session_id == "sess-b"

    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home-a"))
    assert SessionStore(_config()).load_latest().session_id == "sess-a"


def test_cwd_does_not_receive_session_files(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    SessionStore(_config()).save(SessionState(session_id="s", scope=SessionState().scope))
    assert not (workdir / "data").exists()


def test_absolute_state_dir_is_still_honoured(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    cfg = _config()
    cfg.session.state_dir = str(tmp_path / "elsewhere")
    store = SessionStore(cfg)
    assert store.dir == tmp_path / "elsewhere"
    meta = json.loads((store.dir / "latest.json").read_text()) if (store.dir / "latest.json").exists() else None
    assert meta is None
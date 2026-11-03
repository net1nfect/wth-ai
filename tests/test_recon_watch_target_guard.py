"""Guard target recon — token sampah tidak boleh pernah jadi target scan.

Bug terukur (keluhan operator "ketika gua ngetik apapun tiba tiba scan apapun
gajelas"): `/recon watch --interval` (option tanpa value) menyimpan watch +
entri cron dengan target literal ``--interval``; LocalCronTicker yang hidup di
tiap sesi CLI lalu MENJALANKAN job recon atas target sampah itu tiap interval
di background. Dua kelas token yang terbukti muncul di job store pengguna:
``--interval`` dan ``target``.

Kontrak yang dijaga tes ini:
- parser argumen tidak pernah mengangkat option-token/kata-command jadi target;
- handler `/recon`, `/recon watch`, helper CLI, dan CronStore.add menolak
  target tak valid SEBELUM file/job/entri apa pun dibuat;
- entri cron lama berisi target sampah dinonaktifkan, bukan dijalankan.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from wth_ai.cli.handlers.recon_handler import (
    _parse_recon_args,
    _recon_run,
    _recon_watch,
)
from wth_ai.cron.scheduler import CronScheduler
from wth_ai.cron.store import CronStore
from wth_ai.recon.planner import is_valid_recon_target
from wth_ai.recon.realtime import load_watch


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    return tmp_path


def _orch(scope_target: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(scope=SimpleNamespace(target=scope_target), session_id="t"),
    )


def _watch_files(tmp_path):
    d = tmp_path / "recon" / "watch"
    return sorted(p.name for p in d.glob("*.json")) if d.exists() else []


# --------------------------------------------------------------------------- #
# Validator
# --------------------------------------------------------------------------- #
def test_valid_targets_pass():
    for t in ("example.com", "sub.example.co.id", "https://example.com/x",
              "8.8.8.8", "192.168.1.5", "127.0.0.1", "127.0.0.1:8081",
              "localhost", "localhost:3000", "[::1]:8080"):
        ok, why = is_valid_recon_target(t)
        assert ok, f"{t} harus valid: {why}"


def test_junk_tokens_rejected():
    for t in ("--interval", "--interval=6h", "-p", "target", "run", "watch",
              "6h", "90m", "3600", "", "target=example.com"):
        ok, why = is_valid_recon_target(t)
        assert not ok, f"{t!r} harus ditolak"
        assert why


# --------------------------------------------------------------------------- #
# Parser argumen
# --------------------------------------------------------------------------- #
def test_parse_recon_args_option_token_never_becomes_target():
    target, _, _, interval = _parse_recon_args(["--interval"])
    assert target == ""          # dulu: "--interval"
    assert interval == ""

    target, _, _, interval = _parse_recon_args(["--interval", "6h"])
    assert target == ""          # 6h dikonsumsi sebagai value, bukan target
    assert interval == "6h"


def test_parse_recon_args_command_word_never_becomes_target():
    target, _, _, _ = _parse_recon_args(["target", "--interval", "6h"])
    assert target == ""          # dulu: "target" -> jadi job recon


def test_parse_recon_args_still_finds_real_target():
    assert _parse_recon_args(["example.com", "--interval", "6h"])[0] == "example.com"
    assert _parse_recon_args(["--interval", "6h", "example.com"])[0] == "example.com"
    assert _parse_recon_args(["https://example.com/path", "--fast"])[0] == "example.com"
    assert _parse_recon_args(["localhost:3000"])[0] == "localhost:3000"


# --------------------------------------------------------------------------- #
# Slash handler
# --------------------------------------------------------------------------- #
def test_recon_watch_junk_creates_nothing(tmp_home):
    resp = _recon_watch(_orch("--interval"), ["--interval"])
    assert resp.startswith("Ditolak")
    assert _watch_files(tmp_home) == []
    assert CronStore().list() == []


def test_recon_watch_command_word_creates_nothing(tmp_home):
    resp = _recon_watch(_orch(""), ["target", "--interval", "6h"])
    assert resp.startswith("Usage")           # tidak ada target valid -> usage
    assert _watch_files(tmp_home) == []
    assert CronStore().list() == []


def test_recon_watch_valid_target_creates_watch_and_cron(tmp_home):
    resp = _recon_watch(_orch(""), ["example.com", "--interval", "6h"])
    assert "Recon watch enabled" in resp
    assert load_watch("example.com") is not None
    entries = CronStore().list()
    assert len(entries) == 1 and entries[0].target == "example.com"


def test_recon_run_junk_scope_target_refused(tmp_home):
    resp = _recon_run(_orch("--interval"), [])
    assert resp.startswith("Ditolak")         # dulu: job recon target "--interval"


# --------------------------------------------------------------------------- #
# Helper CLI + cron store
# --------------------------------------------------------------------------- #
def test_cli_watch_text_rejects_junk(tmp_home):
    from wth_ai.cli.recon_commands import recon_watch_text

    resp = recon_watch_text("--interval", "recon-only", "6h")
    assert resp.startswith("Ditolak")
    assert _watch_files(tmp_home) == []
    assert CronStore().list() == []


def test_run_recon_job_rejects_junk_before_touching_manager(tmp_home):
    from wth_ai.cli.recon_commands import run_recon_job

    with pytest.raises(ValueError):
        run_recon_job(MagicMock(), "--interval", "recon-only")


def test_cron_store_add_rejects_junk(tmp_home):
    store = CronStore()
    with pytest.raises(ValueError):
        store.add("recon", "--interval", "every 6h")
    with pytest.raises(ValueError):
        store.add("recon", "target", "every 6h")
    assert store.list() == []
    entry = store.add("recon", "example.com", "every 6h")
    assert entry.target == "example.com"


# --------------------------------------------------------------------------- #
# Scheduler — entri lama berisi target sampah
# --------------------------------------------------------------------------- #
def _write_legacy_entry(tmp_path, target: str) -> None:
    path = tmp_path / "runtime" / "cron.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"entries": [{
        "cron_id": "deadbeef", "job_type": "recon", "target": target,
        "schedule": "every 6h", "profile": "recon-only", "enabled": True,
        "last_run_at": None, "next_run_at": None, "meta": {},
    }]}), encoding="utf-8")


def test_scheduler_disables_legacy_junk_entry_instead_of_scanning(tmp_home):
    _write_legacy_entry(tmp_home, "--interval")
    store = CronStore()
    mgr = MagicMock()
    sched = CronScheduler(mgr, store=store)

    assert sched.tick() == []                     # tidak ada job
    mgr.create_job.assert_not_called()            # worker tidak dipanggil
    entry = store.list()[0]
    assert entry.enabled is False
    assert "tidak valid" in entry.meta.get("disabled_reason", "")

    # tick kedua juga tidak mencoba lagi
    assert sched.tick() == []
    mgr.create_job.assert_not_called()


def test_scheduler_still_fires_valid_entry(tmp_home):
    store = CronStore()
    store.add("recon", "example.com", "every 6h")
    job = MagicMock()
    job.job_id = "job-1"
    mgr = MagicMock()
    mgr.create_job.return_value = job
    sched = CronScheduler(mgr, store=store)

    fired = sched.tick()
    assert fired == [{"cron_id": store.list()[0].cron_id, "job_id": "job-1"}]
    mgr.create_job.assert_called_once()
    assert store.list()[0].last_run_at is not None

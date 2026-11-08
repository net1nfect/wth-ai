"""P0-1: jalur recon tunduk pada scope card + allowlist egress.

Sebelum ini `wth recon run <host apa pun>` langsung memindai: metadata job
hardcoded `authorized=True`, engine punya default `authorized=True`, dan
`set_egress_guard()` tidak pernah dipanggil dari produksi. Tiga test di sini
mengunci invarian yang sama dengan jalur agen: tanpa kartu tidak ada aksi,
di luar scope diblokir, dan guard benar-benar terpasang selama run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from wth_ai.jobs.manager import JobManager
from wth_ai.jobs.worker import WorkerEngine
from wth_ai.recon import http as rhttp
from wth_ai.scope_card import ScopeCard
from wth_ai.workspace import init_workspace, wth_home

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    from wth_ai.events import reset_event_bus

    reset_event_bus()
    init_workspace(tmp_path)
    return tmp_path


def _manager() -> JobManager:
    init_workspace()
    return JobManager()


def _write_card(*, target: str, cidr: list[str], domains: list[str]) -> ScopeCard:
    card = ScopeCard(target=target, cidr=cidr, domains=domains, authorized=True)
    card.write(wth_home() / "scope_card.json")
    return card


def test_recon_without_scope_card_makes_no_request(tmp_home, monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr("requests.request", lambda *a, **k: calls.append(a))

    mgr = _manager()
    job = mgr.create_job("recon", target="example.com", metadata={"authorized": True})
    res = WorkerEngine(mgr).run_once()

    assert res["status"] == "failed"
    assert "scope" in (res.get("error") or "").lower()
    assert calls == []


def test_recon_target_outside_card_is_blocked(tmp_home, monkeypatch):
    _write_card(target="172.28.0.10", cidr=["172.28.0.0/16"], domains=[])
    calls: list[tuple] = []
    monkeypatch.setattr("requests.request", lambda *a, **k: calls.append(a))

    mgr = _manager()
    mgr.create_job("recon", target="example.com", metadata={"authorized": True})
    res = WorkerEngine(mgr).run_once()

    assert res["status"] == "failed"
    assert "scope" in (res.get("error") or "").lower()
    assert calls == []


def test_recon_egress_guard_installed_during_run_and_cleared(tmp_home, monkeypatch):
    _write_card(target="example.com", cidr=[], domains=["example.com"])
    seen: dict[str, object] = {}

    def fake_fetch(url, **kwargs):
        seen["guard"] = rhttp._EGRESS_GUARD
        return rhttp.HttpResponse(url=rhttp.normalize_url(url), status=200,
                                  headers={"Content-Type": "text/html"}, text="<html></html>")

    monkeypatch.setattr(rhttp, "fetch", fake_fetch)

    from wth_ai.recon.engine import ReconEngine

    card = ScopeCard.load(wth_home() / "scope_card.json")
    res = ReconEngine(scope_card=card).run("example.com", profile="recon-only",
                                           job_id="JOB-guard")
    assert res["status"] == "completed"
    assert seen.get("guard") is not None, "guard tidak dipasang selama run"
    assert rhttp._EGRESS_GUARD is None, "guard bocor setelah run"


def test_cli_recon_without_card_exits_2(tmp_path):
    env = {**os.environ, "WTH_HOME": str(tmp_path), "WTH_SKIP_SETUP": "1",
           "WTH_PLAIN": "1", "WTH_TEST_MODE": "1"}
    proc = subprocess.run(
        [sys.executable, "wth-ai-main.py", "recon", "run", "example.com"],
        cwd=REPO, capture_output=True, text=True, timeout=120, env=env,
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert not (tmp_path / "scope_card.json").exists()
    assert not list(tmp_path.glob("wth-ai-results/*/recon_plan.json"))

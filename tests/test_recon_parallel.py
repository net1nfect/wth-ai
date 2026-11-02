"""Tests for concurrent recon tool execution.

The measured problem: `ReconEngine.run()` ran every external tool strictly one at
a time, so wall clock was the SUM of every subprocess. These tests lock the
properties that make concurrency safe to ship:

* results are delivered in PLAN order, not completion order (artifacts stay
  diffable between runs);
* callbacks reach the caller on ONE thread (progress/log go to the job manager,
  which must not be touched from a worker);
* a stop request prevents any FURTHER tool from starting, and keeps what was
  already collected;
* one explosive tool cannot sink the batch;
* pacing is aimed at the TARGET, not applied blindly -- pacing a passive tool that
  asks crt.sh protects nobody, and doing so turned a 5s test run into 350s.
"""

from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest

from wth_ai.recon.model import ToolRun
from wth_ai.recon.parallel import (
    DEFAULT_RPS,
    PROFILE_WORKERS,
    TARGET_FACING_CATEGORIES,
    HostRateLimiter,
    contacts_target,
    resolve_rps,
    resolve_workers,
    run_specs_parallel,
)


class _Spec:
    def __init__(self, name: str, category: str = "subdomain", caps: list[str] | None = None):
        self.name = name
        self.category = category
        self.capabilities = caps or [f"{category}_recon"]
        self.execution_mode = "external"


class _Runner:
    """Fake runner: records start order/thread and can sleep or explode."""

    def __init__(self, delays: dict[str, float] | None = None, explode: set[str] | None = None):
        self.delays = delays or {}
        self.explode = explode or set()
        self.started: list[str] = []
        self.threads: set[int] = set()
        self.lock = threading.Lock()

    def run(self, spec, *, target: str, ctx) -> ToolRun:
        with self.lock:
            self.started.append(spec.name)
            self.threads.add(threading.get_ident())
        delay = self.delays.get(spec.name, 0.0)
        if delay:
            time.sleep(delay)
        run = ToolRun(tool=spec.name, started_at="", finished_at="")
        if spec.name in self.explode:
            raise RuntimeError(f"{spec.name} meledak")
        run.status = "success"
        run.normalized_items = [{"name": spec.name}]
        return run


# --------------------------------------------------------------------------- #
# workers + rps resolution
# --------------------------------------------------------------------------- #
def test_profile_worker_defaults(monkeypatch):
    monkeypatch.delenv("WTH_RECON_WORKERS", raising=False)
    assert resolve_workers("budget") == 1, "budget harus tetap sekuensial"
    assert resolve_workers("recon-only") == PROFILE_WORKERS["recon-only"]
    assert resolve_workers("high-quality") == 6
    assert resolve_workers("profile-yang-tidak-dikenal") >= 1


def test_worker_overrides(monkeypatch):
    monkeypatch.setenv("WTH_RECON_WORKERS", "2")
    assert resolve_workers("high-quality") == 2
    assert resolve_workers("high-quality", override=5) == 5
    monkeypatch.setenv("WTH_RECON_WORKERS", "ngawur")
    assert resolve_workers("budget") == 1


def test_rps_precedence(monkeypatch):
    monkeypatch.setenv("WTH_RECON_RPS", "7.5")
    assert resolve_rps(None) == 7.5
    assert resolve_rps(3.0) == 3.0, "rps dari scope harus menang"
    monkeypatch.setenv("WTH_RECON_RPS", "bukan-angka")
    assert resolve_rps(None) == DEFAULT_RPS


# --------------------------------------------------------------------------- #
# rate limiter
# --------------------------------------------------------------------------- #
def test_limiter_spaces_starts_of_the_same_host():
    limiter = HostRateLimiter(rps=10.0)          # 100ms apart
    limiter.wait("example.com")
    start = time.monotonic()
    limiter.wait("example.com")
    elapsed = time.monotonic() - start
    assert elapsed >= limiter.interval * 0.8, f"start tidak dipace ({elapsed:.3f}s)"
    assert limiter.stats()["starts"] == {"example.com": 2}


def test_limiter_host_parsing():
    limiter = HostRateLimiter(rps=100)
    for raw in ("https://example.com/path?q=1", "http://example.com:8443", "example.com/", "EXAMPLE.com"):
        assert limiter.host_of(raw) == "example.com", raw
    assert limiter.host_of("10.0.0.5") == "10.0.0.5"


# --------------------------------------------------------------------------- #
# pacing policy: aim at the target, not at everything
# --------------------------------------------------------------------------- #
def test_only_target_facing_categories_are_paced():
    assert contacts_target(_Spec("httpx", "http_probe")) is True
    assert contacts_target(_Spec("naabu", "port")) is True
    assert contacts_target(_Spec("katana", "crawl")) is True
    assert contacts_target(_Spec("tlsx", "tls")) is True
    # passive / local: subfinder asks crt.sh, waybackurls asks the Internet
    # Archive, gitleaks reads a file. Pacing these protects nobody.
    for passive in ("subdomain", "dns", "archive", "intel", "js", "container", "mobile", "code_intel"):
        assert contacts_target(_Spec("x", passive)) is False, passive
    assert "http_probe" in TARGET_FACING_CATEGORIES


def test_pacing_falls_back_to_capabilities_when_category_is_missing():
    spec = _Spec("aneh", "")
    spec.capabilities = ["http_probe_recon"]
    assert contacts_target(spec) is True
    spec.capabilities = ["archive_recon"]
    assert contacts_target(spec) is False


def test_limiter_counts_paced_and_unpaced():
    limiter = HostRateLimiter(rps=1000.0)
    runner = _Runner()
    specs = [_Spec("httpx", "http_probe"), _Spec("subfinder", "subdomain"),
             _Spec("naabu", "port"), _Spec("gau", "archive")]
    run_specs_parallel(runner, specs, target="example.com", ctx=None, max_workers=2, limiter=limiter)
    stats = limiter.stats()
    assert stats["paced_starts"] == 2, stats
    assert stats["unpaced_starts"] == 2, stats


# --------------------------------------------------------------------------- #
# ordering, parallelism, isolation
# --------------------------------------------------------------------------- #
def test_results_arrive_in_plan_order_even_when_the_first_tool_is_slowest():
    runner = _Runner(delays={"a": 0.25, "b": 0.01, "c": 0.01})
    specs = [_Spec("a"), _Spec("b"), _Spec("c")]
    seen: list[str] = []
    run_specs_parallel(runner, specs, target="example.com", ctx=None, max_workers=3,
                       on_result=lambda spec, run: seen.append(spec.name))
    assert seen == ["a", "b", "c"], f"urutan hasil tidak sesuai plan: {seen}"


def test_concurrent_run_beats_sequential_wall_clock():
    specs = [_Spec(name) for name in ("a", "b", "c", "d")]
    delays = {name: 0.3 for name in ("a", "b", "c", "d")}

    par = _Runner(delays=delays)
    t0 = time.monotonic()
    run_specs_parallel(par, specs, target="example.com", ctx=None, max_workers=4)
    parallel_elapsed = time.monotonic() - t0

    seq = _Runner(delays=delays)
    t0 = time.monotonic()
    run_specs_parallel(seq, specs, target="example.com", ctx=None, max_workers=1)
    sequential_elapsed = time.monotonic() - t0

    assert parallel_elapsed < sequential_elapsed * 0.75, (
        f"paralel {parallel_elapsed:.2f}s tidak lebih cepat dari sekuensial {sequential_elapsed:.2f}s"
    )


def test_workers_actually_use_different_threads():
    runner = _Runner(delays={"a": 0.15, "b": 0.15})
    run_specs_parallel(runner, [_Spec("a"), _Spec("b")], target="example.com", ctx=None, max_workers=2)
    assert len(runner.threads) >= 2, "tool berjalan di satu thread saja"


def test_callbacks_come_from_the_calling_thread():
    """progress/log reach the job manager from one thread only."""
    caller = threading.get_ident()
    seen: list[int] = []
    runner = _Runner(delays={"a": 0.05, "b": 0.05})
    run_specs_parallel(
        runner, [_Spec("a"), _Spec("b")], target="example.com", ctx=None, max_workers=2,
        on_result=lambda spec, run: seen.append(threading.get_ident()),
    )
    assert set(seen) == {caller}, f"callback dari thread lain: {seen}"


def test_one_exploding_tool_does_not_sink_the_batch():
    runner = _Runner(explode={"b"})
    results: dict[str, str] = {}
    halted = run_specs_parallel(
        runner, [_Spec("a"), _Spec("b"), _Spec("c")], target="example.com", ctx=None, max_workers=3,
        on_result=lambda spec, run: results.__setitem__(spec.name, run.status),
    )
    assert halted is False
    assert results["a"] == "success" and results["c"] == "success"
    assert results["b"] == "failed"


# --------------------------------------------------------------------------- #
# stop semantics
# --------------------------------------------------------------------------- #
def test_halt_prevents_further_starts_and_keeps_partial_results():
    stop = threading.Event()
    runner = _Runner(delays={"a": 0.05})

    def _halt() -> bool:
        return stop.is_set()

    def _on_result(spec, run) -> None:
        if spec.name == "a":
            stop.set()      # operatur menekan stop setelah tool pertama

    skipped: list[str] = []
    halted = run_specs_parallel(
        runner, [_Spec("a"), _Spec("b"), _Spec("c")], target="example.com", ctx=None,
        max_workers=1, halt=_halt, on_result=_on_result, on_skip=lambda spec: skipped.append(spec.name),
    )
    assert halted is True
    assert runner.started == ["a"], f"tool tetap dijalankan setelah stop: {runner.started}"
    assert skipped == ["b", "c"]


def test_halt_before_any_tool_skips_everything():
    runner = _Runner()
    skipped: list[str] = []
    halted = run_specs_parallel(
        runner, [_Spec("a"), _Spec("b")], target="example.com", ctx=None,
        halt=lambda: True, on_skip=lambda spec: skipped.append(spec.name),
    )
    assert halted is True
    assert runner.started == []
    assert skipped == ["a", "b"]


def test_empty_spec_list_is_not_a_halt():
    assert run_specs_parallel(_Runner(), [], target="example.com", ctx=None) is False


# --------------------------------------------------------------------------- #
# budget interplay: pacing must not become a de-facto sequential run
# --------------------------------------------------------------------------- #
def test_paced_and_unpaced_tools_overlap():
    """A passive tool must not wait behind a target-facing one: the whole point of
    separating the policies."""
    limiter = HostRateLimiter(rps=1.0)          # 1s apart for paced tools
    runner = _Runner(delays={"httpx": 0.05, "subfinder": 0.05})
    t0 = time.monotonic()
    run_specs_parallel(
        runner, [_Spec("httpx", "http_probe"), _Spec("subfinder", "subdomain")],
        target="example.com", ctx=None, max_workers=2, limiter=limiter,
    )
    elapsed = time.monotonic() - t0
    assert elapsed < 0.9, f"tool pasif ikut tertahan pacing ({elapsed:.2f}s)"

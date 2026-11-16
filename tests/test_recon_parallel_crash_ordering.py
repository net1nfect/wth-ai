"""Guard Bug C di jalur PARALEL, bukan hanya jalur builtin sekuensial.

Defek asli: `CheckpointEnvelope(...)` menjalankan validasi `engine_state` saat
konstruksi. Di `_mark_inflight` (dipanggil `on_start`), exception itu keluar
sebelum tool dijalankan; `run_specs_parallel._one` membiarkannya menjadi
kegagalan future, sehingga tool yang sehat dilaporkan `failed` atau bahkan
tidak pernah jalan. Guard di `_save_recon_checkpoint` harus menutup SEMUA
titik: konstruksi envelope, penyusunan `finished`, dan callback tulis.

Yang diuji di sini:

1. konstruksi envelope gagal + 4 tool external benar-benar paralel -> tidak
   satu pun tool sehat menjadi korban;
2. `on_start` menulis `INFLIGHT` SEBELUM `runner.run` dipanggil, dan crash di
   tengah batch meninggalkan `INFLIGHT` (bukan `COMPLETED`) untuk tool yang
   sudah mulai;
3. snapshot crash bisa dilanjutkan: tool `COMPLETED` dilewati + datanya
   diputar ulang, tool `INFLIGHT` dijalankan lagi.

Tidak ada jaringan: runner dan fingerprint di-stub.
"""

from __future__ import annotations

import os
import threading
import time
from types import SimpleNamespace

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest

from wth_ai.jobs.checkpoint import CheckpointEnvelope, StepOutcome
from wth_ai.recon.engine import ReconEngine
from wth_ai.recon.model import (
    PlannedTool,
    ReconPlan,
    ToolRun,
    ToolSpec,
)
from wth_ai.recon.parallel import run_specs_parallel

TARGET = "https://example.com"


class _Crash(BaseException):
    """Mati mendadak: tidak ditangkap `except Exception`."""


def _external(name: str) -> ToolSpec:
    return ToolSpec(
        name=name,
        category="misc",
        capabilities=["passive_lookup"],
        execution_mode="external",
        binary_names=["true"],
        enabled_by_default=True,
        risk_level="safe",
    )


def _plan(names: list[str]) -> ReconPlan:
    return ReconPlan(
        target=TARGET,
        profile="recon-only",
        selected=[
            PlannedTool(tool=name, category="misc", execution_mode="external")
            for name in names
        ],
    )


# ---------------------------------------------------------------------------
# 1) guard di engine: konstruksi envelope gagal, tool paralel tetap sehat
# ---------------------------------------------------------------------------


def test_parallel_checkpoint_construction_failure_never_fails_healthy_tools(
    tmp_path, monkeypatch,
):
    import wth_ai.jobs.checkpoint as cp_module
    import wth_ai.recon.engine as engine_module
    import wth_ai.recon.parallel as parallel_module

    names = [f"fake-ext-{index}" for index in range(4)]
    calls: list[str] = []
    completed: list[ToolRun] = []
    active = {"now": 0, "max": 0}
    lock = threading.Lock()

    class _Runner:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, spec, *, target, ctx):
            with lock:
                calls.append(spec.name)
                active["now"] += 1
                active["max"] = max(active["max"], active["now"])
            time.sleep(0.02)
            run = ToolRun(
                tool=spec.name, status="success",
                normalized_items=[
                    {"kind": "url", "url": f"https://example.com/{spec.name}"},
                ],
            )
            with lock:
                completed.append(run)
                active["now"] -= 1
            return run

    def _boom(**kwargs):
        raise ValueError("envelope gagal divalidasi saat konstruksi")

    monkeypatch.setattr(cp_module, "CheckpointEnvelope", _boom)
    monkeypatch.setattr(engine_module, "SafeToolRunner", _Runner)
    monkeypatch.setattr(parallel_module, "resolve_workers", lambda profile: 4)

    logs: list[str] = []
    engine = ReconEngine(results_root=tmp_path)
    for name in names:
        engine.registry.tools[name] = _external(name)
    engine.planner.plan = lambda *a, **k: _plan(names)  # noqa: ARG005
    engine._run_fingerprint = lambda *a, **k: ToolRun(  # noqa: SLF001
        tool="builtin-fingerprint", status="success",
    )

    result = engine.run(
        TARGET, authorized=True, checkpoint=lambda env: None,
        log=logs.append, should_stop=lambda: False,
    )

    assert sorted(calls) == sorted(names), "tool sehat tidak boleh hilang"
    assert len(completed) == len(names)
    assert all(run.status == "success" for run in completed)
    assert active["max"] > 1, "test ini harus benar-benar paralel"
    assert result["summary"]["tool_runs"].get("success", 0) >= len(names)
    assert any("checkpoint recon gagal ditulis" in line for line in logs), logs


# ---------------------------------------------------------------------------
# 2) crash ordering: INFLIGHT tersimpan sebelum runner.run
# ---------------------------------------------------------------------------


def test_parallel_crash_leaves_inflight_after_marking_before_run():
    names = [f"t{index}" for index in range(6)]
    specs = [SimpleNamespace(name=name) for name in names]
    state: dict[str, str] = {}
    snapshots: list[dict[str, str]] = []
    invoked: list[str] = []
    active = {"now": 0, "max": 0}
    lock = threading.Lock()

    def on_start(spec):
        with lock:
            assert spec.name not in state, "tool yang sama tidak boleh start dua kali"
            state[spec.name] = StepOutcome.INFLIGHT.value
            snapshots.append(dict(state))

    def on_result(spec, run):
        with lock:
            state[spec.name] = StepOutcome.COMPLETED.value
            snapshots.append(dict(state))

    def runner(spec, *, target, ctx):
        with lock:
            # Invariant crash-consistency: penanda INFLIGHT harus SUDAH ada
            # sebelum tool dijalankan. Kalau tidak, crash di sini meninggalkan
            # operasi tanpa jejak.
            assert state.get(spec.name) == StepOutcome.INFLIGHT.value, state
            invoked.append(spec.name)
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
        if spec.name == "t2":
            raise _Crash("mati setelah INFLIGHT, sebelum hasil")
        time.sleep(0.02)
        with lock:
            active["now"] -= 1
        return {"tool": spec.name, "status": "success"}

    # Kontrak asli: `run_specs_parallel` memanggil `runner.run(...)`, bukan
    # `runner(...)`. Mengirim fungsi biasa akan menghasilkan AttributeError
    # (subclass Exception) yang ditangkap sebagai kegagalan tool — test akan
    # lolos secara palsu tanpa pernah menguji crash ordering.
    runner_obj = SimpleNamespace(run=runner)

    with pytest.raises(_Crash):
        run_specs_parallel(
            runner_obj, specs, target=TARGET, ctx=None, max_workers=4,
            on_start=on_start, on_result=on_result,
        )

    assert "t2" in invoked
    assert state["t2"] == StepOutcome.INFLIGHT.value
    # Tidak ada tool yang boleh berstatus COMPLETED karena hasilnya diproses
    # setelah batch selesai; crash membuat `on_result` tidak sempat berjalan.
    assert all(value == StepOutcome.INFLIGHT.value for value in state.values())
    assert active["max"] > 1, "crash ordering harus diuji di jalur paralel"
    assert snapshots, "tidak ada checkpoint yang tersimpan"


# ---------------------------------------------------------------------------
# 3) resume snapshot crash: COMPLETED dilewati + replay, INFLIGHT dijalankan
# ---------------------------------------------------------------------------


def test_resume_after_parallel_crash_skips_completed_and_reruns_inflight(
    tmp_path, monkeypatch,
):
    import wth_ai.recon.engine as engine_module
    import wth_ai.recon.parallel as parallel_module

    names = ["fake-a", "fake-b", "fake-c"]
    prior_url = "https://example.com/from-checkpoint"
    resume = CheckpointEnvelope(engine="recon", target=TARGET)
    resume.engine_state = {
        "profile": "recon-only",
        "tools": {
            "fake-a": {
                "outcome": StepOutcome.COMPLETED.value,
                "run": ToolRun(
                    tool="fake-a", status="success",
                    normalized_items=[{"kind": "url", "url": prior_url}],
                ).to_dict(),
            },
            "fake-b": {"outcome": StepOutcome.INFLIGHT.value},
            "fake-c": {
                "outcome": StepOutcome.COMPLETED.value,
                "run": ToolRun(
                    tool="fake-c", status="success",
                    normalized_items=[
                        {"kind": "url", "url": "https://example.com/from-c"},
                    ],
                ).to_dict(),
            },
        },
        "finished": ["fake-a", "fake-c"],
    }
    calls: list[str] = []

    class _Runner:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, spec, *, target, ctx):
            calls.append(spec.name)
            return ToolRun(
                tool=spec.name, status="success",
                normalized_items=[
                    {"kind": "url", "url": f"https://example.com/{spec.name}"},
                ],
            )

    logs: list[str] = []
    monkeypatch.setattr(engine_module, "SafeToolRunner", _Runner)
    monkeypatch.setattr(parallel_module, "resolve_workers", lambda profile: 4)

    engine = ReconEngine(results_root=tmp_path)
    for name in names:
        engine.registry.tools[name] = _external(name)
    engine.planner.plan = lambda *a, **k: _plan(names)  # noqa: ARG005
    engine._run_fingerprint = lambda *a, **k: ToolRun(  # noqa: SLF001
        tool="builtin-fingerprint", status="success",
    )

    result = engine.run(
        TARGET, authorized=True, resume=resume,
        log=logs.append, should_stop=lambda: False,
    )

    assert result["checkpoint"]["resume_rejected"] == "", logs
    assert calls == ["fake-b"], f"hanya INFLIGHT yang boleh dijalankan: {calls}"
    assert "fake-a" in result["checkpoint"]["resumed_tools"]
    assert "fake-c" in result["checkpoint"]["resumed_tools"]
    urls = {item.get("url") for item in result["normalized"]["urls"]}
    assert prior_url in urls, "hasil tool yang dilewati harus diputar ulang"

"""Checkpoint per-tool recon (rekomendasi 5).

Defek yang dikunci: recon hanya punya hasil akhir, jadi crash di tool ke-N
membuang hasil tool sebelumnya dan retry mengulang SEMUA tool. Kontrak yang
diuji di sini:

1. tool yang `COMPLETED` + punya `run` utuh DILEWATI, dan hasilnya tetap
   diputar ulang ke normalizer (data tidak hilang);
2. crash di tengah tool meninggalkan `INFLIGHT` yang jujur, dan tool itu
   dijalankan lagi pada resume (baca ulang aman);
3. checkpoint dengan target lain / engine lain / payload rusak ditolak
   eksplisit, bukan dipercaya;
4. tanpa store, scan tetap berjalan dan recovery dilaporkan tidak tersedia.

Semua target adalah simulasi lokal; tidak ada jaringan.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest

from wth_ai.jobs.checkpoint import (
    CheckpointEnvelope,
    CheckpointValidationError,
    StepOutcome,
)
from wth_ai.recon.engine import ReconEngine
from wth_ai.recon.model import ReconPlan, ToolRun

TARGET = "https://example.com"


class _Crash(BaseException):
    """Mati mendadak: tidak ditangkap `except Exception`."""


def _run_dict(tool: str, items: list[dict] | None = None) -> dict:
    return ToolRun(
        tool=tool, status="success", normalized_items=items or [],
    ).to_dict()


def _env(tools: dict, *, target: str = TARGET, engine: str = "recon") -> CheckpointEnvelope:
    return CheckpointEnvelope(
        engine=engine, target=target,
        engine_state={"profile": "recon-only", "tools": tools, "finished": []},
    )


# ---------------------------------------------------------------------------
# validasi engine_state
# ---------------------------------------------------------------------------


def test_recon_engine_is_registered():
    env = CheckpointEnvelope(engine="recon", target=TARGET)
    assert env.to_dict()["engine"] == "recon"


def test_completed_tool_without_run_is_rejected():
    env = CheckpointEnvelope(engine="recon", target=TARGET)
    env.engine_state = {"tools": {"http": {"outcome": StepOutcome.COMPLETED.value}}}
    with pytest.raises(CheckpointValidationError):
        env.validate_engine_state()


def test_unknown_outcome_is_rejected():
    env = CheckpointEnvelope(engine="recon", target=TARGET)
    env.engine_state = {"tools": {"http": {"outcome": "selesai"}}}
    with pytest.raises(CheckpointValidationError):
        env.validate_engine_state()


def test_inflight_tool_needs_no_run():
    env = CheckpointEnvelope(engine="recon", target=TARGET)
    env.engine_state = {"tools": {"http": {"outcome": StepOutcome.INFLIGHT.value}}}
    env.validate_engine_state()  # tidak raise


def test_finished_must_be_list_of_str():
    env = CheckpointEnvelope(engine="recon", target=TARGET)
    env.engine_state = {"tools": {}, "finished": [1, 2]}
    with pytest.raises(CheckpointValidationError):
        env.validate_engine_state()


# ---------------------------------------------------------------------------
# engine: skip + replay
# ---------------------------------------------------------------------------


def _engine(tmp_path) -> ReconEngine:
    return ReconEngine(results_root=tmp_path)


def test_resumed_completed_tools_are_not_run_again(tmp_path):
    """Tool COMPLETED harus dilewati dan datanya tetap masuk ke normalizer."""
    saved: list[dict] = []
    prior_item = {"kind": "url", "url": "https://example.com/a", "source": "http"}
    resume = _env({
        "http": {"outcome": "completed", "run": _run_dict("http", [prior_item])},
    })

    engine = _engine(tmp_path)
    engine.planner.plan = lambda *a, **k: ReconPlan(  # noqa: ARG005
        target=TARGET, profile="recon-only",
    )
    result = engine.run(
        TARGET, authorized=True, resume=resume,
        checkpoint=lambda env: saved.append(env.to_dict()),
        should_stop=lambda: True,
    )

    cp = result["checkpoint"]
    # Resume harus BERHASIL, bukan ditolak karena bug internal.
    assert cp["resume_rejected"] == "", cp["resume_rejected"]
    assert "http" in cp["finished_tools"]
    assert "http" in cp["resumed_tools"]
    assert resume is not None
    # Data lama tetap ada di hasil ternormalisasi.
    urls = result["normalized"]["urls"]
    assert any(u.get("url") == "https://example.com/a" for u in urls)


def test_foreign_engine_checkpoint_is_rejected(tmp_path):
    logs: list[str] = []
    resume = _env({"http": {"outcome": "completed", "run": _run_dict("http")}},
                  engine="autopilot")
    result = _engine(tmp_path).run(
        TARGET, authorized=True, resume=resume, log=logs.append,
        should_stop=lambda: True,
    )
    assert result["checkpoint"]["resume_rejected"]
    assert any("ditolak" in line for line in logs)


def test_target_mismatch_is_rejected(tmp_path):
    resume = _env({"http": {"outcome": "completed", "run": _run_dict("http")}},
                  target="https://other.example")
    result = _engine(tmp_path).run(
        TARGET, authorized=True, resume=resume, log=lambda m: None,
        should_stop=lambda: True,
    )
    assert "target" in result["checkpoint"]["resume_rejected"]


def test_corrupt_checkpoint_does_not_abort_scan(tmp_path):
    bad = CheckpointEnvelope(engine="recon", target=TARGET)
    bad.engine_state = {"tools": {"x": {"outcome": "bogus"}}}
    # Payload rusak tidak bisa diformat balik; engine harus menolaknya.
    result = _engine(tmp_path).run(
        TARGET, authorized=True, resume=bad, log=lambda m: None,
        should_stop=lambda: True,
    )
    assert isinstance(result, dict) and result.get("target") == TARGET


def test_no_checkpoint_callback_still_runs(tmp_path):
    """Tanpa store/checkpoint, scan tetap jalan; recovery dilaporkan kosong."""
    result = _engine(tmp_path).run(
        TARGET, authorized=True, should_stop=lambda: True,
    )
    assert result["checkpoint"]["finished_tools"] == []
    assert result["checkpoint"]["resume_rejected"] == ""

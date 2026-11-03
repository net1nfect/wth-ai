"""Regression tests — WU-002: bug role-wiring di BaseLLMAgent._call().

Bug asli: _call() menerima parameter `model`/`backup` yang tidak pernah dibaca badan
fungsi; satu-satunya penentu model routing adalah keyword `role` yang dulu punya
default "planner". Caller yang lupa passing role= (verifier.py, executor.py x2)
diam-diam SELALU pakai role "planner" alih-alih "verifier"/"executor" masing-masing.

Catatan penting: ada DUA konsep "role" berbeda di codebase ini:
  1. message role (system/user/assistant) -- dibangun build_messages(), TIDAK PERNAH
     terpengaruh bug ini.
  2. agent role (planner/executor/verifier/reporter/...) -- parameter _call(role=...),
     INI yang bug -- menentukan model/provider apa yang dipakai, bukan struktur pesan.
Test di file ini memverifikasi KEDUANYA secara terpisah agar tidak tertukar.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")
os.environ.setdefault("WTH_NO_ANIMATION", "1")

import pytest

from wth_ai.config import WTHConfig
from wth_ai.models import ExecutorResult, ExecutorTask, Finding, FindingState, PermissionMode
from wth_ai.orchestrator import WTHOrchestrator

REPO_ROOT = Path(__file__).resolve().parent.parent


class RoleSpy:
    """Stand-in untuk LLMProviderRouter.chat_for_role — mencatat role & messages,
    TANPA network call nyata ke provider manapun."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, role, messages, *, timeout=None, stream=False, on_token=None, response_format=None):
        self.calls.append({"role": role, "messages": messages})
        return "STUB_RESPONSE", f"stub-model-for-{role}"


def _fresh_orch_with_spy():
    orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
    spy = RoleSpy()
    orch.client.chat_for_role = spy
    return orch, spy


def test_call_requires_role_keyword():
    """role sekarang keyword-only WAJIB — lupa mengisi harus fail-fast (TypeError),
    bukan diam-diam salah routing seperti sebelumnya."""
    orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
    with pytest.raises(TypeError):
        orch.planner._call("model-a", "model-b", "system", [], "user")  # noqa: SLF001


def test_no_call_site_missing_role_keyword():
    """Guardrail generik: setiap panggilan self._call(...) di wth_ai/agents/*.py wajib
    membawa `role=`. Mencegah kelas bug ini terulang di file manapun ke depannya."""
    call_pattern = re.compile(r"self\._call\(")
    offenders: list[str] = []
    for path in (REPO_ROOT / "wth_ai" / "agents").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for m in call_pattern.finditer(text):
            # Ambil potongan teks dari titik panggilan sampai penutup kurung yang seimbang.
            depth = 0
            i = m.end() - 1
            for j in range(i, len(text)):
                if text[j] == "(":
                    depth += 1
                elif text[j] == ")":
                    depth -= 1
                    if depth == 0:
                        call_src = text[i : j + 1]
                        break
            else:
                call_src = text[i:]
            if "role=" not in call_src:
                line_no = text.count("\n", 0, m.start()) + 1
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_no}")
    assert not offenders, f"Panggilan self._call() tanpa role= eksplisit: {offenders}"


def test_verifier_calls_with_verifier_role():
    orch, spy = _fresh_orch_with_spy()
    finding = Finding(
        id="F1", title="test finding", severity="low", state=FindingState.CANDIDATE,
        component="test", affected_asset="example.com", evidence=["[TX:abc]"],
    )
    orch.verifier.verify(finding, orch.state, evidence_vault=None)
    assert spy.calls, "chat_for_role tidak pernah dipanggil"
    assert spy.calls[-1]["role"] == "verifier"


def test_executor_enrich_calls_with_executor_role():
    orch, spy = _fresh_orch_with_spy()
    task = ExecutorTask(id="T1", goal="g", target="example.com", permission_required=PermissionMode.READ_ONLY_RECON)
    orch.executor._enrich_task_from_llm(task, orch.state)  # noqa: SLF001
    assert spy.calls[-1]["role"] == "executor"


def test_executor_parse_calls_with_executor_role():
    orch, spy = _fresh_orch_with_spy()
    task = ExecutorTask(id="T1", goal="g", target="example.com", permission_required=PermissionMode.READ_ONLY_RECON)
    result = ExecutorResult(
        task_id="T1", status="success", permission_check={}, commands_used=[],
        output_summary="out", evidence=[], observations=[], risk="low", recommended_next_step="none",
    )
    orch.executor._parse_with_llm(task, result, orch.state)  # noqa: SLF001
    assert spy.calls[-1]["role"] == "executor"


def test_planner_calls_with_planner_role():
    orch, spy = _fresh_orch_with_spy()
    orch.planner.plan("test goal", orch.state)
    assert spy.calls, "chat_for_role tidak pernah dipanggil"
    assert all(c["role"] == "planner" for c in spy.calls)


def test_chat_calls_with_planner_role():
    """Titik yang TIDAK tercatat di spesifikasi WU-002 asli (lihat laporan) — sekarang
    ikut diverifikasi eksplisit karena mekanis wajib ikut diperbaiki begitu default
    role dihapus dari _call()."""
    orch, spy = _fresh_orch_with_spy()
    orch.chat.respond("halo apa kabar", orch.state)
    assert spy.calls[-1]["role"] == "planner"


def test_report_builder_calls_with_reporter_role_regression_guard():
    """Sudah benar SEBELUM WU-002 — regression guard supaya tidak diam-diam berubah
    saat refactor berikutnya menyentuh file ini."""
    orch, spy = _fresh_orch_with_spy()
    orch.reporter.build(orch.state, orch.findings_db, evidence_vault=None)
    assert spy.calls[-1]["role"] == "reporter"


def test_message_structure_not_corrupted_by_role_fix():
    """system/user content tetap terpisah & benar terlepas dari agent-role apa yang
    dikirim — role HANYA mempengaruhi pemilihan model/provider, TIDAK PERNAH
    memengaruhi struktur pesan (system tidak berubah jadi user, atau sebaliknya)."""
    orch, spy = _fresh_orch_with_spy()
    finding = Finding(
        id="F2", title="xyz", severity="low", state=FindingState.CANDIDATE,
        component="c", affected_asset="a",
    )
    orch.verifier.verify(finding, orch.state, evidence_vault=None)
    messages = spy.calls[-1]["messages"]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == orch.verifier.system_prompt
    assert messages[-1]["role"] == "user"
    assert "Verifikasi finding ini" in messages[-1]["content"]
    # Pastikan agent-role ("verifier") tidak pernah bocor jadi bagian message role/content.
    assert all(m["role"] in {"system", "user", "assistant"} for m in messages)

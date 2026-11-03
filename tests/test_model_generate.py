"""Regression tests — WU-109: `ModelRequest`/`ModelResponse` kanonik +
`LLMProviderRouter.generate()` (PRD-013).

Scope decision (lebih sempit dari draft PRD-013 FR#1 -- lihat
docs/planning/WORK-UNITS.md WU-109): `generate()` diimplementasikan sebagai
FACADE di atas `chat_for_role()`/`chat_with_tools_for_role()` yang sudah ada
dan teruji (reuse WTH_TEST_MODE stub, failover, resolusi provider APA
ADANYA), BUKAN sebaliknya (chat_for_role dkk menjadi adapter di atas
generate() yang ditulis ulang dari nol). Membalik arah dependency itu akan
menyentuh jalur kode yang dieksekusi hampir SETIAP test di suite ini (test-
mode stub tertanam di 2 method) -- risiko regresi terlalu tinggi untuk satu
WU. `model_catalog.py` (context_length/cost real, WU-110) dan penghapusan
`openrouter.py::OpenRouterClient` dead code (FR#5) TIDAK dikerjakan di WU
ini.

Pilot migrasi: `ReportBuilderAgent.build()` (satu-satunya agent yang sudah
`role=` eksplisit & bersih sejak WU-002, risiko migrasi terendah).
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest

from wth_ai.config import WTHConfig
from wth_ai.llm.model_io import ModelRequest, ModelResponse
from wth_ai.llm.router import LLMProviderRouter


def _router() -> LLMProviderRouter:
    return LLMProviderRouter(WTHConfig.load())


# ---------------------------------------------------------------------------
# generate() -- plain chat path
# ---------------------------------------------------------------------------

def test_generate_plain_chat_success():
    router = _router()
    router.chat_for_role = lambda role, messages, **kw: ("hello world", "stub-model-x")
    response = router.generate(ModelRequest(role="reporter", messages=[{"role": "user", "content": "hi"}]))
    assert response.status == "success"
    assert response.content == "hello world"
    assert response.model == "stub-model-x"
    assert response.role == "reporter"


def test_generate_used_backup_flag_set_when_model_differs_from_primary():
    router = _router()
    primary, _backup = router.model_for_role("reporter")
    router.chat_for_role = lambda role, messages, **kw: ("resp", "some-other-backup-model")
    response = router.generate(ModelRequest(role="reporter", messages=[]))
    assert response.used_backup is True


def test_generate_used_backup_false_when_primary_used():
    router = _router()
    primary, _backup = router.model_for_role("reporter")
    router.chat_for_role = lambda role, messages, **kw: ("resp", primary)
    response = router.generate(ModelRequest(role="reporter", messages=[]))
    assert response.used_backup is False


# ---------------------------------------------------------------------------
# generate() -- tool-calling path
# ---------------------------------------------------------------------------

def test_generate_with_tools_returns_tool_calls():
    router = _router()
    router.chat_with_tools_for_role = lambda role, messages, *, tools, timeout=None: {
        "message": {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "function": {"name": "x"}}]},
        "model": "stub-tool-model",
    }
    response = router.generate(ModelRequest(
        role="executor", messages=[{"role": "user", "content": "do it"}], tools=[{"type": "function"}],
    ))
    assert response.status == "success"
    assert response.tool_calls == [{"id": "1", "function": {"name": "x"}}]
    assert response.model == "stub-tool-model"


# ---------------------------------------------------------------------------
# generate() -- never raises, errors normalized
# ---------------------------------------------------------------------------

def test_generate_never_raises_normalizes_error():
    router = _router()

    def _boom(role, messages, **kw):
        raise TimeoutError("request timed out")

    router.chat_for_role = _boom
    response = router.generate(ModelRequest(role="reporter", messages=[]))
    assert response.status == "error"
    assert response.error is not None
    assert response.error["type"] == "timeout"


def test_generate_error_response_has_no_content():
    router = _router()
    router.chat_for_role = lambda role, messages, **kw: (_ for _ in ()).throw(RuntimeError("api key invalid"))
    response = router.generate(ModelRequest(role="reporter", messages=[]))
    assert response.status == "error"
    assert response.content == ""


# ---------------------------------------------------------------------------
# End-to-end -- ReportBuilderAgent pilot migration
# ---------------------------------------------------------------------------

def test_report_builder_uses_generate_and_returns_content():
    from wth_ai.agents.report_builder import ReportBuilderAgent
    from wth_ai.findings_db import FindingsDB
    from wth_ai.models import SessionState

    config = WTHConfig.load()
    client = LLMProviderRouter(config)
    client.available = lambda: True
    client.generate = lambda request: ModelResponse(
        status="success", provider="stub", model="stub-model", role="reporter", content="# Report\nstub content",
    )
    agent = ReportBuilderAgent(config, client)
    out = agent.build(SessionState(), FindingsDB(), evidence_vault=None)
    assert out == "# Report\nstub content"


def test_report_builder_raises_llmerror_when_generate_returns_error_status():
    from wth_ai.agents.report_builder import ReportBuilderAgent
    from wth_ai.findings_db import FindingsDB
    from wth_ai.llm.errors import LLMError
    from wth_ai.llm.model_io import ModelResponse
    from wth_ai.models import SessionState

    config = WTHConfig.load()
    client = LLMProviderRouter(config)
    client.available = lambda: True
    client.generate = lambda request: ModelResponse(
        status="error", provider="stub", model="stub-model", role="reporter",
        error={"type": "auth", "message": "invalid api key"},
    )
    agent = ReportBuilderAgent(config, client)
    with pytest.raises(LLMError, match="invalid api key"):
        agent.build(SessionState(), FindingsDB(), evidence_vault=None)


def test_report_builder_generate_call_carries_reporter_role():
    """Compatibility langsung dengan WU-002 role-wiring discipline: generate()
    dipanggil dengan ModelRequest(role='reporter'), bukan default lain."""
    from wth_ai.agents.report_builder import ReportBuilderAgent
    from wth_ai.findings_db import FindingsDB
    from wth_ai.llm.model_io import ModelResponse
    from wth_ai.models import SessionState

    config = WTHConfig.load()
    client = LLMProviderRouter(config)
    client.available = lambda: True
    seen: list[ModelRequest] = []

    def _fake_generate(request):
        seen.append(request)
        return ModelResponse(status="success", provider="stub", model="m", role="reporter", content="ok")

    client.generate = _fake_generate
    agent = ReportBuilderAgent(config, client)
    agent.build(SessionState(), FindingsDB(), evidence_vault=None)
    assert seen[-1].role == "reporter"

"""Tests for LLM token/cost accounting.

Before this module, `ModelResponse.usage` was ALWAYS `{}` (one hardcoded literal),
so nothing in the system could say what an engagement spent. Two properties are
non-negotiable here and both are asserted below:

1. a provider that reports nothing must still be recorded as a CALL with zero
   tokens — "not measured" is a fact, and omitting it understates spend;
2. the ledger never stores prompt or completion TEXT. It is a bill, not a
   transcript.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest

from wth_ai.llm.usage import (
    UsageLedger,
    get_ledger,
    normalize_usage,
    reset_ledger,
)


# --------------------------------------------------------------------------- #
# normalisation across providers
# --------------------------------------------------------------------------- #
def test_normalize_openai_shape():
    usage = normalize_usage({"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
    assert usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def test_normalize_anthropic_shape():
    usage = normalize_usage({"usage": {"input_tokens": 7, "output_tokens": 3}})
    assert usage["prompt_tokens"] == 7
    assert usage["completion_tokens"] == 3
    assert usage["total_tokens"] == 10


def test_normalize_ollama_shape():
    usage = normalize_usage({"prompt_eval_count": 4, "eval_count": 6})
    assert usage["total_tokens"] == 10


def test_normalize_openrouter_cost_sits_outside_the_usage_block():
    usage = normalize_usage(
        {"usage": {"prompt_tokens": 1, "completion_tokens": 1}, "cost": 0.00042}
    )
    assert usage["cost_usd"] == pytest.approx(0.00042)


def test_normalize_returns_empty_for_unusable_payloads():
    """An unknown shape yields zeros, never an exception: accounting must not
    break a call that already succeeded."""
    for payload in (None, {}, {"usage": {}}, {"nonsense": "x"}, "not a dict"):
        assert normalize_usage(payload) == {}


# --------------------------------------------------------------------------- #
# ledger
# --------------------------------------------------------------------------- #
def test_ledger_persists_and_reloads(tmp_path):
    ledger = UsageLedger(tmp_path / "usage.jsonl")
    ledger.record(role="planner", provider="custom", model="auto",
                  usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120})

    fresh = UsageLedger(tmp_path / "usage.jsonl")
    rows = fresh.load()
    assert len(rows) == 1
    assert rows[0].model == "auto" and rows[0].total_tokens == 120


def test_ledger_counts_a_call_even_without_usage(tmp_path):
    """Silently dropping unmeasured calls is how a bill becomes a surprise."""
    ledger = UsageLedger(tmp_path / "usage.jsonl")
    ledger.record(role="planner", provider="custom", model="auto", usage=None)
    data = ledger.summary()
    assert data["calls"] == 1
    assert data["total_tokens"] == 0
    assert "belum ada" not in ledger.format_summary()


def test_ledger_aggregates_by_model_and_role(tmp_path):
    ledger = UsageLedger(tmp_path / "usage.jsonl")
    for role, model, tokens in (
        ("planner", "auto", 100), ("planner", "auto", 50), ("executor", "fast", 25),
    ):
        ledger.record(role=role, provider="custom", model=model, usage={"total_tokens": tokens})
    data = ledger.summary()
    assert data["calls"] == 3
    assert data["total_tokens"] == 175
    assert data["by_model"]["auto"]["tokens"] == 150
    assert data["by_role"]["planner"]["calls"] == 2


def test_ledger_never_stores_prompt_or_completion_text(tmp_path):
    """A ledger is a bill, not a transcript."""
    path = tmp_path / "usage.jsonl"
    ledger = UsageLedger(path)
    ledger.record(role="planner", provider="custom", model="auto",
                  usage={"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10})
    blob = path.read_text(encoding="utf-8")
    entry = json.loads(blob.splitlines()[0])
    assert set(entry) == {
        "role", "provider", "model", "prompt_tokens", "completion_tokens",
        "total_tokens", "cost_usd", "calls", "created_at",
    }
    assert "prompt" not in entry and "content" not in entry


def test_budget_overflow_is_reported(tmp_path):
    ledger = UsageLedger(tmp_path / "usage.jsonl", budget_tokens=50)
    ledger.record(role="planner", provider="custom", model="auto", usage={"total_tokens": 80})
    data = ledger.summary()
    assert data["over_budget"] is True
    assert "LEWAT" in ledger.format_summary()


def test_empty_ledger_says_nothing_was_measured(tmp_path):
    text = UsageLedger(tmp_path / "usage.jsonl").format_summary()
    assert "Belum ada pemakaian tercatat" in text


def test_corrupt_lines_do_not_kill_the_report(tmp_path):
    path = tmp_path / "usage.jsonl"
    path.write_text('{"role":"planner","provider":"p","model":"m","total_tokens":5}\nnot json\n', encoding="utf-8")
    rows = UsageLedger(path).load()
    assert len(rows) == 1


# --------------------------------------------------------------------------- #
# provider + router wiring
# --------------------------------------------------------------------------- #
def test_provider_records_usage_from_a_response():
    from wth_ai.llm.providers.openai_compat import OpenAICompatProvider

    provider = OpenAICompatProvider(provider_name="custom", api_key="k", base_url="http://x/v1")
    provider._record_usage({"usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}})
    assert provider.last_usage["total_tokens"] == 7


def test_model_response_carries_the_usage_block():
    """`ModelResponse.usage` used to be a hardcoded `{}`."""
    from wth_ai.llm.providers.ollama import OllamaProvider

    provider = OllamaProvider()
    provider._record_usage({"prompt_eval_count": 2, "eval_count": 3})
    envelope = provider.complete(model="m", messages=[{"role": "user", "content": "hi"}], role="planner")
    assert envelope["usage"]["total_tokens"] == 5


def test_router_records_usage_for_the_model_that_answered(tmp_path, monkeypatch):
    """A failover must be billed to the BACKUP model, not the primary."""
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    reset_ledger()

    from wth_ai.config import ProviderRoleConfig, WTHConfig
    from wth_ai.llm.errors import LLMError
    from wth_ai.llm.router import LLMProviderRouter

    class _Provider:
        provider_type = "stub"
        last_usage: dict = {}

        def available(self) -> bool:
            return True

        def chat_with_fallback(self, primary, backup, messages, **kwargs):
            self.last_usage = {"total_tokens": 42}
            return "text from backup", backup

    provider = _Provider()
    cfg = WTHConfig.load()
    cfg.providers["planner"] = ProviderRoleConfig(type="stub", model="primary-m", backup_model="backup-m")

    router = LLMProviderRouter(cfg)
    monkeypatch.setattr(router, "_get_or_create_provider", lambda ptype: provider)

    text, used = router.chat_for_role("planner", [{"role": "user", "content": "hi"}], timeout=5)
    assert text == "text from backup"
    assert used == "backup-m"

    rows = get_ledger().load()
    assert rows, "usage tidak tercatat di ledger"
    assert rows[-1].model == "backup-m"
    assert rows[-1].total_tokens == 42


def test_router_records_a_call_even_when_provider_reports_no_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    reset_ledger()

    from wth_ai.config import ProviderRoleConfig, WTHConfig
    from wth_ai.llm.router import LLMProviderRouter

    class _Provider:
        provider_type = "stub"
        last_usage: dict = {}

        def available(self) -> bool:
            return True

        def chat_with_fallback(self, primary, backup, messages, **kwargs):
            return "no usage here", primary

    cfg = WTHConfig.load()
    cfg.providers["planner"] = ProviderRoleConfig(type="stub", model="m")
    router = LLMProviderRouter(cfg)
    monkeypatch.setattr(router, "_get_or_create_provider", lambda ptype: _Provider())

    router.chat_for_role("planner", [{"role": "user", "content": "hi"}], timeout=5)
    data = get_ledger().summary()
    assert data["calls"] == 1
    assert data["total_tokens"] == 0

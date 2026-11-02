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


# --------------------------------------------------------------------------- #
# budget enforcement (the guard that did not exist: the budget was REPORTED,
# never enforced, and it compared against the lifetime total)
# --------------------------------------------------------------------------- #
def _router_with_stub(tmp_path, monkeypatch, *, provider_tokens=500):
    """A router whose only provider is a local stub (no network)."""
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
            self.calls = getattr(self, "calls", 0) + 1
            self.last_usage = {"total_tokens": provider_tokens}
            return "ok", primary

    provider = _Provider()
    cfg = WTHConfig.load()
    cfg.providers["planner"] = ProviderRoleConfig(type="stub", model="m")
    router = LLMProviderRouter(cfg)
    monkeypatch.setattr(router, "_get_or_create_provider", lambda ptype: provider)
    return router, provider


def test_budget_policy_parses_env(monkeypatch):
    from wth_ai.llm.usage import load_budget_policy

    monkeypatch.setenv("WTH_TOKEN_BUDGET", "5000")
    monkeypatch.setenv("WTH_BUDGET_MODE", "stop")
    monkeypatch.setenv("WTH_BUDGET_SCOPE", "total")
    policy = load_budget_policy()
    assert (policy.limit_tokens, policy.mode, policy.scope) == (5000, "stop", "total")
    assert policy.active is True


def test_budget_policy_tolerates_garbage(monkeypatch):
    from wth_ai.llm.usage import load_budget_policy

    monkeypatch.setenv("WTH_TOKEN_BUDGET", "banyak")
    monkeypatch.setenv("WTH_BUDGET_MODE", "ngawur")
    policy = load_budget_policy()
    assert policy.limit_tokens == 0 and policy.mode == "warn"
    assert policy.active is False


def test_session_scope_ignores_last_weeks_spend(tmp_path):
    """The measured defect: budget compared to the LIFETIME total, so a budget set
    today read as already-blown because of calls made earlier."""
    from wth_ai.llm.usage import BudgetPolicy, UsageLedger

    path = tmp_path / "usage.jsonl"
    old = UsageLedger(path)
    old.record(role="planner", provider="p", model="m", usage={"total_tokens": 999_999})

    fresh = UsageLedger(path, policy=BudgetPolicy(limit_tokens=1000, mode="stop", scope="session"))
    allowed, _ = fresh.check_budget()
    assert allowed is True, "sesi baru dianggap kehabisan budget gara-gara pemakaian lama"

    fresh.record(role="planner", provider="p", model="m", usage={"total_tokens": 1200})
    allowed, message = fresh.check_budget()
    assert allowed is False and "budget token habis" in message


def test_total_scope_does_count_lifetime(tmp_path):
    from wth_ai.llm.usage import BudgetPolicy, UsageLedger

    path = tmp_path / "usage.jsonl"
    old = UsageLedger(path)
    old.record(role="planner", provider="p", model="m", usage={"total_tokens": 5000})

    fresh = UsageLedger(path, policy=BudgetPolicy(limit_tokens=1000, mode="stop", scope="total"))
    allowed, _ = fresh.check_budget()
    assert allowed is False


def test_off_mode_never_blocks(tmp_path):
    from wth_ai.llm.usage import BudgetPolicy, UsageLedger

    ledger = UsageLedger(tmp_path / "usage.jsonl", policy=BudgetPolicy(limit_tokens=1, mode="off"))
    ledger.record(role="planner", provider="p", model="m", usage={"total_tokens": 10_000})
    allowed, message = ledger.check_budget()
    assert allowed is True and message == ""
    assert ledger.budget_state()["active"] is False


def test_stop_mode_refuses_before_spending(tmp_path, monkeypatch):
    """The essential property: refuse the call, do NOT let the provider run."""
    from wth_ai.llm.usage import BudgetExceeded

    monkeypatch.setenv("WTH_TOKEN_BUDGET", "1")
    monkeypatch.setenv("WTH_BUDGET_MODE", "stop")
    router, provider = _router_with_stub(tmp_path, monkeypatch, provider_tokens=500)

    router.chat_for_role("planner", [{"role": "user", "content": "hi"}], timeout=5)
    assert provider.calls == 1

    with pytest.raises(BudgetExceeded) as exc:
        router.chat_for_role("planner", [{"role": "user", "content": "hi"}], timeout=5)
    assert provider.calls == 1, "provider tetap dipanggil padahal budget sudah habis"
    assert "WTH_TOKEN_BUDGET" in str(exc.value)      # pesannya harus actionable


def test_warn_mode_continues_and_notifies_once(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WTH_TOKEN_BUDGET", "1")
    monkeypatch.setenv("WTH_BUDGET_MODE", "warn")
    router, provider = _router_with_stub(tmp_path, monkeypatch, provider_tokens=500)

    router.chat_for_role("planner", [{"role": "user", "content": "hi"}], timeout=5)
    router.chat_for_role("planner", [{"role": "user", "content": "hi"}], timeout=5)
    router.chat_for_role("planner", [{"role": "user", "content": "hi"}], timeout=5)

    assert provider.calls == 3, "mode warn tidak boleh menghentikan call"
    assert router.budget_notice, "notice tidak diisi"
    err = capsys.readouterr().err
    assert err.count("[budget]") == 1, f"peringatan harus sekali, dapat {err.count('[budget]')}"


def test_under_budget_is_silent(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WTH_TOKEN_BUDGET", "100000")
    monkeypatch.setenv("WTH_BUDGET_MODE", "warn")
    router, _ = _router_with_stub(tmp_path, monkeypatch, provider_tokens=10)
    router.chat_for_role("planner", [{"role": "user", "content": "hi"}], timeout=5)
    assert router.budget_notice == ""
    assert "[budget]" not in capsys.readouterr().err


def test_summary_reports_session_and_budget_with_the_right_denominator(tmp_path):
    from wth_ai.llm.usage import BudgetPolicy, UsageLedger

    path = tmp_path / "usage.jsonl"
    UsageLedger(path).record(role="planner", provider="p", model="m", usage={"total_tokens": 9000})

    ledger = UsageLedger(path, policy=BudgetPolicy(limit_tokens=1000, mode="warn", scope="session"))
    ledger.record(role="planner", provider="p", model="m", usage={"total_tokens": 300})
    text = ledger.format_summary()

    assert "Sesi ini: 1 call . 300 token" in text.replace("·", ".")
    # Label scope ikut di-assert: tanpa itu, "0/1000" di sesi baru terbaca
    # seolah budget belum tersentuh seumur hidup.
    assert "300/1000 token di sesi ini (di bawah)" in text
    assert "9000" not in text.split("Sesi ini")[1].split("Budget")[0]


def test_summary_labels_a_disabled_budget_as_unenforced(tmp_path):
    from wth_ai.llm.usage import BudgetPolicy, UsageLedger

    ledger = UsageLedger(tmp_path / "usage.jsonl", policy=BudgetPolicy(limit_tokens=500, mode="off"))
    ledger.record(role="planner", provider="p", model="m", usage={"total_tokens": 10})
    assert "tidak ditegakkan" in ledger.format_summary()


def test_summary_calls_out_lifetime_total_exceeding_a_session_budget(tmp_path):
    """`wth usage` in a fresh process reported "0/5000 (di bawah)" while the machine
    total was 14k -- technically true for that process, and thoroughly misleading."""
    from wth_ai.llm.usage import BudgetPolicy, UsageLedger

    path = tmp_path / "usage.jsonl"
    UsageLedger(path).record(role="planner", provider="p", model="m", usage={"total_tokens": 14_083})

    ledger = UsageLedger(path, policy=BudgetPolicy(limit_tokens=5_000, mode="warn", scope="session"))
    text = ledger.format_summary()
    assert "0/5000 token di sesi ini (di bawah)" in text
    assert "total seumur hidup 14083 token sudah di atas" in text

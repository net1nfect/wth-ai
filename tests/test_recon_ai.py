"""Fase 6 P4 tests: LLM smart recon layer (offline, fake router)."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from wth_ai.recon.ai import ReconAI
from wth_ai.recon.model import PriorityTarget


class FakeRouter:
    def __init__(self, data):
        self._data = data
        self.calls = 0

    def provider_key(self, ptype):
        return "sk-fake"

    def chat_json(self, model, messages, *, backup=None, timeout=None):
        self.calls += 1
        return self._data, model


def _cfg():
    return SimpleNamespace(models=SimpleNamespace(recon="", planner="planner/model"))


def _targets():
    return [
        PriorityTarget(asset="https://api.example.com/x", score=6, reasons=["alive:200"],
                       severity="medium", confidence=0.6),
        PriorityTarget(asset="https://static.example.com/", score=1, reasons=["alive:200"],
                       severity="info", confidence=0.5),
    ]


def test_disabled_under_test_mode(monkeypatch):
    monkeypatch.setenv("WTH_TEST_MODE", "1")
    ai = ReconAI(config=_cfg(), router=FakeRouter({}))
    assert ai.enabled() is False
    targets = _targets()
    out, hyps = ai.augment(targets, {"subdomains": 1})
    assert out is targets and hyps == []


def test_augment_raises_severity_and_adds_reason(monkeypatch):
    monkeypatch.delenv("WTH_TEST_MODE", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake")
    data = {
        "rankings": [
            {"asset": "https://api.example.com/x", "severity": "high", "reason": "auth API surface"},
            {"asset": "https://static.example.com/", "severity": "critical", "reason": "noise"},
        ],
        "hypotheses": [
            {"title": "IDOR on /x", "asset": "https://api.example.com/x",
             "rationale": "numeric id param", "confidence": 0.6},
            {"title": "ungrounded", "asset": "https://evil.invalid/", "rationale": "x"},
        ],
    }
    ai = ReconAI(config=_cfg(), router=FakeRouter(data))
    assert ai.enabled() is True
    targets = _targets()
    out, hyps = ai.augment(targets, {"subdomains": 2})
    top = out[0]
    assert top.asset == "https://static.example.com/" or top.severity in ("high", "critical")
    api = next(t for t in out if t.asset == "https://api.example.com/x")
    assert api.severity == "high"
    assert any(r.startswith("ai:") for r in api.reasons)
    # only grounded hypothesis kept, tagged HIPOTESIS
    assert len(hyps) == 1
    assert hyps[0]["title"].startswith("HIPOTESIS:")
    assert hyps[0]["proven"] is False


def test_ai_never_lowers_severity(monkeypatch):
    monkeypatch.delenv("WTH_TEST_MODE", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake")
    data = {"rankings": [{"asset": "https://api.example.com/x", "severity": "info", "reason": "meh"}]}
    ai = ReconAI(config=_cfg(), router=FakeRouter(data))
    targets = _targets()
    out, _ = ai.augment(targets, {})
    api = next(t for t in out if t.asset == "https://api.example.com/x")
    assert api.severity == "medium"  # not lowered to info


def test_augment_degrades_on_llm_error(monkeypatch):
    monkeypatch.delenv("WTH_TEST_MODE", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake")

    class BoomRouter(FakeRouter):
        def chat_json(self, *a, **k):
            raise RuntimeError("rate limited")

    ai = ReconAI(config=_cfg(), router=BoomRouter({}))
    targets = _targets()
    out, hyps = ai.augment(targets, {})
    assert out is targets and hyps == []

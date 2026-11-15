"""Adaptive engine: deterministic signals, mutations, memory, live behavior."""

from __future__ import annotations

import json

from wth_ai.adaptive import (
    AdaptiveEngine,
    HttpRequest,
    Observation,
    TargetMemory,
    classify,
    propose,
)


class _Result:
    def __init__(self, status_code: int, headers=None, body: str = "",
                 transcript_id: str = "", url: str = "") -> None:
        self.status_code = status_code
        self.response_headers = headers or {}
        self.stdout = body
        self.analysis = ""
        self.transcript_id = transcript_id
        self.url = url
        self.stderr = ""


def test_classify_cloudflare_403_is_blocked_with_vendor():
    signal = classify(403, {"CF-Ray": "8f00", "Server": "cloudflare"},
                      "Attention Required! cloudflare ray id")
    assert signal.is_blocked
    assert signal.kind == "blocked"
    assert signal.vendor == "cloudflare"
    assert signal.status == 403


def test_classify_429_and_auth_are_blocked_kinds():
    assert classify(429, {}, "").kind == "rate_limited"
    assert classify(401, {}, "").kind == "auth_required"
    assert classify(200, {}, "ok").kind == "success"
    assert classify(200, {}, "Not Found").kind == "soft_not_found"


def test_request_probe_roundtrip_and_fingerprint_changes(tmp_path):
    req = HttpRequest("get", "http://example.com/admin?a=1",
                      {"X-Test": "1"}, '{"x":1}')
    probe = req.to_probe(purpose="unit")
    clone = HttpRequest.from_probe(probe)
    assert probe["method"] == "GET"
    assert clone.url == req.url
    assert clone.headers == req.headers
    assert clone.body == req.body
    assert clone.fingerprint() == req.fingerprint()

    changed = HttpRequest("GET", "http://example.com/admin?a=1",
                          {"X-Test": "2"}, req.body)
    assert changed.fingerprint() != req.fingerprint()


def test_propose_vendor_boost_first():
    req = HttpRequest("GET", "http://example.com/admin")
    memory = TargetMemory.load("example.com", directory=None)
    moves = propose(req, signal_kind="waf", vendor="cloudflare", memory=memory, limit=5)
    assert moves
    assert moves[0].strategy in {
        "path_double_encode", "path_case", "header_xff",
        "header_x_original_url", "method_override_header",
    }
    assert all(move.request.url for move in moves)


def test_memory_persists_wins_and_reloads(tmp_path):
    memory = TargetMemory.load("example.com", tmp_path)
    memory.record("header_xff", signal="success", status=200, improved=True, evidence="TX1")
    memory.record("header_xff", signal="blocked", status=403, improved=False, evidence="TX2")
    memory.record("path_case", signal="blocked", status=403, improved=False)

    raw = json.loads((tmp_path / "example.com.json").read_text())
    assert raw["strategies"]["header_xff"]["tries"] == 2
    assert raw["strategies"]["header_xff"]["wins"] == 1
    assert raw["best_strategy"] == "header_xff"

    reloaded = TargetMemory.load("example.com", tmp_path)
    assert reloaded.attempts == 3
    assert reloaded.strategies["header_xff"].wins == 1
    assert reloaded.top()[0]["strategy"] in {"header_xff", "path_case"}


def test_engine_bypasses_blocked_baseline_and_records_memory(tmp_path):
    def probe(payload):
        headers = {k.lower(): v for k, v in (payload.get("headers") or {}).items()}
        if headers.get("x-forwarded-for") == "127.0.0.1":
            return _Result(200, {}, "ok", transcript_id="TX-OK", url=payload["url"])
        return _Result(403, {"CF-Ray": "8f00"}, "request blocked",
                       transcript_id="TX-BLOCK", url=payload["url"])

    memory = TargetMemory.load("example.com", tmp_path)
    engine = AdaptiveEngine("example.com", memory=memory)
    report = engine.run(HttpRequest("GET", "http://example.com/admin"), probe,
                        max_rounds=1, moves_per_round=10, stop_on_success=True)

    assert report.baseline_status == 403
    assert report.baseline_signal == "blocked"
    assert report.vendor == "cloudflare"
    assert report.attempts
    assert report.bypassed is True
    assert report.best_strategy
    assert report.memory_path.endswith("example.com.json")
    assert memory.strategies[report.best_strategy].wins >= 1
    assert (tmp_path / "example.com.json").exists()


def test_engine_retry_blocked_short_circuits_on_success(tmp_path):
    def probe(payload):
        if "X-Forwarded-For" in (payload.get("headers") or {}):
            return _Result(200, {}, "ok", transcript_id="TX-OK")
        return _Result(403, {"CF-Ray": "8f00"}, "blocked", transcript_id="TX-BLOCK")

    engine = AdaptiveEngine("example.com", memory=TargetMemory.load("example.com", tmp_path))
    attempts = engine.retry_blocked(
        HttpRequest("GET", "http://example.com/x"),
        _Result(403, {"CF-Ray": "8f00"}, "blocked"),
        probe,
        limit=6,
    )
    assert attempts
    assert attempts[-1].improved is True
    assert attempts[-1].strategy == "header_xff"


def test_engine_does_not_claim_bypass_when_baseline_is_open(tmp_path):
    def probe(payload):
        return _Result(200, {}, "ok")

    engine = AdaptiveEngine("example.com", memory=TargetMemory.load("example.com", tmp_path))
    report = engine.run(HttpRequest("GET", "http://example.com/"), probe, max_rounds=2)
    assert report.attempts == []
    assert report.bypassed is False
    assert "tidak terblokir" in report.stopped_reason

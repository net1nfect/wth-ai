"""Integration — scope enforcement end-to-end di jalur executor pipeline.

Membuktikan guard di `ProbeSession` tidak hanya ada, tetapi benar-benar
dipakai oleh `execute_pipeline` (satu-satunya transport vuln executor) dan
tidak memblokir target yang sah.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.models import ExecutorTask, PermissionMode, SessionState
from wth_ai.pipeline.executor import execute_pipeline


class _FakeExecutor:
    def __init__(self, state):
        self.state = state
        self.results = []

    def _result(self, task, **kwargs):
        self.results.append(kwargs)
        class _R:
            pass
        r = _R()
        for k, v in kwargs.items():
            setattr(r, k, v)
        return r


def _state(target="example.com", in_scope=None):
    s = SessionState()
    s.permission_mode = PermissionMode.STANDARD_TESTING
    s.scope.target = target
    s.scope.authorized = True
    s.scope.in_scope = in_scope or [target]
    s.scope.rate_limit_rps = 0
    return s


def test_executor_pipeline_blocks_out_of_scope_without_network(monkeypatch):
    """Out-of-scope target must produce zero real HTTP calls inside pipeline."""
    calls = []

    def fake_get(*a, **k):
        calls.append(a)
        raise AssertionError("network must not be reached for out-of-scope")

    monkeypatch.setattr("wth_ai.pipeline.session.requests.get", fake_get)

    state = _state("example.com")
    ex = _FakeExecutor(state)
    task = ExecutorTask(
        id="T-int-1", goal="probe", module="dynamic",
        target="https://evil-external.tld/callback",
        permission_required=PermissionMode.STANDARD_TESTING,
        evidence_schema={"type": "ssrf"},
    )
    execute_pipeline(ex, task, "ssrf")
    assert calls == []


def test_executor_pipeline_allows_in_scope_target(monkeypatch):
    """In-scope target must reach the transport (no false blocking)."""
    calls = []

    class _Resp:
        url = "https://example.com/?q=1"
        status_code = 200
        text = "<html>ok</html>"
        headers = {"Content-Type": "text/html"}

    def fake_get(url, *a, **k):
        calls.append(url)
        return _Resp()

    monkeypatch.setattr("wth_ai.pipeline.session.requests.get", fake_get)

    state = _state("example.com")
    ex = _FakeExecutor(state)
    task = ExecutorTask(
        id="T-int-2", goal="probe", module="dynamic",
        target="https://example.com/?q=1",
        permission_required=PermissionMode.STANDARD_TESTING,
        evidence_schema={"type": "ssrf"},
    )
    execute_pipeline(ex, task, "ssrf")
    assert calls, "in-scope request harus dikirim"

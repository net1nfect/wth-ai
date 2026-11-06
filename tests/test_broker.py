"""Coverage: `wth_ai/broker/broker.py` (ExecutionBroker).

Broker adalah jalur eksekusi nyata (dipakai `AgentTerminalBridge.dispatch_tool`,
`tool_loop`, supervisor). Ini memverifikasi routing HTTP/shell/unknown dan
normalisasi kegagalan ke `ExecutionResult` seragam dengan audit log. Broker
sengaja tanpa policy sendiri — pengujian ini memakai bridge palsu sehingga
tidak mengetes ulang permission/scope.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")


class _FakeTerminal:
    def __init__(self):
        self.http_calls = []
        self.shell_calls = []

    def http_probe(self, method, url, *, actor, purpose, headers, body, approved):
        self.http_calls.append((method, url, actor, purpose))
        return {"type": "http", "ok": True, "status": 200,
                "actor": actor, "purpose": purpose}

    def run_shell(self, command, *, actor, purpose, approved):
        self.shell_calls.append((command, actor, purpose))
        return {"type": "shell", "ok": True, "exit_code": 0,
                "actor": actor, "purpose": purpose}


def _broker(pres=None):
    from wth_ai.broker.models import ActionIntent, ExecutionResult

    bridge = pres or _FakeTerminal()
    from typing import Any, cast

    from wth_ai.broker.broker import ExecutionBroker

    return bridge, ExecutionBroker(cast(Any, bridge)), ActionIntent, ExecutionResult


def _intent(ActionIntent, *, tool="http_probe", url="http://example.com",
            actor="tester", purpose="p1", arguments=None, **kw):
    base = {"url": url, "method": "GET"}
    if arguments is not None:
        base = dict(arguments)
    base.update(kw)
    return ActionIntent(intent_id="i-1", tool_name=tool, actor=actor,
                        purpose=purpose, arguments=base, approved=False)


def test_http_tool_routes_to_http_probe():
    bridge, broker, ActionIntent, ExecutionResult = _broker()
    res = broker.execute(_intent(ActionIntent))
    assert res.blocked is False
    assert len(bridge.http_calls) == 1
    assert bridge.http_calls[0][0] == "GET"
    assert bridge.http_calls[0][1] == "http://example.com"


def test_shell_tool_routes_to_run_shell():
    bridge, broker, ActionIntent, ExecutionResult = _broker()
    intent = _intent(ActionIntent, tool="run_shell",
                     arguments={"command": "id", "url": ""})
    res = broker.execute(intent)
    assert res.blocked is False
    assert len(bridge.shell_calls) == 1
    assert bridge.shell_calls[0][0] == "id"


def test_unknown_tool_blocked():
    _, broker, ActionIntent, ExecutionResult = _broker()
    res = broker.execute(_intent(ActionIntent, tool="nonsense",
                                 arguments={}))
    assert res.blocked is True
    assert "tidak mengenal" in res.block_reason


def test_unknown_tool_with_url_arg_does_not_probe():
    """P2-7: nama tool tak dikenal TIDAK boleh dirute ke HTTP hanya karena
    membawa arg `url` (klasifikasi berdasar tool_name, bukan bentuk args)."""
    bridge, broker, ActionIntent, ExecutionResult = _broker()
    res = broker.execute(_intent(ActionIntent, tool="weird_tool",
                                 url="http://example.com"))
    assert res.blocked is True
    assert "tidak mengenal" in res.block_reason
    assert bridge.http_calls == []


def test_exception_normalized_to_execution_result():
    class _ExplodingBridge:
        def http_probe(self, *a, **k):
            raise RuntimeError("boom")

    bridge, broker, ActionIntent, _ = _broker(_ExplodingBridge())
    res = broker.execute(_intent(ActionIntent))
    assert res.exit_code == 1
    assert "RuntimeError: boom" in res.stderr


def test_http_intent_with_non_string_url_is_blocked():
    bridge, broker, ActionIntent, ExecutionResult = _broker()
    res = broker.execute(_intent(ActionIntent, arguments={"url": 123, "method": "GET"}))
    assert res.blocked is True
    assert "url" in (res.block_reason or "")
    assert bridge.http_calls == []


def test_shell_intent_with_non_string_command_is_blocked():
    bridge, broker, ActionIntent, ExecutionResult = _broker()
    res = broker.execute(_intent(ActionIntent, tool="run_shell", arguments={"command": ["ls", "-la"]}))
    assert res.blocked is True
    assert "command" in (res.block_reason or "")
    assert bridge.shell_calls == []


def test_http_intent_with_bad_headers_is_blocked():
    bridge, broker, ActionIntent, ExecutionResult = _broker()
    res = broker.execute(_intent(ActionIntent, arguments={"url": "http://example.com",
                                                         "headers": "not-a-dict"}))
    assert res.blocked is True
    assert "header" in (res.block_reason or "").lower()

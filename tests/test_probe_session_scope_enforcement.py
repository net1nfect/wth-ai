"""Regression tests — enforcement scope di titik eksekusi (prioritas #4).

Celah: `ScopeManager.is_in_scope()` dicek di `ExecutorAgent.execute_module()`,
tetapi `ProbeSession` sendiri (satu-satunya transport HTTP pipeline) tidak
punya guard. Pemanggil lain (`supervisor_dispatch`, runner) bisa mengirim
request ke URL di luar scope tanpa melewati gate mana pun.

Kontrak baru: `ProbeSession(scope_guard=...)` menolak request out-of-scope
SEBELUM koneksi dibuat (fail-closed) dan mencatat alasannya. Tanpa
`scope_guard`, perilaku lama tidak berubah (kompatibilitas).
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.pipeline.models import RequestSpec, Surface
from wth_ai.pipeline.session import ProbeSession


class _Sender:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, request):
        self.calls.append(request.url)
        return {
            "method": request.method,
            "url": request.url,
            "status_code": 200,
            "stdout": "ok",
            "response_headers": {},
            "elapsed_ms": 1.0,
        }


class _Guard:
    def __init__(self, allowed_host: str) -> None:
        self.allowed_host = allowed_host

    def is_in_scope(self, target: str):
        host = (target.split("://", 1)[-1]).split("/")[0].split(":")[0].lower()
        if host == self.allowed_host:
            return True, f"in scope: {host}"
        return False, f"Host {host} tidak cocok dengan in-scope list"


def test_in_scope_request_is_sent():
    sender = _Sender()
    session = ProbeSession(sender=sender, scope_guard=_Guard("example.com"))
    surface = Surface.from_target("https://example.com/login")
    obs, _ = session.baseline(surface)
    assert sender.calls == ["https://example.com/login"]
    assert obs.status == 200


def test_out_of_scope_request_blocked_before_send():
    sender = _Sender()
    session = ProbeSession(sender=sender, scope_guard=_Guard("example.com"))
    surface = Surface.from_target("https://evil-external.tld/callback")
    obs, body = session.baseline(surface)
    assert sender.calls == []  # tidak ada koneksi sama sekali
    assert obs.status == 0
    assert "blocked" in body.lower() or "scope" in body.lower()


def test_explicit_send_is_also_guarded():
    sender = _Sender()
    session = ProbeSession(sender=sender, scope_guard=_Guard("example.com"))
    req = RequestSpec(method="GET", url="https://evil-external.tld/x")
    obs, _ = session.send(req)
    assert sender.calls == []
    assert obs.status == 0


def test_no_guard_preserves_legacy_behavior():
    sender = _Sender()
    session = ProbeSession(sender=sender)  # no guard
    surface = Surface.from_target("https://anything.tld/x")
    session.baseline(surface)
    assert sender.calls == ["https://anything.tld/x"]


def test_blocked_request_returns_transcript_evidence():
    sender = _Sender()
    session = ProbeSession(sender=sender, scope_guard=_Guard("example.com"))
    surface = Surface.from_target("https://evil-external.tld/callback")
    obs, _ = session.baseline(surface)
    assert obs.transcript_id  # blocked decision is still evidenced

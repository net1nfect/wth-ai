"""Regresi target lokal: skema URL + opt-in guard untuk alamat yang operator sebut.

Ditemukan dari UAT CLI nyata dengan server lokal milik sendiri: recon melaporkan 0 URL
dan setiap langkah autopilot berstatus kosong. Dua sebab:
1. `normalize_url("127.0.0.1:42969")` -> "https://127.0.0.1:42969"; probe TLS ke server
   HTTP polos gagal.
2. Guard SSRF memblokir alamat private/loopback, sehingga lab lokal tidak bisa diuji --
   padahal proyek ini punya profile/environment "local".
3. `{base}` di autopilot diisi tanpa skema -> requests MissingSchema.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.recon.http import host_is_blocked, is_private_literal, normalize_url


def test_local_target_gets_http_not_https():
    assert normalize_url("127.0.0.1:3000") == "http://127.0.0.1:3000"
    assert normalize_url("localhost:8080/app") == "http://localhost:8080/app"
    assert normalize_url("192.168.1.10") == "http://192.168.1.10"
    assert normalize_url("10.0.0.5:9000/x") == "http://10.0.0.5:9000/x"


def test_public_hosts_still_get_https_and_explicit_scheme_is_respected():
    assert normalize_url("example.com") == "https://example.com"
    assert normalize_url("api.example.com:8443/v1") == "https://api.example.com:8443/v1"
    assert normalize_url("http://example.com/x") == "http://example.com/x"


def test_private_literal_detection_has_no_dns():
    assert is_private_literal("127.0.0.1") is True
    assert is_private_literal("10.1.2.3") is True
    assert is_private_literal("8.8.8.8") is False
    assert is_private_literal("example.com") is False


def test_ssrf_guard_blocks_private_by_default_and_only_opens_when_declared():
    reason = host_is_blocked("127.0.0.1", resolve=False)
    assert reason and "SSRF" in reason, "guard harus tetap menolak secara default"
    assert host_is_blocked("127.0.0.1", resolve=False, allow_private=True) is None
    # alamat publik tidak terpengaruh opt-in
    assert host_is_blocked("8.8.8.8", resolve=False) is None


def test_builtin_context_defaults_to_blocking_internal_addresses():
    from wth_ai.recon.tools.builtin import BuiltinContext

    ctx = BuiltinContext(target="example.com", host="example.com", base_url="https://example.com")
    assert ctx.allow_private is False, "default harus tertutup"


def test_autopilot_base_has_a_scheme(tmp_path):
    from wth_ai.hunt.autopilot import run_autopilot

    class _State:
        def __init__(self):
            self.metadata = {}
            scope = type("ScopeStub", (), {})
            scope.destructive_allowed = False
            self.scope = scope

    report = run_autopilot(target="127.0.0.1:42969", run_probe=lambda probe: None,
                           state=_State(), max_cycles=1, max_steps_per_cycle=2, dry_run=True)
    urls = [str(step.probe.get("url", "")) for step in report.steps]
    assert urls, "rencana harus berisi langkah"
    for url in urls:
        if url.startswith("http"):
            assert url.startswith("http://127.0.0.1:42969") or "://" in url
    assert not any(url.startswith("127.0.0.1") for url in urls), (
        f"URL tanpa skema akan gagal di requests: {urls[:3]}"
    )

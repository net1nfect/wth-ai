"""P1-2: kode payload RPC wajib discan terhadap denylist/high-risk global.

Temuan audit 2026-11-06 (P1-2): `ExecuteRpcServer.execute()` memanggil
`check_command("execute")` (permission), tetapi TIDAK pernah memanggil
`denylist_match()`/`high_risk_match()` pada payload `code`. Engine WU-104
mendokumentasikan denylist wajib untuk "arbitrary Python code blobs over gateway
UDS", tapi tidak diimplementasikan. Kode destruktif (`rm -rf /`, `mkfs`, `dd`)
tereksekusi bila lolos gate permission, bahkan di mode yang seharusnya tetap
menerapkan deny global.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")


def _server(monkeypatch, tmp_path):
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)

    from wth_ai.rpc.execute_server import ExecuteRpcServer

    srv = ExecuteRpcServer()
    # Scope terhadap target lokal supaya lolos gate scope, dan permission cukup
    # untuk `execute` (kita uji denylist, bukan gate permission/scope).
    srv.scope.set_scope(
        target="127.0.0.1", in_scope=["127.0.0.1"], authorized=True
    )
    from wth_ai.models import PermissionMode

    # UNRESTRICTED: wildcard memberi `execute` (tool generik tak terdaftar di
    # profil), sehingga gate permission lolos dan yang kita uji adalah denylist.
    srv.perms.set_mode(PermissionMode.UNRESTRICTED)
    return srv


def test_rpc_rejects_denylisted_code(monkeypatch, tmp_path):
    srv = _server(monkeypatch, tmp_path)

    # Payload memuat literal `rm -rf /` (pattern denylist) di dalam string kode
    # Python — scanner konten harus menangkapnya.
    code = 'import os; os.system("rm -rf /")'
    res = srv.execute({"target": "127.0.0.1", "code": code})
    assert res["status"] in ("blocked", "failed"), res
    assert "denylist" in (res.get("error", "") + res.get("reason", "")).lower()


def test_rpc_rejects_high_risk_code(monkeypatch, tmp_path):
    srv = _server(monkeypatch, tmp_path)

    from wth_ai.policy.engine import high_risk_match

    # Pattern high-risk dengan literal nyata yang bisa dirender ke payload.
    import wth_ai.policy.engine as pol

    risky = next(
        (p for p in pol.HIGH_RISK_PATTERNS if high_risk_match(p.lower()) is not None),
        None,
    )
    if risky is None:
        import pytest

        pytest.skip("tidak ada pattern high-risk yang cocok")

    code = f"x = {risky!r}\nprint(x)\n"
    res = srv.execute({"target": "127.0.0.1", "code": code})
    assert res["status"] in ("blocked", "failed"), res


def test_rpc_denies_dd_targeting_device(monkeypatch, tmp_path):
    srv = _server(monkeypatch, tmp_path)
    code = 'import os; os.system("dd if=/dev/zero of=/dev/sda")'
    res = srv.execute({"target": "127.0.0.1", "code": code})
    assert res["status"] in ("blocked", "failed"), res


def test_rpc_allows_boring_code(monkeypatch, tmp_path):
    srv = _server(monkeypatch, tmp_path)
    code = "x = 1 + 1\nprint(x)\n"
    res = srv.execute({"target": "127.0.0.1", "code": code})
    # Boleh sukses ATAU gagal runtime — yang penting TIDAK diblokir denylist.
    assert res["status"] in ("success", "failed"), res

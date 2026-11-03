"""Regression tests — WU-015: ManifestToolRunner.run_tool() shell=True removal.

Temuan (OBS-3 dari laporan WU-004, diverifikasi ulang lebih dalam sesuai instruksi
sebelum menentukan severity): wth_ai/tools/runner.py::ManifestToolRunner.run_tool()
memakai subprocess.run(cmd, shell=True, ...) dengan command string hasil f-string
concatenation binary+args -- klasik command-injection pattern KALAU args berasal
dari konten yang bisa dipengaruhi pihak luar.

Verifikasi reachability (revisi dari klaim awal): class ini di-wire lewat
constructor injection ke ExecutorAgent dan 5 BaseModule subclass (StaticAnalyzer,
DynamicTester, Fuzzer, DependencyScanner, LogParser) TAPI tidak satu pun caller
tersebut benar-benar memanggil run_tool() -- semua pakai self.runner_v2 (jalur
terpisah). Satu-satunya pemanggil run_tool() dalam file yang sama
(run_tools_parallel_specs) juga tidak punya caller di manapun. Kesimpulan: DEAD
CODE saat ini, bukan reachable live bypass -- severity MEDIUM (landmine defense-
in-depth), BUKAN HIGH/reachable seperti indikasi awal. Tetap diperbaiki karena
murah dan zero regression risk (tidak ada caller yang bisa patah).
"""

from __future__ import annotations

import os
import uuid

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.config import WTHConfig
from wth_ai.models import PermissionMode
from wth_ai.permission_manager import PermissionManager
from wth_ai.scope_manager import ScopeManager
from wth_ai.tools.registry import ToolRegistry, ToolSpec
from wth_ai.tools.runner import ManifestToolRunner


def _make_runner(mode: PermissionMode = PermissionMode.UNRESTRICTED) -> ManifestToolRunner:
    """UNRESTRICTED dipakai sengaja untuk mengisolasi unit-under-test (subprocess
    execution safety) dari permission-allowlist logic (sudah diuji terpisah &
    lengkap di tests/test_permission_manager_correctness.py, WU-004)."""
    config = WTHConfig.load()
    perm = PermissionManager(mode)
    scope = ScopeManager()
    registry = ToolRegistry()
    registry.tools["test-echo"] = ToolSpec(
        id="test-echo", binary="echo", category="test", permission="read_only",
    )
    return ManifestToolRunner(config, perm, scope, registry)


def test_run_tool_does_not_interpret_shell_metacharacters(tmp_path):
    """Test yang GAGAL pada implementasi lama (shell=True) dan LOLOS setelah fix.

    Command "echo hello; touch <marker>" -- kalau shell=True masih aktif, ';'
    akan dipisah shell jadi DUA command terpisah dan file marker akan tercipta.
    Setelah fix (argv list, shell=False), seluruh string jadi argumen literal
    untuk binary "echo" saja -- "touch" tidak pernah dieksekusi sebagai program."""
    marker = tmp_path / f"wu015_marker_{uuid.uuid4().hex}.txt"
    runner = _make_runner()
    args = f"hello; touch {marker}"
    stdout, stderr, code = runner.run_tool("test-echo", args)
    assert not marker.exists(), (
        "Shell metacharacter ';' masih dieksekusi sebagai command terpisah -- "
        "shell=True kemungkinan masih aktif (regresi WU-015)"
    )


def test_run_tool_does_not_interpret_command_substitution(tmp_path):
    """Boundary tambahan: command substitution $(...) juga tidak boleh dieval."""
    marker = tmp_path / f"wu015_marker2_{uuid.uuid4().hex}.txt"
    runner = _make_runner()
    args = f"$(touch {marker})"
    runner.run_tool("test-echo", args)
    assert not marker.exists(), "Command substitution $(...) masih dieval -- shell=True aktif"


def test_run_tool_still_executes_legitimate_command():
    """Positive/compatibility: command legitimate tanpa metacharacter tetap jalan
    normal -- fix tidak boleh membuat false-deny/false-break untuk usage biasa."""
    runner = _make_runner()
    stdout, stderr, code = runner.run_tool("test-echo", "hello world")
    assert code == 0
    assert "hello world" in stdout


def test_run_tool_unknown_tool_id_returns_error():
    runner = _make_runner()
    stdout, stderr, code = runner.run_tool("nonexistent-tool-xyz", "")
    assert code == 1
    assert "Unknown tool" in stderr


def test_run_tool_respects_permission_deny():
    """check_command() masih dikonsultasikan (gate tidak bypass akibat refactor) --
    di mode READ_ONLY_RECON, 'echo' bukan bagian READ_ONLY_TOOLS jadi harus DENY."""
    runner = _make_runner(PermissionMode.READ_ONLY_RECON)
    stdout, stderr, code = runner.run_tool("test-echo", "hello")
    assert code == 1
    assert stdout == ""


def test_no_reachable_caller_of_run_tool_exists_yet():
    """Regression guard untuk temuan reachability itu sendiri -- kalau di masa
    depan seseorang mewire ulang run_tool() ke caller baru, test ini gagal
    sebagai sinyal untuk meninjau ulang security review-nya (bukan mencegah
    perubahan, sekadar memaksa perubahan itu terlihat & disengaja)."""
    import subprocess

    # Sengaja OR-kan beberapa nama variabel penampung hasil grep manual di WU-015:
    # run_tools_parallel_specs (satu-satunya pemanggil run_tool internal) sendiri
    # tidak punya caller. Verifikasi ulang via grep runtime supaya tes ini
    # otomatis gagal kalau asumsi ini berubah di masa depan.
    result = subprocess.run(
        ["grep", "-rn", "run_tools_parallel_specs", "wth_ai/", "--include=*.py"],
        capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(__file__)),
    )
    call_sites = [l for l in result.stdout.splitlines() if "def run_tools_parallel_specs" not in l]
    assert not call_sites, (
        f"run_tools_parallel_specs() sekarang punya caller baru: {call_sites} -- "
        "tinjau ulang apakah ManifestToolRunner.run_tool() sudah reachable, "
        "security review tambahan mungkin diperlukan"
    )

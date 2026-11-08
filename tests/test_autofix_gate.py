"""Auto-fix PR berbatas (§1.2 poin 2): hanya full_lab + --destructive, tanpa push.

Yang dijaga test ini bukan "patch-nya pintar", tapi gerbangnya: mode selain
`full_lab` tidak jalan, tanpa `--destructive` tidak jalan, patch tidak boleh
keluar worktree, retest wajib, dan tidak ada satu pun argv yang berisi `push`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from wth_ai.autofix.apply import ApplyResult, apply_patch
from wth_ai.autofix.planner import AutofixUnavailable, Patch, plan_fix
from wth_ai.redteam.models import AttemptState, Finding


def _repo(tmp_path: Path) -> Path:
    work = tmp_path / "worktree"
    work.mkdir()
    (work / "app.py").write_text("def check(x):\n    return x == 1\n")
    for argv in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.email=t@example", "-c", "user.name=t", "commit", "-qm", "init"]):
        subprocess.run(["git", *argv], cwd=work, check=True,
                       capture_output=True, timeout=60)
    return work

def _patch(work: Path, *, path: str = "app.py") -> Patch:
    return Patch(
        finding_id="F-001",
        path=path,
        diff=("diff --git a/app.py b/app.py\n"
              "--- a/app.py\n"
              "+++ b/app.py\n"
              "@@ -1,2 +1,2 @@\n"
              " def check(x):\n"
              "-    return x == 1\n"
              "+    return x in (1, True)\n"),
        rationale="perbandingan longgar diperketat",
    )

def test_requires_full_lab_mode(tmp_path: Path) -> None:
    work = _repo(tmp_path)
    result = apply_patch(_patch(work), work, mode="elevated", destructive=True)
    assert result.ok is False
    assert "full_lab" in result.error
    assert (work / "app.py").read_text() == "def check(x):\n    return x == 1\n"

def test_requires_destructive_flag(tmp_path: Path) -> None:
    work = _repo(tmp_path)
    result = apply_patch(_patch(work), work, mode="full_lab", destructive=False)
    assert result.ok is False
    assert "destructive" in result.error

def test_path_outside_worktree_is_refused(tmp_path: Path) -> None:
    work = _repo(tmp_path)
    result = apply_patch(_patch(work, path="../outside.py"), work,
                         mode="full_lab", destructive=True)
    assert result.ok is False
    assert "di luar worktree" in result.error

def test_applies_patch_and_never_pushes(tmp_path: Path, monkeypatch) -> None:
    work = _repo(tmp_path)
    seen: list[list[str]] = []
    real_run = subprocess.run

    def recorder(argv, *args, **kwargs):
        seen.append(list(argv))
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", recorder)

    result = apply_patch(_patch(work), work, mode="full_lab", destructive=True)

    assert result.ok is True, result.error
    assert "x in (1, True)" in (work / "app.py").read_text()
    assert seen, "tidak ada perintah yang dijalankan"
    assert all("push" not in part for argv in seen for part in argv)

def test_retest_failure_reverts(tmp_path: Path) -> None:
    work = _repo(tmp_path)
    result = apply_patch(
        _patch(work), work, mode="full_lab", destructive=True,
        retest_argv=["python3", "-c", "raise SystemExit(1)"],
    )

    assert result.ok is False
    assert "retest" in result.error
    # Patch dibatalkan: file kembali seperti semula.
    assert (work / "app.py").read_text() == "def check(x):\n    return x == 1\n"

def test_successful_retest_is_reported(tmp_path: Path) -> None:
    work = _repo(tmp_path)
    result = apply_patch(
        _patch(work), work, mode="full_lab", destructive=True,
        retest_argv=["python3", "-c", "print('ok')"],
    )

    assert result.ok is True
    assert "ok" in result.retest_output

def test_plan_refuses_finding_without_suggestion() -> None:
    finding = Finding(id="F-002", hypothesis="IDOR", vuln_class="idor",
                      target="http://127.0.0.1", state=AttemptState.CANDIDATE)

    with pytest.raises(AutofixUnavailable, match="saran patch"):
        plan_fix(finding)

def test_plan_reads_diff_block_from_evidence() -> None:
    finding = Finding(
        id="F-003",
        hypothesis="shell=True",
        vuln_class="injection",
        target="app.py",
        evidence=["static:opengrep:wth.python.subprocess-shell-true:app.py:12\n"
                  "```diff\n"
                  "--- a/app.py\n"
                  "+++ b/app.py\n"
                  "@@ -1 +1 @@\n"
                  "-run(cmd, shell=True)\n"
                  "+run(cmd, shell=False)\n"
                  "```"],
    )

    patch = plan_fix(finding)

    assert patch.finding_id == "F-003"
    assert patch.path == "app.py"
    assert "shell=False" in patch.diff
    assert isinstance(patch, Patch)

def test_result_shape() -> None:
    result = ApplyResult(ok=False, error="x")
    assert result.applied is False
    assert result.retest_output == ""

def _cli(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    import os
    import sys

    repo = Path(__file__).resolve().parents[1]
    env = {**os.environ, "WTH_HOME": str(tmp_path / "home"),
           "WTH_SKIP_SETUP": "1", "WTH_PLAIN": "1"}
    return subprocess.run(
        [sys.executable, "wth-ai-main.py", "autofix", *args],
        cwd=repo, capture_output=True, text=True, timeout=180, env=env)

def _bundle_with_finding(tmp_path: Path) -> Path:
    import json

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "findings.json").write_text(json.dumps([{
        "id": "F-001",
        "hypothesis": "shell=True",
        "vuln_class": "injection",
        "target": "app.py",
        "evidence": ["```diff\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n"
                     "-run(cmd, shell=True)\n+run(cmd, shell=False)\n```"],
    }]))
    return bundle

def test_cli_refuses_without_destructive(tmp_path: Path) -> None:
    bundle = _bundle_with_finding(tmp_path)
    work = _repo(tmp_path)
    out = _cli(tmp_path, "--bundle", str(bundle), "--finding", "F-001",
               "--worktree", str(work), "--mode", "full_lab")
    assert out.returncode != 0
    assert "destructive" in (out.stdout + out.stderr)
    assert "shell=False" not in (work / "app.py").read_text()

def test_cli_refuses_mode_below_full_lab(tmp_path: Path) -> None:
    bundle = _bundle_with_finding(tmp_path)
    work = _repo(tmp_path)
    out = _cli(tmp_path, "--bundle", str(bundle), "--finding", "F-001",
               "--worktree", str(work), "--destructive")
    assert out.returncode != 0
    assert "full_lab" in (out.stdout + out.stderr)
    assert "shell=False" not in (work / "app.py").read_text()

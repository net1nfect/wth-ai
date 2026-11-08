"""Terapkan patch di worktree terpisah — gerbang berlapis, tanpa `git push`.

Empat gerbang, semuanya wajib: mode `full_lab`, flag `--destructive`, path
patch di dalam worktree, dan retest yang harus lulus. Patch yang menggagalkan
retest dibatalkan (`git apply -R`) supaya worktree tidak tertinggal setengah
jadi. Tidak ada satu pun argv di modul ini yang berisi `push`; patch selalu
menjadi pekerjaan operator untuk direview dan di-commit sendiri.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from wth_ai.audit import audit_log
from wth_ai.autofix.planner import Patch

TIMEOUT_S = 120

@dataclass
class ApplyResult:
    ok: bool
    error: str = ""
    applied: bool = False
    retest_output: str = ""
    commands: list[list[str]] = field(default_factory=list)

def _git(worktree: Path, args: list[str], *, stdin_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=worktree,
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
    )

def apply_patch(
    patch: Patch,
    worktree: Path,
    *,
    mode: str,
    destructive: bool,
    retest_argv: list[str] | None = None,
) -> ApplyResult:
    """Terapkan `patch` di `worktree`. Tidak pernah melempar untuk penolakan."""
    commands: list[list[str]] = []
    if mode != "full_lab":
        return ApplyResult(False, error=f"autofix butuh mode full_lab, bukan {mode!r}",
                           commands=commands)
    if not destructive:
        return ApplyResult(False, error="autofix butuh --destructive di level proses",
                           commands=commands)

    root = Path(worktree).resolve()
    target = (root / patch.path).resolve()
    if not target.is_relative_to(root):
        # `git apply` sendiri bisa menolak, tapi cek eksplisit memberi pesan
        # yang bisa dibaca operator — dan tidak bergantung pada perilaku git.
        return ApplyResult(False, error=f"path patch di luar worktree: {patch.path}",
                           commands=commands)

    check = _git(root, ["apply", "--check", "-"], stdin_text=patch.diff)
    commands.append(["git", "apply", "--check", "-"])
    if check.returncode != 0:
        return ApplyResult(False, error=f"patch tidak bisa diterapkan: {check.stderr.strip()}",
                           commands=commands)

    applied = _git(root, ["apply", "-"], stdin_text=patch.diff)
    commands.append(["git", "apply", "-"])
    if applied.returncode != 0:
        return ApplyResult(False, error=f"git apply gagal: {applied.stderr.strip()}",
                           commands=commands)

    retest_output = ""
    if retest_argv:
        commands.append(list(retest_argv))
        retest = subprocess.run(
            retest_argv, cwd=root, capture_output=True, text=True,
            timeout=TIMEOUT_S, check=False,
        )
        retest_output = (retest.stdout or "") + (retest.stderr or "")
        if retest.returncode != 0:
            revert = _git(root, ["apply", "-R", "-"], stdin_text=patch.diff)
            commands.append(["git", "apply", "-R", "-"])
            reason = "retest gagal" if revert.returncode == 0 else "retest gagal dan revert gagal"
            audit_log("autofix_reverted", finding=patch.finding_id, path=patch.path,
                      reason=reason)
            return ApplyResult(False, error=f"{reason} (exit {retest.returncode})",
                               applied=False, retest_output=retest_output,
                               commands=commands)

    audit_log("autofix_applied", finding=patch.finding_id, path=patch.path,
              retested=bool(retest_argv))
    return ApplyResult(True, applied=True, retest_output=retest_output, commands=commands)

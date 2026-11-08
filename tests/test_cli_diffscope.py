"""CLI: --scope-mode diff memakai git di direktori pemanggil, bukan root repo."""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=True)


def _repo(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.test")
    _git(repo, "config", "user.name", "t")
    (repo / "a.py").write_text("a = 1\n")
    (repo / "b.py").write_text("b = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "checkout", "-q", "-b", "pr")
    (repo / "a.py").write_text("a = 2\n")
    _git(repo, "commit", "-q", "-am", "ubah a")
    return repo


def _scan(repo, tmp_path, *extra):
    env = {**os.environ, "WTH_HOME": str(tmp_path / "home"),
           "WTH_SKIP_SETUP": "1", "WTH_PLAIN": "1"}
    return subprocess.run(
        [sys.executable, str(REPO / "wth-ai-main.py"), "scan", "--offline-demo",
         "--out", "out", *extra],
        cwd=repo, capture_output=True, text=True, timeout=180, env=env)


def test_diff_scope_records_changed_files(tmp_path):
    repo = _repo(tmp_path)
    proc = _scan(repo, tmp_path, "--scope-mode", "diff", "--diff-base", "main")
    assert proc.returncode == 0, proc.stderr
    meta = json.loads((repo / "out" / "run.json").read_text())
    assert meta["scope_mode"] == "diff"
    assert meta["diff_base"] == "main"
    assert meta["changed_files"] == ["a.py"]


def test_diff_scope_filters_targets(tmp_path):
    repo = _repo(tmp_path)
    proc = _scan(repo, tmp_path, "--scope-mode", "diff", "--diff-base", "main",
                 "--target", "a.py", "--target", "b.py")
    assert proc.returncode == 0, proc.stderr
    meta = json.loads((repo / "out" / "run.json").read_text())
    assert meta["targets"] == ["a.py"]


def test_missing_diff_base_fails_loudly(tmp_path):
    repo = _repo(tmp_path)
    proc = _scan(repo, tmp_path, "--scope-mode", "diff",
                 "--diff-base", "tidak-ada-cabang")
    assert proc.returncode != 0
    assert "git diff gagal" in (proc.stdout + proc.stderr)
    assert not (repo / "out" / "run.json").exists()


def test_full_scope_is_default(tmp_path):
    repo = _repo(tmp_path)
    proc = _scan(repo, tmp_path, "--target", "b.py")
    assert proc.returncode == 0, proc.stderr
    meta = json.loads((repo / "out" / "run.json").read_text())
    assert meta["scope_mode"] == "full"
    assert meta["targets"] == ["b.py"]
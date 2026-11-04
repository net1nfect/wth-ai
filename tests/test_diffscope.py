"""Diff-scope: hanya file yang berubah pada PR yang jadi target."""

import subprocess

import pytest

from wth_ai.redteam.diffscope import DiffScopeError, changed_files, select


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=True)


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.test")
    _git(repo, "config", "user.name", "t")
    (repo / "a.py").write_text("a = 1\n")
    (repo / "b.py").write_text("b = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def test_changed_file_is_detected(tmp_path):
    repo = _repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "pr")
    (repo / "a.py").write_text("a = 2\n")
    _git(repo, "commit", "-q", "-am", "ubah a")
    files = changed_files(repo, "main")
    assert files == ["a.py"]


def test_unchanged_file_is_not_reported(tmp_path):
    repo = _repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "pr")
    (repo / "a.py").write_text("a = 3\n")
    _git(repo, "commit", "-q", "-am", "ubah a")
    assert "b.py" not in changed_files(repo, "main")


def test_missing_base_raises(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(DiffScopeError):
        changed_files(repo, "tidak-ada-cabang-ini")


def test_empty_base_raises(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(DiffScopeError):
        changed_files(repo, "")


def test_select_keeps_only_changed_targets():
    picked = select(["a.py", "web/app.js"], ["a.py", "b.py", "web/app.js"],
                    suffixes=(".py",))
    assert picked == ["a.py"]


def test_select_matches_url_targets_by_name():
    picked = select(["fixtures/orders.json"], ["http://lab/orders.json", "x"])
    assert picked == ["http://lab/orders.json"]
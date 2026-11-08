"""P2-7: riwayat CLI milik WTH_HOME, bukan pohon kerja.

`WTHRepl` dulu memakai default relatif `data/cli_history`, jadi setiap sesi
menulis riwayat operator ke dalam repo — dan karena `data/cli_history` ikut
ter-track, isinya masuk git. `.coverage` juga tidak di-ignore.
"""

from __future__ import annotations

from pathlib import Path

from wth_ai.cli.repl import WTHRepl

REPO = Path(__file__).resolve().parents[1]


def test_default_history_lives_in_wth_home(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    repl = WTHRepl(lambda t: t)
    path = Path(repl.session.history.filename)
    assert path == tmp_path / "home" / "data" / "cli_history"
    assert str(REPO) not in str(path)


def test_explicit_history_path_is_still_honoured(tmp_path):
    explicit = tmp_path / "custom_history"
    repl = WTHRepl(lambda t: t, history_path=str(explicit))
    assert Path(repl.session.history.filename) == explicit


def test_repo_does_not_track_operator_history_or_coverage_output():
    gi = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "cli_history" in gi
    assert ".coverage" in gi

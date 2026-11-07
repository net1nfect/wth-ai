"""Runner opengrep: sandbox tanpa jaringan, argumen argv-list."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from wth_ai.sast.opengrep import SastUnavailable, docker_available, run_opengrep


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "app.py").write_text("query = 'SELECT * FROM t WHERE id=' + uid\n", encoding="utf-8")
    return root


def _rules(tmp_path: Path) -> Path:
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "owasp.yaml").write_text("rules: []\n", encoding="utf-8")
    return rules


def _fake_run(captured: dict[str, object]):
    payload = {
        "results": [
            {
                "check_id": "owasp.sqli",
                "path": "app.py",
                "start": {"line": 12},
                "extra": {"severity": "ERROR", "message": "query string tergabung", "lines": "x"},
            }
        ]
    }

    def run(argv, **_kwargs):
        captured["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    return run


def test_run_opengrep_parses_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = _project(tmp_path)
    rules = _rules(tmp_path)
    captured: dict[str, object] = {}
    monkeypatch.setattr("wth_ai.sast.opengrep.subprocess.run", _fake_run(captured))
    monkeypatch.setattr("wth_ai.sast.opengrep.docker_available", lambda: True)

    findings = run_opengrep(target, rules_dir=rules)

    assert findings[0]["rule_id"] == "owasp.sqli"
    assert findings[0]["path"] == "app.py"
    assert findings[0]["line"] == 12
    assert findings[0]["severity"] == "ERROR"


def test_run_opengrep_uses_no_network_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    rules = _rules(tmp_path)
    captured: dict[str, object] = {}
    monkeypatch.setattr("wth_ai.sast.opengrep.subprocess.run", _fake_run(captured))
    monkeypatch.setattr("wth_ai.sast.opengrep.docker_available", lambda: True)

    run_opengrep(target, rules_dir=rules)

    argv = list(captured["argv"])  # type: ignore[arg-type]
    assert argv[:3] == ["docker", "run", "--rm"]
    assert argv[argv.index("--network") + 1] == "none"
    assert f"{target}:/src:ro" in argv
    assert f"{rules}:/rules:ro" in argv
    assert argv[-1] == "/src"


def test_run_opengrep_without_docker_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    rules = _rules(tmp_path)
    monkeypatch.setattr("wth_ai.sast.opengrep.docker_available", lambda: False)

    with pytest.raises(SastUnavailable, match="docker"):
        run_opengrep(target, rules_dir=rules)


def test_run_opengrep_rejects_bad_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    rules = _rules(tmp_path)
    monkeypatch.setattr("wth_ai.sast.opengrep.docker_available", lambda: True)

    def run(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 2, "bukan json", "opengrep error")

    monkeypatch.setattr("wth_ai.sast.opengrep.subprocess.run", run)

    with pytest.raises(SastUnavailable, match="opengrep"):
        run_opengrep(target, rules_dir=rules)


def test_docker_available_reflects_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("wth_ai.sast.opengrep.shutil.which", lambda _name: None)
    assert docker_available() is False
    monkeypatch.setattr("wth_ai.sast.opengrep.shutil.which", lambda _name: "/usr/bin/docker")
    assert docker_available() is True

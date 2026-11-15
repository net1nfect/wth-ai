"""Solidity audit tool: deterministic Foundry/Slither wrapper.

The wrapper must not interpolate model-controlled text into a shell. It builds
argv, joins it with shlex, and hands it to the existing run_shell bridge.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from wth_ai import solidity
from wth_ai.terminal.models import TerminalResult


class _FakeTerminal:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def dispatch_tool(self, name, args, *, actor, approved=False):
        self.calls.append((name, dict(args)))
        return TerminalResult(
            transcript_id="TX-SOL",
            actor=actor,
            purpose=str(args.get("purpose", "")),
            command=str(args.get("command", "")),
            stdout="compiled ok",
            stderr="",
            exit_code=0,
        )


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project with space"
    root.mkdir()
    return root


def test_registry_exposes_solidity_audit_to_supervisor_and_executor():
    from wth_ai.agents.tool_registry import tools_for_actor

    for actor in ("supervisor", "planner", "executor"):
        names = {d["function"]["name"] for d in tools_for_actor(actor)}
        assert "solidity_audit" in names, actor


def test_build_test_command_is_argv_quoted(tmp_path, monkeypatch):
    root = _project(tmp_path)
    monkeypatch.setattr(solidity, "resolve_binary", lambda name: f"/opt/{name}")

    cmd = solidity.build_command("test", root, match_path="test/Pool.t.sol")

    assert cmd.argv == (
        "/opt/forge", "test", "--root", str(root), "-vv",
        "--match-path", "test/Pool.t.sol",
    )
    assert "'" in cmd.shell_command or '"' in cmd.shell_command
    assert str(root) in cmd.shell_command


def test_dispatch_maps_to_run_shell_without_shell_interpolation(tmp_path, monkeypatch):
    root = _project(tmp_path)
    monkeypatch.setattr(solidity, "resolve_binary", lambda name: f"/opt/{name}")
    terminal = _FakeTerminal()
    ctx = SimpleNamespace(config=None, terminal=terminal)

    out = solidity.dispatch_solidity_audit(
        ctx, {"action": "build", "path": str(root)}, approved=True,
    )

    assert "compiled ok" in out
    tool, payload = terminal.calls[-1]
    assert tool == "run_shell"
    assert payload["command"].startswith("/opt/forge build --root")
    assert "project with space" in payload["command"]


def test_doctor_reports_missing_binaries_without_running_shell(monkeypatch):
    monkeypatch.setattr(solidity, "resolve_binary", lambda name: None)
    terminal = _FakeTerminal()
    ctx = SimpleNamespace(config=None, terminal=terminal)

    out = solidity.dispatch_solidity_audit(ctx, {"action": "doctor"}, approved=True)

    assert "forge: NOT FOUND" in out
    assert "slither: NOT FOUND" in out
    assert terminal.calls == []


def test_invalid_project_path_is_rejected(tmp_path):
    terminal = _FakeTerminal()
    ctx = SimpleNamespace(config=None, terminal=terminal)

    out = solidity.dispatch_solidity_audit(
        ctx, {"action": "build", "path": str(tmp_path / "missing")}, approved=True,
    )

    assert "SOLIDITY_AUDIT_ERROR" in out
    assert terminal.calls == []


def test_slither_uses_project_path_as_argument(tmp_path, monkeypatch):
    root = _project(tmp_path)
    monkeypatch.setattr(solidity, "resolve_binary", lambda name: f"/opt/{name}")
    cmd = solidity.build_command("slither", root)
    assert cmd.argv == ("/opt/slither", str(root))


def test_wth_home_tool_bin_is_a_fallback(tmp_path, monkeypatch):
    tool = tmp_path / "tools" / "bin" / "wth-fake-solidity-tool"
    tool.parent.mkdir(parents=True)
    tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    tool.chmod(0o755)
    monkeypatch.setenv("WTH_HOME", str(tmp_path))

    assert solidity.resolve_binary("wth-fake-solidity-tool") == str(tool)

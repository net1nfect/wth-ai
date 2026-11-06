"""Tests for permission presets (AI-CLI-style modes) and computer use."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest

from wth_ai.computer_use import INPUT_ACTIONS, READ_ONLY_ACTIONS, ComputerUse
from wth_ai.models import PermissionMode
from wth_ai.permission_presets import ALIASES, PRESETS, apply_preset, describe_all, resolve_preset


@pytest.fixture(autouse=True)
def _isolate_safety_env(monkeypatch):
    """`apply_preset` writes os.environ directly.

    monkeypatch only restores variables it has SEEN, so a preset applied by a
    test leaked `WTH_UNRESTRICTED=1` into the rest of the session -- which made
    `_eligible("masscan", ...)` return True in tests/test_recon_platform.py and
    test_recon_engine.py, i.e. looked like a product regression. Registering the
    three dimensions here makes every test in this file restore them.
    """
    monkeypatch.setenv("WTH_UNRESTRICTED", "0")
    monkeypatch.setenv("WTH_AUTO_APPROVE", "0")
    monkeypatch.setenv("WTH_REQUIRE_AUTHORIZED", "1")
    yield


# --------------------------------------------------------------------------- #
# presets
# --------------------------------------------------------------------------- #
def test_every_preset_resolves_to_itself():
    for name in PRESETS:
        assert resolve_preset(name).name == name


def test_unknown_mode_lists_the_valid_ones():
    with pytest.raises(ValueError) as excinfo:
        resolve_preset("godmode")
    assert "plan" in str(excinfo.value) and "yolo" in str(excinfo.value)


@pytest.mark.parametrize(
    "spelling,expected",
    [
        ("unrestricted", "yolo"), ("danger", "yolo"), ("full-lab", "full-lab"),
        ("full_lab", "full-lab"),
        ("standard", "ask"), ("default", "ask"), ("safe", "ask"),
        ("elevated", "auto"), ("auto-accept", "auto"), ("autopilot", "auto"),
        ("read_only", "read-only"), ("ro", "read-only"), ("dry-run", "plan"),
    ],
)
def test_old_spellings_still_resolve(spelling, expected):
    """Nothing that worked before may break: the raw PermissionMode values and
    the old synonyms all map onto a preset."""
    assert resolve_preset(spelling).name == expected


def test_yolo_sets_capability_and_approval_together(monkeypatch):
    monkeypatch.setenv("WTH_UNRESTRICTED", "0")
    monkeypatch.setenv("WTH_AUTO_APPROVE", "0")
    from wth_ai.engagement.mode import auto_approve_enabled, is_unrestricted

    assert not is_unrestricted() and not auto_approve_enabled()
    apply_preset("yolo")
    assert is_unrestricted(), "capability ceiling tidak dinaikkan"
    assert auto_approve_enabled(), "approval tidak dimatikan"


def test_ask_keeps_approvals_on(monkeypatch):
    monkeypatch.setenv("WTH_UNRESTRICTED", "0")
    monkeypatch.setenv("WTH_AUTO_APPROVE", "0")
    from wth_ai.engagement.mode import auto_approve_enabled, is_unrestricted

    apply_preset("ask")
    assert not is_unrestricted()
    assert not auto_approve_enabled()


def test_no_preset_ever_lowers_scope_authorization(monkeypatch):
    """The invariant that keeps `yolo` from becoming 'hit any host'.

    Target authorization is not friction about how to test -- it is the record
    of what the operator said they may test. If this test ever fails, the
    distinction has been lost.
    """
    monkeypatch.setenv("WTH_REQUIRE_AUTHORIZED", "1")
    from wth_ai.engagement.mode import scope_authorization_required

    for preset in PRESETS.values():
        assert preset.scope_authorization_required is True, preset.name
    apply_preset("yolo")
    assert scope_authorization_required() is True


def test_apply_preset_sets_the_permission_manager_too():
    from wth_ai.permission_manager import PermissionManager

    mgr = PermissionManager(PermissionMode.READ_ONLY_RECON)
    apply_preset("yolo", permission_mgr=mgr)
    assert mgr.mode is PermissionMode.UNRESTRICTED


def test_describe_all_lists_every_preset_and_alias():
    text = describe_all()
    for name in PRESETS:
        assert name in text
    assert "Alias:" in text


# --------------------------------------------------------------------------- #
# computer use — detection and honest failure
# --------------------------------------------------------------------------- #
def test_detect_reports_a_working_session(monkeypatch):
    monkeypatch.setattr(ComputerUse, "_session_info", lambda self: {
        "display": "wayland:/run/user/1000/wayland-0", "raw_display": "",
        "wayland_socket": "/run/user/1000/wayland-0", "session_type": "wayland",
        "input_backend": "ydotool", "screenshot_backend": "grim", "has_wayland_socket": True,
    })
    result = ComputerUse().detect()
    assert result.ok
    assert "ydotool" in result.detail and "grim" in result.detail


def test_detect_names_what_is_missing(monkeypatch):
    monkeypatch.setattr(ComputerUse, "_session_info", lambda self: {
        "display": "", "raw_display": "", "wayland_socket": "", "session_type": "unknown",
        "input_backend": "", "screenshot_backend": "", "has_wayland_socket": False,
    })
    result = ComputerUse().detect()
    assert not result.ok
    assert "display" in result.detail
    assert "screenshot" in result.detail


def test_screenshot_without_a_backend_fails_cleanly(monkeypatch):
    """No candidate backend -> a clean, specific failure.

    `_screenshot_candidates` must be stubbed here: it reads the REAL machine's
    PATH, so without this the test walked the live desktop's backends and
    actually captured the operator's screen during a unit test run.
    """
    monkeypatch.setattr(ComputerUse, "_screenshot_candidates", lambda self: [])
    result = ComputerUse().screenshot()
    assert not result.ok
    assert "backend" in result.detail


def test_screenshot_verifies_the_file_not_the_exit_code(monkeypatch, tmp_path):
    """Exit 0 with no file on disk is a FAILURE.

    The whole point of this module is that the agent can trust `ok=True` without
    re-checking; trusting the exit code alone would let it report a screenshot
    that does not exist. The method now walks EVERY candidate backend, so the
    contract is "not ok, and the detail names each attempt".
    """
    monkeypatch.setattr(ComputerUse, "_session_info", lambda self: {
        "display": "wayland:/run/user/1000/wayland-0", "raw_display": "",
        "wayland_socket": "/run/user/1000/wayland-0", "session_type": "wayland",
        "input_backend": "ydotool", "screenshot_backend": "grim", "has_wayland_socket": True,
    })
    monkeypatch.setattr(ComputerUse, "_run", lambda self, argv: (True, ""))
    monkeypatch.setattr(ComputerUse, "_screenshot_candidates", lambda self: ["grim", "scrot"])

    result = ComputerUse().screenshot(tmp_path / "nope.png")
    assert not result.ok, "exit 0 tanpa file harus dianggap gagal"
    assert "semua backend" in result.detail
    assert "grim" in result.detail and "scrot" in result.detail


def test_screenshot_succeeds_when_the_file_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(ComputerUse, "_session_info", lambda self: {
        "display": "wayland:/run/user/1000/wayland-0", "raw_display": "",
        "wayland_socket": "/run/user/1000/wayland-0", "session_type": "wayland",
        "input_backend": "ydotool", "screenshot_backend": "grim", "has_wayland_socket": True,
    })
    target = tmp_path / "shot.png"

    def _fake_run(self, argv):
        target.write_bytes(b"\x89PNG" + b"x" * 64)
        return True, ""

    monkeypatch.setattr(ComputerUse, "_run", _fake_run)
    result = ComputerUse().screenshot(target)
    assert result.ok
    assert result.data["bytes"] > 0


def test_unknown_action_fails_loudly_instead_of_pretending():
    result = ComputerUse().run_action("teleport")
    assert not result.ok
    assert "tidak dikenal" in result.detail


def test_run_action_reports_bad_arguments():
    result = ComputerUse().run_action("click", x="abc", y=1)
    assert not result.ok
    assert "argumen" in result.detail


def test_typed_text_is_not_echoed_in_full(monkeypatch):
    """`type` results get shown and logged; typing a pasted credential must not
    write that credential into the transcript."""
    monkeypatch.setattr(ComputerUse, "_session_info", lambda self: {
        "display": ":0", "raw_display": ":0", "wayland_socket": "", "session_type": "x11",
        "input_backend": "xdotool", "screenshot_backend": "scrot", "has_wayland_socket": False,
    })
    monkeypatch.setattr(ComputerUse, "_run", lambda self, argv: (True, ""))
    secret = "hunter2-SUPERSECRETVALUE"
    result = ComputerUse().type_text(secret)
    assert result.ok
    assert secret not in result.detail
    assert "karakter" in result.detail


def test_action_sets_do_not_overlap():
    assert not (INPUT_ACTIONS & READ_ONLY_ACTIONS)
    assert "screenshot" in READ_ONLY_ACTIONS
    assert "click" in INPUT_ACTIONS


# --------------------------------------------------------------------------- #
# dispatch gating
# --------------------------------------------------------------------------- #
def _ctx(mode: PermissionMode, *, action_memory=None) -> Any:
    from wth_ai.permission_manager import PermissionManager

    return SimpleNamespace(
        permission_mgr=PermissionManager(mode),
        action_memory=action_memory,
        config=None,
        # `dispatch_supervisor_tool` narrates progress and persists state
        # through the context; the real SupervisorContext always has these, so
        # they are no-ops here.
        notify_tool_start=lambda name, args: None,
        notify_tool_end=lambda name, result, ms: None,
        persist_fn=lambda *a, **k: None,
    )


def test_observation_is_allowed_in_read_only(monkeypatch):
    from wth_ai.agents.supervisor_dispatch import _dispatch_computer_use

    monkeypatch.setattr(ComputerUse, "_session_info", lambda self: {
        "display": ":0", "raw_display": ":0", "wayland_socket": "", "session_type": "x11",
        "input_backend": "xdotool", "screenshot_backend": "scrot", "has_wayland_socket": False,
    })
    out = _dispatch_computer_use(_ctx(PermissionMode.READ_ONLY_RECON), {"action": "detect"}, approved=False)
    assert "✓" in out


def test_input_is_refused_in_read_only():
    from wth_ai.agents.supervisor_dispatch import _dispatch_computer_use

    out = _dispatch_computer_use(
        _ctx(PermissionMode.READ_ONLY_RECON), {"action": "click", "x": 1, "y": 2}, approved=True
    )
    assert "DITOLAK" in out


def test_input_requires_approval_in_ask_mode(monkeypatch):
    """`ask` = the human confirms. Without `approved` and with auto-approve off,
    input must not run -- including when everything else would succeed."""
    monkeypatch.setenv("WTH_AUTO_APPROVE", "0")
    from wth_ai.agents.supervisor_dispatch import _dispatch_computer_use

    out = _dispatch_computer_use(
        _ctx(PermissionMode.STANDARD_TESTING), {"action": "click", "x": 1, "y": 2}, approved=False
    )
    assert "BUTUH APPROVAL" in out


def test_input_runs_when_approved(monkeypatch):
    monkeypatch.setenv("WTH_AUTO_APPROVE", "0")
    monkeypatch.setattr(ComputerUse, "_session_info", lambda self: {
        "display": ":0", "raw_display": ":0", "wayland_socket": "", "session_type": "x11",
        "input_backend": "xdotool", "screenshot_backend": "scrot", "has_wayland_socket": False,
    })
    monkeypatch.setattr(ComputerUse, "_run", lambda self, argv: (True, ""))
    from wth_ai.agents.supervisor_dispatch import _dispatch_computer_use

    out = _dispatch_computer_use(
        _ctx(PermissionMode.STANDARD_TESTING), {"action": "click", "x": 5, "y": 6}, approved=True
    )
    assert out.startswith("✓")


def test_input_runs_without_approval_when_auto_approve_is_on(monkeypatch):
    monkeypatch.setenv("WTH_AUTO_APPROVE", "1")
    monkeypatch.setattr(ComputerUse, "_session_info", lambda self: {
        "display": ":0", "raw_display": ":0", "wayland_socket": "", "session_type": "x11",
        "input_backend": "xdotool", "screenshot_backend": "scrot", "has_wayland_socket": False,
    })
    monkeypatch.setattr(ComputerUse, "_run", lambda self, argv: (True, ""))
    from wth_ai.agents.supervisor_dispatch import _dispatch_computer_use

    out = _dispatch_computer_use(
        _ctx(PermissionMode.ELEVATED_APPROVAL), {"action": "click", "x": 5, "y": 6}, approved=False
    )
    assert out.startswith("✓")


def test_permission_set_tool_applies_a_preset(monkeypatch):
    monkeypatch.setenv("WTH_UNRESTRICTED", "0")
    monkeypatch.setenv("WTH_AUTO_APPROVE", "0")
    from wth_ai.agents.supervisor_dispatch import dispatch_supervisor_tool
    from wth_ai.engagement.mode import auto_approve_enabled, is_unrestricted

    ctx = _ctx(PermissionMode.READ_ONLY_RECON)
    ctx.state = SimpleNamespace(permission_mode=PermissionMode.READ_ONLY_RECON)

    out = dispatch_supervisor_tool(ctx, "permission_set", {"mode": "yolo"}, approved=True)
    assert "yolo" in out
    assert ctx.permission_mgr.mode is PermissionMode.UNRESTRICTED
    assert is_unrestricted() and auto_approve_enabled()


def test_permission_set_tool_rejects_junk(monkeypatch):
    from wth_ai.agents.supervisor_dispatch import dispatch_supervisor_tool

    ctx = _ctx(PermissionMode.READ_ONLY_RECON)
    ctx.state = SimpleNamespace(permission_mode=PermissionMode.READ_ONLY_RECON)
    out = dispatch_supervisor_tool(ctx, "permission_set", {"mode": "godmode"}, approved=True)
    assert "Invalid mode" in out


def test_type_text_never_echoes_prefix():
    from wth_ai.computer_use.session import ComputerUse as Session

    s = Session()
    s._session_info = lambda: {"input_backend": "xdotool"}
    s._run = lambda argv: (True, "ok")
    secret = "S3cr3t-Passw0rd!"
    result = s.type_text(secret)
    assert result.ok
    assert secret[:12] not in result.detail
    assert secret[:12] not in str(result.data)
    assert result.data.get("chars") == len(secret)


def _fake_proc(tmp_path):
    pid_dir = tmp_path / "1234"
    pid_dir.mkdir()
    (pid_dir / "comm").write_text("gnome-shell")
    (pid_dir / "environ").write_bytes(b"DISPLAY=:99\x00XAUTHORITY=/tmp/xauth")
    return pid_dir


def test_discover_x11_skips_other_uid(tmp_path, monkeypatch):
    from wth_ai.computer_use import session as cu

    _fake_proc(tmp_path)
    monkeypatch.setattr(cu, "_PROC", tmp_path)
    monkeypatch.setattr(cu.os, "getuid", lambda: 1000)
    monkeypatch.setattr(cu, "_proc_uid", lambda path: 4242)
    monkeypatch.setattr(cu, "_runtime_dir", lambda: tmp_path / "empty")
    found = ComputerUse()._discover_x11()
    assert "DISPLAY" not in found
    assert ":99" not in str(found)


def test_discover_x11_reads_own_uid(tmp_path, monkeypatch):
    from wth_ai.computer_use import session as cu

    _fake_proc(tmp_path)
    monkeypatch.setattr(cu, "_PROC", tmp_path)
    monkeypatch.setattr(cu.os, "getuid", lambda: 1000)
    monkeypatch.setattr(cu, "_proc_uid", lambda path: 1000)
    monkeypatch.setattr(cu, "_runtime_dir", lambda: tmp_path)
    assert ComputerUse()._discover_x11()["DISPLAY"] == ":99"

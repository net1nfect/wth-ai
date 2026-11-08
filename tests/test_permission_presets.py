"""P0-3: presets must not silently upgrade `full_lab` into `yolo`.

`full_lab` used to resolve to the `yolo` preset, so the spelling that sounds
like "local lab" raised the capability ceiling and turned approvals off. The
gate is the alias table: every spelling maps to (permission, unrestricted,
auto_approve) explicitly.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from wth_ai.config import WTHConfig
from wth_ai.engagement.mode import auto_approve_enabled, is_unrestricted
from wth_ai.models import PermissionMode


@pytest.mark.parametrize("spelling,permission,unrestricted,auto_approve", [
    ("plan", PermissionMode.READ_ONLY_RECON, False, False),
    ("read-only", PermissionMode.READ_ONLY_RECON, False, False),
    ("read_only", PermissionMode.READ_ONLY_RECON, False, False),
    ("ask", PermissionMode.STANDARD_TESTING, False, False),
    ("standard", PermissionMode.STANDARD_TESTING, False, False),
    ("auto", PermissionMode.ELEVATED_APPROVAL, False, True),
    ("elevated", PermissionMode.ELEVATED_APPROVAL, False, True),
    ("full_lab", PermissionMode.FULL_LAB, False, False),
    ("full-lab", PermissionMode.FULL_LAB, False, False),
    ("yolo", PermissionMode.UNRESTRICTED, True, True),
])
def test_preset_table(spelling, permission, unrestricted, auto_approve):
    from wth_ai.permission_presets import resolve_preset

    preset = resolve_preset(spelling)
    assert preset.permission is permission
    assert preset.unrestricted is unrestricted
    assert preset.auto_approve is auto_approve


def test_only_yolo_raises_the_capability_ceiling():
    from wth_ai.permission_presets import PRESETS

    assert [p.name for p in PRESETS.values() if p.unrestricted] == ["yolo"]


def test_presets_have_no_duplicate_keys():
    """`"full-lab"` was written twice in the dict literal; the second silently
    won, so editing one of them changed nothing. A dict cannot report this
    after import, so the source is parsed."""
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    tree = ast.parse((root / "wth_ai" / "permission_presets.py").read_text())
    literal = next(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and isinstance(node.value, ast.Dict)
        and any(
            getattr(t, "id", "") == "PRESETS"
            for t in (node.targets if isinstance(node, ast.Assign) else [node.target])
        )
    )
    keys = [k.value for k in literal.keys]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    assert not dupes, f"key duplikat di PRESETS: {dupes}"


def test_full_lab_preset_does_not_leave_the_process_unrestricted():
    """`unrestricted=False` harus menurunkan env, bukan hanya tidak menaikkannya."""
    from wth_ai.permission_presets import apply_preset

    apply_preset("yolo")
    assert is_unrestricted()
    apply_preset("full-lab")
    assert not is_unrestricted()
    assert not auto_approve_enabled()


def test_default_mode_comes_from_config():
    """Satu sumber default: nilai config menentukan mode awal sesi."""
    from wth_ai.permission_presets import default_mode

    assert default_mode(SimpleNamespace(default_permission="full-lab")) is PermissionMode.FULL_LAB
    assert default_mode(SimpleNamespace(default_permission="read_only")) is PermissionMode.READ_ONLY_RECON
    # Nilai tak dikenal tidak boleh meledak di jalur start-up.
    assert default_mode(SimpleNamespace(default_permission="godmode")) is PermissionMode.READ_ONLY_RECON


def test_safety_config_defaults_to_read_only():
    from wth_ai.config import SafetyConfig

    assert SafetyConfig().default_permission == "read_only"


def test_old_config_with_full_lab_is_migrated(tmp_path, monkeypatch):
    """`full_lab` lama != `yolo` lagi; ia menjadi preset `full-lab`.

    `USER_CONFIG_PATH` di-patch supaya berkas config operator di mesin ini
    tidak ikut di-merge ke hasil test.
    """
    import wth_ai.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "USER_CONFIG_PATH", tmp_path / "user.yaml")
    path = tmp_path / "wth_ai_config.yaml"
    path.write_text("default_permission: full_lab\n", encoding="utf-8")
    cfg = WTHConfig.load(path)
    assert cfg.default_permission == "full-lab"
    assert cfg.safety.default_permission == "full-lab"
    # Idempoten: memuat dua kali tidak mengubah apa pun lagi.
    assert WTHConfig.load(path).default_permission == "full-lab"


def test_unknown_permission_name_falls_back_to_read_only(tmp_path, monkeypatch):
    import wth_ai.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "USER_CONFIG_PATH", tmp_path / "user.yaml")
    path = tmp_path / "wth_ai_config.yaml"
    path.write_text("default_permission: godmode\n", encoding="utf-8")
    assert WTHConfig.load(path).default_permission == "read_only"
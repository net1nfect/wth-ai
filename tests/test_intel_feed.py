"""Intel feed: egress, sanitasi data eksternal, applicability (audit 2026-11-06)."""

from __future__ import annotations

import subprocess

from wth_ai.config import IntelConfig
from wth_ai.intel.feed import IntelFeed


def test_check_nuclei_templates_never_spawns_subprocess(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))

    def boom(*args, **kwargs):
        raise AssertionError("subprocess.run tidak boleh dipanggil")

    monkeypatch.setattr(subprocess, "run", boom)
    out = IntelFeed().check_nuclei_templates()
    assert out["status"] == "disabled"
    assert "wth tools install" in out["reason"]


def test_nuclei_templates_check_defaults_off():
    assert IntelConfig().nuclei_templates_check is False

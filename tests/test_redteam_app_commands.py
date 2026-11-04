"""Perintah TUI: /run digerbangi mode, /scope butuh CIDR, /egress-test jujur."""

import asyncio

import pytest

pytest.importorskip("textual")


def _exercise(tmp_path, monkeypatch, command: str, *, mode: str = "plan",
              cidr: str = "") -> list[str]:
    """Jalankan satu perintah di dalam app yang benar-benar ter-mount."""
    from wth_ai.cli.tui.redteam.app import WthApp

    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    lines: list[str] = []

    async def scenario():
        app = WthApp(demo=False, mode=mode, cidr=cidr)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._log = lambda text: lines.append(text)  # type: ignore[method-assign]
            app._dispatch(command)
            await pilot.pause()

    asyncio.run(scenario())
    return lines


def test_run_exploit_denied_in_plan_mode(tmp_path, monkeypatch):
    lines = _exercise(tmp_path, monkeypatch, "/run exploit", mode="plan")
    assert any("DITOLAK" in line and "exploit" in line for line in lines), lines


def test_run_exploit_allowed_in_auto_mode(tmp_path, monkeypatch):
    lines = _exercise(tmp_path, monkeypatch, "/run exploit", mode="auto")
    assert not any("DITOLAK" in line for line in lines), lines


def test_mutate_requires_destructive_flag(tmp_path, monkeypatch):
    lines = _exercise(tmp_path, monkeypatch, "/run mutate", mode="auto")
    assert any("DITOLAK" in line and "destructive" in line for line in lines), lines


def test_scope_without_cidr_is_refused(tmp_path, monkeypatch):
    lines = _exercise(tmp_path, monkeypatch, "/scope 172.28.0.10")
    assert any("ditolak" in line.lower() for line in lines), lines
    assert not (tmp_path / "scope_card.json").exists()


def test_scope_with_cidr_writes_card(tmp_path, monkeypatch):
    lines = _exercise(tmp_path, monkeypatch,
                      "/scope 172.28.0.10 --cidr 172.28.0.0/16")
    assert (tmp_path / "scope_card.json").is_file()
    assert any("scope diset" in line for line in lines), lines


def test_egress_test_denies_public_ip(tmp_path, monkeypatch):
    lines = _exercise(tmp_path, monkeypatch, "/egress-test",
                      mode="plan", cidr="172.28.0.0/16")
    assert any("EGRESS DENIED" in line for line in lines), lines


def test_unknown_capability_is_reported(tmp_path, monkeypatch):
    lines = _exercise(tmp_path, monkeypatch, "/run teleport")
    assert any("tidak dikenal" in line for line in lines), lines
"""P1-1: handler_autopilot wajib otorisasi dari scope card, bukan hardcode.

Temuan audit 2026-11-06 (P1-1): `handler_autopilot` hanya cek
`meta.get("authorized")` lalu paksa-authorize scope (`set_scope(authorized=True,
in_scope=[target])`, `state.scope.authorized=True`) tanpa memuat `scope_card.json`.
Target mana pun yang didaftarkan di cron auto-probe tanpa scope card valid —
melanggar invariant P0-1 (authorization harus dari artifact scope card, bukan
metadata job). Perbaikan: handler wajib load/validasi scope card seperti
`handler_recon`; tanpa kartu valid, job berhenti dengan status failed/blocked.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")


class _Job:
    target = "example.com"
    job_id = "J1"
    metadata = {"authorized": True, "steps": 1, "cycles": 1}


class _Ctx:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def progress(self, *args):
        pass

    def log(self, line):
        self.lines.append(str(line))

    def should_stop(self) -> bool:
        return False


def test_autopilot_without_scope_card_fails(tmp_path, monkeypatch):
    """Tanpa scope card valid, autopilot harus berhenti SEBELUM bridge dibuat."""
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)

    from wth_ai.jobs.handlers import handler_autopilot

    result = handler_autopilot(_Job(), _Ctx())
    assert result["type"] == "autopilot"
    assert result["status"] in ("failed", "blocked"), result
    assert "scope" in result.get("error", "") + result.get("reason", "")


def test_autopilot_with_non_authorized_scope_card_fails(tmp_path, monkeypatch):
    """Kartu ada tapi authorized=false → tetap ditolak."""
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)

    import json

    # Tulis file kartu langsung: `card.write()` menevalidasi dan menolak
    # authorized=False. Kita butuh kartu yang ADA tapi tidak ber-otorisasi.
    (home / "scope_card.json").write_text(
        json.dumps(
            {"target": "example.com", "domains": ["example.com"], "authorized": False}
        ),
        encoding="utf-8",
    )

    from wth_ai.jobs.handlers import handler_autopilot

    result = handler_autopilot(_Job(), _Ctx())
    assert result["type"] == "autopilot"
    assert result["status"] in ("failed", "blocked"), result


def test_autopilot_with_valid_scope_card_proceeds(tmp_path, monkeypatch):
    """Kartu valid → handler lanjut (membuat bridge)."""
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)

    from wth_ai.scope_card import ScopeCard

    card = ScopeCard(target="example.com", domains=["example.com"], authorized=True)
    card.write(home / "scope_card.json")

    import wth_ai.jobs.handlers as handlers

    captured: dict = {}

    class _Bridge:
        def __init__(self, config, permission, scope, session_id, **kwargs):
            captured["scope"] = scope
            self.scope = scope

        def dispatch_tool(self, name, args, **kwargs):
            raise AssertionError("probe tidak boleh dijalankan di test ini")

    monkeypatch.setattr(handlers, "AgentTerminalBridge", _Bridge, raising=False)
    import wth_ai.terminal.bridge as bridge_mod

    monkeypatch.setattr(bridge_mod, "AgentTerminalBridge", _Bridge)

    import wth_ai.session_store as store_mod

    class _Store:
        def __init__(self, *a, **k):
            pass

        def load_latest(self):
            return None

    monkeypatch.setattr(store_mod, "SessionStore", _Store)

    result = handlers.handler_autopilot(_Job(), _Ctx())
    scope = captured.get("scope")
    assert scope is not None, "handler tidak membuat bridge dengan scope valid"
    ok, reason = scope.is_in_scope("http://example.com/x")
    assert ok, f"scope bridge tidak menerima target kartu: {reason}"
    assert result["type"] == "autopilot"

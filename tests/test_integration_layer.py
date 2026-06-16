"""Fase 3 tests: event bus, integration manager, adapters, approvals, doctor."""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest

from wth_ai.approvals import ApprovalRegistry
from wth_ai.config import TelegramIntegration, WTHConfig
from wth_ai.events import (
    EVENT_TYPES,
    Event,
    EventBus,
    make_event,
)
from wth_ai.integrations.base import command_allowed, resolve_discord_role
from wth_ai.integrations.commands import BotCommandService
from wth_ai.integrations.manager import IntegrationManager
from wth_ai.integrations.telegram import TelegramNotifier
from wth_ai.vault import SecretVault, vault_ref


@contextlib.contextmanager
def temp_home():
    import wth_ai.config as cfg_mod
    from wth_ai.workspace import init_workspace
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        old_home = os.environ.get("WTH_HOME")
        old_cfg = cfg_mod.USER_CONFIG_PATH
        old_env = cfg_mod.USER_ENV_PATH
        os.environ["WTH_HOME"] = str(base)
        cfg_mod.USER_CONFIG_PATH = base / "config" / "user.yaml"
        cfg_mod.USER_ENV_PATH = base / ".env"
        init_workspace(base)
        try:
            yield base
        finally:
            cfg_mod.USER_CONFIG_PATH = old_cfg
            cfg_mod.USER_ENV_PATH = old_env
            if old_home is None:
                os.environ.pop("WTH_HOME", None)
            else:
                os.environ["WTH_HOME"] = old_home


# --- events ---

def test_event_schema_and_defaults():
    ev = make_event("job.started", job_id="J1", source="executor", data={"target": "x"})
    d = ev.to_dict()
    assert set(d) == {"id", "type", "timestamp", "source", "severity", "data", "correlation_id", "job_id"}
    assert d["type"] == "job.started"
    assert d["severity"] == "info"
    assert d["id"].startswith("EV-")
    assert make_event("job.failed").severity == "error"


def test_make_event_rejects_unknown_type():
    with pytest.raises(ValueError):
        make_event("not.a.real.event")
    assert "job.approval_required" in EVENT_TYPES


def test_event_bus_publish_subscribe():
    bus = EventBus()
    received: list[Event] = []
    bus.subscribe(received.append)
    bus.emit("job.started", job_id="J1")
    assert len(received) == 1
    assert received[0].job_id == "J1"
    assert len(bus.history) == 1


def test_event_bus_type_filter():
    bus = EventBus()
    got: list[str] = []
    bus.subscribe(lambda e: got.append(e.type), types=["report.ready"])
    bus.emit("job.started")
    bus.emit("report.ready")
    assert got == ["report.ready"]


def test_event_bus_isolates_failing_subscriber():
    bus = EventBus()
    calls: list[str] = []

    def bad(_e):
        raise RuntimeError("boom")

    bus.subscribe(bad)
    bus.subscribe(lambda e: calls.append(e.type))
    results = bus.publish(make_event("job.started"))
    assert calls == ["job.started"]  # good subscriber still ran
    assert any(isinstance(r, dict) and "error" in r for r in results)


# --- adapters / manager ---

def _tg_cfg(**kw) -> TelegramIntegration:
    base = dict(enabled=True, bot_token_ref=None, allowed_chat_ids=["123"])
    base.update(kw)
    return TelegramIntegration(**base)


def test_manager_routes_event_to_adapter():
    with temp_home():
        captured: list[tuple] = []
        adapter = TelegramNotifier(_tg_cfg(), sender=lambda r, t, tok: captured.append((r, t)) or True)
        bus = EventBus()
        IntegrationManager(WTHConfig(), bus, adapters=[adapter])
        bus.emit("job.started", job_id="J1", data={"target": "x"})
        assert len(captured) == 1
        assert captured[0][0] == "123"


def test_disabled_integration_receives_nothing():
    with temp_home():
        captured: list[tuple] = []
        adapter = TelegramNotifier(_tg_cfg(enabled=False), sender=lambda r, t, tok: captured.append(r) or True)
        bus = EventBus()
        IntegrationManager(WTHConfig(), bus, adapters=[adapter])
        bus.emit("job.started", job_id="J1")
        assert captured == []


def test_notification_rules_filter_event():
    with temp_home():
        captured: list[str] = []
        cfg = _tg_cfg(notify={"job_started": True, "report_ready": False})
        adapter = TelegramNotifier(cfg, sender=lambda r, t, tok: captured.append(t) or True)
        bus = EventBus()
        IntegrationManager(WTHConfig(), bus, adapters=[adapter])
        bus.emit("report.ready", data={"x": 1})  # filtered out
        assert captured == []
        bus.emit("job.started", data={"x": 1})  # allowed
        assert len(captured) == 1


def test_manager_dry_run_does_not_deliver():
    with temp_home():
        captured: list = []
        adapter = TelegramNotifier(_tg_cfg(), sender=lambda r, t, tok: captured.append(r) or True)
        bus = EventBus()
        IntegrationManager(WTHConfig(), bus, adapters=[adapter], dry_run=True)
        bus.emit("job.started", job_id="J1")
        assert captured == []  # dry-run never delivers


def test_adapter_failure_does_not_break_core():
    with temp_home():
        def boom(r, t, tok):
            raise RuntimeError("network down")
        adapter = TelegramNotifier(_tg_cfg(), sender=boom)
        bus = EventBus()
        mgr = IntegrationManager(WTHConfig(), bus, adapters=[adapter])
        # publish must not raise despite adapter error
        results = mgr.handle(make_event("job.started"))
        assert results[0]["error"] is not None


# --- permissions ---

def test_telegram_recipients_only_allowed_chats():
    adapter = TelegramNotifier(_tg_cfg(allowed_chat_ids=["111", "222"]))
    assert adapter.recipients() == ["111", "222"]


def test_discord_role_permission():
    perms = {
        "owner": ["*"],
        "admin": ["status", "approve"],
        "viewer": ["status"],
    }
    assert command_allowed(perms, "owner", "approve")
    assert command_allowed(perms, "admin", "approve")
    assert not command_allowed(perms, "viewer", "approve")
    assert command_allowed(perms, "viewer", "status")


def test_resolve_discord_role_highest_priority():
    perms = {"owner": ["*"], "admin": ["status"], "analyst": [], "viewer": ["status"]}
    assert resolve_discord_role(["Admin", "Member"], perms) == "admin"
    assert resolve_discord_role(["random"], perms) == "viewer"
    assert resolve_discord_role(["Owner", "Admin"], perms) == "owner"


def test_command_service_enforces_role():
    with temp_home():
        svc = BotCommandService(WTHConfig())
        # viewer cannot approve per default discord role permissions
        denied = svc.handle("approve", ["AP-x"], role="viewer", enforce_role=True)
        assert "Permission denied" in denied
        # owner can
        allowed = svc.handle("help", role="owner", enforce_role=True)
        assert "commands" in allowed.lower()


# --- approvals ---

def test_approval_create_list_approve_deny():
    with tempfile.TemporaryDirectory() as tmp:
        reg = ApprovalRegistry(Path(tmp) / "approvals.json")
        rec = reg.create(job_id="J1", action="authenticated crawl", risk="medium", reason="test")
        assert rec["status"] == "pending"
        assert len(reg.list_pending()) == 1
        approved = reg.approve(rec["approval_id"])
        assert approved["status"] == "approved"
        assert reg.list_pending() == []
        # deny on already-approved returns current state, not pending
        again = reg.deny(rec["approval_id"])
        assert again["status"] == "approved"


def test_approval_deny_and_missing():
    with tempfile.TemporaryDirectory() as tmp:
        reg = ApprovalRegistry(Path(tmp) / "approvals.json")
        rec = reg.create(job_id="J2", action="scan")
        denied = reg.deny(rec["approval_id"])
        assert denied["status"] == "denied"
        assert reg.deny("AP-nope") is None


def test_command_service_jobs_lists_pending():
    with temp_home():
        reg = ApprovalRegistry()
        reg.create(job_id="J9", action="crawl", risk="high")
        svc = BotCommandService(WTHConfig(), approvals=reg)
        out = svc.handle("jobs")
        assert "J9" in out


# --- config / secret hygiene ---

def test_integration_token_ref_not_plaintext_in_config():
    with temp_home() as home:
        SecretVault().set("telegram_bot_token", "secret-TG-12345")
        config = WTHConfig.load()
        config.integrations.telegram.enabled = True
        config.integrations.telegram.bot_token_ref = vault_ref("telegram_bot_token")
        config.integrations.telegram.allowed_chat_ids = ["123"]
        config.save_user_config()
        text = (home / "config" / "user.yaml").read_text()
        assert "secret-TG-12345" not in text
        assert "vault:telegram_bot_token" in text
        # adapter status masks the token
        adapter = TelegramNotifier(config.integrations.telegram)
        assert "secret-TG-12345" not in str(adapter.status())


# --- doctor ---

def test_doctor_event_bus_and_approval_ok():
    with temp_home():
        from wth_ai.tools.doctor import ToolDoctor
        d = ToolDoctor(WTHConfig.load())
        assert d.check_event_bus()["available"] is True
        assert d.check_approval_registry()["ok"] is True


def test_doctor_detects_missing_token_ref():
    with temp_home():
        config = WTHConfig.load()
        config.integrations.telegram.enabled = True
        config.integrations.telegram.bot_token_ref = "vault:telegram_bot_token"  # not in vault
        config.integrations.telegram.allowed_chat_ids = ["123"]
        from wth_ai.tools.doctor import ToolDoctor
        res = ToolDoctor(config).check_integrations()
        assert res["ok"] is False
        assert any("missing in vault" in i for i in res["issues"])


def test_doctor_detects_invalid_integration_config():
    with temp_home():
        SecretVault().set("discord_bot_token", "dtok")
        config = WTHConfig.load()
        config.integrations.discord.enabled = True
        config.integrations.discord.bot_token_ref = "vault:discord_bot_token"
        config.integrations.discord.allowed_channel_ids = []  # invalid: none allowed
        from wth_ai.tools.doctor import ToolDoctor
        res = ToolDoctor(config).check_integrations()
        assert res["ok"] is False
        assert any("allowed_channel_ids" in i for i in res["issues"])


def test_doctor_full_report_has_integration_section():
    with temp_home():
        from wth_ai.tools.doctor import ToolDoctor
        report = ToolDoctor(WTHConfig.load()).full_report()
        assert "Integrations & Events" in report
        assert "Event bus" in report
        assert "Approval registry" in report

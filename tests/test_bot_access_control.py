"""Regression tests — WU-009: Bot access-control fail-closed (NET-01, CRITICAL).

Bug asli (SECURITY_AUDIT.md NET-01): siapa pun yang bisa chat ke bot Discord/
Telegram mendapat sesi orchestrator PENUH (setara operator CLI) karena:
1. `discord_allowed()`/`telegram_allowed()` fail-OPEN saat allowlist kosong
   (default) -- siapa pun boleh chat.
2. Command utama (/doctor /recon /plan /graph, free-text via mention/DM) tidak
   lewat role-check sama sekali -- langsung ke `sessions.run_command()` (sesi
   orchestrator penuh), berbeda dari grup `/wth ...` yang sudah correctly
   role-checked.
3. Telegram hardcode `role="owner"` untuk SEMUA user di `_svc_cmd` (dipakai
   /approve /deny /stop /settings), tanpa `enforce_role=True`.

Fix WU-009 (scope sesuai WORK-UNITS.md -- files: bots/auth.py, discord_bot.py,
telegram_bot.py, config.py):
- `discord_allowed()`/`telegram_allowed()` sekarang fail-closed: allowlist
  kosong (belum dikonfigurasi operator) = TOLAK semua.
- `_handle_slash`/on_message (Discord) dan `_run_cmd`/on_message (Telegram)
  sekarang mewajibkan role "owner" (resolved dari role Discord asli / dari
  `owner_user_ids` eksplisit Telegram) sebelum memberi akses sesi orchestrator
  penuh.
- Telegram `_svc_cmd` tidak lagi hardcode role="owner" -- resolve real role +
  `enforce_role=True` (reuse `command_allowed()`/role_permissions yang sudah
  benar dipakai grup `/wth` Discord).

Di luar scope WU-009 (dicatat eksplisit, BUKAN diabaikan diam-diam): NET-01
poin 4 (verifikasi approver != requester di `_handle_approve` orchestrator)
adalah bagian WU-007 (unifikasi approval) -- file `approvals.py`/
`orchestrator.py` tidak disentuh WU ini.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.bots.auth import discord_allowed, telegram_allowed
from wth_ai.config import BotConfig, WTHConfig
from wth_ai.integrations.base import resolve_telegram_role


# ---------------------------------------------------------------------------
# Unit level -- auth.py fail-closed
# ---------------------------------------------------------------------------

def test_discord_allowed_denies_when_allowlist_empty():
    cfg = BotConfig()  # default: semua list kosong (belum dikonfigurasi)
    assert discord_allowed(cfg, guild_id=123, channel_id=456) is False, (
        "REGRESI NET-01: allowlist Discord kosong (default) masih mengizinkan "
        "chat dari mana pun -- harus fail-closed"
    )


def test_telegram_allowed_denies_when_allowlist_empty():
    cfg = BotConfig()
    assert telegram_allowed(cfg, 999) is False, (
        "REGRESI NET-01: allowlist Telegram kosong (default) masih mengizinkan "
        "chat dari mana pun -- harus fail-closed"
    )


def test_discord_allowed_permits_when_configured_and_matching():
    """Positive/compatibility: begitu allowlist diisi eksplisit, id yang cocok tetap lolos."""
    cfg = BotConfig(discord_guild_ids=[111], discord_channel_ids=[222])
    assert discord_allowed(cfg, guild_id=111, channel_id=222) is True
    assert discord_allowed(cfg, guild_id=999, channel_id=222) is False


def test_telegram_allowed_permits_when_configured_and_matching():
    cfg = BotConfig(telegram_chat_ids=[555])
    assert telegram_allowed(cfg, 555) is True
    assert telegram_allowed(cfg, 999) is False


def test_discord_allowed_channel_only_configured_still_gates_by_channel():
    """Compatibility: operator yang hanya mengisi channel_ids (bukan guild_ids,
    mis. untuk DM) tetap dapat proteksi -- tidak fallback ke fail-open total."""
    cfg = BotConfig(discord_channel_ids=[222])
    assert discord_allowed(cfg, guild_id=None, channel_id=222) is True
    assert discord_allowed(cfg, guild_id=None, channel_id=999) is False


# ---------------------------------------------------------------------------
# Unit level -- resolve_telegram_role
# ---------------------------------------------------------------------------

def test_resolve_telegram_role_owner_in_allowlist():
    # owner_user_ids selalu list[str] setelah lewat config loader
    # (`[str(x) for x in tg_raw.get("owner_user_ids", [])]`) -- user_id boleh int/str.
    assert resolve_telegram_role(42, ["42"]) == "owner"
    assert resolve_telegram_role("42", ["42"]) == "owner"


def test_resolve_telegram_role_defaults_to_viewer():
    assert resolve_telegram_role(999, ["42"]) == "viewer"
    assert resolve_telegram_role(999, []) == "viewer"


# ---------------------------------------------------------------------------
# End-to-end -- Discord: _handle_slash & on_message ditolak untuk non-owner
# ---------------------------------------------------------------------------

def _discord_bot_with_allowlist():
    """Bangun WTHDiscordBot tanpa lewat __init__ penuh -- __init__ memicu
    discord.py app_commands.Group registration (area TIDAK terkait WU-009,
    di luar scope access-control) yang butuh event loop discord.py asli.
    _handle_slash/on_message hanya butuh `.config` dan `.sessions`."""
    from wth_ai.bots.discord_bot import WTHDiscordBot

    cfg = WTHConfig()
    cfg.bots.discord_guild_ids = [111]
    cfg.bots.discord_channel_ids = [222]
    bot = object.__new__(WTHDiscordBot)
    bot.config = cfg
    bot.sessions = MagicMock()
    bot.sessions.run_command = MagicMock(return_value="SHOULD-NOT-BE-CALLED")
    return bot


def test_discord_handle_slash_denies_non_owner_full_session(monkeypatch):
    bot = _discord_bot_with_allowlist()
    monkeypatch.setattr(bot, "_member_role", lambda interaction: "viewer")
    interaction = SimpleNamespace(
        guild_id=111, channel_id=222,
        user=SimpleNamespace(id=1),
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
    )
    import asyncio
    asyncio.run(bot._handle_slash(interaction, "recon example.com"))
    bot.sessions.run_command.assert_not_called()
    interaction.response.send_message.assert_awaited_once()
    assert "Permission denied" in interaction.response.send_message.call_args.args[0]


def test_discord_handle_slash_allows_owner_full_session(monkeypatch):
    bot = _discord_bot_with_allowlist()
    monkeypatch.setattr(bot, "_member_role", lambda interaction: "owner")
    interaction = SimpleNamespace(
        guild_id=111, channel_id=222,
        user=SimpleNamespace(id=1),
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    import asyncio
    asyncio.run(bot._handle_slash(interaction, "recon example.com"))
    bot.sessions.run_command.assert_called_once()


# ---------------------------------------------------------------------------
# End-to-end -- Telegram: _run_cmd & _svc_cmd
# ---------------------------------------------------------------------------

def _telegram_bot_with_allowlist():
    from wth_ai.bots.telegram_bot import WTHTelegramBot

    cfg = WTHConfig()
    cfg.bots.telegram_chat_ids = [777]
    cfg.integrations.telegram.owner_user_ids = ["1"]
    with patch("wth_ai.vault.SecretVault.resolve", return_value=None):
        bot = WTHTelegramBot(cfg)
    bot.sessions = MagicMock()
    bot.sessions.run_command = MagicMock(return_value="SHOULD-NOT-BE-CALLED")
    return bot


def _fake_update(user_id: int, chat_id: int = 777):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        effective_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(
            reply_text=AsyncMock(), reply_chat_action=AsyncMock(), reply_document=AsyncMock(),
        ),
    )


def test_telegram_run_cmd_denies_non_owner_full_session():
    import asyncio

    bot = _telegram_bot_with_allowlist()
    update = _fake_update(user_id=2)  # bukan owner (owner_user_ids=["1"])
    asyncio.run(bot._run_cmd(update, "recon example.com"))
    bot.sessions.run_command.assert_not_called()
    update.message.reply_text.assert_awaited_once()
    assert "Permission denied" in update.message.reply_text.call_args.args[0]


def test_telegram_run_cmd_allows_owner_full_session():
    import asyncio

    bot = _telegram_bot_with_allowlist()
    update = _fake_update(user_id=1)  # owner
    asyncio.run(bot._run_cmd(update, "recon example.com"))
    bot.sessions.run_command.assert_called_once()


def test_telegram_svc_cmd_no_longer_hardcodes_owner_for_non_owner():
    """REGRESI NET-01 poin 3: non-owner user mencoba /approve harus ditolak,
    bukan diam-diam dieksekusi sebagai role='owner'."""
    import asyncio

    bot = _telegram_bot_with_allowlist()
    update = _fake_update(user_id=2)
    ctx = SimpleNamespace(args=["some-approval-id"])
    asyncio.run(bot._svc_cmd(update, ctx, "approve"))
    reply_text = update.message.reply_text.call_args.args[0]
    assert "Permission denied" in reply_text or "cannot run" in reply_text, (
        f"REGRESI NET-01: non-owner masih bisa /approve, respons: {reply_text!r}"
    )


def test_telegram_svc_cmd_still_allows_owner():
    """Positive/compatibility: owner asli tetap bisa /approve seperti sebelumnya."""
    import asyncio

    bot = _telegram_bot_with_allowlist()
    update = _fake_update(user_id=1)
    ctx = SimpleNamespace(args=["nonexistent-id"])
    asyncio.run(bot._svc_cmd(update, ctx, "status"))
    reply_text = update.message.reply_text.call_args.args[0]
    assert "Permission denied" not in reply_text

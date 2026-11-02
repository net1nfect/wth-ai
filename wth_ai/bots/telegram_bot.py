"""Telegram bot daemon for WTH-AI."""

from __future__ import annotations

import asyncio
import io

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from wth_ai.bots.auth import telegram_allowed
from wth_ai.bots.base import BotSessionManager
from wth_ai.config import WTHConfig
from wth_ai.integrations.commands import BotCommandService
from wth_ai.vault import SecretVault

TELEGRAM_MAX = 4000


def _chunk(text: str, limit: int = TELEGRAM_MAX) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        chunks.append(text[:limit])
        text = text[limit:]
    return chunks


class WTHTelegramBot:
    def __init__(self, config: WTHConfig) -> None:
        self.config = config
        self.sessions = BotSessionManager(config)
        from wth_ai.jobs.manager import JobManager
        self.commands = BotCommandService(config, jobs=JobManager(config))
        # Prefer the vault-backed integrations token; fall back to legacy bots config.
        ref = self.config.integrations.telegram.bot_token_ref
        self.token = (SecretVault().resolve(ref) if ref else None) or config.bots.telegram_token

    def _chat_allowed(self, chat_id: int) -> bool:
        tg = self.config.integrations.telegram
        if tg.enabled and tg.allowed_chat_ids:
            return str(chat_id) in tg.allowed_chat_ids
        return telegram_allowed(self.config.bots, chat_id)

    async def _svc_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE, name: str) -> None:
        if not update.effective_chat:
            return
        if not self._chat_allowed(update.effective_chat.id):
            await update.message.reply_text("Chat tidak diizinkan.")  # type: ignore[union-attr]
            return
        args = list(context.args) if context.args else []
        text = self.commands.handle(name, args, role="owner")
        for chunk in _chunk(text):
            await update.message.reply_text(chunk)  # type: ignore[union-attr]

    async def _run_cmd(self, update: Update, command: str) -> None:
        if not update.effective_chat or not update.effective_user:
            return
        if not self._chat_allowed(update.effective_chat.id):
            await update.message.reply_text("Chat tidak diizinkan.")  # type: ignore[union-attr]
            return
        await update.message.reply_chat_action("typing")  # type: ignore[union-attr]
        result = await asyncio.to_thread(
            self.sessions.run_command, "telegram", str(update.effective_user.id), command,
        )
        for chunk in _chunk(result):
            await update.message.reply_text(chunk)  # type: ignore[union-attr]
        if len(result) > TELEGRAM_MAX:
            buf = io.BytesIO(result.encode())
            buf.name = "wth-output.txt"
            await update.message.reply_document(document=buf)  # type: ignore[union-attr]

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(  # type: ignore[union-attr]
            "WTH-AI — Agentic Security Bot\n\n"
            "Commands: /doctor /recon /plan /graph /hunt\n"
            "Or send natural language — the supervisor agent handles everything."
        )

    async def cmd_doctor(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._run_cmd(update, "/doctor")

    async def cmd_recon(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        target = " ".join(context.args) if context.args else ""
        await self._run_cmd(update, f"recon {target}".strip())

    async def cmd_plan(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        goal = " ".join(context.args) if context.args else "Audit target"
        await self._run_cmd(update, f"plan hunt: {goal}")

    async def cmd_graph(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._run_cmd(update, "show asset graph summary")

    async def cmd_hunt(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        goal = " ".join(context.args) if context.args else ""
        await self._run_cmd(update, f"hunt {goal}".strip())

    async def on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.text:
            return
        if not update.effective_chat or not update.effective_user:
            return
        if not self._chat_allowed(update.effective_chat.id):
            return
        text = update.message.text.strip()
        if text.startswith("/"):
            return
        await self._run_cmd(update, text)

    def run(self) -> None:
        if not self.token:
            raise RuntimeError("Telegram bot token tidak dikonfigurasi. Jalankan `wth-ai setup`.")
        app = Application.builder().token(self.token).build()
        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("doctor", self.cmd_doctor))
        app.add_handler(CommandHandler("recon", self.cmd_recon))
        app.add_handler(CommandHandler("plan", self.cmd_plan))
        app.add_handler(CommandHandler("graph", self.cmd_graph))
        app.add_handler(CommandHandler("hunt", self.cmd_hunt))
        # Fase 3 integration commands (shared command service).
        for name in ("status", "jobs", "approve", "deny", "stop", "report", "settings", "help"):
            app.add_handler(CommandHandler(
                name,
                lambda u, c, n=name: self._svc_cmd(u, c, n),
            ))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_message))
        print("WTH-AI Telegram bot starting...")
        app.run_polling()


def run_telegram_bot(config: WTHConfig | None = None) -> None:
    cfg = config or WTHConfig.load()
    WTHTelegramBot(cfg).run()

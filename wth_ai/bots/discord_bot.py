"""Discord bot daemon for WTH-AI."""

from __future__ import annotations

import asyncio
import io

import discord
from discord import app_commands

from wth_ai.bots.auth import discord_allowed
from wth_ai.bots.base import BotSessionManager
from wth_ai.config import WTHConfig

DISCORD_MAX = 1900


def _chunk(text: str, limit: int = DISCORD_MAX) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        chunks.append(text[:limit])
        text = text[limit:]
    return chunks


class WTHDiscordBot:
    def __init__(self, config: WTHConfig) -> None:
        self.config = config
        self.sessions = BotSessionManager(config)
        intents = discord.Intents.default()
        intents.message_content = True
        self.client = discord.Client(intents=intents)
        self.tree = app_commands.CommandTree(self.client)
        self._register_handlers()

    def _register_handlers(self) -> None:
        @self.client.event
        async def on_ready() -> None:
            await self.tree.sync()
            print(f"WTH-AI Discord bot ready: {self.client.user}")

        @self.tree.command(name="doctor", description="Cek status WTH-AI")
        async def cmd_doctor(interaction: discord.Interaction) -> None:
            await self._handle_slash(interaction, "/doctor")

        @self.tree.command(name="recon", description="Jalankan recon pada target")
        @app_commands.describe(target="Domain atau URL target")
        async def cmd_recon(interaction: discord.Interaction, target: str) -> None:
            await self._handle_slash(interaction, f"/recon {target}")

        @self.tree.command(name="plan", description="Buat rencana audit")
        @app_commands.describe(goal="Tujuan audit")
        async def cmd_plan(interaction: discord.Interaction, goal: str) -> None:
            await self._handle_slash(interaction, f"/plan {goal}")

        @self.tree.command(name="graph", description="Tampilkan asset graph")
        async def cmd_graph(interaction: discord.Interaction) -> None:
            await self._handle_slash(interaction, "/graph")

        @self.client.event
        async def on_message(message: discord.Message) -> None:
            if message.author.bot:
                return
            if not discord_allowed(
                self.config.bots,
                guild_id=message.guild.id if message.guild else None,
                channel_id=message.channel.id,
            ):
                return
            if self.client.user and self.client.user.mentioned_in(message):
                text = message.content.replace(f"<@{self.client.user.id}>", "").strip()
                if text:
                    await self._reply(message, text)

    async def _handle_slash(self, interaction: discord.Interaction, command: str) -> None:
        if not discord_allowed(
            self.config.bots,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id if interaction.channel_id else None,
        ):
            await interaction.response.send_message("Channel/guild tidak diizinkan.", ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        result = await asyncio.to_thread(
            self.sessions.run_command, "discord", str(interaction.user.id), command,
        )
        await self._send_result(interaction, result)

    async def _reply(self, message: discord.Message, text: str) -> None:
        async with message.channel.typing():
            result = await asyncio.to_thread(
                self.sessions.run_command, "discord", str(message.author.id), text,
            )
        for chunk in _chunk(result):
            if len(chunk) > DISCORD_MAX:
                buf = io.BytesIO(chunk.encode())
                await message.reply(file=discord.File(buf, filename="wth-output.txt"))
            else:
                await message.reply(chunk[:DISCORD_MAX])

    async def _send_result(self, interaction: discord.Interaction, result: str) -> None:
        chunks = _chunk(result)
        await interaction.followup.send(chunks[0])
        for c in chunks[1:]:
            await interaction.followup.send(c)
        if len(result) > DISCORD_MAX * 2:
            buf = io.BytesIO(result.encode())
            await interaction.followup.send(file=discord.File(buf, filename="wth-output.txt"))

    def run(self) -> None:
        token = self.config.bots.discord_token
        if not token:
            raise RuntimeError("Discord bot token tidak dikonfigurasi. Jalankan `wth-ai setup`.")
        self.client.run(token)


def run_discord_bot(config: WTHConfig | None = None) -> None:
    cfg = config or WTHConfig.load()
    WTHDiscordBot(cfg).run()

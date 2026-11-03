"""Regression test — WU-016: `WTHDiscordBot.__init__()` crash saat registrasi
grup `/wth` (ditemukan selama implementasi WU-009).

Bug asli: `_register_wth_group()` mendaftarkan command "status"/"jobs"/
"settings"/"help" lewat `lambda interaction, n=cmd: run(interaction, n)`.
`app_commands.Group.command()` (discord.py 2.x) menolak apa pun yang bukan
coroutine function ASLI di titik registrasi (`inspect.iscoroutinefunction`),
dan juga butuh introspeksi `__globals__`/signature callback yang tidak
dimiliki `functools.partial` -- jadi `WTHDiscordBot()` selalu crash
`TypeError`/`AttributeError` sebelum bot sempat connect sama sekali,
terlepas dari environment (tidak butuh token/allowlist/network apa pun untuk
reproduce).

Reachability saat ditemukan: TIDAK ADA entry point CLI/gateway/daemon yang
sudah wired memanggil `WTHDiscordBot()`/`run_discord_bot()` -- makanya bug
lolos lama tanpa terdeteksi CI (tidak ada test yang benar-benar instantiate
kelas ini sebelum WU-009 menulis satu untuk keperluan lain).

Fix: ganti lambda dengan closure `async def` asli per command
(`_make_group_cmd()`), bukan `functools.partial` (percobaan pertama --
gagal juga, beda alasan: `functools.partial` lolos cek `iscoroutinefunction`
tapi tidak punya `__globals__` yang dibutuhkan `_extract_parameters_from_callback`).
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.config import WTHConfig


def test_discord_bot_instantiates_without_crash():
    """REGRESI WU-016: instantiate WTHDiscordBot langsung (bukan mock) --
    sebelumnya TypeError/AttributeError di sini, sekarang harus sukses."""
    from wth_ai.bots.discord_bot import WTHDiscordBot

    bot = WTHDiscordBot(WTHConfig())
    assert bot is not None


def test_wth_group_and_top_level_commands_all_registered():
    """Compatibility: semua command yang sebelumnya dimaksud terdaftar tetap
    ada -- fix ini tidak diam-diam menghilangkan command apa pun."""
    from wth_ai.bots.discord_bot import WTHDiscordBot

    bot = WTHDiscordBot(WTHConfig())
    top_level = {c.name for c in bot.tree.get_commands()}
    assert {"doctor", "recon", "plan", "graph", "wth"} <= top_level

    wth_group = next(c for c in bot.tree.get_commands() if c.name == "wth")
    sub = {c.name for c in wth_group.commands}
    assert sub == {"status", "jobs", "settings", "help", "approve", "deny", "stop", "report"}


def test_group_subcommands_are_real_coroutine_functions():
    """Bukti akar penyebab tertutup: setiap callback subcommand grup /wth
    adalah coroutine function asli (bukan lambda/partial)."""
    import inspect

    from wth_ai.bots.discord_bot import WTHDiscordBot

    bot = WTHDiscordBot(WTHConfig())
    wth_group = next(c for c in bot.tree.get_commands() if c.name == "wth")
    for cmd in wth_group.commands:
        assert inspect.iscoroutinefunction(cmd.callback), (
            f"REGRESI WU-016: callback subcommand '{cmd.name}' bukan coroutine "
            "function asli -- risiko crash ulang di versi discord.py lain"
        )

"""`/bypass` — instruksi sesi untuk model, terpisah dari konstitusi repo.

Kenapa bukan AGENTS.md: konstitusi repo dibaca setiap sesi dan berisi konvensi
kerja. Bypass adalah pilihan yang menyala hanya ketika operator memintanya,
karena itu ia disimpan sebagai profil di `$WTH_HOME/bypass/<nama>.md` dan
labelnya selalu ikut di meta balasan — supaya transkrip tidak ambigu tentang
sesi mana yang memakainya.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from wth_ai.agents import bypass as bypass_mod

USAGE = """## /bypass — instruksi sesi (opsional)

  `/bypass`                     status + daftar profil
  `/bypass on [profil]`         aktifkan (tanpa nama = prompt bawaan `wth`)
  `/bypass off`                 matikan
  `/bypass list`                profil yang tersedia
  `/bypass show [profil]`       tampilkan isi prompt
  `/bypass install <file.md>`   salin prompt dari file (opsi: --name, --force)
  `/bypass remove <profil>`     hapus profil dari direktori

Direktori profil: `$WTH_HOME/bypass` (override: `WTH_BYPASS_DIR`).
Bypass tidak mengubah scope, egress allowlist, kill switch, atau gate bukti.
"""


def handle_bypass(orch: Any, args: list[str]) -> str:
    sub = (args[0] if args else "").strip().lower()

    if sub in {"", "status"}:
        return _status(orch)
    if sub in {"on", "enable"}:
        return _enable(orch, args[1] if len(args) > 1 else "")
    if sub in {"off", "disable"}:
        return _disable(orch)
    if sub == "list":
        return _list()
    if sub == "show":
        return _show(args[1] if len(args) > 1 else "")
    if sub == "install":
        return _install(args[1:])
    if sub in {"remove", "rm"}:
        return _remove(args[1] if len(args) > 1 else "")
    if sub in {"help", "-h", "--help", "?"}:
        return USAGE
    return USAGE


# --------------------------------------------------------------------------- #
def _active(orch: Any) -> str:
    return (getattr(orch.config.agent, "bypass", "") or os.environ.get("WTH_BYPASS", "")).strip()


def _status(orch: Any) -> str:
    active = _active(orch)
    lines = [
        f"bypass: {'`' + active + '`' if active else 'mati'}",
        f"direktori: {bypass_mod.bypass_dir()}",
        f"profil tersedia: {', '.join(bypass_mod.list_profiles())}",
    ]
    if active:
        try:
            prompt = bypass_mod.load(active)
            origin = "bawaan" if prompt.is_builtin else str(prompt.source)
            lines.append(f"sumber: {origin} ({len(prompt.text)} karakter)")
        except bypass_mod.BypassError as exc:
            lines.append(f"  ! {exc}")
    lines.append("")
    lines.append("Aktifkan: `/bypass on [profil]` · matikan: `/bypass off`")
    return "\n".join(lines)


def _enable(orch: Any, name: str) -> str:
    try:
        prompt = bypass_mod.load(name or bypass_mod.DEFAULT_PROFILE)
    except bypass_mod.BypassError as exc:
        return f"Gagal: {exc}\nLihat profil: `/bypass list`"

    orch.config.agent.bypass = prompt.name if not prompt.is_builtin else "default"
    # Tidak menulis `WTH_BYPASS` di sini: env adalah jalur flag CLI. Menyentuhnya
    # dari handler membuat status sesi "bocor" ke proses lain dan ke test
    # berikutnya, sementara `config.agent.bypass` sudah menjadi sumber kebenaran
    # untuk perakitan system prompt.
    _reload_prompts(orch)
    return (
        f"✓ bypass aktif: `{prompt.name}` ({len(prompt.text)} karakter)\n"
        "  System prompt dimuat ulang untuk sesi ini.\n"
        "  Invarian tetap: scope, egress allowlist, kill switch, gate bukti."
    )


def _disable(orch: Any) -> str:
    orch.config.agent.bypass = ""
    _reload_prompts(orch)
    return "✓ bypass dimatikan (prompt bawaan repo saja)."


def _list() -> str:
    directory = bypass_mod.bypass_dir()
    lines = [f"## Profil bypass — {directory}", ""]
    for name in bypass_mod.list_profiles():
        marker = " (bawaan)" if name == bypass_mod.BUILTIN_PROFILE else ""
        lines.append(f"- `{name}`{marker}")
    lines.append("")
    lines.append("Pasang: `/bypass install <file.md>`")
    return "\n".join(lines)


def _show(name: str) -> str:
    try:
        prompt = bypass_mod.load(name or bypass_mod.DEFAULT_PROFILE)
    except bypass_mod.BypassError as exc:
        return f"Gagal: {exc}"
    return f"## Profil `{prompt.name}`\n\n```text\n{prompt.text}\n```"


def _install(args: list[str]) -> str:
    positional: list[str] = []
    name = ""
    force = False
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--force":
            force = True
            i += 1
        elif arg in {"--name", "-n"} and i + 1 < len(args):
            name = args[i + 1]
            i += 2
        elif arg.startswith("--name="):
            name = arg.split("=", 1)[1]
            i += 1
        else:
            positional.append(arg)
            i += 1

    if not positional:
        return "Usage: `/bypass install <file.md> [--name N] [--force]`"
    source = Path(positional[0]).expanduser()
    try:
        target = bypass_mod.install(source, name=name, overwrite=force)
    except bypass_mod.BypassError as exc:
        return f"Gagal: {exc}"
    return f"✓ profil terpasang: `{target.stem}` → {target}\nAktifkan: `/bypass on {target.stem}`"


def _remove(name: str) -> str:
    if not name:
        return "Usage: `/bypass remove <profil>`"
    try:
        removed = bypass_mod.remove(name)
    except bypass_mod.BypassError as exc:
        return f"Gagal: {exc}"
    return f"✓ profil `{name}` dihapus." if removed else f"Profil `{name}` tidak ada."


def _reload_prompts(orch: Any) -> None:
    """Rakit ulang system prompt agent yang sudah hidup.

    `ChatAgent`/`SupervisorAgent` membaca prompt sekali di `__init__`, jadi
    mengubah config saja tidak berpengaruh sampai proses dijalankan ulang.
    """
    reloaders = (
        ("chat", "chat_system.txt"),
        ("supervisor", "supervisor_system.txt"),
        ("planner", "planner_system.txt"),
        ("executor", "executor_system.txt"),
        ("verifier", "verifier_system.txt"),
    )
    for attr, prompt_file in reloaders:
        agent = getattr(orch, attr, None)
        if agent is None:
            continue
        loader = getattr(agent, "_reload_system_prompt", None)
        if loader is not None:
            try:
                loader(prompt_file)
                continue
            except Exception:  # noqa: BLE001 - reload adalah kenyamanan, bukan syarat
                pass
        try:
            from wth_ai.agents import load_prompt

            agent.system_prompt = load_prompt(orch.config, prompt_file)
        except Exception:  # noqa: BLE001
            pass
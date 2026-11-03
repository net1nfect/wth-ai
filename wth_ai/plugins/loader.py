"""Discover plugins from ~/.wth-ai/plugins and built-in packs."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

from wth_ai.plugins.context import PluginContext
from wth_ai.workspace import wth_home


def discover_plugin_dirs() -> list[Path]:
    """Direktori yang di-scan untuk plugin.

    `wth_home()/plugins` (home user, trusted) SELALU di-scan. `.wth/plugins`
    RELATIF terhadap current working directory HANYA di-scan kalau
    WTH_ALLOW_CWD_PLUGINS=1 di-set eksplisit -- default OFF (WU-013 / DATA-01).

    Sebelumnya direktori CWD-relative selalu ikut di-scan, artinya siapa pun
    yang menaruh file `.wth/plugins/evil.py` di sebuah direktori (mis. hasil
    clone repo publik, arsip yang diekstrak) bisa membuat kode Python arbitrer
    tereksekusi hanya dengan menjalankan `wth-ai` di direktori itu -- home dir
    user sudah trusted secara wajar (perlu akses filesystem home), tapi CWD
    bisa jadi direktori mana pun operator kebetulan sedang berada.
    """
    dirs = [wth_home() / "plugins"]
    if os.environ.get("WTH_ALLOW_CWD_PLUGINS") == "1":
        dirs.append(Path(".wth") / "plugins")
    return [d for d in dirs if d.is_dir()]


def load_directory_plugins(ctx: PluginContext) -> list[str]:
    loaded: list[str] = []
    for base in discover_plugin_dirs():
        for path in sorted(base.glob("*.py")):
            if path.name.startswith("_"):
                continue
            name = path.stem
            spec = importlib.util.spec_from_file_location(f"wth_plugin_{name}", path)
            if not spec or not spec.loader:
                continue
            mod = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(mod)
                if hasattr(mod, "register"):
                    mod.register(ctx)
                    loaded.append(name)
            except Exception:
                pass
    return loaded


def load_first_party(ctx: PluginContext) -> list[str]:
    """Register built-in CTF/bounty plugin hooks without opening recon registry."""
    loaded: list[str] = []

    def _ctf_hook(payload: dict[str, Any]) -> None:
        if payload.get("event") != "job.completed":
            return
        job_type = payload.get("job_type", "")
        if job_type not in ("recon", "custom"):
            return
        target = payload.get("target", "")
        if target and ctx.bus:
            ctx.bus.emit("app.started", source="plugin-ctf", data={"note": f"recon completed for {target}"})

    def _bounty_hook(payload: dict[str, Any]) -> None:
        if payload.get("event") != "report.ready":
            return
        if ctx.bus:
            ctx.bus.emit("finding.high_priority", source="plugin-bounty", data={"note": "report ready"})

    ctx.on("job.completed", _ctf_hook)
    ctx.on("report.ready", _bounty_hook)
    loaded.extend(["wth-plugin-ctf", "wth-plugin-bounty"])
    return loaded

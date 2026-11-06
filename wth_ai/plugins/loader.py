"""Discover plugins from ~/.wth-ai/plugins and built-in packs."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from wth_ai.audit import audit_log
from wth_ai.plugins.context import PluginContext
from wth_ai.workspace import wth_home


def discover_plugin_dirs() -> list[Path]:
    # P0-1 (audit 2026-11-06): hanya load dari $WTH_HOME/plugins (dimiliki
    # user, absolut). Sebelumnya `.wth/plugins` relatif CWD ikut dimuat dan
    # tereksekusi via exec_module — supply-chain/code-injection vector: sebuah
    # `.wth/plugins/*.py` di direktori tempat tool dijalankan (repo clone,
    # artifact target, workspace bersama) dieksekusi dengan privilege penuh.
    dirs = [wth_home() / "plugins"]
    return [d for d in dirs if d.is_dir() and d.is_absolute()]


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
            except Exception as exc:  # noqa: BLE001 - plugin pihak ketiga
                # Dulu `pass` membuat plugin lama (mis. yang membaca `ctx.config`)
                # menghilang tanpa pesan sama sekali.
                print(f"plugin {name} gagal dimuat: {type(exc).__name__}: {exc}",
                      file=sys.stderr)
                audit_log("plugin_load_failed", plugin=name,
                          error=f"{type(exc).__name__}: {exc}")
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

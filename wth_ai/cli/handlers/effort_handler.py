"""`/effort` — level effort: low | medium | high | xhigh | ultra."""

from __future__ import annotations

from typing import Any

from wth_ai.effort import EFFORT_LEVELS, apply_effort, detect_effort, effort_names


def _current_line(config: Any) -> str:
    m = config.models
    return (
        f"Effort sekarang: `{detect_effort(config)}` · max_tokens {m.max_tokens} · "
        f"timeout planner {m.planner_timeout_seconds}s / executor {m.executor_timeout_seconds}s"
    )


def _options_lines() -> list[str]:
    return [
        f"  {name:<7} — {spec['label']} (max_tokens {spec['max_tokens']}, "
        f"timeout {spec['planner_timeout_seconds']}/{spec['executor_timeout_seconds']}s)"
        for name, spec in EFFORT_LEVELS.items()
    ]


def handle_effort(orch: Any, args: list[str]) -> str:
    """Set level effort. Tanpa argumen: picker interaktif (atau daftar di mode plain)."""
    raw = (args[0].strip().lower() if args else "")

    if not raw:
        from wth_ai.cli.ui.picker import interactive_available, pick_from_list

        if interactive_available():
            entries = [f"{name:<7} {spec['label']}" for name, spec in EFFORT_LEVELS.items()]
            choice = pick_from_list(
                "Effort level",
                entries,
                subtitle="↑/↓ pilih · Enter pasang · Esc batal",
            )
            if not choice:
                return "Dibatalkan — tidak ada yang diubah."
            raw = choice.split()[0]
        else:
            return "\n".join(
                ["Level effort — pakai `/effort <level>`:", *_options_lines(), "", _current_line(orch.config)]
            )

    if raw not in EFFORT_LEVELS:
        return (
            f"Level `{raw}` tidak dikenal. Pilihan: {' · '.join(effort_names())}.\n"
            + _current_line(orch.config)
        )

    try:
        spec = apply_effort(orch.config, raw)
    except AttributeError as exc:  # config tanpa section models — jangan crash
        return f"Gagal menerapkan effort: {exc}"

    notes: list[str] = []
    try:
        orch.config.save_user_config()
        notes.append("tersimpan")
    except Exception:  # noqa: BLE001 - sesi ini tetap pakai nilai baru
        notes.append("gagal simpan (sesi ini tetap pakai nilai baru)")
    client = getattr(orch, "client", None)
    if client is not None and hasattr(client, "reset_provider_cache"):
        client.reset_provider_cache()
        notes.append("cache provider di-reset")

    suffix = f" · {', '.join(notes)}" if notes else ""
    return (
        f"✓ effort → {raw} · max_tokens {spec['max_tokens']} · "
        f"timeout planner {spec['planner_timeout_seconds']}s / executor {spec['executor_timeout_seconds']}s{suffix}"
    )

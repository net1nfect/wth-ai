"""Setup slash handlers — /target, /model, /provider, /profile, /ui."""

from __future__ import annotations

from typing import Any

import yaml

from wth_ai.config import PROVIDER_NAMES, ROLE_NAMES, ProviderRoleConfig
from wth_ai.llm.model_pick import is_likely_invalid_model, resolve_model_choice
from wth_ai.scope_intent import parse_scope_intent


def handle_target(orch: Any, args: list[str]) -> str:
    """Set or show assessment target."""
    if not args or args[0] in {"show", "get"}:
        target = orch.state.scope.target or "unset"
        lines = [
            f"target: {target}",
            f"authorized: {'yes' if orch.state.scope.authorized else 'no'}",
            f"in_scope: {', '.join(orch.state.scope.in_scope) or 'unset'}",
        ]
        notes = orch.state.scope.notes
        if notes:
            lines.append(f"notes: {notes}")
        lines.append("")
        lines.append("Usage: `/target example.com` · `/target https://host/path` · `/target clear`")
        return "\n".join(lines)

    if args[0] == "clear":
        orch.scope_mgr.set_scope(target="", authorized=False, in_scope=[], notes="")
        orch.state.scope = orch.scope_mgr.scope
        orch._persist()
        return "Target cleared."

    raw = " ".join(args).strip()
    intent = parse_scope_intent(f"set target {raw}") or parse_scope_intent(raw)
    if not intent:
        # WU-011 (AGENT-01): default False, bukan True -- konsisten dengan
        # parse_scope_intent() sendiri (fail-closed law: MISSING -> DENY).
        intent = {"target": raw.split()[0], "authorized": False, "in_scope": [raw.split()[0]]}

    return _apply_scope_intent(orch, intent)


#: Everything `/model` treats as a subcommand. Kept next to the dispatcher so the
#: two cannot drift: a verb added below must be listed here, or it can be stored as
#: a model name by the "treat a single token as a model id" fallback.
_MODEL_VERBS = frozenset({
    "show", "current", "list", "fix", "use", "set", "test", "try", "pick", "pilih",
})


def handle_model(orch: Any, args: list[str]) -> str:
    """List or switch LLM models from the active provider."""
    # Bare `/model` opens the interactive picker on a real terminal: choosing from
    # 268 models should not require reading a numbered wall of text and retyping a
    # number. Piped/CI runs keep the text summary so nothing can block on a picker.
    if not args:
        from wth_ai.cli.ui.picker import interactive_available

        if interactive_available():
            return _model_picker_flow(orch)
        return _model_show(orch) + (
            "\n\nPilih interaktif: `/model pick` (butuh terminal). "
            "Non-interaktif: `/model list` lalu `/model <nomor>`."
        )

    sub = args[0]

    if sub in {"show", "current"}:
        return _model_show(orch)

    if sub == "list":
        # `/model list [provider] [kata]`. A known provider name still selects the
        # provider (`/model list 9router`, as before); anything else is a search
        # needle. Without search, 268 ids print as a wall of text and the operator
        # has no way to find theirs.
        first = (args[1] if len(args) > 1 else "").strip()
        if first and first.lower() in {name.lower() for name in _known_providers(orch)}:
            provider = first
            needle = " ".join(args[2:]).strip()
        else:
            provider = _primary_provider(orch)
            needle = " ".join(args[1:]).strip()
        return _model_list(orch, provider, store_picker=True, needle=needle)

    if sub in {"test", "try"} and len(args) >= 2:
        return _model_test(orch, " ".join(args[1:]).strip())

    if sub in {"pick", "pilih"}:
        return _model_picker_flow(orch, needle=" ".join(args[1:]).strip())

    if sub == "fix":
        return _model_fix(orch)

    if sub == "use" and len(args) >= 2:
        return _model_pick_index(orch, args[1], role=args[2] if len(args) > 2 else "planner")

    if sub == "set":
        if len(args) >= 3 and args[1] == "all":
            model_id = " ".join(args[2:])
            return _model_set_all(orch, model_id)
        if len(args) >= 3:
            return _model_set_role(orch, args[1], " ".join(args[2:]))
        return "Usage: `/model set planner <id>` · `/model set all <id>`"

    if sub.isdigit():
        return _model_pick_index(orch, sub)

    if len(args) == 2 and args[0] in ROLE_NAMES:
        return _model_set_role(orch, args[0], args[1])

    # A bare VERB is not a model id. Measured: `/model use` (the verb, without its
    # argument) fell through to this branch and PERSISTED a model literally named
    # "use", breaking the operator's config. Anything this handler already treats
    # as a verb must ask for usage instead of being stored as a model name.
    if sub in _MODEL_VERBS:
        return (
            f"`{sub}` butuh argumen.\n"
            "Usage:\n"
            "  `/model list [kata]` — daftar/cari model\n"
            "  `/model pick` — pilih interaktif (panah + enter)\n"
            "  `/model use <nomor>` — pilih dari daftar terakhir\n"
            "  `/model set <role|all> <id>` — set role\n"
            "  `/model test <nomor|id>` — uji tanpa mengubah config\n"
            "  `/model show` — lihat konfigurasi sekarang"
        )

    if len(args) == 1 and sub not in ROLE_NAMES:
        return _model_set_role(orch, "planner", sub)

    return (
        _model_show(orch)
        + "\n\nUsage:\n"
        "  `/model list [kata]` — daftar/cari model provider\n"
        "  `/model test <nomor|id>` — uji model tanpa mengubah config\n"
        "  `/model 4` atau `/model use 4` — pilih by nomor\n"
        "  `/model set planner Hacker` — set role\n"
        "  `/model set all cu/default` — semua role\n"
        "  `/model fix` — perbaiki index salah"
    )


def _known_providers(orch: Any) -> set[str]:
    """Provider bawaan + yang didaftarkan lewat `/connect`."""
    return set(PROVIDER_NAMES) | set(getattr(orch.config, "custom_providers", {}))


def handle_provider(orch: Any, args: list[str]) -> str:
    """Show or switch LLM provider per role."""
    if not args or args[0] == "show":
        lines = ["## Providers"]
        for role in ROLE_NAMES:
            spec = orch.config.providers.get(role)
            if spec:
                lines.append(f"- {role}: {spec.type} / {spec.model}")
            else:
                lines.append(f"- {role}: (default legacy)")
        lines.append("\nUsage: `/provider set planner 9router` · `/provider set all openrouter`")
        return "\n".join(lines)

    if args[0] == "set" and len(args) >= 3:
        role, ptype = args[1], args[2].lower()
        if role == "all":
            if ptype not in _known_providers(orch):
                return f"Provider tidak dikenal. Pilih: {', '.join(sorted(_known_providers(orch)))}"
            for r in ROLE_NAMES:
                spec = orch.config.providers.get(r)
                model = spec.model if spec else getattr(orch.config.models, r, orch.config.models.planner)
                orch.config.providers[r] = ProviderRoleConfig(type=ptype, model=model)
            orch.config.save_user_config()
            return f"Semua role → provider `{ptype}`"
        if ptype not in _known_providers(orch):
            return f"Provider tidak dikenal. Pilih: {', '.join(sorted(_known_providers(orch)))}"
        spec = orch.config.providers.get(role)
        model = spec.model if spec else getattr(orch.config.models, role, orch.config.models.planner)
        orch.config.providers[role] = ProviderRoleConfig(type=ptype, model=model)
        orch.config.save_user_config()
        return f"{role} → provider `{ptype}` (model: {model})"

    if args[0] == "list":
        return "Providers: " + ", ".join(sorted(_known_providers(orch)))

    return "Usage: `/provider show` · `/provider set <role|all> <type>`"


def handle_profile(orch: Any, args: list[str]) -> str:
    from wth_ai.profiles import apply_profile, list_profiles

    sub = args[0] if args else "list"
    if sub == "list":
        names = list_profiles()
        return "## Profiles\n" + "\n".join(f"- {n}" for n in names)
    if sub == "use" and len(args) > 1:
        try:
            apply_profile(args[1])
            return f"Profile activated: {args[1]}"
        except Exception as exc:
            return f"Profile error: {exc}"
    if sub in list_profiles():
        try:
            apply_profile(sub)
            return f"Profile activated: {sub}"
        except Exception as exc:
            return f"Profile error: {exc}"
    return "Usage: `/profile list` · `/profile use bug-bounty`"


def handle_ui(orch: Any, args: list[str]) -> str:
    ui = orch.config.ui
    if not args or args[0] == "show":
        return (
            f"ui.mode: {ui.mode}\n"
            f"ui.compact: {ui.compact}\n"
            f"ui.theme: {ui.theme}\n"
            f"ui.animations: {ui.animations}\n"
            f"ui.stream_tokens: {ui.stream_tokens}\n"
            f"ui.verbose: {ui.verbose}"
        )

    if args[0] == "compact" and len(args) > 1:
        val = args[1].lower() in {"1", "on", "true", "yes"}
        return _ui_set(orch, "compact", val)

    if args[0] == "verbose" and len(args) > 1:
        val = args[1].lower() in {"1", "on", "true", "yes"}
        return _ui_set(orch, "verbose", val)

    if args[0] == "mode" and len(args) > 1:
        mode = args[1].lower()
        if mode not in {"plain", "tui"}:
            return "Usage: `/ui mode plain|tui`"
        return _ui_set(orch, "mode", mode)

    if args[0] == "theme" and len(args) > 1:
        return _ui_set(orch, "theme", args[1])

    return "Usage: `/ui show` · `/ui compact on|off` · `/ui verbose on|off` · `/ui mode plain|tui` · `/ui theme dark`"


def apply_target_from_text(orch: Any, text: str) -> str | None:
    """Apply target from natural language; returns message or None."""
    intent = parse_scope_intent(text)
    if not intent:
        return None
    msg = _apply_scope_intent(orch, intent)
    return f"{msg}\n(tip: gunakan `/target ...` ke depan)"


def _apply_scope_intent(orch: Any, intent: dict[str, Any]) -> str:
    orch.scope_mgr.update_from_dict(intent)
    orch.state.scope = orch.scope_mgr.scope
    if intent.get("target") and getattr(orch, "_supervisor_ctx", None) and orch._supervisor_ctx.recon_bridge:
        orch._supervisor_ctx.recon_bridge.set_target(intent["target"])
    orch._persist()
    target = intent.get("target", "")
    extra = ""
    if intent.get("notes", "").startswith("entry_url="):
        extra = f" · entry: {intent['notes'].split('=', 1)[1]}"
    return f"✓ target `{target}`{extra} · authorized={'yes' if intent.get('authorized') else 'no'}"


#: Verification makes one tiny call, so it needs a bound. Long enough for a slow
#: gateway, short enough that a hung model cannot freeze the REPL.
VERIFY_TIMEOUT = 20

#: Escape hatch for scripts/tests that must not spend a call per selection.
VERIFY_ENV = "WTH_SKIP_MODEL_VERIFY"


def _verify_enabled() -> bool:
    import os

    return os.environ.get(VERIFY_ENV, "").strip() not in {"1", "true", "yes"}


def _probe_model(orch: Any, model_id: str, *, role: str = "planner") -> tuple[bool, str]:
    """Try ONE short call with `model_id` WITHOUT changing any config.

    (ok, human explanation). Two reasons this is the feature that makes 268 models
    usable: a gateway can list hundreds of ids while only some actually answer,
    and a list you cannot test is a list you cannot choose from.
    """
    try:
        provider = orch.client.provider_for_role(role)
    except Exception as exc:  # noqa: BLE001
        return False, f"provider tidak bisa dipakai: {type(exc).__name__}: {str(exc)[:120]}"
    try:
        env = provider.complete(
            model_id,
            [{"role": "user", "content": "ping"}],
            role=role,
            timeout=VERIFY_TIMEOUT,
            max_tokens=8,
        )
    except Exception as exc:  # noqa: BLE001 - never let a probe kill the REPL
        return False, f"{type(exc).__name__}: {str(exc)[:140]}"
    if str(env.get("status", "")) == "success":
        return True, f"dijawab model `{env.get('model') or model_id}`"
    err = env.get("error")
    if isinstance(err, dict):
        detail = err.get("message") or err.get("code") or str(err)
    else:
        detail = str(err or env.get("content") or "tanpa keterangan")
    return False, f"{str(env.get('status') or 'error')}: {str(detail)[:140]}"


def _verify_role_model(orch: Any, role: str) -> str:
    """Line appended after a selection so the operator learns whether it WORKS."""
    if not _verify_enabled():
        return "  [verifikasi dilewati: WTH_SKIP_MODEL_VERIFY=1]"
    try:
        model_id = orch.config.providers[role].model
    except Exception:  # noqa: BLE001
        return ""
    ok, detail = _probe_model(orch, model_id, role=role)
    if ok:
        return f"  ✓ terverifikasi — {detail}"
    return (
        f"  ⚠ terpasang TAPI belum merespons — {detail}\n"
        "    Uji kandidat lain tanpa mengubah config: `/model test <nomor|id>`\n"
        "    Cari model: `/model list <kata>`"
    )


def _primary_provider(orch: Any) -> str:
    spec = orch.config.providers.get("planner")
    return spec.type if spec else "openrouter"


def _model_show(orch: Any) -> str:
    lines = ["## Models (configured)"]
    for role in ROLE_NAMES:
        spec = orch.config.providers.get(role)
        if spec:
            lines.append(f"- {role}: {spec.type} / {spec.model}")
        else:
            model = getattr(orch.config.models, role, orch.config.models.planner)
            lines.append(f"- {role}: legacy / {model}")
    active = orch.state.metadata.get("model_picker", {})
    if active.get("models"):
        lines.append(f"\nLast list: {active.get('provider')} ({len(active['models'])} models) — `/model <n>`")
    return "\n".join(lines)


#: How many rows to print when browsing. A search shows its full match set (it is
#: already narrowed); browsing a 268-model catalog must not flood the chat.
BROWSE_LIMIT = 25
SEARCH_LIMIT = 60


def _model_list(orch: Any, provider: str, *, store_picker: bool = False, needle: str = "") -> str:
    try:
        models = orch.client.list_models(provider)
    except Exception as exc:
        return f"Tidak bisa list model `{provider}`: {exc}"
    if not models:
        # Say WHERE the request went. Without this the operator sees an empty list
        # and no reason, which is exactly how "kenapa gabisa milih model" happens.
        endpoint = ""
        try:
            endpoint = getattr(orch.client.provider_for_role("planner"), "base_url", "") or ""
        except Exception:  # noqa: BLE001
            endpoint = ""
        return (
            f"Tidak ada model dari `{provider}`.\n"
            f"  Endpoint yang dipakai: {endpoint or '(base URL belum diset)'}\n"
            "  Kalau itu bukan gateway yang lu maksud, perbaiki base URL-nya "
            "(`wth-ai setup`, atau `/provider`)."
        )

    needle = (needle or "").strip().lower()
    matches = [m for m in models if needle in m.lower()] if needle else list(models)
    if not matches:
        return (
            f"Tidak ada model cocok `{needle}` di `{provider}` ({len(models)} total).\n"
            "Coba kata yang lebih pendek, atau `/model list` untuk melihat semuanya."
        )

    limit = SEARCH_LIMIT if needle else BROWSE_LIMIT
    shown = matches[:limit]
    if store_picker:
        # Store what was SHOWN (not the whole catalog) so the numbers the operator
        # reads are exactly the numbers `/model <n>` accepts.
        orch.state.metadata["model_picker"] = {"provider": provider, "models": shown}
        orch._persist()

    if needle:
        title = f"## Models — {provider} · cocok `{needle}`: {len(matches)}"
    else:
        title = f"## Models — {provider} · {len(models)} total (menampilkan {len(shown)})"
    lines = [title, ""]
    for i, m in enumerate(shown, 1):
        lines.append(f"  {i}. {m}")
    if len(matches) > limit:
        lines.append(f"  … dan {len(matches) - limit} lain lagi")
    lines.append("")
    if needle:
        lines.append(f"Pilih: `/model <nomor>` · uji dulu: `/model test <nomor>`")
    else:
        lines.append("Persempit: `/model list <kata>` (mis. `/model list glm`)")
        lines.append("Pilih: `/model <nomor>` · uji tanpa ganti: `/model test <nomor|id>`")
    return "\n".join(lines)


def _model_picker_flow(orch: Any, *, needle: str = "", role: str = "planner") -> str:
    """Interactive picker (arrows + Enter). Degrades to the text listing off-tty."""
    from wth_ai.cli.ui.picker import interactive_available, pick_from_list

    provider = _primary_provider(orch)
    try:
        models = orch.client.list_models(provider)
    except Exception as exc:
        return f"Tidak bisa list model `{provider}`: {exc}"
    if not models:
        endpoint = ""
        try:
            endpoint = getattr(orch.client.provider_for_role("planner"), "base_url", "") or ""
        except Exception:  # noqa: BLE001
            endpoint = ""
        return (
            f"Tidak ada model dari `{provider}`.\n"
            f"  Endpoint yang dipakai: {endpoint or '(base URL belum diset)'}\n"
            "  Kalau itu bukan gateway yang lu maksud, perbaiki base URL-nya "
            "(`wth-ai setup`, atau `/provider`)."
        )

    needle = (needle or "").strip().lower()
    candidates = [m for m in models if needle in m.lower()] if needle else list(models)
    if not candidates:
        return (
            f"Tidak ada model cocok `{needle}` di `{provider}` ({len(models)} total). "
            "Coba kata lain."
        )
    if not interactive_available():
        return _model_list(orch, provider, store_picker=True, needle=needle)

    current = ""
    try:
        current = orch.config.providers[role].model
    except Exception:  # noqa: BLE001
        current = ""

    from wth_ai.cli.ui.picker import describe_model

    choice = pick_from_list(
        f"Pilih model · {provider}",
        candidates,
        subtitle=f"{len(candidates)} kandidat · dipasang ke role {role}",
        describe=lambda m: describe_model(m, current=current),
    )
    if not choice:
        return "Dibatalkan — tidak ada yang diubah."
    result = _model_set_role(orch, role, choice)
    return f"{result}\n  Pasang ke semua role: `/model set all {choice}`"


def _model_test(orch: Any, spec: str) -> str:
    """Try a model by number (from the last listing) or by id. Changes NOTHING."""
    if not spec:
        return "Usage: `/model test <nomor|id>` (mis. `/model test 3` atau `/model test glm-5.3-flash-rr`)"
    picker = orch.state.metadata.get("model_picker", {})
    models: list[str] = picker.get("models") or []
    if spec.isdigit():
        idx = int(spec) - 1
        if not models:
            return "Belum ada daftar model. Jalankan `/model list` dulu."
        if idx < 0 or idx >= len(models):
            return f"Nomor di luar range 1-{len(models)}"
        model_id = models[idx]
    else:
        model_id = spec
    ok, detail = _probe_model(orch, model_id)
    if ok:
        return f"✓ `{model_id}` merespons — {detail}\nPasang: `/model set planner {model_id}`"
    return f"✗ `{model_id}` TIDAK merespons — {detail}"


def _model_pick_index(orch: Any, index: str, *, role: str = "planner") -> str:
    picker = orch.state.metadata.get("model_picker", {})
    models: list[str] = picker.get("models") or []
    if not models:
        listed = _model_list(orch, _primary_provider(orch), store_picker=True)
        picker = orch.state.metadata.get("model_picker", {})
        models = picker.get("models") or []
        if not models:
            return listed
    try:
        idx = int(index) - 1
    except ValueError:
        return f"Nomor tidak valid: {index}"
    if idx < 0 or idx >= len(models):
        return f"Nomor di luar range 1-{len(models)}"
    return _model_set_role(orch, role, models[idx])


def _model_set_role(orch: Any, role: str, model: str) -> str:
    if role not in ROLE_NAMES:
        return f"Role tidak dikenal: {role}. Pilih: {', '.join(ROLE_NAMES)}"
    spec = orch.config.providers.get(role)
    ptype = spec.type if spec else _primary_provider(orch)
    try:
        catalog = orch.client.list_models(ptype)
    except Exception:
        catalog = []
    resolved = resolve_model_choice(model, catalog) if catalog else model
    backup = spec.backup_model if spec else ""
    orch.config.providers[role] = ProviderRoleConfig(type=ptype, model=resolved, backup_model=backup)
    # Invalidate through the canonical router API as well, so any per-role cache
    # is dropped rather than relying on the handler writing config directly.
    try:
        orch.client.set_role(role, ptype, resolved, backup_model=backup)
    except Exception:  # noqa: BLE001 - a stub client in tests may not implement it
        pass
    orch.config.save_user_config()
    return f"✓ {role} → `{resolved}` ({ptype})\n{_verify_role_model(orch, role)}"


def _model_set_all(orch: Any, model: str) -> str:
    ptype = _primary_provider(orch)
    try:
        catalog = orch.client.list_models(ptype)
    except Exception:
        catalog = []
    resolved = resolve_model_choice(model, catalog) if catalog else model
    for role in ROLE_NAMES:
        spec = orch.config.providers.get(role)
        backup = spec.backup_model if spec else ""
        orch.config.providers[role] = ProviderRoleConfig(type=ptype, model=resolved, backup_model=backup)
    orch.config.save_user_config()
    return f"✓ semua role ({len(ROLE_NAMES)}) → `{resolved}` ({ptype})"


def _model_fix(orch: Any) -> str:
    fixed: list[str] = []
    for role in ROLE_NAMES:
        spec = orch.config.providers.get(role)
        if not spec:
            continue
        try:
            catalog = orch.client.list_models(spec.type)
        except Exception:
            catalog = []
        if not catalog or not is_likely_invalid_model(spec.model, catalog):
            continue
        new = resolve_model_choice(str(spec.model), catalog)
        if new in catalog and new != spec.model:
            orch.config.providers[role] = ProviderRoleConfig(
                type=spec.type, model=new, backup_model=spec.backup_model,
            )
            fixed.append(f"{role} → {new}")
    if fixed:
        orch.config.save_user_config()
        return "## Models fixed\n" + "\n".join(f"- {x}" for x in fixed)
    return "Tidak ada model index/invalid yang perlu diperbaiki."


def _ui_set(orch: Any, key: str, value: Any) -> str:
    path = orch.config.user_config_path
    data: dict = {}
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            data = {}
    ui_data = data.setdefault("ui", {})
    ui_data[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, default_flow_style=False), encoding="utf-8")
    setattr(orch.config.ui, key, value)
    return f"✓ ui.{key} = {value}"

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
        intent = {"target": raw.split()[0], "authorized": True, "in_scope": [raw.split()[0]]}

    return _apply_scope_intent(orch, intent)


def handle_model(orch: Any, args: list[str]) -> str:
    """List or switch LLM models from the active provider."""
    sub = args[0] if args else "show"

    if sub in {"show", "current"}:
        return _model_show(orch)

    if sub == "list":
        provider = args[1] if len(args) > 1 else _primary_provider(orch)
        return _model_list(orch, provider, store_picker=True)

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

    if len(args) == 1 and sub not in ROLE_NAMES:
        return _model_set_role(orch, "planner", sub)

    return (
        _model_show(orch)
        + "\n\nUsage:\n"
        "  `/model list` — daftar model provider\n"
        "  `/model 4` atau `/model use 4` — pilih by nomor\n"
        "  `/model set planner Hacker` — set role\n"
        "  `/model set all cu/default` — semua role\n"
        "  `/model fix` — perbaiki index salah"
    )


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
            if ptype not in PROVIDER_NAMES:
                return f"Provider tidak dikenal. Pilih: {', '.join(PROVIDER_NAMES)}"
            for r in ROLE_NAMES:
                spec = orch.config.providers.get(r)
                model = spec.model if spec else getattr(orch.config.models, r, orch.config.models.planner)
                orch.config.providers[r] = ProviderRoleConfig(type=ptype, model=model)
            orch.config.save_user_config()
            return f"Semua role → provider `{ptype}`"
        if ptype not in PROVIDER_NAMES:
            return f"Provider tidak dikenal. Pilih: {', '.join(PROVIDER_NAMES)}"
        spec = orch.config.providers.get(role)
        model = spec.model if spec else getattr(orch.config.models, role, orch.config.models.planner)
        orch.config.providers[role] = ProviderRoleConfig(type=ptype, model=model)
        orch.config.save_user_config()
        return f"{role} → provider `{ptype}` (model: {model})"

    if args[0] == "list":
        return "Providers: " + ", ".join(PROVIDER_NAMES)

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


def _model_list(orch: Any, provider: str, *, store_picker: bool = False) -> str:
    try:
        models = orch.client.list_models(provider)
    except Exception as exc:
        return f"Tidak bisa list model `{provider}`: {exc}"
    if not models:
        return f"Tidak ada model dari `{provider}`."
    if store_picker:
        # store full catalog so `/model <n>` works beyond 50
        orch.state.metadata["model_picker"] = {"provider": provider, "models": models}
        orch._persist()
    lines = [f"## Models — {provider} ({len(models)} total)", ""]
    for i, m in enumerate(models, 1):
        lines.append(f"  {i}. {m}")
    lines.append("")
    lines.append("Pilih: `/model 4` · set role: `/model set planner Hacker`")
    return "\n".join(lines)


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
    orch.config.save_user_config()
    return f"✓ {role} → `{resolved}` ({ptype})"


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

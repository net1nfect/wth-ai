"""`/config` slash handler."""

from __future__ import annotations

from typing import Any

import yaml


def handle_config(orch: Any, args: list[str]) -> str:
    sub = args[0] if args else "show"
    if sub == "show":
        return _config_show(orch)
    if sub == "set" and len(args) >= 2:
        return _config_set(orch, args[1])
    if sub == "profile":
        from wth_ai.cli.handlers.setup_handler import handle_profile
        return handle_profile(orch, args[1:])
    if sub == "model":
        from wth_ai.cli.handlers.setup_handler import handle_model
        return handle_model(orch, args[1:])
    return _config_show(orch) + "\n\nUsage: `/config show|set key=val`\nSetup: `/target` `/model` `/provider` `/profile` `/ui`"


def _config_show(orch: Any) -> str:
    cfg = orch.config
    lines = [
        "## Config",
        f"- theme: {cfg.ui.theme}",
        f"- recon_profile: {cfg.agent.recon_profile}",
        f"- permission: {orch.state.permission_mode.value}",
        f"- target: {orch.state.scope.target or 'unset'}",
        f"- llm: {'online' if orch.client.available() else 'offline'}",
        "",
        "### Models",
    ]
    for role in ("planner", "executor", "verifier", "reporter"):
        spec = cfg.providers.get(role)
        if spec:
            lines.append(f"- {role}: {spec.type}/{spec.model}")
        else:
            model = getattr(cfg.models, role, cfg.models.planner)
            lines.append(f"- {role}: {model}")
    active = orch.state.metadata.get("active_skills", [])
    if active:
        lines.append(f"\n### Active skills\n- {', '.join(active)}")
    return "\n".join(lines)


def _config_set(orch: Any, expr: str) -> str:
    if "=" not in expr:
        return "Usage: `/config set agent.recon_profile=recon-only`"
    key, val = expr.split("=", 1)
    key, val = key.strip(), val.strip()
    path = orch.config.user_config_path
    data: dict = {}
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            data = {}

    parts = key.split(".")
    cursor = data
    for p in parts[:-1]:
        cursor = cursor.setdefault(p, {})
    cursor[parts[-1]] = _coerce(val)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, default_flow_style=False), encoding="utf-8")

    # hot-reload common keys
    if key == "ui.theme":
        orch.config.ui.theme = val
    elif key == "agent.recon_profile":
        orch.config.agent.recon_profile = val
    return f"Set `{key}` = `{val}`"


def _coerce(val: str):
    if val.lower() in {"true", "yes"}:
        return True
    if val.lower() in {"false", "no"}:
        return False
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        return val


def _config_profile(orch: Any, args: list[str]) -> str:
    from wth_ai.profiles import apply_profile, list_profiles

    sub = args[0] if args else "list"
    if sub == "list":
        names = list_profiles()
        return "## Profiles\n" + "\n".join(f"- {n}" for n in names)
    if sub == "use" and len(args) > 1:
        name = args[1]
        try:
            from wth_ai.profiles import apply_profile
            apply_profile(name)
            return f"Profile activated: **{name}**"
        except Exception as exc:
            return f"Profile error: {exc}"
    return "Usage: `/config profile list` or `/config profile use bug-bounty`"


def _config_model(orch: Any, args: list[str]) -> str:
    from wth_ai.config import ROLE_NAMES, ProviderRoleConfig
    from wth_ai.llm.model_pick import is_likely_invalid_model, resolve_model_choice

    sub = args[0] if args else "list"
    if sub == "list":
        lines = ["## Models"]
        for ptype in ("openrouter", "ollama", "9router"):
            try:
                models = orch.client.list_models(ptype)[:8]
                if models:
                    lines.append(f"\n### {ptype}\n" + "\n".join(f"- {m}" for m in models))
            except Exception:
                pass
        lines.append("\nConfigured roles:")
        for role in ROLE_NAMES:
            spec = orch.config.providers.get(role)
            if spec:
                lines.append(f"- {role}: {spec.type} / {spec.model}")
        return "\n".join(lines) if len(lines) > 1 else "No models listed."
    if sub == "fix":
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
        return "No numeric/index model ids to fix (or 9router unreachable)."
    if sub == "set" and len(args) >= 3:
        role, model = args[1], " ".join(args[2:])
        from wth_ai.llm.model_pick import resolve_model_choice
        spec = orch.config.providers.get(role)
        ptype = spec.type if spec else "openrouter"
        catalog = orch.client.list_models(ptype)
        model = resolve_model_choice(model, catalog) if catalog else model
        orch.config.providers[role] = ProviderRoleConfig(type=ptype, model=model)
        orch.config.save_user_config()
        return f"Model {role} → {model}"
    return "Usage: `/config model list|fix|set <role> <model>`"

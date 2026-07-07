"""Provider smoke test — validate a provider end-to-end before use."""

from __future__ import annotations

from typing import Any

from wth_ai.config import ROLE_NAMES, WTHConfig
from wth_ai.llm.model_pick import is_likely_invalid_model, resolve_model_choice
from wth_ai.llm.router import LLMProviderRouter


def run_smoke_test(
    config: WTHConfig,
    provider_type: str,
    *,
    model: str | None = None,
    do_completion: bool = True,
    router: LLMProviderRouter | None = None,
    completion_timeout: int = 30,
) -> dict[str, Any]:
    """Run minimal provider checks and return a structured report.

    Steps: secret readable, provider reachable, model listing, short completion,
    role models valid, fallback valid, error normalization.
    """
    router = router or LLMProviderRouter(config)
    steps: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: Any = None) -> None:
        steps.append({"name": name, "ok": bool(ok), "detail": detail})

    # 1. Secret readable (ollama is local, no key needed).
    key = router.provider_key(provider_type)
    key_ok = provider_type == "ollama" or bool(key)
    add("secret_readable", key_ok, "local provider" if provider_type == "ollama" else None)

    # 2-4. Reachability + model listing + short completion.
    probe = router.test_provider(provider_type, None)
    add("provider_reachable", probe["reachable"], probe.get("reach_error"))

    models: list[str] = []
    try:
        models = router.list_models(provider_type)
        probe["models_listed"] = len(models)
    except Exception:
        probe["models_listed"] = probe.get("models_listed", 0)
    add("model_list", probe["models_listed"] > 0, {"count": probe["models_listed"]})

    completion_model = model
    if model and models:
        if is_likely_invalid_model(model, models):
            resolved = resolve_model_choice(model, models)
            if resolved in models:
                completion_model = resolved
            else:
                completion_model = models[0]
        elif model not in models:
            completion_model = models[0]

    if do_completion and completion_model:
        comp = router.test_provider(provider_type, completion_model, completion_timeout=completion_timeout)
        add("completion", comp["completion_ok"], comp.get("error"))
        if model and completion_model != model:
            steps[-1]["detail"] = {
                **(steps[-1].get("detail") or {}),
                "configured_model": model,
                "probed_model": completion_model,
            }

    # 5. Role models valid (non-empty and not a mistaken list index).
    missing_roles: list[str] = []
    invalid_roles: list[str] = []
    for r in ROLE_NAMES:
        rc = router.role_config(r)
        if not rc.model:
            missing_roles.append(r)
            continue
        try:
            catalog = router.list_models(rc.type)
        except Exception:
            catalog = []
        if catalog and is_likely_invalid_model(rc.model, catalog):
            invalid_roles.append(r)
    add(
        "role_models_valid",
        not missing_roles and not invalid_roles,
        {"missing": missing_roles, "invalid_index": invalid_roles},
    )

    # 6. Fallback model valid.
    fb = router.role_config("fallback").model
    fb_ok = bool(fb)
    try:
        fbc = router.role_config("fallback")
        catalog = router.list_models(fbc.type)
        if catalog and is_likely_invalid_model(fb, catalog):
            fb_ok = False
    except Exception:
        pass
    add("fallback_valid", fb_ok, {"model": fb})

    # 7. Error normalization shape is well-formed when present.
    comp_step = next((s for s in steps if s["name"] == "completion"), None)
    err = comp_step.get("detail") if comp_step and isinstance(comp_step.get("detail"), dict) else None
    if isinstance(err, dict) and "type" in err:
        err_norm_ok = True
    else:
        err = comp_step.get("detail") if comp_step else probe.get("reach_error")
        err_norm_ok = err is None or (isinstance(err, dict) and "type" in err and "message" in err)
    add("error_normalized", err_norm_ok, err)

    ok = all(s["ok"] for s in steps)
    return {"provider": provider_type, "ok": ok, "steps": steps}


def format_smoke_report(report: dict[str, Any]) -> str:
    lines = [f"## Smoke Test — {report['provider']}: {'PASS' if report['ok'] else 'FAIL'}", ""]
    for s in report["steps"]:
        mark = "✓" if s["ok"] else "✗"
        detail = f" — {s['detail']}" if s.get("detail") not in (None, {}) else ""
        lines.append(f"- {mark} {s['name']}{detail}")
    return "\n".join(lines)

"""Provider smoke test — validate a provider end-to-end before use."""

from __future__ import annotations

from typing import Any

from wth_ai.config import ROLE_NAMES, WTHConfig
from wth_ai.llm.router import LLMProviderRouter


def run_smoke_test(
    config: WTHConfig,
    provider_type: str,
    *,
    model: str | None = None,
    do_completion: bool = True,
    router: LLMProviderRouter | None = None,
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
    probe = router.test_provider(provider_type, model if do_completion else None)
    add("provider_reachable", probe["reachable"], probe.get("error"))
    add("model_list", probe["models_listed"] > 0, {"count": probe["models_listed"]})
    if do_completion and model:
        add("completion", probe["completion_ok"], probe.get("error"))

    # 5. Role models valid (every role resolves to a non-empty model).
    missing_roles = [r for r in ROLE_NAMES if not router.role_config(r).model]
    add("role_models_valid", not missing_roles, {"missing": missing_roles})

    # 6. Fallback model valid.
    fb = router.role_config("fallback").model
    add("fallback_valid", bool(fb), {"model": fb})

    # 7. Error normalization shape is well-formed when present.
    err = probe.get("error")
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

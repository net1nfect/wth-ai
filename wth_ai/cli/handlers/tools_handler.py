"""`/tools` slash handler."""

from __future__ import annotations

from typing import Any


def handle_tools(orch: Any, args: list[str]) -> str:
    sub = args[0] if args else "list"
    if sub == "list":
        try:
            from wth_ai.recon.registry import ReconToolRegistry
            reg = ReconToolRegistry()
            return reg.format_list()[:8000]
        except Exception:
            return orch.tool_registry.format_report()
    if sub == "doctor":
        rec = orch.doctor.check_recon()
        return f"Recon doctor: ok={rec.get('ok')} registry={rec.get('registry_count')}"
    if sub == "show" and len(args) > 1:
        try:
            from wth_ai.recon.registry import ReconToolRegistry
            spec = ReconToolRegistry().get(args[1])
            if spec:
                return f"## {spec.name}\n- category: {spec.category}\n- risk: {spec.risk_level}\n- mode: {spec.execution_mode}\n- {spec.notes}"
        except Exception:
            pass
        return orch.tool_registry.format_report(args[1])
    if sub == "enable" and len(args) > 1:
        from wth_ai.recon.registry import ReconToolRegistry
        ReconToolRegistry().enable(args[1])
        return f"Enabled: {args[1]}"
    if sub == "disable" and len(args) > 1:
        from wth_ai.recon.registry import ReconToolRegistry
        ReconToolRegistry().disable(args[1])
        return f"Disabled: {args[1]}"
    if sub == "install":
        return orch._handle_tools(args[1:])
    if sub == "available":
        from wth_ai.recon.registry import ReconToolRegistry
        reg = ReconToolRegistry()
        lines = ["## Available recon tools", ""]
        for name, spec in sorted(reg.tools.items()):
            if reg.is_available(name):
                lines.append(f"- {name} ({spec.category})")
        return "\n".join(lines[:200])
    if sub == "missing":
        from wth_ai.recon.registry import ReconToolRegistry
        reg = ReconToolRegistry()
        lines = ["## Missing recon tools", ""]
        for name, spec in sorted(reg.tools.items()):
            if not reg.is_available(name):
                hint = reg.install_hint(name) if hasattr(reg, "install_hint") else ""
                lines.append(f"- {name} [{spec.category}] {hint}".rstrip())
        return "\n".join(lines[:300])
    return orch._handle_tools(args)

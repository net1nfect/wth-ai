"""`/skills` slash handler + skill hub."""

from __future__ import annotations

from typing import Any

from wth_ai.skills.hub import SkillHub


def handle_skills(orch: Any, args: list[str]) -> str:
    hub = SkillHub(orch.config, orch.skills)
    sub = args[0] if args else "list"
    if sub == "list":
        cat = args[1] if len(args) > 1 else None
        return hub.list_text(category=cat)
    if sub == "search" and len(args) > 1:
        return hub.search_text(" ".join(args[1:]))
    if sub == "install" and len(args) > 1:
        return hub.install(" ".join(args[1:]))
    if sub == "use" and len(args) > 1:
        return hub.use(orch, " ".join(args[1:]))
    if sub == "doctor":
        return hub.doctor()
    if sub == "sync" and len(args) > 1 and args[1] == "9router":
        force = "--force" in args
        return hub.sync_9router(force=force)
    return hub.list_text()

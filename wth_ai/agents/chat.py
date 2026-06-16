"""Natural conversation agent — chat mode like modern AI CLI."""

from __future__ import annotations

import json
from typing import Any

from wth_ai.agents import BaseLLMAgent, load_prompt
from wth_ai.config import WTHConfig
from wth_ai.models import SessionState
from wth_ai.llm.router import LLMProviderRouter
from wth_ai.skills.registry import SkillRegistry
from wth_ai.skills.router import SkillRouter


class ChatAgent(BaseLLMAgent):
    """Handle casual conversation and capability questions."""

    def __init__(
        self,
        config: WTHConfig,
        client: LLMProviderRouter,
        skills: SkillRegistry,
    ) -> None:
        super().__init__(config, client)
        self.system_prompt = load_prompt(config, "chat_system.txt")
        self.skill_router = SkillRouter(skills)

    def respond(self, user_input: str, state: SessionState) -> dict[str, Any]:
        active_skills = self.skill_router.route(user_input, limit=2)
        skill_ctx = self.skill_router.build_context(active_skills, max_chars=8000)

        context = "\n".join([
            f"Session: {state.session_id}",
            f"Permission: {state.permission_mode.value}",
            f"Target: {state.scope.target or 'belum diset'}",
            f"Environment: {state.scope.environment}",
            f"Authorized: {state.scope.authorized}",
            f"Phase: {state.current_phase.value}",
            f"Skills loaded: {len(self.skill_router.registry.skills)}",
            f"Active skills: {', '.join(s.id for s in active_skills)}",
            skill_ctx,
        ])

        prompt = f"{context}\n\nUser: {user_input}"
        raw, model = self._call(
            self.config.models.planner,
            self.config.models.planner_backup,
            self.system_prompt,
            state.conversation,
            prompt,
        )
        return {
            "raw": raw,
            "model": model,
            "skills_used": [s.id for s in active_skills],
            "mode": "chat",
        }

    def capabilities_summary(self, skills: SkillRegistry) -> str:
        total = len(skills.skills)
        categories = """
**WTH-AI v2.0 — Cybersecurity AI CLI**

Saya dual-agent security research assistant:

| Otak | Model | Fungsi |
|------|-------|--------|
| Planner | Nemotron 3 Ultra | Recon, hipotesis, triage, planning |
| Executor | Qwen3 Coder | Command, tools, browser, parsing |

**Testing yang bisa dilakukan:**
- Static analysis (route, middleware, IDOR logic, mass assignment)
- 10-phase recon (subdomain → secrets → cloud → fingerprint)
- Dynamic testing (permission-gated): IDOR, auth, headers, API
- Browser deep testing + Cloudflare bypass (nodriver)
- Login/register flow dengan test account
- Dependency audit (CVE reachability)
- 118+ security skills (XSS, SSRF, SQLi, JWT, GraphQL, AD, K8s, ...)

**Permission:** `/permission read_only|standard|elevated|full_lab`

**Mulai testing:**
```
/scope set target=yourdomain.com authorized=yes environment=staging
/permission standard
/plan
/recon
/execute
```

**Skills:** `/skills` atau `/skill xss-cross-site-scripting`

Ketik apa saja — saya bisa chat normal juga. Mau mulai dari mana?
"""
        return categories.replace("{total}", str(total))

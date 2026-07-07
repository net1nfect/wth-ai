"""Route user queries to relevant cybersecurity skills."""

from __future__ import annotations

import re
from typing import Any

from wth_ai.skills.registry import Skill, SkillRegistry

INTENT_SKILL_MAP: dict[str, list[str]] = {
    "recon": [
        "recon-and-methodology", "recon-for-sec", "api-recon-and-docs",
        "insecure-source-code-management", "9router-web-search", "9router-web-fetch",
    ],
    "xss": ["xss-cross-site-scripting", "csp-bypass-advanced", "dangling-markup-injection"],
    "sqli": ["sqli-sql-injection", "nosql-injection", "ghost-bits-cast-attack"],
    "ssrf": ["ssrf-server-side-request-forgery", "dns-rebinding-attacks"],
    "idor": ["idor-broken-object-authorization", "api-authorization-and-bola"],
    "auth": ["auth-sec", "authbypass-authentication-flaws", "jwt-oauth-token-attacks", "oauth-oidc-misconfiguration"],
    "api": ["api-sec", "graphql-and-hidden-parameters", "api-auth-and-jwt-abuse"],
    "business": ["business-logic-vuln", "business-logic-vulnerabilities", "race-condition"],
    "cloud": ["kubernetes-pentesting", "unauthorized-access-common-services", "subdomain-takeover"],
    "waf": ["waf-bypass-techniques", "ghost-bits-cast-attack"],
    "llm": ["llm-prompt-injection", "ai-ml-security", "9router", "9router-chat"],
    "upload": ["upload-insecure-files", "file-access-vuln"],
    "smuggling": ["request-smuggling", "http2-specific-attacks"],
    "ctf": ["solve-challenge", "ctf-web", "ctf-pwn", "ctf-crypto"],
    "report": ["bug-bounty-deep-testing"],
}


class SkillRouter:
    """Select skills to inject into agent context."""

    def __init__(self, registry: SkillRegistry) -> None:
        self.registry = registry

    def route(self, user_input: str, limit: int = 3) -> list[Skill]:
        text = user_input.lower()
        selected: list[Skill] = []
        seen: set[str] = set()

        for intent, skill_ids in INTENT_SKILL_MAP.items():
            if intent in text or any(k in text for k in intent.split()):
                for sid in skill_ids:
                    if sid not in seen and (s := self.registry.get(sid)):
                        selected.append(s)
                        seen.add(sid)

        if "/skill " in text:
            m = re.search(r"/skill\s+([\w-]+)", text)
            if m and (s := self.registry.get(m.group(1))):
                if s.id not in seen:
                    selected.insert(0, s)
                    seen.add(s.id)

        if len(selected) < limit:
            for s in self.registry.search(user_input, limit=limit):
                if s.id not in seen:
                    selected.append(s)
                    seen.add(s.id)
                if len(selected) >= limit:
                    break

        if not selected and (hack := self.registry.get("hack")):
            selected.append(hack)

        return selected[:limit]

    def build_context(self, skills: list[Skill], max_chars: int = 24000) -> str:
        if not skills:
            return ""
        parts = ["=== ACTIVE SKILLS ==="]
        budget = max_chars
        for skill in skills:
            content = skill.load_content(min(8000, budget))
            block = f"\n--- SKILL: {skill.id} ({skill.name}) ---\n{content}\n"
            if len(block) > budget:
                block = block[:budget]
            parts.append(block)
            budget -= len(block)
            if budget <= 0:
                break
        parts.append("=== END SKILLS ===")
        return "\n".join(parts)

    def detect_intent(self, user_input: str) -> str:
        text = user_input.strip().lower()
        if text.startswith("/"):
            return "command"
        chat_patterns = [
            r"^(hi|halo|hallo|hello|hey|hai)\b",
            r"^(apa|what|bagaimana|how|bisa|can you|tolong|help)\b",
            r"skills?\b",
            r"testing apa",
            r"siapa kamu",
            r"terima kasih|thanks",
        ]
        for pat in chat_patterns:
            if re.search(pat, text):
                return "chat"
        assess_patterns = [
            r"\b(audit|scan|recon|test|pentest|assess|cve|vuln|exploit|idor|xss|sqli)\b",
            r"^(plan|hunt|recon|execute|verify|report|graph)\b",
            r"\b(asset\s+graph|attack\s+surface|findings?)\b",
            r"/(plan|recon|static|dynamic|execute|verify|report)\b",
        ]
        for pat in assess_patterns:
            if re.search(pat, text):
                return "assess"
        return "chat"

"""LLM agent base utilities."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Callable

from wth_ai.config import WTHConfig
from wth_ai.llm.errors import LLMUnavailableError
from wth_ai.llm.router import LLMProviderRouter


def load_agent_constitution(root: Path | None = None) -> str:
    """Load AGENT.md constitution from project root."""
    base = root or Path(__file__).resolve().parents[2]
    path = base / "AGENT.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def load_prompt(config: WTHConfig, name: str) -> str:
    path = Path(config.prompts_dir) / name
    body = path.read_text(encoding="utf-8") if path.exists() else ""
    constitution = load_agent_constitution(Path(config.prompts_dir).parent if config.prompts_dir else None)
    if constitution and body:
        return f"{constitution}\n\n---\n\n{body}"
    return constitution or body


def parse_blocks(text: str, block_name: str) -> list[dict[str, str]]:
    """Parse structured blocks like EXECUTOR_TASK: from LLM output."""
    pattern = rf"{block_name}:\s*\n(.*?)(?=\n[A-Z][A-Z_]+:|\Z)"
    matches = re.findall(pattern, text, re.DOTALL | re.IGNORECASE)
    results = []
    for match in matches:
        block: dict[str, str] = {}
        current_key = ""
        for line in match.splitlines():
            if re.match(r"^-\s+\w+.*:", line):
                key_part = line.lstrip("- ").split(":", 1)
                current_key = key_part[0].strip().lower().replace(" ", "_")
                block[current_key] = key_part[1].strip() if len(key_part) > 1 else ""
            elif line.strip().startswith("- ") and current_key:
                block[current_key] = (block.get(current_key, "") + "\n" + line.strip()).strip()
            elif current_key and line.strip():
                block[current_key] = (block.get(current_key, "") + "\n" + line.strip()).strip()
        if block:
            results.append(block)
    return results


def build_messages(system: str, history: list[dict[str, str]], user: str) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": system}]
    messages.extend(history[-20:])
    messages.append({"role": "user", "content": user})
    return messages


class BaseLLMAgent:
    def __init__(self, config: WTHConfig, client: LLMProviderRouter) -> None:
        self.config = config
        self.client = client

    def _call(
        self,
        model: str,
        backup: str,
        system: str,
        history: list[dict[str, str]],
        user: str,
        *,
        role: str,
        timeout: int | None = None,
        on_token: Callable[[str], None] | None = None,
    ) -> tuple[str, str]:
        messages = build_messages(system, history, user)
        if os.environ.get("WTH_TEST_MODE") == "1" and not self.client.available():
            return self._offline_stub(system, user), "offline"
        if self.config.realtime_only and self.config.require_llm:
            self.client.require_available()
        if self.client.available():
            use_stream = bool(on_token and self.config.models.streaming)
            return self.client.chat_for_role(
                role,
                messages,
                timeout=timeout,
                stream=use_stream,
                on_token=on_token,
            )
        if self.config.realtime_only:
            raise LLMUnavailableError(
                "LLM tidak tersedia. Jalankan `wth-ai setup` untuk konfigurasi API key."
            )
        return self._offline_stub(system, user), "offline"

    def _offline_stub(self, system: str, user: str) -> str:
        return (
            "LLM offline — set OPENROUTER_API_KEY di .env\n\n"
            "WTH-AI tetap bisa: /help /skills /recon /execute\n"
            f"Pertanyaan: {user[:200]}"
        )

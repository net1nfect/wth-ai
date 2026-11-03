"""Canonical LLM request/response envelope (WU-109, PRD-013).

`LLMProviderRouter.generate(request: ModelRequest) -> ModelResponse` is the
canonical entry point this module supports -- see `wth_ai.llm.router` for
the implementation and its docstring for the strangler-fig scope decision
(facade over `chat_for_role()`/`chat_with_tools_for_role()`, not the
reverse -- documented in `docs/planning/WORK-UNITS.md` WU-109).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ModelRequest:
    role: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] = "auto"
    response_format: dict[str, Any] | None = None
    timeout: int | None = None
    max_tokens: int | None = None
    stream: bool = False
    on_token: Callable[[str], None] | None = None


@dataclass
class ModelResponse:
    status: str  # "success" | "error"
    provider: str
    model: str
    role: str | None = None
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    used_backup: bool = False
    error: dict[str, Any] | None = None
    raw: Any = None

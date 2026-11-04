"""Canonical request/response types for the LLM layer (WU-109, PRD-013).

`LLMProviderRouter.generate()` is the strangler-fig entry point: it takes a
:class:`ModelRequest` and always returns a :class:`ModelResponse` -- including
on failure, where the error is normalized into `ModelResponse.error` instead of
raised. `complete()` on every provider returns the same envelope shape
(``provider``/``model``/``role``/``status``/``content``/``usage``/
``tool_calls``/``error``), so the two are deliberately interchangeable.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable

STATUS_SUCCESS = "success"
STATUS_ERROR = "error"


@dataclass
class ModelRequest:
    """A single LLM call, independent of provider and transport."""

    role: str = "planner"
    messages: list[dict[str, Any]] = field(default_factory=list)
    #: OpenAI-style tool schemas. Empty means a plain text completion.
    tools: list[dict[str, Any]] = field(default_factory=list)
    timeout: int | None = None
    stream: bool = False
    on_token: Callable[[str], None] | None = None
    response_format: dict[str, Any] | None = None
    temperature: float | None = None
    max_tokens: int | None = None

    def __post_init__(self) -> None:
        if self.messages is None:
            self.messages = []
        if self.tools is None:
            self.tools = []

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # A callback is not serializable and has no meaning outside this process.
        data.pop("on_token", None)
        return data


@dataclass
class ModelResponse:
    """The result of a :class:`ModelRequest`. Never an exception on the wire."""

    status: str = STATUS_ERROR
    provider: str = ""
    model: str = ""
    role: str = ""
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    #: True when the primary model failed and a backup answered.
    used_backup: bool = False

    @property
    def ok(self) -> bool:
        return self.status == STATUS_SUCCESS

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_envelope(cls, envelope: dict[str, Any], *, role: str = "") -> "ModelResponse":
        """Build from a provider ``complete()`` envelope."""
        envelope = envelope or {}
        return cls(
            status=envelope.get("status", STATUS_ERROR),
            provider=envelope.get("provider", ""),
            model=envelope.get("model", ""),
            role=envelope.get("role") or role,
            content=envelope.get("content") or "",
            tool_calls=list(envelope.get("tool_calls") or []),
            usage=dict(envelope.get("usage") or {}),
            raw=envelope,
            error=envelope.get("error"),
        )

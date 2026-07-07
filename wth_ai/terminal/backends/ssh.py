"""SSH terminal backend stub — deferred full implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SshTerminalBackend:
    host: str = ""
    name: str = "ssh"

    def run(self, command: list[str], **kwargs: Any) -> dict[str, Any]:
        return {"backend": self.name, "error": "ssh backend not configured", "exit_code": 127}

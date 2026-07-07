"""Terminal backend registry."""

from wth_ai.terminal.backends.local import LocalTerminalBackend
from wth_ai.terminal.backends.docker import DockerTerminalBackend

__all__ = ["LocalTerminalBackend", "DockerTerminalBackend"]

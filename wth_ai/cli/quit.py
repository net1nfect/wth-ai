"""Session quit signal for interactive CLI."""

from __future__ import annotations


class SessionQuit(Exception):
    """Raised when the user requests exit; carries a farewell message."""

    def __init__(self, message: str = "bye") -> None:
        self.message = message
        super().__init__(message)

"""Error / recovery handling (Fase 6, flowchart ERR + C11F)."""

from wth_ai.recovery.controller import (
    ERROR_TYPES,
    RecoveryController,
    classify_error,
)

__all__ = ["ERROR_TYPES", "RecoveryController", "classify_error"]

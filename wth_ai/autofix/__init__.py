"""Auto-fix PR berbatas (izin berbatas §1.2 poin 2)."""

from __future__ import annotations

from .apply import ApplyResult, apply_patch
from .planner import AutofixUnavailable, Patch, plan_fix

__all__ = ["ApplyResult", "AutofixUnavailable", "Patch", "apply_patch", "plan_fix"]

"""Engagement mode — CTF vs bug bounty (label/tuning only, no safety blocking)."""

from __future__ import annotations

import os
from enum import Enum

from wth_ai.config import WTHConfig


class EngagementMode(str, Enum):
    BUG_BOUNTY = "bug_bounty"
    CTF = "ctf"
    DEFAULT = "default"


_UNRESTRICTED_ENV = "WTH_UNRESTRICTED"
_mode: EngagementMode = EngagementMode.DEFAULT
_unrestricted: bool = os.environ.get(_UNRESTRICTED_ENV, "0") == "1"


def set_engagement_mode(mode: EngagementMode | str) -> None:
    global _mode
    _mode = EngagementMode(mode) if isinstance(mode, str) else mode


def get_engagement_mode() -> EngagementMode:
    return _mode


def is_unrestricted(config: WTHConfig | None = None) -> bool:
    if os.environ.get(_UNRESTRICTED_ENV) == "0":
        return False
    if os.environ.get(_UNRESTRICTED_ENV) == "1":
        return True
    if config is not None and getattr(config, "safety", None):
        return bool(config.safety.unrestricted)
    return _unrestricted


def set_unrestricted(enabled: bool = True) -> None:
    global _unrestricted
    _unrestricted = enabled
    os.environ[_UNRESTRICTED_ENV] = "1" if enabled else "0"

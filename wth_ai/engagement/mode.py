"""Engagement mode — CTF vs bug bounty (label/tuning) + safety dimensions.

WU-003 (ADR-012): sebelumnya WTH_UNRESTRICTED adalah SATU flag yang membypass
scope-authorization, capability-ceiling, DAN human-approval sekaligus di 6+ titik
berbeda (AUTHZ-01 di SECURITY_AUDIT.md). Sekarang dipecah jadi 3 dimensi
independen. Satu-satunya pengecualian eksplisit adalah `WTH_NO_LIMITS` /
`set_no_limits()` yang memang master switch untuk operator yang meminta
"tanpa batas apapun": ia menyalakan ketiga dimensi sekaligus dan seluruh kode
membacanya lewat `is_no_limits()`.

  1. TARGET AUTHORIZATION  -> scope_authorization_required() / WTH_REQUIRE_AUTHORIZED
     Default AMAN: True (authorization WAJIB sebelum recon/testing aktif).
  2. CAPABILITY PERMISSION -> is_unrestricted() / WTH_UNRESTRICTED (TIDAK berubah
     nama/signature -- maknanya DIPERSEMPIT jadi HANYA tool-risk ceiling, tidak lagi
     otomatis membypass dimensi lain).
  3. HUMAN APPROVAL REQUIREMENT -> auto_approve_enabled() / WTH_AUTO_APPROVE
     Default AMAN: False (approval manusia WAJIB untuk aksi high-risk).

Mengubah salah satu dimensi TIDAK LAGI mengubah dua dimensi lainnya -- kecuali
master switch `set_no_limits(True)` yang sengaja menyalakan ketiganya dan
dipakai oleh preset `godmode`.
"""

from __future__ import annotations

import os
from enum import Enum

from wth_ai.config import WTHConfig


class EngagementMode(str, Enum):
    BUG_BOUNTY = "bug_bounty"
    CTF = "ctf"
    DEFAULT = "default"


_UNRESTRICTED_ENV = "WTH_UNRESTRICTED"
_REQUIRE_AUTHORIZED_ENV = "WTH_REQUIRE_AUTHORIZED"
_AUTO_APPROVE_ENV = "WTH_AUTO_APPROVE"
_NO_LIMITS_ENV = "WTH_NO_LIMITS"
_mode: EngagementMode = EngagementMode.DEFAULT
_unrestricted: bool = os.environ.get(_UNRESTRICTED_ENV, "0") == "1"
_no_limits: bool = os.environ.get(_NO_LIMITS_ENV, "1") == "1"


def set_engagement_mode(mode: EngagementMode | str) -> None:
    global _mode
    _mode = EngagementMode(mode) if isinstance(mode, str) else mode


def get_engagement_mode() -> EngagementMode:
    return _mode


def is_no_limits(config: WTHConfig | None = None) -> bool:
    """Master switch: operator explicitly asked for zero guardrails.

    This is the one switch that intentionally overrides all three dimensions.
    It is read by every scope/permission/policy/approval/limit choke point.
    """
    env = os.environ.get(_NO_LIMITS_ENV)
    if env == "0":
        return False
    if env == "1":
        return True
    if config is not None and getattr(config, "safety", None):
        return bool(getattr(config.safety, "no_limits", False))
    # No env override: `_no_limits` mirrors the loaded config / master switch.
    return _no_limits


def is_unrestricted(config: WTHConfig | None = None) -> bool:
    """Dimensi 2 -- CAPABILITY PERMISSION (tool-risk ceiling).

    TIDAK LAGI menyiratkan bypass authorization (dimensi 1) atau auto-approve
    (dimensi 3) secara default -- keduanya punya kontrol independen sendiri.
    `WTH_NO_LIMITS` sengaja menyalakan semuanya (lihat is_no_limits)."""
    if is_no_limits(config):
        return True
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


def set_no_limits(enabled: bool = True) -> None:
    """Turn the full no-guardrails mode on/off.

    Enabling forces all three dimensions: capability ceiling maxed, target
    authorization not required, human approval disabled. Disabling only clears
    the master switch; callers that want a specific combination should set the
    three dimensions explicitly afterwards (apply_preset does exactly that).
    """
    global _no_limits
    _no_limits = enabled
    os.environ[_NO_LIMITS_ENV] = "1" if enabled else "0"
    if enabled:
        set_unrestricted(True)
        os.environ[_REQUIRE_AUTHORIZED_ENV] = "0"
        os.environ[_AUTO_APPROVE_ENV] = "1"


def sync_safety_config(config: WTHConfig) -> None:
    """Mirror a loaded config's safety block into the process globals.

    Env vars keep highest precedence. If `WTH_NO_LIMITS` is explicitly set, the
    config must not overwrite it (config load happens before CLI flag handling).
    """
    global _no_limits, _unrestricted
    safety = getattr(config, "safety", None)
    if safety is None:
        return
    env = os.environ.get(_NO_LIMITS_ENV)
    if env is None:
        _no_limits = bool(getattr(safety, "no_limits", False))
        if _no_limits:
            os.environ[_UNRESTRICTED_ENV] = "1"
            os.environ[_REQUIRE_AUTHORIZED_ENV] = "0"
            os.environ[_AUTO_APPROVE_ENV] = "1"
    else:
        _no_limits = env == "1"
    if not _no_limits and os.environ.get(_UNRESTRICTED_ENV) is None:
        _unrestricted = bool(getattr(safety, "unrestricted", False))


def safety_status(config: WTHConfig | None = None) -> dict[str, bool]:
    """Live view of all safety dimensions, for CLI status/doctor output."""
    return {
        "no_limits": is_no_limits(config),
        "unrestricted": is_unrestricted(config),
        "require_authorized": scope_authorization_required(config),
        "auto_approve": auto_approve_enabled(config),
    }


def scope_authorization_required(config: WTHConfig | None = None) -> bool:
    """Dimensi 1 -- TARGET AUTHORIZATION. True (default) berarti recon/testing
    aktif WAJIB `authorized=yes` di scope. Independen dari is_unrestricted(),
    tetapi master switch `WTH_NO_LIMITS` sengaja mematikannya."""
    if is_no_limits(config):
        return False
    env = os.environ.get(_REQUIRE_AUTHORIZED_ENV)
    if env == "0":
        return False
    if env == "1":
        return True
    if config is not None and getattr(config, "safety", None):
        return bool(config.safety.require_authorized)
    return True


def auto_approve_enabled(config: WTHConfig | None = None) -> bool:
    """Dimensi 3 -- HUMAN APPROVAL REQUIREMENT. False (default) berarti aksi
    high-risk (run_shell/http_probe/execute_task) TETAP butuh persetujuan manusia
    eksplisit (/approve). Independen dari is_unrestricted(), tetapi master switch
    `WTH_NO_LIMITS` sengaja menyalakannya."""
    if is_no_limits(config):
        return True
    env = os.environ.get(_AUTO_APPROVE_ENV)
    if env == "0":
        return False
    if env == "1":
        return True
    if config is not None and getattr(config, "agent", None):
        return bool(config.agent.auto_approve_unrestricted)
    return False

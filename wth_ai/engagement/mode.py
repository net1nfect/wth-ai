"""Engagement mode — CTF vs bug bounty (label/tuning) + 3 dimensi safety independen.

WU-003 (ADR-012): sebelumnya WTH_UNRESTRICTED adalah SATU flag yang membypass
scope-authorization, capability-ceiling, DAN human-approval sekaligus di 6+ titik
berbeda (AUTHZ-01 di SECURITY_AUDIT.md). Sekarang dipecah jadi 3 dimensi
independen -- TIDAK ADA satu switch pun yang menyentuh ketiganya:

  1. TARGET AUTHORIZATION  -> scope_authorization_required() / WTH_REQUIRE_AUTHORIZED
     Default AMAN: True (authorization WAJIB sebelum recon/testing aktif).
  2. CAPABILITY PERMISSION -> is_unrestricted() / WTH_UNRESTRICTED (TIDAK berubah
     nama/signature -- maknanya DIPERSEMPIT jadi HANYA tool-risk ceiling, tidak lagi
     otomatis membypass dimensi lain).
  3. HUMAN APPROVAL REQUIREMENT -> auto_approve_enabled() / WTH_AUTO_APPROVE
     Default AMAN: False (approval manusia WAJIB untuk aksi high-risk).

Mengubah salah satu dimensi TIDAK LAGI mengubah dua dimensi lainnya.
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
_mode: EngagementMode = EngagementMode.DEFAULT
_unrestricted: bool = os.environ.get(_UNRESTRICTED_ENV, "0") == "1"


def set_engagement_mode(mode: EngagementMode | str) -> None:
    global _mode
    _mode = EngagementMode(mode) if isinstance(mode, str) else mode


def get_engagement_mode() -> EngagementMode:
    return _mode


def is_unrestricted(config: WTHConfig | None = None) -> bool:
    """Dimensi 2 -- CAPABILITY PERMISSION (tool-risk ceiling) SAJA.

    TIDAK LAGI menyiratkan bypass authorization (dimensi 1) atau auto-approve
    (dimensi 3) -- keduanya sekarang punya kontrol independen sendiri, lihat
    scope_authorization_required() dan auto_approve_enabled()."""
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


def scope_authorization_required(config: WTHConfig | None = None) -> bool:
    """Dimensi 1 -- TARGET AUTHORIZATION. True (default) berarti recon/testing
    aktif WAJIB `authorized=yes` di scope. Independen total dari is_unrestricted()
    -- mengaktifkan capability ceiling paling permisif TIDAK membuat fungsi ini
    ikut return False."""
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
    eksplisit (/approve). Independen total dari is_unrestricted()."""
    env = os.environ.get(_AUTO_APPROVE_ENV)
    if env == "0":
        return False
    if env == "1":
        return True
    if config is not None and getattr(config, "agent", None):
        return bool(config.agent.auto_approve_unrestricted)
    return False

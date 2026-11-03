"""Permission presets — one word for a coherent safety policy.

Why a preset layer instead of more enum values
----------------------------------------------
`PermissionMode` is a single axis (how risky may the TOOL be). The safety model
in `wth_ai/engagement/mode.py` actually has THREE independent dimensions:

  1. target authorization  (`scope_authorization_required`)
  2. capability ceiling    (`is_unrestricted`)
  3. human approval        (`auto_approve_enabled`)

An operator running an AI CLI expects to type one word ("yolo", "auto", "ask")
and get a coherent combination, not to flip three switches in the right order.
This module maps those words onto the dimensions and applies them together, so
the modes cannot be set into a half-configured state (e.g. prompts off but
capability still low, which is the most confusing possible outcome).

Deliberate design decision: `yolo` turns OFF the approval prompts and RAISES the
capability ceiling, but KEEPS target authorization. That dimension is not
friction about *how* to test -- it is the record of *what the operator said they
may test*. Dropping it would let a typo or a stale asset graph point the tool at
a host nobody authorized, and would leave an engagement with no authorisation
record at all. `WTH_REQUIRE_AUTHORIZED=0` remains the explicit escape hatch for
a lab where that record genuinely does not matter.
"""

from __future__ import annotations

from dataclasses import dataclass

from wth_ai.engagement.mode import set_unrestricted
from wth_ai.models import PermissionMode


@dataclass(frozen=True)
class Preset:
    name: str
    permission: PermissionMode
    unrestricted: bool
    auto_approve: bool
    scope_authorization_required: bool
    description: str

    @property
    def summary(self) -> str:
        capability = "max (risk tinggi boleh)" if self.unrestricted else "aman saja"
        approval = "TANPA konfirmasi" if self.auto_approve else "konfirmasi untuk aksi berisiko"
        scope = "wajib authorized" if self.scope_authorization_required else "TIDAK wajib"
        return f"tool={self.permission.value} · capability={capability} · approval={approval} · scope={scope}"


PRESETS: dict[str, Preset] = {
    "plan": Preset(
        "plan", PermissionMode.READ_ONLY_RECON, False, False, True,
        "Hanya baca & rencana — tidak ada aksi yang mengubah apa pun.",
    ),
    "read-only": Preset(
        "read-only", PermissionMode.READ_ONLY_RECON, False, False, True,
        "Recon pasif + tool baca saja.",
    ),
    "ask": Preset(
        "ask", PermissionMode.STANDARD_TESTING, False, False, True,
        "Default aman: aksi berisiko minta konfirmasi dulu.",
    ),
    "auto": Preset(
        "auto", PermissionMode.ELEVATED_APPROVAL, False, True, True,
        "Jalan sendiri: konfirmasi dimatikan, tapi capability masih dibatasi.",
    ),
    "yolo": Preset(
        "yolo", PermissionMode.UNRESTRICTED, True, True, True,
        "Tanpa konfirmasi + capability maksimal. Target tetap harus authorized.",
    ),
}

# Accepted spellings -> canonical preset name.
ALIASES: dict[str, str] = {
    "plan": "plan", "planning": "plan", "dry-run": "plan", "dryrun": "plan",
    "read": "read-only", "readonly": "read-only", "read_only": "read-only", "ro": "read-only",
    "ask": "ask", "default": "ask", "safe": "ask", "normal": "ask",
    "auto": "auto", "autopilot": "auto", "accept": "auto", "auto-accept": "auto",
    "yolo": "yolo", "unrestricted": "yolo", "danger": "yolo", "full": "yolo",
    "full_lab": "yolo", "full-lab": "yolo", "standard": "ask", "elevated": "auto",
}


def resolve_preset(name: str) -> Preset:
    """Resolve a user-typed mode name to a preset. Raises ValueError with the
    valid list so a typo is actionable."""
    key = (name or "").strip().lower()
    canonical = ALIASES.get(key, key)
    preset = PRESETS.get(canonical)
    if preset is None:
        valid = ", ".join(sorted(PRESETS))
        raise ValueError(f"mode '{name}' tidak dikenal. Pilihan: {valid}")
    return preset


def apply_preset(name: str, *, permission_mgr=None, config=None) -> Preset:
    """Apply a preset to the live process (and optionally a PermissionManager).

    The three dimensions are set together and never partially: a half-applied
    preset is worse than either extreme, because the operator cannot tell from
    the prompt which half took effect.
    """
    preset = resolve_preset(name)

    # Dimension 2 (capability ceiling) and 3 (approval) are process-level.
    set_unrestricted(preset.unrestricted)
    _set_auto_approve(preset.auto_approve)

    # Dimension 1 (target authorization) is deliberately NOT forced open by any
    # preset -- see the module docstring. Only an explicit env var lowers it.
    if permission_mgr is not None:
        permission_mgr.set_mode(preset.permission)
    return preset


def _set_auto_approve(enabled: bool) -> None:
    import os

    from wth_ai.engagement.mode import _AUTO_APPROVE_ENV

    os.environ[_AUTO_APPROVE_ENV] = "1" if enabled else "0"


def describe_all() -> str:
    width = max(len(p.name) for p in PRESETS.values())
    lines = ["Mode permission:"]
    for preset in PRESETS.values():
        lines.append(f"  {preset.name:<{width}}  {preset.description}")
        lines.append(f"  {'':<{width}}  {preset.summary}")
    lines.append("")
    lines.append("Alias: " + ", ".join(sorted(k for k in ALIASES if k not in PRESETS)))
    return "\n".join(lines)

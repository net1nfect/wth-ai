"""Shipped permission profiles -- the concrete grant sets behind each mode.

Built from the tool tiers in :mod:`wth_ai.permission_manager` (that module
keeps the canonical ``READ_ONLY_TOOLS``/``STANDARD_TOOLS``/... lists and imports
this one lazily to avoid a cycle), plus two sets this module owns:

* :data:`INTERPRETER_TOOLS` -- anything that can run arbitrary code. Gated
  behind ``REQUIRE_HUMAN`` in **every** profile including ``UNRESTRICTED``:
  the whole point of a profile is to bound blast radius, and an interpreter
  hands that radius to the command line (ADR-009).
* :data:`SAFE_READ_BASELINE` -- ubiquitous read-only inspection commands
  (``uname``, ``id``, ``hostname``, ``ps``, ...). Without them a STANDARD-tier
  agent cannot even fingerprint the host it is allowed to test, which is how
  the pre-fix code forced operators into UNRESTRICTED just to run ``uname``.
"""
from __future__ import annotations

from wth_ai.models import PermissionMode
from wth_ai.permission_manager import (
    ELEVATED_TOOLS,
    FULL_LAB_TOOLS,
    READ_ONLY_TOOLS,
    STANDARD_TOOLS,
)
from wth_ai.policy.capability import (
    ApprovalMode,
    Capability,
    PermissionProfile,
    RiskLevel,
)

#: Tools that execute arbitrary code or start an interactive shell.
INTERPRETER_TOOLS: frozenset[str] = frozenset(
    {
        "python", "python3", "python2", "pypy3",
        "bash", "sh", "dash", "zsh", "ksh", "fish",
        "perl", "ruby", "node", "nodejs", "php", "pwsh", "powershell",
        "osascript", "expect", "tclsh", "lua",
    }
)

#: Read-only inspection commands that carry no write or network side effect.
SAFE_READ_BASELINE: frozenset[str] = frozenset(
    {
        "uname", "whoami", "id", "groups", "hostname", "hostnamectl", "uptime",
        "date", "cal", "pwd", "echo", "printf", "true", "false", "test", "[",
        "env", "printenv", "which", "command", "type", "basename", "dirname",
        "realpath", "readlink", "stat", "file", "wc", "sort", "uniq", "cut",
        "tr", "nl", "od", "xxd", "hexdump", "base64", "md5sum", "sha256sum",
        "sha1sum", "diff", "comm", "join", "column", "tree", "du", "df",
        "free", "lsblk", "lscpu", "lsusb", "lspci", "ps", "pgrep", "top",
        "head", "tail", "less", "more", "grep", "rg", "egrep", "fgrep",
        "sed", "awk", "find", "ls", "cat", "jq", "yq", "sleep", "man", "help",
    }
)

#: Tools whose worst realistic outcome is a full-volume scan or credential
#: attack. They stay available in FULL_LAB/UNRESTRICTED but always ask first.
DESTRUCTIVE_RESOURCES: frozenset[str] = frozenset({"masscan", "zmap", "hydra", "medusa"})


def _grant(name: str, risk: RiskLevel, mode: ApprovalMode = ApprovalMode.AUTO_ALLOW,
           notes: str = "") -> Capability:
    return Capability(name=name, risk=risk, approval_mode=mode, notes=notes)


def _build(
    profile_name: str,
    low: set[str],
    medium: set[str],
    high: set[str],
    *,
    wildcard: Capability | None = None,
) -> PermissionProfile:
    profile = PermissionProfile(name=profile_name, wildcard=wildcard)

    # Interpreter gate binds first: a tool that is also a member of a lower
    # tier (``python`` sits in READ_ONLY_TOOLS) must still be gated.
    for tool in INTERPRETER_TOOLS:
        profile.grant(
            _grant(tool, RiskLevel.HIGH, ApprovalMode.REQUIRE_HUMAN,
                   notes="interpreter: runs arbitrary code")
        )
    for tool in sorted(low):
        if tool in INTERPRETER_TOOLS:
            continue
        profile.grant(_grant(tool, RiskLevel.LOW))
    for tool in sorted(medium):
        if tool in INTERPRETER_TOOLS or tool in low:
            continue
        profile.grant(_grant(tool, RiskLevel.MEDIUM))
    for tool in sorted(high):
        if tool in INTERPRETER_TOOLS or tool in low or tool in medium:
            continue
        destructive = tool in DESTRUCTIVE_RESOURCES
        profile.grant(
            _grant(
                tool,
                RiskLevel.CRITICAL if destructive else RiskLevel.HIGH,
                ApprovalMode.REQUIRE_HUMAN if destructive else ApprovalMode.AUTO_ALLOW,
                notes="high-volume or credential attack surface" if destructive else "",
            )
        )
    return profile


_READ_LOW = READ_ONLY_TOOLS | SAFE_READ_BASELINE
_STANDARD_MEDIUM = (STANDARD_TOOLS - READ_ONLY_TOOLS) - SAFE_READ_BASELINE
_ELEVATED_HIGH = (ELEVATED_TOOLS - STANDARD_TOOLS)
_FULL_HIGH = (FULL_LAB_TOOLS - ELEVATED_TOOLS)


PROFILES: dict[PermissionMode, PermissionProfile] = {
    PermissionMode.READ_ONLY_RECON: _build(
        "read_only_recon", set(_READ_LOW), set(), set(),
    ),
    PermissionMode.STANDARD_TESTING: _build(
        "standard_testing", set(_READ_LOW), set(_STANDARD_MEDIUM), set(),
    ),
    PermissionMode.ELEVATED_APPROVAL: _build(
        "elevated_approval", set(_READ_LOW), set(_STANDARD_MEDIUM), set(_ELEVATED_HIGH),
    ),
    PermissionMode.FULL_LAB: _build(
        "full_lab",
        set(_READ_LOW),
        set(_STANDARD_MEDIUM),
        set(_ELEVATED_HIGH) | set(_FULL_HIGH),
    ),
    PermissionMode.UNRESTRICTED: _build(
        "unrestricted",
        set(_READ_LOW),
        set(_STANDARD_MEDIUM),
        set(_ELEVATED_HIGH) | set(_FULL_HIGH),
        # An unknown tool is granted, but the interpreter gate above still
        # wins for anything that can execute code, and the engine's
        # Action-Policy layer still scans the arguments.
        wildcard=Capability("wildcard", RiskLevel.MEDIUM, ApprovalMode.AUTO_ALLOW),
    ),
}


def profile_for(mode: PermissionMode | str) -> PermissionProfile:
    """Profile for a mode (accepts the enum or its string value)."""
    if isinstance(mode, str):
        mode = PermissionMode(mode)
    return PROFILES[mode]

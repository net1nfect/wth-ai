"""Built-in `PermissionProfile` per `PermissionMode` (ADR-009, WU-005).

1:1 mapping from `wth_ai.permission_manager`'s existing tool sets -- tool
membership is IDENTICAL to before, just wrapped granular (no behavior change
from this mapping alone).

`shell.interpreter_passthrough` is layered onto every profile as the FIRST
capability (ordering = precedence in `PermissionProfile.resolve()`): always
HIGH risk + REQUIRE_HUMAN, including under UNRESTRICTED. This closes the
"UNRESTRICTED = auto-bypass even for bash" gap (ADR-009 SS Why) -- previously
`check_command()` early-returned for UNRESTRICTED before the interpreter
check ever ran.

UNRESTRICTED's catch-all `shell.wildcard_unrestricted` (AUTO) is an
INTERIM state, not the ADR's full target: ADR-009 calls for this to default
`PROHIBITED` unless `Engagement.allowed_operations` explicitly grants it --
that requires the `Engagement` entity/store (WU-101), which does not exist
yet (WU-014 is a skeleton only). Tightening this further is WU-101+/WU-103
scope, not this WU -- implementing it now against non-existent
infrastructure would be hollow architecture.
"""

from __future__ import annotations

from wth_ai.models import PermissionMode
from wth_ai.permission_manager import (
    ELEVATED_TOOLS,
    FULL_LAB_TOOLS,
    READ_ONLY_TOOLS,
    STANDARD_TOOLS,
)
from wth_ai.policy.capability import ApprovalMode, Capability, PermissionProfile, RiskLevel

INTERPRETER_TOOLS: frozenset[str] = frozenset(
    {"bash", "sh", "zsh", "python", "python3", "perl", "ruby", "node"}
)

CAP_INTERPRETER_PASSTHROUGH = Capability(
    name="shell.interpreter_passthrough",
    tools=INTERPRETER_TOOLS,
    risk=RiskLevel.HIGH,
    approval_mode=ApprovalMode.REQUIRE_HUMAN,
)


def _non_interpreter(tools: frozenset[str]) -> frozenset[str]:
    return frozenset(tools) - INTERPRETER_TOOLS


_READ_ONLY = _non_interpreter(READ_ONLY_TOOLS)
_STANDARD_NEW = _non_interpreter(STANDARD_TOOLS) - _READ_ONLY
_ELEVATED_NEW = _non_interpreter(ELEVATED_TOOLS) - _non_interpreter(STANDARD_TOOLS)
_FULL_LAB_NEW = _non_interpreter(FULL_LAB_TOOLS) - _non_interpreter(ELEVATED_TOOLS)

CAP_SHELL_READONLY = Capability(
    name="shell.readonly", tools=_READ_ONLY, risk=RiskLevel.LOW, approval_mode=ApprovalMode.AUTO,
)
CAP_SHELL_ACTIVE_RECON = Capability(
    name="shell.active_recon", tools=_STANDARD_NEW, risk=RiskLevel.MEDIUM, approval_mode=ApprovalMode.NOTIFY,
)
CAP_SHELL_ACTIVE_SCAN_AGGRESSIVE = Capability(
    name="shell.active_scan_aggressive", tools=_ELEVATED_NEW, risk=RiskLevel.HIGH, approval_mode=ApprovalMode.AUTO,
)
CAP_SHELL_EXPLOIT_HEAVY = Capability(
    name="shell.exploit_heavy", tools=_FULL_LAB_NEW, risk=RiskLevel.HIGH, approval_mode=ApprovalMode.AUTO,
)
# ELEVATED/FULL_LAB capabilities stay AUTO within their own ceiling mode --
# matches today's tested behavior (e.g. sqlmap already allowed in FULL_LAB
# without a separate approval step). The Action-Policy layer in
# `policy.engine.evaluate_intent()` (HIGH_RISK_PATTERNS) is the real gate for
# argument-content risk (e.g. "sqlmap ... --dump"), independent of this layer.
CAP_SHELL_WILDCARD_UNRESTRICTED = Capability(
    name="shell.wildcard_unrestricted", tools=frozenset({"*"}), risk=RiskLevel.HIGH, approval_mode=ApprovalMode.AUTO,
)

PROFILES: dict[PermissionMode, PermissionProfile] = {
    PermissionMode.READ_ONLY_RECON: PermissionProfile(
        "read_only_recon", (CAP_INTERPRETER_PASSTHROUGH, CAP_SHELL_READONLY),
    ),
    PermissionMode.STANDARD_TESTING: PermissionProfile(
        "standard_testing",
        (CAP_INTERPRETER_PASSTHROUGH, CAP_SHELL_READONLY, CAP_SHELL_ACTIVE_RECON),
    ),
    PermissionMode.ELEVATED_APPROVAL: PermissionProfile(
        "elevated_approval",
        (
            CAP_INTERPRETER_PASSTHROUGH,
            CAP_SHELL_READONLY,
            CAP_SHELL_ACTIVE_RECON,
            CAP_SHELL_ACTIVE_SCAN_AGGRESSIVE,
        ),
    ),
    PermissionMode.FULL_LAB: PermissionProfile(
        "full_lab",
        (
            CAP_INTERPRETER_PASSTHROUGH,
            CAP_SHELL_READONLY,
            CAP_SHELL_ACTIVE_RECON,
            CAP_SHELL_ACTIVE_SCAN_AGGRESSIVE,
            CAP_SHELL_EXPLOIT_HEAVY,
        ),
    ),
    PermissionMode.UNRESTRICTED: PermissionProfile(
        "unrestricted",
        (
            CAP_INTERPRETER_PASSTHROUGH,
            CAP_SHELL_READONLY,
            CAP_SHELL_ACTIVE_RECON,
            CAP_SHELL_ACTIVE_SCAN_AGGRESSIVE,
            CAP_SHELL_EXPLOIT_HEAVY,
            CAP_SHELL_WILDCARD_UNRESTRICTED,
        ),
    ),
}

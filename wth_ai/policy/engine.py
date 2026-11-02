"""`evaluate_intent()` -- single Policy Engine core (ADR-010, WU-005).

ADR-010 defines 6 hierarchical layers: Kill-switch -> Global-Safety ->
Environment -> Engagement -> Agent-Capability -> Tool-Policy -> Action-Policy
(7 names, 6 conceptual gates), each required to return an EXPLICIT decision
-- no implicit fallthrough to ALLOW anywhere.

Layers implemented NOW:
- Global-Safety (`DENYLIST_PATTERNS`, absolute, checked before anything else
  incl. capability ceiling -- WU-004 BUG-1).
- Tool-Policy (`Capability` resolution via `PermissionProfile`, argv
  exact-match -- WU-004 BUG-3, WU-005 capability granularity/ADR-009).
- Action-Policy (`HIGH_RISK_PATTERNS`, argument-content risk independent of
  tool identity) -- now applied UNCONDITIONALLY including under
  UNRESTRICTED, closing the same class of early-return bypass as BUG-1
  (previously `check_command()`'s UNRESTRICTED branch returned before this
  loop ever ran).

Layers deferred, explicit no-ops documented here rather than silently
omitted -- implementing them now would be hollow architecture against
infrastructure that doesn't exist yet:
- Kill-switch: needs `RunningActionRegistry` (WU-106).
- Environment: no per-environment config layer designed yet beyond scattered
  `WTH_TEST_MODE` checks.
- Engagement: needs the `Engagement` entity/store (WU-101; WU-014 is a
  skeleton only, no store/broker integration).
- Agent-Capability (per-actor restriction): `ACTOR_TOOLS` filtering already
  exists (`agents/tool_registry.py`) but wiring it into this engine is
  WU-007/WU-011 scope (trust-boundary rework), not this WU.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass

from wth_ai.permission_manager import DENYLIST_PATTERNS, HIGH_RISK_PATTERNS
from wth_ai.policy.capability import ApprovalMode, PermissionProfile


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str
    risk: str  # "low"|"medium"|"high"|"critical"
    approval_needed: bool
    capability: str | None = None


def _extract_tool(cmd_lower: str) -> str:
    return cmd_lower.split()[0].split("/")[-1] if cmd_lower else ""


_WS_RUN = re.compile(r"\s+")


def _collapse_ws(text: str) -> str:
    """Collapse every whitespace run to one space.

    The denylist/high-risk patterns are written with single literal spaces, so
    without this a second space or a TAB inside the command walked straight
    past an otherwise-correct pattern -- verified before the fix:
    ``rm  -rf  /`` and ``hydra\\t-l x`` were both ALLOWED while their
    single-space spellings were correctly blocked.
    """
    return _WS_RUN.sub(" ", text or "").strip()


# Flags that turn `find` into an arbitrary-program runner.
_FIND_EXEC_FLAGS = frozenset({"-exec", "-execdir", "-ok", "-okdir"})


def _is_root_path(token: str) -> bool:
    """True for '/', '/*', '//', '*/' -- but False for '/tmp/x'.

    Deliberately about *the root itself*: the argv rule exists to catch
    recursive-force deletion of the filesystem root, not to widen the
    substring denylist (which already conservatively rejects `rm -rf /any/path`).
    """
    return bool(token) and set(token) <= {"/", "*"}


def argv_safety_match(command: str) -> tuple[str, bool] | None:
    """Argv-aware safety rules a substring list cannot express.

    Returns ``(reason, hard_deny)`` or ``None``. ``hard_deny=True`` means always
    DENY (even under UNRESTRICTED); ``False`` means "raise approval".

    Two concrete holes reproduced against the substring-only implementation:
      - flag reordering / long flags: ``rm -fr /`` and
        ``rm -rf --no-preserve-root /`` contain neither ``rm -rf /`` nor
        ``rm -rf /*``, so both were ALLOWED.
      - ``find ... -exec <program>``: ``find`` is granted as read-only, but
        -exec runs whatever follows -- exactly what the interpreter capability
        exists to gate. Verified: ``find /tmp -maxdepth 0 -exec python3 -c pass ;``
        was allowed with cap=shell.readonly while a bare ``python3 -c pass``
        demanded approval.
    """
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if not argv:
        return None
    tool = os.path.basename(argv[0]).lower()
    args = argv[1:]

    if tool == "rm":
        short_flags = "".join(a[1:] for a in args if a.startswith("-") and not a.startswith("--"))
        long_flags = {a[2:].split("=", 1)[0] for a in args if a.startswith("--")}
        recursive = "r" in short_flags.lower() or "recursive" in long_flags
        force = "f" in short_flags or "force" in long_flags
        if recursive and force:
            targets = [a for a in args if not a.startswith("-")]
            if any(_is_root_path(t) for t in targets):
                return "rm recursive+force on the filesystem root", True

    if tool == "find" and any(a in _FIND_EXEC_FLAGS for a in args):
        return "find -exec/-ok runs an arbitrary program", False

    return None


def denylist_match(text_lower: str) -> str | None:
    """First absolute-denylist pattern contained in `text_lower`, else None.

    WU-104: single source of truth for the Global-Safety layer. Shared by
    `evaluate_intent()` (shell commands) and `ExecuteRpcServer` (arbitrary
    Python code blobs over the gateway UDS socket) so a pattern added to
    `DENYLIST_PATTERNS` gates BOTH execution surfaces from ONE list -- no
    second inline loop that could drift out of sync. Caller passes an
    already-lowercased string (the layer is case-insensitive by matching
    against lowercase, consistent with the P0 exit-review denylist fix).

    Matches raw text and whitespace-collapsed text, so spacing tricks do not
    evade a pattern (see `_collapse_ws`).
    """
    collapsed = _collapse_ws(text_lower)
    for pattern in DENYLIST_PATTERNS:
        if pattern in text_lower or pattern in collapsed:
            return pattern
    return None


def high_risk_match(text_lower: str) -> str | None:
    """First high-risk-action pattern contained in `text_lower`, else None.

    WU-104: single source of truth for the Action-Policy layer (see
    `denylist_match`). Content-based risk independent of tool identity, so
    it applies equally to a shell command and to a code blob. Also matched
    against whitespace-collapsed text -- several patterns carry a literal
    trailing space (`hydra `, `medusa `, `masscan `), which a TAB defeated.
    """
    collapsed = _collapse_ws(text_lower)
    for pattern in HIGH_RISK_PATTERNS:
        if pattern in text_lower or pattern in collapsed:
            return pattern
    return None


def evaluate_intent(*, command: str, profile: PermissionProfile, approved: bool = False) -> PolicyDecision:
    """Evaluate a shell command against `profile`. Every branch returns an
    explicit decision -- no path reaches the end of this function without
    one (ADR-010: no fallthrough-to-ALLOW).

    `approved`: P0 exit-review finding (HIGH). Previously REQUIRE_HUMAN and
    HIGH_RISK_PATTERNS both returned `allowed=False` unconditionally, with
    no way for a caller-supplied `approved=True` to ever unblock -- callers
    (`terminal/bridge.py::run_shell`, `tool_runner.py::execute_task`) check
    `allowed` FIRST and return early, so their own `approval_needed and not
    approved` branch was dead code (this pre-dates WU-005, but WU-005
    rewrote and extensively tested this exact function without catching it).
    Now: `approved=True` lets execution proceed past either gate;
    `approval_needed` in the result still reflects that approval WAS
    required, for audit/reason-string purposes.
    """
    cmd_lower = command.lower().strip()

    # --- Global-Safety: absolute denylist, before any other layer ---
    denied = denylist_match(cmd_lower)
    if denied is not None:
        return PolicyDecision(False, f"Denied by global denylist: {denied}", "critical", False)

    # --- Global-Safety (2): argv-aware rules a substring list cannot express.
    # Evaluated here, before capability resolution, so a hard hit binds even
    # under UNRESTRICTED's wildcard (same discipline as the denylist layer).
    # Soft hits (hard_deny=False) are folded into the Action-Policy layer below
    # so the operator can still approve them explicitly.
    safety = argv_safety_match(cmd_lower)
    if safety is not None and safety[1]:
        return PolicyDecision(False, f"Denied by global safety rule: {safety[0]}", "critical", False)

    tool = _extract_tool(cmd_lower)
    if not tool:
        # Empty command: no execution occurs (WU-004 documented behavior,
        # unchanged by this WU).
        return PolicyDecision(True, "", "low", False)

    # --- Tool-Policy: Capability resolution (argv exact-match) ---
    cap = profile.resolve(tool)
    if cap is None:
        return PolicyDecision(
            False, f"Tool '{tool}' not in allowlist for profile {profile.name}", "low", False,
        )

    if cap.approval_mode == ApprovalMode.ALWAYS_DENY:
        return PolicyDecision(False, f"Capability '{cap.name}' is always-deny", cap.risk.value, False, cap.name)

    needs_approval = cap.approval_mode == ApprovalMode.REQUIRE_HUMAN
    if needs_approval and not approved:
        return PolicyDecision(
            False,
            f"Capability '{cap.name}' (tool '{tool}') requires explicit human approval",
            cap.risk.value, True, cap.name,
        )

    # --- Action-Policy: argument-content risk, independent of tool identity.
    # Applied for EVERY profile incl. unrestricted's wildcard match above --
    # closes the same early-return-bypass bug class as Global-Safety/BUG-1.
    hr = high_risk_match(cmd_lower)
    if hr is None and safety is not None:
        hr = safety[0]  # soft argv rule (e.g. `find -exec`) -> approval, not deny
    if hr is not None:
        needs_approval = True
        if not approved:
            return PolicyDecision(
                False,
                f"High-risk action pattern matched: '{hr}' (capability '{cap.name}')",
                "high", True, cap.name,
            )

    return PolicyDecision(True, "", cap.risk.value, needs_approval, cap.name)

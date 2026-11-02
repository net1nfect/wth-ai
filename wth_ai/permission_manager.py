"""Permission mode management and action gating."""

from __future__ import annotations

from typing import Any

from wth_ai.models import PermissionMode


PERMISSION_LEVEL = {
    PermissionMode.READ_ONLY_RECON: 0,
    PermissionMode.STANDARD_TESTING: 1,
    PermissionMode.ELEVATED_APPROVAL: 2,
    PermissionMode.FULL_LAB: 3,
    PermissionMode.UNRESTRICTED: 4,
}

READ_ONLY_TOOLS = {
    "cat", "grep", "rg", "find", "git", "semgrep", "composer", "npm", "pip-audit",
    "cargo", "trivy", "curl", "httpx", "whatweb", "head", "tail", "less", "wc",
    "ls", "file", "strings", "read", "python", "jq",
    # Passive name/registry lookups. These send no traffic to the target itself
    # (resolver + registry queries only), so they belong to the read-only tier
    # -- they were previously granted in NO mode except UNRESTRICTED's wildcard,
    # which forced operators out of read-only recon just to resolve a hostname.
    "dig", "host", "nslookup", "getent", "whois", "openssl",
    # Computer-use OBSERVATION. Reading the screen / window list / geometry
    # changes nothing, so it belongs here rather than behind an approval prompt.
    "scrot", "grim", "spectacle", "gnome-screenshot", "import",
    "wmctrl", "xrandr", "xclip",
}

STANDARD_TOOLS = READ_ONLY_TOOLS | {
    "ffuf", "gobuster", "nuclei", "nmap", "arjun", "katana", "subfinder",
    "dnsx", "waybackurls", "gau", "paramspider",
    # Active-but-light scanners, same tier as nmap/nuclei above. Previously
    # absent from every set, so they were unreachable outside UNRESTRICTED.
    "nikto", "naabu", "wpscan", "whatweb-aggressive",
    # Computer-use INPUT INJECTION: clicking and typing act on whatever is
    # focused on the operator's real desktop, so they start at the same tier as
    # other active actions and never lower.
    "xdotool", "ydotool",
}

ELEVATED_TOOLS = STANDARD_TOOLS | {
    "sqlmap", "dalfox", "ffuf-aggressive",
}

FULL_LAB_TOOLS = ELEVATED_TOOLS | {
    "masscan", "hydra", "medusa",
}

DENYLIST_PATTERNS = [
    "rm -rf /",
    "rm -rf /*",
    ":(){ :|:& };:",
    "mkfs.",
    "dd if=/dev/zero",
    "> /dev/sda",
    "chmod -r 777 /",  # lowercase -- dibandingkan terhadap cmd_lower yang selalu lowercase (P0 exit-review finding)
]

HIGH_RISK_PATTERNS = [
    "--dump",
    "--os-shell",
    "--os-pwn",
    "reverse shell",
    "nc -e",
    "/bin/bash -i",
    "credential stuffing",
    "password spray",
    "hydra ",
    "medusa ",
    "masscan ",
    "sqlmap.*--dump",
]

MODE_TOOLS = {
    PermissionMode.READ_ONLY_RECON: READ_ONLY_TOOLS,
    PermissionMode.STANDARD_TESTING: STANDARD_TOOLS,
    PermissionMode.ELEVATED_APPROVAL: ELEVATED_TOOLS,
    PermissionMode.FULL_LAB: FULL_LAB_TOOLS,
    PermissionMode.UNRESTRICTED: FULL_LAB_TOOLS | {"*"},
}

PERMISSION_ALIASES = {
    "active_test": PermissionMode.ELEVATED_APPROVAL.value,
    "elevated": PermissionMode.ELEVATED_APPROVAL.value,
    "unrestricted": PermissionMode.UNRESTRICTED.value,
    # Legacy spellings. These were the values advertised by the LLM-facing
    # `permission_set` tool schema ("read_only_recon" / "standard_testing" /
    # "elevated_testing"), none of which are real PermissionMode values -- so
    # `PermissionMode(mode)` raised ValueError for every mode except full_lab.
    # Kept as accepted aliases so a stored session or a model echoing the old
    # spelling still resolves instead of erroring.
    "read_only_recon": PermissionMode.READ_ONLY_RECON.value,
    "standard_testing": PermissionMode.STANDARD_TESTING.value,
    "elevated_testing": PermissionMode.ELEVATED_APPROVAL.value,
}

MODE_DESCRIPTIONS = {
    PermissionMode.READ_ONLY_RECON: "Read-only recon: static analysis, safe HEAD/GET, dependency audit",
    PermissionMode.STANDARD_TESTING: "Standard: low-rate dynamic tests, nuclei info-low-medium",
    PermissionMode.ELEVATED_APPROVAL: "Elevated: controlled fuzzing, IDOR tests — approval per high-risk action",
    PermissionMode.FULL_LAB: "Full lab: disposable local/CTF only — still no real-world harm",
    PermissionMode.UNRESTRICTED: "Unrestricted: all tools enabled, no rate limits, auto-approve",
}


class PermissionManager:
    """Enforce permission modes and command allowlists."""

    def __init__(self, mode: PermissionMode = PermissionMode.READ_ONLY_RECON) -> None:
        self.mode = mode
        self._pending_approvals: dict[str, dict[str, Any]] = {}

    def set_mode(self, mode: PermissionMode | str) -> PermissionMode:
        if isinstance(mode, str):
            normalized = mode.strip().lower()
            normalized = PERMISSION_ALIASES.get(normalized, normalized)
            mapping = {p.value: p for p in PermissionMode}
            if normalized not in mapping:
                raise ValueError(
                    f"Unknown permission mode: {mode}. "
                    f"Valid: {', '.join(p.value for p in PermissionMode)} "
                    f"(alias: active_test → elevated)"
                )
            mode = mapping[normalized]
        self.mode = mode
        return self.mode

    def display(self) -> str:
        return (
            f"PERMISSION MODE: {self.mode.value}\n"
            f"Description: {MODE_DESCRIPTIONS[self.mode]}\n"
            f"Level: {PERMISSION_LEVEL[self.mode]}/{max(PERMISSION_LEVEL.values())}"
        )

    def mode_sufficient(self, required: PermissionMode | str) -> bool:
        if isinstance(required, str):
            required = PermissionMode(required) if required in {p.value for p in PermissionMode} else PermissionMode.READ_ONLY_RECON
        return PERMISSION_LEVEL[self.mode] >= PERMISSION_LEVEL[required]

    def check_command(
        self, command: str, *, required_mode: PermissionMode | None = None, approved: bool = False,
    ) -> dict[str, Any]:
        """Validate a shell command against permission rules.

        Thin adapter over `wth_ai.policy.engine.evaluate_intent()` (WU-005,
        ADR-009/ADR-010) — return-shape kept identical to the pre-WU-005
        implementation so existing callers/tests need no change. Lazy import
        to avoid a circular dependency (`policy.profiles_builtin` imports the
        tool sets defined in this module).

        `approved`: P0 exit-review fix (HIGH) — threads through to
        `evaluate_intent()` so a caller-supplied approval can actually
        unblock a REQUIRE_HUMAN/high-risk-pattern decision. Previously this
        method had no `approved` parameter at all, making callers' own
        `approval_needed and not approved` checks dead code.
        """
        from wth_ai.policy.engine import evaluate_intent
        from wth_ai.policy.profiles_builtin import PROFILES

        profile = PROFILES[self.mode]
        decision = evaluate_intent(command=command, profile=profile, approved=approved)
        result: dict[str, Any] = {
            "command": command,
            "current_mode": self.mode.value,
            "allowed": decision.allowed,
            "reason": decision.reason,
            "risk": decision.risk,
            "approval_needed": decision.approval_needed,
        }
        if not decision.allowed:
            return result

        if required_mode and not self.mode_sufficient(required_mode):
            result["allowed"] = False
            result["reason"] = f"Insufficient permission: need {required_mode.value}, have {self.mode.value}"

        return result

    def permission_check_block(self, required: PermissionMode, action_risk: str, approved: bool = False) -> dict[str, Any]:
        return {
            "current_mode": self.mode.value,
            "required_mode": required.value if isinstance(required, PermissionMode) else required,
            "action_risk": action_risk,
            "approval_needed": action_risk in {"high", "critical"} and not approved,
            "approved": approved,
            "sufficient": self.mode_sufficient(required),
        }

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
}

STANDARD_TOOLS = READ_ONLY_TOOLS | {
    "ffuf", "gobuster", "nuclei", "nmap", "arjun", "katana", "subfinder",
    "dnsx", "waybackurls", "gau", "paramspider",
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
    "chmod -R 777 /",
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

    def check_command(self, command: str, *, required_mode: PermissionMode | None = None) -> dict[str, Any]:
        """Validate a shell command against permission rules."""
        cmd_lower = command.lower().strip()
        result = {
            "command": command,
            "current_mode": self.mode.value,
            "allowed": True,
            "reason": "",
            "risk": "low",
            "approval_needed": False,
        }

        if self.mode == PermissionMode.UNRESTRICTED:
            result["reason"] = "unrestricted mode"
            return result

        for pattern in DENYLIST_PATTERNS:
            if pattern in cmd_lower:
                result["allowed"] = False
                result["reason"] = f"Denied by global denylist: {pattern}"
                result["risk"] = "critical"
                return result

        for pattern in HIGH_RISK_PATTERNS:
            if pattern in cmd_lower:
                result["risk"] = "high"
                result["approval_needed"] = True
                if self.mode != PermissionMode.FULL_LAB:
                    result["allowed"] = False
                    result["reason"] = f"High-risk command requires FULL_LAB + explicit approval: matched '{pattern}'"
                    return result

        if required_mode and not self.mode_sufficient(required_mode):
            result["allowed"] = False
            result["reason"] = f"Insufficient permission: need {required_mode.value}, have {self.mode.value}"
            return result

        tool = cmd_lower.split()[0].split("/")[-1] if cmd_lower else ""
        allowed_tools = MODE_TOOLS.get(self.mode, READ_ONLY_TOOLS)
        if tool and tool not in allowed_tools and not any(t in cmd_lower for t in allowed_tools):
            if tool in {"bash", "sh", "zsh"}:
                pass
            elif tool not in READ_ONLY_TOOLS:
                result["allowed"] = False
                result["reason"] = f"Tool '{tool}' not in allowlist for mode {self.mode.value}"
                return result

        if result["approval_needed"] and self.mode == PermissionMode.FULL_LAB:
            result["allowed"] = False
            result["reason"] = "High-risk command needs explicit user approval (/approve)"

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

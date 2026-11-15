"""Foundry/Slither integration for local Solidity audit work.

The tool is deliberately a thin, deterministic wrapper around the binaries the
operator already trusts. It composes an argv with ``shlex.join`` (no shell
interpolation from the model), then executes it through the existing
``run_shell`` bridge so scope, permission, approval, audit, and transcript
handling are identical to any other shell command.
"""

from __future__ import annotations

import os
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ACTIONS = frozenset({"build", "test", "slither", "doctor"})


class SolidityAuditError(ValueError):
    """Invalid tool arguments or project path."""


@dataclass(frozen=True)
class SolidityCommand:
    action: str
    binary: str
    argv: tuple[str, ...]
    cwd: Path
    root: Path

    @property
    def shell_command(self) -> str:
        return shlex.join(self.argv)


def resolve_binary(name: str) -> str | None:
    """Resolve Foundry/Slither without hard-coding an audit workspace path.

    ``*_BIN`` environment variables let the operator point at a venv or a
    non-standard Foundry install. ``PATH`` remains the normal source of truth.
    """
    env_name = f"{name.upper()}_BIN"
    configured = os.environ.get(env_name)
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    found = shutil.which(name)
    if found:
        return found
    # Foundry's installer, WTH's managed tool dir, and common user-local
    # installs are not always on PATH for non-interactive agents.
    wth_home = Path(os.environ.get("WTH_HOME", Path.home() / ".wth-ai"))
    candidates = [
        Path.home() / ".foundry" / "bin" / name,
        Path.home() / ".local" / "bin" / name,
        wth_home / "tools" / "bin" / name,
    ]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def resolve_project(path: str | os.PathLike[str]) -> Path:
    raw = str(path or "").strip()
    if not raw:
        raise SolidityAuditError("argumen 'path' wajib berisi direktori proyek Solidity")
    root = Path(raw).expanduser().resolve()
    if not root.is_dir():
        raise SolidityAuditError(f"path bukan direktori: {root}")
    return root


def build_command(
    action: str,
    path: str | os.PathLike[str],
    *,
    match_path: str = "",
) -> SolidityCommand:
    action = str(action or "").strip().lower()
    if action not in ACTIONS:
        raise SolidityAuditError(
            f"action tidak dikenal: {action!r}; pilih {', '.join(sorted(ACTIONS))}"
        )
    root = resolve_project(path)
    if action == "doctor":
        raise SolidityAuditError("doctor tidak memakai command Foundry/Slither")

    binary_name = "slither" if action == "slither" else "forge"
    binary = resolve_binary(binary_name)
    if not binary:
        raise SolidityAuditError(
            f"{binary_name} tidak ditemukan. Set {binary_name.upper()}_BIN atau pasang di PATH."
        )

    if action == "build":
        argv = (binary, "build", "--root", str(root))
    elif action == "test":
        argv = (binary, "test", "--root", str(root), "-vv")
        if match_path:
            argv += ("--match-path", match_path)
    else:  # slither
        argv = (binary, str(root))

    return SolidityCommand(
        action=action,
        binary=binary,
        argv=argv,
        cwd=root,
        root=root,
    )


def doctor() -> str:
    rows = []
    for name in ("forge", "slither", "solc"):
        resolved = resolve_binary(name)
        rows.append(f"{name}: {resolved or 'NOT FOUND'}")
    return "\n".join(rows)


def dispatch_solidity_audit(
    ctx: Any,
    args: dict[str, Any],
    *,
    approved: bool = False,
) -> str:
    """Dispatch ``solidity_audit`` through the audited shell bridge."""
    action = str(args.get("action", "") or "").strip().lower()
    if action == "doctor":
        return "Solidity toolchain:\n" + doctor()

    try:
        command = build_command(
            action,
            str(args.get("path", "") or ""),
            match_path=str(args.get("match_path", "") or ""),
        )
    except SolidityAuditError as exc:
        return f"SOLIDITY_AUDIT_ERROR: {exc}"

    from wth_ai.engagement.mode import auto_approve_enabled

    auto = auto_approve_enabled(getattr(ctx, "config", None))
    result = ctx.terminal.dispatch_tool(
        "run_shell",
        {
            "command": command.shell_command,
            "purpose": f"solidity:{action}:{command.root.name}",
        },
        actor="supervisor",
        approved=approved or auto,
    )
    if getattr(result, "blocked", False):
        reason = getattr(result, "block_reason", "") or "blocked"
        if "approval" in reason.lower():
            return f"APPROVAL_REQUIRED:{result.transcript_id} — ask user to approve"
        return f"SOLIDITY_AUDIT_BLOCKED: {reason}"

    stdout = getattr(result, "stdout", "") or ""
    stderr = getattr(result, "stderr", "") or ""
    header = f"[{command.action}] {command.root} (exit {result.exit_code})"
    body = stdout[:12000] if stdout else stderr[:4000]
    return f"{header}\n{body}".strip()

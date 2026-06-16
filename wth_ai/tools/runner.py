"""Manifest-aware tool execution engine."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from wth_ai.config import WTHConfig
from wth_ai.models import ExecutorResult, ExecutorTask, PermissionMode
from wth_ai.permission_manager import PermissionManager
from wth_ai.scope_manager import ScopeManager
from wth_ai.tool_runner import ToolRunner
from wth_ai.tools.registry import ToolRegistry


class ManifestToolRunner(ToolRunner):
  """Extend ToolRunner with manifest allowlist validation."""

  def __init__(
    self,
    config: WTHConfig,
    permission: PermissionManager,
    scope: ScopeManager,
    registry: ToolRegistry | None = None,
  ) -> None:
    super().__init__(config, permission, scope)
    self.registry = registry or ToolRegistry()

  def run_tool(
    self,
    tool_id: str,
    args: str,
    *,
    timeout: int | None = None,
    cwd: Path | None = None,
  ) -> tuple[str, str, int]:
    spec = self.registry.tools.get(tool_id)
    if not spec:
      return "", f"Unknown tool: {tool_id}", 1
    if not self.registry.is_installed(tool_id):
      return "", f"Tool not installed: {tool_id} — run /tools install", 127
    binary = spec.binary
    cmd = f"{binary} {args}".strip()
    check = self.permission.check_command(cmd)
    if not check["allowed"]:
      return "", check["reason"], 1
    try:
      proc = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout or self.config.tools.timeout_seconds,
        cwd=cwd,
      )
      return proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired:
      return "", f"Timeout after {timeout}s", 124

  def run_tools_parallel_specs(
    self,
    specs: list[tuple[str, str]],
    *,
    rate_limit_rps: float = 2.0,
  ) -> list[dict[str, Any]]:
    results = []
    for tool_id, args in specs:
      stdout, stderr, code = self.run_tool(tool_id, args)
      results.append({
        "tool": tool_id,
        "args": args,
        "exit_code": code,
        "stdout": stdout[: self.config.tools.max_output_bytes],
        "stderr": stderr[:5000],
      })
      time.sleep(1.0 / max(rate_limit_rps, 0.1))
    return results

  def allowed_tool_ids(self, mode: PermissionMode) -> list[str]:
    level_map = {
      PermissionMode.READ_ONLY_RECON: {"read_only"},
      PermissionMode.STANDARD_TESTING: {"read_only", "standard"},
      PermissionMode.ELEVATED_APPROVAL: {"read_only", "standard", "elevated"},
      PermissionMode.FULL_LAB: {"read_only", "standard", "elevated", "full_lab"},
    }
    allowed_perms = level_map.get(mode, {"read_only"})
    return [
      tid for tid, spec in self.registry.tools.items()
      if spec.permission in allowed_perms and self.registry.is_installed(tid)
    ]

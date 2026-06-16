"""Base recon phase — runs all installed tools for a phase."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from wth_ai.models import AttackSurface, LiveHost, ParamCandidate, SecretHit
from wth_ai.tools.registry import ToolRegistry
from wth_ai.tools.runner import ManifestToolRunner


class PhaseContext:
    def __init__(
        self,
        target: str,
        out_dir: Path,
        runner: ManifestToolRunner,
        registry: ToolRegistry,
        surface: AttackSurface,
        *,
        fast: bool = False,
        rate_limit_rps: float = 2.0,
        terminal_bridge: Any = None,
    ) -> None:
        self.target = target
        self.host = target
        self.out_dir = out_dir
        self.runner = runner
        self.registry = registry
        self.surface = surface
        self.fast = fast
        self.rate_limit_rps = rate_limit_rps
        self.terminal_bridge = terminal_bridge
        self.warnings: list[str] = []

    def write_lines(self, name: str, lines: list[str]) -> Path:
        path = self.out_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        unique = sorted(set(ln.strip() for ln in lines if ln.strip()))
        path.write_text("\n".join(unique) + ("\n" if unique else ""), encoding="utf-8")
        return path

    def read_lines(self, name: str) -> list[str]:
        path = self.out_dir / name
        if not path.exists():
            return []
        return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]

    def merge_file(self, name: str, new_lines: list[str]) -> list[str]:
        existing = set(self.read_lines(name))
        existing.update(ln.strip() for ln in new_lines if ln.strip())
        merged = sorted(existing)
        self.write_lines(name, merged)
        return merged

    def run_phase_tools(self, phase: int, arg_builder) -> list[str]:
        """Run all installed tools for phase, return merged output lines."""
        tools = [t for t in self.registry.by_phase(phase) if self.registry.is_installed(t.id)]
        all_lines: list[str] = []
        for spec in tools:
            args = arg_builder(spec)
            if args is None:
                continue
            stdout, stderr, code = self.runner.run_tool(spec.id, args, timeout=300)
            if stdout:
                all_lines.extend(stdout.splitlines())
            elif code != 0 and stderr:
                self.warnings.append(f"{spec.id}: {stderr[:120]}")
        return all_lines


class BasePhase(ABC):
    phase: int = 0
    name: str = "base"
    artifact: str = "output.txt"

    @abstractmethod
    async def run(self, ctx: PhaseContext) -> dict[str, Any]:
        ...

    def parse_httpx(self, line: str) -> LiveHost:
        url = line.split()[0] if line.split() else line
        status_m = re.search(r"\[(\d{3})\]", line)
        title_m = re.search(r"\]\s*\[([^\]]+)\]", line)
        return LiveHost(
            url=url,
            status=status_m.group(1) if status_m else "",
            title=title_m.group(1) if title_m else "",
            raw=line.strip(),
        )

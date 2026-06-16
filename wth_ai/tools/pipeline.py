"""Piped tool chains for professional recon workflows."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from wth_ai.models import LiveHost, ParamCandidate
from wth_ai.tools.registry import ToolRegistry, _which


@dataclass
class ChainResult:
    name: str
    stdout: str
    stderr: str
    returncode: int
    parsed: dict[str, Any] = field(default_factory=dict)


class ToolChainOrchestrator:
    """Run predefined piped tool chains and parse output."""

    def __init__(self, registry: ToolRegistry | None = None, *, timeout: int = 300) -> None:
        self.registry = registry or ToolRegistry()
        self.timeout = timeout

    def _bin(self, tool_id: str) -> str | None:
        spec = self.registry.tools.get(tool_id)
        if not spec:
            return None
        return self.registry.binary_path(tool_id) or _which(spec.binary)

    def _run_shell(self, cmd: str) -> tuple[str, str, int]:
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            return proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired:
            return "", "timeout", 124
        except OSError as exc:
            return "", str(exc), 1

    def chain_subdomain_dns_http(self, domain: str) -> ChainResult:
        """subfinder | dnsx | httpx — core subdomain → live host pipeline."""
        subfinder = self._bin("subfinder")
        dnsx = self._bin("dnsx")
        httpx = self._bin("httpx")
        if not all([subfinder, dnsx, httpx]):
            return ChainResult("subdomain_dns_http", "", "missing tools", 1)

        cmd = (
            f"{subfinder} -d {domain} -silent 2>/dev/null | "
            f"{dnsx} -a -aaaa -silent 2>/dev/null | "
            f"{httpx} -silent -status-code -title -tech-detect -json 2>/dev/null"
        )
        stdout, stderr, code = self._run_shell(cmd)
        parsed = self._parse_httpx_json(stdout)
        if not parsed.get("hosts") and stdout:
            parsed = self._parse_httpx_text(stdout)
        return ChainResult("subdomain_dns_http", stdout, stderr, code, parsed)

    def chain_gau_sqli_candidates(self, domain: str) -> ChainResult:
        """gau + gf sqli pattern filter."""
        gau = self._bin("gau")
        if not gau:
            return ChainResult("gau_sqli", "", "gau missing", 1)
        gf = self._bin("gf")
        cmd = f"{gau} {domain} 2>/dev/null"
        if gf:
            cmd += f" | {gf} sqli 2>/dev/null"
        else:
            cmd += " | grep -Ei '(id=|user=|page=|cat=)' 2>/dev/null"
        stdout, stderr, code = self._run_shell(cmd)
        urls = [ln.strip() for ln in stdout.splitlines() if ln.strip().startswith("http")]
        return ChainResult("gau_sqli", stdout, stderr, code, {"urls": urls[:500]})

    def chain_katana_params(self, url: str, *, depth: int = 2) -> ChainResult:
        """katana crawl → extract param URLs."""
        katana = self._bin("katana")
        if not katana:
            return ChainResult("katana_params", "", "katana missing", 1)
        cmd = f"{katana} -u {url} -d {depth} -silent -jc 2>/dev/null"
        stdout, stderr, code = self._run_shell(cmd)
        urls = [ln.strip() for ln in stdout.splitlines() if ln.strip().startswith("http")]
        params: list[dict[str, str]] = []
        for u in urls:
            if "?" in u:
                base, qs = u.split("?", 1)
                for pair in qs.split("&"):
                    if "=" in pair:
                        params.append({"url": u, "param": pair.split("=", 1)[0]})
        return ChainResult("katana_params", stdout, stderr, code, {"urls": urls[:300], "params": params[:200]})

    def _parse_httpx_json(self, stdout: str) -> dict[str, Any]:
        hosts: list[LiveHost] = []
        urls: list[str] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            url = obj.get("url", "")
            if url:
                urls.append(url)
                tech = obj.get("tech", []) or []
                hosts.append(LiveHost(
                    url=url,
                    status=str(obj.get("status_code", "")),
                    title=str(obj.get("title", "")),
                    tech=[str(t) for t in tech] if isinstance(tech, list) else [],
                    raw=line[:500],
                ))
        return {"hosts": hosts, "urls": urls}

    def _parse_httpx_text(self, stdout: str) -> dict[str, Any]:
        hosts: list[LiveHost] = []
        urls: list[str] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("http"):
                continue
            url = line.split()[0]
            urls.append(url)
            status_m = re.search(r"\[(\d{3})\]", line)
            hosts.append(LiveHost(url=url, status=status_m.group(1) if status_m else "", raw=line))
        return {"hosts": hosts, "urls": urls}

    def run_all(self, domain: str, base_url: str | None = None) -> dict[str, ChainResult]:
        base_url = base_url or f"https://{domain}"
        return {
            "subdomain_dns_http": self.chain_subdomain_dns_http(domain),
            "gau_sqli": self.chain_gau_sqli_candidates(domain),
            "katana_params": self.chain_katana_params(base_url),
        }

    def apply_to_surface(self, results: dict[str, ChainResult], surface: Any) -> None:
        """Populate AttackSurface from chain results."""
        core = results.get("subdomain_dns_http")
        if core and core.parsed.get("hosts"):
            for h in core.parsed["hosts"]:
                if h.url not in [x.url for x in surface.live_hosts]:
                    surface.live_hosts.append(h)
            for u in core.parsed.get("urls", []):
                host = u.split("//")[-1].split("/")[0].lower()
                if host and host not in surface.subdomains:
                    surface.subdomains.append(host)

        gau = results.get("gau_sqli")
        if gau and gau.parsed.get("urls"):
            for u in gau.parsed["urls"]:
                if u not in surface.urls:
                    surface.urls.append(u)

        kat = results.get("katana_params")
        if kat:
            for u in kat.parsed.get("urls", []):
                if u not in surface.urls:
                    surface.urls.append(u)
            for p in kat.parsed.get("params", []):
                surface.parameters.append(ParamCandidate(url=p["url"], param=p["param"]))

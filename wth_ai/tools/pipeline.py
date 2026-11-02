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


# Characters that can never legitimately appear in a hostname/IP literal.
# Pipeline stages hand the target to a tool as a plain argv element (never as a
# shell string) -- this is a sanity filter so a malformed or hostile target
# makes the chain skip cleanly instead of being passed to a tool as a bogus
# argument. It is deliberately permissive: every real host, IPv4, IPv6 and
# host:port form passes.
_UNSAFE_TARGET_CHARS = re.compile(r"[\s;|&$`'\"()<>\\{}\n\r\t]")


def _safe_target(value: str) -> str | None:
    """Return an argv-safe target token, or None if it can never be one."""
    token = (value or "").strip()
    if not token or _UNSAFE_TARGET_CHARS.search(token):
        return None
    return token


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

    def _run_pipeline(self, stages: list[list[str]]) -> tuple[str, str, int]:
        """Run ``a | b | c`` as a real OS pipe built from argv lists — no shell.

        Previously this was a single ``subprocess.run(f"...|...", shell=True)``
        string assembled with f-strings, so anything reaching ``domain``/``url``
        was interpreted by /bin/sh. A genuine argv pipe keeps identical
        ``a | b | c`` semantics with nothing interpreted by a shell at all.

        Stderr is discarded for every stage, matching the ``2>/dev/null`` that
        the old command strings carried on each one -- so the stderr behaviour
        of each chain is unchanged.
        """
        live = [argv for argv in stages if argv]
        if not live:
            return "", "empty command", 1

        procs: list[subprocess.Popen[str]] = []
        try:
            prev: subprocess.Popen[str] | None = None
            for argv in live:
                proc = subprocess.Popen(
                    argv,
                    stdin=prev.stdout if prev is not None else None,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
                if prev is not None and prev.stdout is not None:
                    # Close our copy so the upstream stage sees EOF once the
                    # downstream stage has consumed what it needs.
                    prev.stdout.close()
                procs.append(proc)
                prev = proc
            stdout, _ = procs[-1].communicate(timeout=self.timeout)
            for proc in procs[:-1]:
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            return stdout or "", "", procs[-1].returncode
        except subprocess.TimeoutExpired:
            self._kill_all(procs)
            return "", f"timeout after {self.timeout}s", 124
        except OSError as exc:
            self._kill_all(procs)
            return "", str(exc), 1

    @staticmethod
    def _kill_all(procs: list[subprocess.Popen[str]]) -> None:
        for proc in procs:
            try:
                proc.kill()
            except OSError:
                pass
            try:
                # Reap, so a timed-out pipeline does not leave zombies behind.
                proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                pass

    def chain_subdomain_dns_http(self, domain: str) -> ChainResult:
        """subfinder | dnsx | httpx — core subdomain → live host pipeline."""
        host = _safe_target(domain)
        if host is None:
            return ChainResult("subdomain_dns_http", "", "invalid target", 1)
        subfinder = self._bin("subfinder")
        dnsx = self._bin("dnsx")
        httpx = self._bin("httpx")
        if subfinder is None or dnsx is None or httpx is None:
            return ChainResult("subdomain_dns_http", "", "missing tools", 1)

        stdout, stderr, code = self._run_pipeline([
            [subfinder, "-d", host, "-silent"],
            [dnsx, "-a", "-aaaa", "-silent"],
            [httpx, "-silent", "-status-code", "-title", "-tech-detect", "-json"],
        ])
        parsed = self._parse_httpx_json(stdout)
        if not parsed.get("hosts") and stdout:
            parsed = self._parse_httpx_text(stdout)
        return ChainResult("subdomain_dns_http", stdout, stderr, code, parsed)

    def chain_gau_sqli_candidates(self, domain: str) -> ChainResult:
        """gau + gf sqli pattern filter."""
        host = _safe_target(domain)
        if host is None:
            return ChainResult("gau_sqli", "", "invalid target", 1)
        gau = self._bin("gau")
        if not gau:
            return ChainResult("gau_sqli", "", "gau missing", 1)
        gf = self._bin("gf")
        stages: list[list[str]] = [[gau, host]]
        if gf:
            stages.append([gf, "sqli"])
        else:
            stages.append(["grep", "-Ei", "(id=|user=|page=|cat=)"])
        stdout, stderr, code = self._run_pipeline(stages)
        urls = [ln.strip() for ln in stdout.splitlines() if ln.strip().startswith("http")]
        return ChainResult("gau_sqli", stdout, stderr, code, {"urls": urls[:500]})

    def chain_katana_params(self, url: str, *, depth: int = 2) -> ChainResult:
        """katana crawl → extract param URLs."""
        # Validate the target before touching the registry, so a malformed
        # target fails identically whether or not the binaries are installed.
        target = _safe_target(url)
        if target is None:
            return ChainResult("katana_params", "", "invalid target", 1)
        katana = self._bin("katana")
        if not katana:
            return ChainResult("katana_params", "", "katana missing", 1)
        stdout, stderr, code = self._run_pipeline([
            [katana, "-u", target, "-d", str(depth), "-silent", "-jc"],
        ])
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

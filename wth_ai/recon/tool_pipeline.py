"""Real recon pipeline using external security tools."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from wth_ai.config import WTHConfig


class ToolPipeline:
    """Run subfinder → dnsx/httpx → katana/gau → gf → nuclei against a target."""

    GF_PATTERNS = ("xss", "ssrf", "idor", "sqli", "redirect", "lfi")
    API_PATTERN = re.compile(r"/api/|/v1/|/v2/|/graphql|/rest/", re.I)

    def __init__(self, config: WTHConfig | None = None, *, output_root: str = "recon") -> None:
        self.config = config or WTHConfig.load()
        self.output_root = Path(output_root)
        self.warnings: list[str] = []

    def run(
        self,
        target: str,
        *,
        fast: bool = False,
        focus: str | None = None,
        rate_limit_rps: float | None = None,
    ) -> dict[str, Any]:
        host = self._normalize_host(target)
        if not host:
            return {"target": target, "error": "Invalid target", "summary": {}}

        out_dir = self.output_root / host
        out_dir.mkdir(parents=True, exist_ok=True)
        self.warnings = []
        rps = rate_limit_rps or self.config.tools.default_rate_limit_rps

        subdomains = self._enumerate_subdomains(host, out_dir)
        self._sleep(rps)
        resolved = self._resolve_dns(subdomains, out_dir)
        self._sleep(rps)
        live_hosts = self._probe_http(resolved, out_dir)
        self._sleep(rps)
        urls = self._crawl_urls(host, live_hosts, out_dir, fast=fast)
        self._sleep(rps)
        candidates = self._classify_urls(urls, out_dir, focus=focus)
        self._sleep(rps)
        nuclei_findings = self._run_nuclei(live_hosts, out_dir)

        summary = self._build_summary(host, subdomains, live_hosts, urls, candidates, nuclei_findings)
        return {
            "target": host,
            "artifacts_dir": str(out_dir),
            "subdomains": subdomains,
            "live_hosts": live_hosts,
            "urls": urls,
            "urls_count": len(urls),
            "candidates": candidates,
            "nuclei_findings": nuclei_findings,
            "warnings": list(self.warnings),
            "summary": summary,
        }

    def _normalize_host(self, target: str) -> str:
        target = target.strip()
        parsed = urlparse(target if "://" in target else f"https://{target}")
        return (parsed.hostname or "").lower()

    def _tool_available(self, name: str) -> bool:
        return shutil.which(name) is not None

    def _warn_missing(self, tool: str, phase: str) -> None:
        msg = f"Tool '{tool}' tidak ditemukan — skip fase {phase}"
        self.warnings.append(msg)

    def _run_cmd(
        self,
        cmd: str,
        *,
        timeout: int | None = None,
        cwd: Path | None = None,
    ) -> tuple[str, str, int]:
        timeout = timeout or self.config.tools.timeout_seconds
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
            )
            return proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired:
            return "", f"Command timed out after {timeout}s", 124
        except OSError as exc:
            return "", str(exc), 1

    def _write_lines(self, path: Path, lines: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(sorted(set(lines))) + ("\n" if lines else ""), encoding="utf-8")

    def _read_lines(self, path: Path) -> list[str]:
        if not path.exists():
            return []
        return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]

    def _merge_unique(self, path: Path, new_lines: list[str]) -> list[str]:
        existing = set(self._read_lines(path))
        existing.update(ln.strip() for ln in new_lines if ln.strip())
        merged = sorted(existing)
        self._write_lines(path, merged)
        return merged

    def _sleep(self, rps: float) -> None:
        time.sleep(1.0 / max(rps, 0.1))

    def _enumerate_subdomains(self, host: str, out_dir: Path) -> list[str]:
        path = out_dir / "subdomains.txt"
        found: set[str] = {host}

        chaos_key = os.environ.get("CHAOS_API_KEY", "")
        if chaos_key:
            try:
                resp = requests.get(
                    f"https://dns.projectdiscovery.io/dns/{host}/subdomains",
                    headers={"Authorization": chaos_key},
                    timeout=30,
                )
                if resp.ok:
                    data = resp.json()
                    if isinstance(data, list):
                        found.update(str(s).lower() for s in data)
            except requests.RequestException as exc:
                self.warnings.append(f"Chaos API error: {exc}")

        if self._tool_available("subfinder"):
            stdout, _, _ = self._run_cmd(f"subfinder -d {host} -silent", timeout=180)
            found.update(ln.strip().lower() for ln in stdout.splitlines() if ln.strip())
        else:
            self._warn_missing("subfinder", "subdomain_enumeration")

        if self._tool_available("assetfinder"):
            stdout, _, _ = self._run_cmd(f"assetfinder --subs-only {host}", timeout=120)
            found.update(ln.strip().lower() for ln in stdout.splitlines() if ln.strip())
        else:
            self._warn_missing("assetfinder", "subdomain_enumeration (assetfinder)")

        return self._merge_unique(path, list(found))

    def _resolve_dns(self, subdomains: list[str], out_dir: Path) -> list[str]:
        path = out_dir / "resolved.txt"
        if not subdomains:
            return []

        if self._tool_available("dnsx"):
            input_file = out_dir / "_dnsx_input.txt"
            self._write_lines(input_file, subdomains)
            stdout, _, _ = self._run_cmd(
                f"dnsx -l {input_file} -silent",
                timeout=180,
            )
            resolved = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
            input_file.unlink(missing_ok=True)
        else:
            self._warn_missing("dnsx", "dns_resolution")
            resolved = list(subdomains)

        return self._merge_unique(path, resolved)

    def _probe_http(self, hosts: list[str], out_dir: Path) -> list[str]:
        path = out_dir / "live-hosts.txt"
        if not hosts:
            return []

        if not self._tool_available("httpx"):
            self._warn_missing("httpx", "http_probing")
            live = [f"https://{h}" for h in hosts[:20]]
            self._write_lines(path, live)
            return live

        input_file = out_dir / "_httpx_input.txt"
        self._write_lines(input_file, hosts)
        stdout, _, _ = self._run_cmd(
            f"httpx -l {input_file} -silent -status-code -title -tech-detect",
            timeout=300,
        )
        input_file.unlink(missing_ok=True)
        live = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
        return self._merge_unique(path, live)

    def _crawl_urls(
        self,
        host: str,
        live_hosts: list[str],
        out_dir: Path,
        *,
        fast: bool,
    ) -> list[str]:
        path = out_dir / "urls.txt"
        urls: set[str] = set()

        host_urls = [h if h.startswith("http") else f"https://{h}" for h in live_hosts[:30]]
        if self._tool_available("katana") and host_urls:
            input_file = out_dir / "_katana_input.txt"
            self._write_lines(input_file, host_urls)
            stdout, _, _ = self._run_cmd(
                f"katana -list {input_file} -d 3 -jc -kf all -silent",
                timeout=300,
            )
            input_file.unlink(missing_ok=True)
            urls.update(ln.strip() for ln in stdout.splitlines() if ln.strip())
        else:
            self._warn_missing("katana", "content_discovery")

        if not fast:
            if self._tool_available("waybackurls"):
                stdout, _, _ = self._run_cmd(f"echo {host} | waybackurls", timeout=180)
                urls.update(ln.strip() for ln in stdout.splitlines() if ln.strip())
            else:
                self._warn_missing("waybackurls", "historical URLs (wayback)")

            if self._tool_available("gau"):
                stdout, _, _ = self._run_cmd(f"gau {host} --subs", timeout=180)
                urls.update(ln.strip() for ln in stdout.splitlines() if ln.strip())
            else:
                self._warn_missing("gau", "historical URLs (gau)")

        return self._merge_unique(path, list(urls))

    def _classify_urls(
        self,
        urls: list[str],
        out_dir: Path,
        *,
        focus: str | None,
    ) -> dict[str, list[str]]:
        candidates: dict[str, list[str]] = {}
        if not urls:
            for name in self.GF_PATTERNS:
                (out_dir / f"{name}-candidates.txt").write_text("", encoding="utf-8")
            (out_dir / "api-endpoints.txt").write_text("", encoding="utf-8")
            return candidates

        url_input = out_dir / "_urls_pipe.txt"
        self._write_lines(url_input, urls)

        patterns = self.GF_PATTERNS
        if focus == "api":
            patterns = ()
        elif focus == "auth":
            patterns = ("idor", "redirect")

        if self._tool_available("gf"):
            for pattern in patterns:
                out_path = out_dir / f"{pattern}-candidates.txt"
                stdout, _, code = self._run_cmd(
                    f"cat {url_input} | gf {pattern}",
                    timeout=60,
                )
                if code == 0 and stdout.strip():
                    lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
                    self._write_lines(out_path, lines)
                    candidates[pattern] = lines
                else:
                    out_path.write_text("", encoding="utf-8")
                    candidates[pattern] = []
        else:
            self._warn_missing("gf", "URL classification")
            for pattern in patterns:
                candidates[pattern] = []

        api_lines = [u for u in urls if self.API_PATTERN.search(u)]
        if focus == "auth":
            api_lines = [u for u in api_lines if re.search(r"auth|login|oauth|sso|session", u, re.I)]
        api_path = out_dir / "api-endpoints.txt"
        self._write_lines(api_path, api_lines)
        candidates["api"] = api_lines

        url_input.unlink(missing_ok=True)
        return candidates

    def _run_nuclei(self, live_hosts: list[str], out_dir: Path) -> list[str]:
        path = out_dir / "nuclei.txt"
        if not live_hosts:
            path.write_text("", encoding="utf-8")
            return []

        if not self._tool_available("nuclei"):
            self._warn_missing("nuclei", "CVE/misconfig scan")
            return []

        input_file = out_dir / "_nuclei_input.txt"
        bare = []
        for h in live_hosts:
            bare.append(h.split()[0] if " " in h else h)
        self._write_lines(input_file, bare)

        templates = Path.home() / "nuclei-templates"
        tflag = f"-t {templates}/" if templates.is_dir() else ""
        stdout, stderr, code = self._run_cmd(
            f"nuclei -l {input_file} {tflag} -severity critical,high,medium -silent",
            timeout=600,
        )
        input_file.unlink(missing_ok=True)

        findings = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
        if not findings and stderr.strip():
            self.warnings.append(f"nuclei stderr: {stderr[:200]}")
        self._write_lines(path, findings)
        return findings

    def _build_summary(
        self,
        host: str,
        subdomains: list[str],
        live_hosts: list[str],
        urls: list[str],
        candidates: dict[str, list[str]],
        nuclei_findings: list[str],
    ) -> dict[str, Any]:
        api_count = len(candidates.get("api", []))
        idor_count = len(candidates.get("idor", []))
        ssrf_count = len(candidates.get("ssrf", []))
        thin_surface = (
            len(live_hosts) <= 1
            and all("403" in h or "401" in h for h in live_hosts if h)
            or (not api_count and not idor_count and not nuclei_findings)
        )
        return {
            "subdomains": len(subdomains),
            "live_hosts": len(live_hosts),
            "urls": len(urls),
            "api_endpoints": api_count,
            "idor_candidates": idor_count,
            "ssrf_candidates": ssrf_count,
            "nuclei_findings": len(nuclei_findings),
            "nuclei_critical_high": sum(
                1 for f in nuclei_findings if re.search(r"\[(critical|high)\]", f, re.I)
            ),
            "thin_surface": thin_surface,
            "phase_names": [
                "subdomain_enumeration",
                "dns_resolution",
                "http_probing",
                "content_discovery",
                "parameter_mining",
            ],
        }

    @staticmethod
    def parse_httpx_line(line: str) -> dict[str, str]:
        """Parse httpx output line: URL [status] [title] [tech]."""
        parts = line.split()
        url = parts[0] if parts else ""
        status = ""
        title = ""
        m = re.search(r"\[(\d{3})\]", line)
        if m:
            status = m.group(1)
        m = re.search(r"\] \[([^\]]+)\]", line)
        if m:
            title = m.group(1)
        return {"url": url, "status": status, "title": title, "raw": line}

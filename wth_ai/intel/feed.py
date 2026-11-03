"""Continuous intel feed — nuclei templates, CVE hints, wayback delta."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from wth_ai.config import WTHConfig
from wth_ai.models import AttackSurface
from wth_ai.running_actions import get_running_actions, kill_process_tree


class IntelFeed:
    """Poll intel updates relevant to current engagement."""

    def __init__(self, config: WTHConfig | None = None) -> None:
        self.config = config or WTHConfig.load()
        home = Path(os.environ.get("WTH_HOME", Path.home() / ".wth-ai"))
        self.cache_dir = home / "data" / "intel"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.cache_dir / "last_intel.json"

    def _load_state(self) -> dict[str, Any]:
        if self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        return {}

    def _save_state(self, state: dict[str, Any]) -> None:
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def check_nuclei_templates(self) -> dict[str, Any]:
        if not self.config.intel.nuclei_templates_check:
            return {"status": "disabled"}
        try:
            proc = subprocess.Popen(
                ["nuclei", "-update-templates", "-silent"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=(os.name != "nt"),
            )
        except FileNotFoundError:
            return {"status": "unavailable", "error": "nuclei tidak ada di PATH"}
        action_id = get_running_actions().register(proc.pid, label="intel:nuclei-templates")
        try:
            try:
                out, _err = proc.communicate(timeout=120)
            except subprocess.TimeoutExpired:
                kill_process_tree(proc)
                proc.communicate()
                return {"status": "unavailable", "error": "timeout after 120s"}
            return {
                "status": "ok" if proc.returncode == 0 else "failed",
                "stdout": out[-300:] if out else "",
            }
        finally:
            get_running_actions().unregister(action_id)

    def cve_hints_for_tech(self, tech_stack: dict[str, str]) -> list[dict[str, str]]:
        hints: list[dict[str, str]] = []
        for product, version in tech_stack.items():
            if not product or len(product) < 2:
                continue
            try:
                r = requests.get(
                    "https://services.nvd.nist.gov/rest/json/cves/2.0",
                    params={"keywordSearch": product, "resultsPerPage": 3},
                    timeout=15,
                )
                if not r.ok:
                    continue
                data = r.json()
                for item in (data.get("vulnerabilities") or [])[:2]:
                    cve = item.get("cve", {})
                    cve_id = cve.get("id", "")
                    desc = ""
                    for d in cve.get("descriptions", []):
                        if d.get("lang") == "en":
                            desc = d.get("value", "")[:200]
                            break
                    if cve_id:
                        hints.append({"cve": cve_id, "product": product, "version": version, "summary": desc})
            except requests.RequestException:
                continue
        return hints[:10]

    def wayback_delta(self, domain: str, known_urls: list[str]) -> list[str]:
        if not self.config.intel.wayback_delta:
            return []
        state = self._load_state()
        prev = set(state.get(f"wayback_{domain}", []))
        current = set(known_urls)
        new_urls = sorted(current - prev)
        state[f"wayback_{domain}"] = list(current)[-500:]
        self._save_state(state)
        return new_urls[:50]

    def gather(self, surface: AttackSurface | None = None, domain: str = "") -> dict[str, Any]:
        if not self.config.intel.enabled:
            return {"enabled": False}

        result: dict[str, Any] = {"enabled": True}
        result["nuclei"] = self.check_nuclei_templates()

        if surface:
            result["cve_hints"] = self.cve_hints_for_tech(surface.tech_stack)
            if domain or surface.target:
                d = domain or surface.target
                result["wayback_new"] = self.wayback_delta(d, surface.urls)

        self._save_state({"last_gather": result, "domain": domain})
        return result

    def format_report(self, intel: dict[str, Any]) -> str:
        if not intel.get("enabled", True):
            return "Intel feed disabled in config."
        lines = ["## Intel Feed", ""]
        nuc = intel.get("nuclei", {})
        lines.append(f"- Nuclei templates: {nuc.get('status', 'unknown')}")

        cves = intel.get("cve_hints", [])
        if cves:
            lines.append("\n### CVE hints (tech stack)")
            for c in cves[:5]:
                lines.append(f"- `{c['cve']}` ({c['product']}) — {c['summary'][:100]}")

        new_urls = intel.get("wayback_new", [])
        if new_urls:
            lines.append(f"\n### Wayback delta ({len(new_urls)} new URLs)")
            for u in new_urls[:8]:
                lines.append(f"- {u}")

        return "\n".join(lines)

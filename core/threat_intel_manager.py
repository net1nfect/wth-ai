"""Rate-limited public threat intelligence aggregation and correlation."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import aiohttp


class ThreatIntelligenceManager:
    """Aggregate and correlate public defensive threat intelligence."""

    PUBLIC_FEEDS = {
        "cisa_kev": "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
    }

    def __init__(self, config: Any, logger: Any) -> None:
        self.config = config
        self.logger = logger
        self.cache: dict[str, tuple[float, Any]] = {}
        self.available_feeds: dict[str, str] = {}

    async def initialize_feeds(self) -> None:
        """Select configured supported public feeds."""
        self.available_feeds = {
            name: url for name, url in self.PUBLIC_FEEDS.items()
            if name in self.config.enabled_feeds
        }
        self.logger.info("Initialized %d threat intelligence feeds", len(self.available_feeds))

    async def _fetch(self, session: aiohttp.ClientSession, name: str, url: str) -> tuple[str, Any]:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                response.raise_for_status()
                return name, await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as exc:
            self.logger.warning("Threat feed %s unavailable: %s", name, exc)
            return name, {}

    async def fetch_live_intelligence(self) -> dict[str, Any]:
        """Fetch configured feeds concurrently, with cached fallback."""
        if not self.available_feeds:
            await self.initialize_feeds()
        async with aiohttp.ClientSession(headers={"User-Agent": "H1T4M-AI/3.0 defensive-research"}) as session:
            results = await asyncio.gather(
                *(self._fetch(session, name, url) for name, url in self.available_feeds.items())
            )
        now = time.monotonic()
        for name, data in results:
            if data:
                self.cache[name] = (now, data)
        return {name: data for name, (_, data) in self.cache.items()}

    async def correlate_vulnerabilities(self, tech_stack: list[str]) -> list[dict[str, Any]]:
        """Match normalized technology terms against cached CVE descriptions."""
        intelligence = await self.fetch_live_intelligence()
        terms = {item.lower() for item in tech_stack if item}
        matches: list[dict[str, Any]] = []
        for item in intelligence.get("cisa_kev", {}).get("vulnerabilities", []):
            text = f"{item.get('vendorProject', '')} {item.get('product', '')} {item.get('vulnerabilityName', '')}".lower()
            if any(term in text for term in terms):
                matches.append(item)
        return matches

    async def get_exploit_recommendations(self, vuln_id: str) -> list[dict[str, Any]]:
        """Return defensive validation and remediation recommendations."""
        return [
            {
                "vulnerability_id": vuln_id,
                "action": "validate_with_vendor_advisory",
                "execution": "dry_run",
                "recommendation": "Patch affected assets and verify with a non-destructive check.",
            }
        ]

    def predict_exploitability(self, vuln: dict[str, Any]) -> float:
        """Estimate exploitability from public signals."""
        score = 0.2
        score += 0.5 if vuln.get("knownRansomwareCampaignUse") == "Known" else 0.0
        score += 0.3 if vuln.get("dateAdded") else 0.0
        return min(score, 1.0)

    def cache_intelligence(self, ttl: int) -> None:
        """Remove expired cache entries."""
        now = time.monotonic()
        self.cache = {key: value for key, value in self.cache.items() if now - value[0] <= ttl}

    async def get_threat_actor_info(self, target: str) -> dict[str, Any]:
        """Return an explicit unsupported response for actor attribution."""
        return {"target": target, "status": "not_attributed", "reason": "No verified attribution source configured"}

    def detect_active_exploits_in_wild(self, vuln: dict[str, Any]) -> bool:
        """Return whether public intelligence marks active exploitation."""
        return bool(vuln.get("dateAdded") or vuln.get("known_exploited"))

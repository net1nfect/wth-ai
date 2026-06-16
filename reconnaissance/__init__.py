"""Ten-phase authorized reconnaissance pipeline."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse


class ReconPhase:
    """Base deterministic reconnaissance phase."""

    phase = 0
    name = "base"

    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        """Process existing observations without active probing."""
        return {
            "phase": self.phase,
            "name": self.name,
            "target": context.get("target"),
            "mode": context.get("mode", "passive"),
            "observations": [],
        }


def target_host(context: dict[str, Any]) -> str:
    """Extract a normalized host from context."""
    target = str(context.get("target", "")).strip()
    parsed = urlparse(target if "://" in target else f"https://{target}")
    return (parsed.hostname or "").lower()


from .phase1_subdomain_enum import SubdomainEnumerationPhase
from .phase2_dns_resolution import DNSResolutionPhase
from .phase3_http_probing import HTTPProbingPhase
from .phase4_content_discovery import ContentDiscoveryPhase
from .phase5_parameter_mining import ParameterMiningPhase
from .phase6_js_analysis import JavaScriptAnalysisPhase
from .phase7_secret_detection import SecretDetectionPhase
from .phase8_cloud_enum import CloudEnumerationPhase
from .phase9_ssl_analysis import SSLAnalysisPhase
from .phase10_fingerprinting import FingerprintingPhase

PHASES = {
    1: SubdomainEnumerationPhase,
    2: DNSResolutionPhase,
    3: HTTPProbingPhase,
    4: ContentDiscoveryPhase,
    5: ParameterMiningPhase,
    6: JavaScriptAnalysisPhase,
    7: SecretDetectionPhase,
    8: CloudEnumerationPhase,
    9: SSLAnalysisPhase,
    10: FingerprintingPhase,
}

__all__ = ["PHASES", "ReconPhase"]

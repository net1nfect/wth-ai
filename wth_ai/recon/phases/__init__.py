"""Recon phases package."""

from wth_ai.recon.phases.base import BasePhase, PhaseContext
from wth_ai.recon.phases.phase01_subdomain import Phase01Subdomain
from wth_ai.recon.phases.phase02_dns import Phase02Dns
from wth_ai.recon.phases.phase03_http import Phase03Http
from wth_ai.recon.phases.phase04_crawl import Phase04Crawl
from wth_ai.recon.phases.phase05_params import Phase05Params
from wth_ai.recon.phases.phase06_js_secrets import Phase06JsSecrets
from wth_ai.recon.phases.phase07_cloud import Phase07Cloud
from wth_ai.recon.phases.phase08_ssl import Phase08Ssl
from wth_ai.recon.phases.phase09_fingerprint import Phase09Fingerprint
from wth_ai.recon.phases.phase10_tech_stack import Phase10TechStack
from wth_ai.recon.phases.phase11_api_schema import Phase11ApiSchema

from wth_ai.recon.phases.phase12_live_agent import Phase12LiveAgent

PHASES: dict[int, type[BasePhase]] = {
    1: Phase01Subdomain,
    2: Phase02Dns,
    3: Phase03Http,
    4: Phase04Crawl,
    5: Phase05Params,
    6: Phase06JsSecrets,
    7: Phase07Cloud,
    8: Phase08Ssl,
    9: Phase09Fingerprint,
    10: Phase10TechStack,
    11: Phase11ApiSchema,
    12: Phase12LiveAgent,
}

__all__ = ["PHASES", "BasePhase", "PhaseContext"]

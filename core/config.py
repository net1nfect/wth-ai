"""Validated YAML, environment, and default configuration management."""

from __future__ import annotations

import copy
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .exceptions import ConfigurationError


AGENT_NAMES = [
    "reconnaissance",
    "exploitation",
    "privilege_escalation",
    "lateral_movement",
    "persistence_evasion",
    "data_extraction",
    "post_exploitation",
    "learning_adaptation",
    "reporting",
]


@dataclass
class EngagementConfig:
    """Engagement boundaries and authorization."""

    target: str = ""
    scope: list[str] = field(default_factory=list)
    objectives: list[str] = field(default_factory=list)
    duration_limit: int = 8
    budget_limit: int = 1000
    authorization_proof: str = ""


@dataclass
class ReconnaissanceConfig:
    """Reconnaissance controls."""

    phases_to_run: list[int] = field(default_factory=lambda: list(range(1, 11)))
    thread_count: dict[str, int] = field(default_factory=lambda: {"default": 10})
    timeout_values: dict[str, int] = field(default_factory=lambda: {"default": 30})
    tool_selection: dict[str, bool] = field(default_factory=dict)
    ai_enhancement_level: str = "advanced"


@dataclass
class ExploitationConfig:
    """Safe validation controls."""

    payload_generation_mode: str = "template"
    waf_evasion_strategy: str = "static"
    encoding_layers: int = 1
    mutation_aggressiveness: int = 1
    detection_evasion_level: int = 1
    dry_run: bool = True
    require_human_approval: bool = True


@dataclass
class AgentConfig:
    """Multi-agent controls."""

    enable_agents: list[str] = field(default_factory=lambda: list(AGENT_NAMES))
    agent_parallelization: bool = True
    agent_communication_timeout: int = 300
    agent_resource_limits: dict[str, int] = field(default_factory=lambda: {"concurrency": 10})


@dataclass
class LearningConfig:
    """Learning subsystem controls."""

    enable_reinforcement_learning: bool = True
    learning_update_frequency: int = 10
    model_persistence: bool = True
    learning_data_retention: int = 90


@dataclass
class ThreatIntelConfig:
    """Threat intelligence controls."""

    enabled_feeds: list[str] = field(default_factory=lambda: ["cisa_kev", "nvd"])
    update_frequency: int = 60
    api_keys: dict[str, str] = field(default_factory=dict)
    dark_web_monitoring: bool = False


@dataclass
class ReportingConfig:
    """Reporting controls."""

    report_format: list[str] = field(default_factory=lambda: ["json", "html"])
    include_pocs: bool = False
    remediation_detail_level: str = "detailed"
    compliance_frameworks: list[str] = field(default_factory=lambda: ["GDPR", "HIPAA", "PCI-DSS"])


@dataclass
class SecurityConfig:
    """Security and audit controls."""

    audit_logging_enabled: bool = True
    log_retention_days: int = 90
    evidence_preservation: bool = True
    cleanup_on_exit: bool = True
    requests_per_second: float = 2.0


@dataclass
class DatabaseConfig:
    """Database controls."""

    url: str = "sqlite:///data/h1t4m.db"
    encryption_key_env: str = "H1T4M_ENCRYPTION_KEY"


class H1T4MConfiguration:
    """Load validated configuration from YAML, environment, and defaults."""

    _sections = {
        "engagement": EngagementConfig,
        "reconnaissance": ReconnaissanceConfig,
        "exploitation": ExploitationConfig,
        "agents": AgentConfig,
        "learning": LearningConfig,
        "threat_intel": ThreatIntelConfig,
        "reporting": ReportingConfig,
        "security": SecurityConfig,
        "database": DatabaseConfig,
    }

    def __init__(self, path: str | Path = "config/h1t4m_config.yaml") -> None:
        self.path = Path(path)
        self._mtime: float | None = None
        self.load()

    def load(self) -> None:
        """Load, override, instantiate, and validate all sections."""
        raw: dict[str, Any] = {}
        if self.path.exists():
            try:
                raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
                self._mtime = self.path.stat().st_mtime
            except (OSError, yaml.YAMLError) as exc:
                raise ConfigurationError(f"Unable to load configuration: {exc}") from exc
        raw = self._apply_environment(raw)
        for name, section_type in self._sections.items():
            values = raw.get(name, {})
            try:
                setattr(self, name, section_type(**values))
            except TypeError as exc:
                raise ConfigurationError(f"Invalid {name} configuration: {exc}") from exc
        self.validate()

    def _apply_environment(self, raw: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(raw)
        mappings = {
            "H1T4M_TARGET": ("engagement", "target"),
            "H1T4M_AUTHORIZATION_PROOF": ("engagement", "authorization_proof"),
            "H1T4M_DATABASE_URL": ("database", "url"),
        }
        for variable, (section, key) in mappings.items():
            if variable in os.environ:
                result.setdefault(section, {})[key] = os.environ[variable]
        return result

    def validate(self) -> None:
        """Validate bounds and allowed values."""
        if self.engagement.duration_limit <= 0 or self.engagement.budget_limit <= 0:
            raise ConfigurationError("Engagement duration and budget limits must be positive")
        if not set(self.reconnaissance.phases_to_run).issubset(set(range(1, 11))):
            raise ConfigurationError("Reconnaissance phases must be between 1 and 10")
        if self.reconnaissance.ai_enhancement_level not in {"basic", "advanced", "expert"}:
            raise ConfigurationError("Invalid AI enhancement level")
        if self.exploitation.payload_generation_mode not in {"template", "ai-generated", "hybrid"}:
            raise ConfigurationError("Invalid payload generation mode")
        if self.exploitation.waf_evasion_strategy not in {"static", "dynamic", "learning"}:
            raise ConfigurationError("Invalid WAF strategy")
        if not 1 <= self.exploitation.encoding_layers <= 5:
            raise ConfigurationError("encoding_layers must be between 1 and 5")
        if not 1 <= self.exploitation.mutation_aggressiveness <= 10:
            raise ConfigurationError("mutation_aggressiveness must be between 1 and 10")
        if not 1 <= self.exploitation.detection_evasion_level <= 10:
            raise ConfigurationError("detection_evasion_level must be between 1 and 10")
        if unknown := set(self.agents.enable_agents) - set(AGENT_NAMES):
            raise ConfigurationError(f"Unknown agents: {sorted(unknown)}")
        if self.agents.agent_communication_timeout <= 0:
            raise ConfigurationError("Agent communication timeout must be positive")
        if self.learning.learning_update_frequency <= 0 or self.learning.learning_data_retention <= 0:
            raise ConfigurationError("Learning frequency and retention must be positive")
        if self.threat_intel.update_frequency <= 0:
            raise ConfigurationError("Threat intelligence update frequency must be positive")
        if not set(self.reporting.report_format).issubset({"json", "html", "pdf"}):
            raise ConfigurationError("Supported report formats are json, html, and pdf")
        if self.reporting.remediation_detail_level not in {"basic", "detailed", "expert"}:
            raise ConfigurationError("Invalid remediation detail level")
        if self.security.log_retention_days <= 0:
            raise ConfigurationError("Log retention must be positive")
        if self.security.requests_per_second <= 0:
            raise ConfigurationError("requests_per_second must be positive")

    def hot_reload(self) -> bool:
        """Reload configuration when the primary file changed."""
        if not self.path.exists() or self.path.stat().st_mtime == self._mtime:
            return False
        self.load()
        return True

    def as_dict(self, mask_secrets: bool = True) -> dict[str, Any]:
        """Return serializable configuration."""
        result = {name: asdict(getattr(self, name)) for name in self._sections}
        if mask_secrets:
            result["engagement"]["authorization_proof"] = "***" if self.engagement.authorization_proof else ""
            result["threat_intel"]["api_keys"] = {
                key: "***" for key in self.threat_intel.api_keys
            }
        return result

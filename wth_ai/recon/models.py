# wth_ai/recon/models.py
"""
Data models for Recon Engine (Fase 5.1)

Keep it simple and aligned with existing manifest.yaml structure.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Literal


@dataclass
class ToolSpec:
    """Represents one tool from manifest.yaml"""
    name: str
    binary: str
    category: str
    phases: List[int] = field(default_factory=list)
    permission: Literal["read_only", "standard", "elevated", "full_lab"] = "read_only"
    enabled_by_default: bool = True
    risk_level: Literal["safe", "medium", "high"] = "safe"
    requires_network: bool = True
    requires_api_key: bool = False
    install: Dict = field(default_factory=dict)
    verify: str = ""
    notes: str = ""

    def is_available(self) -> bool:
        """Check if binary exists in PATH (simple implementation)"""
        import shutil
        return shutil.which(self.binary) is not None


@dataclass
class ReconPlan:
    """Output of the planner"""
    target: str
    profile: str
    selected_tools: List[ToolSpec]
    skipped_tools: List[Dict]  # reason why skipped
    total_tools: int = 0


@dataclass
class ToolRunResult:
    """Result of running one tool"""
    tool: str
    status: Literal["success", "skipped", "failed", "timeout"]
    started_at: str = ""
    finished_at: str = ""
    duration_ms: int = 0
    output_path: Optional[str] = None
    error: Optional[str] = None
    normalized_items: List[Dict] = field(default_factory=list)

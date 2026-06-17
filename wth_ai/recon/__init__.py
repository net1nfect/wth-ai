# wth_ai/recon/__init__.py
"""
Recon Engine - Fase 5.1 Foundation

Extends existing recon components with structured Tool Registry + Planner.

Follows AGENTS.md strictly:
- CLI/library-first testing
- All secrets via vault
- High-risk tools respect permission levels
"""

# Keep backward compatibility with existing code
try:
    from wth_ai.recon.graph import AdaptiveReconGraph
    from wth_ai.recon.orchestrator import ReconOrchestrator
    from wth_ai.recon.tool_pipeline import ToolPipeline
except ImportError:
    pass

# New Fase 5.1 components
from .models import ToolSpec, ReconPlan, ToolRunResult
from .registry import ToolRegistry

__all__ = [
    "ToolRegistry",
    "ToolSpec",
    "ReconPlan",
    "ToolRunResult",
    # legacy
    "ReconOrchestrator",
    "AdaptiveReconGraph",
    "ToolPipeline",
]

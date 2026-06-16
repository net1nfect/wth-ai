"""Autonomous agent implementations."""

from .data_extraction_agent import DataExtractionAgent
from .exploitation_agent import ExploitationAgent
from .lateral_movement_agent import LateralMovementAgent
from .learning_adaptation_agent import LearningAdaptationAgent
from .persistence_evasion_agent import PersistenceEvasionAgent
from .post_exploitation_agent import PostExploitationAgent
from .privilege_escalation_agent import PrivilegeEscalationAgent
from .reconnaissance_agent import ReconnaissanceAgent
from .reporting_agent import ReportingAgent

AGENT_CLASSES = {
    "reconnaissance": ReconnaissanceAgent,
    "exploitation": ExploitationAgent,
    "privilege_escalation": PrivilegeEscalationAgent,
    "lateral_movement": LateralMovementAgent,
    "persistence_evasion": PersistenceEvasionAgent,
    "data_extraction": DataExtractionAgent,
    "post_exploitation": PostExploitationAgent,
    "learning_adaptation": LearningAdaptationAgent,
    "reporting": ReportingAgent,
}

__all__ = ["AGENT_CLASSES"]

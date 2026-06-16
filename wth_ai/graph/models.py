"""Graph node/edge models for attack surface."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class NodeType(str, Enum):
    HOST = "host"
    ENDPOINT = "endpoint"
    PARAMETER = "parameter"
    AUTH_ROLE = "auth_role"
    JS_BUNDLE = "js_bundle"
    SECRET = "secret"
    TECH_STACK = "tech_stack"
    AUTH_FLOW = "auth_flow"


class EdgeType(str, Enum):
    CALLS = "calls"
    REQUIRES_AUTH = "requires_auth"
    REDIRECTS_TO = "redirects_to"
    PARAM_OF = "param_of"
    OWNED_BY_ROLE = "owned_by_role"
    HOSTED_ON = "hosted_on"
    DISCOVERED_IN = "discovered_in"


@dataclass
class GraphNode:
    id: str
    type: NodeType
    label: str
    data: dict[str, Any] = field(default_factory=dict)
    tested: bool = False
    priority: int = 5

    @classmethod
    def create(cls, ntype: NodeType, label: str, **data: Any) -> GraphNode:
        return cls(
            id=f"{ntype.value}-{uuid4().hex[:10]}",
            type=ntype,
            label=label,
            data=data,
            priority=int(data.get("priority", 5)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "label": self.label,
            "data": self.data,
            "tested": self.tested,
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GraphNode:
        return cls(
            id=d["id"],
            type=NodeType(d["type"]),
            label=d["label"],
            data=d.get("data", {}),
            tested=d.get("tested", False),
            priority=d.get("priority", 5),
        )


@dataclass
class GraphEdge:
    source: str
    target: str
    type: EdgeType
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "target": self.target, "type": self.type.value, "data": self.data}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GraphEdge:
        return cls(
            source=d["source"],
            target=d["target"],
            type=EdgeType(d["type"]),
            data=d.get("data", {}),
        )

"""OpenRouter tool definitions for agent terminal access."""

from __future__ import annotations

from typing import Any

TOOL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "query_graph": {
        "type": "function",
        "function": {
            "name": "query_graph",
            "description": "Query asset graph summary and high-value parameters",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max params to return", "default": 10},
                },
            },
        },
    },
    "http_probe": {
        "type": "function",
        "function": {
            "name": "http_probe",
            "description": "Send HTTP request (curl-like) to in-scope URL. Returns TX transcript id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "method": {"type": "string", "enum": ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]},
                    "url": {"type": "string"},
                    "purpose": {"type": "string"},
                },
                "required": ["url"],
            },
        },
    },
    "run_shell": {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Run shell command (httpx, nuclei, curl, etc.) gated by permission mode",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "purpose": {"type": "string"},
                },
                "required": ["command"],
            },
        },
    },
    "store_evidence": {
        "type": "function",
        "function": {
            "name": "store_evidence",
            "description": "Link transcript to finding with evidence schema",
            "parameters": {
                "type": "object",
                "properties": {
                    "transcript_id": {"type": "string"},
                    "finding_id": {"type": "string"},
                    "vuln_class": {"type": "string"},
                },
                "required": ["transcript_id"],
            },
        },
    },
}

ACTOR_TOOLS: dict[str, list[str]] = {
    "planner": ["query_graph", "http_probe", "store_evidence"],
    "executor": ["query_graph", "http_probe", "run_shell", "store_evidence"],
    "recon": ["query_graph", "http_probe", "run_shell"],
}


def tools_for_actor(actor: str) -> list[dict[str, Any]]:
    names = ACTOR_TOOLS.get(actor, ACTOR_TOOLS["executor"])
    return [TOOL_DEFINITIONS[n] for n in names if n in TOOL_DEFINITIONS]

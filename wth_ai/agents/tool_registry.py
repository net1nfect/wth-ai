"""OpenRouter tool definitions for supervisor agent."""

from __future__ import annotations

from typing import Any

TOOL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "scope_set": {
        "type": "function",
        "function": {
            "name": "scope_set",
            "description": "Set assessment scope: target domain/URL, authorization, environment",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "authorized": {"type": "boolean"},
                    "environment": {"type": "string", "enum": ["production", "staging", "development", "local"]},
                    "in_scope": {"type": "array", "items": {"type": "string"}},
                    "rate_limit_rps": {"type": "number"},
                },
                "required": ["target"],
            },
        },
    },
    "scope_show": {
        "type": "function",
        "function": {
            "name": "scope_show",
            "description": "Show current scope configuration",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "permission_set": {
        "type": "function",
        "function": {
            "name": "permission_set",
            "description": "Set permission mode for testing",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        # Real PermissionMode values. The previous enum listed
                        # "read_only_recon"/"standard_testing"/"elevated_testing",
                        # none of which `PermissionMode(value)` accepts -- so the
                        # model could only ever set full_lab.
                        "enum": ["read_only", "standard", "elevated", "full_lab", "unrestricted"],
                    },
                },
                "required": ["mode"],
            },
        },
    },
    "session_status": {
        "type": "function",
        "function": {
            "name": "session_status",
            "description": "Session phase, graph coverage, findings count, task queue",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "recon_list_tools": {
        "type": "function",
        "function": {
            "name": "recon_list_tools",
            "description": "List recon tools from registry (filter by category, availability)",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "available_only": {"type": "boolean", "default": False},
                },
            },
        },
    },
    "recon_run_tool": {
        "type": "function",
        "function": {
            "name": "recon_run_tool",
            "description": "Run one recon tool safely (builtin or external)",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string"},
                    "target": {"type": "string"},
                },
                "required": ["tool_name"],
            },
        },
    },
    "recon_finalize": {
        "type": "function",
        "function": {
            "name": "recon_finalize",
            "description": "Normalize recon results, score priority targets, write report",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "query_graph": {
        "type": "function",
        "function": {
            "name": "query_graph",
            "description": "Query asset graph summary and high-value parameters",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 10},
                },
            },
        },
    },
    "http_probe": {
        "type": "function",
        "function": {
            "name": "http_probe",
            "description": "Send HTTP request to in-scope URL. Returns TX transcript id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "method": {"type": "string", "enum": ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]},
                    "url": {"type": "string"},
                    "purpose": {"type": "string"},
                    "headers": {
                        "type": "object",
                        "description": (
                            "Optional request headers. For a target credential "
                            "(e.g. Authorization/session token), use a "
                            "'vault:<name>' reference as the value instead of "
                            "the plaintext secret -- it is resolved just-in-time "
                            "server-side and the real value is never sent back "
                            "to you or stored in any transcript."
                        ),
                    },
                },
                "required": ["url"],
            },
        },
    },
    "run_shell": {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Run shell command gated by permission mode",
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
    "plan_hunt": {
        "type": "function",
        "function": {
            "name": "plan_hunt",
            "description": "Generate hunt plan and task queue from graph and goal",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                },
                "required": ["goal"],
            },
        },
    },
    "execute_task": {
        "type": "function",
        "function": {
            "name": "execute_task",
            "description": "Execute a task by id from the queue, or next queued task",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                },
            },
        },
    },
    "verify_finding": {
        "type": "function",
        "function": {
            "name": "verify_finding",
            "description": "Verify a finding candidate with evidence gate",
            "parameters": {
                "type": "object",
                "properties": {
                    "finding_id": {"type": "string"},
                },
                "required": ["finding_id"],
            },
        },
    },
    "list_findings": {
        "type": "function",
        "function": {
            "name": "list_findings",
            "description": "List findings and hypotheses in current session",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "generate_report": {
        "type": "function",
        "function": {
            "name": "generate_report",
            "description": "Generate markdown security assessment report",
            "parameters": {"type": "object", "properties": {}},
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
    "job_create_recon": {
        "type": "function",
        "function": {
            "name": "job_create_recon",
            "description": "Create background recon job via job manager",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "profile": {"type": "string", "default": "recon-only"},
                },
                "required": ["target"],
            },
        },
    },
    "job_poll": {
        "type": "function",
        "function": {
            "name": "job_poll",
            "description": "Poll status of a background job",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                },
                "required": ["job_id"],
            },
        },
    },
    "delegate_task": {
        "type": "function",
        "function": {
            "name": "delegate_task",
            "description": "Spawn a focused subagent (injection, xss, auth, authz, ssrf, infra, ctf, reporter, recon)",
            "parameters": {
                "type": "object",
                "properties": {
                    "subagent": {"type": "string"},
                    "prompt": {"type": "string"},
                },
                "required": ["subagent", "prompt"],
            },
        },
    },
    "session_search": {
        "type": "function",
        "function": {
            "name": "session_search",
            "description": "Search FTS session memory across prior turns",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                },
                "required": ["query"],
            },
        },
    },
}

ACTOR_TOOLS: dict[str, list[str]] = {
    "supervisor": list(TOOL_DEFINITIONS.keys()),
    "planner": ["query_graph", "http_probe", "store_evidence", "scope_show", "session_status"],
    "executor": ["query_graph", "http_probe", "run_shell", "store_evidence"],
    "recon": ["query_graph", "http_probe", "run_shell", "recon_list_tools", "recon_run_tool", "recon_finalize"],
}


def tools_for_actor(actor: str) -> list[dict[str, Any]]:
    names = ACTOR_TOOLS.get(actor, ACTOR_TOOLS["supervisor"])
    tools = [TOOL_DEFINITIONS[n] for n in names if n in TOOL_DEFINITIONS]
    try:
        from wth_ai.config import WTHConfig
        from wth_ai.mcp.manager import MCPClientManager
        cfg = WTHConfig.load()
        if cfg.mcp.enabled:
            mgr = MCPClientManager(cfg)
            mgr.connect_all()
            for server, mcp_tools in mgr.list_tools().items():
                for t in mcp_tools[:20]:
                    tname = t.get("name", "tool")
                    tools.append({
                        "type": "function",
                        "function": {
                            "name": f"mcp_{server}_{tname}",
                            "description": f"MCP {server}/{tname}",
                            "parameters": t.get("inputSchema", {"type": "object", "properties": {}}),
                        },
                    })
    except Exception:  # noqa: BLE001
        pass
    return tools

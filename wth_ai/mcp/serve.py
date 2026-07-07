"""MCP server — expose read-only WTH tools."""

from __future__ import annotations

import json
import sys
from typing import Any

from wth_ai.config import WTHConfig


READ_ONLY_TOOLS = ("graph_export", "recon_plan", "findings_list")


class McpServeRegistry:
    def __init__(self, config: WTHConfig | None = None, *, session_id: str | None = None) -> None:
        self.config = config or WTHConfig.load()
        self.session_id = session_id

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": n,
                "description": f"Read-only {n}",
                "inputSchema": {"type": "object", "properties": {}},
            }
            for n in READ_ONLY_TOOLS
        ]

    def call(self, name: str, args: dict[str, Any], *, scope_token: str | None = None) -> Any:
        if name not in READ_ONLY_TOOLS and not scope_token:
            return {"error": "mutating tools require scope token"}
        if name == "graph_export":
            from wth_ai.graph.asset_graph import AssetGraph
            sid = self.session_id or "default"
            g = AssetGraph(sid)
            return g.export_for_planner(limit=50)
        if name == "recon_plan":
            target = args.get("target", "")
            from wth_ai.recon.planner import ReconPlanner
            from wth_ai.recon.registry import ReconToolRegistry
            plan = ReconPlanner(ReconToolRegistry()).plan(
                target, profile=args.get("profile", "recon-only")
            )
            return plan.to_dict() if hasattr(plan, "to_dict") else str(plan)
        if name == "findings_list":
            from wth_ai.findings_db import FindingsDB
            from wth_ai.session_store import SessionStore
            db = FindingsDB()
            if self.session_id:
                state = SessionStore(self.config).load(self.session_id)
                if state:
                    db.load_from_dict({"findings": state.findings, "hypotheses": state.hypotheses})
            return {"findings": [f.to_dict() for f in db.list_findings()[:50]]}
        return {"error": f"unknown tool {name}"}

    def handle_request(self, req: dict[str, Any]) -> dict[str, Any]:
        """Handle MCP JSON-RPC style request."""
        method = req.get("method", "")
        req_id = req.get("id")
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": self.list_tools()}}
        if method == "tools/call":
            params = req.get("params", {})
            name = params.get("name", "")
            arguments = params.get("arguments", {})
            result = self.call(name, arguments)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps(result)}]},
            }
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"unknown method {method}"}}

    def serve_stdio(self) -> None:
        """JSON-RPC stdio loop (MCP-compatible subset)."""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                continue
            resp = self.handle_request(req)
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()

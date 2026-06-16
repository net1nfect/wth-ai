"""Human approval gate for high-risk actions."""

from __future__ import annotations

from typing import Any
from uuid import uuid4


class ApprovalGate:
    """Queue and track explicit user approvals for risky operations."""

    def __init__(self) -> None:
        self._pending: dict[str, dict[str, Any]] = {}
        self._approved: set[str] = set()

    def request(self, action: str, reason: str, task_id: str = "") -> str:
        approval_id = f"APR-{uuid4().hex[:8]}"
        self._pending[approval_id] = {
            "id": approval_id,
            "action": action,
            "reason": reason,
            "task_id": task_id,
            "status": "pending",
        }
        return approval_id

    def approve(self, approval_id: str) -> bool:
        if approval_id not in self._pending:
            return False
        self._pending[approval_id]["status"] = "approved"
        self._approved.add(approval_id)
        return True

    def deny(self, approval_id: str) -> bool:
        if approval_id not in self._pending:
            return False
        self._pending[approval_id]["status"] = "denied"
        return True

    def is_approved(self, approval_id: str) -> bool:
        return approval_id in self._approved

    def is_approved_for_task(self, task_id: str) -> bool:
        return any(
            p.get("task_id") == task_id and p.get("status") == "approved"
            for p in self._pending.values()
        )

    def list_pending(self) -> list[dict[str, Any]]:
        return [p for p in self._pending.values() if p.get("status") == "pending"]

    def format_pending(self) -> str:
        pending = self.list_pending()
        if not pending:
            return "Tidak ada approval pending."
        lines = ["PENDING APPROVALS:"]
        for p in pending:
            lines.append(f"- [{p['id']}] {p['action']}")
            lines.append(f"  Reason: {p['reason']}")
            lines.append(f"  Approve: /approve {p['id']}")
        return "\n".join(lines)

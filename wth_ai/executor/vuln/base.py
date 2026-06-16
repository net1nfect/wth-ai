"""Base vuln-class executor."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from wth_ai.http.session_engine import SessionEngine
from wth_ai.models import ExecutorResult, ExecutorTask, SessionState


class BaseVulnExecutor(ABC):
  vuln_type: str = "unknown"

  def __init__(self, state: SessionState) -> None:
      self.state = state
      self.sessions = SessionEngine(state.scope, rate_limit_rps=state.scope.rate_limit_rps)

  @abstractmethod
  def execute(self, task: ExecutorTask) -> ExecutorResult:
      ...

  def _result(
      self,
      task: ExecutorTask,
      *,
      status: str,
      summary: str,
      evidence: list[str],
      observations: list[str],
      risk: str = "medium",
      next_step: str = "/verify",
      request_pairs: list[dict[str, Any]] | None = None,
  ) -> ExecutorResult:
      if request_pairs:
          self.state.metadata.setdefault("request_pairs", []).extend(request_pairs)
      return ExecutorResult(
          task_id=task.id,
          status=status,
          permission_check={"mode": task.permission_required.value, "allowed": True},
          commands_used=[f"vuln:{self.vuln_type}"],
          output_summary=summary,
          evidence=evidence,
          observations=observations,
          risk=risk,
          recommended_next_step=next_step,
          errors="none",
      )

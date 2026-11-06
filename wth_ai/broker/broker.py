"""The execution broker: one place where an intent becomes a tool run.

Call sites (`AgentTerminalBridge.dispatch_tool`, `tool_loop`, supervisor
dispatch) hand over an :class:`~wth_ai.broker.models.ActionIntent`; the broker
picks the transport, invokes the bridge, and returns a uniform
:class:`~wth_ai.broker.models.ExecutionResult`.

It deliberately adds **no** policy of its own: permission mode, scope,
denylist, rate limit and approval all still run inside the bridge methods it
calls. Duplicating any of those checks here would create a second source of
truth that can drift -- the exact bug class WU-104 removed from the policy
engine.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from wth_ai.audit import audit_log
from wth_ai.broker.models import ActionIntent, ExecutionResult

if TYPE_CHECKING:  # pragma: no cover - typing only
    from wth_ai.terminal.bridge import AgentTerminalBridge

#: Tool names that mean "send one HTTP request".
_HTTP_TOOLS = frozenset({"http_probe", "http_request", "probe", "http"})
#: Tool names that mean "run this shell command".
_SHELL_TOOLS = frozenset(
    {"run_shell", "shell", "terminal", "exec", "execute_command", "run_command", "bash"}
)


def _valid_http_args(args: dict[str, Any]) -> str | None:
    """Validasi tipe argumen sebelum `str()` memaksa nilai apa pun jadi teks.

    P3-4: `str(args.get("url"))` mengubah `123` menjadi URL sah dan
    `["ls","-la"]` menjadi command yang tampak normal, jadi broker bisa
    menjalankan sesuatu yang tidak pernah diklasifikasi pemanggil.
    """
    url = args.get("url")
    if not isinstance(url, str) or not url.strip():
        return "argumen `url` harus string non-kosong"
    method = args.get("method", "GET")
    if not isinstance(method, str) or not method.strip():
        return "argumen `method` harus string non-kosong"
    headers = args.get("headers")
    if headers is not None and not isinstance(headers, dict):
        return "argumen `headers` harus dict"
    body = args.get("body")
    if body is not None and not isinstance(body, (str, bytes)):
        return "argumen `body` harus string atau bytes"
    return None


def _valid_shell_args(args: dict[str, Any]) -> str | None:
    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        return "argumen `command` harus string non-kosong"
    return None


class ExecutionBroker:
    """Routes intents to the bridge method that can carry them out."""

    def __init__(self, bridge: "AgentTerminalBridge") -> None:
        self.bridge = bridge

    def execute(self, intent: ActionIntent) -> ExecutionResult:
        try:
            result = self._dispatch(intent)
        except Exception as exc:  # noqa: BLE001 - one shape for every failure
            audit_log(
                "execution_failed",
                intent_id=intent.intent_id,
                tool=intent.tool_name,
                actor=intent.actor,
                error=f"{type(exc).__name__}: {exc}",
            )
            return ExecutionResult(
                actor=intent.actor,
                purpose=intent.purpose,
                command=intent.command,
                stderr=f"{type(exc).__name__}: {exc}",
                exit_code=1,
            )
        if result.blocked:
            audit_log(
                "execution_blocked",
                intent_id=intent.intent_id,
                tool=intent.tool_name,
                actor=intent.actor,
                command=intent.command,
                reason=result.block_reason,
            )
        return result

    def _dispatch(self, intent: ActionIntent) -> ExecutionResult:
        # P2-7 (audit 2026-11-06): klasifikasi berdasarkan tool_name, bukan bentuk
        # args. Versi lama memakai fallback bentuk-args — `"url" in args` atau
        # `"command" in args` — sehingga tool bernama apa pun yang membawa arg
        # `url`/`command` diarahkan ke http/shell. Itu misclassification: sebuah
        # tool tak dikenal (atau tool non-jaringan dengan field `url`) dijalankan
        # sebagai probe HTTP. Sekarang hanya nama yang dikenal yang dirute; nama
        # tak dikenal diblokir dengan alasan eksplisit.
        name = (intent.tool_name or "").strip().lower()
        args: dict[str, Any] = dict(intent.arguments or {})

        if name in _HTTP_TOOLS:
            bad = _valid_http_args(args)
            if bad:
                return ExecutionResult.blocked_for(intent, bad)
            terminal = self.bridge.http_probe(
                str(args.get("method") or "GET"),
                str(args.get("url") or ""),
                actor=intent.actor,
                purpose=intent.purpose,
                headers=args.get("headers") or None,
                body=args.get("body"),
                approved=intent.approved,
            )
            return ExecutionResult.from_terminal(terminal, intent)

        if name in _SHELL_TOOLS:
            bad = _valid_shell_args(args)
            if bad:
                return ExecutionResult.blocked_for(intent, bad)
            terminal = self.bridge.run_shell(
                str(args.get("command") or ""),
                actor=intent.actor,
                purpose=intent.purpose,
                approved=intent.approved,
            )
            return ExecutionResult.from_terminal(terminal, intent)

        return ExecutionResult.blocked_for(
            intent,
            f"Broker tidak mengenal tool '{intent.tool_name}' "
            f"( dikenal: {', '.join(sorted(_HTTP_TOOLS | _SHELL_TOOLS))})",
        )

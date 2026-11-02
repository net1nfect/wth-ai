"""Token + cost accounting — the agent had no idea what it was spending.

Verified gap before this module: `ModelResponse.usage` was ALWAYS `{}` (a single
hardcoded literal in `providers/base.py`), so nothing in the system could answer
"how many tokens has this engagement burned, and on which model?". An autonomous
agent that runs for hours with no budget visibility is a liability, and an
operator cannot judge whether a plan is affordable.

Design:
* providers normalise whatever their API returns into ONE shape
  (`prompt_tokens`, `completion_tokens`, `total_tokens`, `cost_usd`);
* the router records one entry per call into an append-only JSONL ledger under
  `$WTH_HOME/usage/`;
* **no prompt or completion text is ever stored** -- counts, model, role and
  timestamp only. The ledger is a bill, not a transcript.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# OpenAI-compatible field aliases seen across providers.
_IN_KEYS = ("prompt_tokens", "input_tokens", "prompt_eval_count")
_OUT_KEYS = ("completion_tokens", "output_tokens", "eval_count")
_TOTAL_KEYS = ("total_tokens",)
_COST_KEYS = ("cost", "cost_usd", "total_cost")


class BudgetExceeded(RuntimeError):
    """Raised when `WTH_BUDGET_MODE=stop` and the session budget is spent.

    Carries the actionable message (what the limit is, what was used, how to
    raise it) because the caller is usually an agent loop that will show it
    verbatim.
    """


@dataclass(frozen=True)
class BudgetPolicy:
    """How much this run may spend, and what happens when it runs out.

    Scope is SESSION (this process run) by default. Measured defect in the first
    version: the budget was compared against the LIFETIME ledger total, so a
    budget set today read as "already over" because of calls made last week --
    the number was never about the run being bounded.
    """

    limit_tokens: int = 0
    mode: str = "warn"          # warn | stop | off
    scope: str = "session"      # session | total

    @property
    def active(self) -> bool:
        return self.limit_tokens > 0 and self.mode != "off"

    def describe(self) -> str:
        return f"{self.limit_tokens} token (mode={self.mode}, scope={self.scope})"


def load_budget_policy() -> BudgetPolicy:
    """Read the policy from the environment, tolerating garbage."""
    def _int(name: str) -> int:
        try:
            return max(0, int(os.environ.get(name, "0") or 0))
        except ValueError:
            return 0

    mode = (os.environ.get("WTH_BUDGET_MODE", "warn") or "warn").strip().lower()
    if mode not in ("warn", "stop", "off"):
        mode = "warn"
    scope = (os.environ.get("WTH_BUDGET_SCOPE", "session") or "session").strip().lower()
    if scope not in ("session", "total"):
        scope = "session"
    return BudgetPolicy(limit_tokens=_int("WTH_TOKEN_BUDGET"), mode=mode, scope=scope)


def _first_int(payload: dict[str, Any], keys: tuple[str, ...]) -> int:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return 0


def _first_float(payload: dict[str, Any], keys: tuple[str, ...]) -> float:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                continue
    return 0.0


def normalize_usage(payload: Any) -> dict[str, Any]:
    """Map a provider's usage object to the one canonical shape.

    Accepts OpenAI-style (`prompt_tokens`/`completion_tokens`), OpenRouter's
    variant (same names plus `cost`), Anthropic-style
    (`input_tokens`/`output_tokens`) and Ollama's
    (`prompt_eval_count`/`eval_count`). An unrecognised shape yields zeros rather
    than an exception: accounting must never break a call that succeeded.
    """
    if not isinstance(payload, dict):
        return {}
    usage_block = payload.get("usage")
    if isinstance(usage_block, dict):
        merged: dict[str, Any] = dict(usage_block)
        for key in _COST_KEYS:                      # cost sometimes sits outside
            if key not in merged and key in payload:
                merged[key] = payload[key]
    else:
        merged = dict(payload)

    prompt = _first_int(merged, _IN_KEYS)
    completion = _first_int(merged, _OUT_KEYS)
    total = _first_int(merged, _TOTAL_KEYS) or (prompt + completion)
    cost = _first_float(merged, _COST_KEYS)
    if not any((prompt, completion, total, cost)):
        return {}
    out: dict[str, Any] = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }
    if cost:
        out["cost_usd"] = round(cost, 8)
    return out


@dataclass
class UsageEntry:
    role: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 1
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class UsageLedger:
    """Append-only token/cost ledger. Read-side aggregates on demand."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        budget_tokens: int = 0,
        policy: BudgetPolicy | None = None,
    ) -> None:
        self.path = path or self._default_path()
        self.policy = policy or BudgetPolicy(limit_tokens=budget_tokens)
        self.budget_tokens = self.policy.limit_tokens
        self._session: list[UsageEntry] = []
        self._warned = False

    @staticmethod
    def _default_path() -> Path:
        try:
            from wth_ai.workspace import wth_home

            return wth_home() / "usage" / "ledger.jsonl"
        except Exception:  # noqa: BLE001
            return Path.home() / ".wth-ai" / "usage" / "ledger.jsonl"

    # ------------------------------------------------------------------ #
    def record(
        self,
        *,
        role: str,
        provider: str,
        model: str,
        usage: dict[str, Any] | None,
    ) -> UsageEntry:
        """Record one LLM call. A missing usage block still counts as a call."""
        clean = normalize_usage(usage or {})
        entry = UsageEntry(
            role=role or "unknown",
            provider=provider or "unknown",
            model=model or "unknown",
            prompt_tokens=int(clean.get("prompt_tokens", 0)),
            completion_tokens=int(clean.get("completion_tokens", 0)),
            total_tokens=int(clean.get("total_tokens", 0)),
            cost_usd=float(clean.get("cost_usd", 0.0)),
        )
        self._session.append(entry)
        self._append(entry)
        return entry

    def _append(self, entry: UsageEntry) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry.to_dict()) + "\n")
        except OSError:
            pass  # accounting is best-effort, never fatal to the call it describes

    def load(self) -> list[UsageEntry]:
        if not self.path.exists():
            return []
        out: list[UsageEntry] = []
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                data = json.loads(line)
                out.append(UsageEntry(**{k: v for k, v in data.items() if k in UsageEntry.__annotations__}))
        except (OSError, ValueError, TypeError):
            return out
        return out

    # ------------------------------------------------------------------ #
    def session_entries(self) -> list[UsageEntry]:
        """Entries recorded by THIS process run (the budget's denominator)."""
        return list(self._session)

    def session_tokens(self) -> int:
        return sum(entry.total_tokens for entry in self._session)

    def budget_state(self) -> dict[str, Any]:
        """Limit vs used, with the RIGHT denominator for the configured scope."""
        # NB: sum the rows directly here. Calling `summary()` would re-enter
        # `budget_state()` (summary embeds it) and blow the stack -- caught by
        # `test_total_scope_does_count_lifetime`.
        used = (
            self.session_tokens()
            if self.policy.scope == "session"
            else sum(row.total_tokens for row in self.load())
        )
        limit = self.policy.limit_tokens
        over = bool(limit and used >= limit)
        return {
            "active": self.policy.active,
            "mode": self.policy.mode,
            "scope": self.policy.scope,
            "limit_tokens": limit,
            "used_tokens": used,
            "remaining_tokens": max(0, limit - used) if limit else 0,
            "over": over,
        }

    def check_budget(self) -> tuple[bool, str]:
        """(allowed, message). Pure read: decides nothing, records nothing."""
        state = self.budget_state()
        if not state["active"] or not state["over"]:
            return True, ""
        detail = (
            f"budget token habis: {state['used_tokens']}/{state['limit_tokens']} "
            f"(scope={state['scope']}, mode={state['mode']})"
        )
        if state["mode"] == "stop":
            return False, (
                f"{detail}. Panggilan LLM dihentikan supaya tidak melewati batas. "
                "Naikkan `WTH_TOKEN_BUDGET`, set `WTH_BUDGET_MODE=warn` untuk lanjut dengan "
                "peringatan, atau `WTH_BUDGET_MODE=off` untuk mematikan batasnya."
            )
        return True, detail

    def summary(self, *, entries: list[UsageEntry] | None = None) -> dict[str, Any]:
        rows = entries if entries is not None else self.load()
        total = sum(row.total_tokens for row in rows)
        cost = sum(row.cost_usd for row in rows)
        by_model: dict[str, dict[str, Any]] = {}
        by_role: dict[str, dict[str, Any]] = {}
        for row in rows:
            for bucket, key in ((by_model, row.model), (by_role, row.role)):
                slot = bucket.setdefault(key, {"calls": 0, "tokens": 0, "cost_usd": 0.0})
                slot["calls"] += 1
                slot["tokens"] += row.total_tokens
                slot["cost_usd"] = round(slot["cost_usd"] + row.cost_usd, 8)
        return {
            "calls": len(rows),
            "total_tokens": total,
            "cost_usd": round(cost, 8),
            "by_model": by_model,
            "by_role": by_role,
            "session_calls": len(self._session),
            "session_tokens": self.session_tokens(),
            "budget_tokens": self.budget_tokens,
            "over_budget": bool(self.budget_state()["over"]),
            "budget": self.budget_state(),
        }

    def budget_line(self, *, entries: list[UsageEntry] | None = None) -> str:
        """The budget/caveat lines, as ONE source of wording.

        Extracted so the rich-table path (a `caption`) and the plain path cannot
        drift into saying different things about the same numbers. `entries` is
        passed through so `format_summary(entries=...)` does not silently read a
        different set than the one it was handed.
        """
        data = self.summary(entries=entries)
        budget = data.get("budget", {})
        lines: list[str] = []
        if data.get("session_calls"):
            lines.append(f"Sesi ini: {data['session_calls']} call · {data['session_tokens']} token")
        if budget.get("active"):
            status = "HABIS/LEWAT" if budget["over"] else "di bawah"
            scope_label = "di sesi ini" if budget["scope"] == "session" else "seumur hidup"
            lines.append(
                f"Budget ({budget['mode']}, scope={budget['scope']}): "
                f"{budget['used_tokens']}/{budget['limit_tokens']} token {scope_label} "
                f"({status}), sisa {budget['remaining_tokens']}"
            )
            if budget["scope"] == "session" and data["total_tokens"] > budget["limit_tokens"]:
                lines.append(
                    f"Catatan: total seumur hidup {data['total_tokens']} token sudah di atas "
                    f"batas {budget['limit_tokens']}, tapi batas ini berlaku per sesi (proses), "
                    "bukan seumur hidup."
                )
        elif data["budget_tokens"]:
            lines.append(f"Budget: {data['budget_tokens']} token (mode=off — tidak ditegakkan)")
        return "\n".join(lines)

    def format_summary(self, *, entries: list[UsageEntry] | None = None) -> str:
        """Operator view. Says so plainly when nothing was measured."""
        data = self.summary(entries=entries)
        if not data["calls"]:
            return (
                "Belum ada pemakaian tercatat.\n"
                "Catatan: provider yang tidak mengirim blok usage tetap tercatat sebagai 1 call "
                "dengan 0 token -- itu bukan 'gratis', itu 'tidak terukur'."
            )
        lines = [
            f"Pemakaian: {data['calls']} call · {data['total_tokens']} token"
            + (f" · ${data['cost_usd']}" if data["cost_usd"] else " · biaya tidak dilaporkan provider"),
        ]
        if data["by_model"]:
            lines.append("Per model:")
            for model, slot in sorted(data["by_model"].items(), key=lambda kv: -kv[1]["tokens"]):
                lines.append(f"  {model}: {slot['calls']} call · {slot['tokens']} token")
        if data["by_role"]:
            lines.append("Per role:")
            for role, slot in sorted(data["by_role"].items(), key=lambda kv: -kv[1]["tokens"]):
                lines.append(f"  {role}: {slot['calls']} call · {slot['tokens']} token")
        # Budget/caveat lines come from budget_line() so the plain view and the
        # rich-table caption can never disagree about the same numbers.
        tail = self.budget_line(entries=entries)
        if tail:
            lines.extend(tail.splitlines())
        return "\n".join(lines)


_LEDGER: UsageLedger | None = None


def get_ledger() -> UsageLedger:
    """Process-wide ledger, so every router instance writes to one file."""
    global _LEDGER
    if _LEDGER is None:
        _LEDGER = UsageLedger(policy=load_budget_policy())
    return _LEDGER


def reset_ledger() -> None:
    """Drop the cached ledger (tests, and after changing WTH_HOME)."""
    global _LEDGER
    _LEDGER = None

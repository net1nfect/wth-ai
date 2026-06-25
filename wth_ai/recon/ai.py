"""LLM "smart" recon layer (Fase 6 P4).

Augments the deterministic recon engine with an LLM brain that re-ranks
priority targets and proposes *grounded* hypotheses. Strict rules:

- Evidence-driven (TTST): the model may only reference assets we actually
  observed; hypotheses are explicitly tagged ``HIPOTESIS`` (unproven).
- Non-authoritative: the LLM augments, never overrides, deterministic signals.
  It can only *raise* attention, add reasons, and suggest hypotheses.
- Fully optional & safe: disabled under test mode or when no provider key is
  present; every call is wrapped so a failure degrades to deterministic output.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from wth_ai.recon.model import PriorityTarget

_SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

_SYSTEM = (
    "You are WTH-AI's recon analyst. You receive normalized, AUTHORIZED recon "
    "data and must prioritize attack surface for a human pentester. "
    "Rules: (1) Only reference assets present in the input. (2) Never invent "
    "URLs, params, or secrets. (3) Findings are HYPOTHESES, not confirmations. "
    "(4) Recon-only mindset: suggest where to look, never how to exploit "
    "destructively. Reply with STRICT JSON only."
)


class ReconAI:
    """Optional LLM augmentation around the deterministic scoring engine."""

    def __init__(self, config: Any = None, router: Any = None) -> None:
        self._config = config
        self._router = router
        self._model: str | None = None

    # ----------------------------------------------------------------- #
    @property
    def config(self) -> Any:
        if self._config is None:
            from wth_ai.config import WTHConfig
            self._config = WTHConfig.load()
        return self._config

    @property
    def router(self) -> Any:
        if self._router is None:
            from wth_ai.llm.router import LLMProviderRouter
            self._router = LLMProviderRouter(self.config)
        return self._router

    def model(self) -> str:
        if self._model:
            return self._model
        m = self.config.models
        self._model = getattr(m, "recon", "") or m.planner or "openrouter/auto"
        return self._model

    def enabled(self) -> bool:
        """Smart layer is on only outside test-mode and when a key is present.

        Bridges a vault-stored ``openrouter_api_key`` into the process env so the
        provider (which reads env/config) works without re-running the wizard.
        """
        if os.environ.get("WTH_TEST_MODE") or os.environ.get("WTH_RECON_AI") == "0":
            return False
        try:
            key = os.environ.get("OPENROUTER_API_KEY") or self.router.provider_key("openrouter")
            if key and not os.environ.get("OPENROUTER_API_KEY"):
                os.environ["OPENROUTER_API_KEY"] = key
            return bool(key)
        except Exception:  # noqa: BLE001
            return False

    # ----------------------------------------------------------------- #
    def _ask(self, payload: dict[str, Any], timeout: int = 60) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)[:12000]},
        ]
        data, _model = self.router.chat_json(self.model(), messages, timeout=timeout)
        return data if isinstance(data, dict) else {}

    def augment(
        self,
        targets: "list[PriorityTarget]",
        summary: dict[str, Any],
        *,
        log: Callable[[str], None] | None = None,
    ) -> tuple["list[PriorityTarget]", list[dict[str, Any]]]:
        """Return (possibly re-ranked targets, grounded hypotheses).

        Never raises: any failure returns the inputs unchanged + no hypotheses.
        """
        log = log or (lambda *_: None)
        if not self.enabled() or not targets:
            return targets, []
        assets = [t.asset for t in targets[:30]]
        payload = {
            "instruction": (
                "Re-rank by real attacker interest and propose up to 6 grounded "
                "hypotheses. Only use assets from 'targets'/'assets'. Output JSON: "
                '{"rankings":[{"asset":"","severity":"info|low|medium|high|critical",'
                '"reason":""}],"hypotheses":[{"title":"","asset":"","rationale":"",'
                '"confidence":0.0}]}'
            ),
            "summary": summary,
            "assets": assets,
            "targets": [
                {"asset": t.asset, "score": t.score, "severity": t.severity,
                 "signals": (t.signals or t.reasons)[:6]}
                for t in targets[:30]
            ],
        }
        try:
            data = self._ask(payload)
        except Exception as exc:  # noqa: BLE001 — degrade to deterministic
            log(f"recon-ai: LLM augmentation unavailable ({exc}); using deterministic ranking")
            return targets, []

        by_asset = {t.asset: t for t in targets}
        for rk in data.get("rankings", []) or []:
            asset = str(rk.get("asset", ""))
            t = by_asset.get(asset)
            if not t:
                continue
            reason = str(rk.get("reason", "")).strip()
            if reason:
                tag = f"ai:{reason}"[:160]
                if tag not in t.reasons:
                    t.reasons.append(tag)
            sev = str(rk.get("severity", "")).lower()
            # AI may only *raise* severity, never lower it.
            if sev in _SEVERITY_RANK and _SEVERITY_RANK[sev] > _SEVERITY_RANK.get(t.severity, 0):
                t.severity = sev
                t.confidence = min(1.0, t.confidence + 0.1)

        hyps = self._ground_hypotheses(data.get("hypotheses", []) or [], set(by_asset))
        targets.sort(key=lambda t: (_SEVERITY_RANK.get(t.severity, 0), t.score), reverse=True)
        log(f"recon-ai: augmented {len(targets)} targets, {len(hyps)} grounded hypotheses")
        return targets, hyps

    @staticmethod
    def _ground_hypotheses(raw: list[dict], known_assets: set[str]) -> list[dict[str, Any]]:
        """Keep only hypotheses that reference an observed asset; tag as HIPOTESIS."""
        out: list[dict[str, Any]] = []
        for h in raw[:8]:
            asset = str(h.get("asset", "")).strip()
            grounded = asset in known_assets or any(asset and asset in a for a in known_assets)
            if not asset or not grounded:
                continue
            title = str(h.get("title", "")).strip()[:200]
            if not title:
                continue
            out.append({
                "title": f"HIPOTESIS: {title}",
                "asset": asset,
                "rationale": str(h.get("rationale", "")).strip()[:600],
                "confidence": max(0.0, min(1.0, float(h.get("confidence", 0.3) or 0.3))),
                "grounded": True,
                "proven": False,
            })
        return out

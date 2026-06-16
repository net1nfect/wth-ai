"""Parameter ranking for vuln hunting priority."""

from __future__ import annotations

from dataclasses import dataclass

from wth_ai.models import ParamCandidate

HIGH = frozenset({
    "id", "user_id", "uid", "uuid", "account_id", "order_id", "file", "path",
    "url", "redirect", "next", "return", "callback", "dest", "target",
    "token", "key", "api_key", "email", "role", "admin", "debug",
})

MEDIUM = frozenset({"q", "search", "query", "page", "sort", "filter", "name", "type"})


@dataclass
class RankedParam:
    candidate: ParamCandidate
    score: int
    reasons: list[str]

    def to_dict(self) -> dict:
        return {
            **self.candidate.to_dict(),
            "score": self.score,
            "reasons": self.reasons,
        }


class ParamRanker:
    def score(self, param: str, url: str = "") -> tuple[int, list[str]]:
        p = param.lower()
        reasons: list[str] = []
        score = 3
        if p in HIGH:
            score = 9
            reasons.append("high_value_name")
        elif any(k in p for k in ("id", "user", "token", "redirect", "url", "file")):
            score = 7
            reasons.append("partial_match")
        elif p in MEDIUM:
            score = 5
            reasons.append("medium_value")
        if "/api/" in url:
            score = min(10, score + 2)
            reasons.append("api_endpoint")
        if "admin" in url.lower():
            score = min(10, score + 1)
            reasons.append("admin_path")
        return score, reasons

    def rank(self, params: list[ParamCandidate], limit: int = 50) -> list[RankedParam]:
        ranked = [
            RankedParam(c, *self.score(c.param, c.url))
            for c in params
        ]
        ranked.sort(key=lambda r: (-r.score, r.candidate.url))
        return ranked[:limit]


def rank_params(params: list[ParamCandidate], limit: int = 50) -> list[RankedParam]:
    return ParamRanker().rank(params, limit)

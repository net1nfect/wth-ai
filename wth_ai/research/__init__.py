"""Autonomous research — search, fetch and remember public information."""

from __future__ import annotations

from typing import Any

from wth_ai.research.engine import (
    DEFAULT_DELAY_SECONDS,
    ResearchNote,
    Researcher,
    SearchResult,
)

__all__ = [
    "Researcher",
    "ResearchNote",
    "SearchResult",
    "get_researcher",
    "reset_researchers",
    "DEFAULT_DELAY_SECONDS",
]

# One Researcher per session, so the search cache and the note list survive
# across agent turns. Rebuilding it per call would defeat rule 3 of the module
# docstring: the same query would be re-fetched every turn and the rate limiting
# would be meaningless.
_RESEARCHERS: dict[str, Researcher] = {}
_MAX_TRACKED = 16


def _search_credentials() -> dict[str, str]:
    """Search API provider + key from config/vault.

    Read lazily and defensively: a broken config must degrade to the HTML
    fallback, never to a crashed research call.
    """
    try:
        from wth_ai.config import WTHConfig

        secrets = WTHConfig.load().secrets
        provider = getattr(secrets, "search_provider", "") or ""
        key = getattr(secrets, "search_api_key", None) or ""
        if not key:
            try:
                from wth_ai.vault import SecretVault

                key = SecretVault().get("search_api_key") or ""
            except Exception:  # noqa: BLE001
                key = ""
        return {"search_provider": provider, "search_api_key": key}
    except Exception:  # noqa: BLE001
        return {"search_provider": "", "search_api_key": ""}


def get_researcher(session_id: str = "", **kwargs: Any) -> Researcher:
    key = session_id or "default"
    researcher = _RESEARCHERS.get(key)
    if researcher is None:
        if len(_RESEARCHERS) >= _MAX_TRACKED:
            _RESEARCHERS.pop(next(iter(_RESEARCHERS)))
        merged = {**_search_credentials(), **kwargs}
        researcher = Researcher(session_id=session_id, **merged)
        _RESEARCHERS[key] = researcher
    return researcher


def reset_researchers() -> None:
    """Drop the cache (tests, and `/new` starting a fresh session)."""
    _RESEARCHERS.clear()

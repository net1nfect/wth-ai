"""Resolve numbered or typed model choices from provider listings."""

from __future__ import annotations

import re


def resolve_model_choice(choice: str, models: list[str]) -> str:
    """Map setup wizard input to a real model id.

    Users often type ``2`` meaning item #2 in the printed list, not the literal
    model name ``2``. Accept ``N`` (1-based index) when it maps to a listed model.
    """
    raw = (choice or "").strip()
    if not raw:
        return raw
    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(models):
            return models[idx - 1]
    # Partial prefix match (e.g. user typed first segment of id)
    low = raw.lower()
    for m in models:
        if m.lower() == low:
            return m
    return raw


def looks_like_list_index(model: str, models: list[str]) -> bool:
    """True when model is a bare number that maps to a list position."""
    raw = (model or "").strip()
    if not raw.isdigit():
        return False
    idx = int(raw)
    return 1 <= idx <= len(models) and models[idx - 1] != raw


def is_likely_invalid_model(model: str, models: list[str]) -> bool:
    """Heuristic: numeric-only id not in catalog."""
    raw = (model or "").strip()
    if not raw:
        return True
    if raw in models:
        return False
    if looks_like_list_index(raw, models):
        return True
    if re.fullmatch(r"\d+", raw) and raw not in models:
        return True
    return False

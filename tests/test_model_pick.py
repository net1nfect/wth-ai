"""Tests for model pick resolution."""

from __future__ import annotations

from wth_ai.llm.model_pick import is_likely_invalid_model, resolve_model_choice


def test_resolve_numeric_index():
    models = ["alpha/model-a", "beta/model-b", "gamma/model-c"]
    assert resolve_model_choice("2", models) == "beta/model-b"
    assert resolve_model_choice("beta/model-b", models) == "beta/model-b"


def test_invalid_numeric_not_in_catalog():
    models = ["alpha/model-a", "beta/model-b"]
    assert is_likely_invalid_model("2", models) is True
    assert is_likely_invalid_model("beta/model-b", models) is False

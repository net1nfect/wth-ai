"""Defensive control test descriptors."""

from typing import Any


def defensive_validation_plan(control: str) -> dict[str, Any]:
    """Describe a transparent defensive-control validation."""
    return {
        "control": control,
        "marker": "H1T4M_SAFE_VALIDATION_CANARY",
        "expected_effect": "none",
        "telemetry_required": True,
    }

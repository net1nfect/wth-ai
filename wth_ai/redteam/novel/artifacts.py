"""Cek kelengkapan bukti. Tanpa tujuh artefak, maksimal CANDIDATE."""

from __future__ import annotations

from pathlib import Path

REQUIRED = (
    "repro.sh",
    "request_response",
    "control",
    "differential.json",
    "replay.json",
    "impact",
    "minimal_poc.md",
)


def missing_artifacts(bundle: Path) -> list[str]:
    root = Path(bundle)
    return [name for name in REQUIRED if not (root / name).exists()]


def may_confirm(bundle: Path) -> bool:
    return not missing_artifacts(bundle)
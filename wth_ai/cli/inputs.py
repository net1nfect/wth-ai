"""Membaca input operator dari file, tanpa menebak."""

from __future__ import annotations

from pathlib import Path


def read_targets(path: Path) -> list[str]:
    """Satu target per baris; `#` komentar; baris kosong diabaikan."""
    targets: list[str] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        text = line.split("#", 1)[0].strip()
        if text:
            targets.append(text)
    return targets


def read_instruction(path: Path | None, inline: str | None) -> str:
    if inline:
        return inline.strip()
    if path:
        return Path(path).read_text(encoding="utf-8").strip()
    return ""
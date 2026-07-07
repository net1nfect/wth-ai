"""CTF wordlist/payload presets mapped to challenge types."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def ctf_wordlists(challenge_type: str = "web") -> list[Path]:
    """Return wordlist paths for a CTF challenge type."""
    base = _ROOT / "data" / "wordlists"
    mapping: dict[str, list[str]] = {
        "web": ["common.txt", "directories.txt", "api-endpoints.txt"],
        "pwn": ["common.txt"],
        "crypto": ["common.txt"],
        "misc": ["common.txt"],
    }
    names = mapping.get(challenge_type, mapping["web"])
    out: list[Path] = []
    for name in names:
        p = base / name
        if p.exists():
            out.append(p)
    if not out and base.exists():
        out = [p for p in base.glob("*.txt")][:5]
    return out


def suggest_challenge_type(fingerprint: dict) -> str:
    """Heuristic challenge type from fingerprint dict."""
    tech = str(fingerprint.get("technologies", fingerprint.get("tech", ""))).lower()
    if any(x in tech for x in ("ssh", "ftp", "telnet")):
        return "pwn"
    if any(x in tech for x in ("flask", "django", "php", "nginx", "apache")):
        return "web"
    return "web"

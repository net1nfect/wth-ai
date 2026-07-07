"""Parse natural-language scope / target commands."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse


_SCOPE_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"^(?:please\s+)?(?:set\s+)?target\s+(.+)$", re.I), 1),
    (re.compile(r"^scope\s+(\S+)\s+authorized\b", re.I), 1),
    (re.compile(r"^(\S+)\s+authorized\b", re.I), 1),
)


def parse_scope_intent(text: str) -> dict[str, Any] | None:
    """Return scope_set args when text is a direct scope/target command."""
    raw = text.strip()
    if not raw:
        return None

    lower = raw.lower()
    if "scope" in lower or "target" in lower or lower.startswith("http"):
        for pattern, group in _SCOPE_PATTERNS:
            m = pattern.match(raw)
            if m:
                return _args_from_target(m.group(group).strip())

        if lower.startswith("scope "):
            rest = raw[6:].strip()
            authorized = "authorized" in lower or "izin" in lower
            token = rest.split()[0] if rest.split() else rest
            if token:
                args = _args_from_target(token)
                if authorized:
                    args["authorized"] = True
                return args

    if re.match(r"^https?://", raw, re.I):
        return _args_from_target(raw)

    return None


def _args_from_target(raw: str) -> dict[str, Any]:
    raw = raw.strip().strip('"').strip("'").rstrip(".,;")
    if "://" in raw:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower().rstrip(".")
        if not host:
            return {"target": raw, "authorized": True, "in_scope": [raw]}
        in_scope = [host, f"*.{host}"]
        if parsed.path and parsed.path not in {"/", ""}:
            path_scope = f"{host}{parsed.path.rstrip('/')}"
            in_scope.append(path_scope)
        return {
            "target": host,
            "authorized": True,
            "in_scope": in_scope,
            "notes": f"entry_url={raw}",
        }

    host = raw.split("/")[0].split(":")[0].lower().rstrip(".")
    if re.match(r"^[\w.-]+\.[a-z]{2,}$", host, re.I):
        return {"target": host, "authorized": True, "in_scope": [host, f"*.{host}"]}
    return {"target": raw, "authorized": True, "in_scope": [raw]}

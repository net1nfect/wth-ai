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

# P0 exit-review finding (HIGH): naive `"authorized" in lower` substring
# check TERBALIK oleh negasi -- "set target evil.com not authorized" ->
# authorized=True (SALAH, "authorized" muncul sebagai substring), begitu
# juga "unauthorized" (mengandung "authorized" literal). \b...\b menutup
# celah "unauthorized" (tidak ada word-boundary antara "un" dan "authorized").
# Negasi eksplisit ("not"/"tidak"/"belum"/"tanpa"/dst tepat sebelum kata)
# dicek terpisah -- fail-closed: ambigu/negasi -> False, TIDAK PERNAH True.
_AUTH_WORD_RE = re.compile(r"\b(authorized|izin)\b")
_NEGATION_WORDS = frozenset({"not", "tidak", "belum", "tanpa", "no", "never", "jangan"})


def _text_expresses_authorization(lower: str) -> bool:
    for m in _AUTH_WORD_RE.finditer(lower):
        preceding = lower[: m.start()].strip().split()[-2:]
        if any(neg in preceding for neg in _NEGATION_WORDS):
            continue
        return True
    return False


def parse_scope_intent(text: str) -> dict[str, Any] | None:
    """Return scope_set args when text is a direct scope/target command."""
    raw = text.strip()
    if not raw:
        return None

    lower = raw.lower()
    # WU-011 (AGENT-01): authorization TIDAK LAGI default True. Sebelumnya
    # _args_from_target() hardcode authorized=True di SEMUA jalur, termasuk
    # "set target x.com" yang sama sekali tidak menyebut otorisasi -- fail-
    # closed law (MISSING -> DENY): hanya True kalau teks eksplisit menyebut
    # "authorized"/"izin", dihitung SEKALI di sini dan dioper ke semua cabang
    # (sebelumnya deteksi ini cuma dipakai di cabang "scope " prefix, dan
    # bahkan di situ pun cuma bisa MENAMBAH True, tidak pernah mengoreksi ke
    # False -- karena _args_from_target() sudah kadung set True duluan).
    authorized = _text_expresses_authorization(lower)

    if "scope" in lower or "target" in lower or lower.startswith("http"):
        for pattern, group in _SCOPE_PATTERNS:
            m = pattern.match(raw)
            if m:
                return _args_from_target(m.group(group).strip(), authorized=authorized)

        if lower.startswith("scope "):
            rest = raw[6:].strip()
            token = rest.split()[0] if rest.split() else rest
            if token:
                return _args_from_target(token, authorized=authorized)

    if re.match(r"^https?://", raw, re.I):
        return _args_from_target(raw, authorized=authorized)

    return None


def _args_from_target(raw: str, *, authorized: bool = False) -> dict[str, Any]:
    raw = raw.strip().strip('"').strip("'").rstrip(".,;")
    if "://" in raw:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower().rstrip(".")
        if not host:
            return {"target": raw, "authorized": authorized, "in_scope": [raw]}
        in_scope = [host, f"*.{host}"]
        if parsed.path and parsed.path not in {"/", ""}:
            path_scope = f"{host}{parsed.path.rstrip('/')}"
            in_scope.append(path_scope)
        return {
            "target": host,
            "authorized": authorized,
            "in_scope": in_scope,
            "notes": f"entry_url={raw}",
        }

    host = raw.split("/")[0].split(":")[0].lower().rstrip(".")
    if re.match(r"^[\w.-]+\.[a-z]{2,}$", host, re.I):
        return {"target": host, "authorized": authorized, "in_scope": [host, f"*.{host}"]}
    return {"target": raw, "authorized": authorized, "in_scope": [raw]}

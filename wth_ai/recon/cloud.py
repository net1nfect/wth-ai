"""Builtin cloud/storage/CDN candidate detection (metadata only).

Detects references to cloud assets in hosts/URLs/HTML. Strictly head/metadata
recon: it never lists or downloads bucket contents.
"""

from __future__ import annotations

import re
from typing import Any

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_s3", re.compile(r"[a-z0-9.\-]+\.s3[.\-][a-z0-9.\-]*amazonaws\.com", re.I)),
    ("aws_s3", re.compile(r"s3\.amazonaws\.com/[a-z0-9.\-_]+", re.I)),
    ("gcs", re.compile(r"storage\.googleapis\.com/[a-z0-9.\-_]+", re.I)),
    ("gcs", re.compile(r"[a-z0-9.\-_]+\.storage\.googleapis\.com", re.I)),
    ("azure_blob", re.compile(r"[a-z0-9]+\.blob\.core\.windows\.net", re.I)),
    ("firebase", re.compile(r"[a-z0-9.\-]+\.firebaseio\.com", re.I)),
    ("firebase_hosting", re.compile(r"[a-z0-9.\-]+\.web\.app", re.I)),
    ("supabase", re.compile(r"[a-z0-9.\-]+\.supabase\.co", re.I)),
    ("vercel", re.compile(r"[a-z0-9.\-]+\.vercel\.app", re.I)),
    ("netlify", re.compile(r"[a-z0-9.\-]+\.netlify\.app", re.I)),
    ("cloudfront", re.compile(r"[a-z0-9]+\.cloudfront\.net", re.I)),
    ("digitalocean_spaces", re.compile(r"[a-z0-9.\-]+\.digitaloceanspaces\.com", re.I)),
    ("heroku", re.compile(r"[a-z0-9.\-]+\.herokuapp\.com", re.I)),
    ("fastly", re.compile(r"[a-z0-9.\-]+\.fastly\.net", re.I)),
)


def detect(text: str) -> list[dict[str, Any]]:
    """Detect cloud candidates in arbitrary text (host/url/html)."""
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for provider, pat in _PATTERNS:
        for m in pat.finditer(text or ""):
            asset = m.group(0).lower()
            dedup = f"{provider}|{asset}"
            if dedup in seen:
                continue
            seen.add(dedup)
            items.append({"kind": "cloud_candidate", "provider": provider, "asset": asset})
    return items


def run(host: str, *, extra_text: str = "") -> list[dict[str, Any]]:
    blob = f"{host}\n{extra_text}"
    return detect(blob)

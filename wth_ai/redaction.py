"""Canonical secret redaction — single source of truth (WU-203, DATA-03).

Before this module there were FOUR divergent `redact*()` implementations
(`scope_manager.redact_secrets`, `evidence/vault.redact`,
`rpc/execute_server.redact_secrets`, `recon/runner.redact`) with different
pattern sets -- so which secret shapes got masked depended on WHICH surface
happened to touch the text. The most-complete set (`recon/runner`, which
also caught AWS keys + JWTs) is promoted here as the ONE canonical function;
every other site now delegates to it, so a pattern added here hardens EVERY
surface at once. This also closes the WU-203 critique gap: `EvidencePipeline`
built `Evidence.content` straight from raw tool output with NO redaction at
all, then fed it into findings persisted to disk.

Patterns are deliberately conservative (unambiguous secret shapes only) so
they don't shred legitimate evidence with false positives -- broadening them
(GCP keys, generic high-entropy blobs) is a documented follow-up, not
smuggled in here.
"""

from __future__ import annotations

import re

# ORDER MATTERS: the specific credential shapes (Bearer/AWS/JWT) run BEFORE the
# generic key=value pattern. The generic pattern's value is `\S+` (one
# whitespace-delimited token), so on "Authorization: Bearer <token>" it would
# otherwise consume only "Bearer" and LEAVE <token> exposed (a real leak the
# original recon/runner ordering had). Redacting "Bearer <token>" first closes
# that; the generic pattern then mops up any plain `key=singletoken` cases.
_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # HTTP Bearer credential (value may follow a space, so run first)
    (re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*"), "Bearer ***REDACTED***"),
    # AWS access key id
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AKIA***REDACTED***"),
    # JWT (header.payload.signature)
    (re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}"), "***JWT-REDACTED***"),
    # key=value / token: ... / password = ... / authorization: ... (generic, last)
    (re.compile(r"(?i)(api[_-]?key|token|password|secret|authorization)\s*[:=]\s*\S+"), r"\1=***REDACTED***"),
)


def redact_secrets(text: str) -> str:
    """Mask known secret shapes in `text`. Idempotent (re-redacting
    already-redacted text is a no-op) and never raises on non-str-ish input
    (coerces None/empty to '')."""
    out = text or ""
    if not isinstance(out, str):
        out = str(out)
    for pat, repl in _REDACT_PATTERNS:
        out = pat.sub(repl, out)
    return out


MASKED = "***REDACTED***"

# Query-parameter NAMES whose value is a credential. Matching on the name (not
# the value) means it does not depend on the secret's shape -- an opaque
# session id or `code=` is masked just like an `AKIA...` key.
_SENSITIVE_PARAM_RE = re.compile(
    r"(?i)^(?:.*[_-])?(?:api[_-]?key|apikey|key|token|access[_-]?token|id[_-]?token|"
    r"refresh[_-]?token|auth|authorization|secret|client[_-]?secret|password|passwd|"
    r"pwd|session|session[_-]?id|sid|sig|signature|code|credential)s?$"
)


def mask_url_secrets(url: str) -> str:
    """Mask credential-looking query VALUES in `url`, keeping its structure.

    Recon artifacts are written straight from DISCOVERED URLs (JS extraction,
    crawler links, archived-URL tools), and a discovered URL can carry a live
    credential -- ``https://api.example.com/v1/x?api_key=AKIA...``.
    ``redact_secrets()`` never touched those: its patterns match ``key=value``
    with no surrounding URL syntax, and it is not applied to normalized assets
    at all. The result was a full credential persisted verbatim in
    ``normalized/*.json``, ``normalized_result.json``, ``asset_graph.json``,
    ``recon_summary.json``, ``recon_report.md`` and the job result -- and
    forwarded to Telegram/Discord with the change events.

    Only the VALUE is replaced; the parameter name and path survive, so the
    endpoint stays identifiable and deduplicable.
    """
    if not url or "?" not in url:
        return redact_secrets(url)
    base, _, rest = url.partition("?")
    query, sep, fragment = rest.partition("#")
    parts: list[str] = []
    for pair in query.split("&"):
        if not pair:
            continue
        name, eq, value = pair.partition("=")
        if eq and value and _SENSITIVE_PARAM_RE.match(name):
            parts.append(f"{name}={MASKED}")
        else:
            parts.append(redact_secrets(pair))
    out = f"{base}?{'&'.join(parts)}" if parts else base
    return f"{out}{sep}{fragment}" if sep else out

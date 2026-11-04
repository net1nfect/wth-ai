"""Canonical secret redaction for everything WTH-AI writes down.

One implementation, imported by every surface that can leak a credential:
provider error messages, terminal transcripts, recon raw/ output, research
notes, skill autowrite. Historically each surface carried its own regex (or
none at all), so a fix in one place left the others exposed -- and
`recon/runner.py` and `scope_manager.py` both document themselves as
"delegating to the canonical `wth_ai.redaction.redact_secrets`". This module
is that canonical implementation.

Two classes of pattern are covered:

1. **Assignments** -- ``token=...``, ``"api_key": "..."``, ``Password: ...``,
   ``Authorization: Bearer ...``. The key name carries the intent, so the
   value is masked regardless of its shape.
2. **Well-known credential shapes** -- provider prefixes (``sk-``, ``ghp_``,
   ``xoxb-``, ``AKIA...``, ``AIza...``), JWTs, and PEM private-key blocks.
   These are masked even when they appear bare in output.

Masking is deliberately lossy: the marker replaces the value, and the key
name is preserved so an operator still reads *which* credential leaked.
"""
from __future__ import annotations

import re

#: The single masking marker used across transcripts, logs, raw tool output
#: and reports. Tests and operators match on this exact string.
MASKED = "***REDACTED***"

# Key names that imply the value beside them is a credential. Word-boundary
# anchored and matched case-insensitively; the alternation is ordered longest
# first so ``private_key`` is not consumed as ``key``.
# NOTE: no ``[\w.-]*`` prefix/suffix around this group -- an unbounded wildcard
# on BOTH sides of an alternation backtracks catastrophically on long
# non-matching text (measured: a 600 KB raw tool output hung for minutes).
# Word boundaries match the same keys without the cost.
_SECRET_KEY = (
    r"(?:secret[_-]?access[_-]?key|access[_-]?key[_-]?id|private[_-]?key"
    r"|client[_-]?secret|refresh[_-]?token|auth[_-]?token|x-auth-token"
    r"|secret[_-]?key|access[_-]?token|api[_-]?key|apikey|x-api-key"
    r"|session[_-]?id|id[_-]?token|bearer|authorization"
    r"|password|passwd|passphrase|token|secret|session|cookie|csrf)"
)

_ASSIGNMENT_RE = re.compile(
    r"(?i)(?<![A-Za-z])(?P<key>" + _SECRET_KEY + r")(?![A-Za-z])"
    # A JSON/YAML key is often quoted: `"password": "..."`. Consume the closing
    # quote between the key and the separator, otherwise every JSON payload
    # slipped through unredacted.
    r"(?P<q>[\"']?)"
    r"(?P<sep>\s*[:=]\s*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s\"',;&]{4,})"
)

# Ordered: most specific shapes first so a prefixed key is masked whole.
_SHAPE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # PEM private key blocks (multi-line).
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        MASKED,
    ),
    # JSON Web Tokens: three base64url segments, header always starts with eyJ.
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), MASKED),
    # Provider-prefixed keys.
    (
        re.compile(
            r"\b(?:sk|rk)[-_](?:live|test|prod)?[-_]?[A-Za-z0-9_-]{12,}"
        ),
        MASKED,
    ),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), MASKED),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), MASKED),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), MASKED),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"), MASKED),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"), MASKED),
    (re.compile(r"\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}"), MASKED),
    # AWS access-key ids. The elided/documented form (``AKIAIO...MPLE``) is
    # matched too, because a truncated key in a fixture is still a leak when
    # the fixture is the thing being written to disk.
    (re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z._-]{6,}"), MASKED),
    # Slack/Discord webhooks.
    (re.compile(r"https://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/\S+"), MASKED),
    (re.compile(r"https://hooks\.slack\.com/services/\S+"), MASKED),
    # `Authorization: Bearer <token>` -- the generic assignment rule below only
    # reaches the scheme word ("Bearer"), leaving the token itself in place.
    (
        re.compile(
            r"(?i)\b((?:authorization|bearer)\s*[:=]?\s*)"
            r"(?:(?:bearer|basic|digest|token)\s+)?"
            r"([A-Za-z0-9\-._~+/=]{8,})"
        ),
        r"\1" + MASKED,
    ),
)


def redact_secrets(text: str) -> str:
    """Return ``text`` with credentials replaced by :data:`MASKED`.

    Never raises: redaction runs on error paths and on output that is about to
    be persisted, and a malformed payload must not take the caller down.
    """
    if not text:
        return "" if text is None else str(text)
    if not isinstance(text, str):
        try:
            text = str(text)
        except Exception:  # noqa: BLE001 - a value we cannot stringify
            return ""
    redacted = text
    for pattern, repl in _SHAPE_PATTERNS:
        redacted = pattern.sub(repl, redacted)
    redacted = _ASSIGNMENT_RE.sub(
        lambda m: f"{m.group('key')}{m.group('q')}{m.group('sep')}{MASKED}", redacted
    )
    return redacted


def contains_secret(text: str) -> bool:
    """Whether redaction would change ``text`` (i.e. a credential shows up)."""
    return redact_secrets(text) != text

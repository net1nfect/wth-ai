"""Scope validation and management."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from wth_ai.models import Scope


class ScopeManager:
    """Manage and validate assessment scope."""

    def __init__(self) -> None:
        self.scope = Scope()

    def set_scope(self, **kwargs: Any) -> Scope:
        for key, value in kwargs.items():
            if hasattr(self.scope, key) and value is not None:
                setattr(self.scope, key, value)
        return self.scope

    def update_from_dict(self, data: dict[str, Any]) -> Scope:
        return self.set_scope(**{k: v for k, v in data.items() if k in Scope.__dataclass_fields__})

    def display(self) -> str:
        s = self.scope
        lines = [
            "SCOPE:",
            f"- Project: {s.project or 'unset'}",
            f"- Target: {s.target or 'unset'}",
            f"- Environment: {s.environment}",
            f"- Owned/Authorized: {'yes' if s.authorized else 'no/unclear'}",
            f"- In scope: {', '.join(s.in_scope) or 'unset'}",
            f"- Out of scope: {', '.join(s.out_of_scope) or 'none'}",
            f"- Test accounts: {', '.join(s.test_accounts) or 'none'}",
            f"- Rate limit: {s.rate_limit_rps} req/s",
            f"- Forbidden: {', '.join(s.forbidden) or 'none'}",
            f"- Destructive: {'DIIZINKAN (opt-in)' if getattr(s, 'destructive_allowed', False) else 'tidak'}",
            f"- Notes: {s.notes or 'none'}",
        ]
        return "\n".join(lines)

    def _normalize_host(self, target: str) -> str:
        target = target.strip()
        if "://" in target:
            parsed = urlparse(target)
            return (parsed.hostname or target).lower().rstrip(".")
        return target.split("/")[0].split(":")[0].lower().rstrip(".")

    def _normalize_pattern(self, pattern: str) -> str:
        """Buang skema/port/path dari POLA in-scope, wildcard dipertahankan.

        Terukur dari UAT: `/target 127.0.0.1:34621` menyimpan pola mentah bertanda port,
        sementara host yang diperiksa sudah dinormalisasi (port dibuang) -- sehingga host
        itu TIDAK PERNAH cocok dengan scope-nya sendiri dan setiap aksi diblokir
        ("Blocked: Host 127.0.0.1 tidak cocok dengan in-scope list"). Target lokal dan
        staging dengan port adalah kasus normal, jadi ini bug, bukan salah operator.
        """
        text = (pattern or "").strip()
        wildcard = text.startswith("*.")
        if wildcard:
            text = text[2:]
        host = self._normalize_host(text)
        if not host:
            return (pattern or "").strip().lower()
        return f"*.{host}" if wildcard else host

    def _host_matches_pattern(self, host: str, pattern: str) -> bool:
        host = host.lower()
        pattern = pattern.lower().strip()
        if pattern.startswith("*."):
            suffix = pattern[2:]
            return host == suffix or host.endswith("." + suffix)
        return host == pattern

    def is_in_scope(self, target: str) -> tuple[bool, str]:
        """Check if target host/path is in scope."""
        if not self.scope.in_scope and not self.scope.target:
            return False, "Scope belum diset. Gunakan `/target domain.com` dulu."

        host = self._normalize_host(target)

        for out in self.scope.out_of_scope:
            if self._path_matches(target, out) or self._host_matches_pattern(host, self._normalize_pattern(out)):
                return False, f"Target explicitly out of scope: {out}"

        if self.scope.in_scope:
            for pattern in self.scope.in_scope:
                normalized = self._normalize_pattern(pattern)
                if self._host_matches_pattern(host, normalized) or self._path_matches(target, pattern):
                    return True, f"Covered by in-scope pattern: {pattern}"
            return False, f"Host {host} tidak cocok dengan in-scope list"

        if self.scope.target:
            base = self._normalize_host(self.scope.target)
            if host == base or host.endswith("." + base):
                return True, f"Covered by primary target: {base}"

        return False, "Target tidak jelas in-scope"

    def _path_matches(self, target: str, pattern: str) -> bool:
        if "/" not in pattern:
            return False
        if pattern.endswith("/*"):
            prefix = pattern[:-1]
            return target.startswith(prefix) or target.startswith("http://" + prefix) or target.startswith("https://" + prefix)
        return pattern in target

    def validate_authorized(self) -> tuple[bool, str]:
        if not self.scope.authorized:
            return False, "Authorization belum dikonfirmasi. Konfirmasi izin testing sebelum active scan."
        if not self.scope.target and not self.scope.in_scope:
            return False, "Target/scope belum diset."
        return True, "Scope OK"

    def redact_secrets(self, text: str) -> str:
        """WU-203: delegate to canonical `wth_ai.redaction.redact_secrets`
        (single source of truth). Previously a local 2-pattern copy; now also
        covers AWS keys + JWTs on every surface that redacts output through
        this method (run_shell/http_probe stdout, tool_runner output)."""
        from wth_ai.redaction import redact_secrets

        return redact_secrets(text)

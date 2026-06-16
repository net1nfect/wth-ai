"""Input and scope validation."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

from core.exceptions import AuthorizationError, TargetError


HOST_PATTERN = re.compile(r"^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)*[a-zA-Z0-9][a-zA-Z0-9-]{0,62}$")


def normalize_target(target: str) -> str:
    """Validate and normalize a domain, IP address, or HTTP(S) URL."""
    value = target.strip()
    if not value:
        raise TargetError("Target cannot be empty")
    parsed = urlparse(value if "://" in value else f"https://{value}")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise TargetError("Target must be a domain, IP address, or HTTP(S) URL")
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        if not HOST_PATTERN.fullmatch(parsed.hostname):
            raise TargetError("Invalid target host")
    return parsed.hostname.lower()


def enforce_scope(target: str, scope: list[str], authorization_proof: str) -> str:
    """Require authorization proof and confirm target is in scope."""
    host = normalize_target(target)
    if not authorization_proof.strip():
        raise AuthorizationError("Authorization proof is required")
    normalized_scope = [normalize_target(item) for item in scope]
    if not any(host == item or host.endswith(f".{item}") for item in normalized_scope):
        raise AuthorizationError(f"Target is outside authorized scope: {host}")
    return host

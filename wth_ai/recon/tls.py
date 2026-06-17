"""Builtin TLS/certificate recon (passive, metadata only)."""

from __future__ import annotations

import socket
import ssl
from typing import Any


def cert_info(host: str, port: int = 443, timeout: int = 8) -> dict[str, Any] | None:
    """Fetch certificate metadata for ``host``. Never raises; ``None`` on failure.

    Tests monkeypatch this function to run offline.
    """
    host = host.strip().rstrip(".")
    if not host:
        return None
    import os

    if os.environ.get("WTH_TEST_MODE"):
        return None
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert() or {}
        return cert
    except Exception:  # noqa: BLE001 — connection/handshake failures expected
        return None


def _join_rdn(seq: Any) -> str:
    parts: list[str] = []
    for rdn in seq or ():
        for k, v in rdn:
            parts.append(f"{k}={v}")
    return ", ".join(parts)


def run(host: str) -> list[dict[str, Any]]:
    cert = cert_info(host)
    if not cert:
        return []
    san = [v for typ, v in cert.get("subjectAltName", ()) if typ == "DNS"]
    item = {
        "kind": "tls",
        "host": host,
        "subject": _join_rdn(cert.get("subject")),
        "issuer": _join_rdn(cert.get("issuer")),
        "not_after": cert.get("notAfter", ""),
        "san": san,
    }
    items: list[dict[str, Any]] = [item]
    # SAN entries are also subdomains worth tracking.
    for s in san:
        s = s.lstrip("*.").lower()
        if s and s != host:
            items.append({"kind": "subdomain", "host": s})
    return items

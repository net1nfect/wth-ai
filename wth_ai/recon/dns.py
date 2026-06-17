"""Builtin DNS recon (non-destructive resolution only).

Uses ``dnspython`` when available for richer record types, otherwise falls back
to the stdlib ``socket`` resolver. Tests monkeypatch :func:`resolve_host`.
"""

from __future__ import annotations

import socket
from typing import Any


def resolve_host(host: str) -> list[dict[str, str]]:
    """Resolve ``host`` to a list of ``{record_type, value}`` dicts.

    Never raises; returns ``[]`` on failure.
    """
    host = host.strip().rstrip(".")
    if not host:
        return []
    import os

    if os.environ.get("WTH_TEST_MODE"):
        return []

    records: list[dict[str, str]] = []
    try:
        import dns.resolver  # type: ignore

        for rtype in ("A", "AAAA", "CNAME", "MX", "NS", "TXT"):
            try:
                answers = dns.resolver.resolve(host, rtype, lifetime=5)
                for rdata in answers:
                    records.append({"record_type": rtype, "value": str(rdata).strip()})
            except Exception:  # noqa: BLE001 — missing record type is normal
                continue
        if records:
            return records
    except Exception:  # noqa: BLE001 — dnspython missing or resolver error
        pass

    # stdlib fallback: A/AAAA only
    try:
        infos = socket.getaddrinfo(host, None)
        seen: set[str] = set()
        for fam, _, _, _, sockaddr in infos:
            ip = sockaddr[0]
            if ip in seen:
                continue
            seen.add(ip)
            rtype = "AAAA" if fam == socket.AF_INET6 else "A"
            records.append({"record_type": rtype, "value": ip})
    except OSError:
        return records
    return records


def run(host: str, *, subdomains: list[str] | None = None) -> list[dict[str, Any]]:
    """Builtin DNS module entrypoint -> normalized items."""
    items: list[dict[str, Any]] = []
    targets = [host, *(subdomains or [])]
    seen_hosts: set[str] = set()
    for h in targets:
        h = h.strip().rstrip(".")
        if not h or h in seen_hosts:
            continue
        seen_hosts.add(h)
        recs = resolve_host(h)
        if recs:
            items.append({"kind": "subdomain", "host": h})
        for rec in recs:
            items.append(
                {
                    "kind": "dns_record",
                    "host": h,
                    "record_type": rec["record_type"],
                    "value": rec["value"],
                }
            )
    return items

"""Small, safe builtin wordlists for non-aggressive content discovery.

Intentionally tiny and low-noise — recon-only, never an aggressive brute force.
"""

from __future__ import annotations

# Common, low-risk, well-known paths. No credential/admin brute-forcing.
COMMON_SAFE_PATHS: tuple[str, ...] = (
    "robots.txt",
    "sitemap.xml",
    "security.txt",
    ".well-known/security.txt",
    "humans.txt",
    "favicon.ico",
    "openapi.json",
    "swagger.json",
    "swagger-ui.html",
    "api-docs",
    "api/openapi.json",
    "graphql",
    ".well-known/openid-configuration",
    "manifest.json",
    "asset-manifest.json",
    "crossdomain.xml",
)

# Well-known API/doc surface paths (used by api_schema builtin discovery).
API_DOC_PATHS: tuple[str, ...] = (
    "swagger.json",
    "openapi.json",
    "api-docs",
    "v2/api-docs",
    "v3/api-docs",
    "graphql",
    "graphiql",
    "redoc",
)


def safe_paths(limit: int = 16) -> list[str]:
    return list(COMMON_SAFE_PATHS[:limit])

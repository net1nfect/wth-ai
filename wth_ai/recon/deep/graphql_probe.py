"""GraphQL introspection probe."""

from __future__ import annotations

from typing import Any

import requests

INTROSPECTION = """
query IntrospectionQuery {
  __schema { queryType { name } mutationType { name } }
}
"""


class GraphqlProbe:
    def __init__(self, *, timeout: int = 10) -> None:
        self.timeout = timeout

    def probe(self, base_url: str) -> dict[str, Any]:
        if not base_url.startswith("http"):
            base_url = f"https://{base_url}"
        candidates = [
            f"{base_url.rstrip('/')}/graphql",
            f"{base_url.rstrip('/')}/api/graphql",
            f"{base_url.rstrip('/')}/v1/graphql",
        ]
        results: list[dict[str, Any]] = []
        for url in candidates:
            try:
                r = requests.post(
                    url,
                    json={"query": INTROSPECTION},
                    timeout=self.timeout,
                    headers={"Content-Type": "application/json"},
                )
                if r.status_code == 200 and "__schema" in r.text:
                    results.append({"url": url, "introspection": True, "snippet": r.text[:500]})
            except requests.RequestException:
                continue
        return {"graphql": results, "found": bool(results)}

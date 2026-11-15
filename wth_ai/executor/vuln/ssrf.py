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
        from wth_ai.pipeline.executor import execute_pipeline

        return execute_pipeline(self, task, "ssrf")

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

    def _try_oob_callback(self, task: ExecutorTask) -> str:
        """Jalur callback blind-SSRF lewat Callback Center lokal (F2).

        Alur yang benar untuk SSRF *blind*: URL callback milik kita dikirim ke
        parameter yang rentan, lalu kehadiran hit di listener membuktikan server
        target benar-benar menghubungi URL kita — bukti yang tidak bisa dipalsukan
        oleh error generik.

        Kontrak penting:
        * **Tidak pernah membuka listener sendiri.** Listener adalah keputusan
          operator (`/oob start`); executor hanya memakainya bila sudah hidup.
        * **Tidak ada HTTP saat listener mati** — supaya jumlah request di jalur
          non-blind tidak berubah (lihat `test_vuln_executor_scope_guard.py`).
        * Kalau Callback Center tidak tersedia, mundur ke jalur MCP lama
          (kompatibel), dan mengembalikan "" bila keduanya tidak ada.
        """
        try:
            from wth_ai.oob import get_oob_service

            svc = get_oob_service()
            if svc.running:
                entry = svc.create(
                    label=f"blind-ssrf:{task.id}",
                    session_id=getattr(self.state, "session_id", ""),
                    target=getattr(getattr(self.state, "scope", None), "target", ""),
                )
                return (
                    f"token {entry['token'][:12]}… siap — kirim `{entry['url']}` ke parameter "
                    f"yang diuji, lalu periksa hit lewat `oob_check`/`/oob hits {entry['token']}`; "
                    "hit = bukti server menghubungi URL kita"
                )
        except Exception:  # noqa: BLE001
            pass
        return self._try_mcp_oob(task)

    def _try_mcp_oob(self, task: ExecutorTask) -> str:
        """Fallback lama: OOB via MCP bila Callback Center lokal tidak aktif."""
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

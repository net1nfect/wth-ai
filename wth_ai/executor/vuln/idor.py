"""IDOR/BOLA executor — multi-account object swap."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from wth_ai.executor.vuln.base import BaseVulnExecutor
from wth_ai.models import ExecutorResult, ExecutorTask


class IdorExecutor(BaseVulnExecutor):
    vuln_type = "idor"

    def execute(self, task: ExecutorTask) -> ExecutorResult:
        url = task.target
        if not url.startswith("http"):
            url = f"https://{url}"
        roles = self.sessions.role_names()
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        id_param = next((p for p in params if p.lower() in {"id", "user_id", "uuid", "account_id"}), None)
        path_ids = re.findall(r"/(\d+|[0-9a-f-]{36})", parsed.path)
        object_id = path_ids[-1] if path_ids else (params[id_param][0] if id_param else "")

        if len(roles) < 2:
            # Tidak lagi `blocked`. IDOR tanpa akun uji adalah kasus yang paling umum
            # di dunia nyata (endpoint terbuka yang membocorkan objek milik orang
            # lain), dan menghentikan pengujian di sini membuat operator kehilangan
            # temuan yang justru paling mudah dibuktikan. Yang dilakukan: ambil objek
            # di URL, tukar id ke tetangganya, bandingkan respons.
            return self._probe_object_swap(task, url, parsed, params, id_param, object_id)

        user_a, user_b = roles[0], roles[1]
        baseline = self.sessions.request(user_a, "GET", url)
        cross = self.sessions.request(user_b, "GET", url)
        diff = self.sessions.diff_responses(baseline, cross)

        evidence: list[str] = []
        observations = [f"baseline({user_a}) status={baseline.get('status')}", f"cross({user_b}) status={cross.get('status')}"]
        request_pairs = [
            {"role": user_a, "method": "GET", "url": url, "response_status": baseline.get("status"), "body_snippet": baseline.get("body", "")[:500]},
            {"role": user_b, "method": "GET", "url": url, "response_status": cross.get("status"), "body_snippet": cross.get("body", "")[:500]},
        ]

        if cross.get("status") == 200 and diff.get("bodies_differ") and baseline.get("status") == 200:
            if len(cross.get("body", "")) > 50:
                evidence.append(f"User B received different response for object {object_id or 'unknown'}")
                status = "success"
                risk = "high"
            else:
                status = "success"
                risk = "medium"
                evidence.append("Responses differ — manual review needed")
        elif cross.get("status") == 200 and baseline.get("status") == 403:
            evidence.append("Access control may be enforced (403 vs 200)")
            status = "success"
            risk = "low"
        else:
            status = "success"
            risk = "low"
            observations.append("No clear IDOR signal — may need authenticated session login")

        return self._result(
            task, status=status,
            summary=f"IDOR probe: {user_a} vs {user_b} on {url[:80]}",
            evidence=evidence, observations=observations, risk=risk,
            request_pairs=request_pairs,
        )

    def _probe_object_swap(self, task, url, parsed, params, id_param, object_id) -> ExecutorResult:
        """Tanpa akun: tukar id objek dan bandingkan. Bukti = dua respons 200 yang
        isinya BERBEDA dan berisi data objek (bukan halaman error/halaman sama)."""
        import requests

        if not object_id:
            return self._result(
                task, status="success",
                summary="IDOR probe butuh parameter objek (id/uuid); tidak ada di URL",
                evidence=[], observations=["Tambahkan parameter objek, mis. ?id=1"],
                risk="low",
            )

        def _neighbour(value: str) -> str:
            if value.isdigit():
                return str(int(value) + 1)
            # uuid/hex: ubah satu digit terakhir agar tetap bentuk yang valid
            if len(value) > 1:
                head, last = value[:-1], value[-1]
                return head + ("2" if last != "2" else "3")
            return value

        other = _neighbour(object_id)

        def _with_id(new_id: str) -> str:
            if id_param and id_param in params:
                mutated = dict(params)
                mutated[id_param] = [new_id]
                return urlunparse(parsed._replace(query=urlencode(mutated, doseq=True)))
            return urlunparse(parsed._replace(path=parsed.path.replace(object_id, new_id, 1)))

        url_a, url_b = url, _with_id(other)
        try:
            ra = requests.get(url_a, timeout=10)
            rb = requests.get(url_b, timeout=10)
        except Exception as exc:  # noqa: BLE001
            return self._result(task, status="failed", summary=f"request gagal: {exc}", evidence=[], observations=[])

        evidence: list[str] = []
        observations = [
            f"object {object_id} -> status={ra.status_code} len={len(ra.text)}",
            f"object {other} -> status={rb.status_code} len={len(rb.text)}",
        ]
        request_pairs = [
            {"method": "GET", "url": url_a, "response_status": ra.status_code, "body_snippet": ra.text[:500]},
            {"method": "GET", "url": url_b, "response_status": rb.status_code, "body_snippet": rb.text[:500]},
        ]

        both_ok = ra.status_code == 200 and rb.status_code == 200
        differ = ra.text.strip() != rb.text.strip()
        looks_like_object = any(
            token in ra.text.lower() for token in ('"id"', '"email"', '"name"', '"role"', '"user"')
        )
        if both_ok and differ and len(rb.text) > 30:
            if looks_like_object:
                evidence.append(f"Objek {other} dapat dibaca tanpa otorisasi (isi berbeda dari {object_id})")
                evidence.append("Respons berisi field objek (id/email/name/role) — data milik objek lain")
                status, risk = "success", "high"
            else:
                evidence.append(f"Objek {other} mengembalikan isi berbeda dari {object_id} — perlu tinjauan")
                status, risk = "success", "medium"
        elif ra.status_code in (401, 403) or rb.status_code in (401, 403):
            evidence.append("Akses ditolak (401/403) — otorisasi tampak ditegakkan pada jalur ini")
            status, risk = "success", "low"
        else:
            # Cabang "tidak ada sinyal" tetap harus menetapkan status/risk: tanpa itu
            # `status` tidak terikat dan hasilnya UnboundLocalError (ketahuan dari test
            # kontrol negatif, bukan dari review).
            observations.append("Tidak ada sinyal IDOR jelas dari pertukaran objek")
            status, risk = "success", "low"

        return self._result(
            task, status=status,
            summary=f"IDOR object-swap (tanpa akun): {object_id} vs {other} pada {url[:70]}",
            evidence=evidence, observations=observations, risk=risk,
            request_pairs=request_pairs,
        )

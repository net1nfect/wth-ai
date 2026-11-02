"""Mesin proaktif: setelah satu temuan, susun sendiri langkah uji berikutnya.

Permintaan operator: AI harus proaktif — begitu ada temuan, dia yang menyusun langkah
berikutnya, bukan menunggu disuruh.

Kenapa ATURAN dan bukan "minta LLM mengarang":
* setiap saran wajib punya ALASAN yang bisa ditelusuri (temuan mana, sinyal respons
  mana) — saran tanpa jejak alasan adalah cara paling cepat menghasilkan tebakan;
* hasilnya bisa diuji tanpa LLM dan tanpa jaringan;
* LLM tetap dipakai untuk memilih/mengarang di luar daftar ini, tapi daftar ini
  memberi titik awal yang konsisten dan tidak bergantung pada model.

Bobot per kelas kerentanan dibaca dari `$WTH_HOME/eval/weights.json` (ditulis oleh
loop self-improvement di `wth_ai/eval/act.py`), sehingga hasil pengukuran benar-benar
mengubah perilaku: kelas yang terbukti produktif naik prioritasnya.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

#: Bobot awal per kelas. 1.0 = netral; angka ini HANYA skala prioritas, bukan klaim
#: kualitas -- dan ditimpa oleh weights.json begitu ada pengukuran nyata.
DEFAULT_WEIGHTS: dict[str, float] = {
    "idor": 1.3,
    "ssrf": 1.2,
    "auth": 1.1,
    "race": 1.0,
    "xss": 0.9,
    "business_logic": 1.4,
    "bypass": 1.2,
}

#: Nilai objek yang lazim dipakai untuk uji tetangga (bukan brute force: 4 nilai).
SIBLING_OBJECT_IDS = ("2", "0", "admin", "1")

#: Varian host untuk membuktikan SSRF tidak hanya bekerja lewat satu bentuk penulisan.
SSRF_HOST_VARIANTS = ("localhost", "[::1]", "0.0.0.0")

OBJ_PARAM_HINTS = ("id", "user", "user_id", "account", "account_id", "uuid", "order", "doc", "file")
URL_PARAM_HINTS = ("url", "uri", "link", "next", "redirect", "dest", "target", "callback", "image", "fetch")


@dataclass(frozen=True)
class NextTest:
    """Satu langkah uji siap-jalan (argumen `http_probe` sudah berbentuk)."""

    title: str
    rationale: str
    probe: dict[str, Any]
    risk: str = "low"
    priority: float = 1.0
    source_finding: str = ""
    vuln_class: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "rationale": self.rationale,
            "probe": self.probe,
            "risk": self.risk,
            "priority": round(self.priority, 3),
            "source_finding": self.source_finding,
            "vuln_class": self.vuln_class,
        }


def load_weights(path: Path | None = None) -> dict[str, float]:
    """Bobot kelas dari hasil pengukuran; jatuh ke default bila belum ada/rusak."""
    target = path or _default_weights_path()
    weights = dict(DEFAULT_WEIGHTS)
    if target is None or not Path(target).exists():
        return weights
    try:
        data = json.loads(Path(target).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return weights
    for key, value in (data.get("weights") or {}).items():
        try:
            weights[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return weights


def _default_weights_path() -> Path | None:
    try:
        from wth_ai.workspace import wth_home

        return wth_home() / "eval" / "weights.json"
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# pembantu URL
# --------------------------------------------------------------------------- #
def _split_url(url: str) -> tuple[str, str, dict[str, str]]:
    """(origin, path, query) tanpa dependensi berat."""
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url if "://" in url else f"https://{url}")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    return origin, parsed.path or "/", query


def _url_with_query(url: str, changes: Mapping[str, str]) -> str:
    from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

    parsed = urlparse(url if "://" in url else f"https://{url}")
    query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    query.update({k: v for k, v in changes.items()})
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def _neighbour_ids(value: str) -> list[str]:
    out: list[str] = []
    if value.isdigit():
        base = int(value)
        for delta in (1, -1, 2):
            candidate = str(base + delta)
            if candidate not in out and candidate != value:
                out.append(candidate)
    for candidate in SIBLING_OBJECT_IDS:
        if candidate not in out and candidate != value:
            out.append(candidate)
    return out[:4]


# --------------------------------------------------------------------------- #
# aturan per kelas
# --------------------------------------------------------------------------- #
def _rules_idor(finding: Mapping[str, Any], weights: Mapping[str, float]) -> list[NextTest]:
    url = str(finding.get("target") or finding.get("url") or "")
    if not url:
        return []
    origin, path, query = _split_url(url)
    obj_param = next((k for k in query if k.lower() in OBJ_PARAM_HINTS), "")
    obj_value = query.get(obj_param, "") if obj_param else ""
    if not obj_value:
        tail = re.findall(r"([A-Za-z0-9-]{1,36})$", path.rstrip("/"))
        obj_value = tail[0] if tail else ""
    weight = float(weights.get("idor", 1.0))
    tests: list[NextTest] = []
    for candidate in _neighbour_ids(obj_value):
        probe_url = _url_with_query(url, {obj_param: candidate}) if obj_param else url.replace(obj_value, candidate, 1)
        tests.append(NextTest(
            title=f"Ambil objek tetangga {candidate}",
            rationale=(
                f"Temuan IDOR di {url[:70]} membuktikan objek tetangga bisa dibaca tanpa otorisasi; "
                f"nilai {candidate} menguji apakah batasnya lebih luas dari satu objek."
            ),
            probe={"method": "GET", "url": probe_url, "purpose": f"idor neighbour {candidate}",
                   "compare_to": str(finding.get("transcript_id") or "")},
            risk="low", priority=2.0 * weight, source_finding=str(finding.get("id", "")),
            vuln_class="idor",
        ))
    # endpoint saudara dengan bentuk path yang sama (mis. /api/user/{id} -> /api/order/{id})
    siblings = re.sub(r"/(api/)?[A-Za-z_-]+/", "/REPLACE/", path)
    if siblings != path:
        tests.append(NextTest(
            title="Uji endpoint bersaudara dengan pola sama",
            rationale=(
                f"Pola path {path[:50]} menghasilkan IDOR; endpoint lain dengan pola serupa "
                "sering dibuat oleh generator/template yang sama dan mewarisi cacat yang sama."
            ),
            probe={"method": "GET", "url": f"{origin}{siblings}".replace("/REPLACE/", "/api/"),
                   "purpose": "idor sibling endpoint"},
            risk="low", priority=1.2 * weight, source_finding=str(finding.get("id", "")),
            vuln_class="idor",
        ))
    return tests


def _rules_ssrf(finding: Mapping[str, Any], weights: Mapping[str, float]) -> list[NextTest]:
    url = str(finding.get("target") or finding.get("url") or "")
    if not url:
        return []
    _origin, _path, query = _split_url(url)
    param = next((k for k in query if k.lower() in URL_PARAM_HINTS), "")
    weight = float(weights.get("ssrf", 1.0))
    tests: list[NextTest] = []
    if param:
        current = query.get(param, "")
        for variant in SSRF_HOST_VARIANTS:
            swapped = re.sub(r"(https?://)[^/:]+", rf"\g<1>{variant}", current) if current else current
            if not swapped or swapped == current:
                continue
            tests.append(NextTest(
                title=f"Ulangi SSRF lewat host {variant}",
                rationale=(
                    "SSRF sudah terbukti dari isi; varian penulisan host memisahkan blokir "
                    "berbasis string (denylist '127.0.0.1') dari blokir di lapisan jaringan."
                ),
                probe={"method": "GET", "url": _url_with_query(url, {param: swapped}),
                       "purpose": f"ssrf host variant {variant}"},
                risk="medium", priority=1.4 * weight, source_finding=str(finding.get("id", "")),
                vuln_class="ssrf",
            ))
    if param:
        tests.append(NextTest(
            title="Uji parameter URL lain di halaman yang sama",
            rationale=(
                "Satu parameter terbukti bisa dipakai mengambil URL sisi server; parameter "
                "lain di halaman yang sama sering memakai helper fetch yang sama."
            ),
            probe={"method": "GET", "url": url, "purpose": "ssrf other params (manual: ganti parameter)"},
            risk="low", priority=1.0 * weight, source_finding=str(finding.get("id", "")),
            vuln_class="ssrf",
        ))
    return tests


def _rules_auth(finding: Mapping[str, Any], weights: Mapping[str, float]) -> list[NextTest]:
    url = str(finding.get("target") or finding.get("url") or "")
    weight = float(weights.get("auth", 1.0))
    if not url:
        return []
    return [
        NextTest(
            title="Uji kredensial default pada user yang sudah terkonfirmasi ada",
            rationale=(
                "Enumerasi mengungkap user yang valid; daftar kredensial default yang sangat "
                "pendek (<=5) pada user itu adalah kelanjutan paling wajar — dan tanpa lockout, "
                "percobaan ini tidak mengunci akun siapa pun."
            ),
            probe={"method": "GET", "url": url, "purpose": "auth default creds on confirmed user"},
            risk="medium", priority=1.3 * weight, source_finding=str(finding.get("id", "")),
            vuln_class="auth",
        ),
        NextTest(
            title="Uji oracle reset password",
            rationale=(
                "Aplikasi yang membocorkan keberadaan user di login biasanya melakukan hal sama "
                "di alur reset; oracle yang sama = permukaan kedua."
            ),
            probe={"method": "POST", "url": url.replace("login", "forgot"), "purpose": "auth reset oracle"},
            risk="low", priority=0.9 * weight, source_finding=str(finding.get("id", "")),
            vuln_class="auth",
        ),
    ]


def _rules_race(finding: Mapping[str, Any], weights: Mapping[str, float]) -> list[NextTest]:
    url = str(finding.get("target") or finding.get("url") or "")
    weight = float(weights.get("race", 1.0))
    if not url:
        return []
    return [NextTest(
        title="Uji jendela balapan pada endpoint pengubah state lain",
        rationale=(
            "Endpoint ini terbukti punya jendela check-then-act; pola yang sama biasanya "
            "berulang di endpoint yang mengubah saldo/kuota/status."
        ),
        probe={"method": "POST", "url": url, "purpose": "race on sibling state-changing endpoint"},
        risk="medium", priority=1.3 * weight, source_finding=str(finding.get("id", "")),
        vuln_class="race",
    )]


def _rules_xss(finding: Mapping[str, Any], weights: Mapping[str, float]) -> list[NextTest]:
    url = str(finding.get("target") or finding.get("url") or "")
    weight = float(weights.get("xss", 1.0))
    if not url:
        return []
    origin, _path, _query = _split_url(url)
    return [NextTest(
        title="Uji konteks lain pada parameter yang sama",
        rationale=(
            "Refleksi terkonfirmasi; nilainya bergantung konteks (atribut/JS/URL) — dan "
            "header CSP dari analisa respons menentukan apakah refleksi bisa dieksekusi."
        ),
        probe={"method": "GET", "url": url, "purpose": "xss context variants (attribute/js/url)"},
        risk="low", priority=1.0 * weight, source_finding=str(finding.get("id", "")),
        vuln_class="xss",
    )]


def _rules_from_signals(
    request_pairs: Sequence[Mapping[str, Any]],
    weights: Mapping[str, float],
) -> list[NextTest]:
    """Saran yang lahir dari SINYAL respons, bukan dari temuan yang sudah ada.

    Ini bagian "adaptif": perilaku server yang menarik belum tentu sudah jadi temuan,
    tapi layak diuji lanjut — dan alasannya disebut eksplisit.
    """
    tests: list[NextTest] = []
    events: dict[str, bool] = {}
    for pair in request_pairs:
        url = str(pair.get("url") or "")
        status = int(pair.get("response_status") or 0)
        snippet = str(pair.get("body_snippet") or "").lower()
        if not url:
            continue
        # 403/401 pada satu metode tapi bukan metode lain = kandidat bypass otorisasi
        if status in (401, 403) and url not in events:
            events[url] = True
            tests.append(NextTest(
                title="Variasikan metode/trailing-slash pada endpoint yang menolak",
                rationale=(
                    f"{url[:60]} menjawab {status}. Pola bypass paling umum pada lapisan routing: "
                    "metode lain, garis miring di akhir, atau perubahan huruf besar-kecil."
                ),
                probe={"method": "HEAD", "url": url, "purpose": f"bypass variant after {status}",
                       "compare_to": str(pair.get("transcript_id") or "")},
                risk="low", priority=1.1 * float(weights.get("bypass", 1.0)),
                vuln_class="bypass",
            ))
        if "too many" in snippet or status == 429:
            tests.append(NextTest(
                title="Catat ambang rate-limit dan uji perilaku setelahnya",
                rationale=(
                    "Server menunjukkan throttling; ambang dan durasinya bagian dari postur "
                    "keamanan (dan bahan laporan), bukan alasan berhenti menguji."
                ),
                probe={"method": "GET", "url": url, "purpose": "rate-limit threshold check"},
                risk="low", priority=0.8, vuln_class="business_logic",
            ))
    return tests


_RULES = {
    "idor": _rules_idor,
    "ssrf": _rules_ssrf,
    "auth": _rules_auth,
    "auth_jwt": _rules_auth,
    "race": _rules_race,
    "xss": _rules_xss,
}


def propose_next_tests(
    *,
    findings: Iterable[Any] = (),
    request_pairs: Iterable[Mapping[str, Any]] = (),
    weights: Mapping[str, float] | None = None,
    limit: int = 12,
) -> list[NextTest]:
    """Susun langkah uji berikutnya dari temuan + sinyal respons.

    Urutan hasil = prioritas (tertinggi dulu). Duplikat dibuang berdasarkan URL+metode
    supaya usulan yang sama tidak menumpuk di daftar.
    """
    weights = dict(weights or load_weights())
    proposals: list[NextTest] = []

    for finding in findings:
        data = finding if isinstance(finding, Mapping) else getattr(finding, "__dict__", {}) or {}
        vuln_class = str(data.get("vuln_class") or data.get("type") or "").lower()
        rule = _RULES.get(vuln_class)
        if rule is None:
            continue
        proposals.extend(rule(data, weights))

    proposals.extend(_rules_from_signals(list(request_pairs), weights))

    seen: set[tuple[str, str]] = set()
    unique: list[NextTest] = []
    for test in sorted(proposals, key=lambda t: -t.priority):
        key = (str(test.probe.get("method", "")).upper(), str(test.probe.get("url", "")))
        if key in seen:
            continue
        seen.add(key)
        unique.append(test)
    return unique[: max(1, limit)]


def format_next_tests(tests: Sequence[NextTest]) -> str:
    """Tampilan untuk operator/agent: alasan ikut ditampilkan, bukan hanya perintah."""
    if not tests:
        return "Belum ada usulan berikutnya — jalankan recon/hunt dulu, atau belum ada sinyal yang layak dikejar."
    lines = [f"## Langkah uji berikutnya ({len(tests)})"]
    for index, test in enumerate(tests, 1):
        lines.append(f"{index}. [{test.risk}] {test.title} (prioritas {test.priority:.1f})")
        lines.append(f"   alasan: {test.rationale}")
        probe = test.probe
        lines.append(f"   probe : {str(probe.get('method', 'GET')).upper()} {str(probe.get('url', ''))[:100]}")
        if probe.get("compare_to"):
            lines.append(f"   banding: {probe['compare_to']}")
    return "\n".join(lines)

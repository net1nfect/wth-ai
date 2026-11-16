"""Verifier jangkar untuk langkah destruktif: bukti bahwa EFEK-nya hilang.

Masalah yang diselesaikan:

Sebelum modul ini, autopilot menandai setiap langkah destruktif yang menerima
respons HTTP sebagai `UNKNOWN` dan tidak pernah mengulanginya. Itu aman, tetapi
terminal: resume berikutnya menahan langkah yang sama selamanya (atau sampai
operator memverifikasi keadaan target secara manual). Status HTTP `200`
membuktikan request selesai, bukan bahwa tulisannya sudah tidak ada.

Kunci desainnya adalah **jangkar (anchor)**, bukan tebak-tebakan:

* Anchor adalah reference request READ-ONLY yang diambil SEBELUM operasi
  destruktif dijalankan. Anchor harus menyebut objek yang akan disentuh
  (mis. `GET /api/cart?item_id=1`), sehingga nilai di dalamnya mewakili
  keadaan *sebelum* — tidak bisa terkontaminasi oleh efek operasi itu sendiri.
* Setelah operasi, reference yang sama dibaca lagi. Operasi destruktif
  dianggap **terverifikasi** hanya bila keadaan sesudah kembali ke keadaan
  jangkar (mis. `qty` -1 hilang, nilai jangkar muncul lagi) — bukan hanya
  karena respons 200.
* Setelah verifikasi, autopilot menjalankan `cleanup` dari katalog: DELETE
  baris yang kita buat, PATCH nilai semula, dan seterusnya. Cleanup baru
  "berhasil" bila reference jangkar setelah cleanup tidak lagi memuat
  indikator efek — kalau masih ada, langkah tetap `UNKNOWN` dengan alasan
  yang bisa ditindaklanjuti.

Verifier ini sengaja deklaratif: ia tidak tahu domain aplikasi. Ia hanya
membandingkan field JSON/teks pada anchor sebelum dan sesudah, dan mencari
indikator efek yang diasosiasikan dengan payload. Payload yang tidak punya
anchor aman tetap `UNKNOWN` — jangan menebak.

## Payload tanpa indikator statis (mis. `GEN-*`)

Payload hasil generate tidak punya nilai efek yang bisa didaftarkan di depan.
Untuk kelas itu dipakai **marker yang disuntikkan**: nilai uji yang unik
ditempelkan ke request oleh payload sendiri dan dicari ulang di anchor.

Yang marker BUKTIKAN dan yang TIDAK:

* **Status/header yang sama sebelum dan sesudah** hanya membuktikan *shape*
  respons tidak berubah. Itu bukan bukti efek state hilang, jadi ia TIDAK
  pernah cukup untuk `COMPLETED` — hanya dipakai untuk alasan penolakan yang
  spesifik.
* **Hilangnya marker yang sebelumnya terlihat** di anchor adalah bukti
  positif: sesuatu yang nyata pernah tersimpan dan sekarang tidak. Itu satu-
  satunya jalan menuju verdict `verified` untuk payload tanpa indikator
  statis.
* Marker yang belum pernah terlihat di anchor = tidak ada bukti apa pun.
  Verdict tetap `unavailable` (bukan `verified`, bukan "target rusak").
"""

from __future__ import annotations

import json

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

#: Marker yang dipakai katalog payload untuk menandai jejak kita sendiri.
DEFAULT_EFFECT_MARKER = "wth-auto-9z8y7"

#: Nilai probe yang BERARTI "berhasil" bila muncul di keadaan target.
#:
#: Katalog menulis nilai efeknya di `body` payload. Daftar ini adalah
#: indikator POSITIF: bila nilai itu terlihat pada anchor sesudah operasi,
#: berarti efek masih ada. Nilai yang wajar bukan bukti apa pun; hanya nilai
#: yang secara struktural khas payload yang dipakai.
_PAYLOAD_EFFECT_VALUES: tuple[tuple[str, str], ...] = (
    ("BL-001", "-1"),            # qty negatif
    ("BL-002", "0.0001"),        # qty pecahan
    ("BL-003", "2147483648"),    # integer overflow
    ("BL-004", '"price":1'),     # harga dimanipulasi di checkout
    ("ST-003", "closed"),        # status tiket diubah
    ("ST-004", '"plan":"enterprise"'),
)

ProbeRunner = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class EffectAnchor:
    """Reference read-only yang mendefinisikan "keadaan awal".

    `probe` harus berupa request READ-ONLY (GET/HEAD) yang menyebut objek yang
    akan disentuh operasi. `expected` adalah nilai yang WAJIB ada di keadaan
    awal (bila ada): bila nilai itu hilang setelah operasi, berarti objeknya
    sudah berubah/hilang — bukan keadaan yang sama.
    """

    probe: dict[str, Any]
    expected: tuple[str, ...] = ()
    description: str = ""

    @property
    def key(self) -> str:
        return str(self.probe.get("url", ""))


@dataclass
class Verification:
    """Hasil verifikasi satu langkah terhadap anchor."""

    verified: bool
    reason: str
    #: True bila reference sesudah operasi tidak bisa dibaca (request gagal,
    #: status 0, respons kosong). Verifikasi yang tidak mungkin dilakukan
    #: TIDAK boleh menghasilkan `verified=False` yang bisa disalahartikan
    #: sebagai "target bersih"; ia dilaporkan sebagai tidak tersedia.
    unavailable: bool = False
    evidence_refs: list[str] = field(default_factory=list)
    #: True bila marker payload terlihat di anchor SESUDAH operasi lalu HILANG
    #: di pembacaan berikutnya (atau sebaliknya: terlihat sebelum dan tidak
    #: terlihat setelah). Hanya jalan bukti ini yang bisa memberi `verified`
    #: untuk payload tanpa indikator statis.
    marker_transition: bool = False
    #: Ringkasan pembanding status/header. Dipakai untuk alasan penolakan yang
    #: konkret, dan TIDAK pernah sebagai bukti sukses sendirian.
    posture: dict[str, Any] = field(default_factory=dict)


def marker_effect(marker: str | None = None) -> str:
    return f"wth-marker:{marker or DEFAULT_EFFECT_MARKER}"


def payload_effect_indicators(payload_id: str) -> tuple[str, ...]:
    """Nilai khas yang menandai efek payload masih terlihat di keadaan target."""
    payload_id = str(payload_id or "").strip().upper()
    out: list[str] = []
    for pid, value in _PAYLOAD_EFFECT_VALUES:
        if pid == payload_id:
            out.append(value)
    return tuple(out)


def indicator_candidates(payload_id: str) -> tuple[str, ...]:
    """Indikator yang mencari di body anchor sesudah operasi.

    Payload katalog memakai daftar statis. Payload tanpa entri statis (kelas
    `GEN-*`) memakai marker yang disuntikkan payload itu sendiri
    (`DEFAULT_EFFECT_MARKER`), karena request-nya membawa nilai itu ke target.
    Pencarian marker hanya menghasilkan verdict bila transisinya terlihat —
    lihat `verify_effect`.
    """
    static = payload_effect_indicators(payload_id)
    if static:
        return static
    return (DEFAULT_EFFECT_MARKER,)


def _read(probe: Mapping[str, Any], run_probe: ProbeRunner) -> tuple[str, str]:
    """Baca anchor. Return (body, status); body kosong = tidak terbaca."""
    try:
        result = run_probe(dict(probe))
    except Exception:  # noqa: BLE001 - verifikasi gagal != target rusak
        return "", ""
    status = str(getattr(result, "status_code", "") or "")
    body = str(getattr(result, "stdout", "") or "")
    return body, status


def _read_with_posture(
    probe: Mapping[str, Any], run_probe: ProbeRunner
) -> tuple[str, str, dict[str, Any]]:
    """Seperti `_read`, plus posture status/header untuk pembanding.

    Posture sengaja diambil dari objek hasil yang sama; membaca dua kali akan
    membuat status dan body bisa berasal dari request berbeda.
    """
    try:
        result = run_probe(dict(probe))
    except Exception:  # noqa: BLE001
        return "", "", {}

    def _field(name: str) -> Any:
        # `stdout` adalah field kanonik hasil probe, tapi runner lain (stub,
        # adapter) bisa memakai `body`/`text`. Membaca apa adanya lebih jujur
        # daripada mengembalikan body kosong yang akan terlihat seperti
        # "anchor tidak terbaca".
        for candidate in (name, "body", "text"):
            value = getattr(result, candidate, None)
            if value:
                return value
        return ""

    # `_field()` mengembalikan nilai MENTAH (str) dari atribut hasil. Jangan
    # menulis `str(_field(...) or "")` — untuk runner yang mengembalikan objek
    # hasil yang sama setiap panggilan, body bisa berubah setelah pembacaan
    # ini; membungkusnya dalam `str()` di sini tetap mengambil snapshot tekstual
    # saat ini, tapi menelan nilai non-string (mis. `None`) sehingga badan
    # kosong tidak terlihat sebagai "tidak terbaca".
    status = str(getattr(result, "status_code", "") or "")
    raw_body = _field("stdout")
    body = "" if raw_body is None else str(raw_body)

    headers = getattr(result, "response_headers", None) or {}
    posture: dict[str, Any] = {"status": status}
    if isinstance(headers, Mapping):
        # Urutan dan kapitalisasi header tidak stabil antar-server; normalisasi
        # ke lowercase supaya diff tidak berisi derau.
        posture["headers"] = {
            str(key).lower(): str(value).strip()
            for key, value in headers.items()
            if str(key).strip()
        }
    elif headers:
        posture["headers"] = {}
    return body, status, posture


#: Header yang sengaja diabaikan saat membandingkan posture: nilainya berubah
#: tiap respons walau state tidak berubah, jadi selisihnya tidak informatif.
_VOLATILE_HEADERS = frozenset({
    "date", "server-timing", "age", "x-request-id", "x-runtime",
    "x-amzn-requestid", "x-trace-id", "cf-ray", "x-cache", "x-served-by",
    "etag", "last-modified", "set-cookie", "expires", "content-length",
    "transfer-encoding", "connection", "keep-alive", "vary",
})


def posture_diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """Bandingkan posture status/header sebelum dan sesudah.

    Ini **hanya** pembanding informatif. Status/header yang identik membuktikan
    bahwa *shape* respons tidak berubah — bukan bahwa efek state hilang. Hasil
    fungsi ini tidak boleh dipakai sendirian untuk verdict `verified`.
    """
    out: dict[str, Any] = {}
    before_status = str(before.get("status", "") or "")
    after_status = str(after.get("status", "") or "")
    if before_status != after_status:
        out["status"] = {"before": before_status, "after": after_status}
    before_headers = before.get("headers") or {}
    after_headers = after.get("headers") or {}
    if isinstance(before_headers, Mapping) and isinstance(after_headers, Mapping):
        keys = {
            str(key).lower()
            for key in set(before_headers) | set(after_headers)
        } - _VOLATILE_HEADERS
        changed: dict[str, dict[str, str]] = {}
        for key in sorted(keys):
            left = str(before_headers.get(key, "") or "")
            right = str(after_headers.get(key, "") or "")
            # Header bisa ditulis dengan kapitalisasi berbeda di dua mapping.
            if key not in {str(k).lower() for k in before_headers}:
                left = ""
            if key not in {str(k).lower() for k in after_headers}:
                right = ""
            if left != right:
                changed[key] = {"before": left, "after": right}
        if changed:
            out["headers"] = changed
    return out


def _describe_posture(diff: Mapping[str, Any]) -> str:
    if not diff:
        return "status dan header sama"
    parts: list[str] = []
    status = diff.get("status")
    if isinstance(status, Mapping):
        parts.append(f"status {status.get('before')}->{status.get('after')}")
    headers = diff.get("headers")
    if isinstance(headers, Mapping) and headers:
        parts.append("header berubah: " + ", ".join(sorted(map(str, headers))))
    return "; ".join(parts) or "status dan header sama"


def _json_fields(body: str) -> dict[str, str]:
    """Flatten field JSON tingkat atas yang dipakai untuk membandingkan keadaan.

    Hanya string/angka/bool yang diambil; struktur bersarang diabaikan karena
    anchor yang tidak menyebut field spesifik tidak bisa diverifikasi dengan
    jujur. Body non-JSON dikembalikan kosong (indikator teks tetap dipakai).
    """
    text = body.strip()
    if not text or text[0] not in "[{":
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(value, (str, int, float, bool)):
            out[str(key)] = str(value)
    return out


def _contains_indicator(body: str, indicators: Sequence[str]) -> str | None:
    lowered = body.lower()
    for indicator in indicators:
        if indicator and indicator.lower() in lowered:
            return indicator
    return None


def verify_effect(
    *,
    payload_id: str,
    anchor: EffectAnchor | None,
    run_probe: ProbeRunner,
    before_body: str,
    requires_cleanup: bool = False,
    before_posture: Mapping[str, Any] | None = None,
) -> Verification:
    """Apakah efek payload sudah TIDAK terlihat pada anchor sesudah operasi?

    `before_body` wajib berasal dari anchor yang diambil SEBELUM operasi —
    caller menyediakannya supaya pembacaan sesudah tidak bisa "membuktikan"
    keadaan yang sudah tercemar oleh operasi itu sendiri. `before_posture`
    (status + header) bersifat opsional dan hanya dipakai untuk *alasan
    penolakan* yang lebih konkret; ia tidak pernah menjadi bukti sukses.
    """
    if anchor is None:
        return Verification(
            verified=False,
            reason=(
                "tidak ada anchor read-only untuk objek yang disentuh; "
                "efek tidak dapat diverifikasi tanpa membaca keadaan target"
            ),
            unavailable=True,
        )

    # Pembacaan anchor di sini adalah SATU-SATUNYA panggilan `run_probe` di
    # level ini. `before_body` diteruskan caller dan tidak bisa diambil ulang:
    # request yang sama setelah operasi mungkin sudah tidak mencerminkan
    # keadaan sebelum.
    #
    # Catatan implementasi: `_read_with_posture` sendiri membuat snapshot
    # string dari body (`body = str(raw_body)`), jadi objek hasil yang
    # dipakai ulang runner tetap aman — yang dibandingkan adalah teks pada
    # saat pembacaan, bukan objek hidup.
    after_body, after_status, after_posture = _read_with_posture(
        anchor.probe, run_probe
    )
    if not after_body and not after_status:
        return Verification(
            verified=False,
            reason=f"anchor {anchor.key} tidak dapat dibaca setelah operasi",
            unavailable=True,
        )

    diff = posture_diff(dict(before_posture or {}), after_posture)
    posture_note = _describe_posture(diff)

    static_indicators = payload_effect_indicators(payload_id)
    if static_indicators:
        # Indikator statis: nilainya khas payload, jadi kemunculannya sesudah
        # operasi berarti efek masih hidup. Jalur marker sengaja TIDAK lewat
        # sini — nilainya muncul di body sebelum operasi.
        left = _contains_indicator(after_body, static_indicators)
        if left:
            return Verification(
                verified=False,
                reason=(
                    f"efek payload masih terlihat di {anchor.key} (indikator '{left}'); "
                    f"{posture_note}"
                ),
                posture=diff,
            )

    if requires_cleanup:
        wasted = marker_effect()
        if wasted.split(":", 1)[-1] in after_body.lower():
            return Verification(
                verified=False,
                reason=f"jejak marker uji masih ada di {anchor.key}; {posture_note}",
                posture=diff,
            )

    before_fields = _json_fields(before_body)
    after_fields = _json_fields(after_body)
    if anchor.expected:
        # `expected` adalah field yang mendefinisikan objek jangkar. Yang
        # menentukan verdict adalah keadaan SESUDAH operasi: field yang hilang
        # dari sana berarti objeknya berubah/hilang, bukan kembali ke jangkar.
        # Memeriksa `before_body` di sini akan membuat field yang ada SEBELUM
        # operasi tidak pernah bisa memicu penolakan — persis kebalikan dari
        # yang dibutuhkan. Body non-JSON tetap dicek sebagai substring, karena
        # `_json_fields` mengembalikan kosong untuk body seperti itu.
        missing = [
            name for name in anchor.expected
            if name not in after_fields and name not in after_body
        ]
        if missing:
            return Verification(
                verified=False,
                reason=(
                    "objek jangkar berubah: field "
                    f"{', '.join(missing)} tidak ada di {anchor.key} sesudah "
                    f"operasi; {posture_note}"
                ),
                posture=diff,
            )

    if not before_body.strip():
        return Verification(
            verified=False,
            reason=f"jangkar {anchor.key} kosong sebelum operasi; tidak ada pembanding",
            unavailable=True,
            posture=diff,
        )

    # --- payload dengan indikator statis ---------------------------------
    if static_indicators:
        if before_fields and after_fields:
            if before_fields == after_fields:
                return Verification(
                    verified=True,
                    reason=f"{anchor.key} kembali ke keadaan jangkar",
                    posture=diff,
                )
            changed = sorted(
                key for key in set(before_fields) | set(after_fields)
                if before_fields.get(key) != after_fields.get(key)
            )
            if changed:
                # Field berbeda tapi tidak ada indikator efek payload: tidak
                # bisa dibedakan dari perubahan lain (mis. timestamp).
                return Verification(
                    verified=False,
                    reason=(
                        "keadaan anchor berbeda dari jangkar pada "
                        f"{', '.join(changed)}; bukan bukti pemulihan; {posture_note}"
                    ),
                    unavailable=True,
                    posture=diff,
                )
        return Verification(
            verified=True,
            reason=f"indikator efek payload tidak lagi ada di {anchor.key}",
            posture=diff,
        )

    # --- payload TANPA indikator statis (mis. GEN-*) ----------------------
    # Marker hanya berguna bila transisinya terlihat. Hilangnya marker yang
    # sebelumnya ADA di anchor adalah satu-satunya bukti positif yang jujur;
    # marker yang tidak pernah terlihat berarti tidak ada bukti apa pun.
    # Status/header yang sama TIDAK cukup: itu hanya membuktikan shape respons
    # tidak berubah.
    marker = DEFAULT_EFFECT_MARKER
    # Bandingkan case-insensitive. Marker adalah ASCII, jadi `lower()` aman;
    # body target bisa ditampilkan dengan kapitalisasi apa pun.
    #
    # Semua nilai di bawah dihitung SEKALI dari salinan string lokal. `after_body`
    # hanya boleh dipakai lewat salinan itu: objek hasil probe bisa dipakai ulang
    # runner, dan membaca atributnya berulang kali membuat perbandingan bergantung
    # pada saat pembacaan, bukan pada isi yang benar-benar diterima di atas.
    before_text = str(before_body)
    after_text = str(after_body)
    before_has_marker = marker in before_text.lower()
    after_has_marker = marker in after_text.lower()
    if before_has_marker and not after_has_marker:
        return Verification(
            verified=True,
            reason=(
                f"marker uji '{marker}' hilang dari {anchor.key} setelah operasi; "
                f"{posture_note}"
            ),
            marker_transition=True,
            posture=diff,
        )
    if before_has_marker and after_has_marker:
        return Verification(
            verified=False,
            reason=(
                f"marker uji '{marker}' masih terlihat di {anchor.key}; {posture_note}"
            ),
            posture=diff,
        )

    if before_fields == after_fields and before_fields:
        return Verification(
            verified=False,
            reason=(
                "payload tanpa indikator efek terdaftar dan marker uji tidak pernah "
                f"terlihat di {anchor.key}; {posture_note} — status/header sama hanya "
                "membuktikan shape respons, bukan hilangnya efek state"
            ),
            unavailable=True,
            posture=diff,
        )
    return Verification(
        verified=False,
        reason=(
            "payload tanpa indikator efek terdaftar; marker uji tidak terlihat di "
            f"{anchor.key} sehingga tidak ada bukti yang bisa dibaca; {posture_note}"
        ),
        unavailable=True,
        posture=diff,
    )


def verify_cleanup(
    *,
    payload_id: str,
    anchor: EffectAnchor | None,
    run_probe: ProbeRunner,
    before_had_marker: bool | None = None,
) -> Verification:
    """Apakah cleanup berhasil (jejak kita hilang)?

    Dipakai setelah `POST .../cleanup` (DELETE/PATCH) yang sukses. Bila anchor
    tidak terbaca, hasilnya `unavailable` — bukan gagal, bukan sukses.

    `before_had_marker`: apakah marker payload PERNAH terlihat di anchor
    (sebelum operasi). Wajib untuk payload tanpa indikator statis: tanpa
    pembanding itu, tidak adanya marker di satu pembacaan bukan bukti apa pun.
    """
    if anchor is None:
        return Verification(
            verified=False,
            reason="tidak ada anchor untuk memverifikasi cleanup",
            unavailable=True,
        )
    body, status = _read(anchor.probe, run_probe)
    if not body and not status:
        return Verification(
            verified=False,
            reason=f"anchor {anchor.key} tidak dapat dibaca setelah cleanup",
            unavailable=True,
        )
    indicators = indicator_candidates(payload_id)
    left = _contains_indicator(body, indicators)
    if left:
        return Verification(
            verified=False,
            reason=f"cleanup: efek '{left}' masih terlihat di {anchor.key}",
        )
    marker = DEFAULT_EFFECT_MARKER
    if marker in body.lower():
        return Verification(
            verified=False,
            reason=f"cleanup: marker uji masih ada di {anchor.key}",
        )
    if not payload_effect_indicators(payload_id) and not before_had_marker:
        # Payload tanpa indikator statis: cleanup hanya terverifikasi bila
        # MARKER-nya hilang dari pembacaan sebelumnya. Tanpa pembanding itu,
        # "tidak ada marker di body" bukan bukti apa pun.
        return Verification(
            verified=False,
            reason=(
                f"payload tanpa indikator efek terdaftar; hilangnya marker tidak "
                f"bisa dibuktikan dari satu pembacaan {anchor.key} saja"
            ),
            unavailable=True,
        )
    return Verification(
        verified=True,
        reason=f"cleanup terverifikasi: {anchor.key} bersih dari efek payload",
    )

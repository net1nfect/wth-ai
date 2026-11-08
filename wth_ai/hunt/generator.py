"""Generator payload: AI menyusun payload-nya SENDIRI, dengan batas yang tetap berlaku.

Permintaan operator: "AI-nya tetap bisa generate sendiri payload destruktif". Jadi
katalog statis bukan langit-langit — model boleh mengarang payload baru untuk situasi
yang tidak ada di katalog (endpoint khusus, alur bisnis khusus, teknologi khusus).

Yang TIDAK dikompromikan saat AI mengarang, dan alasannya:

* **Struktur wajib.** Payload hasil karangan harus menyatakan `proves` (temuan apa yang
  dibuktikan) dan `cleanup` (cara memulihkan) bila destruktif. Payload tanpa pernyataan
  bukti bukan payload, itu tembakan acak; payload destruktif tanpa pemulihan merusak
  engagement.
* **`blast_radius` wajib dan tidak bisa dikarang bebas.** Nilai `mass` DITERIMA dan
  DISIMPAN (jadi idenya tidak hilang), tetapi langsung dipaksa `auto_runnable=False`:
  mesin tidak pernah menjalankannya, dan katalog akan menampilkan padanan berbatasnya.
  Ini bukan penolakan atas permintaan operator — ini yang membuat laporan tetap bisa
  ditulis setelah pengujian.
* **Gerbang destruktif tetap di jalur eksekusi** (`Scope.destructive_allowed` + audit).
  Generator hanya menyiapkan; yang memutuskan boleh-jalan tetap engagement.

Hasil generate disimpan di `$WTH_HOME/payloads/generated.json` dan ikut terbaca oleh
`filter_payloads()` / `get_payload()` bersama katalog statis.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from wth_ai.hunt.payloads import BLAST_ORDER, Payload

#: Bidang yang wajib ada di payload karangan AI.
REQUIRED_FIELDS = ("vuln_class", "title", "proves", "probe")

#: Kelas yang dikenali. Kelas baru boleh, tapi harus disebut eksplisit oleh model
#: (supaya tidak ada payload tanpa kelas yang lolos ke pelaporan).
KNOWN_CLASSES = frozenset({
    "business_logic", "state_machine", "race", "mass_assignment", "idor_write",
    "injection_detect", "file_path", "cache_desync", "api_graphql", "auth_session",
    "cloud_storage", "host_header", "adaptive",
})

MAX_GENERATED_FILE_BYTES = 2_000_000


def generated_path() -> Path | None:
    try:
        from wth_ai.workspace import wth_home

        return wth_home() / "payloads" / "generated.json"
    except Exception:  # noqa: BLE001
        return None


def _slug(text: str, *, limit: int = 32) -> str:
    return (re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "payload")[:limit]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class GenerationResult:
    accepted: list[Payload]
    rejected: list[tuple[dict[str, Any], str]]
    saved_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": [p.to_dict() for p in self.accepted],
            "rejected": [{"payload": data, "reason": reason} for data, reason in self.rejected],
            "saved_path": self.saved_path,
        }


# --------------------------------------------------------------------------- #
# validasi
# --------------------------------------------------------------------------- #
def validate_generated(data: Mapping[str, Any]) -> tuple[Payload | None, str]:
    """(payload, "") bila sah; (None, alasan) bila tidak.

    Aturan ini yang membuat "AI mengarang sendiri" tetap bisa dipertanggungjawabkan.
    """
    if not isinstance(data, Mapping):
        return None, "bukan objek JSON"

    missing = [field for field in REQUIRED_FIELDS if not str(data.get(field, "") or "").strip()]
    if missing:
        return None, f"bidang wajib kosong: {', '.join(missing)}"

    probe = data.get("probe")
    if not isinstance(probe, Mapping) or not str(probe.get("url", "") or "").strip():
        return None, "probe harus objek dengan `url`"
    method = str(probe.get("method", "GET") or "GET").upper()
    if method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}:
        return None, f"method tidak dikenal: {method}"

    blast = str(data.get("blast_radius", "single-object") or "single-object").strip().lower()
    if blast not in BLAST_ORDER:
        return None, f"blast_radius tidak sah: {blast} (pakai self|single-object|mass)"

    vuln_class = str(data.get("vuln_class", "")).strip().lower()
    if vuln_class not in KNOWN_CLASSES:
        return None, f"kelas tidak dikenal: {vuln_class}"

    # Sifat destruktif DITURUNKAN dari metode, bukan dipercaya dari pengakuan model:
    # terukur dari test -- payload DELETE tanpa `requires_destructive` dan tanpa
    # `cleanup` lolos aturan lama, padahal ia menghapus data. Model yang lupa (atau
    # sengaja tidak) mendeklarasikan tidak boleh membuat pagarnya hilang.
    state_changing = method not in {"GET", "HEAD", "OPTIONS"}
    destructive = bool(data.get("requires_destructive", False)) or state_changing
    cleanup = str(data.get("cleanup", "") or "").strip()
    if destructive and not cleanup:
        return None, (
            f"metode {method} mengubah/menghapus data, jadi wajib menyatakan `cleanup` "
            "(cara memulihkan keadaan)"
        )

    reversible = bool(data.get("reversible", True))
    if destructive and not reversible and not cleanup:
        return None, "tidak reversibel tanpa pemulihan manual = tidak boleh dipakai"

    # Payload massal: DITERIMA sebagai dokumentasi, tapi tidak pernah bisa dijalankan.
    auto_runnable = bool(data.get("auto_runnable", True))
    bounded = str(data.get("bounded_alternative", "") or "").strip()
    if blast == "mass":
        auto_runnable = False
        bounded = bounded or (
            "Belum ada padanan berbatas yang dinyatakan — susun satu payload yang menyentuh "
            "objek/marker sendiri untuk membuktikan kelas bug yang sama."
        )

    payload = Payload(
        id=str(data.get("id") or f"GEN-{_slug(vuln_class, limit=14)}-{_slug(str(data.get('title')), limit=18)}"),
        vuln_class=vuln_class,
        title=str(data.get("title")).strip()[:120],
        proves=str(data.get("proves")).strip()[:400],
        probe={**{k: v for k, v in probe.items()}},
        blast_radius=blast,
        reversible=reversible,
        cleanup=cleanup,
        requires_destructive=destructive,
        severity_hint=str(data.get("severity_hint", "medium")).lower(),
        auto_runnable=auto_runnable,
        notes=(str(data.get("notes", "") or "")[:400] + f" | digenerate AI {_now()}").strip(" |"),
        bounded_alternative=bounded,
        references=tuple(str(r) for r in (data.get("references") or [])),
    )
    return payload, ""


# --------------------------------------------------------------------------- #
# penyimpanan
# --------------------------------------------------------------------------- #
def load_generated(path: Path | None = None) -> list[Payload]:
    target = path or generated_path()
    if target is None or not Path(target).exists():
        return []
    try:
        if Path(target).stat().st_size > MAX_GENERATED_FILE_BYTES:
            return []
        raw = json.loads(Path(target).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = raw.get("payloads") if isinstance(raw, dict) else raw
    out: list[Payload] = []
    for item in items or []:
        payload, _error = validate_generated(item)
        if payload is not None:
            out.append(payload)
    return out


def save_generated(payloads: Iterable[Payload], path: Path | None = None) -> Path | None:
    """Tambahkan payload ke store generate (merge per id, tidak menimpa yang lama)."""
    target = path or generated_path()
    if target is None:
        return None
    existing = {payload.id: payload.to_dict() for payload in load_generated(target)}
    for payload in payloads:
        existing[payload.id] = payload.to_dict()
    try:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_text(
            json.dumps({"updated": _now(), "payloads": list(existing.values())}, indent=2),
            encoding="utf-8",
        )
    except OSError:
        return None
    return target


# --------------------------------------------------------------------------- #
# generate dengan LLM
# --------------------------------------------------------------------------- #
GENERATION_PROMPT = """Kamu menyusun payload pengujian keamanan untuk engagement yang SUDAH \
berizin (target milik operator sendiri / bug bounty in-scope). Balas HANYA array JSON, \
tanpa penjelasan.

Kelas: {vuln_class}
Tujuan operator: {goal}
Teknologi target: {tech_stack}
Jumlah: {count}

Setiap objek wajib punya bidang:
  "title": judul singkat teknik
  "proves": temuan apa yang dibuktikan payload ini (satu kalimat)
  "probe": {{"method": "...", "url": "...", "body": "...", "headers": {{...}}}}
  "blast_radius": "self" | "single-object" | "mass"
  "reversible": true/false
  "cleanup": cara memulihkan keadaan (wajib bila mengubah/menghapus data)
  "requires_destructive": true/false
  "notes": catatan singkat / jebakan

Aturan: placeholder {{base}}, {{marker}}, {{own_id}}, {{other_id}} boleh dipakai.
Payload yang berdampak banyak data tetap boleh ditulis dengan blast_radius "mass" \
(akan disimpan sebagai dokumentasi), tetapi sertakan juga padanan berbatas di \
"bounded_alternative" bila memungkinkan."""


def generate_with_llm(
    router: Any,
    *,
    vuln_class: str,
    goal: str,
    tech_stack: str = "",
    count: int = 3,
    role: str = "planner",
    timeout: int = 90,
) -> GenerationResult:
    """Minta model menyusun payload, lalu validasi setiap usulannya.

    Yang menerima atau menolak adalah `validate_generated()` -- bukan model, dan bukan
    juga keyakinan model. Payload yang tidak menyatakan bukti/pemulihan ditolak dengan
    alasan yang bisa dibaca operator.
    """
    prompt = GENERATION_PROMPT.format(vuln_class=vuln_class, goal=goal,
                                      tech_stack=tech_stack or "tidak diketahui",
                                      count=max(1, min(10, int(count))))
    try:
        # `chat_for_role` TIDAK menerima `max_tokens` (signature-nya: role, messages, *,
        # timeout, stream, on_token, response_format). Versi pertama gua mengirim
        # max_tokens dan hasilnya TypeError yang tertangkap sebagai "LLM gagal" --
        # jebakan yang sama seperti mengarang API dari ingatan.
        text, _model = router.chat_for_role(
            role, [{"role": "user", "content": prompt}], timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        return GenerationResult(accepted=[], rejected=[({"role": role}, f"LLM gagal: {type(exc).__name__}")])

    payload, _ = validate_generated({"vuln_class": vuln_class, "title": "placeholder validation",
                                     "proves": "x", "probe": {"url": "x"}})  # noqa: F841
    candidates = _extract_json_array(text)
    if not candidates:
        return GenerationResult(accepted=[], rejected=[({"raw": str(text)[:400]}, "tidak ada array JSON yang bisa dibaca")])

    accepted: list[Payload] = []
    rejected: list[tuple[dict[str, Any], str]] = []
    for item in candidates:
        if isinstance(item, Mapping) and not item.get("vuln_class"):
            item = {**item, "vuln_class": vuln_class}
        payload, error = validate_generated(item if isinstance(item, Mapping) else {})
        if payload is None:
            rejected.append((dict(item) if isinstance(item, Mapping) else {"raw": str(item)[:200]}, error))
        else:
            accepted.append(payload)
    return GenerationResult(accepted=accepted, rejected=rejected)


def _extract_json_array(text: str) -> list[Any]:
    """Ambil array JSON pertama dari keluaran model (tahan pagar kode & teks pengantar)."""
    if not text:
        return []
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", candidate, re.S)
    if fenced:
        candidate = fenced.group(1)
    start = candidate.find("[")
    end = candidate.rfind("]")
    if start == -1 or end <= start:
        return []
    blob = candidate[start:end + 1]
    try:
        data = json.loads(blob)
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def format_generation(result: GenerationResult) -> str:
    lines = [f"## Generate payload — {len(result.accepted)} diterima, {len(result.rejected)} ditolak"]
    for payload in result.accepted:
        mass = " [MASSAL — dokumentasi saja, tidak dijalankan mesin]" if payload.blast_radius == "mass" else ""
        lines.append(f"- `{payload.id}` {payload.title}{mass}")
        lines.append(f"    membuktikan: {payload.proves}")
        if payload.cleanup:
            lines.append(f"    pemulihan: {payload.cleanup}")
    for data, reason in result.rejected:
        title = str(data.get("title") or data.get("raw") or "?")[:60]
        lines.append(f"- DITOLAK {title}: {reason}")
    if result.saved_path:
        lines.append(f"\nDisimpan: {result.saved_path} (ikut terbaca `filter_payloads()`)")
    return "\n".join(lines)

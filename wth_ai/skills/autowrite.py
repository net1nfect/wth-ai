"""Penulis skill otomatis — self-improvement dalam bentuk memori prosedural.

Permintaan operator: "langsung otomatis nulis skill/update skill".

Aturan yang dijaga (kalau tidak, ini berubah jadi generator sampah):
* **Tanpa bukti, tidak menulis.** Butuh minimal satu rujukan `[TX:...]`/`[EV:...]`.
  Skill yang lahir dari klaim tanpa bukti akan dipakai ulang sebagai "pengetahuan"
  dan memperbanyak kesalahan yang sama.
* **Rahasia tidak pernah masuk.** Isi skill dilewatkan redaksi sebelum ditulis.
* **Skill manusia tidak pernah ditimpa.** Update hanya untuk skill yang frontmatter-nya
  `auto: true`; kalau slug sudah dipakai skill non-auto, tulis ke slug baru.
* **Catatan, bukan klaim.** Skill menyimpan teknik + bukti + jebakan yang TERAMATI,
  bukan pernyataan bahwa sesuatu "berhasil" tanpa jejak.

Lokasi: `$WTH_HOME/skills/auto/<slug>/SKILL.md`, dan `SkillRegistry.discover()`
memindai `$WTH_HOME/skills` sehingga skill ini langsung terpakai oleh router skill.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Sequence

EVIDENCE_RE = re.compile(r"\[(TX|EV):[A-Za-z0-9_-]+\]")

#: Bentuk penugasan yang jelas rahasia. `redact_secrets` global hanya mengenali pola
#: seperti `sk-...`; terukur: `session=supersecretvalue123` lolos. Karena file ini
#: ditulis ke disk dan dipakai ulang sebagai "pengetahuan", masking tambahan di sini
#: bukan kemewahan -- dan klaimnya dibatasi: ini menutup bentuk PENUGASAN, bukan
#: menjanjikan deteksi semua rahasia.
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(session|cookie|token|password|passwd|pass|secret|api[-_]?key|authorization|bearer|csrf)"
    r"(\s*[=:]\s*)(\S{6,})"
)


def mask_secret_assignments(text: str) -> str:
    """Tutup `kunci=nilai-rahasia` sebelum ditulis ke file skill."""
    from wth_ai.redaction import redact_secrets

    masked = SECRET_ASSIGNMENT_RE.sub(r"\1\2***REDACTED***", text or "")
    return redact_secrets(masked)
MAX_EVIDENCE_REFS = 12
MAX_PITFALLS = 8


@dataclass
class SkillNote:
    """Satu pelajaran yang layak disimpan sebagai skill."""

    technique: str
    vuln_class: str = ""
    tech_stack: str = ""
    evidence: list[str] = field(default_factory=list)
    pitfalls: list[str] = field(default_factory=list)
    occurrence: int = 1

    def has_evidence(self) -> bool:
        return any(EVIDENCE_RE.search(str(item)) for item in self.evidence)


def slugify(text: str, *, limit: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return (slug or "skill")[:limit].strip("-")


def auto_skills_root() -> Path | None:
    try:
        from wth_ai.workspace import wth_home

        return wth_home() / "skills" / "auto"
    except Exception:  # noqa: BLE001
        return None


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    out: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            out[key.strip().lower()] = value.strip()
    return out


def _skill_body(note: SkillNote, *, created: str, updated: str, occurrences: int,
                evidence: Sequence[str], pitfalls: Sequence[str], history: Sequence[str]) -> str:
    trigger = f"Use when testing {note.vuln_class or 'a target'} and you see this pattern."
    description = f"{trigger} Teknik yang sudah terbukti pada engagement ini."
    lines = [
        "---",
        f"name: {note.technique.strip()[:60]}",
        f"description: \"{description[:200]}\"",
        "auto: true",
        f"vuln_class: {note.vuln_class or 'unknown'}",
        f"tech_stack: {note.tech_stack or 'unknown'}",
        f"occurrences: {occurrences}",
        f"created: {created}",
        f"updated: {updated}",
        "---",
        "",
        f"# {note.technique.strip()[:80]}",
        "",
        "## Teknik",
        f"{note.technique.strip()}",
        "",
        "## Bukti yang mendukung",
    ]
    for ref in evidence:
        lines.append(f"- `{ref}`")
    lines.append("")
    lines.append("## Jebakan yang teramati")
    if pitfalls:
        for pitfall in pitfalls:
            lines.append(f"- {pitfall}")
    else:
        lines.append("- (belum ada jebakan yang tercatat — tambahkan saat ketemu)")
    if history:
        lines.append("")
        lines.append("## Riwayat konfirmasi")
        for entry in history[-MAX_EVIDENCE_REFS:]:
            lines.append(f"- {entry}")
    lines.append("")
    lines.append(
        "> Skill ini ditulis otomatis dari bukti engagement (rujukan TX/EV di atas). "
        "Ia mencatat teknik dan jebakan yang TERAMATI, bukan klaim keberhasilan."
    )
    return "\n".join(lines)


def record_skill(note: SkillNote, *, root: Path | None = None) -> Path | None:
    """Tulis atau perbarui skill otomatis. None bila tidak layak ditulis.

    Return path SKILL.md yang ditulis, atau None (tanpa bukti / root tidak tersedia).
    """
    if not note or not note.technique.strip():
        return None
    if not note.has_evidence():
        return None  # tanpa rujukan bukti: tidak menulis apa pun

    base = root or auto_skills_root()
    if base is None:
        return None

    evidence = [mask_secret_assignments(str(item)) for item in note.evidence][:MAX_EVIDENCE_REFS]
    pitfalls = [mask_secret_assignments(str(item)) for item in note.pitfalls][:MAX_PITFALLS]

    slug = f"auto-{slugify(note.vuln_class or 'umum', limit=20)}-{slugify(note.technique, limit=28)}"
    directory = base / slug
    skill_md = directory / "SKILL.md"
    today = date.today().isoformat()

    history: list[str] = []
    occurrences = max(1, int(note.occurrence or 1))
    created = today

    if skill_md.exists():
        existing = skill_md.read_text(encoding="utf-8")
        meta = _frontmatter(existing)
        if meta.get("auto", "").lower() != "true":
            # Skill manusia dengan slug yang sama: JANGAN ditimpa. Tulis ke slug baru.
            for suffix in range(2, 10):
                candidate = base / f"{slug}-{suffix}"
                if not (candidate / "SKILL.md").exists():
                    directory, skill_md = candidate, candidate / "SKILL.md"
                    break
        else:
            created = meta.get("created", today) or today
            try:
                occurrences = max(occurrences, int(meta.get("occurrences", "1") or 1) + 1)
            except ValueError:
                occurrences = max(occurrences, 2)
            # kumpulkan bukti lama supaya riwayat tidak hilang saat update
            history = [
                f"{today}: dikonfirmasi lagi ({len(evidence)} rujukan baru)"
            ]
            # `findall` dengan SATU grup mengembalikan ISI grup ("TX"), bukan match
            # penuh ("[TX:one]") -- dan itu membuat rujukan lama HILANG saat file
            # ditulis ulang. `finditer` + group(0) memberi rujukan utuh.
            old_refs = [match.group(0) for match in EVIDENCE_RE.finditer(existing)]
            for token in old_refs:
                if token not in evidence:
                    evidence.append(token)
            evidence = evidence[:MAX_EVIDENCE_REFS]

    directory.mkdir(parents=True, exist_ok=True)
    skill_md.write_text(
        _skill_body(note, created=created, updated=today, occurrences=occurrences,
                    evidence=evidence, pitfalls=pitfalls, history=history),
        encoding="utf-8",
    )
    return skill_md


def note_from_finding(finding: Any, *, technique: str = "", pitfalls: Iterable[str] = ()) -> SkillNote:
    """Bangun catatan skill dari sebuah finding (dipakai setelah verifikasi)."""
    def get(key: str, default: Any = "") -> Any:
        if isinstance(finding, dict):
            return finding.get(key, default)
        return getattr(finding, key, default)
    evidence: list[str] = []
    raw_evidence = get("evidence", []) or []
    if isinstance(raw_evidence, (list, tuple)):
        evidence.extend(str(item) for item in raw_evidence)
    for key in ("transcript_id", "tx_id", "id"):
        value = get(key, "")
        if value and key == "transcript_id":
            evidence.append(f"[TX:{value}]")
    technique_text = technique or str(get("title", "") or get("summary", "") or "teknik tanpa judul")
    return SkillNote(
        technique=technique_text,
        vuln_class=str(get("vuln_class", "") or get("type", "")),
        tech_stack=str(get("tech_stack", "")),
        evidence=evidence,
        pitfalls=list(pitfalls),
    )

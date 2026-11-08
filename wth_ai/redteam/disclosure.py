"""Advisory disclosure dari satu temuan.

Yang dihasilkan: satu markdown yang bisa langsung dikirim ke pemilik aset,
dengan bukti tertaut ke bundel (bukan disalin), dan timeline 90 hari yang
menjadi default industri.
"""

from __future__ import annotations

from datetime import date, timedelta

DISCLOSURE_DAYS = 90

TEMPLATE = """# {title}

**Status:** {label} · **Severitas:** {severity} · **Kelas:** {vuln_class}
**Target:** {target} · **Ditemukan:** {found}
**Atestasi:** {attestation}

## Ringkasan

{summary}

## Langkah reproduksi

{steps}

## Dampak

{impact}

## Remidiasi

{remediation}

## Bukti

Tersedia di bundel: `{evidence}` (verifikasi dengan `wth-ai verify <bundle>`).
Bukti tidak disalin ke dokumen ini agar hash bundel tetap menjadi rujukan tunggal.

## Timeline

- {found} — temuan, laporan awal
- {plus_90} — batas default pengungkapan publik ({days} hari)
{contact_line}
## Catatan

Dokumen ini dibuat otomatis dari bundel bukti. Status `CONFIRMED` berarti klaim
lolos penilaian berbasis bukti (request/response tertangkap, minimal satu
reproduksi, kontrol negatif berbeda perilaku); status `CANDIDATE` belum.
"""


def _bullet(items: list[str], empty: str) -> str:
    if not items:
        return empty
    return "\n".join(f"{index}. {item}" for index, item in enumerate(items, 1))


def _text(value, default: str = "") -> str:
    """Enum → nilainya, dict → label/level-nya, selain itu str()."""
    if isinstance(value, dict):
        for key in ("label", "level", "status"):
            if value.get(key):
                return str(value[key])
        return default
    value = getattr(value, "value", value)
    if value is None or value == "":
        return default
    return str(value)


def build_advisory(finding, *, contact: str = "", summary: str = "",
                   impact: str = "", remediation: str = "",
                   today: date | None = None) -> str:
    """`finding` = objek `Finding` (atau apa pun dengan atribut yang sama)."""
    found = getattr(finding, "created_at", "") or ""
    start = today or date.today()
    try:
        found_date = date.fromisoformat(str(found)[:10])
    except ValueError:
        found_date = start
    evidence = getattr(finding, "evidence", []) or []
    steps = [str(item) for item in evidence] or []
    attestation = _text(getattr(finding, "attestation", ""), "belum diatestasi")
    return TEMPLATE.format(
        title=getattr(finding, "hypothesis", "") or getattr(finding, "id", "temuan"),
        label=_text(getattr(finding, "state", ""), "candidate"),
        severity=_text(getattr(finding, "severity", ""), "medium"),
        vuln_class=_text(getattr(finding, "vuln_class", ""), "-"),
        target=_text(getattr(finding, "target", ""), "-"),
        found=found_date.isoformat(),
        attestation=attestation,
        summary=summary or "Lihat langkah reproduksi; ringkasan naratif belum diisi.",
        steps=_bullet(steps, "Bukti langkah belum dilampirkan pada temuan ini."),
        impact=impact or "Belum dinilai (impact_score belum dijalankan).",
        remediation=remediation or ("Terapkan pemeriksaan otorisasi per objek "
                                    "di sisi server, bukan hanya di UI."),
        evidence=", ".join(evidence) or "evidence/",
        plus_90=(found_date + timedelta(days=DISCLOSURE_DAYS)).isoformat(),
        days=DISCLOSURE_DAYS,
        contact_line=(f"\n## Kontak peneliti\n\n{contact}\n" if contact else ""),
    )
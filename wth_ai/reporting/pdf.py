"""Laporan PDF tanpa dependency baru.

Menulis PDF 1.4 secara langsung: satu font standar (Helvetica), teks satu kolom,
halaman dipotong berdasarkan jumlah baris. Tidak ada library, karena menambah
dependency hanya untuk satu format akan menggeser neraca instalasi untuk semua
orang — dan laporan ini isinya teks.

Yang dihasilkan: `out/report.pdf` dengan ringkasan, tabel temuan sederhana, dan
hash root bundel supaya pembaca bisa mencocokkan dengan `wth verify`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

PAGE_WIDTH = 595
PAGE_HEIGHT = 842
MARGIN_LEFT = 56
MARGIN_TOP = 800
LINE_HEIGHT = 14
MAX_LINES_PER_PAGE = 50
FONT_SIZE = 10
WRAP = 92


def _escape(text: str) -> str:
    """PDF string: kurung dan backslash harus di-escape, non-ASCII dibuang."""
    cleaned = "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in str(text))
    return cleaned.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _wrap(line: str, width: int = WRAP) -> list[str]:
    line = str(line)
    if not line:
        return [""]
    out: list[str] = []
    while len(line) > width:
        cut = line.rfind(" ", 0, width)
        cut = cut if cut > 0 else width
        out.append(line[:cut].rstrip())
        line = line[cut:].lstrip()
    out.append(line)
    return out


def _lines(findings, meta: dict) -> list[str]:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "LAPORAN PENINDAKAN — WTH-AI",
        f"Dibuat: {stamp}",
        "",
        "RINGKASAN",
        f"Total temuan: {len(findings)}",
    ]
    confirmed = [f for f in findings
                 if str(getattr(getattr(f, "state", ""), "value", "")).lower() == "confirmed"]
    lines.append(f"Terkonfirmasi: {len(confirmed)}")
    if meta.get("scan_mode"):
        lines.append(f"Mode: {meta['scan_mode']}")
    if meta.get("root_hash"):
        lines.append(f"Root hash bundel: {meta['root_hash']}")
    lines += ["", "TEMUAN"]
    for finding in findings:
        state = getattr(getattr(finding, "state", ""), "value", "candidate")
        lines.append(f"[{str(state).upper()}] {getattr(finding, 'id', '-')} "
                     f"{getattr(finding, 'severity', '-')} "
                     f"{getattr(finding, 'vuln_class', '-')} "
                     f"{getattr(finding, 'target', '-')}")
        hypothesis = str(getattr(finding, "hypothesis", "") or "").strip()
        if hypothesis:
            lines.append(f"    {hypothesis}")
    lines += ["", "CATATAN",
              "Status CONFIRMED berarti klaim lolos penilaian berbasis bukti:",
              "request/response tertangkap, minimal satu reproduksi, dan kontrol",
              "negatif berperilaku berbeda. CANDIDATE belum memenuhi syarat itu."]
    return lines


def _paginate(lines: list[str]) -> list[list[str]]:
    wrapped: list[str] = []
    for line in lines:
        wrapped.extend(_wrap(line))
    pages: list[list[str]] = []
    for start in range(0, len(wrapped), MAX_LINES_PER_PAGE):
        pages.append(wrapped[start:start + MAX_LINES_PER_PAGE])
    return pages or [[""]]


def render_pdf(findings, *, meta: dict | None = None) -> bytes:
    pages = _paginate(_lines(findings, meta or {}))
    objects: list[bytes] = []

    def add(payload: bytes) -> int:
        objects.append(payload)
        return len(objects)

    catalog_num = len(objects) + 1
    add(b"")                                    # placeholder catalog
    pages_num = len(objects) + 1
    add(b"")                                    # placeholder pages
    font_num = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    page_numbers: list[int] = []
    for page_lines in pages:
        y = MARGIN_TOP
        parts = ["BT", f"/F1 {FONT_SIZE} Tf", f"{LINE_HEIGHT} TL",
                 f"{MARGIN_LEFT} {y} Td"]
        for line in page_lines:
            parts.append(f"({_escape(line)}) Tj T*")
        parts.append("ET")
        stream = "\n".join(parts).encode("latin-1", "replace")
        content_num = add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
                          + stream + b"\nendstream")
        page_num = add(
            f"<< /Type /Page /Parent {pages_num} 0 R "
            f"/MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Resources << /Font << /F1 {font_num} 0 R >> >> "
            f"/Contents {content_num} 0 R >>".encode())
        page_numbers.append(page_num)

    objects[catalog_num - 1] = f"<< /Type /Catalog /Pages {pages_num} 0 R >>".encode()
    kids = " ".join(f"{num} 0 R" for num in page_numbers)
    objects[pages_num - 1] = (
        f"<< /Type /Pages /Count {len(page_numbers)} /Kids [{kids}] >>".encode())

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for index, payload in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + payload + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_num} 0 R >>\n"
            f"startxref\n{xref_at}\n%%EOF\n").encode()
    return bytes(out)


def write_pdf(findings, path: Path, *, meta: dict | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(render_pdf(findings, meta=meta))
    return path
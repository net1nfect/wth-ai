"""Bundel bukti: manifest ber-hash + audit ber-rantai + root hash.

Yang dijual ke juri bukan "kami punya bukti", tapi "ubah satu byte dan
verifikasi akan gagal". Karena itu setiap file dicatat sha256-nya, dan setiap
baris audit menautkan hash baris sebelumnya.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from wth_ai.redaction import redact_data
from wth_ai.redteam.models import Finding


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_bundle(
    out_dir: Path,
    *,
    findings: list[Finding],
    scope_card: dict,
    audit_events: list[dict],
    artifacts: dict[str, Path] | None = None,
) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # P1-0: findings.json dan audit.jsonl bisa memuat header/error mentah.
    # Diredaksi di sini supaya `sha256` di manifest menutup isi yang aman.
    (out / "findings.json").write_text(
        json.dumps(redact_data([f.to_dict() for f in findings]), indent=2,
                   ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "scope_card.json").write_text(
        json.dumps(redact_data(scope_card), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")

    prev = "0" * 64
    lines: list[str] = []
    for event in redact_data(audit_events):
        payload = json.dumps(event, sort_keys=True, ensure_ascii=False)
        digest = sha256_bytes((prev + payload).encode("utf-8"))
        lines.append(json.dumps(
            {"prev_hash": prev, "hash": digest, "event": event},
            ensure_ascii=False, sort_keys=True))
        prev = digest
    (out / "audit.jsonl").write_text(
        "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    for name, src in (artifacts or {}).items():
        shutil.copy2(Path(src), out / name)

    entries = []
    for path in sorted(out.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            entries.append({"path": str(path.relative_to(out)),
                            "sha256": sha256_file(path),
                            "bytes": path.stat().st_size})
    manifest = {
        "schema": "wth-bundle/1",
        "files": entries,
        "root_hash": sha256_bytes(
            json.dumps(entries, sort_keys=True).encode("utf-8")),
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    (out / "root_hash.txt").write_text(manifest["root_hash"] + "\n",
                                       encoding="utf-8")
    return manifest


def verify_bundle(path: Path) -> tuple[bool, list[str]]:
    """True bila semua hash cocok. Mengembalikan daftar masalah (kosong = OK)."""
    root = Path(path)
    problems: list[str] = []
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        return False, ["manifest.json tidak ada"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return False, [f"manifest.json tidak bisa dibaca: {exc}"]

    files = manifest.get("files", [])
    if not isinstance(files, list):
        return False, ["manifest.json: 'files' bukan list"]

    listed: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict):
            problems.append("entri manifest bukan objek")
            continue
        rel = entry.get("path")
        expected_sha = entry.get("sha256")
        if not isinstance(rel, str) or not rel:
            problems.append("entri manifest tanpa path")
            continue
        listed.add(rel)
        if not isinstance(expected_sha, str) or not expected_sha:
            problems.append(f"entri manifest tanpa sha256: {rel}")
            continue
        target = root / rel
        if not target.is_file():
            problems.append(f"hilang: {rel}")
            continue
        if sha256_file(target) != expected_sha:
            problems.append(f"berubah: {rel}")

    # File tambahan tidak tercatat di manifest. Tanpa pemeriksaan ini siapa pun
    # bisa menaruh `findings.json` palsu atau artefak lain di bundel dan
    # `verify_bundle` tetap bilang OK.
    #
    # `root_hash.txt` memang di luar manifest: isinya diturunkan dari daftar
    # `files`, jadi memasukkannya ke daftar itu menciptakan siklus hash. Ia
    # dicek terpisah di bawah.
    unlisted_ok = {"manifest.json", "root_hash.txt"}
    for present in sorted(root.rglob("*")):
        if not present.is_file() or present.is_symlink():
            continue
        rel = present.relative_to(root).as_posix()
        if rel in unlisted_ok or rel in listed:
            continue
        problems.append(f"tidak tercatat: {rel}")

    expected = manifest.get("root_hash", "")
    recomputed = sha256_bytes(
        json.dumps(files, sort_keys=True).encode("utf-8"))
    if recomputed != expected:
        problems.append("root_hash manifest tidak konsisten")
    root_file = root / "root_hash.txt"
    if root_file.is_file() and root_file.read_text().strip() != expected:
        problems.append("root_hash.txt tidak cocok dengan manifest")
    return (not problems), problems
"""Evidence vault: tulis atomik, dan indeks korup tidak menghapus data diam-diam.

Item 7: `_save()` memakai `write_text` (proses mati di tengah tulis meninggalkan
JSON terpotong), dan `_load()` mengembalikan `[]` saat indeks rusak -- lalu
`store()` berikutnya menulis indeks baru berisi satu item, membuat file
`EV-*.json` yang sudah ada jadi yatim.
"""

from __future__ import annotations

import json

from wth_ai.evidence.vault import EvidenceVault

SESSION = "ENG-test"

def _store_one(vault: EvidenceVault, finding: str) -> str:
    return vault.store(
        finding_id=finding,
        request={"method": "GET", "url": "https://example.com/x"},
        response={"status": 200, "body": "ok"},
    )

def test_corrupt_index_recovers_existing_items(tmp_path):
    """Indeks rusak: item dievakuasi dari file, bukan dibuang."""
    vault = EvidenceVault(SESSION, base_dir=tmp_path)
    first = _store_one(vault, "F-1")
    second = _store_one(vault, "F-2")
    del first, second

    vault.index_path.write_text("{ this is not json", encoding="utf-8")

    reopened = EvidenceVault(SESSION, base_dir=tmp_path)
    assert reopened.count() == 2, "item dari file EV-*.json harus dipulihkan"
    assert (vault.dir / "index.json.corrupt").exists(), "indeks rusak harus dikarantina"

def test_store_after_corruption_keeps_old_entries(tmp_path):
    """store() sesudah pemulihan tidak menghilangkan entry lama."""
    vault = EvidenceVault(SESSION, base_dir=tmp_path)
    _store_one(vault, "F-1")
    _store_one(vault, "F-2")
    vault.index_path.write_text("[] trailing garbage", encoding="utf-8")

    reopened = EvidenceVault(SESSION, base_dir=tmp_path)
    _store_one(reopened, "F-3")

    on_disk = json.loads(reopened.index_path.read_text(encoding="utf-8"))
    findings = {e["finding_id"] for e in on_disk}
    assert findings == {"F-1", "F-2", "F-3"}
    assert len(list(reopened.dir.glob("EV-*.json"))) == 3

def test_index_write_leaves_no_tmp_file(tmp_path):
    """Tulis atomik: tidak ada sisa `.tmp` setelah save."""
    vault = EvidenceVault(SESSION, base_dir=tmp_path)
    _store_one(vault, "F-1")
    assert not list(vault.dir.glob("*.tmp")), "atomic_write_text harus membersihkan tmp"

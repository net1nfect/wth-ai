"""Generator payload (AI mengarang sendiri) + autopilot berkala lewat cron.

Dua hal yang diuji, dan keduanya menyangkut janji ke operator:

1. **AI BOLEH mengarang payload apa pun**, termasuk yang destruktif dan yang berdampak
   massal — tetapi yang massal langsung dipaksa `auto_runnable=False` (tersimpan sebagai
   dokumentasi, tidak pernah dijalankan mesin), dan yang destruktif tetap menunggu opt-in
   saat eksekusi. Payload tanpa pernyataan bukti/pemulihan DITOLAK dengan alasan.
2. **Cron autopilot tidak bisa membuka gerbang destruktif sendiri**: flag-nya dibaca dari
   scope tersimpan saat job berjalan, bukan dari metadata job.
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.hunt.generator import (
    format_generation,
    generate_with_llm,
    load_generated,
    save_generated,
    validate_generated,
)
from wth_ai.hunt.payloads import filter_payloads, get_payload
from wth_ai.cron.store import CronStore
from wth_ai.cron.scheduler import CronScheduler


def _valid(**overrides):
    data = {
        "vuln_class": "business_logic",
        "title": "Diskon bertumpuk pada kode promo",
        "proves": "Dua kode promo dapat ditumpuk melewati batas diskon",
        "probe": {"method": "POST", "url": "{base}/api/promo/apply", "body": '{"code":"A","code":"B"}'},
        "blast_radius": "self",
        "reversible": True,
        "cleanup": "Batalkan keranjang uji",
        "requires_destructive": True,
    }
    data.update(overrides)
    return data


# --------------------------------------------------------------------------- #
# validasi: apa yang diterima, apa yang ditolak, dan kenapa
# --------------------------------------------------------------------------- #
def test_valid_generated_payload_is_accepted():
    payload, error = validate_generated(_valid())
    assert payload is not None, error
    assert payload.id.startswith("GEN-")
    assert payload.requires_destructive is True
    assert "digenerate AI" in payload.notes


def test_payload_without_proves_is_rejected():
    payload, error = validate_generated(_valid(proves=""))
    assert payload is None
    assert "proves" in error


def test_destructive_payload_without_cleanup_is_rejected():
    payload, error = validate_generated(_valid(cleanup=""))
    assert payload is None
    assert "cleanup" in error


def test_unknown_class_and_invalid_blast_are_rejected():
    assert validate_generated(_valid(vuln_class="kelas-karangan"))[0] is None
    assert validate_generated(_valid(blast_radius="galaksi"))[0] is None


def test_probe_without_url_is_rejected():
    payload, error = validate_generated(_valid(probe={"method": "GET"}))
    assert payload is None and "url" in error


def test_ai_may_write_a_mass_payload_but_the_machine_never_runs_it():
    """Inti permintaan operator: AI tetap bisa mengarang yang destruktif."""
    payload, error = validate_generated(_valid(
        title="Kosongkan seluruh katalog", blast_radius="mass", reversible=False,
        cleanup="Tidak dapat dipulihkan", requires_destructive=True,
        bounded_alternative="Uji satu item milik sendiri",
    ))
    assert payload is not None, error
    assert payload.blast_radius == "mass"
    assert payload.auto_runnable is False, "payload massal tidak boleh bisa dijalankan mesin"


def test_mass_payload_without_a_bounded_alternative_gets_an_explicit_note():
    payload, _error = validate_generated(_valid(blast_radius="mass", title="Hapus semua"))
    assert payload is not None
    assert "padanan berbatas" in payload.bounded_alternative.lower()


# --------------------------------------------------------------------------- #
# penyimpanan + ikut terbaca mesin
# --------------------------------------------------------------------------- #
def test_generated_payloads_persist_and_are_read_by_the_machine(tmp_path):
    path = tmp_path / "generated.json"
    payload, _ = validate_generated(_valid(title="Uji promo bertumpuk unik"))
    assert payload is not None
    assert save_generated([payload], path) is not None

    reloaded = load_generated(path)
    assert [p.id for p in reloaded] == [payload.id]
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["payloads"][0]["title"] == "Uji promo bertumpuk unik"


def test_saving_twice_merges_and_does_not_drop_earlier_payloads(tmp_path):
    path = tmp_path / "generated.json"
    first, _ = validate_generated(_valid(title="Pertama"))
    second, _ = validate_generated(_valid(title="Kedua", vuln_class="race"))
    assert first and second
    save_generated([first], path)
    save_generated([second], path)
    ids = {p.id for p in load_generated(path)}
    assert first.id in ids and second.id in ids


def test_corrupt_store_degrades_to_empty_not_crash(tmp_path):
    path = tmp_path / "generated.json"
    path.write_text("{ bukan json", encoding="utf-8")
    assert load_generated(path) == []


def test_generated_payloads_join_the_live_catalog(tmp_path, monkeypatch):
    """Payload karangan AI harus benar-benar dipakai, bukan cuma disimpan."""
    import wth_ai.hunt.generator as generator

    path = tmp_path / "generated.json"
    runnable, _ = validate_generated(_valid(title="Uji khusus engagement ini"))
    mass, _ = validate_generated(_valid(title="Hapus semua data", blast_radius="mass"))
    assert runnable and mass
    save_generated([runnable, mass], path)
    monkeypatch.setattr(generator, "generated_path", lambda: path)

    found = get_payload(runnable.id)
    assert found is not None and found.title == "Uji khusus engagement ini"
    listed = filter_payloads(None, include_destructive=True)
    ids = {p.id for p in listed}
    assert runnable.id in ids
    assert mass.id not in ids, "payload massal tidak boleh masuk daftar yang bisa dijalankan"


# --------------------------------------------------------------------------- #
# generate dengan LLM (router distub)
# --------------------------------------------------------------------------- #
class _Router:
    def __init__(self, text: str) -> None:
        self.text = text

    def chat_for_role(self, role, messages, **kwargs):
        return self.text, "stub-model"


class _BrokenRouter:
    def chat_for_role(self, *a, **k):
        raise RuntimeError("provider mati")


def test_llm_generation_parses_valid_payloads_and_reports_rejections():
    text = """Berikut payloadnya:
```json
[
  {"vuln_class":"race","title":"Kupon paralel","proves":"kupon terpakai ganda",
   "probe":{"method":"POST","url":"{base}/redeem"},"blast_radius":"single-object",
   "cleanup":"catat kode kupon"},
  {"vuln_class":"idor_write","title":"Hapus objek lain","proves":"tanpa cek kepemilikan",
   "probe":{"method":"DELETE","url":"{base}/items/2"},"blast_radius":"single-object"}
]
```"""
    result = generate_with_llm(_Router(text), vuln_class="race", goal="uji kupon")
    assert len(result.accepted) == 1, result.to_dict()
    assert len(result.rejected) == 1
    assert "cleanup" in result.rejected[0][1]


def test_llm_generation_handles_garbage_and_provider_failure():
    garbage = generate_with_llm(_Router("maaf saya tidak bisa"), vuln_class="race", goal="x")
    assert garbage.accepted == [] and garbage.rejected
    dead = generate_with_llm(_BrokenRouter(), vuln_class="race", goal="x")
    assert dead.accepted == []
    assert "LLM gagal" in dead.rejected[0][1]


def test_generation_report_shows_rejections_not_just_successes():
    result = generate_with_llm(_Router('[{"title":"tanpa proves","probe":{"url":"x"}}]'),
                               vuln_class="race", goal="x")
    text = format_generation(result)
    assert "DITOLAK" in text


# --------------------------------------------------------------------------- #
# cron autopilot
# --------------------------------------------------------------------------- #
class _Job:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id


class _StubManager:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create_job(self, job_type, **kwargs):
        self.calls.append({"job_type": job_type, **kwargs})
        return _Job(f"JOB-{len(self.calls)}")


def test_cron_registers_an_autopilot_entry(tmp_path):
    store = CronStore(tmp_path / "cron.json")
    entry = store.add(target="example.com", schedule="every 6h",
                      job_type="autopilot", profile="recon-only")
    assert entry.job_type == "autopilot"
    assert any(e.job_type == "autopilot" for e in store.list())


def test_scheduler_enqueues_an_autopilot_job_with_authorization(tmp_path):
    store = CronStore(tmp_path / "cron.json")
    store.add(target="example.com", schedule="every 6h", job_type="autopilot")
    manager = _StubManager()
    scheduler = CronScheduler(manager, store=store)
    fired = scheduler.tick()
    assert fired, "cron autopilot harus memicu job"
    call = manager.calls[0]
    assert call["job_type"] == "autopilot"
    assert call["target"] == "example.com"
    assert call["metadata"]["authorized"] is True, "handler fail-closed butuh flag ini"


def test_autopilot_handler_is_registered_and_fails_closed():
    from wth_ai.jobs.handlers import default_handlers, handler_autopilot

    assert default_handlers()["autopilot"] is handler_autopilot

    class _Job:
        target = "example.com"
        metadata: dict = {}
        job_id = "J1"

    class _Ctx:
        def progress(self, *a): pass
        def log(self, *a): pass
        def should_stop(self): return False

    result = handler_autopilot(_Job(), _Ctx())
    assert result["status"] == "blocked", result
    assert "authorized" in result["reason"]


def test_autopilot_handler_reads_the_destructive_flag_from_the_saved_scope():
    """Cron tidak boleh membuka gate destruktif lewat metadata job."""
    import inspect

    from wth_ai.jobs.handlers import handler_autopilot

    source = inspect.getsource(handler_autopilot)
    assert "SessionStore" in source and "destructive_allowed" in source
    assert 'meta.get("destructive' not in source and "metadata.get(\"destructive" not in source

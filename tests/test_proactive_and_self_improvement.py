"""Mesin proaktif + loop self-improvement yang bertindak + penulis skill otomatis.

Tiga properti yang diuji, karena ini yang membedakan "fitur" dari "klaim":
1. saran langkah berikutnya punya ALASAN yang bisa ditelusuri dan prioritas yang
   benar-benar dipengaruhi hasil pengukuran (loop tertutup, bukan hiasan);
2. pengukuran yang tidak punya cukup sampel TIDAK mengubah apa pun, dan itu
   dinyatakan -- bukan diam-diam dianggap "sudah diukur";
3. skill otomatis TIDAK PERNAH ditulis tanpa rujukan bukti, tidak menimpa skill
   manusia, dan rahasia tidak pernah masuk ke dalamnya.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.eval.act import act_on_report, backfill_failures, compute_weights, format_actions
from wth_ai.hunt.proactive import (
    DEFAULT_WEIGHTS,
    NextTest,
    format_next_tests,
    load_weights,
    propose_next_tests,
)
from wth_ai.skills.autowrite import SkillNote, note_from_finding, record_skill


# --------------------------------------------------------------------------- #
# mesin proaktif
# --------------------------------------------------------------------------- #
def test_idor_finding_proposes_neighbour_objects_with_a_reason():
    finding = {"id": "F1", "vuln_class": "idor", "target": "http://t/api/user?id=1",
               "transcript_id": "TXabc"}
    tests = propose_next_tests(findings=[finding])
    assert tests, "temuan IDOR harus menghasilkan usulan"
    first = tests[0]
    assert "tetangga" in first.title.lower() or "neighbour" in first.title.lower()
    assert first.probe["compare_to"] == "TXabc", "usulan harus menunjuk bukti asalnya"
    assert first.probe["url"] != finding["target"], "URL harus benar-benar diubah"
    assert first.rationale and "IDOR" in first.rationale
    assert first.priority > 0


def test_ssrf_finding_proposes_host_variants():
    finding = {"id": "F2", "vuln_class": "ssrf", "target": "http://t/fetch?url=http://127.0.0.1/x"}
    tests = propose_next_tests(findings=[finding])
    urls = " ".join(t.probe.get("url", "") for t in tests)
    assert "localhost" in urls or "::1" in urls or "0.0.0.0" in urls, urls
    assert all(t.rationale for t in tests)


def test_auth_finding_proposes_default_creds_on_the_confirmed_user():
    finding = {"id": "F3", "vuln_class": "auth", "target": "http://t/login?user=admin&pw=x"}
    titles = " ".join(t.title.lower() for t in propose_next_tests(findings=[finding]))
    assert "kredensial default" in titles
    assert "reset" in titles


def test_signals_alone_produce_proposals_even_without_findings():
    """Adaptif: sinyal respons (403, 429) cukup untuk mengusulkan langkah lanjutan."""
    pairs = [
        {"url": "http://t/admin", "response_status": 403, "body_snippet": "forbidden", "transcript_id": "TX1"},
        {"url": "http://t/api", "response_status": 429, "body_snippet": "too many requests"},
    ]
    tests = propose_next_tests(findings=[], request_pairs=pairs)
    titles = " ".join(t.title.lower() for t in tests)
    assert "menolak" in titles, titles
    assert "rate-limit" in titles
    bypass = next(t for t in tests if "menolak" in t.title.lower())
    assert bypass.probe["compare_to"] == "TX1"


def test_no_data_means_no_invented_proposals():
    assert propose_next_tests() == []
    assert "Belum ada usulan" in format_next_tests([])


def test_proposals_are_deduplicated_and_limited():
    findings = [
        {"id": f"F{i}", "vuln_class": "idor", "target": "http://t/api/user?id=1"} for i in range(5)
    ]
    tests = propose_next_tests(findings=findings, limit=3)
    assert len(tests) == 3
    keys = [(t.probe.get("method"), t.probe.get("url")) for t in tests]
    assert len(keys) == len(set(keys)), "usulan duplikat harus dibuang"


def test_weights_actually_change_priority():
    """Loop tertutup: bobot hasil pengukuran harus mengubah urutan kerja."""
    finding = {"id": "F", "vuln_class": "idor", "target": "http://t/api/user?id=1"}
    low = propose_next_tests(findings=[finding], weights={"idor": 0.5})[0]
    high = propose_next_tests(findings=[finding], weights={"idor": 2.0})[0]
    assert high.priority > low.priority


def test_format_shows_reason_and_probe():
    tests = propose_next_tests(findings=[{"id": "F", "vuln_class": "idor",
                                          "target": "http://t/api/user?id=1"}])
    text = format_next_tests(tests)
    assert "alasan:" in text and "probe :" in text


# --------------------------------------------------------------------------- #
# self-improvement yang bertindak
# --------------------------------------------------------------------------- #
def test_too_few_samples_do_not_move_the_weights():
    weights, notes = compute_weights({"idor": {"verified": 1, "false_positive": 0}})
    assert weights["idor"] == DEFAULT_WEIGHTS["idor"], "1 sampel bukan dasar mengubah bobot"
    assert any("sampel" in note for note in notes)


def test_precision_drives_the_weight():
    good, _ = compute_weights({"idor": {"verified": 10, "false_positive": 0}})
    bad, _ = compute_weights({"idor": {"verified": 0, "false_positive": 10}})
    assert good["idor"] > DEFAULT_WEIGHTS["idor"] > bad["idor"]
    assert bad["idor"] >= 0.5 and good["idor"] <= 2.0


def test_act_writes_weights_that_the_proactive_engine_reads(tmp_path):
    weights_path = tmp_path / "weights.json"
    actions = act_on_report(
        None,
        learning=_FakeLearning({"idor": {"verified": 8, "false_positive": 0}}),
        weights_path=weights_path,
    )
    assert weights_path.exists(), "aksi harus menulis file, bukan hanya melapor"
    written = json.loads(weights_path.read_text(encoding="utf-8"))
    assert written["weights"]["idor"] > 1.0
    # loop tertutup: pembaca bobot memakai angka yang baru ditulis
    assert load_weights(weights_path)["idor"] == written["weights"]["idor"]
    assert any(a.applied for a in actions)


def test_act_says_so_when_there_is_not_enough_data(tmp_path):
    actions = act_on_report(None, learning=_FakeLearning({}), weights_path=tmp_path / "w.json")
    assert any("belum ada sampel" in a.detail for a in actions), format_actions(actions)
    assert not any(a.applied for a in actions)


def test_backfill_writes_false_positives_into_the_learning_store():
    """Menutup cacat dua arah: tanpa ini presisi hanya menghitung kemenangan."""
    learning = _FakeLearning({})
    db = _FakeFindingsDB([{"state": "false_positive", "vuln_class": "idor"},
                          {"state": "verified", "vuln_class": "idor"}])
    action = backfill_failures(learning, db)
    assert action.applied
    assert learning.recorded == [("idor", False)], "hanya false positive yang ditulis"


# --------------------------------------------------------------------------- #
# penulis skill otomatis
# --------------------------------------------------------------------------- #
class _FakeLearning:
    def __init__(self, counts: dict[str, dict[str, int]]) -> None:
        self.counts = counts
        self.recorded: list[tuple[str, bool]] = []

    def all_records(self):
        rows = []
        for vuln_class, slot in self.counts.items():
            rows += [{"vuln_class": vuln_class}] * int(slot.get("verified", 0))
            rows += [{"vuln_class": vuln_class, "failure": True}] * int(slot.get("false_positive", 0))
        return rows

    def record_from_finding(self, finding, *, verified: bool):
        self.recorded.append((str(finding.get("vuln_class", "")), verified))


class _FakeFindingsDB:
    def __init__(self, items: list[dict]) -> None:
        self.items = [_Row(item) for item in items]

    def list_findings(self, state=None):
        return self.items


class _Row:
    def __init__(self, data: dict) -> None:
        self.data = data

    def get(self, key, default=None):
        return self.data.get(key, default)

    def __getattr__(self, item):
        return self.data.get(item)


def test_skill_is_not_written_without_evidence(tmp_path):
    """Properti paling penting: pengetahuan tanpa bukti tidak boleh masuk."""
    note = SkillNote(technique="IDOR lewat parameter id", vuln_class="idor",
                     evidence=["kayaknya bisa kok"])
    assert record_skill(note, root=tmp_path / "skills") is None
    assert not (tmp_path / "skills").exists(), "tidak boleh ada file sama sekali"


def test_skill_is_written_from_real_evidence(tmp_path):
    note = SkillNote(technique="IDOR lewat parameter id", vuln_class="idor",
                     tech_stack="nextjs", evidence=["[TX:abc123]", "[EV:ev-1]"],
                     pitfalls=["hanya berlaku untuk endpoint tanpa auth"])
    path = record_skill(note, root=tmp_path / "skills")
    assert path is not None and path.exists()
    body = path.read_text(encoding="utf-8")
    assert "auto: true" in body
    assert "[TX:abc123]" in body and "[EV:ev-1]" in body
    assert "occurrences: 1" in body
    assert "hanya berlaku untuk endpoint tanpa auth" in body
    assert "bukan klaim keberhasilan" in body


def test_second_observation_updates_instead_of_duplicating(tmp_path):
    root = tmp_path / "skills"
    note = SkillNote(technique="IDOR lewat parameter id", vuln_class="idor", evidence=["[TX:one]"])
    first = record_skill(note, root=root)
    again = SkillNote(technique="IDOR lewat parameter id", vuln_class="idor", evidence=["[TX:two]"])
    second = record_skill(again, root=root)
    assert first == second, "update harus menulis ke file yang sama"
    body = second.read_text(encoding="utf-8")
    assert "occurrences: 2" in body
    assert "[TX:one]" in body and "[TX:two]" in body, "bukti lama tidak boleh hilang"
    assert "Riwayat konfirmasi" in body


def test_human_skill_with_the_same_slug_is_never_overwritten(tmp_path):
    root = tmp_path / "skills"
    note = SkillNote(technique="IDOR lewat parameter id", vuln_class="idor", evidence=["[TX:abc]"])
    path = record_skill(note, root=root)
    # ganti isi jadi "milik manusia" (tanpa auto: true) dan pastikan tidak ditimpa
    path.write_text("---\nname: skill manusia\ndescription: manual\n---\n\n# Manual\n", encoding="utf-8")
    record_skill(note, root=root)
    assert "skill manusia" in path.read_text(encoding="utf-8"), "skill manusia tertimpa!"


def test_secrets_never_reach_the_skill_file(tmp_path):
    note = SkillNote(technique="Auth bypass", vuln_class="auth",
                     evidence=["[TX:abc] token=sk-01088b12deadbeefcafe5c4d"],
                     pitfalls=["cookie session=supersecretvalue123 dipakai"])
    path = record_skill(note, root=tmp_path / "skills")
    body = path.read_text(encoding="utf-8")
    assert "sk-01088b12deadbeefcafe5c4d" not in body
    assert "supersecretvalue123" not in body


def test_note_from_finding_pulls_evidence_and_class():
    finding = {"title": "IDOR pada /api/user", "vuln_class": "idor", "tech_stack": "nextjs",
               "evidence": ["[TX:zzz]"], "transcript_id": "TXother"}
    note = note_from_finding(finding)
    assert note.vuln_class == "idor" and note.tech_stack == "nextjs"
    assert "[TX:zzz]" in note.evidence and "[TX:TXother]" in note.evidence


# --------------------------------------------------------------------------- #
# wiring: hanya temuan TERVERIFIKASI yang memicu keduanya
# --------------------------------------------------------------------------- #
class _State:
    def __init__(self):
        self.metadata: dict = {}
        self.session_id = "s1"


class _FakeOrch:
    """Objek minimal: kedua helper hanya butuh state + findings_db."""

    def __init__(self):
        from wth_ai.orchestrator import WTHOrchestrator

        self.state = _State()
        self.findings_db = _FakeFindingsDB([{"id": "F1", "vuln_class": "idor",
                                             "target": "http://t/api/user?id=1",
                                             "transcript_id": "TX1", "state": "verified"}])
        self._propose = WTHOrchestrator._propose_next_after_finding.__get__(self)
        self._skill = WTHOrchestrator._write_skill_from_finding.__get__(self)


def test_proactive_helper_stores_proposals_in_state(tmp_path, monkeypatch):
    import wth_ai.skills.autowrite as autowrite

    monkeypatch.setattr(autowrite, "auto_skills_root", lambda: tmp_path / "skills")
    orch = _FakeOrch()
    text = orch._propose({"id": "F1", "vuln_class": "idor", "target": "http://t/api/user?id=1",
                          "transcript_id": "TX1"})
    assert "Langkah uji berikutnya" in text
    assert orch.state.metadata["next_tests"], "usulan harus tersimpan untuk /next"
    path = orch._skill({"id": "F1", "vuln_class": "idor", "target": "http://t/api/user?id=1",
                        "transcript_id": "TX1", "title": "IDOR /api/user"})
    assert path, "temuan berbukti harus menghasilkan skill"
    assert Path(path).exists()


def test_registry_discovers_skills_written_one_namespace_deep(tmp_path):
    """Bug integrasi yang ketahuan dari uji end-to-end: penulis skill menaruh file di
    `skills/auto/<slug>/SKILL.md`, sedangkan registry hanya memindai SATU level --
    akibatnya skill tertulis di disk tapi tidak pernah terbaca (117 skill lama, nol auto)."""
    from wth_ai.skills.registry import SkillRegistry

    root = tmp_path / "skills"
    nested = root / "auto" / "auto-idor-contoh"
    nested.mkdir(parents=True)
    (nested / "SKILL.md").write_text(
        "---\nname: contoh auto\ndescription: Use when testing idor. auto.\n---\n\n# x\n",
        encoding="utf-8",
    )
    plain = root / "langsung"
    plain.mkdir()
    (plain / "SKILL.md").write_text(
        "---\nname: langsung\ndescription: Use when something. ok.\n---\n\n# y\n",
        encoding="utf-8",
    )

    registry = SkillRegistry(openclaw_path=tmp_path / "tidak-ada", local_path=root)
    found = registry.discover()
    assert found == 2, f"harus menemukan skill langsung dan yang di namespace: {found}"
    assert any(s.id.startswith("auto-") for s in registry.skills.values())

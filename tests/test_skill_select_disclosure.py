"""Skill selector deterministik (tanpa model) dan advisory pengungkapan."""

from dataclasses import dataclass, field
from datetime import date

from wth_ai.redteam.disclosure import DISCLOSURE_DAYS, build_advisory
from wth_ai.skills.select import LIMIT, score_skill, select_skills


@dataclass
class _Skill:
    id: str
    description: str = ""


@dataclass
class _Finding:
    id: str = "F-001"
    hypothesis: str = "BOLA pada /api/v1/orders"
    state: str = "confirmed"
    severity: str = "high"
    vuln_class: str = "access_control"
    target: str = "172.28.0.10"
    created_at: str = "2026-11-04"
    attestation: object = "arasaka"
    evidence: list[str] = field(default_factory=lambda: ["evidence/req.txt"])


SKILLS = [
    _Skill("bola-api-testing", "Uji BOLA/IDOR pada REST API dan otorisasi per objek"),
    _Skill("sql-injection", "Teknik SQL injection terbatas untuk CTF"),
    _Skill("wireless-ctf", "WiFi WPA handshake capture untuk CTF"),
]


def test_relevant_skill_is_selected():
    picked = select_skills("tolong uji bola pada api orders", SKILLS)
    assert picked and picked[0] == "bola-api-testing"


def test_unrelated_skill_is_not_selected():
    picked = select_skills("tolong uji bola pada api orders", SKILLS)
    assert "wireless-ctf" not in picked


def test_selection_is_capped_and_stable():
    picked = select_skills("api rest otorisasi objek bola ctf", SKILLS)
    assert len(picked) <= LIMIT
    assert picked == select_skills("api rest otorisasi objek bola ctf", SKILLS)


def test_empty_input_selects_nothing():
    assert select_skills("", SKILLS) == []


def test_score_ignores_stopwords():
    assert score_skill({"yang", "dan"}, "x", "yang dan") == 0


def test_advisory_has_sections_and_timeline():
    text = build_advisory(_Finding(), today=date(2026, 11, 4))
    for heading in ("# BOLA", "## Langkah reproduksi", "## Dampak",
                    "## Remidiasi", "## Bukti", "## Timeline"):
        assert heading in text
    assert "2027-02-02" in text  # 2026-11-04 + 90 hari


def test_advisory_keeps_evidence_reference_not_copy():
    text = build_advisory(_Finding())
    assert "wth verify" in text
    assert "evidence/req.txt" in text


def test_advisory_includes_contact_only_when_given():
    assert "Kontak peneliti" not in build_advisory(_Finding())
    assert "a@example.test" in build_advisory(_Finding(), contact="a@example.test")


def test_advisory_renders_enums_and_attestation_dicts_readably():
    from enum import Enum

    class State(str, Enum):
        CONFIRMED = "confirmed"

    finding = _Finding()
    finding.state = State.CONFIRMED
    finding.attestation = {"level": "confirmed",
                           "label": "terkonfirmasi (bukti lengkap)"}
    text = build_advisory(finding)
    assert "AttemptState" not in text
    assert "State.CONFIRMED" not in text
    assert "**Status:** confirmed" in text
    assert "terkonfirmasi (bukti lengkap)" in text
    assert "{'level'" not in text


def test_disclosure_window_is_industry_default():
    assert DISCLOSURE_DAYS == 90
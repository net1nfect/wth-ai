"""Tests for the veracity layer — evidence discipline and overclaim enforcement.

These exist because the previous arrangement stated the rules in
``prompts/chat_system.txt`` ("bedakan hipotesis vs bukti", "jangan mengarang
CVE/finding") and enforced them nowhere.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai import veracity


# --------------------------------------------------------------------------- #
# assess_claim — grade from evidence, not from wording
# --------------------------------------------------------------------------- #
def test_confirmed_requires_evidence_reproduction_and_control():
    a = veracity.assess_claim(has_request_response=True, reproductions=1, controls=1)
    assert a.level == veracity.LEVEL_CONFIRMED
    assert a.blockers == []


def test_missing_control_blocks_confirmation():
    a = veracity.assess_claim(has_request_response=True, reproductions=2, controls=0)
    assert a.level == veracity.LEVEL_CANDIDATE
    assert any("kontrol" in b for b in a.blockers)


def test_missing_reproduction_blocks_confirmation():
    a = veracity.assess_claim(has_request_response=True, reproductions=0, controls=1)
    assert a.level == veracity.LEVEL_CANDIDATE
    assert any("direproduksi" in b for b in a.blockers)


def test_no_evidence_at_all_is_unproven_not_a_soft_candidate():
    a = veracity.assess_claim(has_request_response=False)
    assert a.level == veracity.LEVEL_UNPROVEN


def test_negative_control_refutes_the_claim():
    a = veracity.assess_claim(
        has_request_response=True, reproductions=1, controls=1, refuted_by_control=True
    )
    assert a.level == veracity.LEVEL_REFUTED
    assert a.label.startswith("terbantah")


def test_every_level_has_an_operator_label():
    for level in (veracity.LEVEL_CONFIRMED, veracity.LEVEL_CANDIDATE,
                  veracity.LEVEL_UNPROVEN, veracity.LEVEL_REFUTED):
        assert veracity.LEVEL_LABEL[level]


# --------------------------------------------------------------------------- #
# lint_claims — absolutist language without adjacent evidence
# --------------------------------------------------------------------------- #
def test_lint_flags_unbacked_absolute_claim():
    violations = veracity.lint_claims("Endpoint /api/mcp confirmed vulnerable to SSRF.")
    assert violations
    assert violations[0].line_no == 1


def test_lint_accepts_claim_carrying_evidence():
    text = "Endpoint /api/mcp confirmed vulnerable to SSRF.\nBukti: evidence/mcp_ssrf.json\nResponse HTTP 200"
    assert veracity.lint_claims(text) == []


def test_lint_accepts_hedged_language():
    for text in (
        "Ini masih hipotesis, belum ada bukti request/response.",
        "Belum terbukti; butuh reproduksi dan kontrol pembanding.",
        "Kandidat temuan (belum diverifikasi).",
    ):
        assert veracity.lint_claims(text) == [], text


def test_lint_is_not_confused_by_ordinary_paths():
    """`config/wth_config.yaml` is hostname-shaped; a naive scanner flagged it."""
    assert veracity.lint_claims("Baca config/wth_config.yaml lalu jalankan test.") == []


def test_lint_reports_line_numbers_and_phrase():
    text = "baris satu biasa\nEndpoint ini definitely rentan\nbaris tiga biasa"
    violations = veracity.lint_claims(text)
    assert [v.line_no for v in violations] == [2]
    assert "definitely" in violations[0].phrase.lower()


# --------------------------------------------------------------------------- #
# sanitize_claims — downgrade, and say what was downgraded
# --------------------------------------------------------------------------- #
def test_sanitize_downgrades_confirmed_vulnerable():
    text = "Endpoint /api/mcp confirmed vulnerable to SSRF."
    out, violations = veracity.sanitize_claims(text)
    assert "confirmed vulnerable" not in out
    assert "kandidat" in out and "belum diverifikasi" in out
    assert violations


def test_sanitize_downgrades_proven_exploited():
    out, violations = veracity.sanitize_claims("Payload ini terbukti dieksploitasi di production.")
    assert "terbukti dieksploitasi" not in out
    assert "belum dibuktikan" in out
    assert violations


def test_sanitize_leaves_backed_claims_untouched():
    text = "Endpoint /api/mcp confirmed vulnerable.\nBukti: evidence/x.json\nReproduksi: 2x kontrol negatif ok"
    out, violations = veracity.sanitize_claims(text)
    assert out == text
    assert violations == []


def test_sanitize_is_stable_on_second_pass():
    out, _ = veracity.sanitize_claims("Endpoint confirmed vulnerable to SSRF.")
    again, violations = veracity.sanitize_claims(out)
    assert again == out
    assert violations == []


def test_guard_note_mentions_the_requirement():
    _, violations = veracity.sanitize_claims("Sistem ini pasti kena takeover.")
    note = veracity.guard_note(violations)
    assert "veracity gate" in note
    assert "request/response" in note


# --------------------------------------------------------------------------- #
# self-description must come from live config
# --------------------------------------------------------------------------- #
def test_self_description_block_carries_the_real_model():
    block = veracity.self_description_block(
        provider="custom", model="auto", skills=42, permission="read_only"
    )
    assert "custom/auto" in block
    assert "42" in block
    assert "read_only" in block


# --------------------------------------------------------------------------- #
# ChatAgent actually applies the gate to its own reply
# --------------------------------------------------------------------------- #
def _chat_agent():
    from wth_ai.agents.chat import ChatAgent
    from wth_ai.config import WTHConfig
    from wth_ai.skills.registry import SkillRegistry

    cfg = WTHConfig.load()
    skills = SkillRegistry(
        openclaw_path=cfg.skills.openclaw_path,
        local_path=cfg.skills.local_path,
    )
    return ChatAgent(cfg, object(), skills)


def test_chat_agent_downgrades_its_own_overclaim(monkeypatch):
    from wth_ai.models import SessionState

    agent = _chat_agent()
    monkeypatch.setattr(
        agent, "_call",
        lambda *a, **k: ("Endpoint /api/mcp confirmed vulnerable to SSRF.", "stub-model"),
    )
    out = agent.respond("hasilnya gimana?", SessionState())

    assert "confirmed vulnerable" not in out["raw"]
    assert out.get("veracity_violations"), "violations tidak dilaporkan"
    assert "veracity gate" in out["raw"]


def test_chat_agent_leaves_a_backed_claim_alone(monkeypatch):
    from wth_ai.models import SessionState

    agent = _chat_agent()
    reply = "Indikasi SSRF; status masih kandidat (belum diverifikasi)."
    monkeypatch.setattr(agent, "_call", lambda *a, **k: (reply, "stub-model"))
    out = agent.respond("gimana?", SessionState())
    assert out["raw"] == reply
    assert "veracity_violations" not in out


def test_chat_agent_context_states_the_real_model_and_the_rules(monkeypatch):
    """The old context advertised Nemotron/Qwen regardless of configuration."""
    from wth_ai.models import SessionState

    agent = _chat_agent()
    captured: dict = {}

    def _fake_call(*args, **kwargs):
        captured["prompt"] = args[4] if len(args) > 4 else kwargs.get("prompt", "")
        return ("ok", "stub-model")

    monkeypatch.setattr(agent, "_call", _fake_call)
    agent.respond("pakai model apa kamu?", SessionState())

    prompt = captured["prompt"]
    assert "FAKTA WTH-AI SAAT INI" in prompt
    assert "EVIDENCE DISCIPLINE" in prompt
    assert "Nemotron" not in prompt, "nama model lama masih di-hardcode"


def test_capabilities_summary_no_longer_hardcodes_models():
    agent = _chat_agent()
    summary = agent.capabilities_summary(agent.skill_router.registry)
    assert "Nemotron" not in summary
    assert "Qwen3 Coder" not in summary
    assert "yang BENAR-BENAR aktif" in summary

# --------------------------------------------------------------------------- #
# Loosened precision: don't fight the hedging the gate itself asks for
# --------------------------------------------------------------------------- #
def test_hedged_sentence_is_not_an_overclaim():
    """A sentence that already labels itself unverified must not be downgraded
    again -- otherwise the gate punishes exactly the wording it demands."""
    for text in (
        "Status: kandidat, bukan confirmed vulnerable.",
        "Belum terbukti dieksploitasi; perlu diverifikasi.",
        "Ini baru indikasi SSRF, bukan confirmed vulnerable.",
    ):
        assert veracity.lint_claims(text) == [], text


def test_bare_mention_of_critical_is_not_a_claim():
    """A severity rubric or a quoted table is not a claim about this target."""
    for text in (
        "Rubrik severity: low, medium, high, critical.",
        "Dokumentasi platform memakai kategori critical untuk dampak tertinggi.",
    ):
        assert veracity.lint_claims(text) == [], text


def test_critical_attached_to_a_finding_is_still_flagged():
    assert veracity.lint_claims("Ini critical vulnerability di endpoint login.")
    assert veracity.lint_claims("severity: critical pada temuan ini.")


def test_zero_day_needs_a_claim_context():
    assert veracity.lint_claims("Kami menemukan 0-day exploit di target.") 
    assert veracity.lint_claims("Istilah zero-day di glosarium saja.") == []


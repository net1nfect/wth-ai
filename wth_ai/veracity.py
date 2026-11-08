"""Veracity layer — evidence discipline for every claim WTH-AI makes.

Why this module exists
----------------------
`prompts/chat_system.txt` already told the model "bedakan hipotesis vs bukti"
and "jangan mengarang CVE/finding", but nothing ENFORCED either rule: the rules
were prose the model could ignore, with no code path that noticed when it did.
Two concrete consequences seen in practice:

* `ChatAgent.capabilities_summary()` advertised "Planner Brain (Nemotron) /
  Executor Brain (Qwen Coder)" and "118+ skills" regardless of the configured
  provider -- i.e. the tool made a factual claim about itself that was simply
  false (the live config ran ``custom/auto``).
* A reply or report could say "confirmed vulnerable" / "terbukti dieksploitasi"
  with no request/response, no reproduction and no control, and nothing in the
  pipeline objected.

So veracity lives in code, in one place, and is applied to output:

1. ``assess_claim()`` grades a claim from its EVIDENCE, not its wording.
   A claim can only reach ``confirmed`` with request/response capture, at least
   one reproduction AND at least one negative control -- the same bar this
   codebase already holds bug-bounty findings to.
2. ``lint_claims()`` finds absolute language in generated text that has no
   evidence next to it.
3. ``sanitize_claims()`` downgrades exactly those violations, so an unbacked
   "confirmed" becomes an explicitly labelled candidate instead of a lie.

Deliberately conservative: only unambiguous absolutist phrasing is flagged, and
evidence within a small window (±2 lines) excuses a claim, so legitimate
prose -- "the response returned a stack trace (bukti: evidence/leak.txt)" --
is never touched. A false positive here would train the operator to ignore the
gate, which is worse than missing a subtle overclaim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

# --------------------------------------------------------------------------- #
# Claim levels — ordered by strength. Anything that weakens the claim set must
# stay strictly narrower than `confirmed`.
# --------------------------------------------------------------------------- #
LEVEL_CONFIRMED = "confirmed"
LEVEL_CANDIDATE = "candidate"
LEVEL_UNPROVEN = "unproven"
LEVEL_REFUTED = "refuted"

LEVEL_LABEL: dict[str, str] = {
    LEVEL_CONFIRMED: "terkonfirmasi (bukti lengkap)",
    LEVEL_CANDIDATE: "kandidat (belum diverifikasi)",
    LEVEL_UNPROVEN: "belum terbukti",
    LEVEL_REFUTED: "terbantah (kontrol negatif)",
}


@dataclass(frozen=True)
class ClaimAssessment:
    """Result of grading one claim against its evidence."""

    level: str
    reasons: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return LEVEL_LABEL.get(self.level, self.level)

    def to_dict(self) -> dict[str, object]:
        return {"level": self.level, "label": self.label, "reasons": list(self.reasons), "blockers": list(self.blockers)}


def assess_claim(
    *,
    has_request_response: bool,
    reproductions: int = 0,
    controls: int = 0,
    tool_backed: bool = False,
    refuted_by_control: bool = False,
    category: str | None = None,
    proof_facts: dict | None = None,
) -> ClaimAssessment:
    """Grade a claim from its evidence.

    `confirmed` requires ALL of: captured request/response, >=1 successful
    reproduction, and >=1 negative control that behaves differently. That is
    the bar this project already applies to findings, so a claim that cannot
    clear it must not be reported as if it had.
    """
    reasons: list[str] = []
    blockers: list[str] = []

    if refuted_by_control:
        return ClaimAssessment(
            LEVEL_REFUTED,
            ["kontrol negatif menunjukkan perilaku yang sama seperti klaim"],
            ["tidak boleh dilaporkan sebagai temuan"],
        )

    if category is not None or proof_facts is not None:
        from wth_ai.redteam.proof_profiles import ProofVerdict, evaluate_proof

        verdict = evaluate_proof(category, dict(proof_facts or {}))
        if verdict is not ProofVerdict.INDEPENDENTLY_VERIFIED:
            return ClaimAssessment(
                LEVEL_CANDIDATE,
                ["bukti dasar lengkap"],
                [f"proof profile {category!r} menolak ({verdict.value})"],
            )

    if has_request_response:
        reasons.append("ada bukti request/response")
    else:
        blockers.append("tidak ada bukti request/response")

    if reproductions >= 1:
        reasons.append(f"direproduksi {reproductions}x")
    else:
        blockers.append("belum direproduksi")

    if controls >= 1:
        reasons.append(f"ada {controls} kontrol pembanding")
    else:
        blockers.append("tidak ada kontrol pembanding (bisa jadi false positive)")

    if tool_backed:
        reasons.append("didukung keluaran tool")

    if not blockers:
        return ClaimAssessment(LEVEL_CONFIRMED, reasons, [])

    # No evidence at all -> honestly unproven rather than a soft "candidate".
    if not has_request_response and reproductions == 0:
        return ClaimAssessment(LEVEL_UNPROVEN, reasons, blockers)
    return ClaimAssessment(LEVEL_CANDIDATE, reasons, blockers)


# --------------------------------------------------------------------------- #
# Overclaim detection
# --------------------------------------------------------------------------- #
# Absolutist phrasing that asserts a verified outcome. Matched
# case-insensitively against a single line.
ABSOLUTE_CLAIM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("confirmed-vulnerable", re.compile(r"\b(confirmed|terkonfirmasi)\s+(vuln|vulnerab|kerentanan|bug|exploit|rce|sqli|xss|ssrf|idor)", re.I)),
    ("proven-exploitable", re.compile(r"\b(terbukti|proof|proven)\s+(exploit|dieksploitasi|exploitable|vulnerab|rentan|berhasil)", re.I)),
    ("definitely", re.compile(r"\b(pasti|definitely|certainly|sudah pasti|dipastikan)\b", re.I)),
    ("absolute-percentage", re.compile(r"\b(100\s*%|seratus persen)\b", re.I)),
    # Requires claim CONTEXT. A bare mention of the word "critical" (a severity
    # table, a rubric, a quote of someone else's report) is not a claim, and
    # flagging it trained the operator to ignore the gate.
    ("critical-severity", re.compile(
        r"(severity\s*[:=]\s*critical|\bcritical\s+(vuln\w*|severity|bug|rce|exploit|impact))", re.I)),
    ("zero-day", re.compile(r"\b(0-?day|zero-?day)\s+(vuln\w*|exploit\w*|bug|kerentanan)", re.I)),
    ("full-compromise", re.compile(r"\b(fully compromised|takeover penuh|kompromi penuh|total takeover)\b", re.I)),
    ("guaranteed", re.compile(r"\b(dijamin|guaranteed|tanpa keraguan|undeniably)\b", re.I)),
)

# A sentence that already marks itself as unverified is NOT an overclaim, even
# if it contains a strong word ("status kandidat, bukan confirmed vulnerable").
# Without this exemption the gate fought the very hedging it asks for.
HEDGE_WORDS = re.compile(
    r"\b(kandidat|candidate|belum|unverified|unproven|hipotesis|hypothesis|dugaan|"
    r"mungkin|possibly|indikasi|indication|perlu diverifikasi|to be verified)\b",
    re.I,
)

# Anything here, on the same line or within EVIDENCE_WINDOW lines, counts as
# backing for an absolute claim.
#
# Markers must be CITATION-shaped (``bukti:``, ``evidence/``, an HTTP status, a
# curl command), not merely topic words. An earlier version also accepted bare
# `payload`/`screenshot`/`bukti`, and the sentence
# "Payload ini terbukti dieksploitasi di production." therefore waived its own
# gate -- the claim mentioned a topic word that the gate treated as evidence.
_EVIDENCE_MARKERS = re.compile(
    r"(evidence/|bukti\s*:|request\s*:|response\s*:|req/resp|reproduk|kontrol|control\b|"
    r"HTTP\s*\d{3}|status\s*[:=]\s*\d{3}|curl\s|\[TX:|negative control|kontrol negatif)",
    re.I,
)
EVIDENCE_WINDOW = 2


@dataclass(frozen=True)
class Violation:
    """One absolute claim found without adjacent evidence."""

    rule: str
    phrase: str
    line_no: int
    line: str

    def describe(self) -> str:
        return f"baris {self.line_no}: {self.phrase!r} ({self.rule}) tanpa bukti di sekitarnya"


def lint_claims(text: str) -> list[Violation]:
    """Find absolute claims that nothing nearby backs up."""
    if not text:
        return []
    lines = text.splitlines()
    violations: list[Violation] = []
    for idx, line in enumerate(lines):
        lo = max(0, idx - EVIDENCE_WINDOW)
        hi = min(len(lines), idx + EVIDENCE_WINDOW + 1)
        window = "\n".join(lines[lo:hi])
        if _EVIDENCE_MARKERS.search(window):
            continue
        if HEDGE_WORDS.search(line):
            continue
        for rule, pattern in ABSOLUTE_CLAIM_PATTERNS:
            match = pattern.search(line)
            if match:
                violations.append(Violation(rule, match.group(0), idx + 1, line.strip()))
    return violations


# Deterministic downgrades. Order matters: the more specific phrases first, so
# "confirmed vulnerable" does not get half-rewritten by the generic rule.
_DOWNGRADES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(confirmed|terkonfirmasi)\s+(vuln\w*|kerentanan|bug|exploit\w*|rce|sqli|xss|ssrf|idor)", re.I),
     r"kandidat \2 (belum diverifikasi)"),
    (re.compile(r"\b(terbukti|proof|proven)\s+(exploit\w*|dieksploitasi|exploitable|vulnerab\w*|rentan|berhasil)", re.I),
     r"indikasi \2 (belum dibuktikan)"),
    (re.compile(r"\b(pasti|definitely|certainly|sudah pasti|dipastikan)\b", re.I), "kemungkinan besar"),
    (re.compile(r"\b(100\s*%|seratus persen)\b", re.I), "sebagian besar"),
    (re.compile(r"\b(0-?day|zero-?day)\b", re.I), "kerentanan yang belum dipublikasikan (dugaan)"),
    (re.compile(r"\b(dijamin|guaranteed|tanpa keraguan|undeniably)\b", re.I), "cenderung"),
)


def sanitize_claims(text: str) -> tuple[str, list[Violation]]:
    """Downgrade unbacked absolute claims in `text`.

    Returns the rewritten text plus the violations that were rewritten, so a
    caller can show the operator exactly which sentence was softened instead of
    silently editing their report.
    """
    violations = lint_claims(text)
    if not violations:
        return text, []

    lines = text.splitlines()
    offending = {v.line_no for v in violations}
    for idx, line in enumerate(lines, start=1):
        if idx not in offending:
            continue
        for pattern, repl in _DOWNGRADES:
            line = pattern.sub(repl, line)
        lines[idx - 1] = line
    return "\n".join(lines), violations


def guard_note(violations: Iterable[Violation]) -> str:
    """Operator-facing note appended when the gate fired."""
    items = list(violations)
    if not items:
        return ""
    detail = "; ".join(v.describe() for v in items[:5])
    more = f" (+{len(items) - 5} lagi)" if len(items) > 5 else ""
    return (
        "⚠ [veracity gate] klaim absolut tanpa bukti terdeteksi dan sudah diturunkan tingkatannya: "
        f"{detail}{more}. Klaim hanya boleh berstatus 'terkonfirmasi' kalau ada bukti request/response, "
        "minimal 1 reproduksi, dan 1 kontrol pembanding."
    )


def evidence_discipline_block() -> str:
    """The rule block injected into every agent prompt.

    Lives in code (not only in `prompts/*.txt`) so the ENFORCED rules and the
    STATED rules cannot drift apart: this exact text is what the gate below
    holds output to.
    """
    return "\n".join([
        "EVIDENCE DISCIPLINE (ditegakkan kode, bukan sekadar imbauan):",
        "1. Jangan pernah menyebut sebuah temuan 'terkonfirmasi/confirmed/terbukti/pasti/100%'",
        "   kecuali ada: (a) bukti request+response, (b) minimal 1 reproduksi, (c) 1 kontrol pembanding.",
        "2. Kalau salah satu syarat itu belum ada, tulis levelnya eksplisit:",
        "   'kandidat (belum diverifikasi)' atau 'belum terbukti' — bukan 'vulnerable'.",
        "3. Hasil negatif adalah hasil: katakan 'tidak ditemukan indikasi X' dan sebutkan apa yang diuji.",
        "4. Jangan mengarang CVE, versi, atau nama tool yang belum benar-benar dijalankan.",
        "5. Setiap klaim teknis sebutkan sumbernya (tool apa, path bukti apa).",
    ])


def self_description_block(*, provider: str, model: str, skills: int,
                           permission: str, extra: str = "") -> str:
    """Facts about WTH-AI derived from live state, never hardcoded.

    Replaces phrases like "Planner Brain (Nemotron) / Executor Brain (Qwen
    Coder)": those were typed into the prompt by hand and kept being served
    after the provider changed, so the tool described itself incorrectly.
    """
    lines = [
        "FAKTA WTH-AI SAAT INI (dibaca dari konfigurasi live — jangan sebut yang lain):",
        f"- Provider/model aktif: {provider}/{model}",
        f"- Jumlah skill terdeteksi: {skills}",
        f"- Permission mode: {permission}",
        "- Kalau operator bertanya model apa yang dipakai: jawab sesuai baris di atas.",
        "- Kamu TIDAK boleh menyebut nama model/provider lain sebagai milikmu.",
    ]
    if extra:
        lines.append(extra)
    return "\n".join(lines)

"""Proof profiles deterministik + negative-control (fitur Strix).

Meniru `proof_profiles.py` Strix: `evaluate_proof(category, facts)` menilai
fakta terstruktur saja — tanpa judgment model, tanpa narasi, deterministik.
Setiap profil mewajibkan `negative_control`; profil yang butuh dua identitas
(IDOR) mewajibkan `identity_a/identity_b/owned_baseline/foreign_attempt/
unauthorized_state_delta`.

Ini memperketat di atas gate veracity lama (`veracity.assess_claim`), bukan
menggantikannya. Hanya `ProofVerdict.INDEPENDENTLY_VERIFIED` yang boleh
menaikkan status temuan.
"""

from __future__ import annotations

from typing import Callable, Dict

from enum import Enum


class ProofVerdict(str, Enum):
    INDEPENDENTLY_VERIFIED = "independently_verified"
    TESTED_NEGATIVE = "tested_negative"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"
    OBSERVATION = "observation"
    BLOCKED = "blocked"
    FAILED = "failed"


class UnknownCategoryError(Exception):
    """Kategori proof profile tidak dikenal."""


def _require_facts(facts: Dict, *names: str) -> None:
    missing = [n for n in names if n not in facts]
    if missing:
        raise ValueError(f"facts wajib: {', '.join(missing)}")


def _eval_ssrf(facts: Dict) -> ProofVerdict:
    _require_facts(facts, "negative_control", "callback_received")
    if facts["callback_received"] and facts.get("internal_url_evidence"):
        return ProofVerdict.INDEPENDENTLY_VERIFIED
    return ProofVerdict.REJECTED


def _eval_idor(facts: Dict) -> ProofVerdict:
    _require_facts(
        facts,
        "negative_control",
        "identity_a",
        "identity_b",
        "owned_baseline",
        "foreign_attempt",
        "unauthorized_state_delta",
    )
    delta = facts["unauthorized_state_delta"]
    if delta and delta.lower() not in ("denied", "none"):
        return ProofVerdict.INDEPENDENTLY_VERIFIED
    return ProofVerdict.REJECTED


def _eval_xss(facts: Dict) -> ProofVerdict:
    _require_facts(facts, "negative_control", "execution_evidence")
    if facts.get("execution_evidence") and facts["execution_evidence"].lower() != "none":
        return ProofVerdict.INDEPENDENTLY_VERIFIED
    return ProofVerdict.REJECTED


def _eval_sql_injection(facts: Dict) -> ProofVerdict:
    _require_facts(facts, "negative_control", "db_effect_evidence")
    if facts.get("db_effect_evidence") and facts["db_effect_evidence"].lower() != "none":
        return ProofVerdict.INDEPENDENTLY_VERIFIED
    return ProofVerdict.REJECTED


def _eval_authorization(facts: Dict) -> ProofVerdict:
    _require_facts(facts, "negative_control", "privileged_role_ok", "lower_role_gain")
    if facts.get("privileged_role_ok") and facts.get("lower_role_gain"):
        return ProofVerdict.INDEPENDENTLY_VERIFIED
    return ProofVerdict.REJECTED


def _eval_auth_replay(facts: Dict) -> ProofVerdict:
    _require_facts(facts, "negative_control", "replay_succeeded")
    if facts.get("replay_succeeded"):
        return ProofVerdict.INDEPENDENTLY_VERIFIED
    return ProofVerdict.REJECTED


def _eval_race(facts: Dict) -> ProofVerdict:
    _require_facts(facts, "negative_control", "race_triggered")
    if facts.get("race_triggered") and facts.get("unexpected_state_delta"):
        return ProofVerdict.INDEPENDENTLY_VERIFIED
    return ProofVerdict.REJECTED


def _eval_exposed_service(facts: Dict) -> ProofVerdict:
    _require_facts(facts, "negative_control", "service_exposed")
    if facts.get("service_exposed") and facts.get("sensitive_data_reachable"):
        return ProofVerdict.INDEPENDENTLY_VERIFIED
    return ProofVerdict.REJECTED


def _eval_default_credentials(facts: Dict) -> ProofVerdict:
    _require_facts(facts, "negative_control", "login_succeeded")
    if facts.get("login_succeeded") and facts.get("credentials_source") in (
        "vendor_default",
        "documented",
    ):
        return ProofVerdict.INDEPENDENTLY_VERIFIED
    return ProofVerdict.REJECTED


_PROFILES: Dict[str, Callable[[Dict], ProofVerdict]] = {
    "idor": _eval_idor,
    "xss": _eval_xss,
    "ssrf": _eval_ssrf,
    "sql_injection": _eval_sql_injection,
    "authorization": _eval_authorization,
    "auth_replay": _eval_auth_replay,
    "race": _eval_race,
    "exposed_service": _eval_exposed_service,
    "default_credentials": _eval_default_credentials,
}


def evaluate_proof(category: str, facts: Dict) -> ProofVerdict:
    """Evaluasi fakta terstruktur untuk `category`; deterministik."""
    _require_facts(facts, "negative_control")
    evaluator = _PROFILES.get(category)
    if evaluator is None:
        raise UnknownCategoryError(f"kategori proof tidak dikenal: {category!r}")
    return evaluator(facts)

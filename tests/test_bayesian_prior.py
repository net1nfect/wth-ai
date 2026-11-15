"""Regression GAP-008 — planner prior Bayesian deterministik.

Fokus: sinyal yang TIDAK cocok aturan statis tetap menghasilkan usulan yang
bisa ditelusuri; posterior ternormalisasi; tidak ada usulan tanpa URL/data;
usulan Bayesian tidak mendahului aturan temuan langsung.
"""

from __future__ import annotations

import json

from wth_ai.hunt.priors import (
    DEFAULT_PRIORS,
    bayesian_next_tests,
    class_posterior,
    extract_features,
    load_prior_config,
)
from wth_ai.hunt.proactive import propose_next_tests


def test_posterior_is_normalized_and_deterministic() -> None:
    pairs = [{"url": "http://t/api/user?id=1&q=x", "response_status": 500,
              "body_snippet": "you have an error in your sql syntax"}]
    features = extract_features([], pairs)
    first = class_posterior(features)
    second = class_posterior(features)
    assert abs(sum(belief.probability for belief in first) - 1.0) < 1e-9
    assert [b.vuln_class for b in first] == [b.vuln_class for b in second]
    assert first[0].vuln_class == "sqli"
    assert any("body:sql-error" in item for item in first[0].evidence)


def test_signal_that_matches_no_rule_still_produces_a_proposal() -> None:
    """Bukti GAP-008: dua pair tanpa kelas temuan dulu = 0 usulan.

    Sekarang body error SQL + status 5xx harus menghasilkan usulan sqli yang
    menyebut posterior dan fitur penggeraknya.
    """
    pairs = [
        {"url": "http://t/product?q=shoes", "response_status": 500,
         "body_snippet": "You have an error in your SQL syntax"},
        {"url": "http://t/product?q=bags", "response_status": 500,
         "body_snippet": "You have an error in your SQL syntax"},
    ]
    tests = propose_next_tests(findings=[], request_pairs=pairs)
    assert tests, "sinyal non-rule tidak boleh lagi menghasilkan nol usulan"
    sqli = [t for t in tests if t.vuln_class == "sqli"]
    assert sqli, [t.vuln_class for t in tests]
    assert "posterior" in sqli[0].rationale.lower()
    assert sqli[0].probe["purpose"]
    assert sqli[0].risk == "low"


def test_url_parameter_prioritises_ssrf_probe() -> None:
    from urllib.parse import unquote

    pairs = [{"url": "http://t/fetch?url=http://example.com/a", "response_status": 200,
              "body_snippet": "ok"}]
    proposals = bayesian_next_tests(request_pairs=pairs, limit=3)
    classes = [p.vuln_class for p in proposals]
    assert "ssrf" in classes
    ssrf = next(p for p in proposals if p.vuln_class == "ssrf")
    assert "127.0.0.1:1/wth-ssrf-probe" in unquote(ssrf.probe["url"])


def test_no_data_or_no_url_means_no_bayesian_proposals() -> None:
    assert bayesian_next_tests() == []
    assert bayesian_next_tests(findings=[], request_pairs=[{"response_status": 500}]) == []


def test_bayesian_priority_stays_below_direct_finding_rules() -> None:
    finding = {"id": "F1", "vuln_class": "idor", "target": "http://t/api/user?id=1"}
    direct = propose_next_tests(findings=[finding])
    assert direct and "tetangga" in direct[0].title.lower()
    direct_priority = direct[0].priority

    # Bandingkan langsung: usulan Bayesian untuk URL yang sama boleh terdedupe
    # oleh aturan langsung (memang diinginkan); yang diuji adalah skala prioritas.
    bayesian = bayesian_next_tests(findings=[finding], limit=4)
    assert bayesian, "fitur URL temuan juga harus memicu usulan Bayesian"
    assert all(t.priority < direct_priority for t in bayesian)


def test_limit_is_respected_and_keys_unique() -> None:
    pairs = [{"url": "http://t/api/user?id=1&q=x&url=http://a", "response_status": 500,
              "body_snippet": "sql syntax error uid=0(root) gid=0 root:x:0:0"}]
    proposals = propose_next_tests(findings=[], request_pairs=pairs, limit=3)
    assert 0 < len(proposals) <= 3
    keys = [(p.probe.get("method"), p.probe.get("url")) for p in proposals]
    assert len(keys) == len(set(keys))


def test_priors_override_file(tmp_path) -> None:
    config = tmp_path / "priors.json"
    config.write_text(json.dumps({
        "priors": {"sqli": 0.90, **{cls: 0.01 for cls in DEFAULT_PRIORS if cls != "sqli"}},
        "likelihoods": {"param:q": {"sqli": 3.0}},
    }), encoding="utf-8")
    priors, likelihoods = load_prior_config(config)
    assert priors["sqli"] > 0.8
    assert likelihoods["param:q"]["sqli"] == 3.0
    beliefs = class_posterior({"param:q": ["param q"]}, priors=priors, likelihoods=likelihoods)
    assert beliefs[0].vuln_class == "sqli"


def test_header_signal_feeds_features() -> None:
    pairs = [{"url": "http://t/item?id=7", "response_status": 200,
              "response_headers": {"X-Powered-By": "PHP/8.1", "Content-Type": "application/json"}}]
    features = extract_features([], pairs)
    assert "header:php" in features
    assert "header:json" in features
    assert "param:id" in features

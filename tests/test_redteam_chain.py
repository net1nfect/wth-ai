"""Chaining: hanya temuan CONFIRMED yang memberi konteks."""

from wth_ai.redteam.chain import derive_chain
from wth_ai.redteam.models import AttemptState, Finding


def _confirmed(fid, attestation):
    f = Finding(id=fid, hypothesis="x", attestation=attestation)
    f.transition(AttemptState.ATTEMPTING)
    f.transition(AttemptState.CONFIRMED)
    return f


def test_confirmed_without_context_makes_no_chain():
    assert derive_chain([_confirmed("F-1", {})]) == []


def test_confirmed_with_credentials_makes_one_link():
    chains = derive_chain([_confirmed("F-1", {"credentials": ["userB:pass"]})])
    assert len(chains) == 1
    assert chains[0].root == "F-1"
    assert chains[0].to_dict()["links"][0]["context"]["credentials"] == ["userB:pass"]
"""Global test defaults.

Production ships `WTH_NO_LIMITS` ON. Most legacy tests assert the individual
safety layers (scope, permission, denylist), so they run with the master switch
OFF unless a test explicitly turns it on. Explicit env wins over the module
globals, and this fixture also resets those globals so one test file applying a
preset (`godmode`) cannot leak into the next.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_safety_defaults(monkeypatch):
    from wth_ai.engagement import mode as mode_mod

    monkeypatch.setattr(mode_mod, "_no_limits", False)
    monkeypatch.setattr(mode_mod, "_unrestricted", False)
    monkeypatch.setenv("WTH_NO_LIMITS", "0")
    monkeypatch.setenv("WTH_UNRESTRICTED", "0")
    monkeypatch.setenv("WTH_AUTO_APPROVE", "0")
    monkeypatch.setenv("WTH_REQUIRE_AUTHORIZED", "1")
    yield

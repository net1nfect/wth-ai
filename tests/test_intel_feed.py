"""Intel feed: egress, sanitasi data eksternal, applicability (audit 2026-11-06)."""

from __future__ import annotations

import subprocess

from wth_ai.config import IntelConfig
from wth_ai.intel.feed import IntelFeed


def test_check_nuclei_templates_never_spawns_subprocess(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))

    def boom(*args, **kwargs):
        raise AssertionError("subprocess.run tidak boleh dipanggil")

    monkeypatch.setattr(subprocess, "run", boom)
    out = IntelFeed().check_nuclei_templates()
    assert out["status"] == "disabled"
    assert "wth tools install" in out["reason"]


def test_nuclei_templates_check_defaults_off():
    assert IntelConfig().nuclei_templates_check is False


def test_sanitize_masks_secret_and_control_chars():
    from wth_ai.redaction import sanitize_untrusted_text

    out = sanitize_untrusted_text("token ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n\x00<script>")
    assert "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" not in out
    assert "\x00" not in out and "\n" not in out


def test_cve_hint_is_sanitized_and_tagged(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))

    class Resp:
        ok = True

        def json(self):
            return {"vulnerabilities": [{"cve": {
                "id": "CVE-2020-0001",
                "descriptions": [{"lang": "en",
                                  "value": "leak ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n\x00"}],
                "configurations": [{"nodes": [{"cpeMatch": [{}]}]}],
            }}]}

    monkeypatch.setattr("wth_ai.intel.feed.requests.get", lambda *a, **k: Resp())
    monkeypatch.setattr("wth_ai.intel.feed.audit_log", lambda *a, **k: None)
    feed = IntelFeed()
    feed.config.intel.cve_lookup = True
    hints = feed.cve_hints_for_tech({"nginx": "1.25"})
    assert hints[0]["provenance"] == "external-nvd"
    assert "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" not in hints[0]["summary"]


CVE_RANGE = {"configurations": [{"nodes": [{"cpeMatch": [{
    "versionStartIncluding": "1.0", "versionEndExcluding": "1.21.0"}]}]}]}


def test_version_in_range_true_inside():
    from wth_ai.intel.feed import _version_in_range

    assert _version_in_range(CVE_RANGE, "1.20.0") is True


def test_version_in_range_false_outside():
    from wth_ai.intel.feed import _version_in_range

    assert _version_in_range(CVE_RANGE, "1.25.0") is False


def test_version_in_range_unknown_when_no_configurations():
    from wth_ai.intel.feed import _version_in_range

    assert _version_in_range({}, "1.25.0") is True


def test_cve_hints_skip_version_outside_range(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))

    class Resp:
        ok = True

        def json(self):
            return {"vulnerabilities": [{"cve": {
                "id": "CVE-2020-0002",
                "descriptions": [{"lang": "en", "value": "lama"}],
                "configurations": CVE_RANGE["configurations"],
            }}]}

    monkeypatch.setattr("wth_ai.intel.feed.requests.get", lambda *a, **k: Resp())
    monkeypatch.setattr("wth_ai.intel.feed.audit_log", lambda *a, **k: None)
    feed = IntelFeed()
    feed.config.intel.cve_lookup = True
    assert feed.cve_hints_for_tech({"nginx": "1.25.0"}) == []
    assert feed.cve_hints_for_tech({"nginx": "1.20.0"})


def test_version_in_range_pads_short_versions():
    from wth_ai.intel.feed import _version_in_range

    cve = {"configurations": [{"nodes": [{"cpeMatch": [{"versionStartIncluding": "1.2.0"}]}]}]}
    assert _version_in_range(cve, "1.2") is True


def test_version_in_range_unparseable_version_is_true():
    from wth_ai.intel.feed import _version_in_range

    assert _version_in_range(CVE_RANGE, "v?") is True


def test_sanitize_strips_bidi_and_zero_width():
    from wth_ai.redaction import sanitize_untrusted_text

    out = sanitize_untrusted_text("abc\u202edef\u200bghi\u2028j\x9b")
    for ch in ("\u202e", "\u200b", "\u2028", "\x9b"):
        assert ch not in out

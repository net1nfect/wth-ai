"""Regression tests — P0 Exit Review finding (HIGH): parser natural-language
`parse_scope_intent()` dibalik oleh negasi.

Bug asli (ditemukan adversarial exit-review subagent, TERKONFIRMASI lewat
reproduksi mandiri sebelum fix): `authorized = "authorized" in lower or
"izin" in lower` -- substring check naif. Teks yang secara eksplisit
MENOLAK otorisasi malah menghasilkan authorized=True:
- "set target evil.com not authorized" -> authorized=True (SALAH -- kata
  "authorized" muncul sebagai substring, "not" di depannya diabaikan)
- "target evil.com, unauthorized" -> authorized=True (SALAH -- "authorized"
  adalah substring literal dari "unauthorized")

Ini langsung membalik tujuan WU-011 sendiri ("fail-closed: MISSING -> DENY,
hanya True kalau teks eksplisit menyebut authorized") -- teks yang secara
eksplisit MENYANGKAL otorisasi malah ditafsirkan MEMBERI otorisasi. Reachable
dari jalur LLM-offline fallback (`supervisor.py::_offline_fallback`) MAUPUN
command manusia `/target` (`cli/handlers/setup_handler.py::handle_target`).

Fix: `_text_expresses_authorization()` pakai `\\b...\\b` (menutup celah
"unauthorized" -- tidak ada word-boundary antara "un" dan "authorized") +
cek negasi eksplisit (not/tidak/belum/tanpa/dst) di 1-2 kata sebelum kata
authorized/izin -- fail-closed: ambigu/negasi -> False.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.scope_intent import parse_scope_intent


# ---------------------------------------------------------------------------
# REGRESI-GUARD utama -- kasus persis yang ditemukan exit-review
# ---------------------------------------------------------------------------

def test_not_authorized_does_not_grant_authorization():
    args = parse_scope_intent("set target evil.com not authorized")
    assert args is not None
    assert args["authorized"] is False, (
        "REGRESI: teks yang eksplisit MENOLAK otorisasi ('not authorized') "
        "malah menghasilkan authorized=True"
    )


def test_unauthorized_substring_does_not_grant_authorization():
    args = parse_scope_intent("target evil.com, unauthorized")
    assert args is not None
    assert args["authorized"] is False, (
        "REGRESI: kata 'unauthorized' (mengandung substring 'authorized') "
        "salah ditafsirkan sebagai pemberian otorisasi"
    )


def test_indonesian_negation_tidak_izin():
    args = parse_scope_intent("scope evil.com tidak izin")
    assert args["authorized"] is False


def test_indonesian_negation_tanpa_izin():
    args = parse_scope_intent("scope evil.com tanpa izin")
    assert args["authorized"] is False


def test_indonesian_negation_belum_authorized():
    args = parse_scope_intent("scope evil.com belum authorized")
    assert args["authorized"] is False


# ---------------------------------------------------------------------------
# Positive/compatibility -- otorisasi positif eksplisit tetap terdeteksi
# ---------------------------------------------------------------------------

def test_positive_authorized_still_detected():
    args = parse_scope_intent("scope example.com authorized")
    assert args["authorized"] is True


def test_positive_izin_still_detected():
    args = parse_scope_intent("scope example.com izin")
    assert args["authorized"] is True


def test_no_authorization_language_at_all_defaults_false():
    args = parse_scope_intent("target evil.com")
    assert args is not None
    assert args["authorized"] is False

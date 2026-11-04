"""Knowledge base CVE lokal (SQLite FTS5).

Pengetahuan tidak masuk bobot model: ia masuk database, dan model hanya boleh
menyebut ID yang benar-benar ada di sini (lihat `wth_ai/kb/cite.py`).
"""

from __future__ import annotations

from wth_ai.kb.db import connect, get_meta, set_meta
from wth_ai.kb.cite import UngroundedCitation, validate_citations

__all__ = ["UngroundedCitation", "connect", "get_meta", "set_meta",
           "validate_citations"]
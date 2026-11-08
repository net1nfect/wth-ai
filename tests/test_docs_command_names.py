"""P2-10: satu nama perintah kanonik (`wth-ai`) di semua permukaan dokumen.

`wth` dihapus sepenuhnya (D5 dibalik: `wth-ai` satu-satunya nama). Test ini
menolak `wth` telanjang di blok kode README/DOCKER, tapi tetap membolehkan
token yang bukan nama perintah: `.wth-ai`, `wth_ai`, `wth_ai_config.yaml`,
`wth-ai-results`, `wth-ai-cli`, `wth-ai-worker`, `wth-ai`.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: `wth` berdiri sendiri — bukan bagian dari `wth-ai`, `wth_ai`, `.wth-ai`,
#: `/wth-ai`, `wth_ai_config`, `wth-ai-main.py`, dan sejenisnya.
_BARE_WTH = re.compile(r"(?<![-\w./])wth(?![-\w.])")

#: `git clone <repo> wth` menamai direktori tujuan, bukan perintah.
_NOT_A_COMMAND = re.compile(r"^\s*git clone\b")

_DOCS = ("README.md", "AGENTS.md", "llms.txt", "docs/DOCKER.md")


def _bare_wth_lines(path: Path):
    """Baris di dalam blok kode yang memakai `wth` telanjang sebagai perintah."""
    in_fence = False
    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence or _NOT_A_COMMAND.match(line):
            continue
        if _BARE_WTH.search(line):
            yield lineno, line


def test_no_document_uses_bare_wth_as_a_command():
    bad = []
    for rel in _DOCS:
        path = REPO / rel
        if not path.exists():
            continue
        bad += [f"{rel}:{n}: {ln.strip()}" for n, ln in _bare_wth_lines(path)]
    assert not bad, "pakai `wth-ai` (kanonik); `wth` dihapus:\n" + "\n".join(bad)


def test_docker_uses_the_canonical_command():
    text = (REPO / "Dockerfile").read_text()
    assert '"wth-ai"' in text
    compose = (REPO / "docker-compose.yaml").read_text()
    assert '"wth-ai"' in compose
"""Local file/image attachments for chat turns.

Terminal chat cannot hand a file to a browser file-picker, so the operator
attaches a path (typed, pasted, or dragged into the terminal as a path).  The
model layer receives text files as bounded text and images as data URLs; a
binary file is represented by its path so the agent can inspect it with tools.

This module is deliberately free of Textual/prompt_toolkit imports: the same
attachment objects must work in the line REPL, the fullscreen TUI, and tests.
"""

from __future__ import annotations

import base64
import mimetypes
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlparse

#: Keep a pasted screenshot or source file from turning one turn into an
#: unbounded request.  The manifest still names anything larger.
MAX_TEXT_BYTES = 128 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024

_CODE_SUFFIXES = frozenset({
    ".c", ".cc", ".cpp", ".h", ".hpp", ".py", ".js", ".jsx", ".ts", ".tsx",
    ".go", ".rs", ".java", ".kt", ".rb", ".php", ".sh", ".bash", ".zsh",
    ".sql", ".yml", ".yaml", ".json", ".toml", ".ini", ".cfg", ".conf",
    ".md", ".txt", ".log", ".html", ".css", ".xml", ".env",
})


@dataclass(frozen=True)
class Attachment:
    """A validated local attachment (metadata only; contents are read lazily)."""

    path: str
    name: str
    mime: str
    size: int
    kind: str

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "name": self.name,
            "mime": self.mime,
            "size": self.size,
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "Attachment":
        return cls(
            path=str(data.get("path", "")),
            name=str(data.get("name", "")),
            mime=str(data.get("mime", "application/octet-stream")),
            size=int(data.get("size", 0) or 0),
            kind=str(data.get("kind", "binary")),
        )


def _kind_for(path: Path, mime: str) -> str:
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("text/") or path.suffix.lower() in _CODE_SUFFIXES:
        return "text"
    return "binary"


def attachment_from_path(raw: str | os.PathLike[str]) -> Attachment:
    """Resolve and validate one local file. Raises ValueError with a usable reason."""
    text = str(raw).strip().strip('"').strip("'")
    if text.startswith("file://"):
        parsed = urlparse(text)
        text = unquote(parsed.path)
    path = Path(text).expanduser()
    if not path.exists():
        raise ValueError(f"file tidak ditemukan: {path}")
    if not path.is_file():
        raise ValueError(f"bukan file biasa: {path}")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ValueError(f"tidak bisa membaca metadata {path}: {exc}") from exc
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return Attachment(
        path=str(path.resolve()),
        name=path.name,
        mime=mime,
        size=size,
        kind=_kind_for(path, mime),
    )


def paths_from_paste(text: str) -> list[Path]:
    """Return existing files when a paste looks like a path (or a path per line).

    This deliberately refuses a normal sentence containing a slash: only a whole
    line that resolves to an existing file is treated as an attachment.
    """
    raw = (text or "").strip()
    if not raw:
        return []
    lines = [line.strip().strip('"').strip("'") for line in raw.splitlines() if line.strip()]
    if not lines or len(lines) > 20:
        return []
    found: list[Path] = []
    for line in lines:
        candidate = line
        # Terminal drag-and-drop often inserts a backslash-escaped path:
        # `/tmp/my\ shot.png`. Split once when it yields exactly one token.
        if "\\" in candidate or " " in candidate:
            try:
                parts = shlex.split(candidate)
            except ValueError:
                parts = []
            if len(parts) == 1:
                candidate = parts[0]
        if candidate.startswith("file://"):
            candidate = unquote(urlparse(candidate).path)
        if "\x00" in candidate:
            return []
        path = Path(candidate).expanduser()
        if path.exists() and path.is_file():
            found.append(path)
        else:
            return []
    return found


def read_text(attachment: Attachment) -> str:
    """Bounded UTF-8 text, with an explicit marker when the file is larger."""
    path = Path(attachment.path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        return f"[tidak bisa membaca file: {exc}]"
    clipped = data[:MAX_TEXT_BYTES]
    text = clipped.decode("utf-8", errors="replace")
    if len(data) > MAX_TEXT_BYTES:
        text += f"\n\n[... dipotong: {len(data) - MAX_TEXT_BYTES} byte lagi ...]"
    return text


def image_data_url(attachment: Attachment) -> str | None:
    """Base64 data URL for an image, or None when it is too large/unreadable."""
    if attachment.kind != "image" or attachment.size > MAX_IMAGE_BYTES:
        return None
    try:
        data = Path(attachment.path).read_bytes()
    except OSError:
        return None
    mime = attachment.mime or "image/png"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def attachment_manifest(attachments: Iterable[Attachment | dict[str, object]]) -> str:
    """Plain-text manifest for the agentic supervisor (which reads files via tools)."""
    items = [_coerce(a) for a in attachments]
    if not items:
        return ""
    lines = ["[Lampiran lokal]"]
    for item in items:
        note = ""
        if item.kind == "image":
            note = " · gambar (butuh model vision untuk melihat isi)"
        elif item.kind == "binary":
            note = " · biner (gunakan tool untuk memeriksa)"
        lines.append(f"- {item.path} ({item.mime}, {item.size} byte){note}")
    lines.append(f"[/Lampiran — total {len(items)}]")
    return "\n".join(lines)


def user_content_parts(
    attachments: Iterable[Attachment | dict[str, object]],
    *,
    allow_images: bool = True,
) -> list[dict[str, object]]:
    """OpenAI-style content parts for the chat path.

    Text is included inline (bounded), images are data URLs.  Non-vision
    providers get a path manifest instead of an image part, so they fail
    honestly rather than silently receiving a broken payload.
    """
    parts: list[dict[str, object]] = []
    for raw in attachments:
        item = _coerce(raw)
        if item.kind == "text":
            body = read_text(item)
            parts.append({
                "type": "text",
                "text": f"\n\n[Lampiran: {item.name} · {item.path}]\n{body}\n[/Lampiran]",
            })
        elif item.kind == "image" and allow_images:
            data_url = image_data_url(item)
            if data_url:
                parts.append({"type": "image_url", "image_url": {"url": data_url}})
                continue
            parts.append({
                "type": "text",
                "text": f"\n\n[Lampiran gambar: {item.path} — terlalu besar/tidak bisa dibaca]",
            })
        else:
            parts.append({
                "type": "text",
                "text": f"\n\n[Lampiran: {item.path} ({item.mime}, {item.size} byte)]",
            })
    return parts


def _coerce(value: Attachment | dict[str, object]) -> Attachment:
    return value if isinstance(value, Attachment) else Attachment.from_dict(value)


def attachment_summary(attachments: Iterable[Attachment | dict[str, object]]) -> str:
    items = [_coerce(a) for a in attachments]
    if not items:
        return "Tidak ada lampiran."
    lines = [f"{len(items)} lampiran:"]
    for item in items:
        lines.append(f"  • {item.name} ({item.kind}, {item.size} byte) — {item.path}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# system clipboard helpers (TUI / REPL without a GUI toolkit)
# --------------------------------------------------------------------------- #
def _run(argv: list[str], *, input_bytes: bytes | None = None) -> bytes | None:
    if not shutil.which(argv[0]):
        return None
    try:
        result = subprocess.run(
            argv,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def read_clipboard_text() -> str:
    """Read system clipboard text, trying common Linux/macOS backends."""
    try:
        import pyperclip

        value = pyperclip.paste()
        if isinstance(value, str) and value:
            return value
    except Exception:  # noqa: BLE001 - clipboard is best-effort
        pass
    for argv in (
        ["wl-paste", "--no-newline"],
        ["xclip", "-selection", "clipboard", "-o"],
        ["xsel", "--clipboard", "--output"],
        ["pbpaste"],
    ):
        data = _run(argv)
        if data:
            return data.decode("utf-8", errors="replace")
    return ""


def write_clipboard_text(text: str) -> bool:
    """Write text to the system clipboard; returns whether a backend succeeded."""
    try:
        import pyperclip

        pyperclip.copy(text)
        return True
    except Exception:  # noqa: BLE001
        pass
    data = text.encode("utf-8")
    for argv in (
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
        ["pbcopy"],
    ):
        out = _run(argv, input_bytes=data)
        if out is not None:
            return True
    return False


_IMAGE_MAGIC = {
    "image/png": (b"\x89PNG\r\n\x1a\n", ".png"),
    "image/jpeg": (b"\xff\xd8\xff", ".jpg"),
    "image/webp": (b"RIFF", ".webp"),
}


def read_clipboard_image() -> tuple[bytes, str] | None:
    """Read an image from the system clipboard, returning (bytes, mime)."""
    for mime, (magic, _suffix) in _IMAGE_MAGIC.items():
        for argv in (
            ["wl-paste", "--type", mime],
            ["xclip", "-selection", "clipboard", "-t", mime, "-o"],
        ):
            data = _run(argv)
            if data and len(data) <= MAX_IMAGE_BYTES and data.startswith(magic):
                return data, mime
    return None


def read_clipboard_image_png() -> bytes | None:
    """Backward-compatible PNG-only helper."""
    found = read_clipboard_image()
    if found and found[1] == "image/png":
        return found[0]
    return None


def save_clipboard_image(
    data: bytes,
    *,
    mime: str = "image/png",
    home: str | os.PathLike[str] | None = None,
) -> Path:
    """Persist a clipboard image under WTH_HOME so it can be attached by path."""
    base = Path(home or os.environ.get("WTH_HOME", Path.home() / ".wth-ai"))
    target_dir = base / "attachments"
    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = _IMAGE_MAGIC.get(mime, (b"", ".png"))[1]
    path = target_dir / f"paste-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}{suffix}"
    path.write_bytes(data)
    return path


def looks_like_path(text: str) -> bool:
    """Cheap hint used by UI placeholders; does not touch the filesystem."""
    value = (text or "").strip()
    return bool(value) and "\n" not in value and (
        value.startswith(("/", "~/", "./", "../", "file://"))
        or bool(re.search(r"\.[A-Za-z0-9]{1,8}$", value))
    )

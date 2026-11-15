"""Attach local files/images to the next chat turn."""

from __future__ import annotations

from typing import Any

from wth_ai.attachments import Attachment, attachment_from_path, attachment_summary


def _pending(orch: Any) -> list[dict[str, object]]:
    state = getattr(orch, "state", None)
    metadata = getattr(state, "metadata", None)
    if not isinstance(metadata, dict):
        return []
    raw = metadata.get("pending_attachments") or []
    return [dict(item) for item in raw if isinstance(item, dict)]


def handle_attach(orch: Any, args: list[str]) -> str:
    """`/attach path...`, `/attach list`, `/attach clear`.

    Attachments stay pending until the next normal message. Slash commands do
    not consume them, so `/attach shot.png` can be followed by `/permission
    standard` and then a question without losing the file.
    """
    if not args or args[0].lower() in {"list", "show"}:
        pending = _pending(orch)
        return attachment_summary(pending)

    if args[0].lower() in {"clear", "clear-all", "reset"}:
        orch.state.metadata["pending_attachments"] = []
        orch._persist()
        return "Lampiran dibersihkan."

    added: list[Attachment] = []
    errors: list[str] = []
    for raw in args:
        try:
            added.append(attachment_from_path(raw))
        except ValueError as exc:
            errors.append(str(exc))

    if not added:
        return "Tidak ada lampiran ditambahkan.\n" + "\n".join(errors)

    pending = _pending(orch)
    seen = {str(item.get("path", "")) for item in pending}
    for item in added:
        if item.path not in seen:
            pending.append(item.to_dict())
            seen.add(item.path)
    orch.state.metadata["pending_attachments"] = pending
    orch._persist()

    out = [attachment_summary(pending)]
    if errors:
        out.append("Dilewati:\n" + "\n".join(f"  • {err}" for err in errors))
    out.append("Lampiran dikirim bersama pesan berikutnya. `/attach clear` untuk membatalkan.")
    return "\n".join(out)

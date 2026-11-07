"""Compaction konteks: ringkas pesan lama tanpa membuang referensi bukti."""

from __future__ import annotations

from typing import Any

from wth_ai.llm.compaction import TOOL_OUTPUT_MAX_CHARS, compact_messages


def _tool(role: str, content: str) -> dict[str, Any]:
    return {"role": role, "content": content}


def test_short_conversation_is_not_compacted() -> None:
    messages = [_tool("system", "sys")] + [_tool("user", f"u{i}") for i in range(4)]
    out, summarized = compact_messages(messages, model=None)
    assert out == messages
    assert summarized == 0


def test_system_message_never_touched() -> None:
    sys_msg = _tool("system", "system prompt yang panjang")
    messages = [sys_msg] + [_tool("user", "x" * 40_000) for _ in range(10)]
    out, summarized = compact_messages(messages, model=None)
    assert out[0] == sys_msg
    assert summarized > 0


def test_evidence_reference_survives() -> None:
    messages = [_tool("system", "sys")]
    messages.append(_tool("assistant", "temuan [TX:abc123] pada /login"))
    messages += [_tool("user", "y" * 30_000) for _ in range(10)]
    out, _ = compact_messages(messages, model=None)
    joined = "\n".join(str(m.get("content", "")) for m in out)
    assert "[TX:abc123]" in joined
    assert "bukti utuh di bundel" in joined


def test_long_tool_output_is_head_tail_truncated() -> None:
    body = "AWAL" + ("z" * 20_000) + "AKHIR"
    messages = [_tool("system", "sys")]
    messages.append(_tool("tool", body))
    messages += [_tool("user", "y" * 30_000) for _ in range(10)]
    out, _ = compact_messages(messages, model=None)
    tool_msgs = [m for m in out if m.get("role") == "tool"]
    assert tool_msgs, "pesan tool hilang"
    text = str(tool_msgs[0]["content"])
    assert len(text) <= TOOL_OUTPUT_MAX_CHARS + 100
    assert text.startswith("AWAL")
    assert text.endswith("AKHIR")


def test_keeps_last_messages_verbatim() -> None:
    tail = [_tool("user", f"akhir{i}") for i in range(3)]
    messages = [_tool("system", "sys")] + [_tool("user", "z" * 40_000)] * 9 + tail
    out, _ = compact_messages(messages, model=None, keep_last=3)
    assert out[-3:] == tail

"""Skills picker modal."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from wth_ai.cli.ui.theme import THEME


class SkillsPickerScreen(ModalScreen[str | None]):
    DEFAULT_CSS = f"""
    SkillsPickerScreen {{
        align: center middle;
    }}
    #skills-box {{
        width: 80;
        height: auto;
        max-height: 22;
        border: solid {THEME['plan']};
        background: {THEME['surface_alt']};
        padding: 1 2;
    }}
    """

    def __init__(self, orch, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.orch = orch

    def compose(self) -> ComposeResult:
        with Vertical(id="skills-box"):
            yield Static("[bold]Skills picker[/] — search and press Enter")
            yield Input(placeholder="xss, ctf, recon…", id="skills-input")
            yield Static("", id="skills-results")

    def on_mount(self) -> None:
        self._refresh("")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "skills-input":
            self._refresh(event.value)

    def _refresh(self, query: str) -> None:
        matches = self.orch.skills.search(query, limit=15) if query else list(self.orch.skills.skills.values())[:15]
        lines = [f"`{s.id}` — {s.name[:50]}" for s in matches]
        self.query_one("#skills-results", Static).update("\n".join(lines) or "No skills")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "skills-input":
            return
        q = event.value.strip()
        matches = self.orch.skills.search(q, limit=1)
        if matches:
            self.dismiss(matches[0].id)
        else:
            self.dismiss(None)

"""Footer shortcut bar."""

from textual.widgets import Static

FOOTER_TEXT = (
    " ctrl+p palette · ctrl+o lampiran · ctrl+/ help · ctrl+t activity · "
    "ctrl+l clear · ctrl+c interrupt "
)


class FooterBar(Static):
    def on_mount(self) -> None:
        self.update(FOOTER_TEXT)

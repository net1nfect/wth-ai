"""Footer shortcut bar."""

from textual.widgets import Static

FOOTER_TEXT = (
    " ctrl+1..5 tabs · ctrl+p palette · ctrl+/ help · /quit exit · "
    "ctrl+d exit · ctrl+c interrupt · ctrl+l clear · ctrl+t activity panel "
)


class FooterBar(Static):
    def on_mount(self) -> None:
        self.update(FOOTER_TEXT)

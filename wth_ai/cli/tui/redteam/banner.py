"""Banner ASCII untuk TUI red team.

Dua varian: lebar dan sempit. Dipilih dari lebar terminal supaya tidak pecah.
"""

from __future__ import annotations

WIDE = r"""
 ██╗    ██╗████████╗██╗  ██╗    █████╗ ██╗
 ██║    ██║╚══██╔══╝██║  ██║   ██╔══██╗██║
 ██║ █╗ ██║   ██║   ███████║   ███████║██║
 ██║███╗██║   ██║   ██╔══██║   ██╔══██║██║
 ╚███╔███╔╝   ██║   ██║  ██║   ██║  ██║██║
  ╚══╝╚══╝    ╚═╝   ╚═╝  ╚═╝   ╚═╝  ╚═╝╚═╝
"""

NARROW = r"""
  W T H - A I
"""

TAGLINE = "  red team engine · evidence first · scope enforced"


def banner(width: int = 80, version: str = "1.0.0") -> str:
    art = WIDE if width >= 52 else NARROW
    head = art.rstrip("\n")
    return (
        f"[bold]{head}[/bold]\n"
        f"[dim]{TAGLINE}[/dim]\n"
        f"[dim]  v{version}  ·  ketik [bold]/help[/bold] untuk perintah, "
        f"[bold]tab[/bold] ganti mode[/dim]"
    )


def compact(mode_glyph: str, mode_label: str, color: str) -> str:
    """Satu baris untuk header sempit."""
    return f"[{color}]{mode_glyph}[/{color}] [bold]{mode_label}[/bold]"
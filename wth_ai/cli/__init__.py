"""CLI package.

`WTHRepl` sengaja TIDAK diekspor di sini. Modul ini diimpor setiap kali
`wth_ai.cli.entry` dijalankan, dan symlink `wth-ai` di venv memasukkan direktori
repo ke `sys.path` — jadi `import wth_ai.cli` terjadi SEBELUM impor sungguhan
lewat `runpy`. Kalau paket ini menarik `repl` (yang butuh prompt_toolkit →
slash_completer), seluruh CLI gagal di instalasi minimal karena satu widget
lengkap opsional.

Impor langsung ke modulnya tetap jalan: `from wth_ai.cli.repl import WTHRepl`.
"""

__all__: list[str] = []

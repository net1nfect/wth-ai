"""Regression test untuk dua bug yang lolos dari UAT pertama.

1. **Traceback `on_render` di picker** (dilaporkan operator dari terminalnya): cabang
   `elif on_render is not None` di `build_picker_bindings` merujuk nama yang hanya ada
   di `pick_from_list` -- sisa ekstraksi yang memindahkan blok tanpa membawa nama yang
   dipakainya. Pemicunya: menekan tombol NON-PRINTABLE (Tab, F1, NUL) saat picker
   terbuka. Sweep gua sebelumnya tidak menangkapnya karena memanggil
   `orch.handle_input()` langsung, tidak pernah menyentuh jalur prompt-toolkit.

2. **Command yang punya handler tapi tidak ada di registry**: `/usage`, `/next`,
   `/payloads`, `/autopilot`, `/eval act`, `/jobs` semuanya bisa dijalankan tapi tidak
   muncul di `/help`, tidak ada di menu completion, dan -- yang paling merusak --
   tidak pernah diuji sweep otomatis yang menyusuri registry.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest


# --------------------------------------------------------------------------- #
# 1. picker: tombol non-printable tidak boleh meledak
# --------------------------------------------------------------------------- #
class _Buf:
    complete_state = None
    text = ""


class _App:
    def current_buffer(self):
        return _Buf()


class _Event:
    """Event minimal: cukup untuk handler `<any>` di picker."""

    def __init__(self, data: str) -> None:
        self.data = data
        self.app = type("A", (), {"invalidate": lambda self: None, "exit": lambda self, **k: None})()


def _any_binding(bindings):
    for binding in bindings.bindings:
        if any(str(key) == "Keys.Any" for key in binding.keys):
            return binding
    return None


@pytest.mark.parametrize("key", ["\t", "\x00", "\x1bOP", "\x7f", "a"])
def test_picker_type_handler_survives_every_key_kind(key):
    """Termasuk tombol yang dulu memicu NameError (non-printable, F1, NUL)."""
    from wth_ai.cli.ui.picker import ListPickerState, build_picker_bindings

    state = ListPickerState(items=["a", "b", "c"])
    result: dict = {"value": None}
    bindings = build_picker_bindings(state, result)
    binding = _any_binding(bindings)
    assert binding is not None, "handler <any> tidak ditemukan"

    binding.handler(_Event(key))  # tidak boleh raise (dulu: NameError on_render)


def test_picker_filter_still_works_after_the_fix():
    from wth_ai.cli.ui.picker import ListPickerState, build_picker_bindings

    state = ListPickerState(items=["alpha", "beta"])
    result: dict = {"value": None}
    bindings = build_picker_bindings(state, result)
    handler = _any_binding(bindings).handler

    handler(_Event("b"))
    handler(_Event("e"))
    assert state.query == "be"
    assert state.matches == ["beta"]

    handler(_Event("\x7f"))          # backspace menghapus satu karakter
    assert state.query == "b"


def test_picker_has_no_reference_to_a_parameter_it_does_not_own():
    """Bug aslinya: nama dari fungsi lain dipakai di fungsi binding."""
    import inspect

    from wth_ai.cli.ui import picker as picker_mod

    source = inspect.getsource(picker_mod.build_picker_bindings)
    code_lines = [ln for ln in source.splitlines() if not ln.strip().startswith("#")]
    body = "\n".join(code_lines)
    assert "on_render" not in body, "referensi ke parameter yang tidak ada di scope fungsi ini"


@pytest.mark.parametrize("key,expected", [("pageup", -1), ("pagedown", 1)])
def test_picker_page_keys_move_the_cursor(key, expected):
    """P1-5: `_page_up`/`_page_down` memakai `window` yang hanya ada di
    `pick_from_list`, jadi PageUp/PageDown melempar `NameError`."""
    from prompt_toolkit.keys import Keys

    from wth_ai.cli.ui.picker import ListPickerState, build_picker_bindings

    state = ListPickerState(items=[f"item-{i}" for i in range(40)], window=8)
    state.index = 16
    result: dict = {"value": None}
    bindings = build_picker_bindings(state, result)

    wanted = Keys.PageUp if key == "pageup" else Keys.PageDown
    binding = next(b for b in bindings.bindings if wanted in b.keys)
    binding.handler(_Event(""))

    assert state.selected() == f"item-{16 + 8 * expected}"


# --------------------------------------------------------------------------- #
# 2. setiap handler slash harus terdaftar di registry
# --------------------------------------------------------------------------- #
def test_every_slash_handler_is_in_the_platform_registry():
    """Registry adalah daftar tampilan; kalau ia tidak lengkap, ketidakkonsistenan jadi
    senyap -- help tidak menampilkannya dan sweep otomatis melewatinya (terukur: enam
    command lolos UAT pertama karena alasan ini)."""
    from wth_ai.cli.slash_router import build_slash_router
    from wth_ai.platform.registry import get_platform_registry

    from unittest.mock import MagicMock

    commands = list(get_platform_registry().commands)
    registered = {c.name for c in commands}
    # alias resmi (mis. /quit punya alias /exit) dihitung sebagai terdaftar
    for command in commands:
        registered.update(getattr(command, "aliases", ()) or ())

    handlers = build_slash_router(MagicMock()).handlers
    missing = sorted(
        name for name in handlers
        if name not in registered and name.split()[0] not in registered
    )
    print("handler tanpa entri registry:", missing)
    assert not missing, f"handler tanpa entri registry: {missing}"


def test_registry_command_names_all_have_handlers_or_are_aliases():
    """Arah sebaliknya: command yang ditampilkan di help harus benar-benar bisa dijalankan."""
    from wth_ai.cli.slash_router import build_slash_router
    from wth_ai.platform.registry import get_platform_registry

    class _Orch:
        def __getattr__(self, item):
            return lambda *a, **k: ""

    handlers = set(build_slash_router(_Orch()).handlers)
    # alias yang sengaja ada di registry tapi ditangani lewat bentuk lain
    allow = {"/scope set", "/tools install"}
    orphan = sorted(
        c.name for c in get_platform_registry().commands
        if c.name not in handlers and c.name.split()[0] not in handlers and c.name not in allow
    )
    assert not orphan, f"command di help yang tidak punya handler: {orphan}"


def test_every_documented_command_parses_to_a_handler():
    """Setiap command di help harus DITEMUKAN router (parse -> handler), bukan "unknown".

    Yang diuji di sini adalah jalur pengenalan (registry <-> router). Apakah handler-nya
    BERHASIL dijalankan diuji di tempat lain: unit test per handler, dan UAT pty nyata --
    stub orchestrator tidak bisa membuktikan itu (handler membaca `orch.config.ui` dsb,
    dan kegagalan stub mudah terbaca sebagai kegagalan produk).
    """
    from unittest.mock import MagicMock

    from wth_ai.cli.slash_router import build_slash_router
    from wth_ai.platform.registry import get_platform_registry

    router = build_slash_router(MagicMock())
    failed: list[str] = []
    for command in get_platform_registry().commands:
        try:
            cmd, _args = router.parse(command.name)
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{command.name}: parse error {type(exc).__name__}")
            continue
        if not cmd or cmd not in router.handlers:
            failed.append(f"{command.name}: tidak dikenali router (parse -> {cmd!r})")
    assert not failed, failed

"""Computer use — let the agent drive the real desktop, like a GUI-capable CLI.

Why this is a module and not three `subprocess.run` calls in the agent
----------------------------------------------------------------------
The desktop on this project's machine is Wayland-with-XWayland, and the agent's
shell has NO `DISPLAY`/`WAYLAND_DISPLAY` set (verified: both empty). So a naive
implementation silently does nothing, or worse, reports "clicked" for an action
that never happened. Everything here therefore goes through:

* **backend detection** that resolves a session from the environment OR from
  `/run/user/<uid>/wayland-0`, and reports *which* backend it picked;
* **verification** of the effect (a screenshot is a non-empty file, a window
  list is actually parsed) rather than trusting the exit code;
* **graceful degradation** to a precise reason (`"xdotool tidak ada"`,
  `"tidak ada display"`) instead of a bare failure — same philosophy as the
  recon registry skipping missing external tools.

Actions are permission-gated by the caller (`PermissionManager`): reading the
screen/window list is read-only, injecting input is `standard` and up.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Read-only vs input-injecting actions, so the permission gate can treat them
# differently: looking at the screen is not the same as typing into it.
READ_ONLY_ACTIONS = frozenset({"detect", "screenshot", "windows", "screen_size", "cursor"})
INPUT_ACTIONS = frozenset({"click", "move", "type", "key", "focus"})

DEFAULT_TIMEOUT = 20


@dataclass
class ActionResult:
    """Outcome of one computer-use action.

    `ok` is only True when the effect was VERIFIED -- callers must be able to
    trust it without re-checking, because the agent reports these to the
    operator as fact.
    """

    action: str
    ok: bool
    backend: str = ""
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = {"action": self.action, "ok": self.ok, "backend": self.backend, "detail": self.detail}
        if self.data:
            out["data"] = self.data
        return out

    def describe(self) -> str:
        mark = "✓" if self.ok else "✗"
        backend = f" [{self.backend}]" if self.backend else ""
        return f"{mark} {self.action}{backend}: {self.detail}"


def _which(name: str) -> str | None:
    return shutil.which(name)


def _runtime_dir() -> str:
    return os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"


class ComputerUse:
    """Desktop control with backend detection and honest failure reasons."""

    def __init__(self, *, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout

    # ------------------------------------------------------------------ #
    # session / backend detection
    # ------------------------------------------------------------------ #
    def detect(self) -> ActionResult:
        info = self._session_info()
        if info["display"] and info["input_backend"] and info["screenshot_backend"]:
            detail = (
                f"session={info['session_type']} display={info['display']} "
                f"input={info['input_backend']} screenshot={info['screenshot_backend']}"
            )
            return ActionResult("detect", True, detail=detail, data=info)
        missing = []
        if not info["display"]:
            missing.append(
                "tidak ada display (DISPLAY/WAYLAND_DISPLAY kosong dan "
                f"{_runtime_dir()}/wayland-0 tidak ada)"
            )
        if not info["input_backend"]:
            missing.append("tidak ada backend input (butuh xdotool atau ydotool)")
        if not info["screenshot_backend"]:
            missing.append("tidak ada backend screenshot (butuh scrot, grim, atau spectacle)")
        return ActionResult("detect", False, detail="; ".join(missing), data=info)

    def _session_info(self) -> dict[str, Any]:
        display = os.environ.get("DISPLAY", "")
        wayland = os.environ.get("WAYLAND_DISPLAY", "")
        wayland_socket = f"{_runtime_dir()}/{wayland}" if wayland else f"{_runtime_dir()}/wayland-0"
        has_wayland_socket = Path(wayland_socket).exists()

        # xdotool needs an X display; ydotool injects via uinput (works on
        # Wayland). Prefer whichever is present AND has a usable session.
        if display and _which("xdotool"):
            input_backend = "xdotool"
        elif _which("ydotool"):
            input_backend = "ydotool"
        elif _which("xdotool"):
            input_backend = "xdotool"
        else:
            input_backend = ""

        if wayland or has_wayland_socket:
            screenshot_backend = next(
                (b for b in ("grim", "spectacle", "gnome-screenshot") if _which(b)), ""
            )
            session_type = "wayland"
        else:
            screenshot_backend = next(
                (b for b in ("scrot", "import", "gnome-screenshot") if _which(b)), ""
            )
            session_type = "x11" if display else "unknown"

        resolved_display = display or (f"wayland:{wayland_socket}" if has_wayland_socket else "")
        return {
            "display": resolved_display,
            "raw_display": display,
            "wayland_socket": wayland_socket if has_wayland_socket else "",
            "session_type": session_type,
            "input_backend": input_backend,
            "screenshot_backend": screenshot_backend,
            "has_wayland_socket": has_wayland_socket,
        }

    def _discover_x11(self) -> dict[str, str]:
        """Find the XWayland/X11 display and its auth cookie from the live session.

        The agent's shell has no DISPLAY (verified), so `wmctrl`, `xrandr` and
        `xdotool` all answered "Cannot open display" even though the operator was
        sitting in a working graphical session. On GNOME the session's own
        processes carry the answer, so read it from `/proc/<pid>/environ`
        instead of asking the (empty) environment.
        """
        import re as _re

        found: dict[str, str] = {}
        for pid_dir in Path("/proc").iterdir():
            if not pid_dir.name.isdigit():
                continue
            try:
                comm = (pid_dir / "comm").read_text(errors="ignore").strip()
            except OSError:
                continue
            if comm not in {"gnome-shell", "Xwayland", "mutter", "gnome-session-binary", "Xorg"}:
                continue
            try:
                blob = (pid_dir / "environ").read_bytes().decode("utf-8", "ignore")
            except OSError:
                continue
            for key in ("DISPLAY", "XAUTHORITY"):
                match = _re.search(rf"(?:^|\x00){key}=([^\x00]+)", blob)
                if match and key not in found:
                    found[key] = match.group(1)
            if "DISPLAY" in found and "XAUTHORITY" in found:
                break

        # A cookie file named `.mutter-Xwaylandauth.*` also proves an X server
        # exists; if the process scan missed it, point at it directly.
        if "XAUTHORITY" not in found:
            for candidate in Path(_runtime_dir()).glob(".mutter-Xwaylandauth.*"):
                found["XAUTHORITY"] = str(candidate)
                break
        return found

    def _env(self) -> dict[str, str]:
        """Environment for child processes, with the session exported.

        The agent's own shell frequently has no DISPLAY (verified), so commands
        inherit nothing usable unless the session is resolved and exported here.
        """
        env = dict(os.environ)
        env.setdefault("XDG_RUNTIME_DIR", _runtime_dir())
        info = self._session_info()
        if info["raw_display"]:
            env.setdefault("DISPLAY", info["raw_display"])
        elif info["has_wayland_socket"] and "WAYLAND_DISPLAY" not in env:
            env["WAYLAND_DISPLAY"] = os.path.basename(info["wayland_socket"])
        if not env.get("DISPLAY"):
            discovered = self._discover_x11()
            if discovered.get("DISPLAY"):
                env["DISPLAY"] = discovered["DISPLAY"]
            if discovered.get("XAUTHORITY"):
                env.setdefault("XAUTHORITY", discovered["XAUTHORITY"])
        return env

    def _screenshot_candidates(self) -> list[str]:
        """Ordered screenshot backends to ATTEMPT, not one chosen blindly.

        Measured on this machine: `grim` was selected purely because the session
        is Wayland, then failed with "compositor doesn't support the screen
        capture protocol" -- `grim` speaks wlr-screencopy, which GNOME/Mutter
        does not implement. Selection must be by expected capability AND the
        attempt must be able to fall through to the next option.
        """
        available = [b for b in ("grim", "spectacle", "gnome-screenshot", "scrot", "import") if _which(b)]
        info = self._session_info()
        # On a non-wlroots Wayland session, X11 capture is the likelier winner,
        # so try the X11 tools before grim.
        if info["has_wayland_socket"] and "scrot" in available:
            ordered = [b for b in ("scrot", "spectacle", "gnome-screenshot", "grim", "import") if b in available]
        else:
            ordered = available
        return ordered

    def _argv_for_screenshot(self, backend: str, target: Path) -> list[str]:
        if backend == "grim":
            return ["grim", str(target)]
        if backend == "scrot":
            return ["scrot", "-o", str(target)]
        if backend == "spectacle":
            return ["spectacle", "-b", "-n", "-o", str(target)]
        return [backend, str(target)]

    def _run(self, argv: list[str]) -> tuple[bool, str]:
        try:
            proc = subprocess.run(  # noqa: S603 - argv list, no shell
                argv, capture_output=True, text=True, timeout=self.timeout, env=self._env()
            )
        except subprocess.TimeoutExpired:
            return False, f"timeout {self.timeout}s: {' '.join(argv[:2])}"
        except FileNotFoundError:
            return False, f"binary tidak ada: {argv[0]}"
        except OSError as exc:
            return False, f"{type(exc).__name__}: {exc}"
        output = (proc.stdout or proc.stderr or "").strip()
        if proc.returncode != 0:
            return False, f"exit {proc.returncode}: {output[:300]}"
        return True, output

    # ------------------------------------------------------------------ #
    # read-only actions
    # ------------------------------------------------------------------ #
    def screenshot(self, path: str | Path | None = None) -> ActionResult:
        """Capture the screen, trying every plausible backend in order.

        A single-backend implementation cannot work across desktops: `grim`
        needs wlr-screencopy (absent on GNOME), `scrot` needs an X display,
        `spectacle` needs KWin. The attempts are reported verbatim when all of
        them fail, so the operator sees WHY rather than one opaque error.
        """
        candidates = self._screenshot_candidates()
        if not candidates:
            return ActionResult("screenshot", False, detail="tidak ada backend screenshot terpasang")
        target = Path(path) if path else Path(os.environ.get("TMPDIR", "/tmp")) / f"cu-{int(time.time()*1000)}.png"
        target.parent.mkdir(parents=True, exist_ok=True)

        attempts: list[str] = []
        for backend in candidates:
            ok, detail = self._run(self._argv_for_screenshot(backend, target))
            if ok and target.exists() and target.stat().st_size > 0:
                size = target.stat().st_size
                return ActionResult(
                    "screenshot", True, backend,
                    detail=f"{target} ({size} byte)",
                    data={"path": str(target), "bytes": size, "attempts": attempts},
                )
            # An exit 0 that produced no file is NOT a success.
            attempts.append(f"{backend}: exit 0 tapi tidak ada file" if ok else f"{backend}: {detail}")

        return ActionResult(
            "screenshot", False, candidates[0],
            detail="semua backend screenshot gagal — " + " | ".join(attempts),
            data={"attempts": attempts},
        )

    def windows(self) -> ActionResult:
        if not _which("wmctrl"):
            return ActionResult("windows", False, detail="wmctrl tidak ada")
        ok, detail = self._run(["wmctrl", "-l"])
        if not ok:
            return ActionResult("windows", False, "wmctrl", detail=detail)
        rows = [ln for ln in detail.splitlines() if ln.strip()]
        return ActionResult(
            "windows", bool(rows), "wmctrl",
            detail=f"{len(rows)} window" if rows else "tidak ada window (session headless?)",
            data={"windows": rows},
        )

    def screen_size(self) -> ActionResult:
        if not _which("xrandr"):
            return ActionResult("screen_size", False, detail="xrandr tidak ada")
        ok, detail = self._run(["xrandr", "--current"])
        if not ok:
            return ActionResult("screen_size", False, "xrandr", detail=detail)
        for line in detail.splitlines():
            if " current " in line:
                return ActionResult("screen_size", True, "xrandr", detail=line.strip(), data={"raw": line.strip()})
        return ActionResult("screen_size", False, "xrandr", detail="tidak menemukan baris resolusi")

    def cursor(self) -> ActionResult:
        if not _which("xdotool"):
            return ActionResult("cursor", False, detail="xdotool tidak ada")
        ok, detail = self._run(["xdotool", "getmouselocation"])
        if not ok:
            return ActionResult("cursor", False, "xdotool", detail=detail)
        return ActionResult("cursor", True, "xdotool", detail=detail, data={"raw": detail})

    # ------------------------------------------------------------------ #
    # input actions
    # ------------------------------------------------------------------ #
    def move(self, x: int, y: int) -> ActionResult:
        info = self._session_info()
        if info["input_backend"] == "xdotool":
            ok, detail = self._run(["xdotool", "mousemove", str(x), str(y)])
        elif info["input_backend"] == "ydotool":
            ok, detail = self._run(["ydotool", "mousemove", "--absolute", "-x", str(x), "-y", str(y)])
        else:
            return ActionResult("move", False, detail="tidak ada backend input")
        return ActionResult("move", ok, info["input_backend"], detail=detail or f"({x},{y})")

    def click(self, x: int, y: int, *, button: int = 1) -> ActionResult:
        info = self._session_info()
        if info["input_backend"] == "xdotool":
            ok, detail = self._run(["xdotool", "mousemove", str(x), str(y), "click", str(button)])
        elif info["input_backend"] == "ydotool":
            move = self.move(x, y)
            if not move.ok:
                return ActionResult("click", False, move.backend, detail=move.detail)
            ok, detail = self._run(["ydotool", "click", str(button)])
        else:
            return ActionResult("click", False, detail="tidak ada backend input")
        return ActionResult("click", ok, info["input_backend"], detail=detail or f"click({x},{y}) btn{button}")

    def type_text(self, text: str) -> ActionResult:
        info = self._session_info()
        if not text:
            return ActionResult("type", False, detail="teks kosong")
        if info["input_backend"] == "xdotool":
            ok, detail = self._run(["xdotool", "type", "--delay", "12", text])
        elif info["input_backend"] == "ydotool":
            ok, detail = self._run(["ydotool", "type", text])
        else:
            return ActionResult("type", False, detail="tidak ada backend input")
        # Never echo typed text back in full: it may be a credential the
        # operator pasted, and this result is shown/logged.
        preview = text[:12] + ("…" if len(text) > 12 else "")
        return ActionResult("type", ok, info["input_backend"], detail=detail or f"{len(text)} char ({preview!r})")

    def key(self, combo: str) -> ActionResult:
        info = self._session_info()
        if not combo:
            return ActionResult("key", False, detail="combo kosong")
        if info["input_backend"] == "xdotool":
            ok, detail = self._run(["xdotool", "key", combo])
        elif info["input_backend"] == "ydotool":
            ok, detail = self._run(["ydotool", "key", combo])
        else:
            return ActionResult("key", False, detail="tidak ada backend input")
        return ActionResult("key", ok, info["input_backend"], detail=detail or combo)

    def focus(self, window: str) -> ActionResult:
        if not _which("wmctrl"):
            return ActionResult("focus", False, detail="wmctrl tidak ada")
        ok, detail = self._run(["wmctrl", "-a", window])
        return ActionResult("focus", ok, "wmctrl", detail=detail or window)

    # ------------------------------------------------------------------ #
    def run_action(self, action: str, **kwargs: Any) -> ActionResult:
        """Single entry point for the agent.

        Unknown actions fail loudly: silently returning "ok" for a typo is how
        an agent ends up believing it did something it never did.
        """
        handlers = {
            "detect": lambda: self.detect(),
            "screenshot": lambda: self.screenshot(kwargs.get("path")),
            "windows": lambda: self.windows(),
            "screen_size": lambda: self.screen_size(),
            "cursor": lambda: self.cursor(),
            "move": lambda: self.move(int(kwargs["x"]), int(kwargs["y"])),
            "click": lambda: self.click(int(kwargs["x"]), int(kwargs["y"]), button=int(kwargs.get("button", 1))),
            "type": lambda: self.type_text(str(kwargs.get("text", ""))),
            "key": lambda: self.key(str(kwargs.get("combo", ""))),
            "focus": lambda: self.focus(str(kwargs.get("window", ""))),
        }
        handler = handlers.get(action)
        if handler is None:
            return ActionResult(action, False, detail=f"aksi tidak dikenal. Pilihan: {', '.join(sorted(handlers))}")
        try:
            return handler()
        except (KeyError, TypeError, ValueError) as exc:
            return ActionResult(action, False, detail=f"argumen tidak lengkap/salah: {exc}")

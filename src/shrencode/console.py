"""Terminal output.

Human-readable progress and diagnostics go to stderr. Per-file results go to
stdout, unless --json is active, in which case stdout carries only JSON lines.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys

_RESET = "\x1b[0m"


def _enable_windows_vt(stream) -> bool:
    if os.name != "nt":
        return True
    try:
        import ctypes
        import msvcrt

        handle = msvcrt.get_osfhandle(stream.fileno())
        kernel32 = ctypes.windll.kernel32
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except (OSError, ValueError, AttributeError):
        return False


class Console:
    def __init__(self) -> None:
        self.verbosity = 1
        self.color = False
        self.json = False
        # pythonw / gui-scripts entry point: no streams at all
        self.headless = sys.stderr is None
        self._progress_shown = False

    def setup(self, verbosity: int = 1, color: str = "auto", json_mode: bool = False) -> None:
        self.verbosity = verbosity
        self.json = json_mode
        for stream in (sys.stdout, sys.stderr):
            if stream is not None:
                with contextlib.suppress(AttributeError, ValueError):
                    stream.reconfigure(encoding="utf-8", errors="replace")
        self.color = self._want_color(color)

    def _want_color(self, mode: str) -> bool:
        if self.headless or mode == "never":
            return False
        if mode == "always":
            return _enable_windows_vt(sys.stderr) or True
        if os.environ.get("NO_COLOR"):
            return False
        if os.environ.get("FORCE_COLOR"):
            return _enable_windows_vt(sys.stderr) or True
        if not sys.stderr.isatty() or os.environ.get("TERM") == "dumb":
            return False
        return _enable_windows_vt(sys.stderr)

    def _paint(self, code: str, text: str) -> str:
        return f"\x1b[{code}m{text}{_RESET}" if self.color else text

    def dim(self, text: str) -> str:
        return self._paint("2", text)

    def bold(self, text: str) -> str:
        return self._paint("1", text)

    def green(self, text: str) -> str:
        return self._paint("32", text)

    def yellow(self, text: str) -> str:
        return self._paint("33", text)

    def red(self, text: str) -> str:
        return self._paint("31", text)

    def _write(self, stream, text: str) -> None:
        if stream is None:
            return
        self.clear_progress()
        print(text, file=stream, flush=True)

    def info(self, text: str = "") -> None:
        if self.verbosity >= 1:
            self._write(sys.stderr, text)

    def detail(self, text: str) -> None:
        if self.verbosity >= 2:
            self._write(sys.stderr, text)

    def debug(self, text: str) -> None:
        if self.verbosity >= 3:
            self._write(sys.stderr, text)

    def warn(self, text: str) -> None:
        if self.verbosity >= 0:
            self._write(sys.stderr, self.yellow(text))

    def error(self, text: str) -> None:
        self._write(sys.stderr, self.red("error: ") + text)
        if self.headless:
            message_box(text, error=True)

    def result(self, text: str) -> None:
        """A per-file outcome or final summary."""
        if self.verbosity < 0:
            return
        self._write(sys.stderr if self.json else sys.stdout, text)

    def emit(self, record: dict) -> None:
        if self.json and sys.stdout is not None:
            self.clear_progress()
            print(json.dumps(record, ensure_ascii=False), file=sys.stdout, flush=True)

    def progress(self, text: str) -> None:
        if self.verbosity < 1 or sys.stderr is None or not sys.stderr.isatty():
            return
        width = 118
        sys.stderr.write("\r" + text[:width].ljust(width))
        sys.stderr.flush()
        self._progress_shown = True

    def clear_progress(self) -> None:
        if self._progress_shown and sys.stderr is not None:
            sys.stderr.write("\r" + " " * 118 + "\r")
            sys.stderr.flush()
            self._progress_shown = False


def message_box(text: str, error: bool = False) -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, "shrencode", 0x10 if error else 0x40)
    except (OSError, AttributeError):
        pass


console = Console()


def fmt_size(n: float | None) -> str:
    if n is None:
        return "?"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} TB"


def fmt_rate(bps: float | None) -> str:
    if not bps:
        return "? Mb/s"
    return f"{bps / 1e6:.1f} Mb/s" if bps >= 1e6 else f"{bps / 1e3:.0f} kb/s"


def fmt_duration(seconds: float | None) -> str:
    if seconds is None or seconds != seconds or seconds in (float("inf"), float("-inf")) or seconds < 0:
        return "?"
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

"""Finding and running ffmpeg, ffprobe and video-compare."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

EXE = ".exe" if os.name == "nt" else ""


def app_dir() -> Path:
    """Folder of the running program: the exe when frozen, else the package."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _resolve(name: str, hints: list[str | None]) -> str | None:
    for hint in hints:
        if not hint:
            continue
        p = Path(hint)
        if p.is_dir():
            p = p / (name + EXE)
        if p.is_file():
            return str(p)
    for folder in (app_dir(), app_dir() / "bin"):
        p = folder / (name + EXE)
        if p.is_file():
            return str(p)
    return shutil.which(name)


def run(cmd: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


class Tools:
    def __init__(self, ffmpeg: str | None = None):
        self.ffmpeg = _resolve("ffmpeg", [ffmpeg, os.environ.get("FFMPEG_PATH")])
        sibling = None
        if self.ffmpeg:
            candidate = Path(self.ffmpeg).with_name("ffprobe" + EXE)
            sibling = str(candidate) if candidate.is_file() else None
        self.ffprobe = _resolve("ffprobe", [sibling])
        self._encoders: set[str] | None = None
        self._filters: set[str] | None = None
        self._svt_params: dict[str, bool] = {}

    def encoders(self) -> set[str]:
        if self._encoders is None:
            out = run([self.ffmpeg, "-hide_banner", "-encoders"]).stdout
            self._encoders = set(re.findall(r"^\s*[VAS][A-Z.]{5}\s+(\S+)", out, re.M))
        return self._encoders

    def filters(self) -> set[str]:
        if self._filters is None:
            out = run([self.ffmpeg, "-hide_banner", "-filters"]).stdout
            self._filters = set(re.findall(r"^\s*[A-Z.]{2,3}\s+(\S+)\s+\S*->\S*", out, re.M))
        return self._filters

    def svt_accepts(self, key: str, value: str) -> bool:
        """SVT-AV1 builds differ in which parameters they know; test once per run."""
        param = f"{key}={value}"
        if param not in self._svt_params:
            cmd = [
                self.ffmpeg,
                "-hide_banner",
                "-v",
                "error",
                "-nostdin",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=s=192x128:r=24:d=0.25",
                "-c:v",
                "libsvtav1",
                "-preset",
                "12",
                "-pix_fmt",
                "yuv420p10le",
                "-svtav1-params",
                param,
                "-f",
                "null",
                "-",
            ]
            try:
                self._svt_params[param] = run(cmd, timeout=60).returncode == 0
            except subprocess.TimeoutExpired:
                self._svt_params[param] = False
        return self._svt_params[param]


def find_video_compare(hint: str | None) -> str | None:
    hints = [hint, os.environ.get("VIDEO_COMPARE_PATH")]
    found = _resolve("video-compare", hints)
    if found:
        return found
    p = app_dir() / "video-compare" / ("video-compare" + EXE)
    return str(p) if p.is_file() else None

"""Opening source and output side by side in video-compare."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

from .console import console
from .plan import OUTPUT_INFIX
from .probe import MediaInfo, probe
from .tools import Tools, find_video_compare

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


def screen_size() -> tuple[int, int] | None:
    if os.name != "nt":
        return None
    try:
        import ctypes

        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except (OSError, AttributeError):
        return None


def is_output(info: MediaInfo) -> bool:
    if "shrencode_source" in info.tags:
        return True
    return any(info.path.stem.lower().endswith(f".{infix}") for infix in OUTPUT_INFIX.values())


def match_scan(left: MediaInfo, right: MediaInfo) -> list[str]:
    """Deinterlace an interlaced original next to a progressive encode."""
    if left.interlaced_flag and not right.interlaced_flag:
        doubled = left.fps and right.fps and right.fps > left.fps * 1.5
        return [f"bwdif=mode={'send_field' if doubled else 'send_frame'}:parity=auto"]
    return []


def launch(
    program: str,
    left: Path,
    rights: list[Path],
    left_filters: list[str],
    extra: str | None,
    width: int,
    height: int,
) -> bool:
    cmd = [program]
    screen = screen_size()
    # Keep pixels 1:1 unless the video is bigger than the screen.
    too_big = (width > screen[0] * 0.95 or height > screen[1] * 0.88) if screen else width > 1920
    if too_big:
        cmd.append("-W")
    if left_filters:
        cmd += ["-l", ",".join(left_filters)]
    if extra:
        cmd += shlex.split(extra, posix=os.name != "nt")
    cmd += [str(left), *map(str, rights)]
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(cmd, **kwargs)
    except OSError as e:
        console.error(f"could not start video-compare: {e}")
        return False
    console.detail("      compare: " + subprocess.list2cmdline(cmd))
    return True


def run_compare(args) -> int:
    tools = Tools(args.ffmpeg)
    if not tools.ffprobe:
        console.error("ffprobe not found; set FFMPEG_PATH or put ffmpeg on PATH")
        return 1
    program = find_video_compare(args.video_compare)
    if not program:
        console.error("video-compare not found; put it on PATH or set VIDEO_COMPARE_PATH")
        return 1
    files = [Path(f) for f in args.files]
    missing = [str(f) for f in files if not f.is_file()]
    if missing:
        console.error("not found: " + ", ".join(missing))
        return 1
    try:
        infos = [probe(tools, f) for f in files]
    except RuntimeError as e:
        console.error(str(e))
        return 1
    # Explorer's selection order is arbitrary: originals go left, outputs right.
    ordered = [i for i in infos if not is_output(i)] + [i for i in infos if is_output(i)]
    left, rights = ordered[0], ordered[1:]
    ok = launch(
        program,
        left.path,
        [r.path for r in rights],
        match_scan(left, rights[0]),
        args.args,
        max(left.width, rights[0].width),
        max(left.height, rights[0].height),
    )
    return 0 if ok else 1

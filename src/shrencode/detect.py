"""Content analysis that ffprobe metadata can't answer."""

from __future__ import annotations

import re

from .probe import MediaInfo
from .tools import Tools, run

# Luma PSNR between the source and a temporally denoised copy, in dB.
GRAIN_LEVELS = [(44.5, "clean"), (41.0, "light"), (37.5, "grainy")]
BWDIF = "bwdif=mode=send_frame"


def spread(duration: float | None, count: int, length: float) -> list[float]:
    """Start times of `count` windows spread evenly through the file."""
    if not duration or duration <= length * 1.5:
        return [0.0]
    return [max(0.0, duration * (i + 1) / (count + 1) - length / 2) for i in range(count)]


def interlacing(tools: Tools, info: MediaInfo) -> tuple[bool, str]:
    if info.interlaced_flag:
        return True, f"field order {info.field_order}"
    if info.field_order == "progressive":
        return False, "flagged progressive"
    if info.height > 1088 or "idet" not in tools.filters():
        return False, "assumed progressive"
    start = (info.duration or 0) * 0.4
    r = run(
        [
            tools.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-ss",
            f"{start:.2f}",
            "-i",
            str(info.path),
            "-map",
            f"0:{info.vindex}",
            "-frames:v",
            "300",
            "-vf",
            "idet",
            "-an",
            "-f",
            "null",
            "-",
        ],
        timeout=300,
    )
    m = re.findall(r"Multi frame detection:\s*TFF:\s*(\d+)\s*BFF:\s*(\d+)\s*Progressive:\s*(\d+)", r.stderr)
    if not m:
        return False, "idet inconclusive"
    tff, bff, prog = map(int, m[-1])
    fields = tff + bff
    return fields > 50 and fields > 2 * prog, f"idet {fields} interlaced / {prog} progressive"


def grain(tools: Tools, info: MediaInfo, deinterlace: bool) -> tuple[str, float | None]:
    """Estimate grain from how much a temporal-only denoiser changes the picture.

    Grain differs from frame to frame, so temporal denoising removes it while leaving
    static detail alone. Motion also lowers the score, so the calmest sample wins.
    """
    pre = f"{BWDIF}," if deinterlace else ""
    graph = (
        f"[0:{info.vindex}]{pre}crop='min(iw,1280)':'min(ih,720)',format=yuv420p,split[a][b];"
        f"[b]hqdn3d=0:0:20:20[d];[a][d]psnr"
    )
    scores = []
    for t in spread(info.duration, 4, 2.0):
        r = run(
            [
                tools.ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-ss",
                f"{t:.2f}",
                "-i",
                str(info.path),
                "-filter_complex",
                graph,
                "-frames:v",
                "24",
                "-an",
                "-f",
                "null",
                "-",
            ],
            timeout=300,
        )
        m = re.findall(r"PSNR y:([\d.]+|inf)", r.stderr)
        if m:
            scores.append(99.0 if m[-1] == "inf" else float(m[-1]))
    if not scores:
        return "light", None
    best = max(scores)
    for threshold, level in GRAIN_LEVELS:
        if best >= threshold:
            return level, best
    return "heavy", best


def black_bars(tools: Tools, info: MediaInfo, deinterlace: bool) -> tuple[int, int, int, int] | None:
    pre = f"{BWDIF}," if deinterlace else ""
    boxes = []
    for t in spread(info.duration, 6, 2.0):
        # 8-bit first, so the threshold means the same thing for 10-bit sources
        r = run(
            [
                tools.ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-ss",
                f"{t:.2f}",
                "-i",
                str(info.path),
                "-map",
                f"0:{info.vindex}",
                "-frames:v",
                "30",
                "-vf",
                f"{pre}format=yuv420p,cropdetect=limit=24:round=2:reset=0",
                "-an",
                "-f",
                "null",
                "-",
            ],
            timeout=300,
        )
        m = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", r.stderr)
        if m:
            boxes.append(tuple(map(int, m[-1])))
    if len(boxes) < 2:
        return None
    x = min(b[2] for b in boxes)
    y = min(b[3] for b in boxes)
    w = max(b[2] + b[0] for b in boxes) - x
    h = max(b[3] + b[1] for b in boxes) - y
    if w * h < info.width * info.height * 0.4:
        return None  # a dark scene, not bars
    if w >= info.width * 0.99 and h >= info.height * 0.99:
        return None
    return (w - w % 2, h - h % 2, x, y)

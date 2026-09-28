"""Reading stream properties with ffprobe."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .tools import Tools, run

INTERLACED_ORDERS = {"tt", "bb", "tb", "bt"}


@dataclass
class AudioTrack:
    index: int
    codec: str
    channels: int
    layout: str
    sample_rate: int
    bitrate: float | None


@dataclass
class SubtitleTrack:
    index: int
    codec: str


@dataclass
class MediaInfo:
    path: Path
    size: int
    duration: float | None
    vindex: int
    vcodec: str
    profile: str
    width: int
    height: int
    fps: float | None
    vfr: bool
    pix_fmt: str
    bit_depth: int
    chroma: str
    alpha: bool
    color_primaries: str
    color_transfer: str
    color_space: str
    color_range: str
    field_order: str
    vbitrate: float | None
    timecode: str | None
    tags: dict[str, str] = field(default_factory=dict)
    audio: list[AudioTrack] = field(default_factory=list)
    subtitles: list[SubtitleTrack] = field(default_factory=list)
    has_attachments: bool = False
    hdr: str = "SDR"
    master_display_x265: str | None = None
    master_display_svt: str | None = None
    max_cll: tuple[int, int] | None = None
    dovi_profile: int | None = None

    @property
    def interlaced_flag(self) -> bool:
        return self.field_order in INTERLACED_ORDERS

    @property
    def bpp(self) -> float | None:
        if self.vbitrate and self.fps and self.width and self.height:
            return self.vbitrate / (self.width * self.height * self.fps)
        return None

    def summary(self) -> str:
        fps = f"{self.fps:.3f}".rstrip("0").rstrip(".") if self.fps else "?"
        scan = "i" if self.interlaced_flag else "p"
        hdr = f" {self.hdr}" if self.hdr != "SDR" else ""
        return f"{self.vcodec} {self.width}x{self.height} {fps}{scan} {self.chroma} {self.bit_depth}-bit{hdr}"


def ratio(value) -> float | None:
    if value is None:
        return None
    try:
        text = str(value)
        if "/" in text:
            num, den = text.split("/", 1)
            den = float(den)
            return float(num) / den if den else None
        return float(text)
    except ValueError:
        return None


def as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def pixel_depth(pix_fmt: str, raw_bits) -> int:
    bits = as_int(raw_bits)
    if bits and bits > 8:
        return bits
    m = re.search(r"(\d+)(le|be)$", pix_fmt or "")
    if m:
        v = int(m.group(1))
        if v in (9, 10, 12, 14, 16):
            return v
        if v in (48, 64):
            return 16
    return 8


def chroma_of(pix_fmt: str) -> str:
    pf = pix_fmt or ""
    if pf.startswith(("rgb", "bgr", "gbr", "argb", "abgr", "x2rgb", "x2bgr")) or "444" in pf:
        return "4:4:4"
    if "422" in pf or pf.startswith(("uyvy", "yuyv", "yvyu", "y210", "v210")):
        return "4:2:2"
    if "411" in pf:
        return "4:1:1"
    if "420" in pf or pf.startswith(("nv12", "nv21", "p010", "p016")):
        return "4:2:0"
    if pf.startswith("gray"):
        return "gray"
    return "?"


def resolution_class(width: int, height: int) -> str:
    """Bucket by pixel count so portrait and DCI frames land where they should."""
    px = width * height
    if px <= 720 * 576 * 1.05:
        return "SD"
    if px <= 1280 * 720 * 1.1:
        return "720p"
    if px <= 2048 * 1152:
        return "1080p"
    if px <= 2560 * 1600:
        return "1440p"
    if px <= 4096 * 2304:
        return "4K"
    return "6K+"


def probe(tools: Tools, path: Path) -> MediaInfo:
    r = run(
        [tools.ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        timeout=120,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "ffprobe failed")
    data = json.loads(r.stdout or "{}")
    streams = data.get("streams", [])
    fmt = data.get("format", {})

    videos = [
        s
        for s in streams
        if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
    ]
    if not videos:
        raise RuntimeError("no video stream")
    v = max(videos, key=lambda s: (s.get("width") or 0) * (s.get("height") or 0))

    duration = ratio(fmt.get("duration")) or ratio(v.get("duration"))
    size = path.stat().st_size
    avg, real = ratio(v.get("avg_frame_rate")), ratio(v.get("r_frame_rate"))
    vtags = {k.lower(): val for k, val in (v.get("tags") or {}).items()}
    ftags = {k.lower(): val for k, val in (fmt.get("tags") or {}).items()}
    pix_fmt = v.get("pix_fmt") or ""

    audio = []
    for s in streams:
        if s.get("codec_type") != "audio":
            continue
        stags = {k.lower(): val for k, val in (s.get("tags") or {}).items()}
        audio.append(
            AudioTrack(
                index=s["index"],
                codec=s.get("codec_name", "?"),
                channels=as_int(s.get("channels")) or 2,
                layout=s.get("channel_layout") or "",
                sample_rate=as_int(s.get("sample_rate")) or 48000,
                bitrate=ratio(s.get("bit_rate")) or ratio(stags.get("bps")),
            )
        )

    timecode = vtags.get("timecode") or ftags.get("timecode")
    if not timecode:
        for s in streams:
            if s.get("codec_type") == "data" and (s.get("tags") or {}).get("timecode"):
                timecode = s["tags"]["timecode"]
                break

    vbitrate = ratio(v.get("bit_rate")) or ratio(vtags.get("bps"))
    if not vbitrate and duration:
        total = size * 8 / duration
        audio_bits = 0.0
        for a in audio:
            if a.bitrate:
                audio_bits += a.bitrate
            elif a.codec.startswith("pcm_"):
                audio_bits += a.sample_rate * a.channels * (as_int(re.sub(r"\D", "", a.codec)) or 16)
            else:
                audio_bits += 192_000
        vbitrate = max(total - audio_bits, total * 0.1)

    info = MediaInfo(
        path=path,
        size=size,
        duration=duration,
        vindex=v["index"],
        vcodec=v.get("codec_name", "?"),
        profile=v.get("profile") or "",
        width=v.get("width") or 0,
        height=v.get("height") or 0,
        fps=avg or real,
        vfr=bool(avg and real and abs(avg - real) / max(real, 1e-6) > 0.02),
        pix_fmt=pix_fmt,
        bit_depth=pixel_depth(pix_fmt, v.get("bits_per_raw_sample")),
        chroma=chroma_of(pix_fmt),
        alpha=pix_fmt.startswith(("yuva", "rgba", "argb", "bgra", "abgr", "gbrap", "ya")),
        color_primaries=v.get("color_primaries") or "unknown",
        color_transfer=v.get("color_transfer") or "unknown",
        color_space=v.get("color_space") or "unknown",
        color_range=v.get("color_range") or "unknown",
        field_order=v.get("field_order") or "unknown",
        vbitrate=vbitrate,
        timecode=timecode,
        tags=ftags,
        audio=audio,
        subtitles=[
            SubtitleTrack(s["index"], s.get("codec_name", "?"))
            for s in streams
            if s.get("codec_type") == "subtitle"
        ],
        has_attachments=any(s.get("codec_type") == "attachment" for s in streams),
    )
    if info.color_transfer == "smpte2084":
        info.hdr = "HDR10"
    elif info.color_transfer == "arib-std-b67":
        info.hdr = "HLG"

    side_data = list(v.get("side_data_list") or [])
    if info.hdr != "SDR":
        rel = [s for s in streams if s.get("codec_type") == "video"].index(v)
        fr = run(
            [
                tools.ffprobe,
                "-v",
                "error",
                "-select_streams",
                f"v:{rel}",
                "-read_intervals",
                "%+#1",
                "-show_frames",
                "-print_format",
                "json",
                str(path),
            ],
            timeout=120,
        )
        try:
            for frame in json.loads(fr.stdout or "{}").get("frames", [])[:1]:
                side_data += frame.get("side_data_list") or []
        except json.JSONDecodeError:
            pass
    _read_side_data(info, side_data)
    return info


def _read_side_data(info: MediaInfo, side_data: list[dict]) -> None:
    for sd in side_data:
        kind = (sd.get("side_data_type") or "").lower()
        if "dovi" in kind or "dolby vision" in kind:
            info.dovi_profile = as_int(sd.get("dv_profile"))
        elif "mastering display" in kind and "red_x" in sd:
            keys = (
                "red_x",
                "red_y",
                "green_x",
                "green_y",
                "blue_x",
                "blue_y",
                "white_point_x",
                "white_point_y",
                "max_luminance",
                "min_luminance",
            )
            c = {k: ratio(sd.get(k)) for k in keys}
            if None in c.values():
                continue
            # x265 wants chromaticity in 0.00002 units and luminance in 0.0001 units
            q = lambda x: round(x * 50000)  # noqa: E731
            info.master_display_x265 = (
                f"G({q(c['green_x'])},{q(c['green_y'])})B({q(c['blue_x'])},{q(c['blue_y'])})"
                f"R({q(c['red_x'])},{q(c['red_y'])})WP({q(c['white_point_x'])},{q(c['white_point_y'])})"
                f"L({round(c['max_luminance'] * 10000)},{round(c['min_luminance'] * 10000)})"
            )
            info.master_display_svt = (
                f"G({c['green_x']:.4f},{c['green_y']:.4f})B({c['blue_x']:.4f},{c['blue_y']:.4f})"
                f"R({c['red_x']:.4f},{c['red_y']:.4f})WP({c['white_point_x']:.4f},{c['white_point_y']:.4f})"
                f"L({c['max_luminance']:.4f},{c['min_luminance']:.4f})"
            )
        elif "content light" in kind:
            mc, ma = as_int(sd.get("max_content")), as_int(sd.get("max_average"))
            if mc is not None and ma is not None:
                info.max_cll = (mc, ma)

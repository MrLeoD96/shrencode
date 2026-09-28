"""Deciding how, and whether, to encode a file."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

from . import detect
from .probe import MediaInfo, resolution_class
from .tools import Tools

INTERMEDIATE_CODECS = {
    "prores",
    "dnxhd",
    "dnxhr",
    "cfhd",
    "v210",
    "v410",
    "r210",
    "r10k",
    "rawvideo",
    "huffyuv",
    "ffvhuff",
    "ffv1",
    "utvideo",
    "magicyuv",
    "jpeg2000",
    "qtrle",
    "png",
    "tiff",
    "hqx",
    "hq_hqa",
    "cllc",
    "sheervideo",
    "ayuv",
    "y41p",
    "yuv4",
    "v308",
    "v408",
    "dirac",
    "vc2",
    "zlib",
    "lagarith",
    "rpza",
    "exr",
    "dpx",
    "mjpeg",
    "notchlc",
    "hap",
    "prores_raw",
}
LEGACY_CODECS = {
    "mpeg2video",
    "mpeg1video",
    "mpeg4",
    "msmpeg4v1",
    "msmpeg4v2",
    "msmpeg4v3",
    "wmv1",
    "wmv2",
    "wmv3",
    "vc1",
    "h263",
    "flv1",
    "vp6f",
    "vp6",
    "vp8",
    "theora",
    "dvvideo",
    "cinepak",
    "indeo3",
    "indeo4",
    "indeo5",
    "svq1",
    "svq3",
    "rv30",
    "rv40",
}
EFFICIENT_CODECS = {"hevc", "av1", "vp9", "vvc"}

LOSSY_AUDIO = {
    "aac",
    "mp3",
    "ac3",
    "eac3",
    "opus",
    "vorbis",
    "dts",
    "mp2",
    "wmav1",
    "wmav2",
    "wmapro",
    "amr_nb",
    "amr_wb",
    "mp1",
}
COPYABLE_AUDIO = {
    "mp4": {"aac", "mp3", "ac3", "eac3", "opus", "alac", "flac"},
    "mkv": LOSSY_AUDIO | {"flac", "alac", "truehd", "mlp", "wavpack", "tta"},
}
TEXT_SUBTITLES = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"}

CRF_BASE = {
    "x265": {"SD": 18, "720p": 19, "1080p": 20, "1440p": 21, "4K": 22, "6K+": 23},
    "svt-av1": {"SD": 23, "720p": 25, "1080p": 27, "1440p": 28, "4K": 30, "6K+": 32},
}
QUALITY_OFFSET = {
    "x265": {"high": -2, "balanced": 0, "compact": 3},
    "svt-av1": {"high": -4, "balanced": 0, "compact": 5},
}
X265_PRESET = {"slower": "slower", "normal": "slow", "faster": "medium"}
SVT_PRESET = {"SD": 4, "720p": 4, "1080p": 4, "1440p": 5, "4K": 5, "6K+": 6}
SVT_SPEED_OFFSET = {"slower": -1, "normal": 0, "faster": 2}
X265_PSY = {
    "clean": ("1.5", "1.0"),
    "light": ("2.0", "2.0"),
    "grainy": ("2.0", "4.0"),
    "heavy": ("2.0", "6.0"),
}
SVT_FILM_GRAIN = {"clean": 0, "light": 0, "grainy": 8, "heavy": 12}

OPUS_KBPS = {
    "high": {1: 128, 2: 192, 6: 384, 8: 512},
    "balanced": {1: 96, 2: 160, 6: 320, 8: 448},
    "compact": {1: 64, 2: 112, 6: 256, 8: 320},
}
AAC_KBPS = {
    "high": {1: 160, 2: 256, 6: 512, 8: 640},
    "balanced": {1: 128, 2: 224, 6: 448, 8: 576},
    "compact": {1: 96, 2: 160, 6: 320, 8: 448},
}
OUTPUT_INFIX = {"x265": "x265", "svt-av1": "av1"}
LONG_INPUT_SECONDS = 180


@dataclass
class Plan:
    skip: str | None = None
    reason: str = ""
    warnings: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    width: int = 0
    height: int = 0
    rclass: str = ""
    grain: str = ""
    grain_psnr: float | None = None
    interlaced: bool = False
    crf: int = 0
    crf_reason: str = ""
    preset: str = ""
    video_args: list[str] = field(default_factory=list)
    sample_args: list[str] = field(default_factory=list)
    params: str = ""
    audio_args: list[str] = field(default_factory=list)
    audio_maps: list[int] = field(default_factory=list)
    subtitle_args: list[str] = field(default_factory=list)
    subtitle_maps: list[int] = field(default_factory=list)
    container: str = "mkv"
    vmaf: float | None = None

    def set_crf(self, crf: int) -> None:
        self.crf = crf
        i = self.video_args.index("-crf")
        self.video_args[i + 1] = str(crf)

    def encoder_settings(self) -> str:
        return subprocess.list2cmdline(self.video_args)


def worth_encoding(info: MediaInfo, encode_all: bool) -> tuple[bool, str]:
    codec, bpp = info.vcodec, info.bpp
    if encode_all:
        return True, "--encode-all"
    if codec in INTERMEDIATE_CODECS:
        return True, "editing codec"
    if codec in LEGACY_CODECS:
        return True, "legacy codec"
    if codec == "h264":
        if bpp is not None and bpp < 0.06:
            return False, f"h264 at {bpp:.3f} bpp is already efficient"
        return True, "high-bitrate h264"
    if codec in EFFICIENT_CODECS:
        if bpp is not None and bpp >= 0.12:
            return True, f"high-bitrate {codec} at {bpp:.3f} bpp"
        rate = "unknown bitrate" if bpp is None else f"{bpp:.3f} bpp"
        return False, f"{codec} at {rate} is already efficient"
    return True, f"unrecognised codec {codec}"


def container_for(info: MediaInfo, requested: str) -> str:
    if requested != "auto":
        return requested
    return "mp4" if info.path.suffix.lower() in (".mp4", ".m4v") else "mkv"


def keyint(info: MediaInfo, plan: Plan, opts) -> int | None:
    """10 s keyframe interval for long inputs; encoder default otherwise."""
    if not info.duration or info.duration <= LONG_INPUT_SECONDS:
        return None
    fps = (info.fps or 25) * (2 if plan.interlaced and opts.deint_rate == "double" else 1)
    return min(600, round(fps * 10))


def _video_filters(info: MediaInfo, plan: Plan, opts, crop) -> None:
    w, h = info.width, info.height
    if plan.interlaced:
        mode = "send_field" if opts.deint_rate == "double" else "send_frame"
        plan.filters.append(f"bwdif=mode={mode}:parity=auto")
    if crop:
        cw, ch, cx, cy = crop
        plan.filters.append(f"crop={cw}:{ch}:{cx}:{cy}")
        plan.details.append(f"crop: {w}x{h} to {cw}x{ch}")
        w, h = cw, ch
    if opts.max_res and min(w, h) > opts.max_res:
        scale = opts.max_res / min(w, h)
        nw, nh = round(w * scale / 2) * 2, round(h * scale / 2) * 2
        plan.filters.append(f"scale={nw}:{nh}:flags=lanczos")
        plan.details.append(f"scale: {w}x{h} to {nw}x{nh}")
        w, h = nw, nh
    if w % 2 or h % 2:
        plan.filters.append("crop=trunc(iw/2)*2:trunc(ih/2)*2")
        w, h = w - w % 2, h - h % 2
    plan.filters.append("format=yuv420p10le")
    plan.width, plan.height = w, h


def _x265(info: MediaInfo, plan: Plan, opts) -> None:
    p: dict[str, str] = {"aq-mode": "3", "bframes": "8", "rc-lookahead": "40"}
    ki = keyint(info, plan, opts)
    if ki:
        p["keyint"] = str(ki)
        p["min-keyint"] = str(max(1, round(ki / 10)))
    if opts.tune == "animation":
        p.update({"psy-rd": "1.0", "psy-rdoq": "1.0", "aq-strength": "0.8", "deblock": "1,1"})
    else:
        p["deblock"] = "-1,-1"
        p["psy-rd"], p["psy-rdoq"] = X265_PSY[plan.grain]
    if plan.crf <= 21 or plan.grain in ("grainy", "heavy"):
        p["sao"] = "0"
    else:
        p["limit-sao"] = "1"
    if opts.quality == "high":
        p["strong-intra-smoothing"] = "0"
    if info.hdr == "HDR10":
        p["hdr10-opt"] = "1"
        p["repeat-headers"] = "1"
        if info.master_display_x265:
            p["master-display"] = info.master_display_x265
        if info.max_cll:
            p["max-cll"] = f"{info.max_cll[0]},{info.max_cll[1]}"
    elif info.hdr == "HLG":
        p["repeat-headers"] = "1"
    p["log-level"] = "error"

    plan.preset = opts.preset or X265_PRESET[opts.speed]
    params = ":".join(f"{k}={v}" for k, v in p.items())
    base = ["-c:v", "libx265", "-preset", plan.preset, "-x265-params", params, "-tag:v", "hvc1"]
    plan.video_args = base + ["-crf", str(plan.crf)]
    plan.sample_args = base
    plan.params = " ".join(f"{k}={v}" for k, v in p.items() if k not in ("log-level", "master-display"))


def _svt_av1(tools: Tools, info: MediaInfo, plan: Plan, opts) -> None:
    preset = int(opts.preset) if opts.preset else SVT_PRESET[plan.rclass] + SVT_SPEED_OFFSET[opts.speed]
    plan.preset = str(max(0, min(12, preset)))

    grain_level = 0
    if opts.film_grain == "auto":
        grain_level = 0 if opts.tune == "animation" else SVT_FILM_GRAIN[plan.grain]
    elif opts.film_grain != "off":
        grain_level = int(opts.film_grain)

    wanted = [("tune", "0"), ("enable-variance-boost", "1")]
    if info.hdr == "HDR10":
        wanted.append(("enable-hdr", "1"))
        if info.master_display_svt:
            wanted.append(("mastering-display", info.master_display_svt))
        if info.max_cll:
            wanted.append(("content-light", f"{info.max_cll[0]},{info.max_cll[1]}"))
    grain_params = [("film-grain", str(grain_level)), ("film-grain-denoise", "0")] if grain_level else []

    def supported(pairs):
        kept = []
        for key, value in pairs:
            if tools.svt_accepts(key, value):
                kept.append((key, value))
            else:
                plan.details.append(f"this SVT-AV1 build has no {key}, skipped")
        return kept

    base_params = supported(wanted)
    all_params = base_params + supported(grain_params)

    def args(pairs):
        a = ["-c:v", "libsvtav1", "-preset", plan.preset]
        ki = keyint(info, plan, opts)
        if ki:
            a += ["-g", str(ki)]
        if pairs:
            a += ["-svtav1-params", ":".join(f"{k}={v}" for k, v in pairs)]
        return a

    plan.video_args = args(all_params) + ["-crf", str(plan.crf)]
    # VMAF can't score random grain fairly, so samples are measured without it
    plan.sample_args = args(base_params)
    plan.params = " ".join(f"{k}={v}" for k, v in all_params if k != "mastering-display")


def _audio(tools: Tools, info: MediaInfo, plan: Plan, opts) -> None:
    copyable = COPYABLE_AUDIO[plan.container]
    mode = opts.audio
    if mode == "opus" and "libopus" not in tools.encoders():
        plan.warnings.append("no libopus in this ffmpeg, using aac")
        mode = "aac"
    for i, a in enumerate(info.audio):
        plan.audio_maps.append(a.index)
        name = f"audio {i + 1}: {a.codec} {a.layout or f'{a.channels}ch'}"
        if a.codec in copyable and (mode == "copy" or (a.codec in LOSSY_AUDIO and mode != "flac")):
            plan.audio_args += [f"-c:a:{i}", "copy"]
            plan.details.append(f"{name}, copied")
            continue
        standard = (
            a.channels <= 2
            or (a.channels == 6 and a.layout.startswith("5.1"))
            or (a.channels == 8 and a.layout.startswith("7.1"))
        )
        target = mode if mode in ("opus", "aac", "flac") else "opus"
        if target != "flac" and not standard:
            target = "flac"  # discrete channels, e.g. separate mics, must stay separate
        if target == "flac":
            plan.audio_args += [f"-c:a:{i}", "flac", f"-compression_level:a:{i}", "8"]
            plan.details.append(f"{name} to flac")
            continue
        channels = a.channels if a.channels in (1, 2, 6, 8) else 2
        kbps = (OPUS_KBPS if target == "opus" else AAC_KBPS)[opts.quality][channels]
        if target == "opus":
            plan.audio_args += [f"-c:a:{i}", "libopus", f"-b:a:{i}", f"{kbps}k"]
            if a.channels > 2:
                layout = "5.1" if a.channels == 6 else "7.1"
                plan.audio_args += [
                    f"-filter:a:{i}",
                    f"aformat=channel_layouts={layout}",
                    f"-mapping_family:a:{i}",
                    "1",
                ]
            if a.sample_rate not in (8000, 12000, 16000, 24000, 48000):
                plan.audio_args += [f"-ar:a:{i}", "48000"]
        else:
            plan.audio_args += [f"-c:a:{i}", "aac", f"-b:a:{i}", f"{kbps}k"]
        plan.details.append(f"{name} to {target} {kbps}k")


def _subtitles(info: MediaInfo, plan: Plan) -> None:
    for s in info.subtitles:
        i = len(plan.subtitle_maps)
        if plan.container == "mp4":
            if s.codec not in TEXT_SUBTITLES:
                plan.warnings.append(f"{s.codec} subtitles can't go in mp4, dropped")
                continue
            codec = "mov_text"
        else:
            codec = "srt" if s.codec == "mov_text" else "copy"
        plan.subtitle_maps.append(s.index)
        plan.subtitle_args += [f"-c:s:{i}", codec]


def make_plan(tools: Tools, info: MediaInfo, opts) -> Plan:
    worth, reason = worth_encoding(info, opts.encode_all)
    if not worth:
        return Plan(skip=reason)
    if info.dovi_profile == 5 and not opts.encode_all:
        return Plan(skip="Dolby Vision profile 5 has no HDR10 base layer")

    plan = Plan(reason=reason, container=container_for(info, opts.container))
    if info.dovi_profile is not None:
        plan.warnings.append(f"Dolby Vision profile {info.dovi_profile} dropped, HDR10 kept")
    if info.alpha:
        plan.warnings.append("alpha channel dropped")
    if info.chroma in ("4:2:2", "4:4:4"):
        plan.details.append(f"chroma: {info.chroma} to 4:2:0")
    if info.bit_depth > 10:
        plan.details.append(f"depth: {info.bit_depth}-bit to 10-bit")
    if info.vfr:
        plan.details.append("variable frame rate, timestamps kept")
    if info.hdr != "SDR":
        extras = [
            n for n, v in (("mastering display", info.master_display_x265), ("MaxCLL", info.max_cll)) if v
        ]
        plan.details.append(f"hdr: {info.hdr} kept" + (f" with {', '.join(extras)}" if extras else ""))

    if opts.deinterlace == "auto":
        plan.interlaced, why = detect.interlacing(tools, info)
    else:
        plan.interlaced, why = opts.deinterlace == "on", f"--deinterlace {opts.deinterlace}"
    plan.details.append(f"scan: {'interlaced' if plan.interlaced else 'progressive'}, {why}")

    if opts.grain != "auto":
        plan.grain = opts.grain
    elif opts.tune == "animation":
        plan.grain = "clean"
    else:
        plan.grain, plan.grain_psnr = detect.grain(tools, info, plan.interlaced)
    measured = f", {plan.grain_psnr:.1f} dB" if plan.grain_psnr else ""
    plan.details.append(f"grain: {plan.grain}{measured}")

    crop = detect.black_bars(tools, info, plan.interlaced) if opts.autocrop else None
    _video_filters(info, plan, opts, crop)
    plan.rclass = resolution_class(plan.width, plan.height)

    enc = opts.encoder
    base = CRF_BASE[enc][plan.rclass]
    offset = QUALITY_OFFSET[enc][opts.quality]
    crf, parts = base + offset, [f"{plan.rclass} {base}"]
    if offset:
        parts.append(f"{opts.quality} {offset:+d}")
    fps = (info.fps or 25) * (2 if plan.interlaced and opts.deint_rate == "double" else 1)
    if fps >= 48:
        crf += 1
        parts.append("high frame rate +1")
    if info.hdr != "SDR" and enc == "x265":
        crf -= 1
        parts.append("hdr -1")
    if opts.crf is not None:
        crf, parts = opts.crf, ["--crf"]
    plan.crf = int(crf)
    plan.crf_reason = ", ".join(parts)

    if enc == "x265":
        _x265(info, plan, opts)
    else:
        _svt_av1(tools, info, plan, opts)
    _audio(tools, info, plan, opts)
    _subtitles(info, plan)
    return plan

import argparse
import subprocess
from pathlib import Path

import pytest

from shrencode import config, plan, vmaf
from shrencode.cli import build_parser, duration, film_grain
from shrencode.encode import expand_inputs, output_path
from shrencode.probe import MediaInfo, resolution_class


def media(**kw) -> MediaInfo:
    base = dict(
        path=Path("clip.mov"),
        size=10**9,
        duration=600.0,
        vindex=0,
        vcodec="prores",
        profile="",
        width=1920,
        height=1080,
        fps=25.0,
        vfr=False,
        pix_fmt="yuv422p10le",
        bit_depth=10,
        chroma="4:2:2",
        alpha=False,
        color_primaries="bt709",
        color_transfer="bt709",
        color_space="bt709",
        color_range="tv",
        field_order="progressive",
        vbitrate=150e6,
        timecode=None,
    )
    base.update(kw)
    return MediaInfo(**base)


@pytest.mark.parametrize(
    "text,seconds", [("20s", 20), ("12m", 720), ("1h30m", 5400), ("90", 90), ("1.5s", 1.5)]
)
def test_duration(text, seconds):
    assert duration(text) == seconds


@pytest.mark.parametrize("text", ["", "12x", "m"])
def test_duration_rejects(text):
    with pytest.raises(argparse.ArgumentTypeError):
        duration(text)


def test_film_grain():
    assert film_grain("auto") == "auto"
    assert film_grain("8") == "8"
    with pytest.raises(argparse.ArgumentTypeError):
        film_grain("99")


@pytest.mark.parametrize(
    "w,h,cls",
    [
        (720, 576, "SD"),
        (1280, 720, "720p"),
        (1920, 1080, "1080p"),
        (2048, 1080, "1080p"),
        (1080, 1920, "1080p"),
        (3840, 2160, "4K"),
        (4096, 2160, "4K"),
        (6144, 3160, "6K+"),
    ],
)
def test_resolution_class(w, h, cls):
    assert resolution_class(w, h) == cls


def test_worth_encoding():
    assert plan.worth_encoding(media(), False)[0]
    lean = media(vcodec="h264", vbitrate=2e6, fps=24)
    assert not plan.worth_encoding(lean, False)[0]
    assert plan.worth_encoding(lean, True)[0]
    camera = media(vcodec="hevc", width=3840, height=2160, fps=30, vbitrate=100e6)
    assert plan.worth_encoding(camera, False)[0]
    stream = media(vcodec="hevc", width=3840, height=2160, fps=24, vbitrate=15e6)
    assert not plan.worth_encoding(stream, False)[0]


def test_container_follows_mp4_input():
    assert plan.container_for(media(path=Path("a.mp4")), "auto") == "mp4"
    assert plan.container_for(media(path=Path("a.mov")), "auto") == "mkv"
    assert plan.container_for(media(path=Path("a.mp4")), "mkv") == "mkv"


def test_output_names(tmp_path):
    opts = argparse.Namespace(suffix=None, encoder="svt-av1", output_dir=None)
    src = tmp_path / "shoot" / "a.mov"
    assert output_path(src, tmp_path, opts, "mkv") == tmp_path / "shoot" / "a.av1.mkv"
    opts.output_dir = str(tmp_path / "out")
    assert output_path(src, tmp_path, opts, "mkv") == tmp_path / "out" / "shoot" / "a.av1.mkv"


def test_wildcards_and_temp_files(tmp_path):
    for name in ("a.mov", "b.mov", ".tmp.shrencode.c.x265.mkv", "notes.txt"):
        (tmp_path / name).write_bytes(b"")
    found = [f.name for f, _ in expand_inputs([str(tmp_path / "*.mov")], False)]
    assert found == ["a.mov", "b.mov"]
    found = [f.name for f, _ in expand_inputs([str(tmp_path)], False)]
    assert found == ["a.mov", "b.mov"]


def test_vmaf_scale_only_upscales_small_video():
    assert vmaf.vmaf_scale(3840, 2160) is None
    assert vmaf.vmaf_scale(1920, 1080) is None
    assert vmaf.vmaf_scale(720, 576) == "scale=1350:1080:flags=bicubic"


def test_sample_windows():
    assert len(vmaf.sample_windows(3600, None, 720, 20)) == 5
    assert len(vmaf.sample_windows(3600, 2, 720, 20)) == 2
    assert vmaf.sample_windows(30, None, 720, 20) == [(0.0, 30)]


def test_vmaf_search_finds_highest_passing_crf(monkeypatch, tmp_path):
    """Quality falls as crf rises; the search should land on the last crf above target."""
    curve = {c: 100 - (c - 10) * 0.5 for c in range(0, 60)}  # crf 20 -> 95.0

    def fake_run(cmd, timeout=None):
        if "libvmaf" in " ".join(cmd):
            crf = int(Path(cmd[cmd.index("-i", cmd.index("-i") + 1) + 1]).name.split("-")[1])
            return subprocess.CompletedProcess(cmd, 0, "", f"VMAF score: {curve[crf]}")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    class FakeTools:
        ffmpeg = "ffmpeg"

        def filters(self):
            return {"libvmaf"}

    monkeypatch.setattr(vmaf, "run", fake_run)
    p = plan.Plan(
        filters=["format=yuv420p10le"],
        width=1920,
        height=1080,
        crf=24,
        video_args=["-c:v", "libx265", "-crf", "24"],
        sample_args=["-c:v", "libx265"],
    )
    opts = argparse.Namespace(
        samples=None, sample_every=720, sample_duration=20, encoder="x265", min_vmaf=95.0
    )
    vmaf.search(FakeTools(), media(), p, opts, tmp_path)
    assert p.crf == 20
    assert p.video_args[-1] == "20"


def test_config_values(tmp_path, monkeypatch):
    monkeypatch.setattr(plan, "CRF_BASE", {k: dict(v) for k, v in plan.CRF_BASE.items()})
    _, encode_parser = build_parser()
    path = tmp_path / "config.toml"
    path.write_text('encoder = "svt-av1"\nkeep-mtime = false\nsample-every = "6m"\n[crf.x265]\n1080p = 21\n')
    defaults = config.load(encode_parser, path)
    assert defaults == {"encoder": "svt-av1", "keep_mtime": False, "sample_every": 360.0}
    assert plan.CRF_BASE["x265"]["1080p"] == 21


@pytest.mark.parametrize("text", ['quality = "extreme"', "bogus = 1", "autocrop = 1", "[crf.x265]\n9K = 1"])
def test_config_rejects(tmp_path, text):
    _, encode_parser = build_parser()
    path = tmp_path / "config.toml"
    path.write_text(text)
    with pytest.raises(config.ConfigError):
        config.load(encode_parser, path)

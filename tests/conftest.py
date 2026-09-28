import shutil
import subprocess

import pytest

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def make_clip(path, video_args, source="testsrc2=s=640x360:r=25:d=3", audio=True):
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-f", "lavfi", "-i", source]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=d=3:sample_rate=48000", "-ac", "2"]
    subprocess.run(cmd + video_args + [str(path)], check=True)
    return path


@pytest.fixture(scope="session")
def clips(tmp_path_factory):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    d = tmp_path_factory.mktemp("clips")
    return {
        "prores": make_clip(d / "prores.mov", ["-c:v", "prores_ks", "-c:a", "pcm_s24le"]),
        "lean": make_clip(d / "lean.mp4", ["-c:v", "libx264", "-b:v", "150k", "-c:a", "aac"]),
        "interlaced": make_clip(
            d / "interlaced.mpg",
            [
                "-vf",
                "interlace",
                "-c:v",
                "mpeg2video",
                "-b:v",
                "6M",
                "-flags",
                "+ilme+ildct",
                "-top",
                "1",
                "-c:a",
                "mp2",
            ],
            source="testsrc2=s=720x576:r=50:d=3",
        ),
    }

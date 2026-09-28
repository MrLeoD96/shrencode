# <picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/MrLeoD96/shrencode/main/assets/logo-dark.svg"><img src="https://raw.githubusercontent.com/MrLeoD96/shrencode/main/assets/logo.svg" alt="" height="28"></picture> shrencode

[![PyPI](https://img.shields.io/pypi/v/shrencode)](https://pypi.org/project/shrencode/)
[![Python](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2FMrLeoD96%2Fshrencode%2Fmain%2Fpyproject.toml)](https://www.python.org/downloads/)
[![CI](https://github.com/MrLeoD96/shrencode/actions/workflows/ci.yml/badge.svg)](https://github.com/MrLeoD96/shrencode/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/MrLeoD96/shrencode)](https://github.com/MrLeoD96/shrencode/blob/main/LICENSE)

shrencode is a tool to easily re-encode large or old video files and shrink them down
into a smaller file for storage without visible quality loss. It achieves this by first
analysing the original file, then adapting the encode to its codec, resolution, grain,
interlacing, HDR metadata and audio layout.

```
shrencode encode interview.mov
```

```
[1/1] interview.mov  48.20 GB
      prores 3840x2160 25p 4:2:2 10-bit 734.2 Mb/s → x265 crf 22, preset slow, mkv
      done  2.10 GB, 95.6% smaller, 3:12:04
```

<!-- TODO: replace the output above with a screenshot of the CLI -->

The original is never modified. The encode is written next to it as `interview.x265.mkv`.

Uses _ffmpeg_ with _x265_ or _SVT-AV1_.
Can use [video-compare](https://github.com/pixop/video-compare) to help you visually check the quality of your encode compared
to the original.
If you use [Nilesoft Shell](https://nilesoft.org), this can be added as a right-click menu item.

### Why not HandBrake?

HandBrake applies the same preset to every file you give it. shrencode looks at each file
first: it leaves sources that are already efficiently compressed alone, picks the quality
by resolution, measures grain and only deinterlaces what is actually interlaced. It doesn't
crop by default, and every encode is checked before it's kept. If you want to fine-tune a
single encode by hand, HandBrake is still the better tool.

## Requirements

* ffmpeg with libx265. SVT-AV1 needs libsvtav1, and `--min-vmaf` needs libvmaf. On Windows
  the full builds from gyan.dev have all three: `winget install Gyan.FFmpeg` or
  `scoop install ffmpeg`.
* Python 3.11 or newer, unless you use the standalone zip.
* Optionally [video-compare](https://github.com/pixop/video-compare) for `compare`, and
  [Nilesoft Shell](https://nilesoft.org) for the right-click menu.

## Install

```
uv tool install shrencode
```

`pipx install shrencode` works too, as does the standalone zip on the
[releases page](https://github.com/MrLeoD96/shrencode/releases). Upgrade with `uv tool upgrade shrencode`.

## Commands

### encode

```
shrencode encode [OPTIONS] INPUT...
```

Inputs can be files, folders or wildcards; `-r` includes subfolders. Per file:

* Editing codecs and old delivery codecs are encoded. H.264, HEVC, AV1 and VP9 sources are only
  encoded when their bitrate is high, measured in bits per pixel; `--encode-all` overrides this.
* CRF steps up with resolution, from 18 for SD to 23 for 6K with x265 at `--quality balanced`.
* Grain is measured and sets x265's psy options, or SVT-AV1 film grain synthesis.
* Interlaced sources are deinterlaced with bwdif. HDR10 and HLG metadata is carried over.
* Lossy audio is copied. PCM and other lossless audio becomes Opus, except discrete
  multichannel layouts, which stay lossless as FLAC.
* Subtitles, chapters, timecode and the source's modification time are kept.

Outputs are written to a temporary file and verified before taking their final name. They are
discarded if they end up above 90% of the source size.

Notable options:
* `-e svt-av1` for smaller files.
* `--quality high|balanced|compact` shifts every CRF.
* `--min-vmaf 95` measures samples and picks the highest CRF reaching that score.
* `--autocrop`, `--max-res 2160`, `--deinterlace`, `--tune animation`.
* `--compare` opens source and output in video-compare when a file finishes.
* `-n` shows the plan and ffmpeg command without encoding; `-v` shows the reasoning.

### compare

```
shrencode compare clip.mov clip.x265.mkv
```

Opens the files in [video-compare](https://github.com/pixop/video-compare), the original
on the left whichever order they're given in. Interlaced originals are deinterlaced to
match. Set `VIDEO_COMPARE_PATH` if video-compare isn't on PATH.

### menu

```
shrencode menu install
```

Adds a context menu to [Nilesoft Shell](https://nilesoft.org) for video files and
folders. Nilesoft Shell usually lives under Program Files, so run this from an
administrator terminal. `shrencode menu uninstall` removes it again; `shrencode menu show`
prints the menu file for manual setups.

## Configuration

Defaults can be set in `%APPDATA%\shrencode\config.toml`, or `~/.config/shrencode/config.toml`
elsewhere. Keys are long option names:

```toml
encoder = "svt-av1"
quality = "compact"
output-dir = "E:/Archive"

[crf.x265]
1080p = 21
```

`FFMPEG_PATH` points shrencode at a specific ffmpeg.

## Scripting

`--json` writes one object per file to stdout, and a summary at the end. Progress and
messages stay on stderr.

```json
{"type": "encode", "input": "a.mov", "output": "a.x265.mkv", "status": "done", "reason": null,
 "input_size": 67937399, "output_size": 8766624, "encode_percent": 12.9, "encoder": "x265",
 "crf": 20, "preset": "slow", "vmaf": null, "duration": 8.0, "encode_seconds": 30}
```

`status` is one of `done`, `skipped`, `exists`, `not-smaller`, `dry-run` or `failed`.
The exit code is 1 if any file failed. Failed encodes keep their ffmpeg log in
`%LOCALAPPDATA%\shrencode\logs`.

## Troubleshooting

**A file was skipped.** H.264, HEVC, AV1 and VP9 sources that are already efficiently
compressed are left alone. `-v` shows the bits-per-pixel figure behind the decision, and
`--encode-all` encodes them anyway.

**An encode was discarded as `not-smaller`.** It came out above 90% of the source size, so
keeping it would save little. Try `--quality compact` or `-e svt-av1`, or raise the limit
with `--max-encoded-percent`.

**The right-click menu doesn't show up.** Hold Ctrl and right-click the desktop to reload
Nilesoft Shell. If shrencode was moved or reinstalled somewhere else, run
`shrencode menu install` again so the menu points at the new location.

**Upgrading fails with "Access is denied".** A shrencode window is still open, usually one
from the right-click menu waiting for a key press, or an encode is still running. Close it
and try again.

**An encode failed.** The ffmpeg log is kept in `%LOCALAPPDATA%\shrencode\logs`, or
`~/.local/state/shrencode/logs` on Linux and macOS.

## Development

```
git clone https://github.com/MrLeoD96/shrencode
cd shrencode
uv sync --group dev
uv run pytest
uv run ruff check src tests
uv run ruff format --check src tests
```

The end-to-end tests need ffmpeg on PATH and are skipped without it. Bug reports and pull
requests are welcome.

## Credits

The actual encoding is done by [FFmpeg](https://ffmpeg.org), [x265](https://www.videolan.org/developers/x265.html),
[SVT-AV1](https://gitlab.com/AOMediaCodec/SVT-AV1) and [VMAF](https://github.com/Netflix/vmaf).
Side-by-side checks use [video-compare](https://github.com/pixop/video-compare), and the
right-click menu uses [Nilesoft Shell](https://nilesoft.org). The keyframe interval and VMAF
sampling follow [ab-av1](https://github.com/alexheretic/ab-av1), and the quality levels
started from [HandBrake](https://handbrake.fr)'s presets.

## License

[MIT](https://github.com/MrLeoD96/shrencode/blob/main/LICENSE)

"""Command-line interface."""

from __future__ import annotations

import argparse
import contextlib
import re
import sys

from . import __version__, config
from .console import console

EPILOG = """examples:
  shrencode encode interview.mov
  shrencode encode D:\\Footage -r -e svt-av1 --quality compact
  shrencode encode *.mxf --min-vmaf 95 --output-dir E:\\Archive
  shrencode compare clip.mov clip.x265.mkv
  shrencode menu install"""


def duration(text: str) -> float:
    """Seconds from '20s', '12m', '1h30m' or a plain number."""
    text = text.strip().lower()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    parts = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?", text)
    if not text or not parts:
        raise argparse.ArgumentTypeError(f"invalid duration: {text}")
    h, m, s = parts.groups()
    return int(h or 0) * 3600 + int(m or 0) * 60 + float(s or 0)


def film_grain(text: str) -> str:
    if text in ("auto", "off") or (text.isdigit() and 0 <= int(text) <= 50):
        return text
    raise argparse.ArgumentTypeError("expected auto, off or 0-50")


def build_parser() -> tuple[argparse.ArgumentParser, argparse.ArgumentParser]:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="show decisions and ffmpeg commands; repeat for more",
    )
    common.add_argument(
        "-q",
        "--quiet",
        action="count",
        default=0,
        help="only show results and problems; repeat for errors only",
    )
    common.add_argument(
        "--color", choices=["auto", "always", "never"], default="auto", help="colour output (default: auto)"
    )
    common.add_argument("--ffmpeg", metavar="PATH", help="ffmpeg executable or its folder")
    common.add_argument("--pause", action="store_true", help=argparse.SUPPRESS)

    parser = argparse.ArgumentParser(
        prog="shrencode",
        description="Re-encode video into small viewing copies, adapting to each file.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"shrencode {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    enc = sub.add_parser(
        "encode",
        parents=[common],
        help="encode files or folders",
        description="Encode files or folders. Sources are never modified.",
    )
    enc.add_argument("inputs", nargs="+", metavar="INPUT", help="files, folders or wildcards")

    g = enc.add_argument_group("video")
    g.add_argument(
        "-e", "--encoder", choices=["x265", "svt-av1"], default="x265", help="encoder (default: x265)"
    )
    g.add_argument(
        "--quality",
        choices=["high", "balanced", "compact"],
        default="balanced",
        help="quality level (default: balanced)",
    )
    g.add_argument(
        "--speed",
        choices=["slower", "normal", "faster"],
        default="normal",
        help="encoder effort (default: normal)",
    )
    g.add_argument("--crf", type=int, metavar="N", help="fixed crf instead of the per-file choice")
    g.add_argument("--preset", metavar="P", help="encoder preset, overrides --speed")
    g.add_argument(
        "--min-vmaf",
        type=float,
        nargs="?",
        const=95.0,
        metavar="N",
        help="highest crf that reaches this VMAF score (default: 95)",
    )
    g.add_argument("--samples", type=int, metavar="N", help="VMAF sample count")
    g.add_argument(
        "--sample-every",
        type=duration,
        default=duration("12m"),
        metavar="T",
        help="one VMAF sample per T of input (default: 12m)",
    )
    g.add_argument(
        "--sample-duration",
        type=duration,
        default=duration("20s"),
        metavar="T",
        help="VMAF sample length (default: 20s)",
    )
    g.add_argument("--tune", choices=["animation"], help="tuning for animation and screen content")
    g.add_argument(
        "--grain",
        choices=["auto", "clean", "light", "grainy", "heavy"],
        default="auto",
        help="grain level (default: auto)",
    )
    g.add_argument(
        "--film-grain",
        type=film_grain,
        default="auto",
        metavar="{auto,off,N}",
        help="svt-av1 film grain synthesis (default: auto)",
    )

    g = enc.add_argument_group("picture")
    g.add_argument("--max-res", type=int, metavar="N", help="limit the short side to N pixels")
    g.add_argument("--autocrop", action="store_true", help="remove black bars")
    g.add_argument(
        "--deinterlace", choices=["auto", "on", "off"], default="auto", help="deinterlacing (default: auto)"
    )
    g.add_argument(
        "--deint-rate",
        choices=["single", "double"],
        default="single",
        help="double outputs one frame per field (default: single)",
    )

    g = enc.add_argument_group("audio and container")
    g.add_argument(
        "--audio",
        choices=["opus", "aac", "flac", "copy"],
        default="opus",
        help="codec for uncompressed audio; lossy tracks are copied (default: opus)",
    )
    g.add_argument(
        "--container",
        choices=["auto", "mkv", "mp4"],
        default="auto",
        help="mp4 for mp4 input, otherwise mkv (default: auto)",
    )

    g = enc.add_argument_group("output")
    g.add_argument("--output-dir", metavar="DIR", help="write here, mirroring the input folders")
    g.add_argument("--suffix", metavar="TEXT", help="name suffix (default: encoder, as in clip.x265.mkv)")
    g.add_argument("-r", "--recursive", action="store_true", help="include subfolders")
    g.add_argument("-y", "--overwrite", action="store_true", help="replace existing outputs")
    g.add_argument("--encode-all", action="store_true", help="encode sources that are already efficient")
    g.add_argument(
        "--max-encoded-percent",
        type=float,
        default=90.0,
        metavar="N",
        help="discard outputs above N%% of the source size (default: 90)",
    )
    g.add_argument("--verify", action="store_true", help="decode the whole output, not just its ends")
    g.add_argument(
        "--no-keep-mtime",
        dest="keep_mtime",
        action="store_false",
        help="don't copy the source's modification time",
    )

    g = enc.add_argument_group("comparison")
    g.add_argument("--compare", action="store_true", help="open source and output in video-compare")
    g.add_argument("--compare-args", metavar="ARGS", help="extra video-compare options")
    g.add_argument("--video-compare", metavar="PATH", help="video-compare executable or its folder")

    g = enc.add_argument_group("run")
    g.add_argument("-n", "--dry-run", action="store_true", help="show the plan without encoding")
    g.add_argument("--json", action="store_true", help="write one JSON object per file to stdout")
    g.add_argument(
        "--priority",
        choices=["normal", "below-normal", "idle"],
        default="below-normal",
        help="process priority (default: below-normal)",
    )
    g.add_argument("--no-queue", action="store_true", help="don't wait for other shrencode runs")

    cmp = sub.add_parser(
        "compare",
        parents=[common],
        help="open files in video-compare",
        description="Open files in video-compare, originals on the left.",
    )
    cmp.add_argument("files", nargs="+", metavar="FILE")
    cmp.add_argument("--args", metavar="ARGS", help="extra video-compare options")
    cmp.add_argument("--video-compare", metavar="PATH", help="video-compare executable or its folder")

    men = sub.add_parser(
        "menu",
        parents=[common],
        help="manage the Nilesoft Shell menu",
        description="Add or remove the Nilesoft Shell context menu.",
    )
    men.add_argument("action", choices=["install", "uninstall", "show"])
    men.add_argument("--nilesoft", metavar="DIR", help="Nilesoft Shell folder, if not found automatically")
    return parser, enc


def main(argv: list[str] | None = None) -> int:
    parser, encode_parser = build_parser()
    try:
        encode_parser.set_defaults(**config.load(encode_parser))
    except config.ConfigError as e:
        console.setup()
        console.error(str(e))
        return 2
    args = parser.parse_args(argv)
    console.setup(
        verbosity=1 + args.verbose - args.quiet, color=args.color, json_mode=getattr(args, "json", False)
    )
    if args.command == "compare" and len(args.files) < 2:
        parser.error("compare needs at least two files")

    try:
        if args.command == "encode":
            from .encode import run_encode

            code = run_encode(args)
        elif args.command == "compare":
            from .compare import run_compare

            code = run_compare(args)
        else:
            from .menu import run_menu

            code = run_menu(args)
    except KeyboardInterrupt:
        console.info("\ncancelled")
        code = 130

    if args.pause and sys.stdin is not None:
        with contextlib.suppress(EOFError, KeyboardInterrupt):
            input("\npress enter to close ")
    return code

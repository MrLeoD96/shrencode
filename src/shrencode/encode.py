"""The encode subcommand."""

from __future__ import annotations

import datetime as dt
import glob
import os
import subprocess
import tempfile
import time
import traceback
from pathlib import Path

from . import __version__, compare, vmaf
from .console import console, fmt_duration, fmt_rate, fmt_size
from .lock import QueueLock
from .plan import OUTPUT_INFIX, Plan, container_for, make_plan, worth_encoding
from .probe import MediaInfo, probe
from .tools import Tools, find_video_compare

VIDEO_EXTENSIONS = {
    ".mov",
    ".mp4",
    ".m4v",
    ".mkv",
    ".avi",
    ".mxf",
    ".mts",
    ".m2ts",
    ".ts",
    ".webm",
    ".wmv",
    ".mpg",
    ".mpeg",
    ".vob",
    ".flv",
    ".dv",
    ".3gp",
    ".ogv",
    ".y4m",
    ".asf",
}
TEMP_PREFIX = ".tmp.shrencode."
PRIORITY_CLASS = {"normal": 0x20, "below-normal": 0x4000, "idle": 0x40}
NICENESS = {"normal": 0, "below-normal": 10, "idle": 19}


def expand_inputs(items: list[str], recursive: bool) -> list[tuple[Path, Path]]:
    """Files paired with the folder they were found under, for mirroring with --output-dir."""
    found: list[tuple[Path, Path]] = []
    for item in items:
        paths = [Path(item)]
        # cmd.exe passes wildcards through unexpanded
        if not paths[0].exists() and glob.has_magic(item):
            paths = [Path(p) for p in sorted(glob.glob(item))]
        if not paths or not any(p.exists() for p in paths):
            console.warn(f"not found: {item}")
            continue
        for path in paths:
            if path.is_dir():
                walker = path.rglob("*") if recursive else path.glob("*")
                found += [
                    (f, path) for f in sorted(walker) if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
                ]
            elif path.is_file():
                found.append((path, path.parent))
    seen, unique = set(), []
    for f, base in found:
        if f.name.startswith(TEMP_PREFIX):
            continue
        key = os.path.normcase(str(f.resolve()))
        if key not in seen:
            seen.add(key)
            unique.append((f, base))
    return unique


def output_path(src: Path, base: Path, opts, container: str) -> Path:
    infix = opts.suffix or OUTPUT_INFIX[opts.encoder]
    name = f"{src.stem}.{infix}.{container}"
    if not opts.output_dir:
        return src.with_name(name)
    try:
        rel = src.parent.relative_to(base)
    except ValueError:
        rel = Path()
    return Path(opts.output_dir) / rel / name


def is_own_output(src: Path) -> bool:
    return any(src.stem.lower().endswith(f".{i}") for i in OUTPUT_INFIX.values())


def log_dir() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        root = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return root / "shrencode" / "logs"


def _color_args(info: MediaInfo) -> list[str]:
    args = []
    for flag, value in (
        ("-color_primaries", info.color_primaries),
        ("-color_trc", info.color_transfer),
        ("-colorspace", info.color_space),
    ):
        if value not in ("unknown", "reserved", ""):
            args += [flag, value]
    # swscale converts full-range yuvj input to limited range, so don't tag those as pc
    if info.color_range in ("tv", "pc") and not info.pix_fmt.startswith("yuvj"):
        args += ["-color_range", info.color_range]
    return args


def build_command(tools: Tools, info: MediaInfo, plan: Plan, out: Path) -> list[str]:
    cmd = [tools.ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(info.path), "-map", f"0:{info.vindex}"]
    for index in plan.audio_maps + plan.subtitle_maps:
        cmd += ["-map", f"0:{index}"]
    if plan.container == "mkv" and info.has_attachments:
        cmd += ["-map", "0:t?", "-c:t", "copy"]
    cmd += [
        "-map_metadata",
        "0",
        "-map_chapters",
        "0",
        "-fps_mode",
        "passthrough",
        "-vf",
        ",".join(plan.filters),
        "-pix_fmt",
        "yuv420p10le",
        *plan.video_args,
        *_color_args(info),
        *plan.audio_args,
        *plan.subtitle_args,
        "-metadata",
        f"SHRENCODE_SOURCE={info.path.name}",
        "-metadata:s:v:0",
        f"ENCODER_SETTINGS={plan.encoder_settings()}",
    ]
    if plan.container == "mkv":
        cmd += ["-cues_to_front", "1"]
    else:
        cmd += ["-movflags", "+faststart+use_metadata_tags"]
        if info.timecode:
            cmd += ["-timecode", info.timecode]
    cmd += ["-max_muxing_queue_size", "4096", "-progress", "pipe:1", "-nostats", str(out)]
    return cmd


def _number(value: str | None) -> int:
    """ffmpeg's -progress output uses N/A before the first frame."""
    try:
        return int(value or 0)
    except ValueError:
        return 0


def run_ffmpeg(cmd: list[str], duration: float | None, log: Path, priority: str) -> int:
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if priority != "normal":
        if os.name == "nt":
            kwargs["creationflags"] = PRIORITY_CLASS[priority]
        else:
            kwargs["preexec_fn"] = lambda: os.nice(NICENESS[priority])
    started = time.time()
    with open(log, "w", encoding="utf-8", errors="replace") as log_file:
        proc = subprocess.Popen(cmd, stderr=log_file, **kwargs)
        state: dict[str, str] = {}
        shown = 0.0
        try:
            for line in proc.stdout:
                key, _, value = line.strip().partition("=")
                state[key] = value
                now = time.time()
                if key != "progress" or (now - shown < 1 and value != "end"):
                    continue
                shown = now
                done = _number(state.get("out_time_us") or state.get("out_time_ms")) / 1e6
                size = _number(state.get("total_size"))
                elapsed = now - started
                if duration and done > 0:
                    frac = min(1.0, done / duration)
                    eta = elapsed * (1 - frac) / frac if frac > 0.002 else None
                    estimate = f" (~{fmt_size(size / frac)})" if frac > 0.02 else ""
                    console.progress(
                        f"      {frac * 100:5.1f}%  {state.get('fps', '?'):>5} fps  "
                        f"{state.get('speed', '?').strip():>6}  {fmt_size(size)}{estimate}  "
                        f"eta {fmt_duration(eta)}"
                    )
                else:
                    console.progress(f"      {fmt_duration(done)}  {fmt_size(size)}")
            proc.wait()
        except KeyboardInterrupt:
            proc.kill()
            proc.wait()
            raise
    console.clear_progress()
    return proc.returncode


def verify(tools: Tools, out: Path, src: MediaInfo, full: bool) -> str | None:
    """Return a problem description, or None if the output looks sound."""
    try:
        result = probe(tools, out)
    except RuntimeError as e:
        return f"unreadable output: {e}"
    tolerance = max(1.0, (src.duration or 0) * 0.005)
    if src.duration and result.duration and abs(result.duration - src.duration) > tolerance:
        return f"duration {result.duration:.2f}s, source {src.duration:.2f}s"
    checks = [["-i", str(out)]] if full else [["-t", "5", "-i", str(out)], ["-sseof", "-5", "-i", str(out)]]
    for pre in checks:
        r = subprocess.run(
            [
                tools.ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-v",
                "error",
                *pre,
                "-map",
                "0:v:0",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if r.returncode != 0:
            return "decode failed: " + (r.stderr.strip().splitlines() or ["?"])[-1]
    return None


def _save_log(log: Path, src: Path) -> Path | None:
    try:
        folder = log_dir()
        folder.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = folder / f"{stamp}-{src.stem}.log"
        dest.write_text(log.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        return dest
    except OSError:
        return None


def encode_file(tools: Tools, src: Path, base: Path, opts, number: str, compare_with: str | None) -> dict:
    record: dict = {
        "type": "encode",
        "input": str(src),
        "output": None,
        "status": "",
        "reason": None,
        "input_size": src.stat().st_size,
        "output_size": None,
        "encode_percent": None,
        "encoder": opts.encoder,
        "crf": None,
        "preset": None,
        "vmaf": None,
        "duration": None,
        "encode_seconds": None,
    }
    console.info(f"{console.dim(number)} {console.bold(src.name)}  {fmt_size(record['input_size'])}")
    try:
        info = probe(tools, src)
    except RuntimeError as e:
        console.error(f"{src.name}: {e}")
        record.update(status="failed", reason=str(e))
        return record
    record["duration"] = round(info.duration or 0, 3)

    worth, why = worth_encoding(info, opts.encode_all)
    if not worth:
        console.result(f"      skipped: {why}")
        record.update(status="skipped", reason=why)
        return record

    out = output_path(src, base, opts, container_for(info, opts.container))
    record["output"] = str(out)
    if out.exists() and not opts.overwrite:
        console.result(f"      skipped: {out.name} exists")
        record.update(status="exists", reason="output exists")
        if compare_with:
            try:
                existing = probe(tools, out)
                compare.launch(
                    compare_with,
                    src,
                    [out],
                    compare.match_scan(info, existing),
                    opts.compare_args,
                    info.width,
                    info.height,
                )
            except RuntimeError as e:
                console.warn(f"      compare skipped: {e}")
        return record

    plan = make_plan(tools, info, opts)
    if plan.skip:
        console.result(f"      skipped: {plan.skip}")
        record.update(status="skipped", reason=plan.skip)
        return record

    with tempfile.TemporaryDirectory(prefix="shrencode-") as workdir:
        if opts.min_vmaf is not None and not opts.dry_run:
            vmaf.search(tools, info, plan, opts, Path(workdir))
        record.update(crf=plan.crf, preset=plan.preset, vmaf=round(plan.vmaf, 2) if plan.vmaf else None)

        console.info(
            f"      {info.summary()} {fmt_rate(info.vbitrate)} → "
            f"{opts.encoder} crf {plan.crf}, preset {plan.preset}, {plan.container}"
        )
        for w in plan.warnings:
            console.warn(f"      {w}")
        console.detail(f"      reason: {plan.reason}")
        console.detail(f"      crf: {plan.crf_reason}")
        for d in plan.details:
            console.detail(f"      {d}")
        console.detail(f"      params: {plan.params}")

        tmp = out.with_name(TEMP_PREFIX + out.name)
        cmd = build_command(tools, info, plan, tmp)
        if opts.dry_run:
            shown = cmd[:-4] + [str(out)]  # drop the progress plumbing and temp name
            console.result("      " + subprocess.list2cmdline(shown))
            record.update(status="dry-run")
            return record
        console.debug("      " + subprocess.list2cmdline(cmd))

        out.parent.mkdir(parents=True, exist_ok=True)
        log = Path(workdir) / "ffmpeg.log"
        started = time.time()
        try:
            code = run_ffmpeg(cmd, info.duration, log, opts.priority)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        record["encode_seconds"] = round(time.time() - started)
        if code != 0:
            tmp.unlink(missing_ok=True)
            saved = _save_log(log, src)
            last = (log.read_text(encoding="utf-8", errors="replace").strip().splitlines() or ["?"])[-1]
            console.error(f"{src.name}: ffmpeg exited with {code}: {last}")
            if saved:
                console.info(console.dim(f"      log: {saved}"))
            record.update(status="failed", reason=last, log=str(saved) if saved else None)
            return record

    problem = verify(tools, tmp, info, opts.verify)
    if problem:
        tmp.unlink(missing_ok=True)
        console.error(f"{src.name}: verification failed, {problem}")
        record.update(status="failed", reason=problem)
        return record

    size = tmp.stat().st_size
    percent = 100 * size / info.size
    record.update(output_size=size, encode_percent=round(percent, 2))
    if percent > opts.max_encoded_percent:
        tmp.unlink()
        console.result(f"      discarded: {fmt_size(size)} is {percent:.0f}% of the source")
        record.update(status="not-smaller", reason=f"{percent:.1f}% of source")
        return record

    out.unlink(missing_ok=True)
    tmp.replace(out)
    if opts.keep_mtime:
        st = src.stat()
        os.utime(out, (st.st_atime, st.st_mtime))
    console.result(
        f"      {console.green('done')}  {fmt_size(size)}, {100 - percent:.1f}% smaller, "
        f"{fmt_duration(record['encode_seconds'])}"
    )
    record["status"] = "done"
    if compare_with:
        # the original gets the same deinterlace and crop as the encode
        matched = [f for f in plan.filters if not f.startswith(("format=", "scale="))]
        compare.launch(compare_with, src, [out], matched, opts.compare_args, info.width, info.height)
    return record


def run_encode(opts) -> int:
    tools = Tools(opts.ffmpeg)
    if not tools.ffmpeg or not tools.ffprobe:
        console.error("ffmpeg not found; put it on PATH or set FFMPEG_PATH")
        return 1
    needed = "libx265" if opts.encoder == "x265" else "libsvtav1"
    if needed not in tools.encoders():
        console.error(f"{tools.ffmpeg} has no {needed}; use a full build such as gyan.dev's")
        return 1

    compare_with = None
    if opts.compare:
        compare_with = find_video_compare(opts.video_compare)
        if not compare_with:
            console.warn("video-compare not found, --compare ignored")

    files = [(f, b) for f, b in expand_inputs(opts.inputs, opts.recursive) if not is_own_output(f)]
    if not files:
        console.error("no video files to encode")
        return 1

    lock = None  # held for the whole run; released when the process exits
    if not opts.dry_run and not opts.no_queue:
        lock = QueueLock()
        lock.acquire()

    records = []
    for n, (src, base) in enumerate(files, 1):
        try:
            record = encode_file(tools, src, base, opts, f"[{n}/{len(files)}]", compare_with)
        except Exception as e:  # one bad file shouldn't end the batch
            console.error(f"{src.name}: {type(e).__name__}: {e}")
            console.debug(traceback.format_exc())
            record = {"type": "encode", "input": str(src), "status": "failed", "reason": str(e)}
        records.append(record)
        console.emit(record)
        console.info()

    done = [r for r in records if r["status"] == "done"]
    failed = [r for r in records if r["status"] == "failed"]
    planned = [r for r in records if r["status"] == "dry-run"]
    discarded = [r for r in records if r["status"] == "not-smaller"]
    skipped = len(records) - len(done) - len(failed) - len(planned) - len(discarded)
    parts = [f"{len(planned)} planned" if opts.dry_run else f"{len(done)} encoded"]
    if skipped:
        parts.append(f"{skipped} skipped")
    if discarded:
        parts.append(f"{len(discarded)} discarded")
    if failed:
        parts.append(console.red(f"{len(failed)} failed"))
    line = ", ".join(parts)
    if done:
        before = sum(r["input_size"] for r in done)
        after = sum(r["output_size"] for r in done)
        line += f": {fmt_size(before)} → {fmt_size(after)}"
    if len(records) > 1 or not done:
        console.result(line)
    console.emit(
        {
            "type": "summary",
            "encoded": len(done),
            "planned": len(planned),
            "skipped": skipped,
            "discarded": len(discarded),
            "failed": len(failed),
            "version": __version__,
        }
    )
    del lock
    return 1 if failed else 0

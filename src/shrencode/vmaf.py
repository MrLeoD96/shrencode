"""Finding the highest CRF that still reaches --min-vmaf."""

from __future__ import annotations

import math
import os
import re
from pathlib import Path

from .console import console
from .detect import spread
from .plan import Plan
from .probe import MediaInfo
from .tools import Tools, run

CRF_RANGE = {"x265": (8, 34), "svt-av1": (10, 55)}
CRF_STEP = {"x265": 3, "svt-av1": 5}


class VmafError(RuntimeError):
    pass


def sample_windows(
    duration: float | None, samples: int | None, every: float, length: float
) -> list[tuple[float, float]]:
    """One sample per `every` seconds of input unless `samples` is given."""
    if not duration:
        return [(0.0, length)]
    count = samples or max(1, math.ceil(duration / every))
    if duration <= count * length * 1.5:
        return [(0.0, duration)]
    return [(t, length) for t in spread(duration, count, length)]


def vmaf_scale(width: int, height: int) -> str | None:
    """VMAF's default model expects 1080p; upscale smaller video, leave the rest alone."""
    if width >= 1728 or height >= 972:
        return None
    factor = min(1920 / width, 1080 / height)
    return f"scale={round(width * factor / 2) * 2}:{round(height * factor / 2) * 2}:flags=bicubic"


def search(tools: Tools, info: MediaInfo, plan: Plan, opts, workdir: Path) -> None:
    if "libvmaf" not in tools.filters():
        console.warn("      no libvmaf in this ffmpeg, using the rule-based crf")
        return
    windows = sample_windows(info.duration, opts.samples, opts.sample_every, opts.sample_duration)
    chain = ",".join(plan.filters)
    scale = vmaf_scale(plan.width, plan.height)
    post = f"{scale}," if scale else ""
    threads = os.cpu_count() or 4
    low, high = CRF_RANGE[opts.encoder]
    low, high = max(low, plan.crf - 12), min(high, plan.crf + 12)
    step = CRF_STEP[opts.encoder]
    scores: dict[int, float] = {}

    def score(crf: int) -> float:
        if crf in scores:
            return scores[crf]
        values = []
        for n, (start, length) in enumerate(windows):
            sample = workdir / f"sample-{crf}-{n}.mkv"
            encode = [
                tools.ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-y",
                "-ss",
                f"{start:.3f}",
                "-t",
                f"{length:.3f}",
                "-i",
                str(info.path),
                "-map",
                f"0:{info.vindex}",
                "-an",
                "-sn",
                "-dn",
                "-vf",
                chain,
                *plan.sample_args,
                "-crf",
                str(crf),
                "-fps_mode",
                "passthrough",
                str(sample),
            ]
            r = run(encode)
            if r.returncode != 0:
                raise VmafError("sample encode failed: " + r.stderr.strip()[-300:])
            graph = (
                f"[0:{info.vindex}]{chain},{post}format=yuv420p10le,setpts=PTS-STARTPTS[ref];"
                f"[1:v:0]{post}format=yuv420p10le,setpts=PTS-STARTPTS[dist];"
                f"[dist][ref]libvmaf=n_threads={threads}"
            )
            measure = [
                tools.ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-ss",
                f"{start:.3f}",
                "-t",
                f"{length:.3f}",
                "-i",
                str(info.path),
                "-i",
                str(sample),
                "-filter_complex",
                graph,
                "-f",
                "null",
                "-",
            ]
            r = run(measure)
            found = re.findall(r"VMAF score[:=]\s*([\d.]+)", r.stderr)
            sample.unlink(missing_ok=True)
            if not found:
                raise VmafError("vmaf measurement failed: " + r.stderr.strip()[-300:])
            values.append(float(found[-1]))
        scores[crf] = sum(values) / len(values)
        console.detail(f"      crf {crf}: vmaf {scores[crf]:.2f}")
        return scores[crf]

    target = opts.min_vmaf
    console.detail(f"      vmaf search: {len(windows)} sample(s), target {target}")
    try:
        good = bad = None
        if score(plan.crf) >= target:
            good = plan.crf
            while good < high:
                nxt = min(high, good + step)
                if score(nxt) >= target:
                    good = nxt
                else:
                    bad = nxt
                    break
        else:
            bad = plan.crf
            while bad > low:
                nxt = max(low, bad - step)
                if score(nxt) >= target:
                    good = nxt
                    break
                bad = nxt
        if good is None:
            good = low
            console.warn(f"      vmaf {target} not reached at crf {low}, using {low}")
        if bad is not None:
            while bad - good > 1:
                mid = (good + bad) // 2
                if score(mid) >= target:
                    good = mid
                else:
                    bad = mid
    except VmafError as e:
        console.warn(f"      {e}; using the rule-based crf")
        return
    plan.set_crf(good)
    plan.vmaf = scores.get(good)
    plan.crf_reason = f"vmaf {plan.vmaf:.2f}" if plan.vmaf else f"min vmaf {target}"

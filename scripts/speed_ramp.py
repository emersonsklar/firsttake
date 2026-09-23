#!/usr/bin/env python3
"""speed_ramp.py — speed up a shot with real motion blur, to an exact output length.

Renders a constant-speed or accelerating (ramp) retime of a source clip, producing **exactly** the
number of output frames you ask for, with motion blur built by averaging frames rather than by
dropping them.

    python3 speed_ramp.py --src shot.mov --frames 135 --speed 4 --out fast.mov
    python3 speed_ramp.py --src shot.mov --frames 135 --ramp 1:15 --steps 27 --out warp.mov
    python3 speed_ramp.py --src shot.mov --frames 135 --speed 4 --dry-run   # show the filtergraph
    python3 speed_ramp.py --selftest

THE THINGS THAT MAKE THIS NOT A ONE-LINER
-----------------------------------------
**1. Blur, not decimation.** A plain frame-skip reads as a fast-forward. `tmix=frames=N` averages
N frames so each output frame carries the whole group's movement. ⚠️ Then select the **LAST** frame
of each window (`eq(mod(n,N),N-1)`), not index 0: index 0 is the one frame with no history, so
`not(mod(n,N))` leaves the first frame of every step unblurred while the other 134 are blurred.

**2. Stabilise BEFORE this, never after.** Once `tmix` has averaged the frames, camera shake is
baked into a smear with no sharp feature left to track. If the shot needs stabilising, stabilise a
1x intermediate first and feed that in here.

**3. Match the motion model to the motion.** On a centred forward dolly, `vidstab` removed only
13-15% of a walking bob and left its 1.83 Hz fundamental untouched, because libvidstab models
translation and rotation with **no zoom term** and the radially expanding frame swamps its fit. An
NLE's similarity stabiliser, which fits scale, moved the fundamental to 10.95 Hz. Ask what your
stabiliser can represent before blaming the footage.

**4. Keep the output UNGRADED and full-raster** if it is going back into a timeline. A watchable
candidate that is already graded and cropped will get graded and cropped a second time. Let the
normal chain apply downstream exactly once.

**5. A ramp reaches a far higher peak than a constant speed, for the same source.** A ramp spends
few frames at its top speed. Holding output length fixed, the source consumed is
`step * sum(speeds)` — so on 699 available frames a linear ramp caps near 9x while a quadratic one
reaches 15x. `--curve` picks the exponent; the tool prints what was consumed and refuses to run
past the source.

**6. Frame count is asserted, not hoped for.** `tmix`, `select` and `trim` interact, and this is
the sort of pipeline that silently comes back one frame short. The output is counted by decoding.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

FPS_DEFAULT = "30000/1001"


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                        "-show_entries", "stream=nb_read_frames,width,height,r_frame_rate,"
                        "avg_frame_rate", "-of", "json", path], capture_output=True, text=True)
    return (json.loads(r.stdout or "{}").get("streams") or [{}])[0]


def solve_ramp(lo, hi, steps, curve):
    """Integer speeds rising lo->hi over `steps` steps on a t**curve profile.

    The first step is held at `lo` deliberately: a ramp that starts at true 1x cuts out of the
    preceding shot with no speed pop.
    """
    out = []
    for i in range(steps):
        t = i / max(1, steps - 1)
        out.append(int(round(lo + (hi - lo) * (t ** curve))))
    out[0] = lo
    return out


def solve_peak_for_budget(frames, step, lo, curve, budget):
    """Highest integer peak whose retime fits `budget` source frames. None if even `lo` will not.

    This is the question that actually comes up: not "what does a 15x ramp consume" but "I have
    699 usable frames and 135 to fill — how fast can I get?" A constant speed can only reach
    `budget // frames`; a ramp reaches far higher because it spends few frames at its top speed,
    and a steeper `curve` buys more still.
    """
    steps = frames // step
    best = None
    for hi in range(lo, lo + 512):
        speeds = solve_ramp(lo, hi, steps, curve)
        if step * sum(speeds) > budget:
            break
        best = hi
    return best


def plan(frames, speeds, step, src_frames=None, clean_limit=None):
    if step * len(speeds) != frames:
        sys.exit(f"ERROR: {len(speeds)} steps x {step} frames = {step * len(speeds)}, "
                 f"not the {frames} requested. frames must divide by steps.")
    rows, cursor = [], 0
    for i, n in enumerate(speeds):
        need = step * n
        rows.append((i, cursor, need, n))
        cursor += need
    if src_frames is not None and cursor > src_frames:
        sys.exit(f"ERROR: this retime needs {cursor} source frames but the file has "
                 f"{src_frames}. Flatten the curve (--curve), lower the peak, or shorten it.")
    if clean_limit is not None and cursor > clean_limit:
        sys.exit(f"ERROR: consumes to source frame {cursor}, past the --clean-limit "
                 f"{clean_limit} you declared usable.")
    return rows, cursor


def filtergraph(rows, fps, blur=True):
    """One decode, an N-way split, per-branch speed and blur, concat. No colour, no crop."""
    n = len(rows)
    if n == 1:
        _, a, ln, sp = rows[0]
        parts = [f"trim=start_frame={a}:end_frame={a + ln}", "setpts=PTS-STARTPTS"]
        if sp > 1 and blur:
            parts += [f"tmix=frames={sp}", f"select='eq(mod(n\\,{sp})\\,{sp - 1})'"]
        elif sp > 1:
            parts += [f"select='eq(mod(n\\,{sp})\\,{sp - 1})'"]
        parts += ["setpts=N/FRAME_RATE/TB", f"fps={fps}"]
        return ",".join(parts), False
    out = [f"[0:v]split={n}" + "".join(f"[s{i}]" for i in range(n)) + ";"]
    for i, a, ln, sp in rows:
        seg = [f"[s{i}]trim=start_frame={a}:end_frame={a + ln}", "setpts=PTS-STARTPTS"]
        if sp > 1:
            if blur:
                seg.append(f"tmix=frames={sp}")
            seg.append(f"select='eq(mod(n\\,{sp})\\,{sp - 1})'")
        seg.append("setpts=N/FRAME_RATE/TB")
        out.append("".join(f"{x}," for x in seg)[:-1] + f"[b{i}];")
    out.append("".join(f"[b{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0,fps={fps}[v]")
    return "".join(out), True


def selftest():
    """Assert the arithmetic that is easy to get wrong, without needing a source file."""
    fails = 0
    rows, total = plan(135, [3] * 27, 5)
    ok = total == 405 and rows[0][1] == 0 and rows[1][1] == 15 and rows[-1][1] == 390
    fails += 0 if ok else 1
    print(f"  constant 3x, 27x5=135 out: consumes {total} (405), branch starts contiguous  "
          f"{'ok' if ok else '*** FAIL ***'}")

    # branches must tile the source with no gap and no overlap
    contiguous = all(rows[i][1] + rows[i][2] == rows[i + 1][1] for i in range(len(rows) - 1))
    fails += 0 if contiguous else 1
    print(f"  branches tile the source with no gap or overlap  "
          f"{'ok' if contiguous else '*** FAIL ***'}")

    # For a fixed source budget: a ramp beats a constant speed, and a steeper curve beats a
    # shallower one. Asserted as the RELATIONSHIP, with each candidate proven to fit the budget.
    BUDGET, FRAMES, STEP = 699, 135, 5
    const_peak = BUDGET // FRAMES
    peaks = {c: solve_peak_for_budget(FRAMES, STEP, 1, c, BUDGET) for c in (1.0, 2.0, 2.5)}
    fits = {}
    for c, hi in peaks.items():
        _, used = plan(FRAMES, solve_ramp(1, hi, FRAMES // STEP, c), STEP)
        fits[c] = used
    ok2 = (peaks[1.0] > const_peak and peaks[2.5] >= peaks[2.0] >= peaks[1.0]
           and all(v <= BUDGET for v in fits.values()))
    fails += 0 if ok2 else 1
    print(f"  budget {BUDGET}: constant speed caps at {const_peak}x; ramp reaches "
          f"linear {peaks[1.0]}x ({fits[1.0]}), quadratic {peaks[2.0]}x ({fits[2.0]}), "
          f"k=2.5 {peaks[2.5]}x ({fits[2.5]}) — all within budget  "
          f"{'ok' if ok2 else '*** FAIL ***'}")

    lin = solve_ramp(1, peaks[1.0], 27, 1.0)
    quad = solve_ramp(1, peaks[2.0], 27, 2.0)
    ok3 = lin[0] == 1 and quad[0] == 1
    fails += 0 if ok3 else 1
    print(f"  first step held at 1x so the cut in has no speed pop  "
          f"{'ok' if ok3 else '*** FAIL ***'}")

    # the select expression must take the LAST frame of each window, never index 0
    g, _ = filtergraph([(0, 0, 20, 4)], FPS_DEFAULT)
    ok4 = "eq(mod(n\\,4)\\,3)" in g and "not(mod(" not in g
    fails += 0 if ok4 else 1
    print(f"  selects the last frame of each tmix window, not the unblurred index 0  "
          f"{'ok' if ok4 else '*** FAIL ***'}")

    # refuses to read past the source
    import io, contextlib
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            plan(135, [6] * 27, 5, src_frames=699)
        ok5 = False
    except SystemExit:
        ok5 = True
    fails += 0 if ok5 else 1
    print(f"  refuses a retime that would read past the source  "
          f"{'ok' if ok5 else '*** FAIL ***'}")
    print("selftest:", "PASS" if not fails else f"{fails} FAILURE(S)")
    return 0 if not fails else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src")
    ap.add_argument("--out")
    ap.add_argument("--frames", type=int, help="EXACT output frame count")
    ap.add_argument("--speed", type=int, help="constant speed multiplier")
    ap.add_argument("--ramp", metavar="LO:HI", help="accelerate from LO to HI")
    ap.add_argument("--steps", type=int, default=27,
                    help="ramp steps; --frames must divide by this")
    ap.add_argument("--budget", type=int,
                    help="usable source frames; with --ramp LO:max, solves for the highest peak "
                         "that fits and reports it")
    ap.add_argument("--curve", type=float, default=2.0,
                    help="ramp exponent. 1.0 linear; higher reaches a higher peak for the same "
                         "source budget because it spends fewer frames fast")
    ap.add_argument("--src-in", type=int, default=0, help="first source frame to use")
    ap.add_argument("--clean-limit", type=int,
                    help="last source frame you have declared usable; refuses to go past it")
    ap.add_argument("--no-blur", action="store_true", help="decimate only — a control, not a look")
    ap.add_argument("--fps", default=FPS_DEFAULT)
    ap.add_argument("--codec", default="prores_ks")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()
    if not (a.src and a.frames and (a.speed or a.ramp)):
        ap.error("need --src, --frames and one of --speed / --ramp (or --selftest)")

    if a.speed:
        speeds, step = [a.speed], a.frames
    else:
        lo_s, hi_s = a.ramp.split(":")
        lo = int(lo_s)
        if hi_s == "max":
            if not a.budget:
                ap.error("--ramp LO:max needs --budget")
            hi = solve_peak_for_budget(a.frames, a.frames // a.steps, lo, a.curve, a.budget)
            if hi is None:
                sys.exit(f"ERROR: even {lo}x does not fit a {a.budget}-frame budget")
            print(f"solved peak for a {a.budget}-frame budget at curve {a.curve}: {hi}x")
        else:
            hi = int(hi_s)
        if a.frames % a.steps:
            ap.error(f"--frames {a.frames} does not divide by --steps {a.steps}")
        step = a.frames // a.steps
        speeds = solve_ramp(lo, hi, a.steps, a.curve)

    info = probe(a.src) if os.path.exists(a.src) else {}
    src_n = int(info.get("nb_read_frames") or 0) or None
    if src_n and a.src_in:
        src_n -= a.src_in
    rows, total = plan(a.frames, speeds, step, src_n, a.clean_limit)
    rows = [(i, s + a.src_in, ln, sp) for i, s, ln, sp in rows]
    print(f"{len(speeds)} step(s) x {step} frames = {a.frames} output frames")
    print(f"  speeds   {speeds}")
    print(f"  peak     {max(speeds)}x")
    print(f"  consumes {total} source frames from {a.src_in} to {a.src_in + total}"
          + (f" (file has {int(info.get('nb_read_frames') or 0)})" if info else ""))
    vf, complex_graph = filtergraph(rows, a.fps, blur=not a.no_blur)
    if a.dry_run:
        print("\n" + vf)
        return 0
    if not a.out:
        ap.error("--out is required unless --dry-run")

    cmd = ["ffmpeg", "-v", "error", "-y", "-i", a.src]
    cmd += (["-filter_complex", vf, "-map", "[v]"] if complex_graph else ["-vf", vf])
    cmd += ["-frames:v", str(a.frames), "-an"]
    cmd += (["-c:v", a.codec, "-profile:v", "3", "-pix_fmt", "yuv422p10le"]
            if a.codec == "prores_ks" else ["-c:v", a.codec])
    cmd.append(a.out)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"ERROR: ffmpeg failed\n{r.stderr[-1500:]}")
    got = probe(a.out)
    n = int(got.get("nb_read_frames") or 0)
    print(f"-> {a.out}: {got.get('width')}x{got.get('height')} @ {got.get('r_frame_rate')}, "
          f"{n} frames")
    if n != a.frames:
        sys.exit(f"ERROR: wrote {n} frames, wanted {a.frames}")
    print(f"   {a.frames} frames exactly  OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

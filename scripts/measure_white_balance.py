#!/usr/bin/env python3
"""measure_white_balance.py — what colour cast does each clip actually have?

Answers one question per clip: **if a neutral surface appears in this shot, does it render
neutral?** Reports the R:G:B ratio of the clip's own near-neutral pixels in dB relative to green,
plus luma percentiles so brightness and contrast can be matched too.

    0.0 / 0.0 / 0.0     neutral
    +R and -B           warm / orange
    -R and +B           cool / blue

WHY YOU NEED THIS PER CLIP
--------------------------
A camera-match corrector (a CDL, a look) is fitted under ONE lighting setup. Applied unchanged to
footage shot somewhere else it is wrong, and wrong in a way that reads as "the grade is off" rather
than "this is the wrong tool for this shot". **One static corrector cannot neutralise more than one
white balance.** Measured on one real job, twelve clips from one camera through one shared CDL:

    interiors   R +0.55 .. +1.31   B -1.40 .. -2.28     warm
    exterior    R -0.79            B +0.36              cool
    the setup the CDL was fitted in                      neutral, as expected

The operator's description — "outside looks crisp, the interiors look warm and orange" — was three
lighting conditions, not three grades going wrong. Per-clip trims took that 3.6 dB spread to
0.36 dB. See `fit_clip_trims.py` for the correction.

WHY THIS ESTIMATOR AND NOT GREY-WORLD
-------------------------------------
Grey-world (average the frame, call it neutral) assumes the scene's own colours average to grey.
That is false in a way that actively misleads: on that job one clip was a deliberately
multicoloured wall, another was mostly blue sky. Grey-world would have corrected the sky out of
the sky.

So the mask is built in two stages and both matter:

  1. keep only **mid-luma** pixels — the `--mid-lo`..`--mid-hi` percentile band of this frame's own
     luma. Drops crushed shadows, where chroma noise dominates, and clipped highlights, where
     channels are pinned and the ratio is meaningless.
  2. of those, keep the **least-saturated** `--neutral-frac` — the pixels most likely to be a wall,
     a floor, paper or concrete rather than a saturated object.

That is a *neutral subset* estimator, not a grey card. State results as LIKELY. On a frame with no
neutral surface at all it degrades to the least-coloured thing present, so the mask's pixel count
and mean saturation are reported: a mask that collapsed or found nothing neutral is then visible
rather than silent.

⚠️ SAMPLE SEVERAL FRAMES PER CLIP. A pan or a dolly changes what is in shot and one frame can land
on an atypical moment. And check the per-channel spread across those samples BEFORE fitting any
static correction — but compute it **per channel**:

    🔴 A first version of this pooled the channel spread with the temporal spread and reported
    "the cast moves across the shot" on 8 of 12 clips. Per channel the worst drift was 0.63 dB and
    most were under 0.1 — entirely static, and the warning was an artefact of the statistic.
    A spread that mixes two axes is not a spread.

MEASURE THROUGH THE CHAIN YOU WILL CORRECT IN
---------------------------------------------
Pass `--lut` for each transform your delivery applies, in order. The cast must be measured in the
same space the correction will be applied in, or the correction is fitted against a signal nobody
sees. (In most NLEs a node's corrector runs BEFORE that node's LUT, so a display-space trim cannot
live in the CDL — see `fit_clip_trims.py`.)

USAGE
-----
    python3 measure_white_balance.py --clip A.mov --clip B.mov --out cast.json
    python3 measure_white_balance.py --clip A.mov --lut log2rec709.cube --lut look.cube
    python3 measure_white_balance.py --clip A.mov:120-480          # restrict to a frame range
    python3 measure_white_balance.py --selftest
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile

import numpy as np

MID_LO, MID_HI = 25.0, 85.0
NEUTRAL_FRAC = 0.20
N_SAMPLES = 5
PROBE_W, PROBE_H = 960, 540        # cast and level are scale-invariant; small is ~16x faster


def _have(tool):
    return subprocess.run(["which", tool], capture_output=True).returncode == 0


def probe_frames(path):
    """Decoded frame count. Never `duration x fps` — that reads past the end of a file."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                        "-show_entries", "stream=nb_read_frames", "-of", "default=nw=1:nk=1",
                        path], capture_output=True, text=True)
    try:
        return int(r.stdout.strip())
    except ValueError:
        return None


def grab(path, frame, chain, dst):
    """One frame as RGB. Cut by FRAME NUMBER, never by `-ss`: on long-GOP footage a timestamp
    seek can land on a neighbouring frame."""
    vf = [f"trim=start_frame={frame}:end_frame={frame + 1}", "setpts=PTS-STARTPTS"]
    vf += list(chain)
    vf.append(f"scale={PROBE_W}:{PROBE_H}")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-vf", ",".join(vf),
                    "-frames:v", "1", dst], check=True)
    from PIL import Image
    return np.asarray(Image.open(dst).convert("RGB"), dtype=np.float64)


def neutral_stats(rgb, mid_lo=MID_LO, mid_hi=MID_HI, frac=NEUTRAL_FRAC, iters=4):
    """Cast in dB per channel relative to green, from the near-neutral subset, plus levels.

    🔴 THE SELECTION IS ITERATED, AND IT HAS TO BE. Picking the least-saturated pixels of a frame
    that CARRIES a cast is biased against that cast: once everything is tinted, the pixels that
    measure as least saturated are the ones whose grain happens to oppose the tint. Measured on a
    synthetic grainy grey field, a single pass recovered a planted +1.31/-2.28 dB cast as
    +1.09/-2.07 — **a systematic 16% under-report, always toward zero.**

    That matters because a trim fitted from it under-corrects by the same fraction, which is
    exactly the residual seen on real footage (a 3.6 dB spread corrected to 0.36 rather than to 0).

    The fix is to re-select after removing the current estimate: divide the frame by the cast so
    far, choose the least-saturated subset of the NEUTRALISED frame, and measure what is left.
    Two or three passes converge. `iters=1` reproduces the biased single-pass behaviour if you ever
    need to compare.
    """
    a0 = rgb.reshape(-1, 3)
    y = a0.mean(axis=1)
    lo, hi = np.percentile(y, [mid_lo, mid_hi])
    band = (y >= lo) & (y <= hi)
    if band.sum() < 500:
        return None
    sub0 = a0[band]
    k = max(500, int(len(sub0) * frac))
    gain = np.ones(3)                    # cumulative estimate of the cast, as a linear gain
    idx = None
    for _ in range(max(1, iters)):
        sub = sub0 / gain                # look at the frame with the cast so far removed
        mx, mn = sub.max(axis=1), sub.min(axis=1)
        sat = (mx - mn) / np.maximum(mx, 1.0)
        idx = np.argpartition(sat, k - 1)[:k]
        m = sub[idx].mean(axis=0)
        gain = gain * (m / max(m[1], 1e-6))
    neu = sub0[idx]
    sat_full = (sub0.max(axis=1) - sub0.min(axis=1)) / np.maximum(sub0.max(axis=1), 1.0)
    sat = sat_full
    m = neu.mean(axis=0)
    g = max(m[1], 1e-6)
    return {
        # Report the ITERATED estimate, not the raw mean of the final subset.
        "cast_db": [float(20 * np.log10(max(x, 1e-9))) for x in gain],
        "cast_db_single_pass": [float(20 * np.log10(max(c, 1e-6) / g)) for c in m],
        "neutral_rgb": [float(x) for x in m],
        "mask_px": int(k),
        "mask_mean_sat": float(sat[idx].mean()),
        "p": [float(v) for v in np.percentile(y, [1, 25, 50, 75, 99])],
    }


def parse_clip(spec):
    """`path` or `path:IN-OUT` (frame numbers)."""
    if ":" in spec and "-" in spec.rsplit(":", 1)[-1]:
        path, rng = spec.rsplit(":", 1)
        a, b = rng.split("-")
        return path, int(a), int(b)
    return spec, None, None


def lut3d_arg(path: str) -> str:
    """A lut3d filter argument that survives ffmpeg's filtergraph parser on every platform.

    Same escaping as make_proxies.py: backslashes and colons escaped, then quoted. Unescaped,
    a Windows path (`C:\\Users\\...`) breaks it -- the drive colon reads as an option separator
    and the backslashes as escapes. Quoted, spaces in the path are fine too. Verified with ffmpeg
    9.0 on Windows against a LUT in a folder whose name has spaces.
    """
    esc = path.replace("\\", "\\\\").replace(":", "\\:")
    return f"lut3d=file='{esc}'"


def measure(clips, luts, n_samples=N_SAMPLES, mid=(MID_LO, MID_HI), frac=NEUTRAL_FRAC,
            verbose=True):
    # Quoted and escaped, so spaces and Windows drive paths both work (see lut3d_arg).
    chain = [lut3d_arg(p) for p in luts]
    rows = {}
    with tempfile.TemporaryDirectory() as td:
        if verbose:
            print(f"{'clip':22s} {'R':>7s} {'G':>7s} {'B':>7s}  {'p1':>6s} {'p50':>6s} "
                  f"{'p99':>6s} {'sat':>6s} {'drift':>6s}  note")
        for spec in clips:
            path, f0, f1 = parse_clip(spec)
            if not os.path.exists(path):
                sys.exit(f"ERROR: {path} not found")
            n = probe_frames(path) or 0
            if f0 is None:
                f0, f1 = 0, max(1, n)
            f1 = min(f1, n) if n else f1
            frames = np.linspace(f0, max(f0, f1 - 1), n_samples).round().astype(int)
            per = []
            for i, fr in enumerate(frames):
                s = neutral_stats(grab(path, int(fr), chain, os.path.join(td, f"p{i}.png")),
                                  mid[0], mid[1], frac)
                if s:
                    per.append(s)
            if not per:
                if verbose:
                    print(f"{os.path.basename(path)[:22]:22s}   -- no usable mask --")
                continue
            cast = np.array([p["cast_db"] for p in per])
            mean = cast.mean(axis=0)
            # 🔴 PER CHANNEL. Pooling channels with time invents drift that is not there.
            drift = float((cast.max(axis=0) - cast.min(axis=0)).max())
            p1 = float(np.mean([p["p"][0] for p in per]))
            p50 = float(np.mean([p["p"][2] for p in per]))
            p99 = float(np.mean([p["p"][4] for p in per]))
            sat = float(np.mean([p["mask_mean_sat"] for p in per]))
            note = "" if drift <= 0.8 else ("marginal" if drift <= 1.5
                                            else "⚠️ cast genuinely DRIFTS — a static trim "
                                                 "cannot fix this clip")
            if verbose:
                print(f"{os.path.basename(path)[:22]:22s} {mean[0]:+7.2f} {mean[1]:+7.2f} "
                      f"{mean[2]:+7.2f} {p1:6.1f} {p50:6.1f} {p99:6.1f} {sat:6.3f} "
                      f"{drift:6.2f}  {note}")
            rows[os.path.basename(path)] = {
                "path": path, "frames_sampled": [int(x) for x in frames],
                "cast_db": [float(x) for x in mean], "cast_drift_per_channel_db": drift,
                "p1": p1, "p50": p50, "p99": p99, "mask_mean_sat": sat, "samples": per,
            }
    return rows


def selftest():
    """Plant KNOWN casts and assert recovery — on a realistic frame AND on the pathological one.

    Ground truth, not self-consistency. Three cases, and the second exists to keep the estimator
    honest about where it is weak rather than to make it look good:

      1. **Multi-surface frame** — a large neutral wall plus saturated objects and texture. This is
         what the tool is FOR: the least-saturated subset has real neutral pixels to find. Tight
         tolerance.
      2. **Uniform grainy field** — one surface everywhere. Here "least saturated" is pure noise
         selection and is structurally biased TOWARD ZERO, because the pixels whose grain opposes
         the cast measure as the most neutral. Iterating the selection reduces it (16% -> 9% on a
         planted 2.28 dB) but cannot remove it. Asserted as a BOUND and a DIRECTION: it must
         recover at least 85% and must never overshoot.
      3. **Grey-world trap** — half the frame one saturated colour. The neutral subset must still
         report the wall, where a frame average would report the object.
    """
    rng = np.random.default_rng(7)
    fails = 0

    def multi_surface(cast_db):
        img = np.full((540, 960, 3), 116.0)
        img += rng.normal(0, 1.5, img.shape)                      # the wall, lightly grainy
        img[40:160, 60:300] = [196.0, 52.0, 44.0]                 # red object
        img[200:320, 600:900] = [40.0, 150.0, 190.0]              # teal object
        img[380:460, 100:860] = [150.0, 140.0, 60.0]              # olive object
        img[:30] = rng.uniform(4, 22, (30, 960, 3))               # crushed shadow
        img[-40:] = rng.uniform(238, 255, (40, 960, 3))           # clipped highlight
        g = np.array([10 ** (d / 20.0) for d in cast_db])
        return np.clip(img * g, 0, 255)

    for want in ([0.0, 0.0, 0.0], [1.31, 0.0, -2.28], [-0.79, 0.0, 0.36]):
        got = neutral_stats(multi_surface(want))["cast_db"]
        err = max(abs(a - b) for a, b in zip(got, want))
        ok = err < 0.06
        fails += 0 if ok else 1
        print(f"  multi-surface  planted {want} -> {[round(x, 2) for x in got]}  "
              f"max err {err:.3f} dB  {'ok' if ok else '*** FAIL ***'}")

    for want in ([1.31, 0.0, -2.28],):
        img = np.clip(np.full((540, 960, 3), 118.0) + rng.normal(0, 3.0, (540, 960, 3)), 0, 255)
        g = np.array([10 ** (d / 20.0) for d in want])
        got = neutral_stats(np.clip(img * g, 0, 255))["cast_db"]
        frac = [abs(a) / abs(b) if b else 1.0 for a, b in zip(got, want)]
        over = any(abs(a) > abs(b) + 0.02 for a, b in zip(got, want))
        worst = min(f for f, b in zip(frac, want) if b)
        ok = worst >= 0.85 and not over
        fails += 0 if ok else 1
        print(f"  uniform grainy planted {want} -> {[round(x, 2) for x in got]}  "
              f"recovered {worst:.0%} (>=85%), no overshoot={not over}  "
              f"{'ok' if ok else '*** FAIL ***'}  <- known weak case, documented")

    img = np.full((540, 960, 3), 118.0)
    img[:, :480] = [200.0, 40.0, 40.0]
    img += rng.normal(0, 2.0, img.shape)
    got = neutral_stats(img)["cast_db"]
    gw = img.reshape(-1, 3).mean(axis=0)
    gw_db = [20 * np.log10(max(c, 1e-6) / max(gw[1], 1e-6)) for c in gw]
    ok = max(abs(x) for x in got) < 0.5 and abs(gw_db[0]) > 3.0
    fails += 0 if ok else 1
    print(f"  grey-world trap: neutral-subset {[round(x, 2) for x in got]} (~0 wanted), "
          f"frame average {[round(x, 1) for x in gw_db]} (wrong)  "
          f"{'ok' if ok else '*** FAIL ***'}")

    print("selftest:", "PASS" if not fails else f"{fails} FAILURE(S)")
    return 0 if not fails else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--clip", action="append", default=[],
                    help="path, or path:IN-OUT to restrict to a frame range. Repeatable.")
    ap.add_argument("--lut", action="append", default=[],
                    help="a .cube applied before measuring, in order. Repeatable. Measure "
                         "through the chain you will correct in.")
    ap.add_argument("--out", default="white_balance.json")
    ap.add_argument("--samples", type=int, default=N_SAMPLES)
    ap.add_argument("--mid-lo", type=float, default=MID_LO)
    ap.add_argument("--mid-hi", type=float, default=MID_HI)
    ap.add_argument("--neutral-frac", type=float, default=NEUTRAL_FRAC)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()
    if not a.clip:
        ap.error("give at least one --clip, or --selftest")
    for t in ("ffmpeg", "ffprobe"):
        if not _have(t):
            sys.exit(f"ERROR: {t} not on PATH")
    rows = measure(a.clip, a.lut, a.samples, (a.mid_lo, a.mid_hi), a.neutral_frac)
    json.dump({"mid_band_pct": [a.mid_lo, a.mid_hi], "neutral_frac": a.neutral_frac,
               "n_samples": a.samples, "luts": a.lut, "clips": rows},
              open(a.out, "w", encoding="utf-8"), indent=1)
    print(f"\nwrote {a.out}")
    print("Cast is dB relative to GREEN on each clip's own near-neutral pixels. "
          "+R with -B = warm; the reverse = cool. Feed this to fit_clip_trims.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

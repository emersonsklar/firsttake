#!/usr/bin/env python3
"""conform_graphic.py — vet and conform a supplied motion graphic to your delivery spec.

A card or lower-third arrives from a designer. Before it goes in a timeline it has to be checked
and conformed, and **"they sent a vertical file" is not the same as "the file matches the spec"**.
On one job three cards were delivered for one 5-second slot and the first two were unusable.

    python3 conform_graphic.py --src card.mp4 --raster 1080x1920 --fps 30000/1001 --frames 150
    python3 conform_graphic.py --src card.mp4 --profile          # just vet it, write nothing
    python3 conform_graphic.py --selftest

WHAT IT CHECKS, AND WHY EACH ONE CAUGHT SOMETHING
-------------------------------------------------
**1. Raster against the filename.** A file named `1080x1920_...mp4` was **608x1080** — half the
delivery height, so a 1.78x upscale of a card made of nothing but fine text and a logo. The
filename was the optimistic one. This refuses an upscale by default (`--allow-upscale` to override)
and reports the factor.

**2. Constant frame rate?** All three deliveries were **variable**: 30.31, 62.49, and finally a
clean 30.000. A VFR file cannot be addressed by frame index, so the rate is conformed FIRST and
everything else follows. ⚠️ `r_frame_rate` may report a container timebase (`600/1` on all three
here) — use `avg_frame_rate` and the actual timestamp intervals.

**3. Where the motion actually is.** 🔴 The check that matters most, and three lines of numpy.
Profile the frame-to-frame difference across the WHOLE file and print the static and moving runs:

      static 0-22, MOVING 22-132, static 132-239      a healthy 3.7 s build, then a hold
      static 0-47, MOVING 48-91, static 92-502        BROKEN: 0.7 s of snap, then 6.6 s frozen

The second one had the right raster and looked fine at a glance. Its animation had collapsed and it
froze **on an unfinished graphic** — a bare plus sign where the brand lockup belongs. **A file's
last frame is not evidence its animation completed, only evidence of where it stopped.** Checking
the end and seeing "a card" is not checking that the card is finished.

**4. Start on the first MOVING frame, not at a nominal time.** The good delivery opened with 0.77 s
of dead hold, so "cut the first second" would have begun 8 frames inside the build. `--start auto`
uses the profile.

CONFORM ORDER, AND WHY
----------------------
    fps  ->  crop  ->  scale  ->  trim by frame number

Frame rate first because frame indices mean nothing in a VFR file. Crop before scaling when the
raster is a pixel off exact — 1216 wide against an ideal 1215 would otherwise apply a 0.08%
anamorphic squeeze, since 1080/1216 != 1920/2160. Trim by frame number, never `-ss`.

⚠️ Graphics are already display-referred. Do **not** give them a camera LUT or a per-clip trim; in
most builds the card is the one clip left alone by the colour stage.

⚠️ And do not overwrite a graphic that a live NLE project references. Rewriting one under an open
project wedged a render that could then not be stopped from the API or the UI, and needed a
force-quit. Regenerate graphics first, build second.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile

import numpy as np

STATIC_EPS = 0.05          # mean abs frame-to-frame difference below this = "not moving"
RUN_MIN = 2                # runs shorter than this are not reported


def probe(path, count=False):
    keys = ("stream=width,height,r_frame_rate,avg_frame_rate,nb_frames"
            + (",nb_read_frames" if count else ""))
    args = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", keys,
            "-of", "json"]
    if count:
        args.append("-count_frames")
    r = subprocess.run(args + [path], capture_output=True, text=True)
    return (json.loads(r.stdout or "{}").get("streams") or [{}])[0]


def timestamps(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "frame=best_effort_timestamp_time", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    return [float(x) for x in r.stdout.replace(",", "\n").split() if x.strip()]


def motion_profile(path, w=135, h=240):
    """Static / moving runs across the whole file, by mean abs frame-to-frame difference."""
    with tempfile.TemporaryDirectory() as td:
        raw = os.path.join(td, "f.raw")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-vf", f"scale={w}:{h}",
                        "-f", "rawvideo", "-pix_fmt", "gray", raw], check=True)
        a = np.fromfile(raw, dtype=np.uint8)
    n = len(a) // (w * h)
    if n < 2:
        return [], np.array([]), n
    a = a[:n * w * h].reshape(n, h, w).astype(np.float32)
    d = np.abs(np.diff(a, axis=0)).mean(axis=(1, 2))
    runs, cur = [], None
    for i, v in enumerate(d):
        st = bool(v < STATIC_EPS)
        if cur is None or cur[0] != st:
            runs.append([st, i, i]); cur = runs[-1]
        else:
            cur[2] = i
    return [(st, i, j + 1) for st, i, j in runs if (j - i) >= RUN_MIN - 1], d, n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src")
    ap.add_argument("--out")
    ap.add_argument("--raster", default="1080x1920", help="WxH delivery raster")
    ap.add_argument("--fps", default="30000/1001")
    ap.add_argument("--frames", type=int, help="exact output frame count")
    ap.add_argument("--start", default="auto",
                    help="first CONFORMED frame to keep: an integer, or 'auto' for the first "
                         "moving frame")
    ap.add_argument("--profile", action="store_true", help="vet only; write nothing")
    ap.add_argument("--allow-upscale", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()
    if not a.src:
        ap.error("--src is required unless --selftest")
    if not os.path.exists(a.src):
        sys.exit(f"ERROR: {a.src} not found")
    out_w, out_h = (int(x) for x in a.raster.lower().split("x"))

    st = probe(a.src)
    w, h = int(st["width"]), int(st["height"])
    print(f"source: {w}x{h}  r_frame_rate={st.get('r_frame_rate')} "
          f"(may be a container timebase, not a rate)  avg={st.get('avg_frame_rate')}  "
          f"nb_frames={st.get('nb_frames')}")

    # 1. raster, and the filename claim
    base = os.path.basename(a.src)
    claim = None
    for tok in base.replace("-", "_").split("_"):
        if "x" in tok.lower():
            p = tok.lower().split("x")
            if len(p) == 2 and p[0].isdigit() and p[1].rstrip(".mp4mov").isdigit():
                claim = (int(p[0]), int(''.join(c for c in p[1] if c.isdigit())))
    if claim and claim != (w, h) and claim != (h, w):
        print(f"  ⚠️ the FILENAME claims {claim[0]}x{claim[1]} but the pixels are {w}x{h}. "
              f"Trust the pixels.")
    if h < out_h:
        msg = (f"  {'⚠️' if a.allow_upscale else '🔴'} {h}px tall against a {out_h}px delivery — "
               f"an UPSCALE of {out_h / h:.2f}x on a graphic made of fine text.")
        print(msg)
        if not a.allow_upscale:
            sys.exit("ERROR: refusing to upscale a supplied graphic. Ask for the correct raster, "
                     "or pass --allow-upscale if you have decided to accept it.")

    # 2. constant frame rate?
    t = timestamps(a.src)
    if len(t) > 2:
        d = [round(b - x, 5) for x, b in zip(t, t[1:])]
        uniq = sorted(set(d))
        mean_fps = 1.0 / (sum(d) / len(d))
        print(f"  {len(t)} frames, {len(uniq)} distinct interval(s), mean {mean_fps:.4f} fps"
              + ("  ⚠️ VARIABLE frame rate — conforming the rate first" if len(uniq) > 1 else
                 "  (constant)"))

    # 3. where the motion is
    runs, diffs, n = motion_profile(a.src)
    src_fps = mean_fps if len(t) > 2 else 30.0
    print("  motion profile:")
    for is_static, i, j in runs:
        print(f"    {'static' if is_static else 'MOVING'} src {i:4d}..{j:4d}  "
              f"({j - i} frames, {(j - i) / src_fps:.2f}s)")
    moving = [(i, j) for s, i, j in runs if not s]
    if not moving:
        print("  🔴 NOTHING MOVES in this file. If it is meant to be animated, it is broken.")
    else:
        first_move, last_move = moving[0][0], moving[-1][1]
        span = (last_move - first_move) / src_fps
        tail = (n - last_move) / src_fps
        print(f"  animation spans {span:.2f}s; {tail:.2f}s of hold after it")
        if span < 1.0:
            print("  🔴 the animation is under a second. On one job this signature — a ~0.7s "
                  "collapse then a long freeze — was a BROKEN EXPORT that never reached its "
                  "final graphic. Look at the last moving frame before accepting it.")

    if a.profile:
        return 0
    if not (a.out and a.frames):
        ap.error("--out and --frames are required unless --profile")

    # 4. where to start
    if a.start == "auto":
        start = moving[0][0] if moving else 0
        print(f"  --start auto -> conformed frame {start} (the first MOVING frame; a nominal "
              f"'cut 1 second' would have started elsewhere)")
    else:
        start = int(a.start)

    ideal_w = int(round(h * out_w / out_h))
    if w < ideal_w:
        sys.exit(f"ERROR: {w}x{h} is narrower than {out_w}:{out_h} at that height "
                 f"({ideal_w}x{h}); conforming would pillarbox or stretch.")
    crop_x = (w - ideal_w) // 2
    if w != ideal_w:
        squeeze = 100 * (1 - (out_w / w) / (out_h / h))
        print(f"  cropping {w} -> {ideal_w} (x={crop_x}) so the scale is uniform; forcing {w} "
              f"straight in would squeeze horizontally by {squeeze:.3f}%")

    vf = (f"fps={a.fps},crop={ideal_w}:{h}:{crop_x}:0,"
          f"scale={out_w}:{out_h}:flags=lanczos,"
          f"trim=start_frame={start}:end_frame={start + a.frames},setpts=PTS-STARTPTS")
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", a.src, "-an", "-vf", vf,
           "-frames:v", str(a.frames), "-c:v", "libx264", "-preset", "slow", "-crf", "16",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", a.out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"ERROR: ffmpeg failed\n{r.stderr[-1200:]}")

    got = probe(a.out, count=True)
    gn = int(got.get("nb_read_frames") or 0)
    print(f"-> {a.out}: {got.get('width')}x{got.get('height')} @ {got.get('r_frame_rate')}, "
          f"{gn} frames")
    if (int(got["width"]), int(got["height"])) != (out_w, out_h):
        sys.exit("ERROR: wrong output raster")
    if got.get("r_frame_rate") != a.fps:
        sys.exit(f"ERROR: output rate is {got.get('r_frame_rate')}, not {a.fps}")
    if gn != a.frames:
        sys.exit(f"ERROR: wrote {gn} frames, wanted {a.frames}")
    print("   raster, rate and frame count all exact  OK")
    return 0


def selftest():
    """Synthesise a healthy card and a broken one, and assert the profiler tells them apart.

    Ground truth: two clips are generated with a KNOWN motion structure — one animating over ~3 s
    then holding, one snapping in a fraction of a second then freezing — and the profiler has to
    report the right runs for each. This is the check that caught a real broken delivery, so it is
    the one worth planting faults for.
    """
    if subprocess.run(["which", "ffmpeg"], capture_output=True).returncode:
        print("selftest: ffmpeg not on PATH"); return 1
    fails = 0
    with tempfile.TemporaryDirectory() as td:
        def make(path, hold, move, tail, w=160, h=284):
            """hold static frames, then `move` frames of a moving bar, then `tail` static."""
            import struct
            frames = []
            for i in range(hold):
                frames.append(np.full((h, w), 20, np.uint8))
            for i in range(move):
                f = np.full((h, w), 20, np.uint8)
                y = int((h - 20) * i / max(1, move - 1))
                f[y:y + 20, :] = 220
                frames.append(f)
            last = frames[-1].copy()
            for i in range(tail):
                frames.append(last)
            raw = os.path.join(td, os.path.basename(path) + ".raw")
            with open(raw, "wb") as fh:
                for f in frames:
                    fh.write(f.tobytes())
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray",
                            "-s", f"{w}x{h}", "-r", "30", "-i", raw,
                            "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", path],
                           check=True)
            return len(frames)

        good = os.path.join(td, "good.mp4"); n_good = make(good, 22, 100, 100)
        bad = os.path.join(td, "bad.mp4"); n_bad = make(bad, 22, 18, 200)

        for label, path, want_span_s, expect_flag in (("healthy", good, 100 / 30.0, False),
                                                      ("broken", bad, 18 / 30.0, True)):
            runs, _, n = motion_profile(path)
            moving = [(i, j) for s, i, j in runs if not s]
            ok = bool(moving)
            span = ((moving[-1][1] - moving[0][0]) / 30.0) if moving else 0.0
            near = abs(span - want_span_s) < 0.35
            flagged = span < 1.0
            good_call = near and (flagged == expect_flag)
            fails += 0 if (ok and good_call) else 1
            print(f"  {label:8s}: {len(runs)} runs, animation {span:.2f}s "
                  f"(planted {want_span_s:.2f}s), under-1s flag={flagged} "
                  f"(expected {expect_flag})  {'ok' if ok and good_call else '*** FAIL ***'}")

        # a still image must report as nothing moving, not as a healthy card
        still = os.path.join(td, "still.mp4"); make(still, 60, 1, 0)
        runs, _, _ = motion_profile(still)
        mv = [(i, j) for s, i, j in runs if not s]
        okc = not mv
        fails += 0 if okc else 1
        print(f"  static file: reports no motion  {'ok' if okc else '*** FAIL ***'}")
    print("selftest:", "PASS" if not fails else f"{fails} FAILURE(S)")
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())

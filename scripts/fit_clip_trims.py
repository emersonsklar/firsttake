#!/usr/bin/env python3
"""fit_clip_trims.py — per-clip white balance and black point, baked into per-clip LUTs.

Takes the measurement from `measure_white_balance.py` and produces, per clip, one `.cube` that
applies a display-space trim ON TOP of your input transform:

    clip_cube(x) = trim( input_LUT(x) )

    trim:  out = ((in * gain_c) - black) / (1 - black)      per channel, clipped to [0,1]

* `gain_c = 10^(-cast_c / 20)` neutralises the measured cast.
* `black` lands every clip's black point on a common target. **"Washed out" IS a lifted black
  floor** — on one job the clips ran p1 = 0.4 to 13.5 of 255, and landing them all at ~4 is what
  made twelve locations look like one piece. Dividing by `(1 - black)` restores the white point so
  pulling the blacks down does not darken the image.

🔴 WHY THE TRIM GOES IN THE LUT AND NOT IN THE CDL
--------------------------------------------------
The cast is measured AFTER the input transform, so the correction has to be applied after it too —
measure and correct in the same space and it is one shot with no iteration. But in most NLEs a
node's **corrector runs BEFORE that node's LUT**, on the log signal, so a display-space trim cannot
live in the CDL. Composing the trim into the LUT puts it in the right place, leaves the shared
camera CDL untouched, needs no second node, and gives the NLE and any ffmpeg cross-check the
identical file — so the two cannot disagree about the grade.

⚠️ REGENERATE THESE CUBES EVERY BUILD AND NEVER COMMIT THEM. A grade baked once into a file that
nothing re-derives will eventually disagree with the numbers it came from, and the disagreement
looks like a mystery rather than a stale file. One job lost eight review renders to exactly that:
two committed cubes that did not implement the CDL they were named for, off by 13%.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
* **It does not match exposure.** Scene brightness legitimately differs — a dark display is not a
  bright corridor — and matching mid-grey across them lifts the dark ones into mush.
* **It does not flatten hue.** Only each clip's *neutral* pixels are driven to neutral, so a blue
  sky stays blue; what changes is the cast on its concrete and cloud. Correcting the neutral point
  is what makes shots cut together.
* **It is not a grey card.** The input is a `LIKELY` estimate, so every clip keeps a `strength`
  (1.0 = fully neutral, 0.0 = leave it) that a re-fit **preserves** — it is a judgement, and
  re-measuring must not silently discard it. A shot whose warmth is motivated (tungsten, firelight)
  is where you will want it below 1.0.

USAGE
-----
    python3 fit_clip_trims.py --fit white_balance.json --out trims.json
    python3 fit_clip_trims.py --trims trims.json --lut log2rec709.cube --cubes out/luts
    python3 fit_clip_trims.py --selftest
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

GRID = 33                  # exact for the affine part; see bake()
TARGET_BLACK = 4.0         # common black point, in 0-255 display code


def fit(measurement, target_black=TARGET_BLACK, previous=None, verbose=True):
    """Derive one trim per clip from a measure_white_balance.py report."""
    meas = measurement if isinstance(measurement, dict) else json.load(open(measurement))
    keep = ((previous or {}).get("clips") or {}) if previous else {}
    clips = {}
    for name, v in (meas.get("clips") or {}).items():
        cast = v["cast_db"]
        strength = float((keep.get(name) or {}).get("strength", 1.0))
        gain = [float(10.0 ** (-c * strength / 20.0)) for c in cast]
        p1_after = float(v.get("p1", target_black)) * float(np.mean(gain))
        black = max(0.0, p1_after - target_black) / 255.0
        clips[name] = {
            "measured_cast_db": [round(c, 3) for c in cast],
            "measured_p1": round(float(v.get("p1", 0.0)), 2),
            "strength": strength,
            "gain": [round(g, 6) for g in gain],
            "black": round(black, 6),
            "effect": f"neutralises {cast[0]:+.2f}/{cast[1]:+.2f}/{cast[2]:+.2f} dB, "
                      f"p1 {v.get('p1', 0.0):.1f} -> {target_black:.1f}",
        }
    doc = {"target_black": target_black, "grid": GRID, "clips": clips,
           "_note": ["Per-clip display-space trim: white balance + black point.",
                     "TUNE `strength` BY HAND HERE (1.0 fully neutral, 0.0 leave the cast).",
                     "A re-fit preserves strength; everything else is re-derived.",
                     "Cubes are generated from this at build time and must not be committed."]}
    if verbose:
        print(f"{'clip':22s} {'str':>5s} {'gain R':>8s} {'gain G':>8s} {'gain B':>8s} "
              f"{'black':>6s}  effect")
        for c, t in clips.items():
            print(f"{c[:22]:22s} {t['strength']:5.2f} {t['gain'][0]:8.4f} {t['gain'][1]:8.4f} "
                  f"{t['gain'][2]:8.4f} {t['black'] * 255:6.2f}  {t['effect']}")
    return doc


def read_cube(path):
    rows, n = [], None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            t = line.strip()
            if not t or t.startswith("#"):
                continue
            if t.upper().startswith("LUT_3D_SIZE"):
                n = int(t.split()[1]); continue
            if t[0].isdigit() or t[0] in "+-.":
                p = t.split()
                if len(p) == 3:
                    rows.append([float(x) for x in p])
    if n is None or len(rows) != n ** 3:
        raise SystemExit(f"ERROR: {path} is not a readable 3D cube (size {n}, {len(rows)} rows)")
    return n, np.array(rows, dtype=np.float64)


def sample_cube(n, table, pts):
    """Trilinear sample of a .cube table (r fastest, then g, then b) at pts in [0,1]^3."""
    t = table.reshape(n, n, n, 3)
    p = np.clip(pts, 0.0, 1.0) * (n - 1)
    i0 = np.floor(p).astype(int)
    i1 = np.minimum(i0 + 1, n - 1)
    f = p - i0
    r0, g0, b0 = i0[:, 0], i0[:, 1], i0[:, 2]
    r1, g1, b1 = i1[:, 0], i1[:, 1], i1[:, 2]
    fr, fg, fb = f[:, 0:1], f[:, 1:2], f[:, 2:3]
    c00 = t[b0, g0, r0] * (1 - fr) + t[b0, g0, r1] * fr
    c01 = t[b0, g1, r0] * (1 - fr) + t[b0, g1, r1] * fr
    c10 = t[b1, g0, r0] * (1 - fr) + t[b1, g0, r1] * fr
    c11 = t[b1, g1, r0] * (1 - fr) + t[b1, g1, r1] * fr
    return (c00 * (1 - fg) + c01 * fg) * (1 - fb) + (c10 * (1 - fg) + c11 * fg) * fb


def apply_trim(rgb, trim):
    """The trim, in display space, on [0,1]. ONE definition, used by bake and by the selftest."""
    g = np.asarray(trim["gain"], dtype=np.float64)
    k = float(trim["black"])
    out = rgb * g
    if k > 0.0:
        out = (out - k) / (1.0 - k)
    elif k < 0.0:
        out = out * (1.0 + k) - k
    return np.clip(out, 0.0, 1.0)


def bake(name, trim, input_lut, dst_dir, grid=GRID, resolve_dialect=True):
    """Write `trim(input_LUT(x))` as one cube and return its path.

    The trim is affine (per-channel gain + offset), and trilinear interpolation of an affine
    function is that function, so the trim contributes NO interpolation error at any grid size.
    Resampling the input LUT does — use a grid that matches it (33 for 33) and the resample lands
    on the source's own nodes.

    🔴 `resolve_dialect` writes ASCII-only, CRLF, tab-separated. DaVinci Resolve's parser returns
    failure with no reason on a non-ASCII byte — **a UTF-8 em-dash in a comment line made SetLUT
    return False on every clip.** ffmpeg's `lut3d` splits on arbitrary whitespace and ignores CR,
    so one file in this dialect feeds both, which is the point.

    ⚠️ The flag is named after the application whose parser forced it, but the dialect is the
    PORTABLE one, not a Resolve quirk: it is also what Adobe's `.cube` reader accepts, so leave it on
    unless something specifically demands LF. "Strictest wins" is the rule — a file that satisfies
    the fussiest parser satisfies the others, and one bake in one dialect means two pipelines cannot
    diverge.
    ⚠️ Resolve also refuses any LUT outside its own library directory, and caches that library at
    load — copy the cube in and call `project.RefreshLUTList()`. Keep a space-free copy for
    ffmpeg, whose lut3d parser fails on spaces, and assert the two copies are byte-identical.
    """
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, f"clip_{name}.cube")
    n_src, table = read_cube(input_lut)
    ax = np.linspace(0.0, 1.0, grid)
    b, g, r = np.meshgrid(ax, ax, ax, indexing="ij")     # .cube order: r fastest, then g, then b
    pts = np.stack([r.ravel(), g.ravel(), b.ravel()], axis=1)
    out = apply_trim(sample_cube(n_src, table, pts), trim)
    nl = "\r\n" if resolve_dialect else "\n"
    sep = "\t" if resolve_dialect else " "
    enc = "ascii" if resolve_dialect else "utf-8"
    with open(dst, "w", encoding=enc, newline=nl) as f:
        f.write(f"# GENERATED by fit_clip_trims for {name} - do NOT commit, do NOT edit.\n")
        f.write("# = trim(input_LUT(x))\n")
        f.write(f"# gain={trim['gain']} black={trim['black']:.6f} strength={trim['strength']}\n")
        f.write(f"# input LUT: {os.path.basename(input_lut)}\n#\n\n")
        f.write(f"LUT_3D_SIZE{sep}{grid}\n\n")
        for v in out:
            f.write(f"{v[0]:.6f}{sep}{v[1]:.6f}{sep}{v[2]:.6f}{sep}\n")
    return dst


def _identity_cube(path, n=GRID):
    ax = np.linspace(0.0, 1.0, n)
    b, g, r = np.meshgrid(ax, ax, ax, indexing="ij")
    with open(path, "w", encoding="ascii", newline="\r\n") as f:
        f.write(f"LUT_3D_SIZE\t{n}\n\n")
        for rr, gg, bb in zip(r.ravel(), g.ravel(), b.ravel()):
            f.write(f"{rr:.6f}\t{gg:.6f}\t{bb:.6f}\t\n")
    return path


def selftest():
    """End to end on planted ground truth: cast -> fit -> bake -> apply -> is it neutral?

    The chain that matters is not `bake` alone but measure->fit->bake, so this plants a known cast,
    runs the real fit, bakes against an identity input LUT, samples the cube off-node, and asserts
    the result is neutral. Then it checks the cube is ASCII (the Resolve trap) and that a composed
    cube equals trim-of-LUT evaluated directly.
    """
    import tempfile
    fails = 0
    with tempfile.TemporaryDirectory() as td:
        lut = _identity_cube(os.path.join(td, "identity.cube"))
        for cast in ([1.31, 0.0, -2.28], [-0.79, 0.0, 0.36], [0.0, 0.0, 0.0]):
            meas = {"clips": {"c": {"cast_db": cast, "p1": 9.0}}}
            doc = fit(meas, verbose=False)
            trim = doc["clips"]["c"]
            cube = bake("c", trim, lut, td)
            n, tab = read_cube(cube)
            # a mid-grey that CARRIES the cast must come out neutral
            grey = 0.45
            tinted = np.array([[grey * 10 ** (c / 20.0) for c in cast]])
            got = sample_cube(n, tab, tinted)[0]
            spread = float(got.max() - got.min())
            ok = spread < 1.5 / 255.0
            fails += 0 if ok else 1
            print(f"  cast {cast} -> baked cube maps the tinted grey to "
                  f"{[round(x, 4) for x in got]}, spread {spread * 255:.2f}/255  "
                  f"{'ok' if ok else '*** FAIL ***'}")
            # composition is exact off-node
            want = apply_trim(sample_cube(*read_cube(lut), tinted), trim)[0]
            err = float(np.abs(got - want).max())
            ok2 = err < 2.0 / 255.0
            fails += 0 if ok2 else 1
            print(f"    composed vs direct trim-of-LUT: {err * 255:.3f}/255  "
                  f"{'ok' if ok2 else '*** FAIL ***'}")
            raw = open(cube, "rb").read()
            ok3 = all(byte < 128 for byte in raw) and b"\r\n" in raw
            fails += 0 if ok3 else 1
            print(f"    ASCII-only and CRLF (Resolve's parser): "
                  f"{'ok' if ok3 else '*** FAIL ***'}")
        # black point: p1 above target is pulled down, at/below target is left alone
        doc = fit({"clips": {"hi": {"cast_db": [0, 0, 0], "p1": 13.5},
                             "lo": {"cast_db": [0, 0, 0], "p1": 2.0}}}, verbose=False)
        okb = doc["clips"]["hi"]["black"] > 0 and doc["clips"]["lo"]["black"] == 0
        fails += 0 if okb else 1
        print(f"  black point: p1 13.5 -> pull {doc['clips']['hi']['black'] * 255:.1f}, "
              f"p1 2.0 -> pull {doc['clips']['lo']['black'] * 255:.1f} (0 expected)  "
              f"{'ok' if okb else '*** FAIL ***'}")
        # strength is preserved across a re-fit
        prev = {"clips": {"c": {"strength": 0.5}}}
        doc2 = fit({"clips": {"c": {"cast_db": [2.0, 0, 0], "p1": 4.0}}}, previous=prev,
                   verbose=False)
        oks = abs(doc2["clips"]["c"]["strength"] - 0.5) < 1e-9 and \
            abs(doc2["clips"]["c"]["gain"][0] - 10 ** (-1.0 / 20)) < 1e-6
        fails += 0 if oks else 1
        print(f"  strength preserved across a re-fit and halves the correction  "
              f"{'ok' if oks else '*** FAIL ***'}")
    print("selftest:", "PASS" if not fails else f"{fails} FAILURE(S)")
    return 0 if not fails else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fit", metavar="MEASUREMENT_JSON",
                    help="output of measure_white_balance.py")
    ap.add_argument("--trims", default="clip_trims.json", help="the trims file to write or read")
    ap.add_argument("--target-black", type=float, default=TARGET_BLACK)
    ap.add_argument("--lut", help="input transform to compose the trims onto")
    ap.add_argument("--cubes", metavar="DIR", help="bake one cube per clip into DIR")
    ap.add_argument("--no-resolve-dialect", action="store_true",
                    help="write LF/space-separated instead of Resolve's ASCII+CRLF+tab dialect")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()
    if a.fit:
        prev = json.load(open(a.trims)) if os.path.exists(a.trims) else None
        doc = fit(a.fit, a.target_black, prev)
        json.dump(doc, open(a.trims, "w", encoding="utf-8"), indent=1)
        print(f"\nwrote {a.trims}")
    if a.cubes:
        if not a.lut:
            ap.error("--cubes needs --lut (the input transform to compose onto)")
        doc = json.load(open(a.trims))
        for name, trim in (doc.get("clips") or {}).items():
            p = bake(name, trim, a.lut, a.cubes, int(doc.get("grid", GRID)),
                     not a.no_resolve_dialect)
            print(f"  {name} -> {p}")
        print("⚠️ generated, not for commit. Regenerate every build.")
    if not (a.fit or a.cubes):
        ap.error("nothing to do: give --fit, --cubes or --selftest")
    return 0


if __name__ == "__main__":
    # Post tool: needs an active license (see license_check.py). --selftest is exempt.
    from license_check import gate
    gate(__file__)
    raise SystemExit(main())

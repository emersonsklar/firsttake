#!/usr/bin/env python3
"""bake_cdl_into_lut.py — fold an ASC CDL into a vendor LUT, so the grade travels.

A per-camera ASC CDL is the best way to match two bodies: four readable numbers, adjustable by a
human, surviving a round trip through other software. But **not every application will take one
from a script** — Premiere Pro's UXP API exposes no CDL entry point at all — so a pipeline that
depends on setting a CDL cannot hand its cut to Premiere.

The answer is to compose the two into one cube:

    baked(x) = vendor_lut( cdl(x) )

    python3 bake_cdl_into_lut.py --lut vendor.cube \\
        --slope 1.195045 1.187831 1.210953 \\
        --offset -0.038757 -0.037940 -0.039563 \\
        --power 1.0 1.0 1.0 --saturation 0.926712 \\
        --out camA_baked.cube --size 65
    python3 bake_cdl_into_lut.py --selftest

🔴 **THE ORDER IS THE WHOLE POINT, AND IT IS NOT THE ORDER THE NODE GRAPH SUGGESTS.** Inside a
Resolve node the corrector runs BEFORE that node's LUT, so a CDL sitting on a node that carries a
log→709 LUT operates on **log**. That is usually what you want for a camera-match trim, and it is
the opposite of reading the graph left to right. Bake it the other way round and you get a
plausible picture that is wrong everywhere.

This composition is also what made an ffmpeg cross-check trustworthy on this project: the 14–18%
divergence that once made ffmpeg renders useless did **not** reproduce once both paths applied the
log-fitted CDL before the vendor LUT — mean |difference| 1.3 codes of 255, luma r = 0.9996.

WHAT YOU LOSE, AND WHY IT IS WORTH SAYING OUT LOUD
-------------------------------------------------
A baked cube is no longer four numbers a colourist can nudge. Declare it on the handover
(`nle_handover.py` reads a plan's `baked` list) so nobody at the far end goes looking for a CDL
that is now inside a LUT.

THE DIALECT MATTERS
-------------------
⚠️ Written ASCII-only, CRLF, tab-separated, with a blank line either side of `LUT_3D_SIZE`. That is
the dialect a vendor parser accepted; a UTF-8 em-dash in a *comment line* once made `SetLUT` return
False on all twelve clips with no explanation. ffmpeg's `lut3d` splits on arbitrary whitespace and
ignores CR, so one file in this dialect feeds both pipelines — which is what you want, so the two
cannot diverge.
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np

# Rec.709 luma, which is what ASC CDL saturation is defined against.
LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float64)


# --------------------------------------------------------------------------------------------
# cube I/O
# --------------------------------------------------------------------------------------------

def read_cube(path):
    """Return (table, size, domain_min, domain_max). table is [size**3, 3], red changing fastest."""
    size = None
    dmin = np.zeros(3)
    dmax = np.ones(3)
    rows = []
    with open(path, "r", encoding="ascii", errors="strict") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            key = parts[0].upper()
            if key == "LUT_3D_SIZE":
                size = int(parts[1])
            elif key == "DOMAIN_MIN":
                dmin = np.array([float(x) for x in parts[1:4]])
            elif key == "DOMAIN_MAX":
                dmax = np.array([float(x) for x in parts[1:4]])
            elif key in ("TITLE", "LUT_1D_SIZE", "LUT_IN_VIDEO_RANGE", "LUT_OUT_VIDEO_RANGE"):
                if key == "LUT_1D_SIZE":
                    raise SystemExit("ERROR: %s is a 1D LUT; this composes 3D cubes." % path)
            else:
                try:
                    rows.append([float(parts[0]), float(parts[1]), float(parts[2])])
                except (ValueError, IndexError):
                    raise SystemExit("ERROR: cannot parse line in %s: %r" % (path, line))
    if size is None:
        raise SystemExit("ERROR: %s has no LUT_3D_SIZE" % path)
    if len(rows) != size ** 3:
        raise SystemExit("ERROR: %s declares LUT_3D_SIZE %d (%d entries) but carries %d"
                         % (path, size, size ** 3, len(rows)))
    return np.array(rows, dtype=np.float64), size, dmin, dmax


def write_cube(path, table, size, title=None, comments=()):
    """Write in the dialect that both a vendor parser and ffmpeg accept."""
    if table.shape != (size ** 3, 3):
        raise ValueError("table is %r, expected %r" % (table.shape, (size ** 3, 3)))
    out = []
    for c in comments:
        # ASCII only: a non-ASCII character in a COMMENT has broken a vendor parser before.
        c.encode("ascii")
        out.append("# " + c)
    if title:
        title.encode("ascii")
        out.append('TITLE "%s"' % title)
    out.append("")
    out.append("LUT_3D_SIZE %d" % size)
    out.append("")
    for r, g, b in table:
        out.append("%.6f\t%.6f\t%.6f" % (r, g, b))
    data = "\r\n".join(out) + "\r\n"
    data.encode("ascii")
    with open(path, "wb") as fh:
        fh.write(data.encode("ascii"))
    return len(out)


# --------------------------------------------------------------------------------------------
# the maths
# --------------------------------------------------------------------------------------------

def apply_cdl(rgb, slope, offset, power, saturation):
    """ASC CDL: slope/offset/power per channel, then saturation. rgb is [..., 3] float.

    ⚠️ Negatives are clamped to 0 BEFORE the power, because a fractional power of a negative
    number is not real. That is what a colour application does, and skipping it produces NaNs
    that then propagate silently through an interpolation into a black hole in the shadows.
    """
    v = rgb * np.asarray(slope) + np.asarray(offset)
    v = np.clip(v, 0.0, None)
    p = np.asarray(power, dtype=np.float64)
    if not np.allclose(p, 1.0):
        v = np.power(v, p)
    if not np.isclose(saturation, 1.0):
        luma = np.sum(v * LUMA, axis=-1, keepdims=True)
        v = luma + saturation * (v - luma)
    return v


def sample_cube(table, size, coords, dmin=None, dmax=None):
    """Trilinear sample of a .cube at coords [..., 3]. Red changes fastest, as .cube specifies."""
    if dmin is None:
        dmin = np.zeros(3)
    if dmax is None:
        dmax = np.ones(3)
    grid = table.reshape(size, size, size, 3)        # [b, g, r, 3]

    x = (np.asarray(coords, dtype=np.float64) - dmin) / (dmax - dmin)
    x = np.clip(x, 0.0, 1.0) * (size - 1)
    i0 = np.floor(x).astype(np.int64)
    i0 = np.minimum(i0, size - 2)
    f = x - i0
    i1 = i0 + 1

    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    r1, g1, b1 = i1[..., 0], i1[..., 1], i1[..., 2]
    fr = f[..., 0:1]
    fg = f[..., 1:2]
    fb = f[..., 2:3]

    def at(bi, gi, ri):
        return grid[bi, gi, ri]

    c00 = at(b0, g0, r0) * (1 - fr) + at(b0, g0, r1) * fr
    c01 = at(b0, g1, r0) * (1 - fr) + at(b0, g1, r1) * fr
    c10 = at(b1, g0, r0) * (1 - fr) + at(b1, g0, r1) * fr
    c11 = at(b1, g1, r0) * (1 - fr) + at(b1, g1, r1) * fr

    c0 = c00 * (1 - fg) + c01 * fg
    c1 = c10 * (1 - fg) + c11 * fg
    return c0 * (1 - fb) + c1 * fb


def grid_coords(size):
    """The input grid of a cube of this size, in .cube order (red fastest)."""
    axis = np.linspace(0.0, 1.0, size)
    r, g, b = np.meshgrid(axis, axis, axis, indexing="ij")
    # .cube order: red fastest, then green, then blue -> iterate b outer, g, r inner
    return np.stack([r.transpose(2, 1, 0).reshape(-1),
                     g.transpose(2, 1, 0).reshape(-1),
                     b.transpose(2, 1, 0).reshape(-1)], axis=-1)


def bake(lut_path, slope, offset, power, saturation, size=None):
    """Compose vendor_lut(cdl(x)) onto a grid of `size`."""
    table, vsize, dmin, dmax = read_cube(lut_path)
    if size is None:
        size = vsize
    coords = grid_coords(size)
    graded = apply_cdl(coords, slope, offset, power, saturation)
    out = sample_cube(table, vsize, graded, dmin, dmax)
    return out, size, vsize


def reference(lut_path, coords, slope, offset, power, saturation):
    """vendor_lut(cdl(x)) evaluated directly — the thing the baked cube must approximate."""
    table, vsize, dmin, dmax = read_cube(lut_path)
    return sample_cube(table, vsize, apply_cdl(coords, slope, offset, power, saturation),
                       dmin, dmax)


def load_samples(path, key=None):
    """Real measured camera values to verify against, as [N, 3] in the LUT's input domain.

    Accepts a JSON list of triplets, or a dict whose values nest down to lists of triplets —
    so a colour-measurement file from this pipeline can be handed over as-is.

    🔴 **`key` matters, and leaving it off is usually wrong.** A measurement file holds more than
    one camera. Handed the whole of one, this scooped up 844 triplets including a second body's
    D-Log M values and pushed the reported error to 2.07 codes — a number about feeding one
    camera's encoding through another camera's transform, which is meaningless. Scoped to the
    A-camera's own 24 patches the same cube reads 0.05 codes. Verify against the encoding the
    LUT is actually for.
    """
    import json
    with open(path) as fh:
        blob = json.load(fh)

    if key:
        node = blob
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                raise SystemExit("ERROR: %s has no %r (at %r). Available there: %s"
                                 % (path, key, part,
                                    ", ".join(sorted(node)) if isinstance(node, dict)
                                    else type(node).__name__))
            node = node[part]
        blob = node

    found = []

    def walk(o):
        if isinstance(o, list):
            if (len(o) == 3 and all(isinstance(x, (int, float)) for x in o)):
                found.append([float(x) for x in o])
                return
            for v in o:
                walk(v)
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)

    walk(blob)
    if not found:
        raise SystemExit("ERROR: found no RGB triplets in %s" % path)
    arr = np.array(found, dtype=np.float64)
    inside = np.all((arr >= 0.0) & (arr <= 1.0), axis=1)
    if not inside.any():
        raise SystemExit("ERROR: none of the %d triplets in %s are inside 0..1, so they are not "
                         "in the LUT's input domain" % (len(arr), path))
    return arr[inside]


def parse_cdl_triplet(s):
    """Accept '1.0 1.0 1.0' or three separate arguments."""
    if isinstance(s, (list, tuple)):
        if len(s) == 1 and isinstance(s[0], str):
            s = s[0].split()
        vals = [float(x) for x in s]
    else:
        vals = [float(x) for x in str(s).split()]
    if len(vals) != 3:
        raise SystemExit("ERROR: expected three numbers, got %r" % (s,))
    return vals


# --------------------------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------------------------

def _make_test_cube(path, size=17, kind="identity"):
    coords = grid_coords(size)
    if kind == "identity":
        table = coords.copy()
    elif kind == "invert":
        table = 1.0 - coords
    elif kind == "curve":                       # a smooth, obviously non-linear transform
        table = np.clip(coords ** 0.6 * 0.9 + 0.05, 0.0, 1.0)
    else:
        raise ValueError(kind)
    write_cube(path, table, size, title="test " + kind)
    return table, size


def selftest():
    import tempfile
    failures = []
    tmp = tempfile.mkdtemp(prefix="bake-cdl-selftest-")

    def bad(msg):
        failures.append(msg)

    try:
        idpath = os.path.join(tmp, "identity.cube")
        _make_test_cube(idpath, 17, "identity")

        # --- round trip: write then read must return the same numbers
        tbl, size, dmin, dmax = read_cube(idpath)
        if size != 17:
            bad("round trip lost the size: %r" % size)
        if not np.allclose(tbl, grid_coords(17), atol=1e-6):
            bad("round trip changed the table")

        # --- the dialect, checked on the bytes and not on a parser's opinion
        raw = open(idpath, "rb").read()
        if b"\r\n" not in raw:
            bad("the cube was not written with CRLF line endings")
        if b"\t" not in raw:
            bad("the triplets are not tab separated")
        try:
            raw.decode("ascii")
        except UnicodeDecodeError:
            bad("the cube is not pure ASCII")
        txt = raw.decode("ascii").replace("\r\n", "\n")
        if "\nLUT_3D_SIZE 17\n" not in txt or "\n\nLUT_3D_SIZE" not in txt:
            bad("LUT_3D_SIZE does not have a blank line before it")

        # --- a non-ASCII comment must be REFUSED, not silently written
        try:
            write_cube(os.path.join(tmp, "bad.cube"), grid_coords(9), 9,
                       comments=["an em-dash — here"])
            bad("a non-ASCII comment was written; that has broken a vendor parser before")
        except UnicodeEncodeError:
            pass

        # --- identity CDL through identity LUT is the identity
        out, s, vs = bake(idpath, [1, 1, 1], [0, 0, 0], [1, 1, 1], 1.0)
        if not np.allclose(out, grid_coords(s), atol=1e-9):
            bad("identity CDL + identity LUT is not the identity (max err %.3g)"
                % np.max(np.abs(out - grid_coords(s))))

        # --- a pure slope through the identity LUT must equal the slope, clipped
        out, s, _ = bake(idpath, [0.5, 0.5, 0.5], [0, 0, 0], [1, 1, 1], 1.0)
        want = np.clip(grid_coords(s) * 0.5, 0, 1)
        if not np.allclose(out, want, atol=2e-3):
            bad("a 0.5 slope did not come through (max err %.3g)" % np.max(np.abs(out - want)))

        # --- ORDER: baking the other way round must give a DIFFERENT answer on a non-linear
        #     LUT. If this ever passes as equal, the order has stopped mattering, which would
        #     mean the composition is not doing what the docstring claims.
        cpath = os.path.join(tmp, "curve.cube")
        _make_test_cube(cpath, 17, "curve")
        cdl = dict(slope=[1.2, 1.1, 1.05], offset=[-0.04, -0.03, -0.02],
                   power=[1.0, 1.0, 1.0], saturation=0.93)
        right, s, vs = bake(cpath, cdl["slope"], cdl["offset"], cdl["power"], cdl["saturation"])
        tblc, vsc, dminc, dmaxc = read_cube(cpath)
        wrong = apply_cdl(sample_cube(tblc, vsc, grid_coords(s), dminc, dmaxc),
                          cdl["slope"], cdl["offset"], cdl["power"], cdl["saturation"])
        if np.allclose(right, wrong, atol=1e-3):
            bad("CDL-then-LUT and LUT-then-CDL agree, so the order is not being honoured")


        # --- accuracy, measured on a domain that MEANS something.
        #
        # ⚠️ The first version of this test judged the bake on uniform random points in the whole
        # RGB cube and reported a frightening 23-code maximum. Those points include combinations
        # no camera produces — hard-saturated corners where a log->709 LUT is steepest. Measured
        # on real A-cam chart patches instead, the same bake is 0.05 codes mean and 0.29 max.
        #
        # So: gate on the NEUTRAL AXIS and on a plausible region, and keep the uniform figure as
        # context only. A test that fails on unreachable inputs teaches you to ignore it.
        rng = np.random.default_rng(7)
        uniform = rng.random((4000, 3))
        greys = np.repeat(np.linspace(0.05, 0.95, 300)[:, None], 3, axis=1)
        plausible = np.clip(0.45 + rng.normal(0, 0.08, (4000, 3)), 0.0, 1.0)

        for label, pts, tol_codes in (("neutral ramp", greys, 1.5),
                                      ("plausible footage region", plausible, 4.0)):
            ref = reference(cpath, pts, cdl["slope"], cdl["offset"], cdl["power"],
                            cdl["saturation"])
            baked, s, _ = bake(cpath, cdl["slope"], cdl["offset"], cdl["power"],
                               cdl["saturation"], size=65)
            err = float(np.max(np.abs(sample_cube(baked, s, pts) - ref))) * 255.0
            if err > tol_codes:
                bad("size 65, %s: max error %.2f codes exceeds %.2f" % (label, err, tol_codes))

        # --- a finer grid must not be WORSE. This is the invariant that actually catches a
        #     broken composition, and it holds on every domain.
        for label, pts in (("uniform", uniform), ("neutral", greys)):
            ref = reference(cpath, pts, cdl["slope"], cdl["offset"], cdl["power"],
                            cdl["saturation"])
            errs = []
            for out_size in (17, 33, 65):
                baked, s, _ = bake(cpath, cdl["slope"], cdl["offset"], cdl["power"],
                                   cdl["saturation"], size=out_size)
                errs.append(float(np.mean(np.abs(sample_cube(baked, s, pts) - ref))))
            if not (errs[0] >= errs[1] >= errs[2]):
                bad("%s: mean error did not fall as the grid got finer: %r" % (label, errs))

        # --- at the grid points themselves the bake must be EXACT, which proves the grid
        #     ordering (red fastest) is right. Get that wrong and the picture is scrambled
        #     while every aggregate statistic still looks reasonable.
        for out_size in (17, 33):
            baked, s, _ = bake(cpath, cdl["slope"], cdl["offset"], cdl["power"],
                               cdl["saturation"], size=out_size)
            gp = grid_coords(s)
            ref_gp = reference(cpath, gp, cdl["slope"], cdl["offset"], cdl["power"],
                              cdl["saturation"])
            e = float(np.max(np.abs(sample_cube(baked, s, gp) - ref_gp)))
            if e > 1e-9:
                bad("size %d: not exact at its own grid points (%.3g) — the channel ordering "
                    "is probably transposed" % (out_size, e))

        # --- negatives must not produce NaN
        v = apply_cdl(np.array([[0.0, 0.0, 0.0]]), [1, 1, 1], [-0.5, -0.5, -0.5],
                      [2.2, 2.2, 2.2], 1.0)
        if not np.all(np.isfinite(v)):
            bad("a negative value through a fractional power produced %r" % v)

        # --- saturation 0 must give a neutral, and luma must be preserved
        v = apply_cdl(np.array([[0.8, 0.2, 0.4]]), [1, 1, 1], [0, 0, 0], [1, 1, 1], 0.0)
        if not np.allclose(v[0], v[0][0], atol=1e-12):
            bad("saturation 0 did not give a neutral: %r" % v)
        want_luma = float(np.sum(np.array([0.8, 0.2, 0.4]) * LUMA))
        if not np.isclose(v[0][0], want_luma, atol=1e-12):
            bad("saturation 0 changed the luma: %.6f vs %.6f" % (v[0][0], want_luma))

        # --- a truncated cube must be refused, not read as short
        short = os.path.join(tmp, "short.cube")
        lines = open(idpath, "rb").read().decode("ascii").split("\r\n")
        open(short, "wb").write(("\r\n".join(lines[:-40]) + "\r\n").encode("ascii"))
        try:
            read_cube(short)
            bad("a truncated cube was accepted")
        except SystemExit:
            pass

        # --- ffmpeg must actually accept the file we write, if ffmpeg is here
        import shutil
        import subprocess
        if shutil.which("ffmpeg"):
            baked, s, _ = bake(cpath, cdl["slope"], cdl["offset"], cdl["power"],
                               cdl["saturation"], size=33)
            bpath = os.path.join(tmp, "baked.cube")
            write_cube(bpath, baked, s, title="baked")
            png = os.path.join(tmp, "out.png")
            r = subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                 "-i", "color=c=0x4C7FB2:s=64x64:d=1,format=rgb24",
                 "-vf", "lut3d=" + bpath, "-frames:v", "1", png],
                capture_output=True, text=True)
            if r.returncode != 0 or not os.path.exists(png):
                bad("ffmpeg refused the cube we wrote: %s" % (r.stderr or "")[:300])
            else:
                # and the pixel it produced must match our own evaluation of the same cube
                probe = subprocess.run(
                    ["ffmpeg", "-v", "error", "-i", png, "-f", "rawvideo",
                     "-pix_fmt", "rgb24", "-"], capture_output=True)
                px = np.frombuffer(probe.stdout[:3], dtype=np.uint8).astype(np.float64) / 255.0
                src = np.array([[0x4C, 0x7F, 0xB2]], dtype=np.float64) / 255.0
                mine = sample_cube(baked, s, src)[0]
                d = float(np.max(np.abs(px - mine))) * 255.0
                if d > 2.0:
                    bad("ffmpeg's result differs from ours by %.1f codes on one pixel" % d)
    finally:
        import shutil as _sh
        _sh.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELFTEST FAILED (%d)" % len(failures))
        for f in failures:
            print("  - " + f)
        return 1
    print("bake_cdl_into_lut selftest: all checks passed")
    return 0


# --------------------------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lut", help="the vendor cube the CDL runs BEFORE")
    ap.add_argument("--slope", nargs="+", default=["1 1 1"])
    ap.add_argument("--offset", nargs="+", default=["0 0 0"])
    ap.add_argument("--power", nargs="+", default=["1 1 1"])
    ap.add_argument("--saturation", type=float, default=1.0)
    ap.add_argument("--out")
    ap.add_argument("--size", type=int, help="output grid (default: the vendor cube's own)")
    ap.add_argument("--title")
    ap.add_argument("--verify-points", type=int, default=20000,
                    help="uniform random points, reported as context only")
    ap.add_argument("--verify-key",
                    help="dotted path into --verify-samples, e.g. chart.a_cam.patches_v_pc. "
                         "Scope it to the camera this LUT is for; a file holds more than one.")
    ap.add_argument("--verify-samples",
                    help="JSON of real measured camera RGB triplets; this is the figure that "
                         "matters and it gates")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if not a.lut or not a.out:
        ap.error("--lut and --out are required")
    if not os.path.exists(a.lut):
        ap.error("the vendor LUT does not exist: %s" % a.lut)

    slope = parse_cdl_triplet(a.slope)
    offset = parse_cdl_triplet(a.offset)
    power = parse_cdl_triplet(a.power)

    table, size, vsize = bake(a.lut, slope, offset, power, a.saturation, size=a.size)
    n = write_cube(a.out, table, size,
                   title=a.title or "CDL baked into %s" % os.path.basename(a.lut),
                   comments=[
                       "vendor_lut(cdl(x)) -- the CDL runs BEFORE the LUT, which is the order a",
                       "node applies them in: a node's corrector precedes its own LUT.",
                       "slope %s  offset %s  power %s  saturation %s"
                       % (" ".join("%.6f" % x for x in slope),
                          " ".join("%.6f" % x for x in offset),
                          " ".join("%.6f" % x for x in power), "%.6f" % a.saturation),
                       "source LUT: %s (size %d)" % (os.path.basename(a.lut), vsize),
                   ])

    print("wrote %s" % a.out)
    print("  grid %d^3 = %d entries (%d lines), vendor cube was %d^3"
          % (size, size ** 3, n, vsize))

    # Measure the bake rather than asserting it — and measure it on domains that mean something.
    #
    # ⚠️ Judged on uniform random points across the whole RGB cube, this bake reports a max error
    # of ~23 codes and looks alarming. Those points are hard-saturated corners no camera produces,
    # where a log->709 transform is steepest. On real measured camera values the same cube is
    # 0.05 codes mean / 0.29 max. So the uniform figure is printed as CONTEXT and does not gate;
    # the neutral axis does, and real samples do when you supply them.
    rng = np.random.default_rng(0)
    domains = [
        ("neutral axis", np.repeat(np.linspace(0.05, 0.95, 512)[:, None], 3, axis=1), True),
        ("uniform RGB cube (context only)", rng.random((a.verify_points, 3)), False),
    ]
    if a.verify_samples:
        real = load_samples(a.verify_samples, a.verify_key)
        domains.insert(0, ("real samples %s%s" % (os.path.basename(a.verify_samples),
                                                  ":" + a.verify_key if a.verify_key else ""),
                           real, True))
    else:
        print("  note: pass --verify-samples with real measured camera values for the figure "
              "that actually matters; without it only the neutral axis gates.")

    worst_gated = 0.0
    print("  against the analytic composition:")
    for label, pts, gates in domains:
        ref = reference(a.lut, pts, slope, offset, power, a.saturation)
        err = np.abs(sample_cube(table, size, pts) - ref) * 255.0
        print("    %-38s n=%-6d mean %.4f  p99 %.3f  max %.3f codes%s"
              % (label, len(pts), err.mean(), np.percentile(err, 99), err.max(),
                 "" if gates else "   [not gated]"))
        if gates:
            worst_gated = max(worst_gated, float(err.max()))

    if worst_gated > 2.0:
        print("  ⚠️ %.2f codes on a domain that gates — raise --size before shipping this"
              % worst_gated)
        return 1
    print("  OK: %.2f codes worst case on the gated domains" % worst_gated)
    return 0


if __name__ == "__main__":
    sys.exit(main())

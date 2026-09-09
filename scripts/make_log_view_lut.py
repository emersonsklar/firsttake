#!/usr/bin/env python3
"""
make_log_view_lut — generate a VIEWING .cube for a log curve from its anchors.

For when the camera vendor's own LUT is not to hand. It builds a tone curve from
three published anchors — black, 18% grey, 90% white — and adds a saturation
push, then writes a 3D .cube that any `lut3d` consumer will take.

    ./make_log_view_lut.py --profile dlogm -o dlogm-view.cube
    ./make_log_view_lut.py --grey 39 --white 76 --black 0 -o custom.cube

## ⚠️ What this is NOT

**It is a viewing curve, not a colour conversion.** It corrects TONE only. It does
nothing about primaries, so a wide-gamut log space (D-Gamut, S-Gamut3.Cine) will
still render saturated colours wrong, and two cameras processed this way will NOT
match each other.

Use it to judge **framing and focus**. Do not use it to judge colour, to match
angles, or as anything a post house sees as a reference. Get the vendor's own LUT
for that — this exists so a review copy is watchable tonight rather than perfect
next week.

⚠️ **MEASURED against a vendor transform, 2026-09-09: this runs about half a stop
BRIGHT.** Compared frame-for-frame with DJI's official D-Log M to Rec.709 LUT on
real footage, every region came out 10-16 points high on a 0-100 scale — wall 53.7
vs 66.6, skin 72.9 vs 86.4, dark trousers 39.1 vs 49.3. Hue held; level did not.

The cause is the white anchor. This maps 90% white to 95.3 IRE, while vendor display
transforms deliberately hold highlight headroom and land diffuse white nearer 86.
So **do not judge exposure from this** — the one job a review proxy has. Judging
exposure needs the vendor LUT, or the grey-card reading straight off the camera.

Not yet recalibrated: matching one vendor LUT on one clip would be overfitting, and
the honest fix is a lower diffuse-white target verified across several transforms.

Anchors come from the analyzer's PROFILES table, i.e. the manufacturers' published
transfer functions, not from taste.
"""

from __future__ import annotations

import argparse
import sys

# IRE where each log curve places a known reflectance. Same source as the
# analyzer's PROFILES: the manufacturers' published transfer functions.
#
# The analyzer stores its black floor as a CODE fraction (n/1023). A code is not
# an IRE: in legal-range 10-bit, code 64 is 0 IRE and code 940 is 100 IRE, so the
# conversion is (code - 64) / 876 * 100. Treating 64/1023 as "6.3 IRE" crushed
# everything below that to black. Caught 2026-09-09 by printing the anchors
# instead of trusting them.
def _code_to_ire(code: float) -> float:
    return (code - 64.0) / 876.0 * 100.0


PROFILES = {
    # S-Log3 does not use the bottom of the code range: 0% sits at code 95.
    "slog3": {"black": _code_to_ire(95), "grey": 41.0, "white": 61.0, "sat": 1.40},
    "dlogm": {"black": _code_to_ire(64), "grey": 39.0, "white": 76.0, "sat": 1.25},
}

# Rec.709 display targets for the same reflectances, at ~2.2 encoding gamma.
OUT_GREY = 0.18 ** (1 / 2.2)     # ~0.459
OUT_WHITE = 0.90 ** (1 / 2.2)    # ~0.953
LUMA = (0.2126, 0.7152, 0.0722)  # Rec.709


def tone(v: float, black: float, grey: float, white: float) -> float:
    """Piecewise-linear through the three anchors, in normalised 0-1."""
    b, g, w = black / 100.0, grey / 100.0, white / 100.0
    if v <= b:
        return 0.0
    if v <= g:
        return (v - b) / (g - b) * OUT_GREY
    if v <= w:
        return OUT_GREY + (v - g) / (w - g) * (OUT_WHITE - OUT_GREY)
    if v >= 1.0:
        return 1.0
    return OUT_WHITE + (v - w) / (1.0 - w) * (1.0 - OUT_WHITE)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--profile", choices=sorted(PROFILES))
    ap.add_argument("--black", type=float, help="IRE of 0%% reflectance")
    ap.add_argument("--grey", type=float, help="IRE of 18%% grey")
    ap.add_argument("--white", type=float, help="IRE of 90%% white")
    ap.add_argument("--sat", type=float, help="saturation multiplier")
    ap.add_argument("--size", type=int, default=33, help="cube edge (default 33)")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args(argv)

    if a.profile:
        p = dict(PROFILES[a.profile])
    elif a.grey is not None and a.white is not None:
        p = {"black": a.black or 0.0, "grey": a.grey, "white": a.white, "sat": 1.25}
    else:
        print("give --profile, or --grey and --white")
        return 2
    if a.sat is not None:
        p["sat"] = a.sat
    if not (p["black"] < p["grey"] < p["white"] < 100):
        print(f"anchors must increase and stay under 100 IRE: {p}")
        return 2

    n, sat = a.size, p["sat"]
    lines = [
        f"# Viewing curve for {a.profile or 'custom'} — TONE ONLY, not a colour",
        "# conversion. Primaries are untouched, so saturated colours render wrong and",
        "# two cameras processed this way will not match. Judge exposure, framing and",
        "# focus with it; get the vendor's LUT for anything else.",
        f"# anchors: black {p['black']:.1f} IRE, 18% grey {p['grey']:.1f}, "
        f"90% white {p['white']:.1f} IRE; saturation x{sat:.2f}",
        f"LUT_3D_SIZE {n}",
        "DOMAIN_MIN 0.0 0.0 0.0",
        "DOMAIN_MAX 1.0 1.0 1.0",
        "",
    ]
    # .cube order: red fastest, then green, then blue.
    for bi in range(n):
        for gi in range(n):
            for ri in range(n):
                rgb = [tone(c / (n - 1), p["black"], p["grey"], p["white"])
                       for c in (ri, gi, bi)]
                y = sum(k * c for k, c in zip(LUMA, rgb))
                rgb = [min(1.0, max(0.0, y + (c - y) * sat)) for c in rgb]
                lines.append(" ".join(f"{c:.6f}" for c in rgb))

    with open(a.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"wrote {a.out}  ({n}^3 = {n**3} entries)")
    print(f"  black {p['black']:.1f} -> 0.0 | 18% grey {p['grey']:.1f} -> "
          f"{OUT_GREY*100:.1f} | 90% white {p['white']:.1f} -> {OUT_WHITE*100:.1f} IRE")
    print("  TONE ONLY — not a colour conversion, and angles will not match.")
    print("  ⚠️  Measured ~0.5 stop BRIGHT vs a vendor transform (2026-09-09).")
    print("      Judge framing and focus with it; judge EXPOSURE from the grey card.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

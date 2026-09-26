#!/usr/bin/env python3
"""Generate every image in the README. No stock art, no screenshots, no client footage.

Why generate them: this repo's whole argument is "measure it, the failures are quiet." An
illustration of that argument should itself be drawn from the measurements, not from a stock
library -- so each asset below is a plot or a diagram of a real number that appears in the
references, with generic labels. Nothing here depends on any footage, so nothing here can
leak a client, a face or a location.

    python3 docs/make_assets.py

Writes PNG and GIF into docs/. Deterministic: same bytes every run, so a rebuild is a no-op
in git unless a number actually changed.
"""

from __future__ import annotations

import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- palette: a camera OSD / mastering meter, not a marketing deck ----------
BG      = (11, 14, 19)
PANEL   = (17, 22, 31)
PANEL2  = (23, 30, 41)
INK     = (232, 237, 245)
DIM     = (124, 136, 153)
DIMMER  = (74, 84, 99)
ACCENT  = (255, 92, 43)        # record light / caution
GOOD    = (53, 224, 200)       # measured and passing
WARN    = (255, 196, 61)
GRID    = (33, 41, 54)

# The tagline lives here so it can be changed in one place; it is baked into hero.png.
# ⚠️ NO CREW SIZE AND NO CAMERA COUNT. An earlier version said "one operator, two cameras",
# which is one shoot's configuration, not the skill's range -- the same workflow has run with
# a second person on set and will run with a third camera.
TAGLINE = "FROM AN EMPTY ROOM TO A DELIVERED MASTER"
SUBLINE = ("Plan it, shoot it, and finish it —\n"
           "graded, mixed, captioned, verified.")

F_SANS = "/System/Library/Fonts/HelveticaNeue.ttc"
F_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
F_MONO = "/System/Library/Fonts/Menlo.ttc"


def font(path, size, index=0):
    try:
        return ImageFont.truetype(path, size, index=index)
    except Exception:
        return ImageFont.load_default()


def sans(s):  return font(F_SANS, s, 0)
def bold(s):  return font(F_BOLD, s)
def mono(s):  return font(F_MONO, s, 0)


def text(d, xy, s, f, fill=INK, anchor="la", spacing=4):
    d.text(xy, s, font=f, fill=fill, anchor=anchor, spacing=spacing)


def w_of(d, s, f):
    return d.textbbox((0, 0), s, font=f)[2]


def vignette(img, strength=0.55):
    """A soft corner falloff, so a flat panel reads as lit rather than as a swatch."""
    w, h = img.size
    y, x = np.mgrid[0:h, 0:w]
    cx, cy = w / 2.0, h / 2.0
    r = np.sqrt(((x - cx) / cx) ** 2 + ((y - cy) / cy) ** 2)
    m = np.clip(1.0 - strength * np.clip(r - 0.35, 0, None) ** 1.6, 0, 1)
    a = np.asarray(img).astype(np.float64)
    a[..., :3] *= m[..., None]
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), img.mode)


def panel(d, box, fill=PANEL, outline=GRID, radius=10):
    d.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=1)


# ==========================================================================
# 1. hero banner
# ==========================================================================
def hero(w=1280, h=460):
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)

    # a faint measurement grid, because that is what the repo is
    for x in range(0, w, 40):
        d.line([(x, 0), (x, h)], fill=(16, 20, 27), width=1)
    for y in range(0, h, 40):
        d.line([(0, y), (w, y)], fill=(16, 20, 27), width=1)

    # film perforations down both edges
    for side in (0, 1):
        bx = 16 if side == 0 else w - 40
        for y in range(18, h - 18, 34):
            d.rounded_rectangle([bx, y, bx + 24, y + 20], radius=4,
                                fill=(20, 26, 35), outline=(30, 38, 50))

    # an onset envelope: the thing sync_cameras.py actually correlates
    rng = np.random.default_rng(7)
    n = 900
    t = np.linspace(0, 1, n)
    env = np.zeros(n)
    for c in (0.10, 0.22, 0.31, 0.44, 0.52, 0.63, 0.71, 0.84, 0.92):
        env += np.exp(-((t - c) ** 2) / (2 * 0.010 ** 2)) * rng.uniform(0.55, 1.0)
    env += rng.normal(0, 0.02, n).cumsum() * 0.02
    env = np.clip(env, 0, None)
    env /= env.max()
    base = int(h * 0.80)
    amp = 58
    pts = [(64 + i * (w - 128) / (n - 1), base - env[i] * amp) for i in range(n)]
    d.line(pts, fill=(38, 74, 86), width=3, joint="curve")
    pts2 = [(x, base + (base - y) * 0.45) for x, y in pts]
    d.line(pts2, fill=(30, 52, 62), width=2, joint="curve")
    d.line([(64, base), (w - 64, base)], fill=(44, 56, 72), width=1)

    text(d, (64, 74), "film-shoot", bold(92), INK)
    text(d, (66, 178), TAGLINE, mono(21), ACCENT)
    text(d, (64, 218), SUBLINE, sans(24), DIM, spacing=8)

    chips = ["SHOOT", "QC", "VERIFIED OFFLOAD", "SYNC", "GRADE", "DELIVER"]
    x = 64
    for c in chips:
        cw = w_of(d, c, mono(15)) + 26
        d.rounded_rectangle([x, base + 58, x + cw, base + 88], radius=15,
                            fill=PANEL2, outline=GRID)
        text(d, (x + 13, base + 65), c, mono(15), DIM)
        x += cw + 10

    return vignette(img, 0.5)


# ==========================================================================
# 2. drift: the signature lesson
# ==========================================================================
def drift_gif(w=760, h=300, frames=60):
    """Two angles pulling apart because 29.97 and 30.000 are both called '30p'.

    The numbers are the ones in references/post-production.md: ~1 frame every 33 s, about
    70 frames over a 40-minute interview.
    """
    out = []
    total_min = 40.0
    for k in range(frames):
        img = Image.new("RGB", (w, h), BG)
        d = ImageDraw.Draw(img)
        mins = total_min * k / (frames - 1)
        drift_f = mins * 60 / 33.0

        text(d, (28, 22), "ONE CLAP, TWO CLOCKS", bold(24), INK)
        text(d, (28, 54), "29.97 and 30.000 are both sold as “30p”", sans(16), DIM)

        x0, x1 = 28, w - 150
        for i, (lbl, off, col) in enumerate((("A-CAM", 0.0, GOOD),
                                            ("B-CAM", drift_f, ACCENT))):
            y = 122 + i * 62
            panel(d, [x0, y, x1, y + 40], PANEL, GRID, 8)
            text(d, (x0 + 12, y + 12), lbl, mono(15), DIM)
            # event ticks: the same real events, displaced by the accumulated drift
            # ⚠️ drift accumulates FROM the sync point, so the tick AT the clap stays put
            # and the later ones splay. Offsetting every tick equally would have drawn a
            # constant offset -- the thing a clap actually does fix.
            for ev in np.linspace(0.12, 0.95, 11):
                grow = (ev - 0.12) / (0.95 - 0.12)
                px = x0 + 86 + ev * (x1 - x0 - 100) + off * 1.9 * grow
                if x0 + 84 < px < x1 - 4:
                    d.line([(px, y + 8), (px, y + 32)], fill=col, width=3)
        # the clap, which fixes the start and nothing else
        cx = x0 + 86 + 0.12 * (x1 - x0 - 100)
        d.line([(cx, 112), (cx, 236)], fill=(90, 100, 118), width=1)
        text(d, (cx + 6, 100), "clap", mono(12), DIMMER)

        panel(d, [x1 + 14, 122, w - 22, 224], PANEL2, GRID, 8)
        text(d, (x1 + 26, 134), "DRIFT", mono(13), DIM)
        text(d, (x1 + 26, 152), "%4.1f" % drift_f, bold(38),
             ACCENT if drift_f > 2 else INK)
        text(d, (x1 + 26, 196), "frames", mono(13), DIM)
        text(d, (28, h - 44), "at %4.1f min  —  one frame every 33 s" % mins,
             mono(15), DIM)
        out.append(vignette(img, 0.35))
    # hold on the end so the number is readable before it loops
    out += [out[-1]] * 14
    return out


# ==========================================================================
# 3. the vertical reframe, and the excursion budget
# ==========================================================================
def reframe_gif(w=760, h=430, frames=64):
    """A 1215-of-3840 window held still while the subject sways, with the budget shown.

    The figures are the measured ones: 588 px of head sway across the span, a 48 px
    excursion allowance for up to 1.25 s, and a 0.889 DOWNSCALE so sharpness never enters
    the argument. The subject is a silhouette on purpose -- no footage, no face, no client.

    ⚠️ THE WINDOW IS 31.6% OF THE FRAME WIDTH, not the whole of it. Drawing it full width
    (which the first version did) makes the excursion read 0 px forever and quietly removes
    the only thing the picture is about.
    """
    # ⚠️ EVERY DIMENSION HERE IS DERIVED FROM THE MEASURED ONES, so the readout lands on the
    # real number instead of an invented one. Head UNION across the clip was 1051 px and the
    # sway 588 px, so the head itself is about 1051-588 = 463 px wide. The window is 1215 px
    # and is deliberately NOT centred on the sway -- it is centred for composition (face at
    # 51.7% of frame width), and that offset is exactly why the hair crosses the edge by
    # 48 px at the far lean. Centre it on the sway instead and the excursion is zero, which
    # is a prettier animation and a false one.
    SRC_W = 3840.0
    CROP_SRC = 1215.0
    SWAY_SRC = 588.0
    HEAD_SRC = 1051.0 - 588.0
    WIN_OFFSET_SRC = 130.0
    out = []
    fw, fh = 520, 293                       # the 16:9 frame as drawn
    fx, fy = 28, 96
    crop_w = fw * CROP_SRC / SRC_W          # -> 164 px, a vertical slice
    sway_px = fw * SWAY_SRC / SRC_W / 2.0   # half-amplitude, drawn
    head_px = fw * HEAD_SRC / SRC_W / 2.0   # half-width, drawn
    win_off = fw * WIN_OFFSET_SRC / SRC_W
    for k in range(frames):
        img = Image.new("RGB", (w, h), BG)
        d = ImageDraw.Draw(img)
        text(d, (28, 22), "ONE STATIC 9:16 WINDOW", bold(24), INK)
        text(d, (28, 54), "the subject moves; the frame must not", sans(16), DIM)

        d.rounded_rectangle([fx, fy, fx + fw, fy + fh], radius=6,
                            fill=(19, 25, 34), outline=GRID)
        text(d, (fx + 8, fy + 6), "16:9  3840 px", mono(11), DIMMER)

        ph = 0.5 - 0.5 * math.cos(2 * math.pi * k / frames)
        sway = (ph - 0.5) * 2 * sway_px
        hx = fx + fw * 0.50 + sway
        hy = fy + fh * 0.44
        hr = head_px
        d.ellipse([hx - hr, hy - hr - 6, hx + hr, hy + hr - 6], fill=(46, 58, 75))
        d.polygon([(hx - hr * 1.9, fy + fh - 2), (hx - hr * 1.1, hy + 20),
                   (hx + hr * 1.1, hy + 20), (hx + hr * 1.9, fy + fh - 2)],
                  fill=(38, 48, 63))

        cx = fx + fw * 0.50 + win_off
        L, R = cx - crop_w / 2, cx + crop_w / 2
        over_drawn = max(0.0, L - (hx - hr), (hx + hr) - R)
        px_over = int(round(over_drawn / fw * SRC_W))
        col = GOOD if px_over <= 48 else ACCENT
        d.rectangle([L, fy, R, fy + fh], outline=col, width=2)
        # everything outside the window is what gets thrown away
        for box in ([fx + 1, fy + 1, L, fy + fh - 1], [R, fy + 1, fx + fw - 1, fy + fh - 1]):
            ov = Image.new("RGBA", (max(1, int(box[2] - box[0])),
                                    max(1, int(box[3] - box[1]))), (11, 14, 19, 150))
            img.paste(ov, (int(box[0]), int(box[1])), ov)
        d = ImageDraw.Draw(img)
        d.rectangle([L, fy, R, fy + fh], outline=col, width=2)
        text(d, (L + 6, fy + fh - 22), "1215 px", mono(11), col)

        panel(d, [fx + fw + 20, fy, w - 22, fy + 128], PANEL2, GRID, 8)
        text(d, (fx + fw + 32, fy + 12), "EXCURSION", mono(13), DIM)
        text(d, (fx + fw + 32, fy + 30), "%3d px" % px_over, bold(30), col)
        text(d, (fx + fw + 32, fy + 76), "budget 48", mono(13), DIM)
        text(d, (fx + fw + 32, fy + 94), "for 1.25 s", mono(13), DIMMER)
        text(d, (fx + fw + 32, fy + 150), "sway", mono(12), DIM)
        text(d, (fx + fw + 32, fy + 168), "588 px", mono(15), INK)
        text(d, (fx + fw + 32, fy + 196), "head union", mono(12), DIM)
        text(d, (fx + fw + 32, fy + 214), "1051 px", mono(15), INK)

        text(d, (28, fy + fh + 22),
             "1215 of 3840 px at full height  →  0.889 DOWNSCALE, never an upscale",
             mono(14), DIM)
        text(d, (28, fy + fh + 46),
             "no static window holds a moving head with margin; set a budget and measure it",
             mono(13), DIMMER)
        out.append(vignette(img, 0.35))
    return out


# ==========================================================================
# 4. loudness: the programme is on target and a phrase still jumps
# ==========================================================================
def loudness_png(w=1000, h=420):
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    text(d, (32, 24), "ON TARGET, AND STILL WRONG", bold(26), INK)
    text(d, (32, 58),
         "Integrated loudness, true peak, LRA and per-turn levelling all correct.\n"
         "Nothing in the chain was looking at a phrase.", sans(17), DIM, spacing=7)

    # left: per-turn spread, tight. right: per-phrase, with outliers
    rng = np.random.default_rng(3)
    ax, ay, aw, ah = 32, 150, 440, 210
    bx = 528
    for i, (title, box) in enumerate((("PER TURN  —  spread 1.4 LU", (ax, ay, aw, ah)),
                                      ("PER PHRASE  —  twelve at +4 LU", (bx, ay, aw, ah)))):
        x, y, ww, hh = box
        panel(d, [x, y, x + ww, y + hh], PANEL, GRID, 10)
        text(d, (x + 14, y + 12), title, mono(14), DIM)
        mid = y + hh * 0.62
        d.line([(x + 14, mid), (x + ww - 14, mid)], fill=(48, 60, 76), width=1)
        text(d, (x + ww - 58, y + hh - 26), "median", mono(11), DIMMER)
        if i == 0:
            vals = rng.normal(0, 0.45, 34)
        else:
            vals = np.concatenate([rng.normal(0, 0.9, 46),
                                   rng.uniform(4.0, 5.2, 12)])
            rng.shuffle(vals)          # they are scattered through the programme, not clustered
        for j, v in enumerate(vals):
            px = x + 22 + j * (ww - 44) / (len(vals) - 1)
            py = mid - v * 16
            col = ACCENT if v >= 4.0 else (GOOD if abs(v) <= 2.5 else WARN)
            d.line([(px, mid), (px, py)], fill=col, width=3)
        for lu in (2.5, 4.0):
            yy = mid - lu * 16
            d.line([(x + 14, yy), (x + ww - 14, yy)], fill=(58, 44, 36), width=1)
            text(d, (x + 18, yy - 15), "+%.1f LU" % lu, mono(11),
                 ACCENT if lu == 4 else DIMMER)

    text(d, (32, h - 42),
         "A pre-limiter trim on a phrase already at the ceiling delivered 0.00–2.27 dB "
         "of the 1.75–4.00 asked.", mono(14), DIM)
    return vignette(img, 0.4)


# ==========================================================================
# 5. verified offload: read once, then re-read every destination
# ==========================================================================
def offload_png(w=1000, h=330):
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    text(d, (32, 24), "A COPY IS NOT A BACKUP UNTIL IT HAS BEEN READ BACK",
         bold(24), INK)
    text(d, (32, 58), "Hash while reading the card once, then verify every "
                      "destination from disk.", sans(17), DIM)

    nodes = [("CARD", 60, GOOD), ("HASH", 250, INK), ("DEST 1", 470, INK),
             ("DEST 2", 470, INK), ("COMPARE", 700, ACCENT)]
    y0 = 150
    # card -> hash
    for (lbl, x, col), yy in zip([nodes[0], nodes[1]], [y0 + 40, y0 + 40]):
        panel(d, [x, yy, x + 150, yy + 56], PANEL2, GRID, 8)
        text(d, (x + 16, yy + 20), lbl, mono(16), col)
    # two destinations
    for i in range(2):
        yy = y0 + i * 76
        panel(d, [470, yy, 620, yy + 56], PANEL2, GRID, 8)
        text(d, (486, yy + 20), "DEST %d" % (i + 1), mono(16), INK)
    panel(d, [700, y0 + 40, 700 + 170, y0 + 96], PANEL, ACCENT, 8)
    text(d, (716, y0 + 60), "RE-READ", mono(16), ACCENT)

    def arrow(x0, y0_, x1, y1, col=(60, 74, 92)):
        d.line([(x0, y0_), (x1, y1)], fill=col, width=2)
        d.polygon([(x1, y1), (x1 - 8, y1 - 5), (x1 - 8, y1 + 5)], fill=col)
    arrow(210, y0 + 68, 246, y0 + 68)
    arrow(400, y0 + 68, 466, y0 + 28)
    arrow(400, y0 + 68, 466, y0 + 104)
    arrow(620, y0 + 28, 696, y0 + 62)
    arrow(620, y0 + 104, 696, y0 + 74)

    text(d, (32, h - 54), "MHL 1.1  ·  generation-chained ASC MHL  ·  JSON manifest"
                          "  ·  self-contained HTML report", mono(14), DIM)
    text(d, (32, h - 32), "Card retention: cards stay until the deliverable is live AND "
                          "the archive is verified.", mono(14), DIMMER)
    return vignette(img, 0.4)


# ==========================================================================
# 6. the three acts -- prep, the day, finishing
# ==========================================================================
def pipeline_png(w=1180, h=560):
    """The whole span on one picture.

    Exists because the README used to present this as a shooting skill with a post
    appendix, and the balance is the other way round: `post-production.md` alone carries
    nineteen sections. A reader deciding whether this is for them needs to see the third act
    without scrolling into a table.
    """
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    text(d, (32, 24), "THE WORKFLOW", bold(26), INK)
    text(d, (32, 58), "three stages, one toolkit, from an empty room to a signed-off master",
         sans(17), DIM)

    # ⚠️ STAGES OF A WORKFLOW, not rules and not individual operations. An earlier version
    # listed "nothing may need attention after rolling" and "QC the files, not the monitor" --
    # both true, both PRINCIPLES rather than steps -- and gave the third act a list of
    # individual actions. A reader scanning this wants to know what the stages ARE.
    acts = [
        ("01", "PREPARE", GOOD, [
            "Profile the kit",
            "Scout the location",
            "Design the setup",
            "Build the run sheet",
        ]),
        ("02", "CAPTURE", WARN, [
            "Rig to the plan",
            "Record",
            "Check the footage",
            "Secure the media",
        ]),
        ("03", "FINISH", ACCENT, [
            "Conform the angles",
            "Edit",
            "Grade",
            "Mix",
            "Caption and document",
            "Deliver",
        ]),
    ]
    SUBS = {
        "01": ["measure your own gear, don't trust a datasheet",
               "read a room from photographs",
               "lighting, camera and audio numbers you can follow",
               "checklists, call order, day sheets"],
        "02": ["literal numbers, so nothing is re-derived on set",
               "nothing needing attention after “rolling”",
               "QC the files, not the monitor",
               "verified offload with a chain of custody"],
        "03": ["sync with no timecode; measure the drift",
               "assemble the cut, splices and transitions",
               "match the cameras, then match a reference",
               "restore, level per speaker, master the mix",
               "forced-aligned subtitles, transcript, review doc",
               "masters, social cuts, pull clips"],
    }
    cw = (w - 64 - 2 * 18) // 3
    for i, (num, title, col, items) in enumerate(acts):
        x = 32 + i * (cw + 18)
        panel(d, [x, 104, x + cw, h - 88], PANEL, GRID, 12)
        d.rectangle([x, 104, x + cw, 108], fill=col)
        text(d, (x + 18, 124), num, mono(13), col)
        text(d, (x + 18, 146), title, bold(19), INK)
        yy = 190
        for n_i, it in enumerate(items):
            d.ellipse([x + 20, yy + 7, x + 26, yy + 13], fill=col)
            # wrap by hand: these are short, and a measure keeps the columns even
            words, line, lines = it.split(), "", []
            for wd in words:
                t = (line + " " + wd).strip()
                if w_of(d, t, sans(15)) > cw - 56:
                    lines.append(line); line = wd
                else:
                    line = t
            lines.append(line)
            for ln in lines:
                text(d, (x + 36, yy), ln, sans(17), INK)
                yy += 22
            sub = SUBS[num][n_i]
            text(d, (x + 36, yy), sub, sans(13), DIM)
            yy += 26

    # the through-line under all three
    d.line([(32, h - 66), (w - 32, h - 66)], fill=(44, 56, 72), width=1)
    text(d, (32, h - 54),
         "every number measured, labelled CONFIRMED / LIKELY / UNVERIFIED, with a note on "
         "how it was established", mono(14), DIM)
    return vignette(img, 0.4)


# ==========================================================================
def save_gif(frames, path, ms=70, colors=128):
    """Write a GIF small enough to sit in a README without making it crawl.

    These panels are flat colour plus a vignette, so an adaptive palette of 64 costs nothing
    visible and roughly halves the file. One SHARED palette across every frame is what makes
    it compress: a per-frame palette forces a new colour table into each one and defeats the
    inter-frame diffing.
    """
    # ⚠️ BUILD THE PALETTE FROM A SAMPLE ACROSS THE LOOP, NOT FROM FRAME 0. Taken from the
    # first frame alone, the accent colour dominated its histogram and every white in the
    # animation mapped onto it -- the whole thing came out tinted teal. Tiling a handful of
    # frames spread through the loop gives the palette every colour that actually appears.
    # ⚠️ AND FORCE THE NAMED COLOURS INTO IT. Even across a sample, a mostly-dark frame gives
    # the light text grey so few pixels that it loses its palette slot and every white snaps
    # to the nearest accent -- the animation came out tinted teal twice before this. A swatch
    # band of the palette constants guarantees each one a slot.
    idx = list(range(0, len(frames), max(1, len(frames) // 6)))[:6]
    w0, h0 = frames[0].size
    named = [BG, PANEL, PANEL2, INK, DIM, DIMMER, ACCENT, GOOD, WARN, GRID,
             (255, 255, 255), (0, 0, 0)]
    band = 24
    strip = Image.new("RGB", (w0, h0 * len(idx) + band))
    sd = ImageDraw.Draw(strip)
    for i, c in enumerate(named):
        sd.rectangle([i * (w0 // len(named)), 0,
                      (i + 1) * (w0 // len(named)), band], fill=c)
    for i, j in enumerate(idx):
        strip.paste(frames[j], (0, band + i * h0))
    pal = strip.convert("P", palette=Image.ADAPTIVE, colors=colors)
    qs = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in frames]
    qs[0].save(path, save_all=True, append_images=qs[1:], loop=0,
               duration=ms, optimize=True, disposal=2)


def main():
    os.makedirs(HERE, exist_ok=True)
    jobs = [
        ("hero.png", lambda: hero()),
        ("loudness.png", lambda: loudness_png()),
        ("offload.png", lambda: offload_png()),
        ("pipeline.png", lambda: pipeline_png()),
    ]
    for name, fn in jobs:
        p = os.path.join(HERE, name)
        fn().save(p, optimize=True)
        print("  %-16s %6.0f KB" % (name, os.path.getsize(p) / 1024))
    for name, fn, ms in (("drift.gif", drift_gif, 70),
                         ("reframe.gif", reframe_gif, 60)):
        p = os.path.join(HERE, name)
        save_gif(fn(), p, ms)
        print("  %-16s %6.0f KB" % (name, os.path.getsize(p) / 1024))


if __name__ == "__main__":
    main()

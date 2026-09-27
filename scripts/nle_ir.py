#!/usr/bin/env python3
"""nle_ir.py — one timeline description, two NLEs, and a gate that refuses to degrade silently.

The build scripts on this project grew up talking to DaVinci Resolve's Python API directly, so
the *plan* and the *Resolve dialect* were the same object. That is fine until someone else edits
in Premiere Pro, and then every fact about the cut has to be re-derived in a second dialect —
which is how two timelines that claim to be the same edit stop being the same edit.

So: planners emit an **NLE-neutral intermediate representation**, and a backend turns it into one
application's calls. Parity is defined by this file, not by either backend's feature list.

    python3 nle_ir.py --example > plan.json        # a documented plan to start from
    python3 nle_ir.py --validate plan.json --backend premiere
    python3 nle_ir.py --compare  plan.json         # what EACH application will refuse
    python3 nle_ir.py --explain  plan.json --backend premiere   # the actual numbers
    python3 nle_ir.py --selftest

THE RULE THIS FILE EXISTS TO ENFORCE
------------------------------------
🔴 **A backend that cannot do something must FAIL, never approximate.** The expensive failure on
this kind of pipeline is not "unsupported" — it is a build that reports success having quietly
dropped the ease on a fade, or flattened a keyframed push-in to its first value. Both of those
look like a finished timeline. So every IR node kind is declared in `CAPABILITIES` per backend,
and `capability_gate()` refuses a plan the target cannot render exactly. `UNVERIFIED` counts as
refusable: see `--allow-unverified`, which you must pass deliberately.

WHY THE IR STORES A CROP RECTANGLE AND NOT ZOOM/PAN/TILT
-------------------------------------------------------
Because Zoom/Pan/Tilt is Resolve's parameterisation, and encoding it in the plan bakes one
application into the shared artefact. Worse, it hides a units bug: Resolve's Pan is in TIMELINE
pixels, so a source-pixel offset needs a scale factor, and shipping that factor as 1.0 is correct
at exactly 2x zoom and wrong everywhere else (it happened; see references/resolve-api.md).

The IR stores the primitive fact — **the crop window in source pixels, plus both rasters** — and
each backend derives its own parameters in one place that is unit-tested. Falling out of that:

    PX = timeline_w / crop_w                       is the ONLY number either backend needs
    Resolve : Zoom = src_w/crop_w,  Pan   = d * PX                    [timeline pixels]
    Premiere: Scale = 100 * PX,     Pos.x = 0.5 + d * PX / timeline_w [normalised]

where `d` is the crop centre's offset from the source centre. Both axes take the same form; the
zoom is uniform, so the VERTICAL axis still divides by PX and the *timeline* height, never by the
crop height. Writing it as `d / crop_h` is algebraically identical on the horizontal axis — where
`PX / timeline_w == 1 / crop_w` — and wrong on the vertical whenever the crop's aspect ratio
differs from the timeline's. That is a bug I wrote and the invariant below caught within a minute.

The invariant: **Resolve's Pan divided by the timeline width equals Premiere's normalised Position
offset, identically, on both axes and at any crop aspect.** It is asserted in the selftest over
five crops, so the two backends cannot drift apart without a test going red.
"""

import argparse
import json
import math
import re
import sys

# --------------------------------------------------------------------------------------------
# Capabilities. "Can this backend render this node kind EXACTLY?"
#
#   True         — confirmed by building a timeline and reading it back
#   "UNVERIFIED" — the API says it can; nobody has run it here yet
#   False        — it cannot, and the gate must refuse the plan
#
# Keep the reasons attached. A capability whose stated reason has gone false is worse than no
# capability, because it keeps suppressing the gate while nobody re-reads the reason.
# --------------------------------------------------------------------------------------------

CAPABILITIES = {
    "resolve": {
        "clip":            True,
        "crop":            True,
        "fade_linear":     True,
        "fade_shaped":     False,   # SetFades is linear only; a shaped fade needs a Fusion comp
        "keyframes":       "FUSION",  # no keyframe API on a timeline item at all
        "transition":      "FUSION",  # AddTransition returned None for all 7 signatures tried
        "cdl":             True,
        "lut":             True,     # but only from inside Resolve's own LUT library
        "clip_disable":    True,     # SetClipEnabled; SetTrackEnable reports True and does nothing
        "alpha_track":     True,
        "speed":           True,
        "render":          True,
    },
    # Premiere Pro 26.5.1.1, probed 2026-09-26. Promote an entry ONLY after watching it work and
    # reading the result back out of the application — not because the API declares it.
    "premiere": {
        # CONFIRMED: placed a clip with in/out at an exact frame and read it back — landed at the
        # frame asked for, delivered the exact length, and the out point is EXCLUSIVE (a span of
        # 100 frames gives 100 frames), the same convention as Resolve's endFrame.
        "clip":            True,
        # CONFIRMED by rebuilding a real 2228-frame vertical short and comparing the render against
        # the shipped Resolve master: mean 0.83 codes of 255 over ten sampled frames, and the
        # graphics-only frames (no crop, no grade) at 0.01-0.06 codes, which is what proves the
        # comparison was frame-aligned. Scale read back 88.88888549 against 88.888889 asked.
        "crop":            True,
        # CONFIRMED 2026-09-26 by building a synthetic capability timeline and reading it back
        # (verify_premiere_backend.py). Opacity keyframes landed at frames [0, 12, 71, 89] — exactly
        # as asked — with values [0, 100, 100, 0].
        "fade_linear":     True,
        # CONFIRMED, and this one needed two attempts. Interpolation read [5, 5, 5, 5] against the
        # linear clip's [0, 0, 0, 0]. The first attempt read [0, 5, 0, 5] and PASSED a weaker
        # assertion: a segment takes its shape from the keyframe it LEAVES FROM, so every ramp still
        # departed linear and the ease changed nothing visible.
        "fade_shaped":     True,
        # CONFIRMED: a Scale ramp 100 -> 120 that actually moves. The assertion is the SPAN, because
        # two keyframes at one value is a static value with extra steps.
        "keyframes":       True,
        # CONFIRMED 2026-09-26, and this one could not be confirmed from the timeline at all.
        # A TRANSITION track item marshals across the UXP boundary as `null`: the array has the
        # right length with nothing in any slot, so start and duration are unreadable and there is
        # no hasTransition() either. The count alone (1 on the target track, 0 on every other) says
        # a transition EXISTS and nothing about its geometry.
        # So the geometry was proven by RENDERING and measuring luma per frame: 24 frames asked,
        # a 24-frame ramp rendered, centred on the cut, 50% exactly at the cut.
        # ⚠️ And that render is what caught the real trap: a transition takes HALF its duration from
        # each side of the cut, so an outgoing clip with no tail handle silently truncates it. The
        # same test asking 12 frames against a handle-less clip rendered SIX, on one side only,
        # with no warning and the clip length unchanged. Nothing in the API reports this.
        "transition":      True,
        # 🔴 NO SCRIPTED COLOUR AT ALL, measured 2026-09-26 on 26.5.1.
        # `createSetInputLUTIDAction` wants an opaque ID, not a path, with no documented way to
        # obtain one. And AE.ADBE Lumetri exposes 130 parameters of which ZERO accept a string:
        # `LUTAsset` and `LookAsset` exist but accept neither number, string nor boolean and are
        # not keyframable. So a LUT cannot be attached from a script either.
        # The only route is to PRE-GRADE the media and declare it baked.
        "cdl":             False,
        "lut":             False,
        "clip_disable":    True,           # createSetDisabledAction; read back disabled=True
        "alpha_track":     True,          # both full-length alpha tracks placed at frame 0
        "speed":           False,         # getSpeed() exists; no setter in the declarations
        # CONFIRMED: 2228 frames exactly, 1080x1920, 30000/1001, ProRes 422 HQ, via a generated
        # .epr. The shipped presets are PINNED to 1920x1080 -- see make_export_preset.py.
        "render":          True,
    },
}

BACKENDS = tuple(CAPABILITIES)

# Transition types the IR understands, by intent rather than by vendor string. Each backend maps
# these to whatever it calls them; a plan that names `AE.ADBE Cross Dissolve New` has baked one
# application into the shared artefact, which is the thing this file exists to prevent.
#
# Kept deliberately short: these three are confirmed present in Premiere's own list and have obvious
# equivalents elsewhere. Add one only when you can map it in BOTH backends.
TRANSITIONS = ("cross_dissolve", "dip_to_black", "dip_to_white")

# Which IR features each node kind requires. A node can need more than one.
NODE_REQUIREMENTS = {
    "clip": ("clip",),
    "graphic": ("clip", "alpha_track"),
    "transition": ("transition",),
}


# --------------------------------------------------------------------------------------------
# Geometry — the one place either backend learns where a crop goes
# --------------------------------------------------------------------------------------------

def px_factor(crop_w, timeline_w):
    """The single scale factor both backends are built from."""
    return float(timeline_w) / float(crop_w)


def crop_to_resolve(crop, src, timeline):
    """Resolve's Zoom / Pan / Tilt. Pan and Tilt are in TIMELINE pixels."""
    cx, cy, cw, ch = crop
    src_w, src_h = src
    tl_w, tl_h = timeline
    px = px_factor(cw, tl_w)
    return {
        "zoom": float(src_w) / float(cw),
        "pan": (src_w / 2.0 - (cx + cw / 2.0)) * px,
        "tilt": ((cy + ch / 2.0) - src_h / 2.0) * px,
        "px": px,
    }


def crop_to_premiere(crop, src, timeline):
    """Premiere's Motion Scale (percent) and Position (normalised, 0.5,0.5 = centred).

    ⚠️ Scale is a percentage of the source's NATIVE size, so 100 means one source pixel per
    sequence pixel. That is only true while "Scale to Frame Size" is off for the clip — the API
    has `createSetScaleToFrameSizeAction()` and applying it would silently invalidate every
    number below. The backend must never call it, and the verifier must assert it is off.
    """
    cx, cy, cw, ch = crop
    src_w, src_h = src
    tl_w, tl_h = timeline
    px = px_factor(cw, tl_w)
    return {
        "scale_percent": 100.0 * px,
        "position": [
            0.5 + (src_w / 2.0 - (cx + cw / 2.0)) * px / float(tl_w),
            0.5 + (src_h / 2.0 - (cy + ch / 2.0)) * px / float(tl_h),
        ],
        "px": px,
    }


def frames_to_seconds(frames, fps):
    num, den = fps
    return frames * float(den) / float(num)


# --------------------------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------------------------

def _need(d, key, where, errs, types=None):
    if key not in d:
        errs.append("%s: missing %r" % (where, key))
        return None
    v = d[key]
    if types is not None and not isinstance(v, types):
        errs.append("%s: %r should be %s, got %r" % (where, key, types, type(v).__name__))
    return v


def validate(plan):
    """Structural errors only — nothing about which backend can render it."""
    errs = []

    fps = plan.get("fps")
    if (not isinstance(fps, (list, tuple)) or len(fps) != 2
            or not all(isinstance(x, int) and x > 0 for x in fps)):
        errs.append("plan: fps must be [numerator, denominator] of positive ints, e.g. "
                    "[30000, 1001]. A bare 29.97 float cannot express drop-frame exactly, and "
                    "29.97 delivered as 30.000 drifts a frame every 33 s — got %r." % (fps,))

    raster = _need(plan, "timeline_raster", "plan", errs, (list, tuple))
    _need(plan, "name", "plan", errs, str)

    media = plan.get("media", {})
    if not isinstance(media, dict):
        errs.append("plan: 'media' must be a dict of id -> path")
        media = {}

    tracks = plan.get("tracks", [])
    if not tracks:
        errs.append("plan: no tracks")

    seen_track_ids = set()
    for t in tracks:
        tid = t.get("id", "<unnamed>")
        if tid in seen_track_ids:
            errs.append("track %r: duplicate id" % tid)
        seen_track_ids.add(tid)
        if t.get("kind") not in ("video", "audio"):
            errs.append("track %r: kind must be 'video' or 'audio'" % tid)
        idx = t.get("index")
        if not isinstance(idx, int) or idx < 1:
            errs.append("track %r: index must be a 1-based int" % tid)

        occupied = []
        for n, node in enumerate(t.get("nodes", [])):
            where = "track %r node %d" % (tid, n)
            kind = _need(node, "kind", where, errs, str)
            if kind not in NODE_REQUIREMENTS:
                errs.append("%s: unknown kind %r (known: %s)"
                            % (where, kind, ", ".join(sorted(NODE_REQUIREMENTS))))
                continue

            if kind in ("clip", "graphic"):
                mid = _need(node, "media", where, errs, str)
                if mid is not None and mid not in media:
                    errs.append("%s: media id %r is not in plan['media']" % (where, mid))
                rec = _need(node, "rec", where, errs, int)
                ln = _need(node, "len", where, errs, int)
                _need(node, "src_in", where, errs, int)
                if isinstance(ln, int) and ln < 1:
                    errs.append("%s: len must be >= 1 frame" % where)
                if isinstance(rec, int) and isinstance(ln, int) and ln >= 1:
                    occupied.append((rec, rec + ln, where))

                crop = node.get("crop")
                if crop is not None:
                    if (not isinstance(crop, (list, tuple)) or len(crop) != 4
                            or not all(isinstance(x, (int, float)) for x in crop)):
                        errs.append("%s: crop must be [x, y, w, h] in SOURCE pixels" % where)
                    elif node.get("src_raster") is None:
                        errs.append("%s: a crop needs 'src_raster' — a crop in source pixels is "
                                    "meaningless without the raster it was measured on" % where)
                    else:
                        cx, cy, cw, ch = crop
                        sw, sh = node["src_raster"]
                        if cw <= 0 or ch <= 0:
                            errs.append("%s: crop w/h must be positive" % where)
                        if cx < 0 or cy < 0 or cx + cw > sw or cy + ch > sh:
                            errs.append("%s: crop %r falls outside the source raster %r"
                                        % (where, list(crop), list((sw, sh))))
                        if (cw > 0 and ch > 0 and isinstance(raster, (list, tuple))
                                and len(raster) == 2 and all(raster)):
                            want = float(raster[0]) / float(raster[1])
                            got = float(cw) / float(ch)
                            if abs(got - want) / want > 0.005:
                                errs.append(
                                    "%s: crop aspect %.4f does not match the timeline's %.4f. "
                                    "Both backends scale a crop UNIFORMLY, so a mismatched crop "
                                    "cannot fill the frame — one axis will over- or under-fill by "
                                    "%.1f%% and no warning is raised in either application."
                                    % (where, got, want, abs(got - want) / want * 100.0))

                # An audio node carrying picture properties is a plan that has confused its
                # tracks. Neither backend would complain; the crop would simply be ignored.
                if t.get("kind") == "audio":
                    for bad in ("crop", "src_raster", "grade", "keyframes"):
                        if node.get(bad) is not None:
                            errs.append("%s is on an AUDIO track but carries %r" % (where, bad))

                trans = node.get("transition") or {}
                if trans and not isinstance(trans, dict):
                    errs.append("%s: 'transition' must be a dict of in/out" % where)
                for side in ("in", "out"):
                    # ⚠️ NOT `t` — that is the enclosing loop's track, and shadowing it here broke
                    # the audio-track check twenty lines below with an AttributeError on None.
                    tr = trans.get(side) if isinstance(trans, dict) else None
                    if tr is None:
                        continue
                    tf = tr.get("frames")
                    if not isinstance(tf, int) or tf < 1:
                        errs.append("%s: transition %s frames must be a positive int"
                                    % (where, side))
                    if tr.get("type", "cross_dissolve") not in TRANSITIONS:
                        errs.append("%s: transition %s type %r is not one this IR maps. Known: %s"
                                    % (where, side, tr.get("type"), ", ".join(TRANSITIONS)))
                    # A transition consumes handles from BOTH clips, so it cannot be longer than
                    # either. Neither application warns; you get a shortened clip or a transition
                    # that silently comes out a different length than asked.
                    if isinstance(tf, int) and isinstance(ln, int) and tf > ln:
                        errs.append("%s: transition %s is %d frames on a %d-frame clip"
                                    % (where, side, tf, ln))

                fade = node.get("fade") or {}
                for side in ("in", "out"):
                    f = fade.get(side)
                    if f is None:
                        continue
                    fl = f.get("frames")
                    if not isinstance(fl, int) or fl < 1:
                        errs.append("%s: fade %s frames must be a positive int" % (where, side))
                    if f.get("shape", "linear") not in ("linear", "ease", "ease_in", "ease_out"):
                        errs.append("%s: fade %s shape %r not understood"
                                    % (where, side, f.get("shape")))
                    if isinstance(fl, int) and isinstance(ln, int) and fl > ln:
                        errs.append("%s: fade %s is %d frames inside a %d-frame clip"
                                    % (where, side, fl, ln))

                for k, kf in enumerate(node.get("keyframes", [])):
                    kw = "%s keyframe set %d" % (where, k)
                    _need(kf, "param", kw, errs, str)
                    pts = _need(kf, "points", kw, errs, (list, tuple))
                    if isinstance(pts, (list, tuple)):
                        if len(pts) < 2:
                            errs.append("%s: a keyframe set with fewer than 2 points is a static "
                                        "value; say so instead, or it will read as animation that "
                                        "does not move" % kw)
                        offs = [p.get("frame") for p in pts if isinstance(p, dict)]
                        if any(not isinstance(o, int) for o in offs):
                            errs.append("%s: every point needs an int 'frame'" % kw)
                        elif offs != sorted(offs):
                            errs.append("%s: points are not in time order" % kw)
                        elif len(set(offs)) != len(offs):
                            errs.append("%s: two points share a frame" % kw)
                        elif isinstance(ln, int) and offs and (offs[0] < 0 or offs[-1] > ln - 1):
                            errs.append("%s: points span %d..%d, outside the clip's 0..%d. "
                                        "Keyframes are CLIP-RELATIVE in the IR."
                                        % (kw, offs[0], offs[-1], ln - 1))

        occupied.sort()
        for a, b in zip(occupied, occupied[1:]):
            if a[1] > b[0]:
                errs.append("track %r: %s (%d..%d) overlaps %s (%d..%d). Neither backend will "
                            "tell you — Resolve moves the later clip, and it still tiles."
                            % (tid, a[2], a[0], a[1], b[2], b[0], b[1]))

    render = plan.get("render")
    if render is not None:
        _need(render, "output", "render", errs, str)
        total = render.get("frames")
        if total is not None and not isinstance(total, int):
            errs.append("render: 'frames' must be an int — it is the assertion the delivered "
                        "file is measured against")

    return errs


def capability_gate(plan, backend, allow_unverified=False):
    """Refuse a plan whose features the target cannot render EXACTLY."""
    if backend not in CAPABILITIES:
        return ["unknown backend %r (known: %s)" % (backend, ", ".join(BACKENDS))]
    caps = CAPABILITIES[backend]
    errs = []

    def check(feature, where, detail=""):
        state = caps.get(feature, False)
        if state is True:
            return
        if state is False:
            errs.append("%s needs %r, which %s CANNOT do exactly.%s"
                        % (where, feature, backend, (" " + detail) if detail else ""))
        elif state == "FUSION":
            errs.append("%s needs %r, which %s can only do via a Fusion comp. The backend must "
                        "build one; it must not set a static value and call it done."
                        % (where, feature, backend))
        elif not allow_unverified:
            errs.append("%s needs %r, which is UNVERIFIED on %s — the API declares it but it has "
                        "not been run here. Run the probe, or pass --allow-unverified knowing the "
                        "result is unproven." % (where, feature, backend))

    for t in plan.get("tracks", []):
        for n, node in enumerate(t.get("nodes", [])):
            where = "track %r node %d (%s)" % (t.get("id"), n, node.get("kind"))
            for feature in NODE_REQUIREMENTS.get(node.get("kind"), ()):
                check(feature, where)
            if node.get("crop") is not None:
                check("crop", where)
            if node.get("disabled"):
                check("clip_disable", where)
            if node.get("speed") not in (None, 1.0, 1):
                check("speed", where, "Retime the media before it reaches the timeline.")
            fade = node.get("fade") or {}
            for side in ("in", "out"):
                f = fade.get(side)
                if not f:
                    continue
                shaped = f.get("shape", "linear") != "linear"
                check("fade_shaped" if shaped else "fade_linear", where + " fade " + side)
            if node.get("keyframes"):
                check("keyframes", where)
            if node.get("transition"):
                check("transition", where, "Render the transition into the media, or cut hard.")
            grade = node.get("grade") or {}
            if grade.get("cdl"):
                check("cdl", where, "Bake the CDL into a LUT, or pre-grade the media.")
            if grade.get("lut"):
                check("lut", where, "Render the grade into the media and declare it in the "
                                    "plan's 'baked' list; then drop the grade from the node.")

    if plan.get("render"):
        check("render", "render")

    return errs


def compare_backends(plan, allow_unverified=False):
    """What this exact plan costs in each application. The question a handover asks.

    Someone cutting in Resolve who hands the job to someone in Premiere needs to know, before the
    handover and not after it, which parts of their timeline the other application will refuse.
    """
    rows = []
    for backend in BACKENDS:
        errs = capability_gate(plan, backend, allow_unverified=allow_unverified)
        rows.append((backend, errs))
    return rows


def explain(plan, backend):
    """What the backend will actually do with each crop — the numbers, not the intent."""
    out = []
    tl = plan.get("timeline_raster")
    for t in plan.get("tracks", []):
        for n, node in enumerate(t.get("nodes", [])):
            crop = node.get("crop")
            if crop is None or not tl:
                continue
            src = node["src_raster"]
            conv = crop_to_resolve if backend == "resolve" else crop_to_premiere
            out.append(("track %r node %d" % (t.get("id"), n), conv(crop, src, tl)))
    return out


# --------------------------------------------------------------------------------------------
# Selftest — plant each fault, assert it is caught
# --------------------------------------------------------------------------------------------

EXAMPLE = {
    "_comment": [
        "A worked example: a 9:16 social cut. Two reframed angles on V1, a full-length",
        "ProRes 4444 alpha track of burned-in captions on V2, and a title card on V3 that",
        "fades. Every time is in FRAMES at the plan's own fps; every crop is in SOURCE pixels.",
        "Validate with:  python3 nle_ir.py --validate plan.json --backend premiere",
        "Compare both:   python3 nle_ir.py --compare  plan.json",
    ],
    "name": "example-social-cut-v1",
    "fps": [30000, 1001],
    "timeline_raster": [1080, 1920],

    "sequence_preset": None,
    "_sequence_preset": "Premiere only, optional. A committed .sqpreset pins raster, codec and "
                        "audio layout as well as the rate. Without one the backend sets the rate "
                        "explicitly and reads it back. Resolve needs its own .drpst and cannot "
                        "set the rate any other way.",

    "track_index_base": 0,
    "_track_index_base": "Premiere only. 0 or 1, whichever `premiere_bridge.py --probe` says "
                         "getVideoTrack() uses. Do not guess: off by one puts every graphic on "
                         "the wrong lane and the timeline still renders.",

    "keyframe_time_base": "clip",
    "_keyframe_time_base": "'clip' or 'sequence'. Which one an effect's keyframe times are "
                           "measured against. Settle it with the probe — the wrong choice puts "
                           "keyframes outside the clip, where nothing moves and nothing errors.",

    "media": {
        "wide": "/absolute/path/A001_wide.mov",
        "tight": "/absolute/path/B001_tight.mov",
        "captions": "/absolute/path/captions_alpha.mov",
        "mix": "/absolute/path/programme_mix.wav",
        "card": "/absolute/path/title_card.mov"
    },

    "baked": [
        {"what": "camera match (ASC CDL baked into camA.cube / camB.cube)",
         "why": "Premiere cannot take a CDL from a script, so the grade travels as LUTs. "
                "Not adjustable as four numbers at the far end."}
    ],

    "sidecars": [
        {"kind": "subtitles", "path": "/absolute/path/example.srt"}
    ],

    "tracks": [
        {"id": "V1", "kind": "video", "index": 1, "nodes": [
            {"kind": "clip", "media": "wide", "rec": 0, "src_in": 4783, "len": 210,
             "src_raster": [3840, 2160], "crop": [1312, 0, 1215, 2160],
             "grade": {"lut": "/absolute/path/camA.cube"}},
            {"kind": "clip", "media": "tight", "rec": 210, "src_in": 5004, "len": 180,
             "src_raster": [3840, 2160], "crop": [1430, 0, 1215, 2160],
             "grade": {"lut": "/absolute/path/camB.cube"},
             "keyframes": [
                 {"param": "Scale",
                  "_note": "clip-relative frames; a slow push over the whole clip",
                  "points": [{"frame": 0, "value": 88.89}, {"frame": 179, "value": 93.0}]}
             ]}
        ]},
        {"id": "V2", "kind": "video", "index": 2, "nodes": [
            {"kind": "graphic", "media": "captions", "rec": 0, "src_in": 0, "len": 390,
             "_note": "ONE full-length alpha clip, not one file per cue. Numbered stills in a "
                      "folder import as a single image sequence and all but one cue vanishes."}
        ]},
        {"id": "A1", "kind": "audio", "index": 1, "nodes": [
            {"kind": "clip", "media": "mix", "rec": 0, "src_in": 0, "len": 390,
             "_note": "the MASTERED mix, laid explicitly. Premiere's overwrite places a clip's "
                      "audio along with its picture whether you want it or not, so a plan that "
                      "declares no audio track still gets camera audio unless the backend strips "
                      "it — measured once as a programme delivered 15 LU under its own mix."}
        ]},
        {"id": "V3", "kind": "video", "index": 3, "nodes": [
            {"kind": "graphic", "media": "card", "rec": 0, "src_in": 0, "len": 96,
             "fade": {"in": {"frames": 12, "shape": "linear"},
                      "out": {"frames": 18, "shape": "linear"}},
             "_note": "shape 'ease' is expressible in Premiere and REFUSED by Resolve, whose "
                      "SetFades is linear only — run --compare before promising it."}
        ]}
    ],

    "render": {
        "output": "/absolute/path/out/example-social-cut-v1.mov",
        "preset": "/absolute/path/presets/delivery.epr",
        "frames": 390,
        "_frames": "The assertion the delivered file is measured against. 'Complete' is not "
                   "'correct' — one render came back reporting Complete with 8 frames of 26688."
    }
}


def _good_plan():
    return {
        "name": "selftest",
        "fps": [30000, 1001],
        "timeline_raster": [1080, 1920],
        "media": {"a": "/tmp/a.mov", "card": "/tmp/card.mov"},
        "tracks": [
            {"id": "V1", "kind": "video", "index": 1, "nodes": [
                {"kind": "clip", "media": "a", "rec": 0, "src_in": 100, "len": 200,
                 "src_raster": [3840, 2160], "crop": [130, 0, 1215, 2160]},
                {"kind": "clip", "media": "a", "rec": 200, "src_in": 900, "len": 100,
                 "src_raster": [3840, 2160]},
            ]},
            {"id": "V3", "kind": "video", "index": 3, "nodes": [
                {"kind": "graphic", "media": "card", "rec": 0, "src_in": 0, "len": 300,
                 "fade": {"in": {"frames": 12, "shape": "linear"},
                          "out": {"frames": 12, "shape": "linear"}}},
            ]},
        ],
        "render": {"output": "/tmp/out.mov", "frames": 300},
    }


def selftest():
    failures = []

    def expect(name, errs, needle):
        hit = any(needle.lower() in e.lower() for e in errs)
        if not hit:
            failures.append("%s: expected an error mentioning %r, got %r" % (name, needle, errs))

    def expect_clean(name, errs):
        if errs:
            failures.append("%s: expected no errors, got %r" % (name, errs))

    # --- the good plan validates
    expect_clean("good plan", validate(_good_plan()))

    # --- and so does the documented example, so it cannot rot into a plan nobody can build
    expect_clean("the EXAMPLE plan", validate(EXAMPLE))
    expect("EXAMPLE needs Fusion in Resolve", capability_gate(EXAMPLE, "resolve"), "Fusion comp")

    # Premiere cannot script colour AT ALL — measured, see CAPABILITIES — so the example, which
    # carries a per-camera LUT, must be refused for it and must say how to get round that.
    ex_pm = capability_gate(EXAMPLE, "premiere", allow_unverified=True)
    expect("EXAMPLE refused in Premiere for the LUT", ex_pm, "lut")
    expect("and the refusal says what to do instead", ex_pm, "declare it in the plan's 'baked'")

    # The same plan with the grade rendered into the media is portable. This is the shape a
    # handover to Premiere has to take, so it is asserted rather than described.
    pregraded = json.loads(json.dumps(EXAMPLE))
    for nd in pregraded["tracks"][0]["nodes"]:
        nd.pop("grade", None)
    pregraded["baked"].append({"what": "the per-camera grade", "why": "rendered into the media"})
    expect_clean("a PRE-GRADED EXAMPLE in Premiere",
                 capability_gate(pregraded, "premiere", allow_unverified=True))

    # --- geometry, against numbers measured on a real reframe (3840->1215 of, into 1080x1920)
    r = crop_to_resolve([130, 0, 1215, 2160], [3840, 2160], [1080, 1920])
    if abs(r["zoom"] - 3.160494) > 1e-5:
        failures.append("resolve zoom: expected 3.160494, got %r" % r["zoom"])
    if abs(r["px"] - 0.888889) > 1e-5:
        failures.append("resolve px: expected 0.888889, got %r" % r["px"])

    p = crop_to_premiere([130, 0, 1215, 2160], [3840, 2160], [1080, 1920])
    if abs(p["scale_percent"] - 88.8889) > 1e-3:
        failures.append("premiere scale: expected 88.8889, got %r" % p["scale_percent"])

    # THE cross-backend invariant: Resolve's Pan in timeline pixels, divided by the timeline
    # width, must equal Premiere's normalised Position offset. If these two ever disagree the
    # same plan frames differently in the two applications.
    for crop in ([130, 0, 1215, 2160], [0, 0, 1215, 2160], [2625, 0, 1215, 2160],
                 [400, 200, 1920, 1080], [900, 500, 1000, 700]):
        rr = crop_to_resolve(crop, [3840, 2160], [1080, 1920])
        pp = crop_to_premiere(crop, [3840, 2160], [1080, 1920])
        lhs = rr["pan"] / 1080.0
        rhs = pp["position"][0] - 0.5
        if abs(lhs - rhs) > 1e-9:
            failures.append("crop %r: Resolve pan/tl_w = %.12f but Premiere dx = %.12f — the two "
                            "backends would frame this differently" % (crop, lhs, rhs))
        # and the vertical axis, which carries the opposite sign convention in Resolve
        lhs_y = -rr["tilt"] / 1920.0
        rhs_y = pp["position"][1] - 0.5
        if abs(lhs_y - rhs_y) > 1e-9:
            failures.append("crop %r: vertical mismatch %.12f vs %.12f" % (crop, lhs_y, rhs_y))

    # a 2x zoom is the case where a missing scale factor still looks right
    two = crop_to_resolve([960, 540, 1920, 1080], [3840, 2160], [1920, 1080])
    if abs(two["px"] - 1.0) > 1e-9:
        failures.append("the 2x case should give px == 1.0 exactly; got %r" % two["px"])

    # --- each planted structural fault
    pl = _good_plan(); pl["fps"] = 29.97
    expect("float fps", validate(pl), "numerator")

    pl = _good_plan(); pl["tracks"][0]["nodes"][1]["rec"] = 150
    expect("overlap", validate(pl), "overlaps")

    pl = _good_plan(); pl["tracks"][0]["nodes"][0]["media"] = "nope"
    expect("dangling media", validate(pl), "not in plan")

    pl = _good_plan(); del pl["tracks"][0]["nodes"][0]["src_raster"]
    expect("crop without raster", validate(pl), "src_raster")

    pl = _good_plan(); pl["tracks"][0]["nodes"][0]["crop"] = [3000, 0, 1215, 2160]
    expect("crop off the edge", validate(pl), "outside the source raster")

    pl = _good_plan(); pl["tracks"][0]["nodes"][0]["len"] = 0
    expect("zero length", validate(pl), "len must be")

    pl = _good_plan(); pl["tracks"][0]["nodes"][0]["crop"] = [130, 0, 1215, 1900]
    expect("crop aspect mismatch", validate(pl), "scale a crop UNIFORMLY")

    pl = _good_plan()
    pl["tracks"].append({"id": "A1", "kind": "audio", "index": 1, "nodes": [
        {"kind": "clip", "media": "a", "rec": 0, "src_in": 0, "len": 300,
         "crop": [0, 0, 1080, 1920], "src_raster": [1080, 1920]}]})
    expect("crop on an audio track", validate(pl), "AUDIO track but carries")

    pl = _good_plan(); pl["tracks"][1]["nodes"][0]["fade"]["in"]["frames"] = 500
    expect("fade longer than clip", validate(pl), "inside a")

    pl = _good_plan(); pl["tracks"][0]["nodes"][0]["keyframes"] = [
        {"param": "scale", "points": [{"frame": 0, "value": 1.0}]}]
    expect("one-point keyframe set", validate(pl), "fewer than 2")

    pl = _good_plan(); pl["tracks"][0]["nodes"][0]["keyframes"] = [
        {"param": "scale", "points": [{"frame": 0, "value": 1.0}, {"frame": 500, "value": 2.0}]}]
    expect("keyframe past clip end", validate(pl), "CLIP-RELATIVE")

    pl = _good_plan(); pl["tracks"][0]["nodes"][0]["keyframes"] = [
        {"param": "scale", "points": [{"frame": 90, "value": 2.0}, {"frame": 10, "value": 1.0}]}]
    expect("out of order keyframes", validate(pl), "time order")

    pl = _good_plan(); pl["tracks"].append(dict(pl["tracks"][0]))
    expect("duplicate track id", validate(pl), "duplicate id")

    # --- the gate
    errs = capability_gate(_good_plan(), "resolve")
    expect_clean("resolve gate on a plain plan", errs)

    pl = _good_plan(); pl["tracks"][1]["nodes"][0]["fade"]["in"]["shape"] = "ease"
    expect("resolve refuses a shaped fade", capability_gate(pl, "resolve"), "CANNOT do exactly")

    pl = _good_plan(); pl["tracks"][0]["nodes"][0]["keyframes"] = [
        {"param": "scale", "points": [{"frame": 0, "value": 1.0}, {"frame": 99, "value": 1.1}]}]
    expect("resolve escalates keyframes to Fusion", capability_gate(pl, "resolve"), "Fusion comp")

    pl = _good_plan(); pl["tracks"][0]["nodes"][0]["grade"] = {"cdl": {"slope": [1, 1, 1]}}
    expect("premiere refuses a CDL", capability_gate(pl, "premiere"), "CANNOT do exactly")

    pl = _good_plan(); pl["tracks"][0]["nodes"][0]["speed"] = 0.5
    expect("premiere refuses a speed change", capability_gate(pl, "premiere"), "Retime the media")

    # UNVERIFIED must block by default and pass only when explicitly allowed.
    #
    # ⚠️ Tested against an INJECTED capability, not against whichever backend happens to have an
    # unverified entry today. This assertion used to read the real Premiere table, and it broke the
    # moment the last Premiere entry was promoted — the mechanism was fine and the test was
    # measuring the wrong thing. A guard whose test depends on a transient state stops guarding
    # silently.
    CAPABILITIES["_test"] = dict(CAPABILITIES["resolve"])
    CAPABILITIES["_test"]["clip"] = "UNVERIFIED"
    try:
        errs = capability_gate(_good_plan(), "_test")
        expect("an UNVERIFIED capability blocks by default", errs, "UNVERIFIED")
        expect("and the message says how to proceed anyway", errs, "allow-unverified")
        expect_clean("--allow-unverified lets it through",
                     capability_gate(_good_plan(), "_test", allow_unverified=True))
    finally:
        del CAPABILITIES["_test"]

    # And the real tables must contain no UNVERIFIED that nobody has looked at in a while: this is
    # informational, printed rather than asserted, because UNVERIFIED is a legitimate resting state.
    for backend, caps in CAPABILITIES.items():
        pending = sorted(k for k, v in caps.items() if v == "UNVERIFIED")
        if pending:
            print("  note: %s still UNVERIFIED for %r" % (", ".join(pending), backend))

    expect("unknown backend", capability_gate(_good_plan(), "flame"), "unknown backend")

    if failures:
        print("SELFTEST FAILED (%d)" % len(failures))
        for f in failures:
            print("  - " + f)
        return 1
    print("nle_ir selftest: all checks passed")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--validate", metavar="PLAN.JSON")
    ap.add_argument("--explain", metavar="PLAN.JSON")
    ap.add_argument("--compare", metavar="PLAN.JSON",
                    help="what this plan costs in EVERY backend — run this before a handover")
    ap.add_argument("--backend", choices=BACKENDS, default="resolve")
    ap.add_argument("--allow-unverified", action="store_true")
    ap.add_argument("--example", action="store_true",
                    help="print a documented, valid example plan to start from")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    if a.example:
        print(json.dumps(EXAMPLE, indent=2))
        return 0

    path = a.validate or a.explain or a.compare
    if not path:
        ap.error("give --validate, --explain, --compare or --selftest")
    with open(path, encoding="utf-8") as fh:
        plan = json.load(fh)

    if a.compare:
        errs = validate(plan)
        if errs:
            print("the plan itself is not valid, so no comparison is meaningful:")
            for e in errs:
                print("  - " + e)
            return 1
        worst = 0
        for backend, berrs in compare_backends(plan, a.allow_unverified):
            if not berrs:
                print("%-10s builds this plan exactly." % backend)
                continue
            worst = 1
            # Group by the feature and its reason. Fourteen copies of one sentence is not a
            # more thorough report than one copy with a count; it is a report nobody reads.
            groups = {}
            for e in berrs:
                m = re.search(r"needs '([^']+)', (.*)$", e, re.S)
                key = (m.group(1), m.group(2)) if m else ("", e)
                groups.setdefault(key, []).append(e.split(" needs ")[0])
            print("%-10s %d problem(s) in %d kind(s):" % (backend, len(berrs), len(groups)))
            for (feature, reason), wheres in sorted(groups.items()):
                head = "%s x%d" % (feature, len(wheres)) if feature else "x%d" % len(wheres)
                print("    - %s: %s" % (head, reason))
                shown = ", ".join(wheres[:4]) + (", ..." if len(wheres) > 4 else "")
                print("        at: %s" % shown)
        if worst:
            print("\nBefore handing this over, either change the plan so both applications can "
                  "build it, or bake the difference into the media and say so on the handover.")
        return worst

    if a.explain:
        for where, conv in explain(plan, a.backend):
            print("%-28s %s" % (where, json.dumps(conv, sort_keys=True)))
        return 0

    errs = validate(plan) + capability_gate(plan, a.backend, a.allow_unverified)
    if errs:
        print("PLAN REJECTED for backend %r (%d problems)" % (a.backend, len(errs)))
        for e in errs:
            print("  - " + e)
        return 1
    print("plan OK for backend %r" % a.backend)
    return 0


if __name__ == "__main__":
    # Post tool: needs an active license (see license_check.py). --selftest is exempt.
    from license_check import gate
    gate(__file__)
    sys.exit(main())

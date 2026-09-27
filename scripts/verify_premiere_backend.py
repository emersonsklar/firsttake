#!/usr/bin/env python3
"""verify_premiere_backend.py — prove, on a real timeline, what the Premiere backend can do.

`nle_ir.CAPABILITIES` starts every Premiere entry at `UNVERIFIED`, and the rule is that an entry
only becomes `True` once somebody has watched it work and read the result back out of the
application. This is that step, automated, so the promotion is evidence rather than optimism.

    python3 verify_premiere_backend.py --make-media      # synthetic clips, once
    python3 verify_premiere_backend.py                   # build, read back, report
    python3 verify_premiere_backend.py --selftest        # no Premiere needed

WHY SYNTHETIC MEDIA AND NOT A REAL CUT
--------------------------------------
The first attempt to prove these used a real vertical short, and it could not: **that short's fades
are rendered into its alpha tracks**, so the timeline does no fading and a green result would have
said nothing about `fade_linear`. A capability test has to plant the thing it is testing. So this
generates its own clips with ffmpeg — a few seconds each, exact frame counts, no dependence on any
footage — and builds a timeline whose only purpose is to exercise one feature per clip.

That also means anyone can run it on any machine with Premiere, which is the point: the answer to
"does this work in my Premiere" should not require somebody else's rushes.

WHAT IT ASSERTS, AND WHY EACH ONE IS A SEPARATE CLAIM
-----------------------------------------------------
    fade_linear    opacity keyframes exist at the asked frames, ramping 0 -> 100
    fade_shaped    the same, with BEZIER interpolation rather than LINEAR
    keyframes      a Scale ramp that moves: two points, different values
    clip_disable   the clip reads back disabled

🔴 Each is checked by READING THE TIMELINE, never by the build reporting success. A build that says
`ok` has told you about its own intentions — that is the sentence this whole backend was written
under, and it applies hardest to the tool that decides what to trust.

⚠️ `transition` is NOT tested here, because the backend does not implement it. `nle_ir` says so
(`transition: False`) and the builder refuses the node kind by name. A test that skipped it silently
would leave the capability looking merely untested rather than absent.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import nle_ir                                                          # noqa: E402

MEDIA = os.path.join(HERE, "fixtures", "premiere")
FPS = (30000, 1001)
RASTER = (1920, 1080)

# One clip per feature, each long enough that a fade cannot be confused with a whole clip.
# 🔴 120 frames, while every node below uses at most 90, so EVERY clip keeps a tail handle.
# This is not tidiness. A transition consumes handles from BOTH clips it spans, and when these were
# 90-frame clips used 90 frames long, the outgoing clip had zero tail handle: a 12-frame dissolve
# came out 6 frames, entirely on one side of the cut, and the test called it CONFIRMED because it
# only counted transitions. Asked 12, rendered 6. The handles are load-bearing.
CLIPS = {"a": (120, "0x1E4D6B"), "b": (120, "0x6B1E4D"), "c": (120, "0x4D6B1E"),
         "d": (120, "0x6B4D1E"), "e": (120, "0x1E6B4D")}


def make_media() -> list[str]:
    """Exact-length ProRes clips. Frame counts are asserted, never assumed."""
    os.makedirs(MEDIA, exist_ok=True)
    made = []
    for name, (frames, colour) in CLIPS.items():
        path = os.path.join(MEDIA, f"cap_{name}.mov")
        secs = frames * FPS[1] / FPS[0]
        subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
             "-i", f"color=c={colour}:s={RASTER[0]}x{RASTER[1]}:r={FPS[0]}/{FPS[1]}:d={secs:.6f}",
             "-vf", f"drawtext=text='{name.upper()}':fontsize=200:fontcolor=white:"
                    f"x=(w-text_w)/2:y=(h-text_h)/2,trim=end_frame={frames},format=yuv422p10le",
             "-c:v", "prores_ks", "-profile:v", "3", "-frames:v", str(frames), path],
            check=True)
        got = probe_frames(path)
        if got != frames:
            sys.exit(f"ERROR: {path} is {got} frames, asked for {frames}. A capability test built "
                     f"on a clip of the wrong length proves nothing about the backend.")
        made.append(path)
        print(f"  {os.path.basename(path)}  {got} frames")
    return made


def probe_frames(path: str) -> int:
    r = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                        "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", path],
                       capture_output=True, text=True, check=True)
    return int(r.stdout.strip())


def plan(preset: str | None = None) -> dict:
    """A timeline whose only job is to exercise one feature per clip."""
    m = {k: os.path.join(MEDIA, f"cap_{k}.mov") for k in CLIPS}
    return {
        "name": "premiere-capability-check",
        "fps": list(FPS),
        "timeline_raster": list(RASTER),
        "track_index_base": 0,
        "keyframe_time_base": "clip",
        "media": m,
        "baked": [{"what": "nothing", "why": "synthetic clips; this is a capability check, "
                                            "not a deliverable"}],
        "tracks": [
            {"id": "V1", "kind": "video", "index": 1, "name": "CAPABILITY", "nodes": [
                # 1. a LINEAR fade, in and out
                {"kind": "clip", "media": "a", "rec": 0, "len": 90, "src_in": 0,
                 "_feature": "fade_linear",
                 "fade": {"in": {"frames": 12, "shape": "linear"},
                          "out": {"frames": 18, "shape": "linear"}}},
                # 2. a SHAPED fade — the thing Resolve cannot do at all
                {"kind": "clip", "media": "b", "rec": 90, "len": 90, "src_in": 0,
                 "_feature": "fade_shaped",
                 "fade": {"in": {"frames": 15, "shape": "ease"},
                          "out": {"frames": 15, "shape": "ease"}}},
                # 3. a keyframed push-in that MOVES
                {"kind": "clip", "media": "c", "rec": 180, "len": 90, "src_in": 0,
                 "_feature": "keyframes",
                 "keyframes": [{"param": "Scale",
                                "points": [{"frame": 0, "value": 100.0},
                                           {"frame": 89, "value": 120.0}]}]},
                # 4. a disabled clip — and the OUTGOING side of the transition below, which is why
                #    it is 78 frames of a longer source rather than all of it. See below.
                {"kind": "clip", "media": "d", "rec": 270, "len": 78, "src_in": 0,
                 "_feature": "clip_disable", "disabled": True},
                # 5. a cross dissolve at the head, butted against clip 4 with no gap.
                #
                # 🔴 BOTH clips must keep a handle across this cut, because a centred transition
                # takes HALF its duration from each side. src_in 10 gives the incoming clip its
                # head handle; clip 4 being 78 frames long gives the outgoing clip a tail handle.
                # When clip 4 used its whole source, Premiere silently truncated this dissolve to
                # 6 frames entirely on one side — no warning, clip length unchanged, and the
                # count-based verdict passed it. Nothing in the API reports the truncation.
                #
                # ⚠️ And 78 is deliberately shorter than the source under EITHER length Premiere
                # might believe. Lengthening the media alone did not fix it: Premiere kept the
                # cached 90-frame metadata from the earlier import of the same filename, so the
                # handle existed on disk and not in the project. The layout must not depend on a
                # re-import happening.
                {"kind": "clip", "media": "e", "rec": 348, "len": 70, "src_in": 10,
                 "_feature": "transition",
                 "transition": {"in": {"frames": 12, "type": "cross_dissolve"}}},
            ]},
        ],
        "render": ({"output": os.path.join(MEDIA, "capability.mov"), "preset": preset,
                    "frames": 418} if preset else None),
    }


# --------------------------------------------------------------------------------------------
# reading the timeline back, and judging it
# --------------------------------------------------------------------------------------------

def param(components: list, comp_match: str, param_name: str) -> dict | None:
    for c in components or []:
        if comp_match.lower() not in str(c.get("matchName", "")).lower():
            continue
        for p in c.get("params", []):
            if str(p.get("displayName", "")).lower() == param_name.lower():
                return p
    return None


TRANSITION_CUT = 348          # where clip 5 starts, and so where the dissolve is centred
TRANSITION_FRAMES = 12        # what the plan asks for


def wait_for_render(path: str, frames: int, timeout: float = 600.0) -> bool:
    """Block until the render has all its frames.

    ⚠️ `render` returns as soon as Premiere ACCEPTS the export, not when the file is finished —
    the op reports ok in half a second for a job that takes a minute. Measuring the file at that
    point reads a truncated render, or no file at all, and would report a geometry failure that is
    really a race. So poll the frame count until it stops growing at the number the plan asked for.
    """
    deadline = time.time() + timeout
    last, stable = -1, 0
    while time.time() < deadline:
        got = probe_frames(path) if os.path.exists(path) else 0
        if got >= frames:
            return True
        stable = stable + 1 if got == last else 0
        if stable >= 8 and got > 0:
            print(f"  render stalled at {got}/{frames} frames")
            return False
        last = got
        time.sleep(2.0)
    print(f"  render timed out at {last}/{frames} frames")
    return False


def luma_per_frame(path: str, first: int, last: int) -> list[float]:
    """Mean luma of each frame in [first, last], straight out of ffmpeg."""
    r = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-i", path,
         "-vf", f"select='between(n\\,{first}\\,{last})',signalstats,"
                f"metadata=print:key=lavfi.signalstats.YAVG:file=-",
         "-vsync", "0", "-f", "null", os.devnull],
        capture_output=True, text=True)
    out = []
    for line in r.stdout.splitlines():
        if "YAVG=" in line:
            out.append(float(line.split("YAVG=")[1]))
    return out


def judge_transition_geometry(path: str) -> tuple[bool, str]:
    """Measure the dissolve in the RENDER, because no read-back can see it.

    🔴 This is the only check in this file that can verify a transition's duration or its position
    relative to the cut. A TRANSITION track item marshals as null, so the timeline can only be
    asked *whether* one exists. A 12-frame dissolve that Premiere quietly truncated to 6 frames on
    one side of the cut read back identically to a correct one.

    The outgoing clip is disabled, so it contributes black: the dissolve is a clean ramp from the
    black floor up to the incoming clip's level, and its length and midpoint are both measurable.
    """
    lo, hi = TRANSITION_CUT - TRANSITION_FRAMES, TRANSITION_CUT + TRANSITION_FRAMES
    vals = luma_per_frame(path, lo, hi)
    if len(vals) != hi - lo + 1:
        return False, f"expected {hi - lo + 1} frames of luma from the render, got {len(vals)}"
    return judge_ramp(vals, lo)


def judge_ramp(vals: list[float], lo: int) -> tuple[bool, str]:
    """The measurement itself, separated from ffmpeg so it can be tested without a render."""
    floor, ceiling = min(vals), max(vals)
    if ceiling - floor < 50:
        return False, (f"no ramp in the render at all: luma spans {floor:.1f}..{ceiling:.1f} across "
                       f"frames {lo}..{lo + len(vals) - 1}, so nothing dissolved")

    # the ramp is every frame strictly between the two plateaus
    mid = (floor + ceiling) / 2.0
    ramp = [i + lo for i, v in enumerate(vals) if floor + 1 < v < ceiling - 1]
    if not ramp:
        return False, "luma is flat then jumps: a hard cut, not a dissolve"
    span = (ramp[-1] + 1) - (ramp[0] - 1)          # inclusive of the plateau frames either side

    # and the 50% point lands AT the cut for a centred transition
    nearest_mid = min(range(len(vals)), key=lambda i: abs(vals[i] - mid)) + lo

    ok = span == TRANSITION_FRAMES and abs(nearest_mid - TRANSITION_CUT) <= 1
    return ok, (f"rendered ramp spans {span} frames ({ramp[0] - 1}..{ramp[-1] + 1}), asked "
                f"{TRANSITION_FRAMES}; 50% luma at frame {nearest_mid}, cut is at "
                f"{TRANSITION_CUT} — a centred dissolve is half either side of the cut")


def transition_count(track: dict) -> int:
    """How many TRANSITION items a read-back track reports.

    The panel reports `{"count": N, "unreadable": M, "items": [...]}` because a TRANSITION track
    item marshals as `null` and so cannot be described. Older read-backs are a bare list. Both are
    accepted so a saved report from either panel version still judges the same way.
    """
    t = track.get("transitions")
    if t is None:
        return 0
    if isinstance(t, dict):
        return int(t.get("count", 0))
    return len(t)


def judge(seen: dict, want: dict) -> tuple[dict, list[str]]:
    """Per-feature verdict from what the timeline actually reports."""
    notes: list[str] = []
    verdict: dict[str, bool] = {}

    nodes = []
    for t in seen.get("tracks", []):
        if t.get("askedIndex") == 0:
            nodes = t.get("nodes", [])
    if len(nodes) != 5:
        notes.append(f"expected 5 clips on V1, read back {len(nodes)} — nothing else is "
                     f"trustworthy until that matches")
        return ({k: False for k in ("fade_linear", "fade_shaped", "keyframes", "clip_disable",
                                    "transition")}, notes)

    fps = FPS[0] / FPS[1]

    # --- 1. fade_linear: four opacity keyframes, 0 -> 100 -> 100 -> 0
    op = param(nodes[0].get("components"), "Opacity", "Opacity")
    if not op:
        verdict["fade_linear"] = False
        notes.append("clip 1 has no Opacity parameter to read")
    else:
        kfs = op.get("keyframes") or []
        vals = [k.get("value") for k in kfs]
        ok = (op.get("timeVarying") is True and len(kfs) == 4
              and vals[0] is not None and vals[0] < 5 and vals[-1] is not None and vals[-1] < 5
              and any(v is not None and v > 95 for v in vals[1:3]))
        verdict["fade_linear"] = ok
        notes.append(f"fade_linear: timeVarying={op.get('timeVarying')} "
                     f"keyframes={len(kfs)} values={vals}")
        if kfs:
            frames = [round(k["ticks"] / (TICKS := 254016000000) * fps) for k in kfs]
            notes.append(f"  at frames {frames} (asked 0, 12, 71, 89)")

    # --- 2. fade_shaped: same shape, but the interpolation must not be linear
    op2 = param(nodes[1].get("components"), "Opacity", "Opacity")
    if not op2:
        verdict["fade_shaped"] = False
        notes.append("clip 2 has no Opacity parameter to read")
    else:
        kfs2 = op2.get("keyframes") or []
        # ✅ Interpolation IS readable, through Keyframe.getTemporalInterpolationMode(). An earlier
        # version of this file said it was not and settled for "the keyframes exist" — which would
        # have passed a shaped fade that came out linear. Assert the modes differ from the linear
        # clip's, which is the only thing that makes "shaped" mean anything.
        modes = [k.get("interpolation") for k in kfs2]
        linear_modes = [k.get("interpolation") for k in (op.get("keyframes") or [])] if op else []
        # 🔴 EVERY keyframe must be shaped, not merely "different from the linear clip".
        #
        # The first version asked only that the two sets differ, and passed a read-back of
        # [0, 5, 0, 5] — the mode on the second point of each pair only. A segment takes its
        # interpolation from the keyframe it LEAVES FROM, so every ramp still departed linear and
        # the ease changed nothing visible. The assertion had laundered the defect.
        shaped = [m for m in modes if m is not None and m != 0]
        verdict["fade_shaped"] = bool(op2.get("timeVarying") and len(kfs2) == 4
                                      and len(shaped) == len(kfs2)
                                      and all(m == 0 for m in linear_modes if m is not None))
        notes.append(f"fade_shaped: keyframes={len(kfs2)} interpolation={modes} "
                     f"(linear clip reads {linear_modes}) — ALL four must be non-linear, because a "
                     f"ramp takes its shape from the keyframe it leaves")

    # --- 3. keyframes: a Scale ramp that actually moves
    sc = param(nodes[2].get("components"), "Motion", "Scale")
    if not sc:
        verdict["keyframes"] = False
        notes.append("clip 3 has no Motion/Scale parameter to read")
    else:
        kfs3 = sc.get("keyframes") or []
        vals3 = [k.get("value") for k in kfs3]
        moved = (len(kfs3) >= 2 and vals3[0] is not None and vals3[-1] is not None
                 and abs(vals3[-1] - vals3[0]) > 1.0)
        verdict["keyframes"] = bool(sc.get("timeVarying") and moved)
        notes.append(f"keyframes: timeVarying={sc.get('timeVarying')} count={len(kfs3)} "
                     f"values={vals3} — a set that does not MOVE is a static value with extra "
                     f"steps, so the span is what is asserted")

    # --- 4. clip_disable
    verdict["clip_disable"] = nodes[3].get("disabled") is True
    notes.append(f"clip_disable: disabled={nodes[3].get('disabled')}")

    # --- 5. transition.
    # 🔴 There is NO hasTransition() in this API. The declarations carry a docstring saying there
    # is, attached to createRemoveVideoTransitionAction by mistake; calling it threw "is not a
    # function". A transition is its OWN track item, so the evidence is the track's transition
    # COUNT — and that is all it is. A TRANSITION track item marshals across the UXP boundary as
    # `null`, so its start and duration cannot be read at all. An earlier version of this judgement
    # printed the resulting TypeError into the note and passed anyway.
    #
    # So the count is necessary and not sufficient, and it needs its control: every OTHER video
    # track must read 0. Without that, "length 1" could be any structural artefact rather than the
    # thing this test asked for. The GEOMETRY — duration and which side of the cut — is not
    # knowable here at all; it was established by rendering and measuring luma per frame
    # (24 frames asked, 24 rendered, centred on the cut). See references/PREMIERE-STATUS.md.
    n5 = nodes[4]
    dur5 = n5.get("durationSeconds")
    want5 = 70 * FPS[1] / FPS[0]
    len_ok = dur5 is not None and abs(dur5 - want5) < 1e-6
    trans, elsewhere = None, {}
    for t in seen.get("tracks", []):
        tc = transition_count(t)
        if t.get("askedIndex") == 0:
            trans = tc
        else:
            elsewhere[t.get("askedIndex")] = tc
    control_ok = all(c == 0 for c in elsewhere.values())
    verdict["transition"] = bool(trans == 1 and len_ok and control_ok)
    notes.append(f"transition: {trans} on the target track, {elsewhere or 'no'} on the others "
                 f"(all must be 0, or a count of 1 proves nothing) | clip 5 duration={dur5} "
                 f"(asked {want5:.6f}) — a transition consumes handles, so an unchanged clip "
                 f"length is the half of the claim this can see. Duration and side are NOT "
                 f"readable: the track item marshals as null. They were proven by rendering.")

    return verdict, notes


# --------------------------------------------------------------------------------------------

def selftest() -> int:
    """The judgement, against hand-built read-backs. Needs no Premiere and no media."""
    failures = []

    def check(name, cond, detail=""):
        if not cond:
            failures.append(name)
        print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"   {detail}" if detail and not cond
                                                          else ""))

    T = 254016000000
    f = FPS[0] / FPS[1]

    def kf(frame, value, interp=0):
        return {"ticks": int(round(frame / f * T)), "seconds": frame / f, "value": value,
                "interpolation": interp}

    def clip(components, disabled=False, dur=None):
        return {"components": components, "disabled": disabled, "durationSeconds": dur}

    def opacity(kfs, tv=True):
        return [{"matchName": "AE.ADBE Opacity",
                 "params": [{"displayName": "Opacity", "timeVarying": tv,
                             "keyframes": kfs, "keyframeCount": len(kfs)}]}]

    def motion(kfs, tv=True):
        return [{"matchName": "AE.ADBE Motion",
                 "params": [{"displayName": "Scale", "timeVarying": tv,
                             "keyframes": kfs, "keyframeCount": len(kfs)}]}]

    good = {"tracks": [{"askedIndex": 0, "nodes": [
        clip(opacity([kf(0, 0), kf(12, 100), kf(71, 100), kf(89, 0)])),
        clip(opacity([kf(0, 0, 5), kf(15, 100, 5), kf(74, 100, 5), kf(89, 0, 5)])),
        clip(motion([kf(0, 100.0), kf(89, 120.0)])),
        clip([], disabled=True),
        clip([], dur=70 * FPS[1] / FPS[0]),
    ], "transitions": [{"startTicks": 0, "durationTicks": 1}]}]}
    v, _ = judge(good, plan())
    check("a healthy read-back promotes all five", all(v.values()), str(v))

    # --- 🔴 a transition that ATE HANDLES and shortened the clip must be refused. That is the
    #     failure that matters: the cut is no longer where the plan put it, and hasTransition()
    #     still says true, so the obvious check alone would pass it.
    bad = json.loads(json.dumps(good))
    bad["tracks"][0]["nodes"][4]["durationSeconds"] = 58 * FPS[1] / FPS[0]
    v2, _ = judge(bad, plan())
    check("a transition that shortened the clip is refused", v2["transition"] is False,
          "hasTransition() alone cannot see this")

    # --- and a clip of the right length with NO transition attached is refused too
    bad = json.loads(json.dumps(good))
    bad["tracks"][0]["transitions"] = []
    v3, _ = judge(bad, plan())
    check("the right length without a transition is refused", v3["transition"] is False)

    # --- 🔴 the CONTROL. A count of 1 on the asked track only means anything if the tracks nobody
    #     touched read 0. Without this, any structural artefact that happens to report one
    #     transition-shaped item would confirm the capability.
    bad = json.loads(json.dumps(good))
    bad["tracks"].append({"askedIndex": 1, "nodes": [],
                          "transitions": [{"startTicks": 0, "durationTicks": 1}]})
    v4, _ = judge(bad, plan())
    check("a transition on an untouched track refuses the verdict", v4["transition"] is False,
          "then the count is not attributable to what the plan asked for")

    # a genuinely empty control track must NOT spoil a good verdict
    ok2 = json.loads(json.dumps(good))
    ok2["tracks"].append({"askedIndex": 1, "nodes": [], "transitions": []})
    v5, _ = judge(ok2, plan())
    check("an empty control track still confirms", v5["transition"] is True)

    # --- the panel now reports {count, unreadable, items} because the item marshals as null.
    #     Both shapes must judge identically, or a saved report from either version misleads.
    dictform = json.loads(json.dumps(good))
    dictform["tracks"][0]["transitions"] = {"count": 1, "unreadable": 1, "items": []}
    v6, _ = judge(dictform, plan())
    check("the dict-shaped read-back judges the same as the list", v6["transition"] is True,
          "a null item is expected, not a failure")
    dictform["tracks"][0]["transitions"] = {"count": 0, "unreadable": 0, "items": []}
    v7, _ = judge(dictform, plan())
    check("a dict-shaped count of 0 is refused", v7["transition"] is False)

    # --- a fade with ONE keyframe does not fade
    bad = json.loads(json.dumps(good))
    bad["tracks"][0]["nodes"][0]["components"][0]["params"][0]["keyframes"] = [kf(0, 0)]
    v, _ = judge(bad, plan())
    check("one opacity keyframe is refused", v["fade_linear"] is False)

    # --- keyframes present but NOT time-varying: the parameter was set statically
    bad = json.loads(json.dumps(good))
    bad["tracks"][0]["nodes"][0]["components"][0]["params"][0]["timeVarying"] = False
    v, _ = judge(bad, plan())
    check("opacity keyframes without timeVarying are refused", v["fade_linear"] is False)

    # --- 🔴 a Scale ramp whose two points are the SAME value is not animation
    bad = json.loads(json.dumps(good))
    bad["tracks"][0]["nodes"][2]["components"][0]["params"][0]["keyframes"] = \
        [kf(0, 100.0), kf(89, 100.0)]
    v, _ = judge(bad, plan())
    check("a Scale ramp that does not move is refused", v["keyframes"] is False,
          "two keyframes at the same value is a static value with extra steps")

    # --- a fade that never reaches full opacity
    bad = json.loads(json.dumps(good))
    bad["tracks"][0]["nodes"][0]["components"][0]["params"][0]["keyframes"] = \
        [kf(0, 0), kf(12, 40), kf(71, 40), kf(89, 0)]
    v, _ = judge(bad, plan())
    check("a fade that never reaches 100 is refused", v["fade_linear"] is False)

    # --- 🔴 the ACTUAL bug: shaped on alternate keyframes only, [0, 5, 0, 5]
    bad = json.loads(json.dumps(good))
    ks = bad["tracks"][0]["nodes"][1]["components"][0]["params"][0]["keyframes"]
    for i, k in enumerate(ks):
        k["interpolation"] = 5 if i % 2 else 0
    v, _ = judge(bad, plan())
    check("shaped on alternate keyframes only is refused", v["fade_shaped"] is False,
          "[0,5,0,5] means every ramp departs from a linear keyframe — the ease does nothing")

    # --- 🔴 a "shaped" fade whose interpolation came out LINEAR must be refused
    bad = json.loads(json.dumps(good))
    for k in bad["tracks"][0]["nodes"][1]["components"][0]["params"][0]["keyframes"]:
        k["interpolation"] = 0                      # same as the linear clip
    v, _ = judge(bad, plan())
    check("a shaped fade that came out linear is refused", v["fade_shaped"] is False,
          "identical interpolation to the linear clip means 'shaped' did nothing")

    # --- not disabled
    bad = json.loads(json.dumps(good))
    bad["tracks"][0]["nodes"][3]["disabled"] = False
    v, _ = judge(bad, plan())
    check("an enabled clip is not read as disabled", v["clip_disable"] is False)

    # --- the wrong number of clips must fail EVERYTHING, not be judged per feature
    short = {"tracks": [{"askedIndex": 0, "nodes": good["tracks"][0]["nodes"][:3],
                         "transitions": []}]}
    v, notes = judge(short, plan())
    check("a short timeline fails every feature", not any(v.values()))
    check("and says why", any("expected 5 clips" in n for n in notes))

    # --- 🔴 the RENDER measurement, which is the only thing that can see a transition's geometry.
    #     These four cases are the shapes a dissolve can come out as, and the harness could not
    #     tell them apart until it measured pixels.
    lo = TRANSITION_CUT - TRANSITION_FRAMES
    BLACK, FULL = 64.0, 342.0

    def ramp_between(start, end):
        """luma over [lo, cut+12] with a linear ramp from frame `start` to frame `end`."""
        out = []
        for fr in range(lo, TRANSITION_CUT + TRANSITION_FRAMES + 1):
            if fr <= start:
                out.append(BLACK)
            elif fr >= end:
                out.append(FULL)
            else:
                out.append(BLACK + (FULL - BLACK) * (fr - start) / float(end - start))
        return out

    half = TRANSITION_FRAMES // 2
    ok, why = judge_ramp(ramp_between(TRANSITION_CUT - half, TRANSITION_CUT + half), lo)
    check("a 12-frame dissolve centred on the cut is confirmed", ok, why)

    # the ACTUAL bug: half the duration, entirely on the outgoing side of the cut
    ok, why = judge_ramp(ramp_between(TRANSITION_CUT - half, TRANSITION_CUT), lo)
    check("a dissolve truncated to 6 frames on one side is refused", not ok,
          "this is what a missing tail handle produces, and it read back as correct")

    ok, why = judge_ramp([BLACK] * TRANSITION_FRAMES + [FULL] * (TRANSITION_FRAMES + 1), lo)
    check("a hard cut is refused", not ok, why)

    ok, why = judge_ramp([BLACK] * (2 * TRANSITION_FRAMES + 1), lo)
    check("a render with no ramp at all is refused", not ok, why)

    # right length, wrong place: 12 frames but entirely after the cut
    ok, why = judge_ramp(ramp_between(TRANSITION_CUT, TRANSITION_CUT + TRANSITION_FRAMES), lo)
    check("a full-length dissolve on the wrong side of the cut is refused", not ok, why)

    # --- the plan itself must be valid and must NOT ask for anything unimplemented
    errs = nle_ir.validate(plan())
    check("the capability plan validates", not errs, str(errs))
    kinds = {n["kind"] for t in plan()["tracks"] for n in t["nodes"]}
    check("the plan uses no unimplemented node kind", kinds <= {"clip", "graphic"}, str(kinds))

    print(f"\n{'PASS' if not failures else 'FAIL'}: {len(failures)} failure(s)")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--make-media", action="store_true")
    ap.add_argument("--preset", help="an .epr, to also prove `render` on this timeline")
    ap.add_argument("--via-program", action="store_true",
                    help="build through premiere_program.py's step emitter instead of the panel's "
                         "`build` op. The same plan, the same judgement — which is what makes it "
                         "evidence that the port is equivalent rather than merely finished.")
    ap.add_argument("--jobdir")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    if a.make_media:
        print("building synthetic capability clips:")
        make_media()
        return 0

    missing = [k for k in CLIPS if not os.path.exists(os.path.join(MEDIA, f"cap_{k}.mov"))]
    if missing:
        print(f"missing media for {missing}. Run --make-media first.")
        return 2

    import premiere_bridge as pb
    b = pb.Bridge(jobdir=a.jobdir or pb.DEFAULT_JOBDIR, timeout=900)

    p = plan(preset=a.preset)
    errs = nle_ir.validate(p)
    if errs:
        print("the capability plan does not validate:")
        for e in errs:
            print("  - " + e)
        return 1

    how = "premiere_program.py (steps)" if a.via_program else "the panel's build op"
    print(f"building the capability timeline in Premiere via {how}...")
    try:
        if a.via_program:
            import premiere_program as pp
            res = pp.run_build(b, p)
            seqinfo = res["sequence"]
            print(f"  built: raster {seqinfo.get('raster')} ticks {seqinfo.get('ticksPerFrame')}")
        else:
            res = b.build(p, allow_unverified=True)
            print(f"  built: raster {res['sequence'].get('readBackRaster')} "
                  f"ticks {res['sequence'].get('readBackTicksPerFrame')}")
    except Exception as e:
        print(f"BUILD FAILED: {e}")
        return 1

    print("reading the timeline back...")
    seen = b.readback(with_components=True)
    seen = b.send({"op": "readback", "with_components": True, "with_keyframes": True})

    verdict, notes = judge(seen, p)

    # 🔴 The timeline cannot answer the question that matters about a transition. Render and measure.
    #    Without this, a dissolve Premiere truncated to half its length on one side of the cut is
    #    indistinguishable from a correct one — that happened, and this run passed it.
    if a.preset:
        rendered = p["render"]["output"]
        print(f"rendering to measure the dissolve ({os.path.basename(rendered)})...")
        if os.path.exists(rendered):
            os.remove(rendered)
        r = b.render(rendered, a.preset)
        if not r.get("ok"):
            print(f"  RENDER FAILED: {r}")
            verdict["transition"] = False
            notes.append("transition: the render failed, so its geometry is unverified")
        else:
            waited = wait_for_render(rendered, p["render"]["frames"])
            if not waited:
                verdict["transition"] = False
                notes.append(f"transition: {rendered} never reached "
                             f"{p['render']['frames']} frames, so geometry is unverified")
            else:
                geo_ok, geo = judge_transition_geometry(rendered)
                verdict["transition"] = bool(verdict.get("transition") and geo_ok)
                notes.append("transition geometry (MEASURED IN THE RENDER): " + geo)
    else:
        notes.append("transition: geometry NOT checked — pass --preset to render and measure it. "
                     "The timeline read-back cannot see a transition's duration or its side.")

    print("\nwhat the timeline actually says:")
    for n in notes:
        print("  " + n)

    print("\nverdict:")
    for k in ("fade_linear", "fade_shaped", "keyframes", "clip_disable", "transition"):
        print(f"  {k:<14} {'✅ CONFIRMED' if verdict.get(k) else '🔴 NOT CONFIRMED'}")

    out = os.path.join(MEDIA, "capability_report.json")
    with open(out, "w") as fh:
        json.dump({"verdict": verdict, "notes": notes, "readback": seen}, fh, indent=2)
    print(f"\nwritten to {out}")

    if all(verdict.get(k) for k in ("fade_linear", "keyframes", "clip_disable", "transition")):
        print("\nPromote these in nle_ir.CAPABILITIES['premiere'] — and only these.")
        return 0
    print("\nSomething did not take. Do NOT promote anything that reads NOT CONFIRMED.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

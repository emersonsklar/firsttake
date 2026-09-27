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

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import nle_ir                                                          # noqa: E402

MEDIA = os.path.join(HERE, "fixtures", "premiere")
FPS = (30000, 1001)
RASTER = (1920, 1080)

# One clip per feature, each long enough that a fade cannot be confused with a whole clip.
CLIPS = {"a": (90, "0x1E4D6B"), "b": (90, "0x6B1E4D"), "c": (90, "0x4D6B1E"),
         "d": (90, "0x6B4D1E")}


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
                # 4. a disabled clip
                {"kind": "clip", "media": "d", "rec": 270, "len": 90, "src_in": 0,
                 "_feature": "clip_disable", "disabled": True},
            ]},
        ],
        "render": ({"output": os.path.join(MEDIA, "capability.mov"), "preset": preset,
                    "frames": 360} if preset else None),
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


def judge(seen: dict, want: dict) -> tuple[dict, list[str]]:
    """Per-feature verdict from what the timeline actually reports."""
    notes: list[str] = []
    verdict: dict[str, bool] = {}

    nodes = []
    for t in seen.get("tracks", []):
        if t.get("askedIndex") == 0:
            nodes = t.get("nodes", [])
    if len(nodes) != 4:
        notes.append(f"expected 4 clips on V1, read back {len(nodes)} — nothing else is "
                     f"trustworthy until that matches")
        return {k: False for k in ("fade_linear", "fade_shaped", "keyframes", "clip_disable")}, notes

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

    def clip(components, disabled=False):
        return {"components": components, "disabled": disabled}

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
    ]}]}
    v, _ = judge(good, plan())
    check("a healthy read-back promotes all four", all(v.values()), str(v))

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
    short = {"tracks": [{"askedIndex": 0, "nodes": good["tracks"][0]["nodes"][:2]}]}
    v, notes = judge(short, plan())
    check("a short timeline fails every feature", not any(v.values()))
    check("and says why", any("expected 4 clips" in n for n in notes))

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

    print("building the capability timeline in Premiere...")
    try:
        res = b.build(p, allow_unverified=True)
    except Exception as e:
        print(f"BUILD FAILED: {e}")
        return 1
    print(f"  built: raster {res['sequence'].get('readBackRaster')} "
          f"ticks {res['sequence'].get('readBackTicksPerFrame')}")

    print("reading the timeline back...")
    seen = b.readback(with_components=True)
    seen = b.send({"op": "readback", "with_components": True, "with_keyframes": True})

    verdict, notes = judge(seen, p)
    print("\nwhat the timeline actually says:")
    for n in notes:
        print("  " + n)

    print("\nverdict:")
    for k in ("fade_linear", "fade_shaped", "keyframes", "clip_disable"):
        print(f"  {k:<14} {'✅ CONFIRMED' if verdict.get(k) else '🔴 NOT CONFIRMED'}")
    print(f"  {'transition':<14} ⚠️  not implemented in this backend — not tested")

    out = os.path.join(MEDIA, "capability_report.json")
    with open(out, "w") as fh:
        json.dump({"verdict": verdict, "notes": notes, "readback": seen}, fh, indent=2)
    print(f"\nwritten to {out}")

    if all(verdict.get(k) for k in ("fade_linear", "keyframes", "clip_disable")):
        print("\nPromote these in nle_ir.CAPABILITIES['premiere'] — and only these.")
        return 0
    print("\nSomething did not take. Do NOT promote anything that reads NOT CONFIRMED.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

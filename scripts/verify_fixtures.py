#!/usr/bin/env python3
"""
Regression harness for shootcheck.

Runs shootcheck against the generated fixtures and asserts that it reports the
values that were deliberately planted and flags the faults that were
deliberately introduced. Run this after editing thresholds or checks — a
threshold tweak that silently stops detecting clipped audio is worse than no
tool at all.

    ./make_fixtures.py            # or --fast
    ./verify_fixtures.py

Exit 0 = all assertions held. Exit 1 = at least one regression.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")
TOOL = os.path.join(HERE, "shootcheck.py")

PASS, FAILED = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAILED).append(f"{name}" + (f"  [{detail}]" if detail else ""))
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))


def run_tool(files: list[str], roles: list[str] | None = None,
             profiles: list[str] | None = None) -> dict:
    cmd = [sys.executable, TOOL, "--json"]
    cmd += [os.path.join(FIX, f) for f in files]
    for r in roles or []:
        cmd += ["--role", r]
    for p in profiles or []:
        cmd += ["--profile", p]
    p = subprocess.run(cmd, capture_output=True, timeout=3600)
    try:
        return json.loads(p.stdout.decode())
    except json.JSONDecodeError:
        print(p.stdout.decode()[:2000])
        print(p.stderr.decode()[-2000:])
        raise SystemExit("shootcheck did not return JSON — tool is broken")


def levels(report: dict, fname: str) -> dict[str, str]:
    for f in report["files"]:
        if os.path.basename(f["path"]) == fname:
            return {x["check"]: x["level"] for x in f["findings"]}
    raise KeyError(fname)


def measured(report: dict, fname: str, section: str) -> dict:
    for f in report["files"]:
        if os.path.basename(f["path"]) == fname:
            return f[section]
    raise KeyError(fname)


def cross_levels(report: dict) -> dict[str, str]:
    return {x["check"]: x["level"] for x in report["cross_camera"]}


def near(a, b, tol) -> bool:
    return a is not None and abs(a - b) <= tol


def main() -> int:
    if not os.path.isdir(FIX):
        raise SystemExit("No fixtures/. Run ./make_fixtures.py first.")

    # Is this a full-size fixture set or a --fast one? Resolution expectations
    # only apply to the full-size set.
    probe = run_tool(["good_tight.mp4"], ["tight"], ["slog3"])
    w = measured(probe, "good_tight.mp4", "stream").get("width")
    full = (w == 3840)
    print(f"fixture set: {w}px wide "
          f"({'full size' if full else 'FAST — resolution checks not asserted'})\n")

    # ---------------- known-good pair -----------------------------------
    print("known-good pair (planted: tight face 60 IRE, wide face 56 IRE, "
          "both 29.97, 10-bit)")
    r = run_tool(["good_tight.mp4", "good_wide.mp4"],
                 ["tight", "wide"], ["slog3", "dlogm"])
    gt, gw = levels(r, "good_tight.mp4"), levels(r, "good_wide.mp4")
    vt = measured(r, "good_tight.mp4", "video")
    vw = measured(r, "good_wide.mp4", "video")

    for chk in ("exposure level", "highlight clipping", "shadow crush",
                "focus stability", "video bit depth", "chroma subsampling",
                "rotation", "peak level", "noise floor", "dynamic range",
                "flat samples", "sample rate"):
        check(f"good_tight: {chk} == PASS", gt.get(chk) == "PASS",
              f"got {gt.get(chk)}")
    for chk in ("exposure level", "highlight clipping", "video bit depth",
                "chroma subsampling", "focus stability"):
        check(f"good_wide: {chk} == PASS", gw.get(chk) == "PASS",
              f"got {gw.get(chk)}")
    if full:
        check("good_tight: resolution == PASS", gt.get("resolution") == "PASS",
              f"got {gt.get('resolution')}")

    check("good_tight: brightest region ~= 60 IRE (planted)",
          near(vt.get("brightest_region_ire"), 60.0, 2.0),
          f"got {vt.get('brightest_region_ire')}")
    check("good_wide: brightest region ~= 56 IRE (planted)",
          near(vw.get("brightest_region_ire"), 56.0, 2.0),
          f"got {vw.get('brightest_region_ire')}")
    check("good_tight: clipping ~= 0%", near(vt.get("clip_pct_worst"), 0.0, 0.1),
          f"got {vt.get('clip_pct_worst')}")
    check("good_tight: crush ~= 0%", near(vt.get("crush_pct_worst"), 0.0, 0.1),
          f"got {vt.get('crush_pct_worst')}")
    check("cross-camera frame rate == PASS (both 29.97)",
          cross_levels(r).get("cross-camera frame rate") == "PASS",
          f"got {cross_levels(r).get('cross-camera frame rate')}")
    check("cross-camera exposure == PASS (60 vs 56 IRE)",
          cross_levels(r).get("cross-camera exposure") == "PASS",
          f"got {cross_levels(r).get('cross-camera exposure')}")
    if full:
        check("verdict READY on the good pair", r["verdict"] == "READY",
              f"got {r['verdict']} / {r['counts']}")

    # -------- the threshold that was wrong: a LOG-crushed, 709-legal floor --------
    #
    # 20% of the frame floored at code 70. That is crushed for S-Log3 and D-Log M (black at 95)
    # and legal for Rec.709 (black at 64), so the SAME FILE must read differently per profile.
    # Asserting the asymmetry is the point: a single-profile check would pass with either
    # threshold, which is exactly how D-Log M sat at 64 with the crush fixtures green.
    if os.path.exists(os.path.join(FIX, "crushed_log_floor.mp4")):
        for prof, want_flagged in (("dlogm", True), ("slog3", True), ("unknown", False)):
            rc = run_tool(["crushed_log_floor.mp4"], ["tight"], [prof])
            lv = levels(rc, "crushed_log_floor.mp4")
            mv = measured(rc, "crushed_log_floor.mp4", "video")
            pct = mv.get("crush_pct_worst")
            flagged = lv.get("shadow crush") in ("WARN", "FAIL")
            check("crushed_log_floor @ %-7s : crush %s (%.1f%%)"
                  % (prof, "FLAGGED" if want_flagged else "not flagged",
                     pct if isinstance(pct, (int, float)) else -1),
                  flagged == want_flagged,
                  "shadow crush == %r, crush_pct_worst == %r; a floor at code 70 must register "
                  "for a log profile (black at 95) and not for a Rec.709 one (black at 64)"
                  % (lv.get("shadow crush"), pct))
        # and the measured percentage must actually differ between the two interpretations
        r_log = measured(run_tool(["crushed_log_floor.mp4"], ["tight"], ["dlogm"]),
                         "crushed_log_floor.mp4", "video").get("crush_pct_worst")
        r_709 = measured(run_tool(["crushed_log_floor.mp4"], ["tight"], ["unknown"]),
                         "crushed_log_floor.mp4", "video").get("crush_pct_worst")
        check("crushed_log_floor: dlogm sees more crush than Rec.709 does",
              isinstance(r_log, (int, float)) and isinstance(r_709, (int, float))
              and r_log > r_709 + 5.0,
              "dlogm %r vs unknown %r — if these are equal the threshold is not being applied"
              % (r_log, r_709))

    # ---------------- the expensive error: fps mismatch -----------------
    print("\nplanted fault: 29.97 tight vs TRUE 30.000 wide")
    r2 = run_tool(["good_tight.mp4", "bad_fps_wide.mp4"],
                  ["tight", "wide"], ["slog3", "dlogm"])
    check("cross-camera frame rate == FAIL",
          cross_levels(r2).get("cross-camera frame rate") == "FAIL",
          f"got {cross_levels(r2).get('cross-camera frame rate')}")
    check("verdict NOT READY", r2["verdict"] == "NOT READY",
          f"got {r2['verdict']}")

    # ---------------- everything wrong ----------------------------------
    print("\nplanted faults: 8-bit / 1080p / 25fps / 6% clip / 15% crush / "
          "clipped audio")
    r3 = run_tool(["bad_everything.mp4"], ["tight"], ["slog3"])
    be = levels(r3, "bad_everything.mp4")
    vb = measured(r3, "bad_everything.mp4", "video")
    for chk in ("video bit depth", "frame rate", "highlight clipping",
                "shadow crush", "peak level"):
        check(f"bad_everything: {chk} == FAIL", be.get(chk) == "FAIL",
              f"got {be.get(chk)}")
    if full:
        check("bad_everything: resolution == FAIL", be.get("resolution") == "FAIL",
              f"got {be.get('resolution')}")
    check("bad_everything: clipping ~= 6% (planted)",
          near(vb.get("clip_pct_worst"), 6.0, 0.6),
          f"got {vb.get('clip_pct_worst')}")
    check("bad_everything: crush ~= 15% (planted)",
          near(vb.get("crush_pct_worst"), 15.0, 0.6),
          f"got {vb.get('crush_pct_worst')}")
    check("bad_everything: exposure level flagged",
          be.get("exposure level") in ("WARN", "FAIL"),
          f"got {be.get('exposure level')}")

    # ---------------- 8-bit content in a 10-bit container ---------------
    # pix_fmt says 10-bit for both of these; only the content differs.
    print("\nplanted fault: 8-bit content inside a 10-bit ProRes container")
    r35 = run_tool(["bad_8bit_in_10bit.mov"], ["prores"], ["slog3"])
    b8 = levels(r35, "bad_8bit_in_10bit.mov")
    f8 = measured(r35, "bad_8bit_in_10bit.mov", "video").get(
        "code_multiples_of_4_frac")
    check("bad_8bit_in_10bit: container still reads 10-bit (the point)",
          b8.get("video bit depth") == "PASS",
          f"got {b8.get('video bit depth')}")
    check("bad_8bit_in_10bit: bit depth (content) == WARN",
          b8.get("bit depth (content)") == "WARN",
          f"got {b8.get('bit depth (content)')} frac={f8}")
    check("good_tight: bit depth (content) == PASS (true 10-bit)",
          gt.get("bit depth (content)") == "PASS",
          f"got {gt.get('bit depth (content)')} "
          f"frac={vt.get('code_multiples_of_4_frac')}")
    check("the two fixtures are actually separated by the metric",
          f8 is not None and vt.get("code_multiples_of_4_frac") is not None
          and f8 > vt["code_multiples_of_4_frac"] * 1.4,
          f"8-bit={f8} vs 10-bit={vt.get('code_multiples_of_4_frac')}")

    # ---------------- focus hunting -------------------------------------
    print("\nplanted fault: sharpness oscillating (AF hunting)")
    r4 = run_tool(["bad_focus.mp4"], ["tight"], ["slog3"])
    bf = levels(r4, "bad_focus.mp4")
    check("bad_focus: focus stability flagged",
          bf.get("focus stability") in ("WARN", "FAIL"),
          f"got {bf.get('focus stability')} "
          f"(cv={measured(r4, 'bad_focus.mp4', 'video').get('sharpness_cv')})")

    # ---------------- sharpness changes because the SUBJECT changed -------
    # A real take is chart, then a face, then an empty room, and the whole-clip
    # CV calls that AF hunting. The settled figure is what makes the warning
    # readable rather than a thing to chase at a client site. Both numbers must
    # be reported, and severity must NOT be softened -- an earlier version
    # downgraded "edge dips" to INFO and flipped verdict on where the 18 samples
    # happened to land, because encoders smear ramp frames across a transition.
    print("\nsubject changes, not AF hunting: both figures must be reported")
    rt = run_tool(["ok_focus_transitions.mp4"], ["tight"], ["slog3"])
    mt = measured(rt, "ok_focus_transitions.mp4", "video")
    check("transitions: whole-clip CV is high",
          mt.get("sharpness_cv", 0) >= 0.25, f"got {mt.get('sharpness_cv')}")
    check("transitions: settled CV is reported and much lower",
          mt.get("sharpness_cv_settled", 1) < 0.15,
          f"got {mt.get('sharpness_cv_settled')}")
    check("transitions: some frames were identified as transitions",
          mt.get("focus_transition_frames", 0) > 0,
          f"got {mt.get('focus_transition_frames')}")

    print("\nplanted fault: dip MID-clip with steady footage either side")
    rid = run_tool(["bad_focus_interior_dip.mp4"], ["tight"], ["slog3"])
    bid = levels(rid, "bad_focus_interior_dip.mp4")
    check("interior dip: focus stability still flagged",
          bid.get("focus stability") in ("WARN", "FAIL"),
          f"got {bid.get('focus stability')} (cv="
          f"{measured(rid, 'bad_focus_interior_dip.mp4', 'video').get('sharpness_cv')})")

    # ---------------- frames missing from the timeline -------------------
    print("\nplanted fault: every 10th frame missing (recorder could not keep up)")
    rdf = run_tool(["bad_dropped_frames.mp4"], ["tight"], ["slog3"])
    df = levels(rdf, "bad_dropped_frames.mp4")
    dv = measured(rdf, "bad_dropped_frames.mp4", "video")
    check("bad_dropped_frames: frame timing flagged",
          df.get("frame timing") in ("WARN", "FAIL"),
          f"got {df.get('frame timing')} "
          f"irregular={dv.get('timing_irregular_pct')}% "
          f"gaps={dv.get('timing_n_big_gaps')}")
    check("good_tight: frame timing == PASS",
          gt.get("frame timing") == "PASS", f"got {gt.get('frame timing')}")

    # ---------------- audio faults --------------------------------------
    print("\nplanted audio faults")
    r5 = run_tool(["no_audio.mp4"], ["tight"])
    check("no_audio: audio stream == FAIL",
          levels(r5, "no_audio.mp4").get("audio stream") == "FAIL",
          f"got {levels(r5, 'no_audio.mp4').get('audio stream')}")

    cases = [
        ("good_audio_24b.wav", "peak level", "PASS", None),
        ("good_audio_24b.wav", "audio bit depth", "PASS", None),
        ("bad_audio_quiet.wav", "peak level", "WARN", -34.0),
        ("bad_audio_clipped.wav", "peak level", "FAIL", None),
        ("bad_audio_dualmono.wav", "channel routing", "WARN", None),
        ("bad_audio_dead_ch.wav", "channel balance", "FAIL", None),
        ("bad_audio_all_silent.wav", "channel balance", "FAIL", None),
        ("bad_audio_16b.wav", "audio bit depth", "WARN", None),
    ]
    for fname, chk, want, peak in cases:
        rr = run_tool([fname])
        lv = levels(rr, fname)
        check(f"{fname}: {chk} == {want}", lv.get(chk) == want,
              f"got {lv.get(chk)}")
        if peak is not None:
            got = measured(rr, fname, "audio").get("peak_dbfs")
            check(f"{fname}: peak ~= {peak} dBFS (planted)",
                  near(got, peak, 1.5), f"got {got}")

    r4 = run_tool(["ok_audio_4ch_hdmi_embed.wav"])
    l4 = levels(r4, "ok_audio_4ch_hdmi_embed.wav")
    check("4ch HDMI embed: empty ch1/2 is INFO, not a dead-transmitter FAIL",
          l4.get("unused channels") == "INFO"
          and l4.get("channel balance") == "PASS",
          f"unused={l4.get('unused channels')} balance={l4.get('channel balance')}")
    check("4ch HDMI embed: routing compared the LIVE pair (3,4)",
          measured(r4, "ok_audio_4ch_hdmi_embed.wav", "audio")
          .get("routing_channels_compared") in (["3", "4"], ["3", "4"]),
          f"got {measured(r4, 'ok_audio_4ch_hdmi_embed.wav', 'audio').get('routing_channels_compared')}")

    rall = run_tool(["bad_audio_all_silent.wav"])
    msg = next((f["message"] for f in rall["files"][0]["findings"]
                if f["check"] == "channel balance"), "")
    check("all-silent says ALL channels, not 'while others carry signal'",
          "ALL" in msg and "others carry signal" not in msg, f"got: {msg[:70]}")

    # ---------------- graceful degradation -------------------------------
    print("\nmalformed input must be handled, not crashed on")
    for fname, chk in (("garbage.mp4", "container"), ("empty.mp4", "file")):
        rr = run_tool([fname])
        lv = levels(rr, fname)
        check(f"{fname}: {chk} == FAIL (no crash)", lv.get(chk) == "FAIL",
              f"got {lv}")

    # ---------------- single file: cross-check degrades ------------------
    r6 = run_tool(["good_audio_24b.wav"])
    check("single audio file: cross-camera section degrades to INFO",
          cross_levels(r6).get("cross-camera") == "INFO",
          f"got {cross_levels(r6)}")

    print(f"\n{'=' * 70}")
    print(f"{len(PASS)} assertions held, {len(FAILED)} regressions")
    if FAILED:
        print("\nREGRESSIONS:")
        for f in FAILED:
            print(f"  - {f}")
    print("=" * 70)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

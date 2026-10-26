#!/usr/bin/env python3
"""assemble_ffmpeg — build the programme from the plan with ffmpeg. No NLE, no licence, nothing paid.

    python3 assemble_ffmpeg.py --plan cut.json --out master.mov
    python3 assemble_ffmpeg.py --plan cut.json --out master.mov --keep-work   # leave intermediates
    python3 assemble_ffmpeg.py --selfcheck        # generate media, assemble, MEASURE the result
    python3 assemble_ffmpeg.py --selftest         # no ffmpeg, no media: the emit logic only

The third backend beside Resolve and Premiere, building the same IR. Everything else in this
pipeline was already ffmpeg — offload, QC, sync, transcription, subtitles, loudness, LUT fitting,
graphic conforming — so the one thing that needed an application was assembly. Now it does not.

🔴 **WHAT YOU GIVE UP: the editable timeline. Not the quality.** The output is a full master, not a
proxy. To change a cut you change the plan and re-assemble, which is how every programme here was
built anyway.

## 🔴 The rule that makes this trustworthy: a file can only be MEASURED

The other two backends can be interrogated — ask the application what it built and diff it against
the plan. **There is no application here.** So this backend is the one that can be wrong while
looking completely right, and the verification is a bigger part of it than the assembly:

- every segment's frame count is asserted before it is concatenated;
- the concatenated picture is asserted against the plan's total;
- the final file's frames, raster and rate are measured, never assumed;
- `--selfcheck` goes further and proves the *content* is in the right place, by building sources
  whose LUMA ENCODES THE SOURCE FRAME NUMBER and then reading luma back per frame. That catches a
  clip that slid, an off-by-one in-point, and a reversed order — none of which change a frame count.

## 🔴 No colour here, deliberately, and this is the important part

`lut3d` exists and is refused anyway. The failure that taught this was not "ffmpeg cannot grade": an
ffmpeg pipeline that re-implemented the colour transform agreed with Resolve on geometry to
**r >= 0.9989** and was still **14-18% out in the grade** — eight already-approved review renders
were not showing what the NLE produced. Two implementations of one transform do not agree, and
nothing warns you, because the picture looks fine.

So: bake the grade into the media ONCE (`bake_cdl_into_lut.py`, then a single LUT pass at full
raster), declare it in the plan's `baked` list, and this assembler does geometry and timing only.
`nle_ir.CAPABILITIES["ffmpeg"]` marks `cdl` and `lut` False so the gate refuses a plan that still
carries an unbaked grade.

## Proven on a delivered programme

A shipped 2228-frame vertical short was rebuilt from its own plan and compared to the delivered
master frame by frame: **mean absolute difference 1.08 codes of 255 across every frame**, max 1.38,
zero frames over 5. The reference is H.264 at 40 Mbps, so part of that residual is the reference's
own codec loss rather than this assembler's error.

## Three traps around concatenated timestamps, all measured here

1. **Stream-copy concat leaves the timestamps discontinuous at every boundary**, and that poisons
   every later stage: `overlay` aligns its inputs by timestamp, and a CFR encoder drops a frame at a
   boundary. It cost one frame at the FIRST boundary, so the intro card ran 93 frames instead of 94
   and everything after sat one frame early — total still exactly 2228, file playing perfectly.
   Nine frames of the comparison fell to 9.9 dB PSNR while the median stayed at 41.8. So `concat()`
   RE-ENCODES in CFR mode rather than copying.
2. 🔴 **Do not add `setpts` on top of CFR mode.** It reads like the careful thing to do and it DROPS
   FRAMES: the same four segments gave 2228 with `-fps_mode cfr` alone and **2020** with an added
   `setpts=N/FRAME_RATE/TB`. CFR already rewrites the timestamps and does not want help.
3. **`-shortest` truncated an output by 2 frames** (1041 of 1043). The length comes from the plan
   via `-frames:v`.

⚠️ Traps 1 and 2 were both found by ASSERTING the frame count at every stage, and trap 1 only by
comparing against a known-good master. Neither changed a total; neither made a file that looked
wrong.

## Windows

Text files are written UTF-8 (the default is cp1252 and it has broken this repo before), paths in
the concat list are escaped, and nothing here uses `fcntl`, `os.statvfs` or `os.uname`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import nle_ir                                                          # noqa: E402

BACKEND = "ffmpeg"

# 🔴 ONE set of colour tags on every segment and filler. METADATA ONLY — no pixel is touched, so the
# "no colour in the assembler" rule above stands. Without it a tagged camera file arriving after an
# untagged filler changes the stream's properties mid-track, ffmpeg rebuilds the filter graph, and a
# multi-input overlay loses sync: the top track vanished and the base froze, with the total exact.
# Measured by `_check_mixed_tags`. bt709 / tv range is what every master here is delivered as.
UNIFORM_TAGS = "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709"

STILL_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}

# Lossless-enough intermediates that concat can stream-copy. ProRes rather than ffv1 because both
# platforms' ffmpeg builds carry prores_ks, and 4444 gives the alpha that overlay tracks need.
PRORES = ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"]
PRORES_ALPHA = ["-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuva444p10le"]


class AssembleError(RuntimeError):
    """Something did not match the plan. The message says what was asked and what was measured."""


# --------------------------------------------------------------------------------------------
# running ffmpeg, and measuring what it wrote
# --------------------------------------------------------------------------------------------

def _tool(name: str) -> str:
    p = shutil.which(name)
    if not p:
        raise AssembleError(f"{name} is not on PATH. This backend is ffmpeg and nothing else, so "
                            f"it cannot run without it. `python3 scripts/doctor.py` will say how to "
                            f"install it.")
    return p


def run(args: list[str], what: str) -> None:
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        tail = (r.stderr or "").strip().splitlines()[-12:]
        raise AssembleError(f"{what} failed (exit {r.returncode}):\n  "
                            + "\n  ".join(tail)
                            + f"\n  command: {' '.join(args[:14])} ...")


def probe(path: str) -> dict:
    """Frames, raster and rate, counted rather than read from a header.

    ⚠️ `-count_frames` decodes. That is the point: `nb_frames` is metadata and has been wrong in
    exactly the situation this backend creates (concatenated streams with discontinuous timestamps).
    """
    if not os.path.isfile(path):
        raise AssembleError(f"expected a file at {path} and there is none")
    r = subprocess.run([_tool("ffprobe"), "-v", "error", "-count_frames", "-select_streams", "v:0",
                        "-show_entries",
                        "stream=nb_read_frames,width,height,r_frame_rate,pix_fmt",
                        "-show_entries", "format=duration", "-of", "json", path],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise AssembleError(f"ffprobe could not read {path}: {(r.stderr or '').strip()[:400]}")
    d = json.loads(r.stdout)
    st = (d.get("streams") or [{}])[0]
    return {
        "frames": int(st.get("nb_read_frames") or 0),
        "width": int(st.get("width") or 0),
        "height": int(st.get("height") or 0),
        "rate": st.get("r_frame_rate") or "",
        "pix_fmt": st.get("pix_fmt") or "",
        "duration": float((d.get("format") or {}).get("duration") or 0.0),
    }


def assert_frames(path: str, want: int, what: str) -> dict:
    got = probe(path)
    if got["frames"] != want:
        raise AssembleError(f"{what}: asked for {want} frames and the file has {got['frames']}. "
                            f"A segment of the wrong length shifts everything after it, and the "
                            f"result still plays.")
    return got


def luma_per_frame(path: str, first: int, last: int) -> list[float]:
    """Mean luma of frames [first, last]. The measuring instrument for content checks."""
    r = subprocess.run(
        [_tool("ffmpeg"), "-nostdin", "-hide_banner", "-i", path,
         "-vf", f"select='between(n\\,{first}\\,{last})',signalstats,"
                f"metadata=print:key=lavfi.signalstats.YAVG:file=-",
         "-vsync", "0", "-f", "null", os.devnull],
        capture_output=True, text=True)
    return [float(line.split("YAVG=")[1]) for line in r.stdout.splitlines() if "YAVG=" in line]


# --------------------------------------------------------------------------------------------
# reading the plan
# --------------------------------------------------------------------------------------------

def video_tracks(plan: dict) -> list[dict]:
    """Video tracks, bottom first. Track index 1 is the base; higher indices overlay it."""
    return sorted([t for t in plan["tracks"] if t.get("kind") == "video"],
                  key=lambda t: t["index"])


def audio_tracks(plan: dict) -> list[dict]:
    return sorted([t for t in plan["tracks"] if t.get("kind") == "audio"],
                  key=lambda t: t["index"])


def total_frames(plan: dict) -> int:
    """How long the programme is, from the plan rather than from whatever came out."""
    render = plan.get("render") or {}
    if render.get("frames"):
        return int(render["frames"])
    if plan.get("total_frames"):
        return int(plan["total_frames"])
    end = 0
    for t in plan["tracks"]:
        for n in t.get("nodes", []):
            end = max(end, int(n["rec"]) + int(n["len"]))
    if not end:
        raise AssembleError("the plan has no nodes, so it has no length")
    return end


def rate_args(fps) -> list[str]:
    return ["-r", f"{int(fps[0])}/{int(fps[1])}"]


def crop_filter(node: dict, plan: dict) -> str:
    """crop then scale, from the same source-pixel rectangle the other two backends use.

    🔴 The crop rectangle is the IR's single definition of a reframe, and this is the most direct
    of the three readings of it: Resolve turns it into Zoom/Pan, Premiere into Scale/Position, and
    ffmpeg just takes the rectangle. Nothing is re-derived here, so it cannot drift from them.

    ⚠️ A FRACTIONAL crop is refused rather than rounded. ffmpeg's crop is integer, and rounding it
    silently would put this backend a fraction of a pixel away from the other two on the same plan —
    a difference nothing would ever report, on the axis this project has already got wrong once.
    """
    cx, cy, cw, ch = node["crop"]
    for label, v in (("x", cx), ("y", cy), ("w", cw), ("h", ch)):
        if float(v) != int(v):
            raise AssembleError(
                f"crop {label}={v} is fractional. ffmpeg's crop takes whole pixels, and rounding it "
                f"here would leave this backend a fraction of a pixel from Resolve and Premiere on "
                f"the same plan, silently. Measure the crop to whole source pixels.")
    tw, th = plan["timeline_raster"]
    return (f"crop={int(cw)}:{int(ch)}:{int(cx)}:{int(cy)},"
            f"scale={int(tw)}:{int(th)}:flags=lanczos,setsar=1")


def fade_filter(node: dict, alpha: bool) -> str:
    """The IR's fade, as ffmpeg's.

    On an OVERLAY track a fade is an alpha ramp; on the base track there is nothing behind it, so
    the same fade is a ramp to black. Both are `fade`, and which one is not a detail the plan should
    have to state — it follows from the track.
    """
    parts = []
    fade = node.get("fade") or {}
    a = ":alpha=1" if alpha else ""
    # 🔴 FRAMES, via start_frame/nb_frames — NOT st/d, which are SECONDS.
    #
    # The first version used `st=0:d=12` with the plan's frame counts, so a 20-frame fade became a
    # 20-SECOND one: at frame 25 the ramp was 4% in and the overlay was invisible. It survived a
    # selftest that asserted the filter STRING contained `d=12`, which is true of both the right
    # answer and the wrong one. Only the rendered luma caught it. Frame counts are what the plan
    # speaks, so nothing here converts to seconds and back.
    if fade.get("in"):
        parts.append(f"fade=t=in:start_frame=0:nb_frames={int(fade['in']['frames'])}{a}")
    if fade.get("out"):
        # 🔴 `len - 1 - n`, NOT `len - n`. ffmpeg's ramp is full at start_frame and reaches zero at
        # start_frame + nb_frames, so starting at `len - n` reaches zero one frame AFTER the clip —
        # the last frame never fades out. Premiere's proven keyframes put 100% at last - n and 0%
        # ON the last frame; this matches them. The one-frame disagreement survived every check
        # here until the CapCut probe measured both backends' renders frame by frame against one
        # expected ramp.
        n = int(fade["out"]["frames"])
        parts.append(f"fade=t=out:start_frame={int(node['len']) - 1 - n}:nb_frames={n}{a}")
    return ",".join(parts)


# --------------------------------------------------------------------------------------------
# building the pieces
# --------------------------------------------------------------------------------------------

class Assembler:
    def __init__(self, plan: dict, out: str, work: str, verbose: bool = True):
        self.plan = plan
        self.out = out
        self.work = work
        self.verbose = verbose
        self.fps = plan["fps"]
        self.raster = plan["timeline_raster"]
        self.total = total_frames(plan)
        self.report: dict = {"segments": [], "tracks": [], "total_frames": self.total}

    def say(self, msg: str) -> None:
        if self.verbose:
            print("  " + msg)

    def path(self, *bits: str) -> str:
        return os.path.join(self.work, *bits)

    # -- one clip ---------------------------------------------------------------------------

    def segment(self, node: dict, plan: dict, out: str, alpha: bool) -> None:
        """Exactly `len` frames of one source, cropped and scaled, at the timeline raster.

        The source in-point is applied with `trim=start_frame/end_frame`, whose end is EXCLUSIVE —
        the same convention as Resolve's endFrame and Premiere's out point, so one plan means the
        same span in all three.
        """
        src = plan["media"][node["media"]]
        if not os.path.isfile(src):
            raise AssembleError(f"media {node['media']!r} is not on disk: {src}")
        start = int(node["src_in"])
        end = start + int(node["len"])

        chain = [f"trim=start_frame={start}:end_frame={end}", "setpts=PTS-STARTPTS"]
        if node.get("crop"):
            chain.append(crop_filter(node, plan))
        else:
            tw, th = self.raster
            chain.append(f"scale={int(tw)}:{int(th)}:flags=lanczos,setsar=1")
        if alpha:
            chain.append("format=yuva444p10le")
        chain.append(UNIFORM_TAGS)
        f = fade_filter(node, alpha)
        if f:
            chain.append(f)

        # A STILL image is one frame. Without `-loop 1` a 120-frame title from a PNG comes out as
        # 1 frame — caught by the assertion below, which is the only reason it was ever noticed.
        loop = ["-loop", "1", "-framerate", "%d/%d" % (int(self.fps[0]), int(self.fps[1]))] \
            if os.path.splitext(src)[1].lower() in STILL_EXT else []
        run([_tool("ffmpeg"), "-nostdin", "-v", "error", "-y", *loop, "-i", src,
             "-vf", ",".join(chain), "-an",
             *(PRORES_ALPHA if alpha else PRORES), *rate_args(self.fps),
             "-fps_mode", "cfr", "-frames:v", str(int(node["len"])), out],
            f"segment for {node['media']}")
        assert_frames(out, int(node["len"]), f"segment {os.path.basename(out)}")

    def filler(self, frames: int, out: str, alpha: bool) -> None:
        """A gap in the plan is BLACK (or transparent on an overlay), and it is explicit.

        ⚠️ Not optional. Concatenating only the clips would close every gap by sliding everything
        after it earlier — a timeline that still tiles perfectly and is wrong from the first gap on.
        """
        tw, th = self.raster
        # 🔴 TRANSPARENT FILLER NEEDS THE ALPHA FORCED, not merely requested.
        #
        # An alpha-suffixed colour (`black` joined to its opacity with an "at" sign) does NOT survive
        # `format=yuva...`: the colour source hands over a frame with no alpha and the format filter
        # fills it OPAQUE. Measured — alpha read 3760 of 4095 both with the suffix form and with
        # 0xRRGGBBAA, so a "transparent" gap on an overlay track covered the picture in black.
        # `colorchannelmixer=aa=0` actually zeroes it (alpha reads 256, the floor).
        #
        # ⚠️ This went unnoticed because no check exercised it: the first overlay test had a
        # full-length overlay, so it never needed filler. `_check_concat_boundary` does.
        #
        # 0xRRGGBB rather than a named colour with an opacity suffix, because that syntax uses an
        # "at" sign, which scrub_check reads as an email address — and the denylist should keep its
        # teeth rather than gain an exemption.
        chain = ("format=yuva444p10le,colorchannelmixer=aa=0" if alpha else "format=yuv422p10le")
        chain += "," + UNIFORM_TAGS
        run([_tool("ffmpeg"), "-nostdin", "-v", "error", "-y",
             "-f", "lavfi", "-i",
             f"color=c=0x000000:s={int(tw)}x{int(th)}:r={int(self.fps[0])}/{int(self.fps[1])}",
             "-vf", chain,
             *(PRORES_ALPHA if alpha else PRORES), *rate_args(self.fps),
             "-fps_mode", "cfr", "-frames:v", str(int(frames)), out],
            f"{frames}-frame filler")
        assert_frames(out, int(frames), f"filler {os.path.basename(out)}")

    # -- one track -------------------------------------------------------------------------

    def build_track(self, track: dict, alpha: bool) -> str:
        """One video track as a single full-length file: segments and fillers, in order."""
        nodes = sorted(track["nodes"], key=lambda n: int(n["rec"]))
        pieces: list[tuple[str, int]] = []
        cursor = 0
        tag = str(track["id"]).replace(os.sep, "_")

        for i, node in enumerate(nodes):
            rec, ln = int(node["rec"]), int(node["len"])
            if rec < cursor:
                raise AssembleError(
                    f"track {track['id']} node {i} starts at frame {rec}, inside the previous clip "
                    f"which ends at {cursor}. Overlapping clips need a transition, and "
                    f"`transition` is False for this backend — the gate should have refused this.")
            if rec > cursor:
                p = self.path(f"{tag}_gap{i}.mov")
                self.filler(rec - cursor, p, alpha)
                pieces.append((p, rec - cursor))
            if node.get("disabled"):
                # 🔴 A disabled clip is not skipped — it becomes its own length of filler, or every
                # clip after it moves earlier. "Disabled" means invisible, not absent.
                p = self.path(f"{tag}_off{i}.mov")
                self.filler(ln, p, alpha)
                pieces.append((p, ln))
                self.report["segments"].append({"where": f"{track['id']}#{i}", "disabled": True,
                                                "frames": ln})
            else:
                p = self.path(f"{tag}_{i}.mov")
                self.segment(node, self.plan, p, alpha)
                pieces.append((p, ln))
                self.report["segments"].append({"where": f"{track['id']}#{i}", "frames": ln,
                                                "rec": rec, "src_in": int(node["src_in"]),
                                                "cropped": bool(node.get("crop"))})
            cursor = rec + ln

        if cursor < self.total:
            p = self.path(f"{tag}_tail.mov")
            self.filler(self.total - cursor, p, alpha)
            pieces.append((p, self.total - cursor))
            cursor = self.total
        if cursor > self.total:
            raise AssembleError(f"track {track['id']} runs to frame {cursor}, past the programme's "
                                f"{self.total}. The plan disagrees with itself.")

        out = self.path(f"{tag}_track.mov")
        self.concat([p for p, _ in pieces], out, alpha, self.total)
        got = assert_frames(out, self.total, f"track {track['id']}")
        self.say(f"track {track['id']:<4} {got['frames']} frames  "
                 f"{got['width']}x{got['height']}  {len(pieces)} piece(s)")
        self.report["tracks"].append({"id": track["id"], "index": track["index"],
                                      "frames": got["frames"], "pieces": len(pieces),
                                      "alpha": alpha})
        return out

    def concat(self, parts: list[str], out: str, alpha: bool, frames: int) -> None:
        """Concat through a list file, RE-STAMPING the timestamps as it goes.

        Paths are quoted for the concat demuxer, which is its own small syntax — a Windows path or
        anything with a space breaks an unquoted list, and the failure looks like a missing file.

        🔴 NOT a stream copy, and that is the whole point.
        `-c copy` is faster and leaves the timestamps DISCONTINUOUS at every segment boundary, which
        poisons everything downstream: `overlay` aligns its inputs by timestamp, and an encoder in
        constant-frame-rate mode then drops a frame at a boundary. Measured against a shipped master,
        that made the intro card 93 frames instead of 94 and put everything after it one frame early
        — with the total still exactly 2228 and the file playing perfectly. Re-encoding in CFR mode
        rewrites the timestamps as evenly spaced, which is what every later stage assumes.

        ⚠️ TWO WRONG TURNS, both caught by the assertion at the call site, both worth recording
        because each looked like the obvious fix:
          1. re-stamping at the OVERLAY stage instead — the concat output itself stayed inconsistent
             and the composite came out 2020 frames of 2228;
          2. adding `setpts=N/FRAME_RATE/TB` here. It reads like the careful thing to do and it
             DROPS FRAMES: measured on the same four segments, `-fps_mode cfr` alone gave 2228 and
             the identical command plus that setpts gave 2020. CFR mode already rewrites the
             timestamps; telling it what they should be as well makes it discard frames whose new
             PTS collide with its own schedule.
        """
        lst = out + ".txt"
        with open(lst, "w", encoding="utf-8") as fh:
            for p in parts:
                fh.write("file '%s'\n" % os.path.abspath(p).replace("'", r"'\''"))
        run([_tool("ffmpeg"), "-nostdin", "-v", "error", "-y",
             "-f", "concat", "-safe", "0", "-i", lst,
             *(PRORES_ALPHA if alpha else PRORES), *rate_args(self.fps),
             "-fps_mode", "cfr", "-frames:v", str(int(frames)), out],
            f"concat into {os.path.basename(out)}")

    # -- audio -----------------------------------------------------------------------------

    def build_audio(self) -> str | None:
        tracks = audio_tracks(self.plan)
        if not tracks:
            return None
        seconds = self.total * int(self.fps[1]) / int(self.fps[0])
        mixes = []
        for track in tracks:
            for i, node in enumerate(track["nodes"]):
                src = self.plan["media"][node["media"]]
                if not os.path.isfile(src):
                    raise AssembleError(f"audio media {node['media']!r} is not on disk: {src}")
                out = self.path(f"a_{track['id']}_{i}.wav")
                fr = int(self.fps[0]) / int(self.fps[1])
                ss = int(node["src_in"]) / fr
                dur = int(node["len"]) / fr
                delay = int(round(int(node["rec"]) / fr * 1000))
                run([_tool("ffmpeg"), "-nostdin", "-v", "error", "-y",
                     "-ss", f"{ss:.6f}", "-i", src, "-t", f"{dur:.6f}",
                     "-af", f"adelay={delay}:all=1,apad",
                     "-c:a", "pcm_s24le", "-ar", "48000", "-t", f"{seconds:.6f}", out],
                    f"audio segment {track['id']}#{i}")
                mixes.append(out)

        if len(mixes) == 1:
            return mixes[0]
        out = self.path("audio_mix.wav")
        args = [_tool("ffmpeg"), "-nostdin", "-v", "error", "-y"]
        for m in mixes:
            args += ["-i", m]
        # 🔴 `normalize=0`: amix's default divides by the number of inputs, which would quietly
        # attenuate a mastered mix by 6 dB per extra track and measure as a quiet programme.
        args += ["-filter_complex", f"amix=inputs={len(mixes)}:normalize=0",
                 "-c:a", "pcm_s24le", "-ar", "48000", "-t", f"{seconds:.6f}", out]
        run(args, "audio mix")
        return out

    # -- the whole thing -------------------------------------------------------------------

    def build(self) -> dict:
        vts = video_tracks(self.plan)
        if not vts:
            raise AssembleError("the plan has no video track")

        built = []
        for n, t in enumerate(vts):
            built.append(self.build_track(t, alpha=(n > 0)))

        picture = built[0]
        if len(built) > 1:
            # Overlay upward, bottom track first, so track order is the plan's order.
            args = [_tool("ffmpeg"), "-nostdin", "-v", "error", "-y"]
            for b in built:
                args += ["-i", b]
            # The track files already carry clean sequential timestamps — `concat()` re-stamps them
            # — so overlay can align them by timestamp safely. That is why the fix lives there and
            # not here: re-stamping at this stage instead left the concat output inconsistent and
            # produced 2020 frames of 2228.
            chain, prev = [], "0:v"
            for i in range(1, len(built)):
                label = f"v{i}"
                chain.append(f"[{prev}][{i}:v]overlay=0:0:format=auto:eof_action=pass[{label}]")
                prev = label
            picture = self.path("picture.mov")
            args += ["-filter_complex", ";".join(chain), "-map", f"[{prev}]",
                     *PRORES, *rate_args(self.fps), "-fps_mode", "cfr",
                     "-frames:v", str(self.total), picture]
            run(args, "overlay video tracks")
            assert_frames(picture, self.total, "composited picture")
            self.say(f"composited {len(built)} video track(s)")

        audio = self.build_audio()

        # --- the final encode.
        # ⚠️ Three scars here, all from concatenated ProRes carrying discontinuous timestamps:
        #   1. with variable frame mode the encoder DROPPED frames (1032 of 1043) while both inputs
        #      measured 1043 — hence forced CFR with an explicit rate;
        #   2. `-shortest` truncated an output by 2 frames — hence `-frames:v` from the plan;
        #   3. 🔴 and CFR ALONE IS NOT ENOUGH. A single frame went missing at the first concat
        #      boundary, so the intro card ran 93 frames instead of 94 and everything after it sat
        #      one frame early — with the total still exactly 2228 and the file playing perfectly.
        #      Nothing but a frame-by-frame comparison against a known-good master would show it.
        #      The fix is in `concat()`, which re-encodes in CFR mode so its output is evenly
        #      stamped. ⚠️ And NOT `setpts` — adding that on top of CFR mode drops frames
        #      (2020 of 2228, measured). CFR already rewrites the timestamps; it does not want help.
        spec = (self.plan.get("render") or {}).get("ffmpeg") or {}
        args = [_tool("ffmpeg"), "-nostdin", "-v", "error", "-y", "-i", picture]
        if audio:
            args += ["-i", audio]
        args += ["-map", "0:v"]
        if audio:
            args += ["-map", "1:a"]
        args += ["-fps_mode", "cfr", *rate_args(self.fps)]
        args += spec.get("video") or PRORES
        if audio:
            args += spec.get("audio") or ["-c:a", "pcm_s24le", "-ar", "48000"]
        args += ["-frames:v", str(self.total), self.out]
        run(args, "final encode")

        got = probe(self.out)
        self.report["output"] = {"path": self.out, **got}
        self.verify(got)
        return self.report

    def verify(self, got: dict) -> None:
        """Measure the deliverable against the plan. Nothing here trusts the encoder."""
        want_w, want_h = (int(x) for x in self.raster)
        if got["frames"] != self.total:
            raise AssembleError(f"the master has {got['frames']} frames, the plan says "
                                f"{self.total}. 'Finished' is not 'correct'.")
        if (got["width"], got["height"]) != (want_w, want_h):
            raise AssembleError(f"the master is {got['width']}x{got['height']}, the plan says "
                                f"{want_w}x{want_h}. " + _raster_why(got["width"], got["height"],
                                                                     want_w, want_h))
        want_rate = f"{int(self.fps[0])}/{int(self.fps[1])}"
        if got["rate"] not in (want_rate, _reduced(self.fps)):
            raise AssembleError(f"the master reports {got['rate']}, the plan says {want_rate}. "
                                f"29.97 delivered as 30.000 drifts a frame every 33 s and reads as "
                                f"a lip-sync fault, not a build error.")
        want_secs = self.total * int(self.fps[1]) / int(self.fps[0])
        if got["duration"] and abs(got["duration"] - want_secs) > 0.5:
            raise AssembleError(f"the master is {got['duration']:.3f}s long and the plan is "
                                f"{want_secs:.3f}s, which the frame count did not catch.")


def _raster_why(gw: int, gh: int, ww: int, wh: int) -> str:
    """Say WHICH raster fault it is. A message that calls a 4K export of a vertical programme
    'landscape' sends someone to fix the wrong setting."""
    if (gw > gh) != (ww > wh) and gw != gh and ww != wh:
        return ("It is the wrong ORIENTATION — a vertical programme delivered landscape, or the "
                "reverse — and it has every frame count right.")
    if gw * wh == gh * ww:
        return ("Same shape, %s: the export resolution is set to something other than the plan's "
                "(a 4K default, say). Re-export at %dx%d." % (
                    "%.2gx the size" % (gw / float(ww)), ww, wh))
    return ("A different SHAPE: something has been cropped, padded or letterboxed on the way out.")


def _reduced(fps) -> str:
    from math import gcd
    n, d = int(fps[0]), int(fps[1])
    g = gcd(n, d)
    return f"{n // g}/{d // g}"


# --------------------------------------------------------------------------------------------
# the entry point
# --------------------------------------------------------------------------------------------

def assemble(plan: dict, out: str, work: str | None = None, keep: bool = False,
             allow_unverified: bool = False, verbose: bool = True) -> dict:
    errs = nle_ir.validate(plan)
    errs += nle_ir.capability_gate(plan, BACKEND, allow_unverified=allow_unverified)
    if errs:
        raise AssembleError("the plan cannot be assembled by this backend:\n  - "
                            + "\n  - ".join(errs))
    tmp = work or tempfile.mkdtemp(prefix="firsttake-ffmpeg-")
    os.makedirs(tmp, exist_ok=True)
    try:
        a = Assembler(plan, out, tmp, verbose=verbose)
        return a.build()
    finally:
        if not keep and not work:
            shutil.rmtree(tmp, ignore_errors=True)
        elif verbose:
            print(f"  intermediates kept in {tmp}")


# --------------------------------------------------------------------------------------------
# --selfcheck — build real files and MEASURE them. The only way to trust this backend.
# --------------------------------------------------------------------------------------------
#
# 🔴 Three separate assemblies, on purpose. An earlier lesson in this repo: an assertion that cannot
# tell two cases apart is not verifying either. A single timeline carrying placement, a crop and an
# overlay would confound all three in one luma measurement, so each gets its own plan and its own
# unconfounded question.

SELF_W, SELF_H = 320, 180
SELF_FPS = [30000, 1001]

# Frame identity encoded as LUMA: clip i, source frame N -> 24 + 70*i + 1.5*N. Non-overlapping
# ranges per clip (24..82, 94..152, 164..222) so a swapped clip is a different value, not a near one.
def _self_luma(clip: int, frame: int) -> float:
    return 24 + 70 * clip + 1.5 * frame


def _make_self_media(d: str, frames: int = 60) -> dict:
    """Sources whose every frame says which clip and which frame it is."""
    media = {}
    for i in range(3):
        p = os.path.join(d, f"src{i}.mov")
        run([_tool("ffmpeg"), "-nostdin", "-v", "error", "-y",
             "-f", "lavfi", "-i",
             f"color=c=black:s={SELF_W}x{SELF_H}:r={SELF_FPS[0]}/{SELF_FPS[1]}",
             "-vf", f"geq=lum='{24 + 70 * i}+1.5*N':cb=128:cr=128,format=yuv420p",
             "-c:v", "ffv1", "-frames:v", str(frames), p], f"selfcheck source {i}")
        assert_frames(p, frames, f"selfcheck source {i}")
        media[f"c{i}"] = p
    # a horizontal gradient, for the crop question
    g = os.path.join(d, "grad.mov")
    run([_tool("ffmpeg"), "-nostdin", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"color=c=black:s={SELF_W}x{SELF_H}:r={SELF_FPS[0]}/{SELF_FPS[1]}",
         "-vf", f"geq=lum='16+219*X/({SELF_W}-1)':cb=128:cr=128,format=yuv420p",
         "-c:v", "ffv1", "-frames:v", str(frames), g], "selfcheck gradient")
    media["grad"] = g
    tone = os.path.join(d, "tone.wav")
    run([_tool("ffmpeg"), "-nostdin", "-v", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
         "-c:a", "pcm_s24le", tone], "selfcheck tone")
    media["tone"] = tone
    return media


# Lossless 8-bit output, so a measured luma is directly the number the source encoded — no
# 8-to-10-bit calibration standing between the assertion and the answer.
def _self_render(out: str) -> dict:
    return {"output": out,
            "ffmpeg": {"video": ["-c:v", "ffv1", "-pix_fmt", "yuv420p"],
                       "audio": ["-c:a", "pcm_s24le", "-ar", "48000"]}}


def _check_placement(media: dict, work: str) -> list[str]:
    """Is the right SOURCE frame at the right TIMELINE frame? With a gap and a disabled clip."""
    problems = []
    # c0[10:50] at 0 | gap 0..10 | c1[0:40] at 50 | c2[20:60] at 100, DISABLED
    plan = {
        "name": "selfcheck-placement", "fps": SELF_FPS, "timeline_raster": [SELF_W, SELF_H],
        "media": {k: media[k] for k in ("c0", "c1", "c2", "tone")},
        "baked": [{"what": "nothing", "why": "synthetic; this checks placement"}],
        "render": _self_render(os.path.join(work, "placement.mkv")),
        "tracks": [
            {"id": "V1", "kind": "video", "index": 1, "nodes": [
                {"kind": "clip", "media": "c0", "rec": 0, "len": 40, "src_in": 10},
                {"kind": "clip", "media": "c1", "rec": 50, "len": 40, "src_in": 0},
                {"kind": "clip", "media": "c2", "rec": 100, "len": 40, "src_in": 20,
                 "disabled": True},
            ]},
            {"id": "A1", "kind": "audio", "index": 1, "nodes": [
                {"kind": "clip", "media": "tone", "rec": 0, "len": 140, "src_in": 0},
            ]},
        ],
    }
    out = os.path.join(work, "placement.mkv")
    rep = assemble(plan, out, work=os.path.join(work, "w1"), keep=True,
                   allow_unverified=True, verbose=False)
    if rep["output"]["frames"] != 140:
        problems.append(f"placement: {rep['output']['frames']} frames, expected 140")

    vals = luma_per_frame(out, 0, 139)
    if len(vals) != 140:
        return problems + [f"placement: measured {len(vals)} frames of luma, expected 140"]

    # what each timeline frame MUST look like
    expect: list[tuple[str, float | None]] = []
    for t in range(140):
        if t < 40:
            expect.append(("c0", _self_luma(0, 10 + t)))
        elif t < 50:
            expect.append(("gap", None))
        elif t < 90:
            expect.append(("c1", _self_luma(1, t - 50)))
        else:
            expect.append(("off", None))          # gap + disabled clip are both black

    bad = []
    for t, (what, want) in enumerate(expect):
        got = vals[t]
        if want is None:
            if got > 22:                       # every clip's luma starts at 24 or above
                bad.append(f"frame {t} ({what}) should be black and reads {got:.1f}")
        elif abs(got - want) > 1.0:
            bad.append(f"frame {t} ({what}) should read {want:.1f} and reads {got:.1f}")
    if bad:
        problems.append("placement: " + "; ".join(bad[:6])
                        + (f" (+{len(bad) - 6} more)" if len(bad) > 6 else ""))
    return problems


def _check_crop(media: dict, work: str) -> list[str]:
    """Did the crop take the rectangle the plan named?

    Measured against ffmpeg cropping the same rectangle directly — so what this proves is that the
    assembler passed the RIGHT rectangle, which is the part that can be wrong. A horizontal gradient
    makes the mean luma a function of WHICH band was taken, so a crop offset by a few pixels moves
    the number.
    """
    problems = []
    x0, cw = 40, 160
    ch = int(round(cw * SELF_H / SELF_W))          # keep the timeline's aspect
    plan = {
        "name": "selfcheck-crop", "fps": SELF_FPS, "timeline_raster": [SELF_W, SELF_H],
        "media": {"grad": media["grad"]},
        "baked": [{"what": "nothing", "why": "synthetic; this checks the crop rectangle"}],
        "render": _self_render(os.path.join(work, "crop.mkv")),
        "tracks": [{"id": "V1", "kind": "video", "index": 1, "nodes": [
            {"kind": "clip", "media": "grad", "rec": 0, "len": 20, "src_in": 0,
             "src_raster": [SELF_W, SELF_H], "crop": [x0, 0, cw, ch]},
        ]}],
    }
    out = os.path.join(work, "crop.mkv")
    assemble(plan, out, work=os.path.join(work, "w2"), keep=True, allow_unverified=True,
             verbose=False)

    ref = os.path.join(work, "crop_ref.mkv")
    run([_tool("ffmpeg"), "-nostdin", "-v", "error", "-y", "-i", media["grad"],
         "-vf", f"crop={cw}:{ch}:{x0}:0,scale={SELF_W}:{SELF_H}:flags=lanczos,format=yuv420p",
         "-c:v", "ffv1", "-frames:v", "20", ref], "crop reference")

    got = luma_per_frame(out, 2, 6)
    want = luma_per_frame(ref, 2, 6)
    if not got or not want:
        return ["crop: could not measure luma"]
    dg, dw = sum(got) / len(got), sum(want) / len(want)
    if abs(dg - dw) > 1.0:
        problems.append(f"crop: assembled band reads {dg:.1f}, the same rectangle cropped "
                        f"directly reads {dw:.1f}")
    # and it must NOT match the uncropped frame, or the assertion proves nothing
    full = luma_per_frame(media["grad"], 2, 6)
    if full and abs(dg - sum(full) / len(full)) < 1.0:
        problems.append(f"crop: the result matches the UNCROPPED source ({dg:.1f}), so no crop "
                        f"was applied and the check above would have passed anyway")
    return problems


def _check_overlay_fade(media: dict, work: str) -> list[str]:
    """An overlay track above the base, with an alpha fade in. Proves compositing and the ramp."""
    problems = []
    plan = {
        "name": "selfcheck-overlay", "fps": SELF_FPS, "timeline_raster": [SELF_W, SELF_H],
        "media": {"c0": media["c0"], "c2": media["c2"]},
        "baked": [{"what": "nothing", "why": "synthetic; this checks overlay + fade"}],
        "render": _self_render(os.path.join(work, "overlay.mkv")),
        "tracks": [
            {"id": "V1", "kind": "video", "index": 1, "nodes": [
                {"kind": "clip", "media": "c0", "rec": 0, "len": 40, "src_in": 0}]},
            {"id": "V2", "kind": "video", "index": 2, "nodes": [
                {"kind": "clip", "media": "c2", "rec": 0, "len": 40, "src_in": 0,
                 "fade": {"in": {"frames": 20, "shape": "linear"},
                          "out": {"frames": 10, "shape": "linear"}}}]},
        ],
    }
    out = os.path.join(work, "overlay.mkv")
    assemble(plan, out, work=os.path.join(work, "w3"), keep=True, allow_unverified=True,
             verbose=False)
    vals = luma_per_frame(out, 0, 39)
    if len(vals) < 40:
        return [f"overlay: measured {len(vals)} frames, expected 40"]

    # frame 0 is all base (alpha 0); by frame 20 the overlay is full. c2 is much brighter than c0,
    # so the composite must RISE across the fade and then track c2.
    base0 = _self_luma(0, 0)
    top20 = _self_luma(2, 20)
    if abs(vals[0] - base0) > 2.0:
        problems.append(f"overlay: frame 0 should be the base alone ({base0:.1f}) and reads "
                        f"{vals[0]:.1f} — the fade did not start at zero alpha")
    # The fade out must be COMPLETE on the clip's last frame — the base alone — and still full at
    # last - n. Off by one, the last frame keeps a sliver of overlay and nothing else looks wrong.
    if abs(vals[39] - _self_luma(0, 39)) > 2.0:
        problems.append(f"overlay: frame 39, the last, should be the base alone "
                        f"({_self_luma(0, 39):.1f}) and reads {vals[39]:.1f} — the fade out ends "
                        f"after the clip does")
    if abs(vals[29] - _self_luma(2, 29)) > 3.0:
        problems.append(f"overlay: frame 29 (last - 10) should still be the overlay alone "
                        f"({_self_luma(2, 29):.1f}) and reads {vals[29]:.1f}")
    if abs(vals[25] - _self_luma(2, 25)) > 3.0:
        problems.append(f"overlay: frame 25 should be the overlay alone "
                        f"({_self_luma(2, 25):.1f}) and reads {vals[25]:.1f}")
    rising = [vals[i] < vals[i + 1] + 0.01 for i in range(0, 19)]
    if not all(rising):
        problems.append("overlay: the fade is not monotonic across its 20 frames, so it is not a "
                        f"ramp: {[round(v, 1) for v in vals[0:20]]}")
    if abs(vals[10] - base0) < 2.0 or abs(vals[10] - top20) < 2.0:
        problems.append(f"overlay: frame 10 reads {vals[10]:.1f}, which is one of the two ends — a "
                        f"fade that jumps is not a fade")
    return problems


def _check_concat_boundary(media: dict, work: str) -> list[str]:
    """Does a clip boundary land on the exact frame, when the track has been CONCATENATED and then
    consumed by the overlay stage?

    🔴 This check exists because a bug got past the other three. A stream-copy concat leaves the
    timestamps discontinuous at every segment boundary, and `overlay` aligns its inputs BY
    TIMESTAMP — so one frame vanished at the first boundary and everything after it sat a frame
    early. The total was still exactly right and the file played perfectly; only a frame-by-frame
    comparison against a shipped master showed it.

    The earlier overlay check could not see it: its base track was a single segment, so there was no
    boundary to misalign. This one has two clips on the base AND an overlay above it, which is the
    combination that fails.
    """
    problems = []
    plan = {
        "name": "selfcheck-boundary", "fps": SELF_FPS, "timeline_raster": [SELF_W, SELF_H],
        "media": {"c0": media["c0"], "c1": media["c1"], "c2": media["c2"]},
        "baked": [{"what": "nothing", "why": "synthetic; this checks concat boundary alignment"}],
        "render": _self_render(os.path.join(work, "boundary.mkv")),
        "tracks": [
            # two clips on the base -> a real concat boundary at frame 20
            {"id": "V1", "kind": "video", "index": 1, "nodes": [
                {"kind": "clip", "media": "c0", "rec": 0, "len": 20, "src_in": 0},
                {"kind": "clip", "media": "c1", "rec": 20, "len": 20, "src_in": 0}]},
            # an overlay that stops early, so frames 10..39 show the base alone and the boundary is
            # measurable — while the overlay stage has still done its timestamp-sensitive work
            {"id": "V2", "kind": "video", "index": 2, "nodes": [
                {"kind": "clip", "media": "c2", "rec": 0, "len": 10, "src_in": 0}]},
        ],
    }
    out = os.path.join(work, "boundary.mkv")
    assemble(plan, out, work=os.path.join(work, "w4"), keep=True, allow_unverified=True,
             verbose=False)
    vals = luma_per_frame(out, 10, 39)
    if len(vals) < 30:
        return [f"boundary: measured {len(vals)} frames, expected 30"]

    bad = []
    for i, v in enumerate(vals):
        t = 10 + i
        want = _self_luma(0, t) if t < 20 else _self_luma(1, t - 20)
        if abs(v - want) > 1.0:
            bad.append(f"frame {t} should read {want:.1f} and reads {v:.1f}")
    if bad:
        problems.append("boundary: " + "; ".join(bad[:4])
                        + (f" (+{len(bad) - 4} more)" if len(bad) > 4 else "")
                        + " — a frame lost at the concat boundary shifts everything after it")
    return problems


def _check_mixed_tags(media: dict, work: str) -> list[str]:
    """Three video tracks, and a COLOUR-TAGGED clip arriving after an untagged gap on the middle one.

    🔴 This is the bug the CapCut probe found here. ffmpeg REBUILDS a filter graph when a stream's
    colour properties change mid-stream, and inside a multi-input `overlay` that rebuild loses sync:
    from the boundary on, the top track vanished and the base froze or skipped a frame — different
    frames on different runs — with the total still exactly right. Every generated filler is
    untagged and real camera files are tagged, so ordinary plans hit it. The cure is in
    `UNIFORM_TAGS`; this check plants the boundary and measures every frame.
    """
    tagged = os.path.join(work, "src2_bt709.mov")
    run([_tool("ffmpeg"), "-nostdin", "-v", "error", "-y", "-i", media["c2"],
         "-vf", "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
         "-c:v", "ffv1", tagged], "tagged selfcheck source")
    plan = {
        "name": "selfcheck-mixed-tags", "fps": SELF_FPS, "timeline_raster": [SELF_W, SELF_H],
        "media": {"c0": media["c0"], "c1": media["c1"], "c2t": tagged},
        "baked": [{"what": "nothing", "why": "synthetic; this checks a colour-tag boundary"}],
        "render": _self_render(os.path.join(work, "mixed.mkv")),
        "tracks": [
            {"id": "V1", "kind": "video", "index": 1, "nodes": [
                {"kind": "clip", "media": "c0", "rec": 0, "len": 50, "src_in": 0}]},
            {"id": "V2", "kind": "video", "index": 2, "nodes": [
                {"kind": "clip", "media": "c2t", "rec": 10, "len": 10, "src_in": 0}]},
            {"id": "V3", "kind": "video", "index": 3, "nodes": [
                {"kind": "clip", "media": "c1", "rec": 30, "len": 10, "src_in": 0}]},
        ],
    }
    out = os.path.join(work, "mixed.mkv")
    assemble(plan, out, work=os.path.join(work, "w5"), keep=True, allow_unverified=True,
             verbose=False)
    vals = luma_per_frame(out, 0, 49)
    if len(vals) < 50:
        return [f"mixed tags: measured {len(vals)} frames, expected 50"]
    bad = []
    for t, v in enumerate(vals):
        want = (_self_luma(2, t - 10) if 10 <= t < 20 else
                _self_luma(1, t - 30) if 30 <= t < 40 else _self_luma(0, t))
        if abs(v - want) > 1.0:
            bad.append(f"frame {t} should read {want:.1f} and reads {v:.1f}")
    if bad:
        return ["mixed tags: " + "; ".join(bad[:4]) + (f" (+{len(bad) - 4} more)" if len(bad) > 4
                                                      else "")
                + " — a colour-tag change mid-track broke the overlay stage"]
    return []


def selfcheck() -> int:
    """Generate media, assemble three plans, and measure the results."""
    print("assemble_ffmpeg selfcheck — builds real files and measures them\n")
    work = tempfile.mkdtemp(prefix="firsttake-ffmpeg-selfcheck-")
    try:
        print("  generating sources whose luma encodes clip and frame number...")
        media = _make_self_media(work)
        problems: list[str] = []
        for label, fn in (("placement (gap, disabled clip, exact source frames)", _check_placement),
                          ("crop rectangle", _check_crop),
                          ("overlay track + alpha fade", _check_overlay_fade),
                          ("concat boundary alignment", _check_concat_boundary),
                          ("colour-tag boundary, three tracks", _check_mixed_tags)):
            print(f"  checking {label} ...", end=" ", flush=True)
            try:
                found = fn(media, work)
            except AssembleError as e:
                found = [f"{label}: {e}"]
            print("ok" if not found else "FAIL")
            problems += found
        print()
        if problems:
            print(f"🔴 {len(problems)} problem(s):")
            for p in problems:
                print("  - " + p)
            return 1
        print("✅ every frame measured where the plan says it should be.")
        print("   Promote these in nle_ir.CAPABILITIES['ffmpeg']: clip, crop, fade_linear, "
              "alpha_track, clip_disable, render.")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --------------------------------------------------------------------------------------------
# --selftest — the logic that decides WHAT to run, with no ffmpeg and no media
# --------------------------------------------------------------------------------------------

def _example_plan() -> dict:
    return {
        "name": "ffmpeg-selftest", "fps": [30000, 1001], "timeline_raster": [1080, 1920],
        "media": {"a": "/tmp/a.mov", "b": "/tmp/b.mov"},
        "baked": [{"what": "nothing", "why": "selftest"}],
        "tracks": [{"id": "V1", "kind": "video", "index": 1, "nodes": [
            {"kind": "clip", "media": "a", "rec": 0, "len": 90, "src_in": 0},
            {"kind": "clip", "media": "b", "rec": 120, "len": 60, "src_in": 10,
             "src_raster": [3840, 2160], "crop": [0, 0, 1215, 2160]},
        ]}],
    }


def selftest() -> int:
    failures = []

    def check(name, cond, detail=""):
        if not cond:
            failures.append(name)
        print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"   {detail}" if detail and not cond
                                                          else ""))

    plan = _example_plan()
    check("the example plan validates", not nle_ir.validate(plan), str(nle_ir.validate(plan)))

    # --- length comes from the plan, and a gap counts toward it
    check("total_frames spans the gap", total_frames(plan) == 180, str(total_frames(plan)))
    p2 = json.loads(json.dumps(plan))
    p2["render"] = {"frames": 200}
    check("an explicit render length wins", total_frames(p2) == 200)

    # --- crop: the rectangle is passed straight through, and fractions are REFUSED
    f = crop_filter(plan["tracks"][0]["nodes"][1], plan)
    check("crop names the plan's rectangle", "crop=1215:2160:0:0" in f, f)
    check("crop then scales to the timeline raster", "scale=1080:1920" in f, f)
    check("crop resets the sample aspect", "setsar=1" in f, f)
    frac = json.loads(json.dumps(plan))
    frac["tracks"][0]["nodes"][1]["crop"] = [0, 0, 1215.5, 2160]
    try:
        crop_filter(frac["tracks"][0]["nodes"][1], frac)
        check("a fractional crop is refused", False, "it was rounded silently")
    except AssembleError as e:
        check("a fractional crop is refused", "whole pixels" in str(e))

    # --- a raster fault names the right cause
    check("a 2x export is the wrong SIZE, not landscape",
          "Same shape" in _raster_why(2160, 3840, 1080, 1920), _raster_why(2160, 3840, 1080, 1920))
    check("a turned export is the wrong ORIENTATION",
          "ORIENTATION" in _raster_why(1920, 1080, 1080, 1920), _raster_why(1920, 1080, 1080, 1920))
    check("a letterboxed export is a different SHAPE",
          "SHAPE" in _raster_why(1080, 1350, 1080, 1920), _raster_why(1080, 1350, 1080, 1920))

    # --- fades: frames, both ends, and alpha only where there is something behind
    node = {"len": 90, "fade": {"in": {"frames": 12}, "out": {"frames": 18}}}
    base = fade_filter(node, alpha=False)
    over = fade_filter(node, alpha=True)
    check("a fade in starts at frame 0", "fade=t=in:start_frame=0:nb_frames=12" in base, base)
    check("a fade out starts at len - 1 - frames, so it ends ON the last frame",
          "fade=t=out:start_frame=71:nb_frames=18" in base, base)
    # 🔴 the units, asserted directly. `st`/`d` are SECONDS, and passing frame counts to them made a
    # 20-frame fade a 20-SECOND one — invisible in the render and invisible to a string check that
    # only looked for the number.
    check("fades are expressed in FRAMES, never seconds",
          ":st=" not in base and ":d=" not in base and ":st=" not in over and ":d=" not in over,
          f"seconds-based fade: {base} / {over}")
    check("an overlay fade ramps ALPHA", ":alpha=1" in over, over)
    check("a base-track fade does NOT touch alpha", ":alpha=1" not in base,
          "there is nothing behind the bottom track, so its fade is to black")
    check("no fade means no filter", fade_filter({"len": 9}, False) == "")

    # --- the gate must refuse what this backend cannot do, by name
    for feature, key, value in (("cdl", "grade", {"cdl": {"slope": [1, 1, 1]}}),
                                ("lut", "grade", {"lut": "/tmp/x.cube"}),
                                ("transition", "transition", {"in": {"frames": 12}}),
                                ("keyframes", "keyframes", [{"param": "Scale", "points": []}]),
                                ("speed", "speed", 0.5)):
        bad = json.loads(json.dumps(plan))
        bad["tracks"][0]["nodes"][0][key] = value
        errs = nle_ir.capability_gate(bad, BACKEND, allow_unverified=True)
        check(f"the gate refuses {feature}", any(feature in e for e in errs), str(errs))

    shaped = json.loads(json.dumps(plan))
    shaped["tracks"][0]["nodes"][0]["fade"] = {"in": {"frames": 12, "shape": "ease"}}
    errs = nle_ir.capability_gate(shaped, BACKEND, allow_unverified=True)
    check("the gate refuses a SHAPED fade", any("fade_shaped" in e for e in errs), str(errs))
    linear = json.loads(json.dumps(plan))
    linear["tracks"][0]["nodes"][0]["fade"] = {"in": {"frames": 12, "shape": "linear"}}
    errs = nle_ir.capability_gate(linear, BACKEND, allow_unverified=True)
    check("...and allows a linear one", not errs, str(errs))

    # 🔴 UNVERIFIED must block by default, or "not proven" quietly becomes "fine".
    #
    # ⚠️ Asserted against a TEMPORARY backend, not against the real table. The first version of this
    # check asked the real 'ffmpeg' entry — and passed only while something was still unproven, so
    # promoting the last capability broke the test that guards promotion. A selftest that stops
    # working when the code gets better was testing the wrong thing.
    nle_ir.CAPABILITIES["_selftest_tmp"] = dict(nle_ir.CAPABILITIES[BACKEND])
    nle_ir.CAPABILITIES["_selftest_tmp"]["clip"] = "UNVERIFIED"
    try:
        blocked = nle_ir.capability_gate(plan, "_selftest_tmp", allow_unverified=False)
        allowed = nle_ir.capability_gate(plan, "_selftest_tmp", allow_unverified=True)
        check("UNVERIFIED blocks by default", bool(blocked), str(blocked))
        check("...and --allow-unverified lets it through", not allowed, str(allowed))
    finally:
        del nle_ir.CAPABILITIES["_selftest_tmp"]

    # and the real table must have nothing unproven left for this backend
    unproven = [k for k, v in nle_ir.CAPABILITIES[BACKEND].items() if v not in (True, False)]
    check("nothing is left UNVERIFIED for ffmpeg", not unproven, str(unproven))

    # --- overlapping clips have no transition support, so they must be caught not composited
    over_plan = json.loads(json.dumps(plan))
    over_plan["tracks"][0]["nodes"][1]["rec"] = 80          # inside node 0, which ends at 90
    check("the IR itself flags an overlap", bool(nle_ir.validate(over_plan)),
          "an overlap with no transition would silently drop frames")

    # --- track order: bottom first, so overlays composite upward
    multi = json.loads(json.dumps(plan))
    multi["tracks"].append({"id": "V2", "kind": "video", "index": 2, "nodes": [
        {"kind": "clip", "media": "a", "rec": 0, "len": 180, "src_in": 0}]})
    multi["tracks"].insert(0, {"id": "V3", "kind": "video", "index": 3, "nodes": [
        {"kind": "clip", "media": "a", "rec": 0, "len": 180, "src_in": 0}]})
    order = [t["id"] for t in video_tracks(multi)]
    check("video tracks come back bottom-first", order == ["V1", "V2", "V3"], str(order))

    # --- rate is expressed exactly, never as a float
    check("the rate is a rational", rate_args([30000, 1001]) == ["-r", "30000/1001"])
    check("29.97 reduces correctly for comparison", _reduced([30000, 1001]) == "30000/1001")
    check("30000/1000 reduces to 30/1", _reduced([30000, 1000]) == "30/1")

    # --- the concat list must survive a path with a space or a quote
    import tempfile as _tf
    with _tf.TemporaryDirectory() as d:
        a = Assembler(plan, os.path.join(d, "out.mov"), d, verbose=False)
        target = os.path.join(d, "a b.mov")
        lst = os.path.join(d, "list.mov.txt")
        with open(lst, "w", encoding="utf-8") as fh:
            fh.write("file '%s'\n" % os.path.abspath(target).replace("'", r"'\''"))
        body = open(lst, encoding="utf-8").read()
        check("a path with a space is quoted in the concat list", "'" in body and "a b.mov" in body)
        check("the assembler knows the programme length", a.total == 180)

    print(f"\n{'PASS' if not failures else 'FAIL'}: {len(failures)} failure(s)")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--plan")
    ap.add_argument("--out")
    ap.add_argument("--work", help="where to put intermediates (implies --keep-work)")
    ap.add_argument("--keep-work", action="store_true")
    ap.add_argument("--allow-unverified", action="store_true")
    ap.add_argument("--selfcheck", action="store_true",
                    help="generate media, assemble, and measure the result")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if a.selfcheck:
        return selfcheck()
    if not a.plan:
        ap.error("--plan is required")

    with open(a.plan, encoding="utf-8") as fh:
        plan = json.load(fh)
    # The plan may name its own deliverable; --out overrides it. One of them must say.
    out = a.out or ((plan.get("render") or {}).get("output") or "")
    if not out:
        ap.error("no output: pass --out, or give the plan a render.output")
    a.out = out
    try:
        rep = assemble(plan, a.out, work=a.work, keep=a.keep_work,
                       allow_unverified=a.allow_unverified)
    except AssembleError as e:
        print(f"\n🔴 ASSEMBLY FAILED\n{e}")
        return 1
    o = rep["output"]
    print(f"\n✅ {o['path']}")
    print(f"   {o['frames']} frames, {o['width']}x{o['height']}, {o['rate']}, "
          f"{o['duration']:.3f}s, {o['pix_fmt']}")
    print("   Measured, not assumed. This is a master, not a proxy — and there is no editable "
          "timeline: change the plan and re-assemble.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

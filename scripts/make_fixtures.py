#!/usr/bin/env python3
"""
Generate known-good and known-bad test fixtures for shootcheck.

Why this exists: a synthetic clip built with a mismatched codec / pixel-format
combination can produce a file that does not decode, which then looks exactly
like an analyser bug. So this script plants EXACT code values and EXACT audio
levels, and verify_fixtures.py then confirms (a) every fixture decodes and
(b) shootcheck reports the planted values and flags the planted faults.

Frames are built in numpy at real code values and piped to ffmpeg as raw
planar YUV, so "60 IRE" in a fixture is genuinely 60 IRE in the encoded file.

    ./make_fixtures.py            # writes ./fixtures/
    ./make_fixtures.py --fast     # 1280x720, much quicker, resolution checks
                                  # will (correctly) fail
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

import numpy as np

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
SR = 48000


# ---------------------------------------------------------------------------
# code-value helpers
# ---------------------------------------------------------------------------

def ire_to_code(ire: float, depth: int) -> float:
    """Legal-range IRE -> code value at the given bit depth."""
    black = 16 << (depth - 8)
    white = 235 << (depth - 8)
    return black + (ire / 100.0) * (white - black)


def make_frame(w: int, h: int, depth: int, *,
               base_ire: float, face_ire: float, noise: float,
               clip_frac: float = 0.0, crush_frac: float = 0.0,
               rng: np.random.Generator) -> np.ndarray:
    """
    Luma plane with a planted 'face' patch in the top-centre grid cell.

    noise is fine high-frequency texture, in normalised units — it is what the
    sharpness metric measures, so it is how a fixture is made 'in focus'.
    """
    maxv = (1 << depth) - 1
    y = np.full((h, w), ire_to_code(base_ire, depth) / maxv, dtype=np.float32)

    # 'face' occupies the top-centre cell of the 3x3 grid the analyser uses
    r0, r1 = 0, h // 3
    c0, c1 = w // 3, 2 * w // 3
    y[r0:r1, c0:c1] = ire_to_code(face_ire, depth) / maxv

    if noise > 0:
        y += rng.normal(0.0, noise, size=(h, w)).astype(np.float32)
        # a hard edge as well, so sharpness is not purely noise-driven
        y[h // 3: 2 * h // 3, ::16] += noise * 8

    # Planted as CONTIGUOUS BANDS, not scattered pixels. Isolated single-pixel
    # extremes are exactly what a lossy encoder smooths away, so a scattered
    # plant does not survive into the encoded file and the fixture silently
    # understates the fault it was built to test. Real clipping is a region --
    # a window, or a forehead specular -- so bands are also more realistic.
    cursor = h
    if clip_frac > 0:
        rows = max(1, int(round(h * clip_frac)))
        y[cursor - rows:cursor, :] = 1.0
        cursor -= rows
    if crush_frac > 0:
        rows = max(1, int(round(h * crush_frac)))
        y[cursor - rows:cursor, :] = 0.0

    return np.clip(y, 0.0, 1.0)


def pack_planar(y_norm: np.ndarray, depth: int, subsample: str) -> bytes:
    """Pack a normalised luma plane plus neutral chroma into planar YUV bytes."""
    h, w = y_norm.shape
    maxv = (1 << depth) - 1
    dt = np.uint16 if depth > 8 else np.uint8
    y = np.rint(y_norm * maxv).astype(dt)

    if subsample == "422":
        cw, ch = w // 2, h
    elif subsample == "420":
        cw, ch = w // 2, h // 2
    else:
        cw, ch = w, h
    neutral = (1 << (depth - 1))
    u = np.full((ch, cw), neutral, dtype=dt)
    v = np.full((ch, cw), neutral, dtype=dt)

    if depth > 8:
        return (y.astype("<u2").tobytes() + u.astype("<u2").tobytes()
                + v.astype("<u2").tobytes())
    return y.tobytes() + u.tobytes() + v.tobytes()


# ---------------------------------------------------------------------------
# audio
# ---------------------------------------------------------------------------

def speechlike(dur: float, seed: int, *, peak_dbfs: float,
               noise_floor_dbfs: float, hard_clip: bool = False) -> np.ndarray:
    """A crude but level-accurate stand-in for a talking voice."""
    rng = np.random.default_rng(seed)
    n = int(SR * dur)
    t = np.arange(n) / SR
    sig = np.zeros(n, dtype=np.float64)
    for f, a in ((110, 1.0), (220, 0.5), (330, 0.28), (660, 0.12), (1320, 0.05)):
        sig += a * np.sin(2 * np.pi * f * t + rng.uniform(0, 6.28))
    # syllable envelope with real pauses, so a noise floor can be measured
    env = np.clip(np.sin(2 * np.pi * 2.7 * t) ** 2, 0, 1)
    env *= (np.sin(2 * np.pi * 0.21 * t) > -0.55)
    sig *= env
    if np.max(np.abs(sig)) > 0:
        sig /= np.max(np.abs(sig))
    sig *= 10 ** (peak_dbfs / 20.0)
    sig += rng.normal(0, 10 ** (noise_floor_dbfs / 20.0), n)
    if hard_clip:
        sig = np.clip(sig * 4.0, -1.0, 1.0)     # flat-tops the waveform
    return np.clip(sig, -1.0, 1.0)


def write_wav(path: str, chans: list[np.ndarray], depth_codec="pcm_s24le") -> None:
    inter = np.stack(chans, axis=1).astype("<f4").reshape(-1)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "f32le", "-ar", str(SR), "-ac", str(len(chans)), "-i", "-",
           "-c:a", depth_codec, path]
    p = subprocess.run(cmd, input=inter.tobytes(), capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"wav encode failed for {path}: "
                           f"{p.stderr.decode('utf-8', 'replace')[-500:]}")


# ---------------------------------------------------------------------------
# video
# ---------------------------------------------------------------------------

def write_video(path: str, *, w: int, h: int, depth: int, subsample: str,
                fps: str, encoder: str, pix_fmt: str, nframes: int,
                frame_specs, bitrate: str | None, extra: list[str] | None = None,
                seed: int = 0, audio: str | None = None) -> None:
    """
    Encode video and attach audio in ONE pass.

    Deliberately never stream-copies the video. ffmpeg 8's MOV muxer corrupts a
    4:2:2 10-bit h264 bitstream on `-c:v copy` (even a video-only remux):
    the avcC extradata does not survive and the result probes as
    "missing picture in access unit / no start code found". Encoding once,
    straight to the final file, removes that step entirely.
    """
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", pix_fmt,
            "-s", f"{w}x{h}", "-r", fps, "-i", "-"]
    if audio:
        args += ["-i", audio]
    args += ["-c:v", encoder, "-r", fps]
    if encoder == "libx264":
        args += ["-preset", "ultrafast", "-x264-params", "keyint=1"]
        args += ["-profile:v", "high422" if subsample == "422" else "high10"]
    elif encoder == "libx265":
        args += ["-preset", "ultrafast", "-x265-params",
                 "keyint=1:log-level=none"]
    elif encoder == "prores_ks":
        args += ["-profile:v", "3"]
    if bitrate:
        args += ["-b:v", bitrate, "-maxrate", bitrate,
                 "-bufsize", bitrate.replace("M", "") + "M"]
    if extra:
        args += extra
    if audio:
        args += ["-c:a", "copy", "-map", "0:v:0", "-map", "1:a:0", "-shortest"]
    else:
        args += ["-an"]
    args += [path]

    p = subprocess.Popen(args, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    rng = np.random.default_rng(seed)
    try:
        for i in range(nframes):
            spec = frame_specs(i)
            y = make_frame(w, h, depth, rng=rng, **spec)
            p.stdin.write(pack_planar(y, depth, subsample))
    finally:
        p.stdin.close()
        err = p.stderr.read().decode("utf-8", "replace")
        rc = p.wait()
    if rc != 0:
        raise RuntimeError(f"video encode failed for {path}: {err[-800:]}")


def verify(path: str) -> None:
    """
    Confirm a fixture actually decodes before it is trusted as a test input.

    This is not paranoia. A synthetic clip built from a mismatched
    codec/pixel-format/container combination produces a file that does not
    decode, and in a report that looks exactly like an analyser bug rather than
    a fixture bug. Every fixture is checked here, at the point of creation.
    """
    pr = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_name,nb_frames", "-of", "csv=p=0", path],
        capture_output=True)
    dec = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "null", "-"],
                         capture_output=True)
    if pr.returncode != 0 or dec.stderr.strip():
        raise RuntimeError(
            f"FIXTURE IS UNDECODABLE: {os.path.basename(path)}\n"
            f"  probe: {pr.stderr.decode('utf-8', 'replace')[:300]}\n"
            f"  decode: {dec.stderr.decode('utf-8', 'replace')[:300]}\n"
            f"  This is a fixture bug, not an analyser bug. Fix the "
            f"codec/container combination before running verify_fixtures.py.")


# ---------------------------------------------------------------------------
# the fixture set
# ---------------------------------------------------------------------------

def build(fast: bool) -> None:
    os.makedirs(FIX, exist_ok=True)
    W, H = (1280, 720) if fast else (3840, 2160)
    DUR = 3.6
    n = int(DUR * 30)

    def f(name: str) -> str:
        return os.path.join(FIX, name)

    made: list[str] = []

    def emit(name: str, **kw) -> None:
        write_video(f(name), **kw)
        verify(f(name))
        made.append(name)

    def wav(name: str, chans, codec="pcm_s24le") -> str:
        write_wav(f(name), chans, codec)
        verify(f(name))
        if name not in made:
            made.append(name)
        return f(name)

    common = dict(w=W, h=H, depth=10, fps="30000/1001", nframes=n)

    # ---- audio beds (also fixtures in their own right) -------------------
    print("  audio beds")
    a_two = wav("_bed_two_channel.wav",
                [speechlike(DUR, 11, peak_dbfs=-12, noise_floor_dbfs=-78),
                 speechlike(DUR, 22, peak_dbfs=-14, noise_floor_dbfs=-78)])
    a_one = wav("_bed_one_channel.wav",
                [speechlike(DUR, 33, peak_dbfs=-13, noise_floor_dbfs=-78)])
    a_hot = wav("_bed_clipped.wav",
                [speechlike(DUR, 44, peak_dbfs=-3, noise_floor_dbfs=-30,
                            hard_clip=True)])

    # ---- GOOD: tight camera ---------------------------------------------
    # planted: face 60 IRE, base 30 IRE, no clip, no crush, steady sharpness,
    # 29.97 fps, 10-bit 4:2:2, two audio channels at different levels
    print("  good_tight.mp4   (29.97, 10-bit 4:2:2, face @ 60 IRE)")
    emit("good_tight.mp4", subsample="422", encoder="libx264",
         pix_fmt="yuv422p10le", bitrate="200M", seed=1, audio=a_two,
         frame_specs=lambda i: dict(base_ire=30.0, face_ire=60.0, noise=0.004),
         **common)

    # ---- GOOD: wide camera ----------------------------------------------
    print("  good_wide.mp4    (29.97, 10-bit 4:2:0, face @ 56 IRE)")
    emit("good_wide.mp4", subsample="420", encoder="libx265",
         pix_fmt="yuv420p10le", bitrate="120M", seed=2, audio=a_one,
         frame_specs=lambda i: dict(base_ire=28.0, face_ire=56.0, noise=0.004),
         **common)

    # ---- BAD: frame-rate mismatch ---------------------------------------
    # identical to good_wide but TRUE 30.000 -> must trip the cross-camera FAIL
    print("  bad_fps_wide.mp4 (30.000 — plants the cross-camera mismatch)")
    emit("bad_fps_wide.mp4", subsample="420", encoder="libx265",
         pix_fmt="yuv420p10le", bitrate="120M", seed=3, audio=a_one,
         w=W, h=H, depth=10, fps="30", nframes=n,
         frame_specs=lambda i: dict(base_ire=28.0, face_ire=56.0, noise=0.004))

    # ---- BAD: 8-bit, clipped, crushed, wrong res, wrong fps, hot audio --
    print("  bad_everything.mp4 (8-bit, 1080p, 25fps, 6% clip, 15% crush, "
          "clipped audio)")
    emit("bad_everything.mp4", w=1920, h=1080, depth=8, subsample="420",
         fps="25", encoder="libx264", pix_fmt="yuv420p", nframes=int(DUR * 25),
         bitrate="12M", seed=4, audio=a_hot,
         frame_specs=lambda i: dict(base_ire=70.0, face_ire=95.0, noise=0.004,
                                    clip_frac=0.06, crush_frac=0.15))

    # ---- BAD: focus hunting ---------------------------------------------
    # sharpness swings hard frame to frame -> focus-stability FAIL
    print("  bad_focus.mp4    (sharpness oscillates — plants AF hunting)")
    emit("bad_focus.mp4", subsample="422", encoder="libx264",
         pix_fmt="yuv422p10le", bitrate="200M", seed=5, audio=a_two,
         frame_specs=lambda i: dict(
             base_ire=30.0, face_ire=60.0,
             noise=0.006 if (i // 6) % 2 == 0 else 0.00004),
         **common)

    # ---- OK: sharpness changes because the SUBJECT changes ----------------
    # Chart held in frame (fine detail, sharper), then a face at a steady level,
    # then the subject walks off (softer empty room). This is what every real take
    # looks like, and a single whole-clip CV calls it AF hunting. Must come back
    # as INFO, not WARN: the dips are at the ENDS.
    #
    # The contrast is deliberately MILD, matched to a real measurement -- a 23 s
    # test clip on 2026-09-07 read 36% whole-clip against 4.9% settled. A first
    # attempt used a 30x sharpness swing, produced a 138% CV, and tripped the
    # FAIL cap. That cap is a safety property (a FAIL is never explained away by
    # transitions), so the fixture was wrong, not the threshold.
    print("  ok_focus_transitions.mp4 (chart -> steady subject -> walk-off)")
    emit("ok_focus_transitions.mp4", subsample="422", encoder="libx264",
         pix_fmt="yuv422p10le", bitrate="200M", seed=11, audio=a_two,
         frame_specs=lambda i: dict(
             base_ire=30.0, face_ire=60.0,
             noise=0.0075 if i < 29 else (0.006 if i < 80 else 0.004)),
         **common)

    # ---- BAD: a dip in the MIDDLE, with steady footage either side --------
    # The distinction that keeps the transition allowance honest. Same overall
    # spread as the fixture above, but the soft frames have sharp footage on
    # BOTH sides -- which is autofocus letting go mid-answer, and must stay
    # flagged however the run-finder carves the clip up.
    print("  bad_focus_interior_dip.mp4 (steady, dip mid-clip, steady)")
    emit("bad_focus_interior_dip.mp4", subsample="422", encoder="libx264",
         pix_fmt="yuv422p10le", bitrate="200M", seed=12, audio=a_two,
         frame_specs=lambda i: dict(
             base_ire=30.0, face_ire=60.0,
             noise=0.0004 if 44 <= i < 64 else 0.006),
         **common)

    # ---- BAD: 8-bit content inside a 10-bit container --------------------
    # Quantise so every 10-bit value is a multiple of 4 -- that is what a real
    # 8->10 bit promotion does (a left shift of 2), NOT a rescale by 1023/255.
    # Encoded as ProRes 422 HQ deliberately: its DCT smears the fingerprint
    # harder than any intra h264, and it is the format this shoot records, so
    # this is the case the threshold has to catch.
    print("  bad_8bit_in_10bit.mov (8-bit content, 10-bit ProRes wrapper)")
    _orig_pack = pack_planar

    def _pack_8bit_content(y_norm, d, sub):
        step = 4.0 / ((1 << d) - 1)
        return _orig_pack(np.rint(y_norm / step) * step, d, sub)

    globals()["pack_planar"] = _pack_8bit_content
    try:
        emit("bad_8bit_in_10bit.mov", subsample="422", encoder="prores_ks",
             pix_fmt="yuv422p10le", bitrate=None, seed=9, audio=a_two,
             frame_specs=lambda i: dict(base_ire=30.0, face_ire=60.0,
                                        noise=0.004),
             **common)
    finally:
        globals()["pack_planar"] = _orig_pack

    # ---- BAD: video with no audio stream --------------------------------
    print("  no_audio.mp4     (short — audio-stream check only)")
    emit("no_audio.mp4", subsample="422", encoder="libx264",
         pix_fmt="yuv422p10le", bitrate="200M", seed=6, audio=None,
         w=W, h=H, depth=10, fps="30000/1001", nframes=int(DUR * 30),
         frame_specs=lambda i: dict(base_ire=30.0, face_ire=60.0, noise=0.004))

    # ---- BAD: frames missing from the timeline ---------------------------
    # Remove two CONTIGUOUS RUNS of frames while preserving the surviving
    # timestamps. That leaves real holes among otherwise-nominal intervals,
    # which is the signature of a recorder that missed writes. Note a
    # UNIFORMLY sparse file is a different thing entirely -- that is just a
    # lower frame rate, and the frame-rate check catches it instead.
    print("  bad_dropped_frames.mp4 (two runs of frames missing)")
    rc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", f("good_tight.mp4"),
         "-vf", "select='not(between(n,30,36))*not(between(n,70,73))'",
         "-fps_mode", "passthrough", "-c:v", "libx264", "-preset", "ultrafast",
         "-x264-params", "keyint=1", "-profile:v", "high422",
         "-pix_fmt", "yuv422p10le", "-b:v", "200M", "-an",
         f("bad_dropped_frames.mp4")], capture_output=True)
    if rc.returncode != 0:
        raise RuntimeError("dropped-frame fixture failed: "
                           + rc.stderr.decode()[-300:])
    verify(f("bad_dropped_frames.mp4"))
    made.append("bad_dropped_frames.mp4")

    # ---- audio-only fixtures --------------------------------------------
    print("  audio-only fixtures")
    wav("good_audio_24b.wav",
        [speechlike(DUR * 2, 55, peak_dbfs=-12, noise_floor_dbfs=-78)])
    wav("bad_audio_quiet.wav",
        [speechlike(DUR * 2, 66, peak_dbfs=-34, noise_floor_dbfs=-85)])
    wav("bad_audio_clipped.wav",
        [speechlike(DUR * 2, 77, peak_dbfs=-1, noise_floor_dbfs=-55,
                    hard_clip=True)])
    one = speechlike(DUR * 2, 88, peak_dbfs=-12, noise_floor_dbfs=-78)
    wav("bad_audio_dualmono.wav", [one, one.copy()])
    live = speechlike(DUR * 2, 99, peak_dbfs=-12, noise_floor_dbfs=-78)
    wav("bad_audio_dead_ch.wav", [live, np.zeros_like(live)])
    # every channel silent -- a different fault from one channel silent, and it
    # must not be reported as "silent while others carry signal"
    wav("bad_audio_all_silent.wav",
        [np.zeros_like(live), np.zeros_like(live)])
    # 4-channel HDMI embed: channels 1/2 empty, 3/4 carrying two different
    # voices. This is what every Ninja recording off the A7C R looks like and it
    # must NOT be reported as a dead transmitter.
    wav("ok_audio_4ch_hdmi_embed.wav",
        [np.zeros_like(live), np.zeros_like(live),
         speechlike(DUR * 2, 121, peak_dbfs=-12, noise_floor_dbfs=-78),
         speechlike(DUR * 2, 131, peak_dbfs=-16, noise_floor_dbfs=-78)])
    wav("bad_audio_16b.wav",
        [speechlike(DUR * 2, 111, peak_dbfs=-12, noise_floor_dbfs=-78)],
        codec="pcm_s16le")

    # ---- malformed files: graceful-degradation tests --------------------
    print("  garbage.mp4, empty.mp4  (must be handled, not crash)")
    with open(f("garbage.mp4"), "wb") as fh:
        fh.write(np.random.default_rng(7).bytes(200_000))
    open(f("empty.mp4"), "wb").close()
    made += ["garbage.mp4", "empty.mp4"]

    print(f"\n{len(made)} fixtures written to {FIX}")
    print("every playable fixture was probed and fully decoded before being "
          "accepted.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true",
                    help="1280x720 instead of 3840x2160 (much quicker; "
                         "resolution checks will correctly fail)")
    a = ap.parse_args()
    try:
        build(a.fast)
    except Exception as e:
        sys.stderr.write(f"FATAL: {type(e).__name__}: {e}\n")
        sys.exit(1)

#!/usr/bin/env python3
"""
shootcheck — objective technical QC for interview footage and audio.

Measures. Does not opine. Every number printed here is something you could read
off a waveform monitor or a level meter. It cannot tell you whether the lighting
looks good, whether the background is distracting, or whether the framing works.
A clean report means "no technical defect detected", not "good footage".

Usage:
    ./shootcheck.py FILE [FILE ...]
    ./shootcheck.py --dir /path/to/card
    ./shootcheck.py --dir . --json > report.json
    ./shootcheck.py A.MP4 --role tight  B.MP4 --role wide

Exit codes:
    0  READY                       (no WARN, no FAIL)
    1  USABLE WITH RESERVATIONS    (WARN present, no FAIL)
    2  NOT READY                   (at least one FAIL)
    3  tool error (nothing analysed)

Requires: python3, ffmpeg, ffprobe, numpy.  Pillow is not required.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from fractions import Fraction
from typing import Any

try:
    import numpy as np
except ImportError:
    sys.stderr.write("FATAL: numpy is required.  pip3 install numpy\n")
    sys.exit(3)


# ===========================================================================
#  THRESHOLDS — edit these, nothing else
# ===========================================================================

TH: dict[str, Any] = {
    # ---- container / stream -------------------------------------------------
    "resolution_expect": (3840, 2160),
    # Accepted frame-rate family. Both are "30p" colloquially; they are NOT
    # interchangeable in an NLE. Mixing them drifts ~1 frame every 33 s.
    "fps_family": {
        "29.97": Fraction(30000, 1001),
        "30.000": Fraction(30, 1),
    },
    "fps_tolerance": 0.02,          # fps units, for matching a measured rate
    "bit_depth_min": 10,            # 8-bit log grades badly (banding in skin)
    # A 10-bit CONTAINER does not prove 10-bit CONTENT. ProRes 422 HQ is always a
    # 10-bit file even when fed an 8-bit signal, so pix_fmt cannot answer "did the
    # camera really send 10-bit". An 8-bit source promoted into a 10-bit container
    # leaves a fingerprint: its 10-bit code values are all multiples of 4.
    # Calibrated by measurement, not guessed. Known-10-bit content reads 0.250 in
    # every codec tested (uniform residues, as theory predicts). Known-8-bit
    # content promoted to a 10-bit container reads 1.000 lossless, 0.934 through
    # h264 intra at 400 Mbps, and 0.476 through ProRes 422 HQ -- ProRes's DCT
    # smears the fingerprint hardest, and ProRes is the format actually being
    # recorded, so the boundary has to sit low enough to catch 0.476.
    "bitdepth_util_warn": 0.35,
    "bitrate_floor_mbps": {         # per-role sanity floor, video stream
        "tight": 80.0,
        "wide": 40.0,
        "prores": 300.0,
        "unknown": 30.0,
    },
    "duration_min_s": 3.0,          # shorter than this and stats are noise

    # ---- exposure (IRE, 0 = legal black, 100 = legal white) ----------------
    # Log-aware. Correctly exposed log looks FLAT and sits mid-range. If it
    # looks "right" on an ungraded monitor it is overexposed.
    "clip_pct_warn": 0.50,          # % of pixels at top of code range
    "clip_pct_fail": 2.00,
    "crush_pct_warn": 2.00,         # % of pixels below the log black floor
    "crush_pct_fail": 10.00,
    # Frame-wide median IRE window. Wide, because a dark suit / dark background
    # legitimately pulls the whole-frame median down.
    "frame_median_ire_warn": (18.0, 62.0),
    # Brightest-region median IRE — the closest cheap proxy for "is skin at the
    # right level". Per-profile, set in PROFILES below.

    # ---- focus -------------------------------------------------------------
    # Absolute sharpness is content-dependent, so the absolute floor is a
    # LOW-CONFIDENCE check. Stability across frames is the reliable one: it is
    # how autofocus hunting shows up, and hunting is the exact failure that
    # cannot be caught from in front of the camera.
    "focus_cv_warn": 0.25,          # coeff. of variation of sharpness, 0..1
    "focus_cv_fail": 0.50,
    "focus_acutance_floor": 0.004,  # normalised; below this nothing resolves

    # ---- colour cast -------------------------------------------------------
    # Frame-average chroma. A weak metric — a red shirt moves it — so this only
    # WARNs at a large deviation and is reported as information otherwise.
    "chroma_dev_warn_pct": 4.0,     # % of full chroma scale from neutral

    # ---- audio -------------------------------------------------------------
    "audio_peak_target_dbfs": -12.0,
    "audio_peak_fail_above": 0.0,   # digital clip
    "audio_peak_warn_above": -6.0,
    "audio_peak_warn_below": -24.0,
    "audio_peak_fail_below": -40.0,  # effectively a dead channel
    "audio_noise_floor_warn": -50.0,  # above this = audible hiss/HVAC
    "audio_dynamic_range_warn": 40.0,  # dB, peak-to-noise
    "audio_dc_offset_warn": 0.01,   # fraction of full scale
    "audio_flat_factor_warn": 1.0,  # >0 means consecutive identical samples
    "audio_sample_rate_expect": 48000,
    "audio_bit_depth_expect": 24,
    "lufs_warn_above": -12.0,
    "lufs_warn_below": -30.0,
    # A Rode transmitter's internal recorder applies NO gain -- Rode: "it
    # captures a raw, unprocessed signal." Those files are legitimately quiet and
    # must not be judged against a -12 dBFS camera-track target, or the tool
    # cries wolf on the one audio file that actually gets delivered.
    "tx_peak_warn_below": -45.0,
    "tx_peak_fail_below": -65.0,
    "tx_noise_floor_warn": -60.0,
    # A table room-mic on built-in capsules is a THIRD-copy rescue track. It is
    # deliberately recorded conservatively and it will never match a lav for
    # peak-to-noise, so judging it against the primary-track thresholds leaves a
    # permanent warning on a correctly configured file.
    "roommic_dynamic_range_warn": 30.0,
    "roommic_noise_floor_warn": -40.0,
    "roommic_peak_warn_below": -32.0,

    # ---- cross-file --------------------------------------------------------
    "cross_luma_spread_ire_warn": 12.0,
    "cross_luma_spread_ire_fail": 25.0,

    # ---- frame timing integrity --------------------------------------------
    # A recorder whose media cannot keep up drops frames. Atomos flags this with
    # a kangaroo icon, but nothing in the FILE says so, so it has to be measured.
    "timing_irregular_pct_warn": 0.5,   # % of inter-frame gaps off-nominal
    "timing_irregular_pct_fail": 2.0,
    "timing_gap_mult_warn": 1.5,        # a gap this many x nominal = a hole
    "fps_implied_tolerance": 0.005,     # implied vs declared fps
    "dup_frame_pct_warn": 0.1,          # % exact-duplicate frames
    "dup_frame_pct_fail": 1.0,

    # ---- sampling ----------------------------------------------------------
    # 18, not 9: the focus-stability transition check needs at least 5 samples
    # inside one steady run to tell "subject entered frame" from "autofocus
    # hunting", and 9 samples across a take containing a chart hold cannot supply
    # that. Measured cost is ~0.45 s per sample, so 9 -> 18 buys the
    # discrimination for about 4 seconds.
    "video_samples": 18,            # frames pulled per file
    "sample_margin": 0.04,          # skip first/last 4% of duration
}

# Per-log-profile anchors. IRE values are where each curve puts a known
# reflectance, taken from the manufacturers' published transfer functions.
PROFILES: dict[str, dict[str, Any]] = {
    "slog3": {
        "label": "S-Log3",
        "mid_grey_ire": 41.0,       # 18% grey
        "white_ire": 61.0,          # 90% white
        "skin_ire": (55.0, 65.0),   # nominal exposure
        "black_floor_code": 95 / 1023,   # S-Log3 codes 0% reflectance here
    },
    "dlogm": {
        "label": "D-Log M",
        "mid_grey_ire": 39.0,
        "white_ire": 76.0,
        "skin_ire": (50.0, 62.0),
        # 🔴 WAS 64/1023 -- the Rec.709 value, and byte-identical to the "unknown" default below
        # with no comment, so it was an untouched copy rather than a decision. D-Log M floors at
        # code 95, same as S-Log3: see references/grade-match.md ("D-Log M also sits at 95",
        # identified three independent ways) and the kit changelog.
        #
        # This value is the CRUSH threshold, so 64 made the detector blind in exactly the band that
        # matters: blacks crushed from 95 down to ~70 are crushed *for this format* and still above
        # Rec.709 black, so `(y <= 64)` never fired. The existing fixture plants TRUE BLACK, which
        # is below both numbers and therefore could not tell the two apart -- see
        # `crushed_log_floor.mp4` in make_fixtures.py, added to cover the gap.
        "black_floor_code": 95 / 1023,
    },
    "unknown": {
        "label": "unknown/none",
        "mid_grey_ire": None,
        "white_ire": None,
        "skin_ire": (40.0, 75.0),   # deliberately loose
        "black_floor_code": 64 / 1023,
    },
}


# ===========================================================================
#  plumbing
# ===========================================================================

LEVEL_ORDER = {"FAIL": 0, "WARN": 1, "INFO": 2, "PASS": 3, "SKIP": 4}

VIDEO_EXT = {".mp4", ".mov", ".mxf", ".mts", ".m2ts", ".avi", ".mkv", ".m4v"}
AUDIO_EXT = {".wav", ".flac", ".aif", ".aiff", ".mp3", ".m4a"}


@dataclass
class Finding:
    level: str          # FAIL | WARN | INFO | PASS | SKIP
    check: str
    message: str
    fix: str = ""

    def sort_key(self) -> tuple[int, str]:
        return (LEVEL_ORDER.get(self.level, 9), self.check)


@dataclass
class FileReport:
    path: str
    name: str = ""
    role: str = "unknown"
    profile: str = "unknown"
    kind: str = "unknown"           # video | audio | unknown
    findings: list[Finding] = field(default_factory=list)
    stream: dict[str, Any] = field(default_factory=dict)
    video: dict[str, Any] = field(default_factory=dict)
    audio: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def add(self, level: str, check: str, message: str, fix: str = "") -> None:
        self.findings.append(Finding(level, check, message, fix))

    @property
    def worst(self) -> str:
        if self.error:
            return "FAIL"
        best = "PASS"
        for f in self.findings:
            if LEVEL_ORDER[f.level] < LEVEL_ORDER[best]:
                best = f.level
        return "FAIL" if best == "FAIL" else ("WARN" if best == "WARN" else "PASS")


def run(cmd: list[str], timeout: int = 900) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return (
            p.returncode,
            p.stdout.decode("utf-8", "replace"),
            p.stderr.decode("utf-8", "replace"),
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except FileNotFoundError as e:
        return 127, "", str(e)


def run_raw(cmd: list[str], timeout: int = 900) -> tuple[int, bytes, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return 124, b"", f"timeout after {timeout}s"
    except FileNotFoundError as e:
        return 127, b"", str(e)


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def fmt_fps(fr: Fraction | None) -> str:
    if fr is None:
        return "?"
    if fr.denominator == 1001:
        return f"{float(fr):.3f} ({fr.numerator}/{fr.denominator})"
    return f"{float(fr):.3f}"


def finite(x):
    """None for inf/nan. json.dumps would otherwise emit invalid `-Infinity`."""
    if x is None:
        return None
    try:
        return x if math.isfinite(float(x)) else None
    except (TypeError, ValueError):
        return None


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def human_dur(s: float | None) -> str:
    if s is None:
        return "?"
    m, sec = divmod(int(round(s)), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{sec:02d}" if h else f"{m:d}:{sec:02d}"


# ===========================================================================
#  probing
# ===========================================================================

def ffprobe_json(path: str) -> dict[str, Any] | None:
    rc, out, _ = run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ], timeout=120)
    if rc != 0 or not out.strip():
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def probe_decoded_pix_fmt(path: str) -> str | None:
    """pix_fmt is sometimes absent from the stream header; decode one frame."""
    rc, out, _ = run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "frame=pix_fmt", "-read_intervals", "%+#1",
        "-print_format", "json", path,
    ], timeout=120)
    if rc != 0:
        return None
    try:
        frames = json.loads(out).get("frames") or []
        if frames:
            return frames[0].get("pix_fmt")
    except json.JSONDecodeError:
        pass
    return None


PIX_FMT_RE = re.compile(
    r"^(?:yuvj?|gbr)(?P<sub>444|422|420|411|410)?p?(?P<depth>9|10|12|14|16)?"
)


def parse_pix_fmt(pf: str | None) -> tuple[int | None, str | None]:
    """Return (bit_depth, chroma_subsampling) from an ffmpeg pix_fmt name."""
    if not pf:
        return None, None
    depth: int | None = 8
    sub: str | None = None
    m = re.search(r"(\d{1,2})(?:le|be)?$", pf)
    if m and int(m.group(1)) in (9, 10, 12, 14, 16):
        depth = int(m.group(1))
    for token, label in (("444", "4:4:4"), ("422", "4:2:2"),
                         ("420", "4:2:0"), ("411", "4:1:1"), ("410", "4:1:0")):
        if token in pf:
            sub = label
            break
    if pf.startswith("gray"):
        sub = "monochrome"
    if pf.startswith(("rgb", "bgr", "gbrp")):
        sub = "4:4:4 (RGB)"
    return depth, sub


def parse_fps(s: str | None) -> Fraction | None:
    if not s or s in ("0/0", "N/A"):
        return None
    try:
        fr = Fraction(s)
        return fr if fr > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


def classify_fps(fr: Fraction | None) -> tuple[str | None, bool]:
    """Return (family_label, exact). exact=True if it is literally the rational."""
    if fr is None:
        return None, False
    for label, target in TH["fps_family"].items():
        if fr == target:
            return label, True
        if abs(float(fr) - float(target)) <= TH["fps_tolerance"]:
            return label, False
    return None, False


def detect_rotation(stream: dict[str, Any]) -> int:
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                return int(round(float(sd["rotation"])))
            except (TypeError, ValueError):
                pass
    tags = stream.get("tags") or {}
    if "rotate" in tags:
        try:
            return int(round(float(tags["rotate"])))
        except (TypeError, ValueError):
            pass
    return 0


def guess_role(path: str, vstream: dict[str, Any] | None) -> str:
    """Best-effort. --role always wins; this is only a default."""
    name = os.path.basename(path).upper()
    if vstream is None:
        return "audio"
    codec = (vstream.get("codec_name") or "").lower()
    pf = vstream.get("pix_fmt") or ""
    if codec == "prores":
        return "prores"
    if name.startswith("DJI_"):
        return "wide"
    if re.match(r"^(C\d{4}|DSC\d|CLIP)", name):
        return "tight"
    if "422" in pf:
        return "tight"
    if codec in ("hevc", "h265") and "420" in pf:
        return "wide"
    if codec in ("h264", "avc1"):
        return "tight"
    return "unknown"


def guess_profile(role: str, override: str | None) -> str:
    if override:
        return override
    if role in ("tight", "prores"):
        return "slog3"
    if role == "wide":
        return "dlogm"
    return "unknown"


# ===========================================================================
#  video sampling — luma / chroma / focus from native code values
# ===========================================================================

def sample_timestamps(duration: float, n: int) -> list[float]:
    m = TH["sample_margin"]
    lo, hi = duration * m, duration * (1 - m)
    if hi <= lo:
        return [max(0.0, duration / 2)]
    if n <= 1:
        return [(lo + hi) / 2]
    step = (hi - lo) / (n - 1)
    return [lo + i * step for i in range(n)]


def grab_yuv444p16(path: str, ts: float, w: int, h: int
                   ) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """
    Pull one frame as planar 16-bit YUV 4:4:4.

    Chosen deliberately: converting to RGB would apply a matrix and a range
    expansion, which destroys the code values a waveform monitor would show.
    yuv444p16le from a 10-bit YUV source is a chroma upsample plus a left-shift,
    so Y stays proportional to the original code value. verify_mapping() below
    checks this assumption against ffmpeg's own signalstats.
    """
    rc, buf, _ = run_raw([
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", f"{ts:.3f}", "-i", path,
        "-frames:v", "1", "-pix_fmt", "yuv444p16le",
        "-f", "rawvideo", "-",
    ], timeout=180)
    need = w * h * 2 * 3
    if rc != 0 or len(buf) < need:
        return None
    a = np.frombuffer(buf[:need], dtype="<u2").astype(np.float32) / 65535.0
    planes = a.reshape(3, h, w)
    return planes[0], planes[1], planes[2]


def signalstats_yavg(path: str, ts: float) -> float | None:
    """ffmpeg's own frame statistics, used only to validate our pixel maths."""
    rc, _, err = run([
        "ffmpeg", "-hide_banner", "-loglevel", "info",
        "-ss", f"{ts:.3f}", "-i", path, "-frames:v", "1",
        "-vf", "signalstats,metadata=print",
        "-f", "null", "-",
    ], timeout=180)
    m = re.search(r"lavfi\.signalstats\.YAVG=([0-9.]+)", err)
    return float(m.group(1)) if m else None


def ire_from_code(code: np.ndarray | float, depth: int):
    """Code fraction (0..1) -> IRE, using legal-range anchors for that depth."""
    maxv = float((1 << depth) - 1)
    black = (16 << (depth - 8)) / maxv
    white = (235 << (depth - 8)) / maxv
    return (code - black) / (white - black) * 100.0


def laplacian_var(y: np.ndarray) -> float:
    lap = (4.0 * y[1:-1, 1:-1]
           - y[:-2, 1:-1] - y[2:, 1:-1]
           - y[1:-1, :-2] - y[1:-1, 2:])
    return float(lap.var())


# Fewest samples that can honestly support a stability number. Below this a
# 'settled run' is just two frames that happened to agree.
MIN_SETTLED = 5


def settled_sharpness(sharp: list[float],
                      lo_frac: float = 0.88) -> tuple[float, int, bool]:
    """
    Sharpness variation with entry/exit frames removed, and WHERE the dips were.

    Returns (cv_of_kept, n_excluded, dips_were_edges_only).

    A single CV over a whole clip WARNs on every real take, because every real
    take contains transitions: the colour chart comes out of frame, someone sits
    down, someone walks off. Measured on a 23 s test clip 2026-09-07 — 28.4%
    overall, 4.9% once the entry ramp and the walk-off were excluded.

    But a dip is ALSO the signature of autofocus hunting, which is the one
    failure that cannot be caught from in front of the camera. So dips must not
    simply be thrown away to make the number look good. What separates the two
    is position: entry and exit dips sit at the ends of the clip by definition,
    while hunting happens mid-answer, in the interior. Only edge dips are
    excusable, and only the caller decides what to do about it.
    """
    arr = np.asarray(sharp, dtype=float)
    n = len(arr)
    whole = float(np.std(arr) / np.mean(arr)) if n and np.mean(arr) > 0 else 0.0
    if n < MIN_SETTLED or np.mean(arr) <= 0:
        return whole, 0, False

    # Longest CONTIGUOUS run that stays inside a tight band around its own
    # median. Contiguity is what makes this honest: a clip that genuinely hunts
    # has no long steady run anywhere, so it falls back to the whole-clip number
    # and keeps its warning. A clip that merely changes subject -- chart, then a
    # face, then an empty room -- has one long steady run, which is the part you
    # actually care about. Trimming by level alone cannot do this: the colour
    # chart is SHARPER than a face, so it survives a low-pass filter and still
    # wrecks the number.
    best = (0, 0)
    for i in range(n):
        for j in range(i + MIN_SETTLED, n + 1):
            seg = arr[i:j]
            med = float(np.median(seg))
            if med <= 0:
                continue
            if float(seg.min()) >= lo_frac * med and float(seg.max()) <= med / lo_frac:
                if (j - i) > (best[1] - best[0]):
                    best = (i, j)
    lo, hi = best
    if (hi - lo) < MIN_SETTLED:
        return whole, 0, False
    seg = arr[lo:hi]
    cv = float(np.std(seg) / np.mean(seg)) if np.mean(seg) > 0 else 0.0

    # "Excluded" is not the same as "at an end". If the steadiest run is the tail
    # of the clip, everything before it is excluded -- including any mid-clip dip.
    # A dip only counts as a transition when there is NO in-band sample on the far
    # side of it; a dip with steady footage on both sides is hunting, whatever the
    # run-finder picked. Getting this wrong would let an interior dip inside the
    # WARN band be explained away, which is the failure this whole check exists
    # to catch.
    level = float(np.median(seg))
    in_band = (arr >= lo_frac * level) & (arr <= level / lo_frac)
    dip = arr < lo_frac * level
    interior_dip = False
    for k in np.flatnonzero(dip):
        if in_band[:k].any() and in_band[k + 1:].any():
            interior_dip = True
            break
    return cv, int(n - (hi - lo)), not interior_dip


def grid_sharpness(y: np.ndarray, n: int = 3) -> tuple[float, tuple[int, int]]:
    """
    Sharpest cell of an n x n grid, and where it is.

    Whole-frame sharpness is misleading on a two-shot: the centre of a wide
    two-shot is the gap between the subjects. Taking the max over a grid asks
    the more useful question -- did focus land ANYWHERE -- and reports where.
    """
    h, w = y.shape
    best, where = 0.0, (0, 0)
    for r in range(n):
        for c in range(n):
            cell = y[h * r // n:h * (r + 1) // n, w * c // n:w * (c + 1) // n]
            if cell.shape[0] < 3 or cell.shape[1] < 3:
                continue
            v = laplacian_var(cell)
            if v > best:
                best, where = v, (r, c)
    return best, where


GRID_NAMES = [["top-left", "top-centre", "top-right"],
              ["mid-left", "centre", "mid-right"],
              ["bottom-left", "bottom-centre", "bottom-right"]]


def packet_timing(path: str) -> dict[str, Any] | None:
    """
    Inter-frame timing from PACKET timestamps -- no decoding, so this is cheap
    even on a 35-minute 4K file. Catches holes where frames were never written.
    """
    rc, out, _ = run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "packet=pts_time", "-of", "csv=p=0", path,
    ], timeout=900)
    if rc != 0:
        return None
    ts = []
    for line in out.splitlines():
        line = line.strip().rstrip(",")
        if not line or line == "N/A":
            continue
        try:
            ts.append(float(line))
        except ValueError:
            continue
    if len(ts) < 10:
        return None
    ts.sort()
    d = np.diff(np.array(ts, dtype=np.float64))
    d = d[d > 0]
    if d.size < 5:
        return None
    nominal = float(np.median(d))
    return {
        "packets": len(ts),
        "span_s": ts[-1] - ts[0],
        "implied_fps": (len(ts) - 1) / (ts[-1] - ts[0]) if ts[-1] > ts[0] else None,
        "nominal_interval": nominal,
        "irregular_pct": float((np.abs(d - nominal) > nominal * 0.5).mean() * 100),
        "max_gap_mult": float(d.max() / nominal) if nominal > 0 else None,
        "n_big_gaps": int((d > nominal * TH["timing_gap_mult_warn"]).sum()),
    }


def duplicate_frames(path: str) -> dict[str, Any] | None:
    """
    Exact-duplicate detection via per-frame MD5. Requires a full decode, hence
    --deep. Uses exact hashes rather than ffmpeg's mpdecimate because a
    locked-off tripod shot of a static room legitimately contains near-identical
    frames -- mpdecimate would call those duplicates and cry wolf.
    """
    rc, out, _ = run(["ffmpeg", "-hide_banner", "-loglevel", "error",
                      "-i", path, "-an", "-f", "framemd5", "-"], timeout=3600)
    if rc != 0:
        return None
    hashes = [ln.rsplit(",", 1)[-1].strip() for ln in out.splitlines()
              if ln and not ln.startswith("#") and "," in ln]
    if len(hashes) < 10:
        return None
    dups = sum(1 for a, b in zip(hashes, hashes[1:]) if a == b)
    runs, cur = [], 1
    for a, b in zip(hashes, hashes[1:]):
        if a == b:
            cur += 1
        else:
            if cur > 1:
                runs.append(cur)
            cur = 1
    if cur > 1:
        runs.append(cur)
    return {"frames": len(hashes), "dup_frames": dups,
            "dup_pct": dups / len(hashes) * 100,
            "longest_dup_run": max(runs) if runs else 1}


def check_timing(rep: FileReport, path: str, fps: Fraction | None,
                 duration: float, deep: bool) -> None:
    t = packet_timing(path)
    if t is None:
        rep.add("SKIP", "frame timing", "Could not read packet timestamps.")
    else:
        rep.video.update({f"timing_{k}": (round(v, 4) if isinstance(v, float) else v)
                          for k, v in t.items() if v is not None})
        ip = t.get("implied_fps")
        if fps and ip:
            rel = abs(ip - float(fps)) / float(fps)
            if rel > TH["fps_implied_tolerance"]:
                rep.add("WARN", "frame timing",
                        f"Timestamps imply {ip:.4f} fps but the stream declares "
                        f"{float(fps):.4f}.",
                        "The file's own timing disagrees with its declared rate. "
                        "Usually variable frame rate or a recorder that dropped "
                        "frames. Compare the clip's duration against how long you "
                        "actually rolled.")
        irr = t["irregular_pct"]
        if irr >= TH["timing_irregular_pct_fail"]:
            rep.add("FAIL", "frame timing",
                    f"{irr:.2f}% of inter-frame gaps are off-nominal; largest is "
                    f"{t['max_gap_mult']:.1f}x the normal interval "
                    f"({t['n_big_gaps']} big gaps).",
                    "Frames are missing from the recording. On an external "
                    "recorder this is the media failing to keep up -- Atomos "
                    "shows a kangaroo icon for exactly this. Use a qualified, "
                    "faster, emptier drive, reseat it, and re-record. Do not "
                    "deliver this file.")
        elif irr >= TH["timing_irregular_pct_warn"] or t["n_big_gaps"] > 0:
            rep.add("WARN", "frame timing",
                    f"{irr:.2f}% of gaps off-nominal, {t['n_big_gaps']} gap(s) "
                    f"over {TH['timing_gap_mult_warn']}x nominal (largest "
                    f"{t['max_gap_mult']:.1f}x).",
                    "A handful of irregular gaps can be container overhead, but "
                    "it is also what a recorder dropping frames looks like. "
                    "Check the clip's duration against how long you rolled, and "
                    "re-run with --deep to hunt for duplicated frames.")
        else:
            rep.add("PASS", "frame timing",
                    f"{t['packets']} frames, gaps regular "
                    f"(worst {t['max_gap_mult']:.2f}x nominal), implied "
                    f"{ip:.4f} fps.")

    # Report the numbers needed to compare against wall-clock -- the only way to
    # catch a recorder that dropped frames AND renumbered what remained.
    if fps and duration:
        rep.video["expected_frames_at_declared_fps"] = round(duration * float(fps))
        rep.add("INFO", "duration cross-check",
                f"Clip is {duration:.2f}s = {duration * float(fps):.0f} frames at "
                f"{float(fps):.3f} fps.",
                "Compare this against how long you actually rolled. If the clip "
                "is shorter than the real take, frames were dropped and "
                "renumbered -- which leaves no other trace in the file.")

    if deep:
        d = duplicate_frames(path)
        if d is None:
            rep.add("SKIP", "duplicate frames", "framemd5 unavailable.")
        else:
            rep.video.update({"dup_frame_pct": round(d["dup_pct"], 3),
                              "longest_dup_run": d["longest_dup_run"]})
            if d["dup_pct"] >= TH["dup_frame_pct_fail"]:
                rep.add("FAIL", "duplicate frames",
                        f"{d['dup_pct']:.2f}% of frames are byte-identical to "
                        f"the one before ({d['dup_frames']} frames, longest run "
                        f"{d['longest_dup_run']}).",
                        "A recorder that cannot keep up pads with repeated "
                        "frames. This shows as a stutter and cannot be fixed. "
                        "Use a qualified drive and re-record.")
            elif d["dup_pct"] >= TH["dup_frame_pct_warn"]:
                rep.add("WARN", "duplicate frames",
                        f"{d['dup_pct']:.2f}% byte-identical repeats "
                        f"(longest run {d['longest_dup_run']}).",
                        "Small numbers can occur in genuinely motionless "
                        "footage, but repeats are also how a struggling "
                        "recorder pads. Watch for a stutter.")
            else:
                rep.add("PASS", "duplicate frames",
                        f"{d['dup_pct']:.2f}% byte-identical repeats.")


def analyse_video(rep: FileReport, path: str, vs: dict[str, Any],
                  duration: float, depth: int) -> None:
    w = int(vs.get("width") or 0)
    h = int(vs.get("height") or 0)
    if w < 8 or h < 8:
        rep.add("SKIP", "pixels", "No usable frame dimensions; pixel analysis skipped.")
        return

    prof = PROFILES[rep.profile]
    ts_list = sample_timestamps(duration, TH["video_samples"])

    ire_med, ire_mean, clip_p, crush_p = [], [], [], []
    sharp, acut, cb_dev, cv_dev, bright_med = [], [], [], [], []
    mult4: list[float] = []
    where_hist: dict[str, int] = {}
    got = 0

    maxv = float((1 << depth) - 1)
    clip_code = 0.996
    crush_code = prof["black_floor_code"]

    for ts in ts_list:
        fr = grab_yuv444p16(path, ts, w, h)
        if fr is None:
            continue
        got += 1
        y, u, v = fr

        y10 = (np.rint(y.astype(np.float64) * 65535.0).astype(np.uint32) >> 6)
        mult4.append(float((y10 & 3 == 0).mean()))

        ire = ire_from_code(y, depth)
        ire_med.append(float(np.median(ire)))
        ire_mean.append(float(ire.mean()))
        clip_p.append(float((y >= clip_code).mean() * 100.0))
        crush_p.append(float((y <= crush_code).mean() * 100.0))
        # Brightest grid cell's median IRE -- cheap proxy for a lit face.
        gh, gw = h // 3, w // 3
        cells = [(float(np.median(ire[gh * r:gh * (r + 1), gw * c:gw * (c + 1)])))
                 for r in range(3) for c in range(3)]
        bright_med.append(max(cells))

        s, where = grid_sharpness(y)
        sharp.append(s)
        where_hist[GRID_NAMES[where[0]][where[1]]] = \
            where_hist.get(GRID_NAMES[where[0]][where[1]], 0) + 1
        ystd = float(y.std())
        acut.append(math.sqrt(s) / ystd if ystd > 1e-9 else 0.0)

        cb_dev.append((float(u.mean()) - 0.5) * 100.0)
        cv_dev.append((float(v.mean()) - 0.5) * 100.0)

    if got == 0:
        rep.add("FAIL", "decode",
                f"Sampled {len(ts_list)} frames and decoded none.",
                "File is unreadable or truncated. Re-copy from the source card "
                "and check the card for errors before reformatting anything.")
        return

    if got < len(ts_list):
        rep.add("WARN", "decode",
                f"Only {got} of {len(ts_list)} sampled frames decoded.",
                "Partial decode usually means a truncated or damaged file. "
                "Re-copy from the card and re-run before trusting these numbers.")

    v = rep.video
    v["frames_sampled"] = got
    v["ire_median"] = round(float(np.mean(ire_med)), 1)
    v["ire_mean"] = round(float(np.mean(ire_mean)), 1)
    v["ire_p10"] = round(float(np.percentile(ire_med, 10)), 1)
    v["ire_p90"] = round(float(np.percentile(ire_med, 90)), 1)
    v["brightest_region_ire"] = round(float(np.mean(bright_med)), 1)
    v["clip_pct"] = round(float(np.mean(clip_p)), 3)
    v["clip_pct_worst"] = round(float(np.max(clip_p)), 3)
    v["crush_pct"] = round(float(np.mean(crush_p)), 3)
    v["crush_pct_worst"] = round(float(np.max(crush_p)), 3)
    v["sharpness"] = round(float(np.mean(sharp)) * 1e6, 2)
    v["acutance"] = round(float(np.mean(acut)), 5)
    v["sharpness_cv"] = round(
        float(np.std(sharp) / np.mean(sharp)) if np.mean(sharp) > 0 else 0.0, 3)
    _cv_s, _n_tr, _edge = settled_sharpness(sharp)
    v["sharpness_cv_settled"] = round(_cv_s, 3)
    v["focus_transition_frames"] = _n_tr
    v["focus_dips_edge_only"] = _edge
    v["sharpest_region"] = max(where_hist, key=where_hist.get) if where_hist else "?"
    v["cb_dev_pct"] = round(float(np.mean(cb_dev)), 2)
    v["cr_dev_pct"] = round(float(np.mean(cv_dev)), 2)

    # ---- is the CONTENT really 10-bit, or 8-bit in a 10-bit wrapper? -------
    if depth >= 10 and mult4:
        util = float(np.mean(mult4))
        v["code_multiples_of_4_frac"] = round(util, 3)
        if util >= TH["bitdepth_util_warn"]:
            rep.add("WARN", "bit depth (content)",
                    f"{util * 100:.0f}% of luma code values are multiples of 4, "
                    f"against the 25% expected of true {depth}-bit — the "
                    f"signature of an 8-bit source promoted into a {depth}-bit "
                    f"container.",
                    "The file is a 10-bit wrapper but the camera may only have "
                    "sent 8 bits, and pix_fmt cannot tell you that. On the Sony, "
                    "HDMI colour depth follows the Record Setting -- confirm it "
                    "is a 4:2:2 10-bit setting and that the HDMI output option "
                    "says 10bit. 8-bit S-Log3 bands in skin and on smooth walls "
                    "and cannot be recovered. Note compression dilutes this "
                    "measure, so anything well above 25% is suspicious even if "
                    "it is nowhere near 100%.")
        else:
            rep.add("PASS", "bit depth (content)",
                    f"{util * 100:.0f}% of luma codes are multiples of 4, "
                    f"matching the 25% expected of true {depth}-bit — the source "
                    f"really was {depth}-bit, not 8-bit in a {depth}-bit wrapper.")

    # ---- clipping ----------------------------------------------------------
    cp = v["clip_pct_worst"]
    if cp >= TH["clip_pct_fail"]:
        rep.add("FAIL", "highlight clipping",
                f"{cp:.2f}% of pixels at the top of the code range "
                f"(worst sampled frame). Unrecoverable.",
                "Close down one stop, or flag/ND the window. On the Sony that is "
                "f/4 -> f/5.6, NOT a lower ISO (below base ISO 800 costs "
                "highlight latitude). On the Osmo, add ND.")
    elif cp >= TH["clip_pct_warn"]:
        rep.add("WARN", "highlight clipping",
                f"{cp:.2f}% of pixels at the top of the code range.",
                "Usually a window or a forehead specular. Check whether it is on "
                "a face; on a background it may be acceptable. Reduce a third to "
                "half a stop if it is on skin.")
    else:
        rep.add("PASS", "highlight clipping", f"{cp:.2f}% clipped.")

    # ---- shadow crush ------------------------------------------------------
    xp = v["crush_pct_worst"]
    floor_label = (f"below the {prof['label']} black floor"
                   if rep.profile != "unknown" else "at/near code black")
    if xp >= TH["crush_pct_fail"]:
        rep.add("FAIL", "shadow crush",
                f"{xp:.2f}% of pixels {floor_label}.",
                "If that is a dark background it is fine and you can ignore this. "
                "If it is on a subject, the detail is gone: open up, or add fill. "
                "Look at the frame before acting on this one.")
    elif xp >= TH["crush_pct_warn"]:
        rep.add("WARN", "shadow crush",
                f"{xp:.2f}% of pixels {floor_label}.",
                "Expected on an unlit background. A problem only if it is on a "
                "subject. Verify visually.")
    else:
        rep.add("PASS", "shadow crush", f"{xp:.2f}% crushed.")

    # ---- log exposure level ------------------------------------------------
    lo, hi = prof["skin_ire"]
    br = v["brightest_region_ire"]
    if rep.profile == "unknown":
        rep.add("INFO", "exposure level",
                f"Brightest region median {br:.1f} IRE, frame median "
                f"{v['ire_median']:.1f} IRE. No log profile assumed, so no "
                f"target applied.",
                "Pass --profile slog3 or --profile dlogm to get a verdict.")
    elif br > hi:
        rep.add("WARN", "exposure level",
                f"Brightest region median {br:.1f} IRE, above the "
                f"{prof['label']} window of {lo:.0f}-{hi:.0f} IRE.",
                f"Overexposed for {prof['label']}. Correctly exposed log looks "
                f"FLAT and dull ungraded -- if it looked right on the monitor "
                f"without a LUT, that is the symptom. Close down "
                f"{max(0.3, math.log2(max(br, 1) / hi)):.1f} stop(s).")
    elif br < lo:
        rep.add("WARN", "exposure level",
                f"Brightest region median {br:.1f} IRE, below the "
                f"{prof['label']} window of {lo:.0f}-{hi:.0f} IRE.",
                f"Underexposed for {prof['label']}; noise will show in the grade. "
                f"Open up, or raise key output. Middle grey should sit near "
                f"{prof['mid_grey_ire']:.0f} IRE and skin near {lo:.0f}-{hi:.0f}.")
    else:
        rep.add("PASS", "exposure level",
                f"Brightest region median {br:.1f} IRE, inside the "
                f"{prof['label']} window {lo:.0f}-{hi:.0f} IRE.")

    fm_lo, fm_hi = TH["frame_median_ire_warn"]
    if not (fm_lo <= v["ire_median"] <= fm_hi):
        rep.add("INFO", "frame median",
                f"Whole-frame median {v['ire_median']:.1f} IRE, outside "
                f"{fm_lo:.0f}-{fm_hi:.0f}.",
                "Not necessarily wrong -- a dark suit against a dark wall does "
                "this. Reported so it is not a surprise.")

    # ---- focus -------------------------------------------------------------
    cv = v["sharpness_cv"]
    cv_settled = v["sharpness_cv_settled"]
    n_tr = v["focus_transition_frames"]

    # Every real take contains transitions -- the chart leaves frame, someone
    # sits down, someone walks off -- and each dips the sharpness, so a single
    # whole-clip CV WARNs on good footage. The fix is NOT to reclassify: an
    # earlier attempt scored where the dips sat and downgraded "edge-only" cases
    # to INFO, and it flipped verdict depending on which 18 frames got sampled,
    # because an encoder smears ramp frames across a transition. A coin-flip
    # guarding the one failure that cannot be caught from in front of the camera
    # is worse than a warning that needs reading.
    #
    # So the severity is untouched and BOTH numbers are printed. "46% overall,
    # 4% over the steadiest run of 12" is readable at a glance and needs no
    # re-derivation on site, which is the actual requirement.
    if cv >= TH["focus_cv_fail"]:
        rep.add("FAIL", "focus stability",
                f"Sharpness varies {cv * 100:.0f}% across {got} sampled frames"
                f"{f' ({cv_settled * 100:.0f}% over the steadiest run of '
                    f'{got - n_tr})' if n_tr else ''}.",
                "This is the signature of autofocus hunting mid-take -- the exact "
                "failure you cannot see from in front of the camera. Lower AF "
                "subject-shift sensitivity, slow AF transition speed, register "
                "the interviewee's face, or switch to manual focus with a marked "
                "distance and ask them not to lean.")
    elif cv >= TH["focus_cv_warn"]:
        rep.add("WARN", "focus stability",
                f"Sharpness varies {cv * 100:.0f}% across {got} sampled frames"
                f"{f' ({cv_settled * 100:.0f}% over the steadiest run of '
                    f'{got - n_tr})' if n_tr else ''}.",
                "Could be autofocus breathing, or could be the subject moving in "
                "and out of a shallow depth of field. Watch the clip at 100% "
                "before dismissing it. Stopping down adds margin.")
    else:
        rep.add("PASS", "focus stability",
                f"Sharpness varies {cv * 100:.0f}% across {got} frames"
                f"{f' ({cv_settled * 100:.0f}% over the steadiest run of '
                    f'{got - n_tr})' if n_tr else ''}.")

    if v["acutance"] < TH["focus_acutance_floor"]:
        rep.add("WARN", "focus (absolute)",
                f"Acutance {v['acutance']:.4f} is below the floor "
                f"{TH['focus_acutance_floor']:.4f} -- little fine detail anywhere "
                f"in frame.",
                "LOW CONFIDENCE. This metric cannot tell soft focus from a "
                "legitimately soft image (heavy diffusion, black mist filter, "
                "shallow depth of field, or a plain wall filling the frame). "
                "Confirm by eye at 100% before changing anything.")
    else:
        rep.add("INFO", "focus (absolute)",
                f"Acutance {v['acutance']:.4f}; sharpest region most often "
                f"{v['sharpest_region']}.",
                "Sanity-check that region is where the face actually is. This "
                "metric cannot distinguish soft focus from shallow depth of field.")

    # ---- colour cast -------------------------------------------------------
    dev = max(abs(v["cb_dev_pct"]), abs(v["cr_dev_pct"]))
    desc = []
    if abs(v["cb_dev_pct"]) > 0.5:
        desc.append("cooler/bluer" if v["cb_dev_pct"] > 0 else "warmer/yellower")
    if abs(v["cr_dev_pct"]) > 0.5:
        desc.append("redder/magenta" if v["cr_dev_pct"] > 0 else "greener")
    desc_s = ", ".join(desc) if desc else "neutral"
    if dev > TH["chroma_dev_warn_pct"]:
        rep.add("WARN", "colour cast",
                f"Frame-average chroma is {dev:.1f}% off neutral "
                f"(Cb {v['cb_dev_pct']:+.1f}%, Cr {v['cr_dev_pct']:+.1f}%) -- "
                f"reads {desc_s}.",
                "Could be a real white-balance error, a filter cast (a budget "
                "10-stop ND is a common culprit and it ships in the delivered "
                "file), or just a strongly coloured scene. Shoot the "
                "ColorChecker through the same filter stack to tell which.")
    else:
        rep.add("INFO", "colour cast",
                f"Frame-average chroma {dev:.1f}% off neutral "
                f"(Cb {v['cb_dev_pct']:+.1f}%, Cr {v['cr_dev_pct']:+.1f}%) -- "
                f"{desc_s}.",
                "Weak metric: a red shirt or a blue wall moves it. Use the "
                "ColorChecker frames for an actual white-balance judgement.")


# ===========================================================================
#  audio
# ===========================================================================

def parse_astats(err: str) -> dict[str, dict[str, float]]:
    """
    Parse ffmpeg astats text output into {'overall': {...}, '1': {...}, ...}.

    Per-channel matters here: with the Rode receiver in split mode, channel 1
    and channel 2 are two different people. One dead channel is a lost
    interview subject, and it does not show up in a summed measurement.
    """
    out: dict[str, dict[str, float]] = {}
    cur: str | None = None
    for line in err.splitlines():
        line = re.sub(r"^\[[^\]]*\]\s*", "", line).strip()
        m = re.match(r"^Channel:\s*(\d+)$", line)
        if m:
            cur = m.group(1)
            out.setdefault(cur, {})
            continue
        if re.match(r"^Overall$", line):
            cur = "overall"
            out.setdefault(cur, {})
            continue
        if cur is None:
            continue
        m = re.match(r"^([A-Za-z][A-Za-z0-9 _.\-/]*?):\s*(.+)$", line)
        if not m:
            continue
        key = m.group(1).strip().lower()
        raw = m.group(2).strip()
        if key == "bit depth":
            # The number of fields here has changed between ffmpeg releases:
            # ffmpeg 8 prints four ("22/24/24/24" for a 24-bit file whose low
            # bits are not fully exercised). Reading the first field as "the"
            # bit depth reports a real 24-bit file as 22-bit. Take the min as
            # significant bits actually used and the max as the format depth.
            bd = [float(x) for x in re.findall(r"\d+", raw)]
            if bd:
                out[cur]["bit_depth_significant"] = min(bd)
                out[cur]["bit_depth_format"] = max(bd)
            continue
        try:
            out[cur][key] = float(raw.split()[0])
        except (ValueError, IndexError):
            pass
    return out


def parse_ebur128(err: str) -> dict[str, float]:
    res: dict[str, float] = {}
    tail = err[-4000:]
    for key, pat in (
        ("lufs_i", r"I:\s*(-?[0-9.]+)\s*LUFS"),
        ("lra", r"LRA:\s*(-?[0-9.]+)\s*LU"),
        ("lra_low", r"LRA low:\s*(-?[0-9.]+)\s*LUFS"),
        ("lra_high", r"LRA high:\s*(-?[0-9.]+)\s*LUFS"),
        ("true_peak", r"Peak:\s*(-?[0-9.]+)\s*dBFS"),
    ):
        m = re.findall(pat, tail)
        if m:
            try:
                res[key] = float(m[-1])
            except ValueError:
                pass
    return res


def analyse_audio(rep: FileReport, path: str, astream: dict[str, Any] | None,
                  stream_index: int = 0) -> None:
    if astream is None:
        if rep.kind == "video":
            rep.add("FAIL", "audio stream",
                    "Video file has NO audio stream.",
                    "If this is the Sony, the receiver was not plugged in or the "
                    "camera audio was disabled -- you have lost the on-camera "
                    "reference track and the sync guide. Check the 3.5mm jack is "
                    "seated and audio recording is enabled.")
        else:
            rep.add("FAIL", "audio stream", "No audio stream found in file.",
                    "File may be truncated or the wrong file.")
        return

    a = rep.audio
    sr = int(astream.get("sample_rate") or 0)
    ch = int(astream.get("channels") or 0)
    a["codec"] = astream.get("codec_name")
    a["sample_rate"] = sr
    a["channels"] = ch
    a["sample_fmt"] = astream.get("sample_fmt")
    bps = astream.get("bits_per_raw_sample") or astream.get("bits_per_sample")
    try:
        a["bit_depth_container"] = int(bps) if bps and int(bps) > 0 else None
    except (TypeError, ValueError):
        a["bit_depth_container"] = None

    if sr and sr != TH["audio_sample_rate_expect"]:
        rep.add("WARN", "sample rate",
                f"{sr} Hz; expected {TH['audio_sample_rate_expect']} Hz.",
                "Resampling is lossy and annoying for the editor. Set 48 kHz on "
                "the device.")
    elif sr:
        rep.add("PASS", "sample rate", f"{sr} Hz.")

    rc, _, err = run([
        "ffmpeg", "-hide_banner", "-loglevel", "info", "-i", path,
        "-map", f"0:a:{stream_index}",
        "-af", "astats=metadata=1:reset=0", "-f", "null", "-",
    ], timeout=1800)
    stats = parse_astats(err)
    if not stats:
        rep.add("SKIP", "audio levels",
                "astats produced no parseable output; level checks skipped.",
                "Check ffmpeg is a recent build and the audio stream decodes.")
        return

    ov = stats.get("overall", {})
    chans = {k: v for k, v in stats.items() if k != "overall"}

    peak = ov.get("peak level db")
    rms = ov.get("rms level db")
    nf = ov.get("noise floor db")
    dr = ov.get("dynamic range")
    dc = ov.get("dc offset")
    flat = ov.get("flat factor")
    pkcount = ov.get("peak count")
    bd_sig = ov.get("bit_depth_significant")
    bd_fmt = ov.get("bit_depth_format")

    # ffmpeg 8's astats no longer prints "Dynamic range", so derive the
    # peak-to-noise figure ourselves rather than silently dropping the check.
    dr_derived = False
    if dr is None and finite(peak) is not None and finite(nf) is not None:
        dr = peak - nf
        dr_derived = True

    peak, rms, nf, dr = (finite(peak), finite(rms), finite(nf), finite(dr))
    for k, val in (("peak_dbfs", peak), ("rms_dbfs", rms),
                   ("noise_floor_dbfs", nf), ("dynamic_range_db", dr),
                   ("dc_offset", dc), ("flat_factor", flat),
                   ("peak_count", pkcount),
                   ("bit_depth_significant", bd_sig),
                   ("bit_depth_format", bd_fmt)):
        if val is not None:
            a[k] = round(val, 3)

    tx = (rep.role == "transmitter")
    rm = (rep.role == "roommic")
    warn_below = (TH["tx_peak_warn_below"] if tx else
                  TH["roommic_peak_warn_below"] if rm else
                  TH["audio_peak_warn_below"])
    fail_below = TH["tx_peak_fail_below"] if tx else TH["audio_peak_fail_below"]
    nf_warn = (TH["tx_noise_floor_warn"] if tx else
               TH["roommic_noise_floor_warn"] if rm else
               TH["audio_noise_floor_warn"])
    dr_warn = (TH["roommic_dynamic_range_warn"] if rm
               else TH["audio_dynamic_range_warn"])

    # ---- peak level --------------------------------------------------------
    if peak is None:
        rep.add("SKIP", "peak level", "Peak level not reported.")
    elif peak >= TH["audio_peak_fail_above"]:
        rep.add("FAIL", "peak level",
                f"Peak {peak:.1f} dBFS -- at or above full scale. Clipped.",
                "Distortion is baked in and cannot be repaired. Lower the "
                "transmitter gain (or the camera's manual record level) and "
                "re-take. Target peaks near -12 dBFS. The transmitters' internal "
                "WAV may have escaped this -- check those files too.")
    elif peak > TH["audio_peak_warn_above"]:
        rep.add("WARN", "peak level",
                f"Peak {peak:.1f} dBFS -- hotter than the -12 dBFS target.",
                "No headroom for a laugh or a raised voice. Drop the gain "
                "3-6 dB.")
    elif peak < fail_below:
        rep.add("FAIL", "peak level",
                f"Peak {peak:.1f} dBFS -- effectively silent.",
                "Dead channel. Transmitter off, muted, unpaired, lav unplugged, "
                "or the receiver output cable is not seated. Check before "
                "anything else.")
    elif peak < warn_below:
        rep.add("WARN", "peak level",
                f"Peak {peak:.1f} dBFS -- under-recorded even for a "
                f"transmitter's unprocessed recording." if tx else
                f"Peak {peak:.1f} dBFS -- under-recorded.",
                "Move the lav closer to the mouth, roughly a hand's width below "
                "the collarbone." if tx else
                "Raising this in post raises the noise floor with it. Increase "
                "transmitter gain to land peaks near -12 dBFS.")
    elif tx:
        rep.add("PASS", "peak level",
                f"Peak {peak:.1f} dBFS -- normal for a transmitter's "
                f"gain-free internal recording.",
                "Rode applies no gain to the onboard recording, so these files "
                "are meant to be quiet. Tell the post house so a low level is "
                "not read as a bad take.")
    else:
        rep.add("PASS", "peak level", f"Peak {peak:.1f} dBFS.")

    # ---- noise floor / dynamic range ---------------------------------------
    if nf is not None:
        if nf > nf_warn:
            rep.add("WARN", "noise floor",
                    f"Noise floor {nf:.1f} dBFS -- above "
                    f"{nf_warn:.0f} dBFS.",
                    "Audible hiss or room tone. Usual causes: HVAC, transmitter "
                    "gain too high compensating for a quiet lav, or fabric "
                    "rustle. Ask for the air handling to be switched off during "
                    "takes and reposition the lav.")
        else:
            rep.add("PASS", "noise floor", f"Noise floor {nf:.1f} dBFS.")

    if dr is not None:
        src = " (derived: peak minus noise floor)" if dr_derived else ""
        if dr < dr_warn:
            rep.add("WARN", "dynamic range",
                    f"Peak-to-noise {dr:.1f} dB{src} -- under "
                    f"{dr_warn:.0f} dB.",
                    "Speech sits close to the noise. Either the source is too "
                    "quiet (move the lav closer to the mouth, roughly a hand's "
                    "width below the collarbone) or the room is too loud.")
        else:
            rep.add("PASS", "dynamic range", f"Peak-to-noise {dr:.1f} dB{src}.")

    # ---- DC offset / flat samples ------------------------------------------
    if dc is not None:
        if abs(dc) > TH["audio_dc_offset_warn"]:
            rep.add("WARN", "DC offset",
                    f"DC offset {dc:+.4f} of full scale.",
                    "Wastes headroom and thumps on edits. A high-pass filter in "
                    "post fixes it; mention it to the post house.")
        else:
            rep.add("PASS", "DC offset", f"DC offset {dc:+.4f}.")

    if flat is not None:
        if flat > TH["audio_flat_factor_warn"]:
            rep.add("WARN", "flat samples",
                    f"Flat factor {flat:.2f} (consecutive identical samples "
                    f"detected, peak count {pkcount:.0f}).",
                    "Either clipping flattening waveform tops, or a wireless "
                    "dropout muting the channel. Listen at the loudest and "
                    "quietest points to tell which. If it is dropout, shorten "
                    "the transmitter-to-receiver path or move off a congested "
                    "2.4 GHz band.")
        else:
            rep.add("PASS", "flat samples", f"Flat factor {flat:.2f}.")

    # ---- bit depth ---------------------------------------------------------
    # ffprobe's container depth is the authoritative answer to "is this a
    # 24-bit file"; astats' significant-bit count is a softer signal and its
    # output format has changed between ffmpeg releases. Prefer the former.
    depth = a.get("bit_depth_container") or (int(bd_fmt) if bd_fmt else None)
    sig_note = (f" ({bd_sig:.0f} bits carry signal)"
                if bd_sig is not None and depth and bd_sig < depth - 0.5 else "")
    lossy = (a.get("codec") or "").lower() in ("aac", "mp3", "opus", "vorbis", "ac3")

    if lossy:
        rep.add("INFO", "audio bit depth",
                f"Lossy codec ({a.get('codec')}); bit depth not meaningful.",
                "Fine for a scratch/reference track. The deliverable audio is "
                "the uncompressed WAV from the transmitters -- make sure those "
                "exist and are 24-bit/48 kHz.")
    elif depth is None:
        rep.add("SKIP", "audio bit depth", "Could not determine bit depth.")
    elif depth >= TH["audio_bit_depth_expect"]:
        rep.add("PASS", "audio bit depth", f"{depth}-bit{sig_note}.")
    elif rep.kind == "video":
        # An embedded camera track at 16-bit LPCM is normal and not a defect.
        rep.add("INFO", "audio bit depth",
                f"{depth}-bit camera track{sig_note}.",
                "Normal for an in-camera audio track. The 24-bit requirement "
                "applies to the transmitters' own WAV files, not this one.")
    else:
        lvl = "WARN" if depth >= 16 else "FAIL"
        rep.add(lvl, "audio bit depth",
                f"{depth}-bit standalone audio file{sig_note}; expected "
                f"{TH['audio_bit_depth_expect']}-bit.",
                "This is meant to be a max-quality WAV from a transmitter, so "
                "the device is in a lower-quality mode. Set it to uncompressed "
                "24-bit / 48 kHz and re-record. The post house asked for "
                "max-bitrate WAV.")

    # ---- per channel -------------------------------------------------------
    if len(chans) >= 2:
        per = {}
        for cid, cs in sorted(chans.items(), key=lambda kv: int(kv[0])):
            per[cid] = {
                k: (round(v, 2) if v is not None else None) for k, v in (
                    ("peak_dbfs", finite(cs.get("peak level db"))),
                    ("rms_dbfs", finite(cs.get("rms level db"))),
                    ("noise_floor_dbfs", finite(cs.get("noise floor db"))),
                )}
        a["per_channel"] = per
        peaks = {cid: cs.get("peak level db") for cid, cs in chans.items()
                 if cs.get("peak level db") is not None}
        if peaks:
            dead = sorted((c for c, p in peaks.items()
                           if p < TH["audio_peak_fail_below"]), key=int)
            active = sorted((c for c in peaks if c not in dead), key=int)
            a["active_channels"] = active
            if not active:
                fin = [p for p in peaks.values() if math.isfinite(p)]
                rng = (f"peaks {min(fin):.1f} to {max(fin):.1f} dBFS"
                       if fin else "digital silence, no signal at all")
                rep.add("FAIL", "channel balance",
                        f"ALL {len(peaks)} channels are effectively silent "
                        f"({rng}). No audio source is reaching the recorder.",
                        "If this is a picture-only test with no microphone "
                        "connected, that is expected and you can ignore it. On a "
                        "real take it means the receiver is unplugged, powered "
                        "off, or muted, or the cable is not seated -- check that "
                        "before anything else.")
            else:
                # Empty channels are NORMAL when a stereo source is embedded into
                # a 4-channel HDMI stream -- an A7C R puts its analogue input on
                # channels 3/4 and leaves 1/2 empty. Reporting that as a dead
                # transmitter is a false alarm on every single recording.
                if dead:
                    rep.add("INFO", "unused channels",
                            f"Channel(s) {', '.join(dead)} carry no signal; "
                            f"channel(s) {', '.join(active)} do.",
                            "Normal when a stereo source is embedded in a "
                            "4-channel HDMI stream — the Sony puts its 3.5mm "
                            "input on channels 3/4 and leaves 1/2 empty. Tell "
                            "the post house which pair the voices are on. Only a "
                            "gap WITHIN the pair carrying audio means a "
                            "transmitter is down.")
                if len(active) == 1:
                    rep.add("FAIL", "channel balance",
                            f"Only channel {active[0]} carries signal. If two "
                            f"transmitters were miked, one is not reaching the "
                            f"receiver.",
                            "Check pairing, mute, battery and lav connection on "
                            "the missing transmitter. In SPLIT mode that is one "
                            "interview subject with no camera track.")
                else:
                    live = {c: peaks[c] for c in active}
                    spread = max(live.values()) - min(live.values())
                    a["channel_peak_spread_db"] = round(spread, 2)
                    detail = ", ".join(f"ch{c}={live[c]:.1f}" for c in active)
                    if spread > 12.0:
                        rep.add("WARN", "channel balance",
                                f"{spread:.1f} dB peak difference between live "
                                f"channels ({detail}).",
                                "Expected if the two speakers differ in volume "
                                "or in mic distance, but a large gap usually "
                                "means mismatched transmitter gain. Match the "
                                "gain on both transmitters.")
                    else:
                        rep.add("PASS", "channel balance",
                                f"{spread:.1f} dB peak spread across live "
                                f"channels ({detail}).")

        # Identical channels = dual-mono, i.e. NOT split routing.
        # Compare the two channels that actually CARRY SIGNAL, not channels 1
        # and 2: an A7C R embeds its analogue input on HDMI channels 3/4, so a
        # hard-coded 1/2 comparison would test two empty channels and pass.
        live_ids = sorted(a.get("active_channels", []),
                          key=lambda c: peaks.get(c, -999), reverse=True)[:2]
        if len(live_ids) == 2:
            a["routing_channels_compared"] = sorted(live_ids, key=int)
            c1, c2 = [chans[k] for k in sorted(live_ids, key=int)]
            same = all(
                c1.get(k) is not None and c2.get(k) is not None
                and abs(c1[k] - c2[k]) < 0.01
                for k in ("peak level db", "rms level db")
            )
            if same:
                rep.add("WARN", "channel routing",
                        f"Channels {' and '.join(sorted(live_ids, key=int))} "
                        f"measure identically -- this is dual-mono, not two "
                        f"separate sources.",
                        "If two people were miked into one receiver, it is in "
                        "MERGED mode and post cannot separate the voices. Switch "
                        "the receiver to SPLIT so transmitter 1 goes to the left "
                        "channel and transmitter 2 to the right, then re-test.")

    # ---- loudness ----------------------------------------------------------
    rc2, _, err2 = run([
        "ffmpeg", "-hide_banner", "-loglevel", "info", "-i", path,
        "-map", f"0:a:{stream_index}",
        "-af", "ebur128=peak=true", "-f", "null", "-",
    ], timeout=1800)
    eb = parse_ebur128(err2)
    if not eb:
        rep.add("SKIP", "loudness", "ebur128 produced no summary; skipped.")
    else:
        a.update({k: round(v, 2) for k, v in eb.items()})
        i = eb.get("lufs_i")
        # Integrated loudness is a PROGRAM measure for a finished mix. EVERY raw
        # production take reads low, so a "too quiet" LUFS warning fires on
        # correctly configured files regardless of role -- which only teaches you
        # to ignore the tool. Peak level and noise floor do the real judging.
        # A too-HOT reading is still worth flagging: it corroborates over-level.
        if i is not None and i > -70:
            if i > TH["lufs_warn_above"]:
                rep.add("WARN", "loudness",
                        f"Integrated {i:.1f} LUFS -- hot.",
                        "Fine for a delivery master, hot for a camera-original "
                        "file that still has to be mixed. Leave headroom.")
            else:
                # Integrated loudness is a PROGRAM measure for a finished mix.
                # A camera-original take full of pauses reads very low and that
                # is correct, so warning on it fires on properly configured
                # files -- which just teaches you to ignore the tool.
                # Peak level and noise floor are the meaningful checks here.
                rep.add("INFO", "loudness",
                        f"Integrated {i:.1f} LUFS, loudness range "
                        f"{eb.get('lra', float('nan')):.1f} LU.",
                        "Informational only on camera-original material -- a raw "
                        "take with pauses reads low by nature. Judge the level "
                        "by peak and noise floor, not by LUFS. Mixing to a "
                        "loudness target is the post house's job.")

        tp = eb.get("true_peak")
        if tp is not None and tp > -0.5:
            rep.add("WARN", "true peak",
                    f"True peak {tp:.1f} dBFS -- inter-sample peaks at or over "
                    f"full scale.",
                    "Will distort on some playback chains even if sample peaks "
                    "look legal. Reduce gain.")


# ===========================================================================
#  per-file driver
# ===========================================================================

def analyse_file(path: str, role_override: str | None,
                 profile_override: str | None, deep: bool = False) -> FileReport:
    rep = FileReport(path=path, name=os.path.basename(path))

    if not os.path.isfile(path):
        rep.error = "not a file"
        rep.add("FAIL", "file", f"{path} does not exist or is not a file.",
                "Check the path.")
        return rep

    size = os.path.getsize(path)
    rep.stream["size_bytes"] = size
    if size == 0:
        rep.error = "empty file"
        rep.add("FAIL", "file", "File is zero bytes.",
                "Recording never started or the card was pulled while writing. "
                "Do not reformat the card -- it may be recoverable.")
        return rep

    info = ffprobe_json(path)
    if info is None:
        rep.error = "unparseable"
        rep.add("FAIL", "container",
                "ffprobe could not read this file.",
                "Truncated, corrupt, or not a media file. If it came off a card, "
                "re-copy it with a checksummed clone before reformatting "
                "anything.")
        return rep

    streams = info.get("streams") or []
    fmt = info.get("format") or {}
    vs = next((s for s in streams if s.get("codec_type") == "video"), None)
    aud = [s for s in streams if s.get("codec_type") == "audio"]
    a0 = aud[0] if aud else None

    ext = os.path.splitext(path)[1].lower()
    rep.kind = "video" if vs is not None else ("audio" if a0 is not None else "unknown")
    rep.role = role_override or guess_role(path, vs)
    rep.profile = guess_profile(rep.role, profile_override) if rep.kind == "video" \
        else "unknown"

    try:
        duration = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0 and vs is not None:
        try:
            duration = float(vs.get("duration") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0

    rep.stream.update({
        "container": fmt.get("format_name"),
        "duration_s": round(duration, 2) if duration else None,
        "size_human": human_bytes(size),
        "n_audio_streams": len(aud),
    })
    try:
        rep.stream["bitrate_mbps"] = round(float(fmt.get("bit_rate")) / 1e6, 1)
    except (TypeError, ValueError):
        rep.stream["bitrate_mbps"] = None

    if duration and duration < TH["duration_min_s"]:
        rep.add("WARN", "duration",
                f"Only {duration:.1f} s long.",
                "Too short for the statistics below to mean much. Shoot a longer "
                "test -- 30 s minimum, and long enough to include real speech.")

    # ---------------- video stream checks --------------------------------
    if vs is not None:
        w = int(vs.get("width") or 0)
        h = int(vs.get("height") or 0)
        pf = vs.get("pix_fmt")
        probed = False
        if not pf:
            pf = probe_decoded_pix_fmt(path)
            probed = True
        depth, sub = parse_pix_fmt(pf)
        r_fps = parse_fps(vs.get("r_frame_rate"))
        avg_fps = parse_fps(vs.get("avg_frame_rate"))
        fps = avg_fps or r_fps
        fam, exact = classify_fps(fps)
        rot = detect_rotation(vs)

        rep.stream.update({
            "codec": vs.get("codec_name"),
            "profile": vs.get("profile"),
            "width": w, "height": h,
            "pix_fmt": pf, "pix_fmt_probed": probed,
            "bit_depth": depth, "chroma": sub,
            "fps": float(fps) if fps else None,
            "fps_rational": str(fps) if fps else None,
            "fps_family": fam,
            "rotation": rot,
            "color_primaries": vs.get("color_primaries"),
            "color_transfer": vs.get("color_transfer"),
            "color_space": vs.get("color_space"),
            "color_range": vs.get("color_range"),
        })
        try:
            rep.stream["video_bitrate_mbps"] = round(float(vs.get("bit_rate")) / 1e6, 1)
        except (TypeError, ValueError):
            rep.stream["video_bitrate_mbps"] = rep.stream.get("bitrate_mbps")

        ew, eh = TH["resolution_expect"]
        if (w, h) == (ew, eh):
            rep.add("PASS", "resolution", f"{w}x{h}.")
        elif (w, h) == (eh, ew):
            rep.add("FAIL", "resolution",
                    f"{w}x{h} -- portrait/vertical.",
                    "The camera was in vertical mode. Reshoot in horizontal. "
                    "This cannot be fixed without cropping away most of the "
                    "frame.")
        else:
            rep.add("FAIL", "resolution",
                    f"{w}x{h}; expected {ew}x{eh}.",
                    f"Set the camera to {ew}x{eh}. Delivering a mismatched "
                    f"resolution forces the post house to scale one angle.")

        if rot % 360 != 0:
            rep.add("FAIL", "rotation",
                    f"Rotation metadata {rot}deg is set.",
                    "The camera recorded rotated. Some NLEs honour this flag and "
                    "some ignore it, so the two angles may not agree. Check the "
                    "camera was horizontal and its orientation lock was on.")
        else:
            rep.add("PASS", "rotation", "No rotation flag.")

        if fps is None:
            rep.add("FAIL", "frame rate", "Could not determine frame rate.",
                    "File is likely damaged.")
        elif fam is None:
            rep.add("FAIL", "frame rate",
                    f"{fmt_fps(fps)} fps -- not in the 30p family.",
                    "The post house asked for 30. Set the camera to 30p. "
                    "Mixing frame rates between angles is the single most "
                    "expensive mistake in this shoot.")
        else:
            note = "" if exact else " (approximate match)"
            rep.add("INFO", "frame rate",
                    f"{fmt_fps(fps)} fps -- {fam}{note}.",
                    "29.97 and 30.000 are BOTH called '30p' and are NOT "
                    "interchangeable: mixed, they drift about one frame every "
                    "33 seconds. Both cameras must write the same one. See the "
                    "cross-camera section.")

        if depth is None:
            rep.add("SKIP", "video bit depth",
                    "Could not determine bit depth (pix_fmt unavailable).")
        elif depth < TH["bit_depth_min"]:
            rep.add("FAIL", "video bit depth",
                    f"{depth}-bit ({pf}).",
                    f"{depth}-bit log grades badly -- banding shows in skin and "
                    f"in smooth walls, and it cannot be recovered. Set the "
                    f"camera to {TH['bit_depth_min']}-bit.")
        else:
            rep.add("PASS", "video bit depth", f"{depth}-bit ({pf}).")

        expect_sub = {"tight": "4:2:2", "prores": "4:2:2", "wide": "4:2:0"}.get(rep.role)
        if sub is None:
            rep.add("SKIP", "chroma subsampling", "Could not determine.")
        elif expect_sub is None:
            rep.add("INFO", "chroma subsampling", f"{sub}.")
        elif sub == expect_sub:
            rep.add("PASS", "chroma subsampling", f"{sub} (expected for {rep.role}).")
        elif expect_sub == "4:2:2" and sub == "4:2:0":
            rep.add("WARN", "chroma subsampling",
                    f"{sub}; expected {expect_sub} for the {rep.role} camera.",
                    "4:2:0 holds up less well to keying and heavy grading. On "
                    "the Sony, XAVC S-I / XAVC S 10-bit gives 4:2:2 -- check the "
                    "file format setting.")
        else:
            rep.add("INFO", "chroma subsampling",
                    f"{sub}; expected {expect_sub} for the {rep.role} camera.")

        vb = rep.stream.get("video_bitrate_mbps")
        floor = TH["bitrate_floor_mbps"].get(rep.role, TH["bitrate_floor_mbps"]["unknown"])
        if vb is None:
            rep.add("SKIP", "bitrate", "Bitrate not reported.")
        elif vb < floor:
            rep.add("WARN", "bitrate",
                    f"{vb:.1f} Mbps, below the {floor:.0f} Mbps floor for the "
                    f"{rep.role} camera.",
                    "Suggests a lower-quality codec setting than intended "
                    "(Long GOP instead of All-Intra, or a proxy/low-bitrate "
                    "mode). Check the file format setting.")
        else:
            rep.add("PASS", "bitrate", f"{vb:.1f} Mbps.")

        ct = (vs.get("color_transfer") or "").lower()
        if ct in ("arib-std-b67", "smpte2084"):
            rep.add("WARN", "colour transfer",
                    f"Transfer characteristic tagged '{ct}' (HLG/PQ), not log.",
                    "If you meant to shoot S-Log3, the picture profile is wrong "
                    "and the file is HDR-tagged. Check the Picture Profile.")
        elif ct:
            rep.add("INFO", "colour transfer", f"Tagged '{ct}'.")

        check_timing(rep, path, fps, duration, deep)

        if duration >= TH["duration_min_s"]:
            analyse_video(rep, path, vs, duration, depth or 10)
        else:
            rep.add("SKIP", "pixels", "Clip too short for pixel analysis.")

    # ---------------- audio ------------------------------------------------
    if rep.kind == "video" and len(aud) > 1:
        rep.add("INFO", "audio streams",
                f"{len(aud)} audio streams present; analysing the first only.")
    analyse_audio(rep, path, a0)

    return rep


# ===========================================================================
#  cross-file checks — the expensive errors live here
# ===========================================================================

def cross_check(reports: list[FileReport]) -> list[Finding]:
    out: list[Finding] = []
    vids = [r for r in reports
            if r.kind == "video" and not r.error and r.stream.get("fps_family")]

    if len(vids) < 2:
        out.append(Finding(
            "INFO", "cross-camera",
            f"Only {len(vids)} analysable video file(s); cross-camera checks "
            f"need at least two.",
            "Run the tool on a clip from BOTH cameras at once. Frame-rate "
            "mismatch between angles is the error that costs the most in post, "
            "and it can only be caught by comparing files."))
        return out

    # ---- frame rate: hard fail on mismatch ---------------------------------
    fams: dict[str, list[str]] = {}
    for r in vids:
        fams.setdefault(r.stream["fps_family"], []).append(f"{r.name} ({r.role})")
    if len(fams) > 1:
        desc = "; ".join(f"{k}: {', '.join(v)}" for k, v in sorted(fams.items()))
        out.append(Finding(
            "FAIL", "cross-camera frame rate",
            f"Angles are at DIFFERENT frame rates -- {desc}.",
            "This is the expensive one. 29.97 and 30.000 drift about one frame "
            "every 33 seconds, so over a 40-minute interview the two angles end "
            "up roughly 70 frames apart and no single sync point fixes it. "
            "Decide which rate to use, set BOTH cameras to it, and re-test. If "
            "one camera cannot be changed, match the other to it -- agreement "
            "matters more than which number you pick."))
    else:
        fam = next(iter(fams))
        rationals = {r.stream.get("fps_rational") for r in vids}
        if len(rationals) > 1:
            out.append(Finding(
                "WARN", "cross-camera frame rate",
                f"All angles read as {fam} but the exact rationals differ "
                f"({', '.join(sorted(str(x) for x in rationals))}).",
                "Probably variable frame rate or a container rounding "
                "difference. Ask the post house to confirm they can conform "
                "these, and mention it explicitly when you ship."))
        else:
            out.append(Finding("PASS", "cross-camera frame rate",
                               f"All {len(vids)} angles at {fam}."))

    # ---- resolution --------------------------------------------------------
    res = {(r.stream.get("width"), r.stream.get("height")) for r in vids}
    if len(res) > 1:
        detail = ", ".join(f"{r.name}={r.stream.get('width')}x{r.stream.get('height')}"
                           for r in vids)
        out.append(Finding(
            "WARN", "cross-camera resolution",
            f"Angles differ in resolution: {detail}.",
            "The post house has to scale one angle to match. Set both cameras "
            "to 3840x2160."))
    else:
        w, h = next(iter(res))
        out.append(Finding("PASS", "cross-camera resolution",
                           f"All angles {w}x{h}."))

    # ---- luma spread -------------------------------------------------------
    lum = {r.name: r.video.get("brightest_region_ire")
           for r in vids if r.video.get("brightest_region_ire") is not None}
    if len(lum) >= 2:
        hi_k = max(lum, key=lum.get)
        lo_k = min(lum, key=lum.get)
        spread = lum[hi_k] - lum[lo_k]
        detail = ", ".join(f"{k}={v:.1f}" for k, v in lum.items())
        if spread >= TH["cross_luma_spread_ire_fail"]:
            out.append(Finding(
                "FAIL", "cross-camera exposure",
                f"{spread:.1f} IRE spread between angles ({detail}).",
                f"Too far apart. Different sensors and different log curves "
                f"never match exactly, but this gap means the colorist is "
                f"rebuilding one angle rather than matching it. Bring "
                f"{lo_k} up toward {hi_k}: on the Osmo that is ISO or removing "
                f"ND; on the Sony it is aperture. Then re-test."))
        elif spread >= TH["cross_luma_spread_ire_warn"]:
            out.append(Finding(
                "WARN", "cross-camera exposure",
                f"{spread:.1f} IRE spread between angles ({detail}).",
                f"Workable but it makes the match harder. Nudge {lo_k} brighter "
                f"if it is easy to do."))
        else:
            out.append(Finding("PASS", "cross-camera exposure",
                               f"{spread:.1f} IRE spread between angles "
                               f"({detail})."))

    # ---- codec/profile inventory ------------------------------------------
    inv = ", ".join(f"{r.name}: {r.role}/{r.stream.get('codec')}/"
                    f"{r.stream.get('bit_depth')}-bit/{r.stream.get('chroma')}"
                    for r in vids)
    out.append(Finding("INFO", "cross-camera inventory", inv,
                       "Confirm the role guesses are right. If a file is "
                       "mislabelled, re-run with --role tight / --role wide so "
                       "the correct expectations apply."))
    return out


# ===========================================================================
#  reporting
# ===========================================================================

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
COLOUR = {"FAIL": "\033[1;31m", "WARN": "\033[1;33m", "PASS": "\033[32m",
          "INFO": "\033[36m", "SKIP": "\033[2m"}


def paint(level: str, use_colour: bool) -> str:
    if not use_colour:
        return f"{level:<4}"
    return f"{COLOUR.get(level, '')}{level:<4}{RESET}"


def wrap(text: str, width: int, indent: str) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for wd in words:
        if cur and len(cur) + 1 + len(wd) > width:
            lines.append(cur)
            cur = wd
        else:
            cur = f"{cur} {wd}" if cur else wd
    if cur:
        lines.append(cur)
    return [indent + ln for ln in lines]


def render_text(reports: list[FileReport], cross: list[Finding],
                verbose: bool, use_colour: bool, width: int = 92) -> str:
    L: list[str] = []
    b = (lambda s: f"{BOLD}{s}{RESET}") if use_colour else (lambda s: s)
    d = (lambda s: f"{DIM}{s}{RESET}") if use_colour else (lambda s: s)

    L.append("=" * width)
    L.append(b("shootcheck — technical QC"))
    L.append(d("Measures only. Says nothing about whether the image looks good, "
               "the framing works,"))
    L.append(d("or the background is right. Look at the footage as well."))
    L.append("=" * width)

    order = sorted(reports, key=lambda r: (LEVEL_ORDER[r.worst], r.name))

    for r in order:
        L.append("")
        hdr = f"{r.name}   [{r.worst}]"
        L.append(b(hdr))
        meta = [f"role={r.role}"]
        if r.kind == "video":
            meta.append(f"profile={PROFILES[r.profile]['label']}")
        meta.append(f"kind={r.kind}")
        s = r.stream
        if s.get("width"):
            meta.append(f"{s['width']}x{s['height']}")
        if s.get("fps_family"):
            meta.append(f"{s['fps_family']} fps")
        if s.get("bit_depth"):
            meta.append(f"{s['bit_depth']}-bit {s.get('chroma') or ''}".strip())
        if s.get("duration_s"):
            meta.append(human_dur(s["duration_s"]))
        if s.get("size_human"):
            meta.append(s["size_human"])
        L.append(d("  " + "  ·  ".join(meta)))
        L.append(d("  " + "-" * (width - 4)))

        fs = sorted(r.findings, key=lambda f: f.sort_key())
        shown = fs if verbose else [f for f in fs
                                    if f.level in ("FAIL", "WARN", "INFO", "SKIP")]
        if not shown:
            shown = [Finding("PASS", "all checks", "All checks passed.")]
        for f in shown:
            L.append(f"  {paint(f.level, use_colour)}  {b(f.check)}: {f.message}")
            if f.fix and f.level in ("FAIL", "WARN", "SKIP", "INFO"):
                L.extend(wrap("FIX: " + f.fix, width - 12, " " * 8))
        if not verbose:
            npass = sum(1 for f in fs if f.level == "PASS")
            if npass:
                L.append(d(f"        ({npass} checks passed, hidden — use -v)"))

        if verbose and r.video:
            L.append(d("  measured (video):"))
            for k in ("frames_sampled", "ire_median", "brightest_region_ire",
                      "clip_pct_worst", "crush_pct_worst", "acutance",
                      "sharpness_cv", "sharpness_cv_settled",
                      "focus_transition_frames", "sharpest_region", "cb_dev_pct",
                      "cr_dev_pct"):
                if k in r.video:
                    L.append(d(f"      {k:<22} {r.video[k]}"))
        if verbose and r.audio:
            L.append(d("  measured (audio):"))
            for k in ("codec", "channels", "sample_rate", "bit_depth_used",
                      "peak_dbfs", "rms_dbfs", "noise_floor_dbfs",
                      "dynamic_range_db", "lufs_i", "lra", "true_peak",
                      "flat_factor", "dc_offset", "channel_peak_spread_db"):
                if k in r.audio:
                    L.append(d(f"      {k:<22} {r.audio[k]}"))
            if "per_channel" in r.audio:
                for cid, cv in r.audio["per_channel"].items():
                    L.append(d(f"      ch{cid:<20} {cv}"))

    L.append("")
    L.append("=" * width)
    L.append(b("CROSS-CAMERA"))
    L.append("=" * width)
    for f in sorted(cross, key=lambda f: f.sort_key()):
        L.append(f"  {paint(f.level, use_colour)}  {b(f.check)}: {f.message}")
        if f.fix:
            L.extend(wrap("FIX: " + f.fix, width - 12, " " * 8))

    # ---- verdict -----------------------------------------------------------
    all_f = [f for r in reports for f in r.findings] + cross
    n_fail = sum(1 for f in all_f if f.level == "FAIL")
    n_warn = sum(1 for f in all_f if f.level == "WARN")
    n_skip = sum(1 for f in all_f if f.level == "SKIP")

    L.append("")
    L.append("=" * width)
    if n_fail:
        L.append(b("VERDICT: NOT READY") + f"   ({n_fail} FAIL, {n_warn} WARN)")
        L.append("")
        L.extend(wrap("Every FAIL above survives into the files you hand the "
                      "post house. None of them are fixable later. Fix the "
                      "cause and shoot another test.", width - 2, ""))
        L.append("")
        L.append("  Fix in this order:")
        for i, f in enumerate([x for x in all_f if x.level == "FAIL"][:8], 1):
            L.append(f"   {i}. {f.check} — {f.message}")
        code = 2
    elif n_warn:
        L.append(b("VERDICT: USABLE WITH RESERVATIONS")
                 + f"   ({n_warn} WARN)")
        L.append("")
        L.extend(wrap("Nothing here ruins the shoot, but each WARN is something "
                      "you would rather not hand over. Work down the list and "
                      "clear what is cheap to clear.", width - 2, ""))
        code = 1
    else:
        L.append(b("VERDICT: READY"))
        L.append("")
        L.extend(wrap("No technical defect detected. This measures technical "
                      "quality ONLY -- it has not looked at your lighting, your "
                      "framing, your background, or your performance. Watch the "
                      "footage before you trust it.", width - 2, ""))
        code = 0
    if n_skip:
        L.append("")
        L.append(f"  ({n_skip} check(s) skipped — see SKIP lines above.)")
    L.append("=" * width)
    return "\n".join(L), code


def render_json(reports: list[FileReport], cross: list[Finding]) -> tuple[str, int]:
    all_f = [f for r in reports for f in r.findings] + cross
    n_fail = sum(1 for f in all_f if f.level == "FAIL")
    n_warn = sum(1 for f in all_f if f.level == "WARN")
    verdict = "NOT READY" if n_fail else ("USABLE WITH RESERVATIONS" if n_warn
                                          else "READY")
    code = 2 if n_fail else (1 if n_warn else 0)
    payload = {
        "tool": "shootcheck",
        "verdict": verdict,
        "counts": {
            "fail": n_fail, "warn": n_warn,
            "pass": sum(1 for f in all_f if f.level == "PASS"),
            "info": sum(1 for f in all_f if f.level == "INFO"),
            "skip": sum(1 for f in all_f if f.level == "SKIP"),
        },
        "caveat": ("Technical measurements only. Says nothing about whether the "
                   "image looks good."),
        "thresholds": {k: (str(v) if isinstance(v, dict) else v)
                       for k, v in TH.items()},
        "files": [
            {**{k: v for k, v in asdict(r).items() if k != "findings"},
             "worst": r.worst,
             "findings": [asdict(f) for f in
                          sorted(r.findings, key=lambda f: f.sort_key())]}
            for r in sorted(reports, key=lambda r: (LEVEL_ORDER[r.worst], r.name))
        ],
        "cross_camera": [asdict(f) for f in sorted(cross, key=lambda f: f.sort_key())],
    }
    return json.dumps(payload, indent=2, default=str), code


# ===========================================================================
#  self-test: validate the code-value mapping
# ===========================================================================

def verify_mapping(path: str) -> str:
    """
    Cross-check our yuv444p16le luma extraction against ffmpeg's own
    signalstats YAVG. If these disagree, every IRE number in the report is
    wrong, so it is worth being able to prove they agree.
    """
    info = ffprobe_json(path)
    if not info:
        return "could not probe file"
    vs = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if not vs:
        return "no video stream"
    w, h = int(vs["width"]), int(vs["height"])
    depth, _ = parse_pix_fmt(vs.get("pix_fmt") or probe_decoded_pix_fmt(path))
    depth = depth or 10
    fr = grab_yuv444p16(path, 0.0, w, h)
    if fr is None:
        return "could not grab frame"
    ours = float(fr[0].mean()) * ((1 << depth) - 1)
    theirs = signalstats_yavg(path, 0.0)
    if theirs is None:
        return "signalstats unavailable (cannot cross-check)"
    delta = abs(ours - theirs)
    tol = max(2.0, theirs * 0.01)
    status = "AGREE" if delta <= tol else "DISAGREE"
    return (f"{status}: our mean Y={ours:.1f}, ffmpeg signalstats YAVG={theirs:.1f} "
            f"(delta {delta:.2f}, tolerance {tol:.2f}, {depth}-bit)")


# ===========================================================================
#  cli
# ===========================================================================

def collect_dir(d: str) -> list[str]:
    found = []
    for root, _, files in os.walk(d):
        for fn in sorted(files):
            if fn.startswith("."):
                continue
            if os.path.splitext(fn)[1].lower() in (VIDEO_EXT | AUDIO_EXT):
                found.append(os.path.join(root, fn))
    return sorted(found)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="shootcheck",
        description="Objective technical QC for interview footage and audio. "
                    "Measures; does not opine.",
        epilog="Exit: 0 READY, 1 USABLE WITH RESERVATIONS, 2 NOT READY, 3 error.")
    ap.add_argument("files", nargs="*", help="media files to analyse")
    ap.add_argument("--dir", action="append", default=[],
                    help="analyse every media file under this directory "
                         "(repeatable)")
    ap.add_argument("--role", action="append", default=[],
                    help="override role, positionally matched to files: "
                         "tight | wide | prores | transmitter | roommic | "
                         "audio | unknown")
    ap.add_argument("--profile", action="append", default=[],
                    help="override log profile, positionally matched: "
                         "slog3 | dlogm | unknown")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="show passing checks and raw measurements")
    ap.add_argument("--samples", type=int, default=None,
                    help=f"frames sampled per file (default "
                         f"{TH['video_samples']})")
    ap.add_argument("--deep", action="store_true",
                    help="also hash every frame to find byte-identical repeats "
                         "(full decode; slow on long files)")
    ap.add_argument("--no-colour", action="store_true", help="disable ANSI colour")
    ap.add_argument("--verify-mapping", metavar="FILE",
                    help="self-test: cross-check luma extraction against "
                         "ffmpeg signalstats, then exit")
    args = ap.parse_args(argv)

    for tool in ("ffprobe", "ffmpeg"):
        if not have(tool):
            sys.stderr.write(f"FATAL: {tool} not found on PATH.\n")
            return 3

    if args.verify_mapping:
        print(verify_mapping(args.verify_mapping))
        return 0

    if args.samples:
        TH["video_samples"] = max(1, args.samples)

    paths = list(args.files)
    for d in args.dir:
        if not os.path.isdir(d):
            sys.stderr.write(f"WARNING: --dir {d} is not a directory; skipping.\n")
            continue
        paths.extend(collect_dir(d))
    seen, ordered = set(), []
    for p in paths:
        ap_ = os.path.abspath(p)
        if ap_ not in seen:
            seen.add(ap_)
            ordered.append(p)

    if not ordered:
        sys.stderr.write("Nothing to analyse. Give files or --dir.\n")
        return 3

    reports = []
    for i, p in enumerate(ordered):
        role = args.role[i] if i < len(args.role) else None
        prof = args.profile[i] if i < len(args.profile) else None
        if not args.json:
            sys.stderr.write(f"  analysing {os.path.basename(p)} ...\n")
            sys.stderr.flush()
        try:
            reports.append(analyse_file(p, role, prof, args.deep))
        except Exception as e:                     # never crash on one bad file
            r = FileReport(path=p, name=os.path.basename(p))
            r.error = f"{type(e).__name__}: {e}"
            r.add("FAIL", "analyser error",
                  f"Unhandled error analysing this file: {type(e).__name__}: {e}",
                  "This is a tool bug, not necessarily a footage problem. "
                  "Inspect the file manually with ffprobe and report the error.")
            reports.append(r)

    cross = cross_check(reports)

    if args.json:
        text, code = render_json(reports, cross)
    else:
        use_colour = sys.stdout.isatty() and not args.no_colour
        text, code = render_text(reports, cross, args.verbose, use_colour)
    print(text)
    return code


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.stderr.write("\ninterrupted\n")
        sys.exit(3)

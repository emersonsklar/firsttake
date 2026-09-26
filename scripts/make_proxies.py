#!/usr/bin/env python3
"""
make_proxies — build review and editorial proxies from a VERIFIED offload.

Runs after offload.py, reads from the offload's destination, and writes into a
`proxies/` folder that sits OUTSIDE the card folder.

    ./make_proxies.py "/Volumes/WORKING/PROJECT/A001"
    ./make_proxies.py "/Volumes/WORKING/PROJECT/A001" --lut /path/to/look.cube
    ./make_proxies.py "/Volumes/WORKING/PROJECT/A001" --only review
    ./make_proxies.py "/Volumes/WORKING/PROJECT/A001" --dry-run

## Three deliberate design choices

**It is not part of offload.py.** offload.py's promise is "every byte that left the
card arrived at every destination." A proxy is a DERIVED file, not a copy; hashing
one into that manifest would blur what VERIFIED means. Encoding also takes minutes,
and the offload should finish fast so the card can be released.

**It reads the destination, not the card.** Proxies are made from bytes that have
already been verified, and the card can be pulled the moment the offload passes.

**Proxies land OUTSIDE the card folder.** Measured 2026-09-07: an ASC MHL generation
tracks every file in its folder, so anything dropped into the card folder gets
absorbed into the next hop's generation and then travels to the archive forever.
The card folder must stay exactly what came off the card.

## Two flavours, and why the flat one has no LUT

    review     H.264, LUT baked in     -> for anyone judging focus/framing/exposure
    editorial  ProRes Proxy, NO LUT    -> for cutting and conforming back

A reviewer looking at flat log will misjudge exposure every time, so the review
copy gets the look. The editorial copy must NOT carry a baked look -- the grade is
the post house's job, and a proxy that already has one fights them. This mirrors
the delivery spec's "camera-original, no LUT applied".

Exit codes:
    0  every proxy built and verified
    1  a proxy failed to build or failed its frame-count check
    2  could not run (no verified offload, ffmpeg missing)
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys

# Encoders, best first. VideoToolbox is hardware on Apple silicon and is what makes
# this practical: measured 0.29x realtime for ProRes Proxy on an M4 Pro.
REVIEW_ENC = [("h264_videotoolbox", ["-b:v", "12M", "-profile:v", "high"]),
              ("libx264", ["-crf", "20", "-preset", "veryfast"])]
EDIT_ENC = [("prores_videotoolbox", ["-profile:v", "0"]),          # 0 = Proxy
            ("prores_ks", ["-profile:v", "0", "-vendor", "apl0"])]

VIDEO_EXT = {".mov", ".mp4", ".mxf", ".m4v", ".mts", ".m2ts", ".avi", ".mkv"}
# Proxies of proxies, and the camera's own low-res sidecars, are pointless.
SKIP_SUBSTR = ("-review", "-proxy", ".lrf", ".lrv")


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def pick(encoders: list[tuple[str, list[str]]]) -> tuple[str, list[str]] | None:
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                         capture_output=True, text=True).stdout
    for name, args in encoders:
        if name in out:
            return name, args
    return None


def probe(path: str) -> dict:
    """
    Frame count, rate and size from CONTAINER METADATA -- never by decoding.

    `ffprobe -count_frames` decodes the whole file. Measured 2026-09-09: 2 min 18 s
    for one 6.4 GB ProRes clip versus 0.13 s to read nb_frames, for the identical
    answer (2292). Four of those calls made this tool appear to hang, and on a
    40-minute interview it would have run over an hour.

    The general rule this cost us: a verification must not cost more than the
    operation it verifies, or it gets switched off at wrap -- exactly when it
    matters. Where nb_frames is absent, derive it from duration x rate instead of
    reaching for a decode.
    """
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=nb_frames,duration,r_frame_rate,"
                        "width,height", "-of", "json", path],
                       capture_output=True, text=True)
    try:
        st = json.loads(r.stdout)["streams"][0]
    except Exception:
        return {}
    frames = int(st["nb_frames"]) if str(st.get("nb_frames", "")).isdigit() else 0
    fps = st.get("r_frame_rate")
    if not frames and st.get("duration") and fps and "/" in fps:
        n, d = fps.split("/")
        try:
            frames = round(float(st["duration"]) * (float(n) / float(d)))
        except (ValueError, ZeroDivisionError):
            frames = 0
    return {"frames": frames, "fps": fps, "w": st.get("width"), "h": st.get("height"),
            "derived": not str(st.get("nb_frames", "")).isdigit()}


def offload_verified(card_dir: str) -> tuple[bool, str]:
    """Refuse to build proxies from an offload that did not pass."""
    mans = sorted(glob.glob(os.path.join(card_dir, "offload-manifest-*.json")))
    if not mans:
        return False, "no offload-manifest-*.json here — is this an offload destination?"
    try:
        d = json.load(open(mans[-1], encoding="utf-8"))
    except (OSError, ValueError) as e:
        return False, f"cannot read {os.path.basename(mans[-1])}: {e}"
    s = d.get("summary", {})
    if s.get("files_failed", 1) != 0:
        return False, f"{os.path.basename(mans[-1])} reports {s['files_failed']} failed file(s)"
    return True, os.path.basename(mans[-1])


def sources(card_dir: str) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(card_dir):
        dirnames[:] = [d for d in dirnames if d not in ("ascmhl", "proxies")]
        for fn in sorted(filenames):
            low = fn.lower()
            if os.path.splitext(low)[1] not in VIDEO_EXT:
                continue
            if any(s in low for s in SKIP_SUBSTR):
                continue
            out.append(os.path.join(dirpath, fn))
    return out


def build(src: str, dst: str, enc: tuple[str, list[str]], lut: str | None,
          height: int, dry: bool) -> tuple[bool, str]:
    vf = f"scale=-2:{height}"
    if lut:
        # Escape for ffmpeg's filter parser: colons and backslashes break it.
        esc = lut.replace("\\", "\\\\").replace(":", "\\:")
        vf += f",lut3d=file='{esc}'"
    cmd = ["ffmpeg", "-v", "error", "-i", src, "-vf", vf,
           "-c:v", enc[0], *enc[1], "-c:a", "pcm_s24le", "-y", dst]
    if dry:
        return True, " ".join(cmd)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(dst):
        return False, (r.stderr or "ffmpeg failed").strip().splitlines()[-1:][0] \
            if r.stderr else "ffmpeg failed"

    # A proxy that silently dropped frames will not conform, and nothing in the
    # file says so. Compare frame counts rather than trusting a zero exit code.
    a, b = probe(src), probe(dst)
    tol = 1 if (a.get("derived") or b.get("derived")) else 0
    if a.get("frames") and b.get("frames") and abs(a["frames"] - b["frames"]) > tol:
        return False, (f"frame count mismatch: source {a['frames']}, "
                       f"proxy {b['frames']} — will not conform")
    if a.get("fps") and b.get("fps") and a["fps"] != b["fps"]:
        return False, f"frame rate changed: {a['fps']} -> {b['fps']}"
    return True, f"{b.get('w')}x{b.get('h')}, {b.get('frames')} frames"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Build proxies from a verified offload.")
    ap.add_argument("card_dir", help="an offload destination, e.g. .../PROJECT/A001")
    ap.add_argument("--lut", action="append", default=[], metavar="[GLOB=]FILE",
                    help="LUT for the REVIEW proxy. Repeatable and matched per file: "
                         "--lut '*atomos*=slog3.cube' --lut '*dji*=dlogm.cube'. "
                         "A bare FILE applies to everything (single-camera kit).")
    ap.add_argument("--only", choices=["review", "editorial"],
                    help="build just one flavour")
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    if not (have("ffmpeg") and have("ffprobe")):
        print("FATAL: ffmpeg/ffprobe not on PATH")
        return 2
    card = os.path.abspath(os.path.expanduser(a.card_dir))
    ok, why = offload_verified(card)
    if not ok:
        print(f"FATAL: {why}")
        print("  Proxies are only built from a VERIFIED offload — fix the offload first.")
        return 2
    print(f"offload manifest: {why}")

    # Each camera needs ITS OWN LUT. Applying a Sony S-Log3 LUT to DJI D-Log M
    # footage produces a confidently wrong image -- caught by a dry run on
    # 2026-09-09, before it reached a post team. A file with no matching LUT gets
    # an UNGRADED review proxy and says so, which is honest; a wrong LUT is not.
    luts: list[tuple[str, str]] = []
    for spec in a.lut:
        pat, _, path = spec.rpartition("=")
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isfile(path):
            print(f"FATAL: LUT not found: {path}")
            return 2
        luts.append((pat or "*", path))

    # Sibling of the card folder, never inside it.
    out = os.path.join(os.path.dirname(card), "proxies", os.path.basename(card))
    if not a.dry_run:
        os.makedirs(out, exist_ok=True)
    print(f"proxies -> {out}\n")

    review = pick(REVIEW_ENC) if a.only in (None, "review") else None
    edit = pick(EDIT_ENC) if a.only in (None, "editorial") else None
    if a.only in (None, "review") and not review:
        print("  no H.264 encoder available"); return 2
    if a.only in (None, "editorial") and not edit:
        print("  no ProRes encoder available"); return 2

    srcs = sources(card)
    if not srcs:
        print("  no video files to proxy")
        return 0

    fails = 0
    for src in srcs:
        base = os.path.splitext(os.path.basename(src))[0]
        print(f"{os.path.basename(src)}")
        import fnmatch
        mine = next((p for pat, p in luts
                     if fnmatch.fnmatch(os.path.basename(src).lower(), pat.lower())), None)
        jobs = []
        if review:
            jobs.append(("review  ", os.path.join(out, f"{base}-review.mp4"),
                         review, mine))
        if edit:
            # No LUT here, on purpose: the grade belongs to the post house.
            jobs.append(("editorial", os.path.join(out, f"{base}-proxy.mov"),
                         edit, None))
        for label, dst, enc, lut in jobs:
            good, detail = build(src, dst, enc, lut, a.height, a.dry_run)
            tag = "would run" if a.dry_run else ("ok  " if good else "FAIL")
            if lut:
                note = f"  [LUT: {os.path.basename(lut)}]"
            elif label.strip() == "review":
                note = "  [NO LUT — still log, do not judge exposure by eye]"
            else:
                note = ""
            print(f"  {tag}  {label}  {enc[0]:22s}{note}")
            print(f"        {detail}")
            if not good:
                fails += 1
        print()

    if a.dry_run:
        print("dry run — nothing written")
        return 0
    if fails:
        print(f"{fails} proxy job(s) FAILED — do not ship these")
        return 1
    print("all proxies built and frame-count verified")
    print("\n  Reminder: the review copies have a look baked in. They are NOT")
    print("  camera-original and must not be delivered as such.")
    return 0


if __name__ == "__main__":
    # Post tool: needs an active license (see license_check.py). --selftest is exempt.
    from license_check import gate
    gate(__file__)
    sys.exit(main(sys.argv[1:]))

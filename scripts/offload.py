#!/usr/bin/env python3
"""
offload — verified card offload to one or more destinations, with reports.

Copies media off a card to as many destinations as you name, hashing the source
once while it is read and then re-reading every destination independently to
prove what landed. Writes an MHL manifest, a JSON manifest, and a self-contained
HTML report.

The verification is the point. A copy that "looked fine" is not a copy. Every
destination is read back and compared against the hash taken from the source
stream. On macOS the read-back bypasses the page cache, so it comes from the
platter. ⚠️ Windows and Linux offer no equivalent this tool uses, so a read-back
there MAY be served from RAM for files just written -- the report says so, and
the remedy is exact: eject and reconnect the destination (which drops its cache),
then run `--verify-only` on it.

Usage:
    ./offload.py /Volumes/CARD --dest /Volumes/ARCHIVE
    ./offload.py /Volumes/CARD --dest /Volumes/ARCHIVE --dest /Volumes/cold
    ./offload.py /Volumes/CARD --dest /Volumes/ARCHIVE \\
        --project "PROJECT" --card A001 --operator OPERATOR \\
        --notes "Sony tight + DJI wide, day 1"

    # later, push an existing offload to the home cold-storage archive
    ./offload.py "/Volumes/ARCHIVE/PROJECT/A001" --dest /Volumes/cold

    # periodic archive integrity check, no copying
    ./offload.py --verify-only "/Volumes/COLD/PROJECT/A001"

Exit codes:
    0  VERIFIED                    (every file matched on every destination)
    1  VERIFIED WITH WARNINGS      (matched, but something is worth reading)
    2  FAILED                      (a mismatch, a short copy, or an I/O error)
    3  tool error (nothing copied)

Requires: python3. Optional: ffmpeg/ffprobe for clip thumbnails and metadata,
xxhash for the fast Hedge-compatible hash. Neither is needed to copy or verify.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
try:
    import fcntl                     # POSIX only; used for macOS F_NOCACHE below
except ImportError:                  # Windows: no fcntl, and no page-cache bypass here
    fcntl = None
import glob
import hashlib
import html
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import xml.sax.saxutils as saxutils
from dataclasses import dataclass, field, asdict
from typing import Any


# ===========================================================================
#  CONFIG — edit these, nothing else
# ===========================================================================

CFG: dict[str, Any] = {
    # Read/write chunk. 8 MiB keeps spinning disks in their sequential sweet
    # spot without making the progress line feel dead.
    "chunk": 8 * 1024 * 1024,

    # What counts as a "clip" for the report's Clips section. Everything else
    # is listed under Other Files. Lowercase, with the dot.
    "clip_ext": {
        ".mov", ".mp4", ".m4v", ".mxf", ".avi", ".mts", ".m2ts", ".mkv",
        ".braw", ".r3d", ".ari", ".arx", ".dng", ".cine", ".crm",
        ".wav", ".aif", ".aiff", ".flac", ".bwf", ".mp3", ".m4a",
    },

    # Sidecars worth calling out as belonging to a clip rather than as loose
    # files: proxies, low-res copies, camera logs.
    "sidecar_ext": {".lrf", ".lrv", ".thm", ".xml", ".cpf", ".if2", ".egg"},

    # Skipped by default. These are macOS/Windows bookkeeping that has no
    # business in an archive. --all-files copies them anyway.
    "junk_names": {
        ".DS_Store", "Thumbs.db", "desktop.ini", ".Spotlight-V100",
        ".fseventsd", ".Trashes", ".TemporaryItems", "System Volume Information",
        ".DocumentRevisions-V100", ".TemporaryItems", "$RECYCLE.BIN",
    },

    # Refuse to start if a destination has less than this much headroom beyond
    # the payload. Filling an archive volume to the last byte invites trouble.
    "free_space_margin": 512 * 1024 * 1024,

    # Thumbnail width in the HTML report. Embedded as base64 JPEG so the report
    # is a single file you can email.
    "thumb_width": 320,

    # Grab the still this far into the clip. 10% avoids slates and lens caps.
    "thumb_position_frac": 0.10,

    # Hashes that MHL 1.1 can legally carry. sha256 is stronger but is not in
    # the MHL spec, so choosing it means no MHL file (the JSON manifest still
    # covers you).
    "mhl_legal": {"xxh64", "md5", "sha1"},

    # Our hash names -> what `ascmhl create -h` accepts. ascmhl 1.2 offers
    # md5|sha1|xxh128|xxh3|xxh64|c4 — no sha256 — so sha256 maps to xxh64.
    # ascmhl hashes the files itself, so this only picks the format it records.
    "ascmhl_hash": {"xxh64": "xxh64", "md5": "md5", "sha1": "sha1",
                    "sha256": "xxh64"},
}

# macOS: tell the kernel not to keep these pages. Without this, verification
# reads come back from RAM and prove nothing about what is on the disk.
F_NOCACHE = 48

# What the report may honestly claim about the read-back, per platform. Never claim a
# cache bypass that did not happen: this line is what someone trusts before wiping a card.
CACHE_NOTE_MAC = ("Verification re-reads every destination from the device with the "
                  "page cache disabled, so a match here describes the bytes on the "
                  "disk and not a copy still sitting in RAM.")
CACHE_NOTE_OTHER = ("Verification re-reads every destination and compares it to the source "
                    "hash. On this operating system the read-back of a file just written "
                    "may come from memory rather than the disk. Before wiping the card: eject "
                    "and reconnect each destination, then run offload --verify-only on it — "
                    "that check reads from the disk.")

VERSION = "1.0"


# ===========================================================================
#  DATA
# ===========================================================================

@dataclass
class DestResult:
    """One destination's outcome for one file."""
    dest_root: str
    path: str = ""
    status: str = "pending"     # copied | present | verified | MISMATCH | ERROR | skipped
    hash: str | None = None
    bytes_written: int = 0
    copy_s: float = 0.0
    verify_s: float = 0.0
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("verified", "present")


@dataclass
class FileEntry:
    rel: str                       # path relative to the source root
    size: int = 0
    mtime: float = 0.0
    kind: str = "other"            # clip | sidecar | other
    hidden: bool = False
    src_hash: str | None = None
    read_s: float = 0.0
    dests: list[DestResult] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    thumb_b64: str | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.dests) and all(d.ok for d in self.dests) and not self.error


@dataclass
class Session:
    source: str
    dest_roots: list[str]
    hash_name: str
    project: str = ""
    card: str = ""
    operator: str = ""
    title: str = ""
    notes: str = ""
    logo_b64: str = ""
    started: float = 0.0
    finished: float = 0.0
    files: list[FileEntry] = field(default_factory=list)
    # Concerning skips (unreadable, symlink) — these move the verdict.
    skipped: list[tuple[str, str]] = field(default_factory=list)
    # Expected skips (.DS_Store and friends) — reported, but not a problem.
    skipped_system: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Informational only; never moves the verdict.
    infos: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    manifests: list[str] = field(default_factory=list)
    dry_run: bool = False
    verify_only: bool = False


# ===========================================================================
#  SMALL HELPERS
# ===========================================================================

def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(n) < 1024.0 or unit == "PB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:,.2f} {unit}"
        n /= 1024.0
    return f"{n} B"


def human_dur(s: float | None) -> str:
    if s is None or s != s:
        return "-"
    s = int(round(s))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {sec:02d}s"
    if m:
        return f"{m}m {sec:02d}s"
    return f"{sec}s"


def human_rate(nbytes: int, seconds: float) -> str:
    if seconds <= 0:
        return "-"
    return f"{human_bytes(nbytes / seconds)}/s"


def iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def open_nocache(path: str):
    """Open for reading with the page cache disabled where the OS allows it."""
    f = open(path, "rb", buffering=0)
    if sys.platform == "darwin" and fcntl is not None:
        try:
            fcntl.fcntl(f.fileno(), F_NOCACHE, 1)
        except OSError:
            pass
    return f


def make_hasher(name: str):
    if name == "xxh64":
        import xxhash                      # checked by resolve_hash() first
        return xxhash.xxh64()
    return hashlib.new(name)


def resolve_hash(requested: str | None) -> tuple[str, list[str]]:
    """Pick a hash. Prefer xxh64 (fast, what Hedge writes) when available."""
    warnings: list[str] = []
    if requested:
        if requested == "xxh64":
            try:
                import xxhash  # noqa: F401
            except ImportError:
                return "md5", ["xxh64 requested but the xxhash module is not "
                               "installed (pip3 install xxhash) — used md5."]
        return requested, warnings
    try:
        import xxhash  # noqa: F401
        return "xxh64", warnings
    except ImportError:
        return "md5", ["xxhash not installed, so md5 was used. md5 is fine for "
                       "integrity; install xxhash (pip3 install xxhash) for a "
                       "roughly 5x faster hash."]


def classify(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    if ext in CFG["clip_ext"]:
        return "clip"
    if ext in CFG["sidecar_ext"]:
        return "sidecar"
    return "other"


def is_junk(name: str) -> bool:
    return name in CFG["junk_names"]


def free_bytes(path: str) -> int:
    # shutil.disk_usage is statvfs's f_bavail * f_frsize on POSIX -- the same number as
    # before -- and GetDiskFreeSpaceEx on Windows, where os.statvfs does not exist.
    return shutil.disk_usage(path).free


# ===========================================================================
#  SCAN
# ===========================================================================

def scan(source: str, all_files: bool
         ) -> tuple[list[FileEntry], list[tuple[str, str]], list[tuple[str, str]]]:
    """Walk the source. Returns (entries, skipped, skipped_system)."""
    entries: list[FileEntry] = []
    skipped: list[tuple[str, str]] = []
    skipped_system: list[tuple[str, str]] = []

    for dirpath, dirnames, filenames in os.walk(source, followlinks=False):
        if not all_files:
            pruned = [d for d in dirnames if is_junk(d)]
            for d in pruned:
                skipped_system.append(
                    (os.path.relpath(os.path.join(dirpath, d), source),
                     "system directory"))
            dirnames[:] = [d for d in dirnames if not is_junk(d)]
        dirnames.sort()

        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, source)

            if not all_files and is_junk(fn):
                skipped_system.append((rel, "system file"))
                continue
            if os.path.islink(full):
                skipped.append((rel, "symlink"))
                continue
            try:
                st = os.stat(full)
            except OSError as e:
                skipped.append((rel, f"unreadable: {e.strerror}"))
                continue
            if not os.path.isfile(full):
                skipped.append((rel, "not a regular file"))
                continue


            entries.append(FileEntry(
                rel=rel,
                size=st.st_size,
                mtime=st.st_mtime,
                kind=classify(fn),
                hidden=fn.startswith("."),
            ))
    return entries, skipped, skipped_system


# ===========================================================================
#  COPY + VERIFY
# ===========================================================================

def copy_one(src: str, entry: FileEntry, targets: list[tuple[str, str]],
             hash_name: str, overwrite: bool) -> None:
    """
    Read the source exactly once. Hash that stream. Fan it out to every target.

    Hashing the stream as it is read -- rather than re-reading the source
    afterwards -- means the recorded hash describes the bytes that were actually
    sent to each destination.
    """
    hasher = make_hasher(hash_name)
    fresh: list[tuple[str, Any, DestResult]] = []

    for dest_root, dest_path in targets:
        res = DestResult(dest_root=dest_root, path=dest_path)
        entry.dests.append(res)

        if os.path.exists(dest_path) and not overwrite:
            try:
                if os.path.getsize(dest_path) == entry.size:
                    res.status = "present"      # verified in the pass below
                    res.note = "already present, hash-checked"
                    continue
            except OSError:
                pass
            res.status = "ERROR"
            res.note = "exists with a different size; use --overwrite to replace"
            continue

        try:
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            fh = open(dest_path, "wb", buffering=0)
            fresh.append((dest_path, fh, res))
        except OSError as e:
            res.status = "ERROR"
            res.note = f"cannot create: {e.strerror}"

    t0 = time.time()
    try:
        with open_nocache(src) as sf:
            while True:
                buf = sf.read(CFG["chunk"])
                if not buf:
                    break
                hasher.update(buf)
                for dest_path, fh, res in fresh:
                    if res.status == "ERROR":
                        continue
                    try:
                        fh.write(buf)
                        res.bytes_written += len(buf)
                    except OSError as e:
                        res.status = "ERROR"
                        res.note = f"write failed: {e.strerror}"
    except OSError as e:
        entry.error = f"read failed: {e.strerror}"
    finally:
        for dest_path, fh, res in fresh:
            try:
                fh.flush()
                os.fsync(fh.fileno())
            except OSError:
                pass
            fh.close()

    entry.read_s = time.time() - t0
    entry.src_hash = hasher.hexdigest()

    for dest_path, fh, res in fresh:
        if res.status == "ERROR":
            continue
        if res.bytes_written != entry.size:
            res.status = "ERROR"
            res.note = (f"short copy: wrote {res.bytes_written} of {entry.size} bytes")
            continue
        res.status = "copied"
        res.copy_s = entry.read_s
        try:
            shutil.copystat(src, dest_path)
        except OSError:
            pass


def verify_one(entry: FileEntry, hash_name: str) -> None:
    """Re-read each destination from disk and compare to the source hash."""
    for res in entry.dests:
        if res.status in ("ERROR", "skipped"):
            continue
        t0 = time.time()
        try:
            hasher = make_hasher(hash_name)
            with open_nocache(res.path) as f:
                while True:
                    buf = f.read(CFG["chunk"])
                    if not buf:
                        break
                    hasher.update(buf)
            res.hash = hasher.hexdigest()
        except OSError as e:
            res.status = "ERROR"
            res.note = f"verify read failed: {e.strerror}"
            res.verify_s = time.time() - t0
            continue
        res.verify_s = time.time() - t0

        if entry.src_hash is None:
            res.status = "ERROR"
            res.note = "no source hash to compare against"
        elif res.hash == entry.src_hash:
            if res.status == "present":
                res.note = "already present, hash matched"
            res.status = "verified" if res.status == "copied" else "present"
        else:
            res.status = "MISMATCH"
            res.note = f"expected {entry.src_hash}, read back {res.hash}"


# ===========================================================================
#  CLIP AWARENESS — thumbnails and metadata, when ffmpeg is around
# ===========================================================================

def ffprobe_meta(path: str) -> dict[str, Any]:
    if not have("ffprobe"):
        return {}
    cmd = ["ffprobe", "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", path]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if p.returncode != 0:
            return {}
        data = json.loads(p.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}

    fmt = data.get("format", {}) or {}
    out: dict[str, Any] = {}
    try:
        out["duration"] = float(fmt.get("duration"))
    except (TypeError, ValueError):
        out["duration"] = None
    try:
        out["bitrate"] = int(fmt.get("bit_rate"))
    except (TypeError, ValueError):
        out["bitrate"] = None

    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    for key in ("creation_time", "com.apple.quicktime.make",
                "com.apple.quicktime.model"):
        if key in tags:
            out[key.split(".")[-1]] = tags[key]

    for s in data.get("streams", []) or []:
        if s.get("codec_type") == "video" and "video" not in out:
            fr = s.get("r_frame_rate") or "0/1"
            try:
                num, den = fr.split("/")
                fps = float(num) / float(den) if float(den) else None
            except (ValueError, ZeroDivisionError):
                fps = None
            out["video"] = {
                "codec": s.get("codec_name"),
                "width": s.get("width"),
                "height": s.get("height"),
                "fps": round(fps, 3) if fps else None,
                "pix_fmt": s.get("pix_fmt"),
            }
        elif s.get("codec_type") == "audio" and "audio" not in out:
            out["audio"] = {
                "codec": s.get("codec_name"),
                "channels": s.get("channels"),
                "sample_rate": s.get("sample_rate"),
                "bits": s.get("bits_per_raw_sample") or s.get("bits_per_sample"),
            }
    return out


def grab_thumb(path: str, duration: float | None) -> str | None:
    if not have("ffmpeg"):
        return None
    ts = 0.0
    if duration and duration > 1:
        ts = duration * CFG["thumb_position_frac"]
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{ts:.3f}", "-i", path,
           "-frames:v", "1", "-vf", f"scale={CFG['thumb_width']}:-2",
           "-f", "mjpeg", "pipe:1"]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=120)
        if p.returncode != 0 or not p.stdout:
            return None
        return base64.b64encode(p.stdout).decode("ascii")
    except (OSError, subprocess.SubprocessError):
        return None


def enrich_clips(sess: Session, want_thumbs: bool) -> None:
    """Read metadata from the first destination, as OffShoot does."""
    for e in sess.files:
        if e.kind != "clip" or not e.ok:
            continue
        read_from = e.dests[0].path if e.dests else None
        if not read_from or not os.path.exists(read_from):
            continue
        e.meta = ffprobe_meta(read_from)
        if want_thumbs and e.meta.get("video"):
            e.thumb_b64 = grab_thumb(read_from, e.meta.get("duration"))


# ===========================================================================
#  MANIFESTS
# ===========================================================================

def write_mhl(sess: Session, dest_root: str, out_path: str) -> bool:
    """
    Classic MHL 1.1. Silverstack, Hedge/OffShoot, and Resolve all read this,
    which is the point of writing it rather than only our own JSON.
    """
    if sess.hash_name not in CFG["mhl_legal"]:
        return False
    esc = saxutils.escape
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tag = {"xxh64": "xxhash64", "md5": "md5", "sha1": "sha1"}[sess.hash_name]

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<hashlist version=\"1.1\">",
        "  <creatorinfo>",
        f"    <name>{esc(sess.operator or 'unknown')}</name>",
        f"    <username>{esc(sess.operator or 'unknown')}</username>",
        f"    <hostname>{esc(platform.node())}</hostname>",
        f"    <tool>offload {VERSION}</tool>",
        f"    <startdate>{iso(sess.started)}</startdate>",
        f"    <finishdate>{iso(sess.finished or time.time())}</finishdate>",
        "  </creatorinfo>",
    ]
    written = 0
    for e in sess.files:
        res = next((d for d in e.dests if d.dest_root == dest_root), None)
        if res is None or not res.ok or not e.src_hash:
            continue
        rel = os.path.relpath(res.path, os.path.dirname(out_path))
        lines += [
            "  <hash>",
            f"    <file>{esc(rel)}</file>",
            f"    <size>{e.size}</size>",
            f"    <lastmodificationdate>{iso(e.mtime)}</lastmodificationdate>",
            f"    <{tag}>{e.src_hash}</{tag}>",
            f"    <hashdate>{iso(sess.finished or time.time())}</hashdate>",
            "  </hash>",
        ]
        written += 1
    lines.append("</hashlist>")

    if not written:
        return False
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return True


def write_ascmhl(sess: Session, dest_dir: str) -> tuple[bool, str]:
    """
    Add an ASC MHL generation for this destination, if the `ascmhl` CLI is here.

    Worth having alongside classic MHL: ASC MHL is generation-chained, so a
    card -> working drive -> cold archive path records its own lineage instead
    of leaving three unrelated snapshots. Classic MHL 1.1 cannot express that.
    """
    if not have("ascmhl"):
        return False, "ascmhl not installed (pip3 install ascmhl)"
    algo = CFG["ascmhl_hash"].get(sess.hash_name, "xxh64")
    cmd = ["ascmhl", "create", "-h", algo, dest_dir]
    if sess.operator:
        cmd += ["--author_name", sess.operator]
    comment = " · ".join(x for x in (sess.project, sess.card, sess.notes) if x)
    if comment:
        cmd += ["--comment", comment[:400]]

    # Snapshot the generations that already exist. `ascmhl create` can WRITE a
    # new generation and still exit non-zero — measured 2026-09-07, exit 10 with
    # "Files referenced in the ASC MHL history are missing". Reporting that as
    # "not written" is a false statement about what is on the archive drive, and
    # an archivist reading the ascmhl/ directory later would see an apparently
    # intact chain that no run ever vouched for. So diff before and after.
    before = set(_ascmhl_generations(dest_dir))
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=86400)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"ascmhl failed to run: {e}"
    if p.returncode != 0:
        detail = (p.stderr or p.stdout or "").strip().splitlines()
        msg = f"ascmhl exited {p.returncode}: {detail[-1] if detail else '?'}"
        new = sorted(set(_ascmhl_generations(dest_dir)) - before)
        if new:
            msg = (f"{msg} — but generation {os.path.basename(new[-1])} WAS "
                   f"written. The files copied here are recorded; the CHAIN back "
                   f"to the previous hop has a gap.")
        # Scan every generation INHERITED from earlier hops, not the one ascmhl
        # just wrote — a fresh generation is built from what is on disk, so by
        # construction it has no gaps. Nor is the newest prior one enough: a
        # previous broken hop leaves a clean generation behind it while the gap
        # still sits further back in the history.
        gone = _history_gaps_all(dest_dir, sorted(before))
        if gone:
            shown = ", ".join(gone[:4]) + ("…" if len(gone) > 4 else "")
            msg = (f"{msg} Referenced but absent here: {shown}. A generation "
                   f"tracks the offload's own manifest/report sidecars too, so "
                   f"deleting one breaks the chain at the next hop.")
        return False, msg
    return True, os.path.join(dest_dir, "ascmhl")


def _ascmhl_generations(dest_dir: str) -> list[str]:
    """Generation files currently in DEST/ascmhl, oldest name first."""
    return sorted(glob.glob(os.path.join(dest_dir, "ascmhl", "[0-9][0-9][0-9][0-9]_*.mhl")))


def _history_gaps_all(dest_dir: str, gen_paths: list[str]) -> list[str]:
    """Union of gaps across several generations, order preserved, deduped."""
    seen: dict[str, None] = {}
    for g in gen_paths:
        for rel in _history_gaps(dest_dir, g):
            seen.setdefault(rel, None)
    return list(seen)


def _history_gaps(dest_dir: str, gen_path: str | None = None) -> list[str]:
    """
    Paths an ASC MHL generation references that are not present in dest_dir.

    `ascmhl` reports that files are missing without naming any of them, which
    leaves you grepping an XML by hand at wrap time. Name them.

    Pass the generation inherited from the previous hop. Defaulting to the
    newest one is nearly useless right after a create: that generation was just
    built from what is on disk, so by construction it has no gaps.
    """
    if gen_path is None:
        gens = _ascmhl_generations(dest_dir)
        if not gens:
            return []
        gen_path = gens[-1]
    try:
        text = open(gen_path, encoding="utf-8", errors="replace").read()
    except OSError:
        return []
    missing = []
    for rel in re.findall(r"<path[^>]*>([^<]+)</path>", text):
        rel = html.unescape(rel).strip()
        if rel and not os.path.exists(os.path.join(dest_dir, rel)):
            missing.append(rel)
    return missing


def unique_stamp(dest_dir: str, base: str) -> str:
    """
    A stamp that does not already exist in this destination.

    Two offloads landing in the same second must not share a filename: the
    second would overwrite the first's manifest, destroying the record of where
    those files came from. Provenance is the whole point, so this never
    silently reuses a name.
    """
    stamp = base
    n = 1
    while glob.glob(os.path.join(dest_dir, f"offload-manifest-{stamp}.json")):
        n += 1
        stamp = f"{base}-{n}"
    return stamp


def session_dict(sess: Session) -> dict[str, Any]:
    d = asdict(sess)
    d.pop("logo_b64", None)
    for fe in d["files"]:
        fe.pop("thumb_b64", None)
    d["tool"] = f"offload {VERSION}"
    d["summary"] = summarise(sess)
    return d


def write_json_manifest(sess: Session, out_path: str) -> None:
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(session_dict(sess), f, indent=2, sort_keys=False)
        f.write("\n")


# ===========================================================================
#  SUMMARY
# ===========================================================================

def summarise(sess: Session) -> dict[str, Any]:
    clips = [e for e in sess.files if e.kind == "clip"]
    total = sum(e.size for e in sess.files)
    ok = [e for e in sess.files if e.ok]
    bad = [e for e in sess.files if not e.ok]
    elapsed = (sess.finished or time.time()) - sess.started
    moved = total * max(1, len(sess.dest_roots))
    return {
        "sources": 1,
        "destinations": len(sess.dest_roots),
        "clips": len(clips),
        "files": len(sess.files),
        "hidden_files": sum(1 for e in sess.files if e.hidden),
        "total_bytes": total,
        "total_bytes_written": moved,
        "files_ok": len(ok),
        "files_failed": len(bad),
        "skipped": len(sess.skipped),
        "skipped_system": len(sess.skipped_system),
        "elapsed_s": round(elapsed, 2),
        "verification": sess.hash_name,
        "avg_rate_bytes_s": round(moved / elapsed, 0) if elapsed > 0 else None,
    }


def verdict(sess: Session) -> tuple[str, int]:
    """
    Only real problems move this. Skipping .DS_Store is the tool doing its job,
    not a warning — if routine housekeeping downgraded every run, the verdict
    would stop meaning anything.
    """
    if sess.errors or any(not e.ok for e in sess.files):
        return "FAILED", 2
    if sess.warnings or sess.skipped:
        return "VERIFIED WITH WARNINGS", 1
    return "VERIFIED", 0


# ===========================================================================
#  TEXT OUTPUT
# ===========================================================================

def render_text(sess: Session) -> str:
    s = summarise(sess)
    v, _ = verdict(sess)
    L: list[str] = []
    L.append("")
    L.append("=" * 74)
    L.append(f"  {v}   —   {sess.title or sess.project or 'offload'}")
    L.append("=" * 74)
    L.append(f"  source        {sess.source}")
    for d in sess.dest_roots:
        L.append(f"  destination   {d}")
    L.append(f"  verification  {sess.hash_name}, every destination re-read from disk")
    if sess.card:
        L.append(f"  card          {sess.card}")
    if sess.operator:
        L.append(f"  operator      {sess.operator}")
    L.append("")
    L.append(f"  {s['clips']} clips, {s['files']} files, {human_bytes(s['total_bytes'])}"
             f"  ->  {s['destinations']} destination(s),"
             f" {human_bytes(s['total_bytes_written'])} written")
    L.append(f"  {human_dur(s['elapsed_s'])} elapsed,"
             f" {human_rate(s['total_bytes_written'], s['elapsed_s'])} average")
    L.append("")

    bad = [e for e in sess.files if not e.ok]
    if bad:
        L.append("  FAILURES")
        for e in bad:
            if e.error:
                L.append(f"    {e.rel}: {e.error}")
            for d in e.dests:
                if not d.ok:
                    L.append(f"    {e.rel} -> {d.dest_root}: {d.status} {d.note}")
        L.append("")

    if sess.skipped:
        L.append(f"  SKIPPED ({len(sess.skipped)})")
        for rel, why in sess.skipped[:12]:
            L.append(f"    {rel} — {why}")
        if len(sess.skipped) > 12:
            L.append(f"    ... and {len(sess.skipped) - 12} more")
        L.append("")

    if sess.skipped_system:
        L.append(f"  system files not copied: {len(sess.skipped_system)}"
                 f"  (--all-files to include)")
        L.append("")

    for w in sess.warnings:
        L.append(f"  WARNING  {w}")
    for n in sess.infos:
        L.append(f"  note     {n}")
    if sess.warnings or sess.infos:
        L.append("")

    if sess.manifests:
        L.append("  WROTE")
        for m in sess.manifests:
            L.append(f"    {m}")
        L.append("")
    return "\n".join(L)


# ===========================================================================
#  HTML REPORT
# ===========================================================================

CSS = """
:root{--ink:#16191d;--mute:#5d6672;--line:#dfe3e8;--bg:#fff;--panel:#f7f8fa;
--ok:#1a7f4b;--okbg:#e8f6ee;--bad:#b3261e;--badbg:#fdecea;--warn:#8a5a00;--warnbg:#fdf3e2;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;}
.wrap{max-width:1040px;margin:0 auto;padding:40px 28px 80px}
header{display:flex;align-items:flex-start;gap:20px;border-bottom:2px solid var(--ink);
padding-bottom:18px;margin-bottom:26px}
header img{max-height:56px;max-width:200px}
h1{font-size:25px;margin:0 0 4px;letter-spacing:-.01em}
h2{font-size:17px;margin:38px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--line)}
h3{font-size:14px;margin:22px 0 8px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.sub{color:var(--mute);font-size:13px}
.banner{padding:14px 18px;border-radius:6px;font-weight:600;margin:0 0 26px;
display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}
.banner .det{font-weight:400;font-size:13px}
.b-ok{background:var(--okbg);color:var(--ok);border:1px solid #b8e2ca}
.b-bad{background:var(--badbg);color:var(--bad);border:1px solid #f3c1bd}
.b-warn{background:var(--warnbg);color:var(--warn);border:1px solid #ecd9ab}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:10px;margin:0 0 8px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:12px 14px}
.tile .n{font-size:21px;font-weight:600;font-variant-numeric:tabular-nums}
.tile .l{font-size:11px;color:var(--mute);text-transform:uppercase;letter-spacing:.06em;margin-top:2px}
table{width:100%;border-collapse:collapse;margin:10px 0 4px;font-size:13px}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.05em;
color:var(--mute);border-bottom:1px solid var(--ink);padding:6px 8px;font-weight:600}
td{border-bottom:1px solid var(--line);padding:7px 8px;vertical-align:top}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.hash{font-family:ui-monospace,Menlo,monospace;font-size:11px;color:var(--mute);word-break:break-all}
.pill{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;font-weight:600}
.p-ok{background:var(--okbg);color:var(--ok)}
.p-bad{background:var(--badbg);color:var(--bad)}
.p-warn{background:var(--warnbg);color:var(--warn)}
/* align-items:flex-start is load-bearing, not cosmetic. A flex container
   defaults to align-items:stretch, and a replaced element with height:auto is
   stretched to the flex line's cross size — so the thumbnail got pulled tall to
   match the metadata column beside it, distorting the aspect ratio in exact
   proportion to how much metadata that clip had. Do not remove it, and do not
   pin a hardcoded aspect-ratio here instead: a vertical clip must still render
   at its own ratio, since spotting accidental vertical mode is the point. */
.clip{display:flex;align-items:flex-start;gap:14px;padding:14px 0;
border-bottom:1px solid var(--line)}
.clip img{width:200px;height:auto;align-self:flex-start;border-radius:4px;
background:#eee;flex:0 0 auto}
.clip .noimg{width:200px;aspect-ratio:16/9;border-radius:4px;background:var(--panel);
display:flex;align-items:center;justify-content:center;color:var(--mute);font-size:11px;flex:0 0 auto}
.kv{display:grid;grid-template-columns:auto 1fr;gap:2px 14px;font-size:12.5px;margin-top:5px}
.kv dt{color:var(--mute)}
.kv dd{margin:0}
.notes{background:var(--panel);border-left:3px solid var(--mute);padding:10px 14px;margin:0 0 22px;font-size:13px}
a{color:#0b5fa5}
footer{margin-top:50px;padding-top:14px;border-top:1px solid var(--line);
color:var(--mute);font-size:12px}
@media print{.wrap{padding:0;max-width:none}h2{page-break-after:avoid}
.clip{page-break-inside:avoid}tr{page-break-inside:avoid}}
"""


def _tile(n: str, label: str) -> str:
    return f'<div class="tile"><div class="n">{html.escape(n)}</div>' \
           f'<div class="l">{html.escape(label)}</div></div>'


def _pill(status: str) -> str:
    cls = "p-ok" if status in ("verified", "present") else \
          ("p-warn" if status in ("copied", "skipped") else "p-bad")
    return f'<span class="pill {cls}">{html.escape(status)}</span>'


def render_html(sess: Session) -> str:
    s = summarise(sess)
    v, code = verdict(sess)
    bcls = {0: "b-ok", 1: "b-warn", 2: "b-bad"}[code]
    E = html.escape
    P: list[str] = []

    P.append("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">")
    P.append(f"<title>{E(sess.title or sess.project or 'Offload Report')}</title>")
    P.append(f"<style>{CSS}</style></head><body><div class=\"wrap\">")

    P.append("<header>")
    if sess.logo_b64:
        P.append(f'<img src="data:image/png;base64,{sess.logo_b64}" alt="">')
    P.append("<div>")
    P.append(f"<h1>{E(sess.title or sess.project or 'Offload Report')}</h1>")
    meta = []
    if sess.project:
        meta.append(f"Project: {sess.project}")
    if sess.card:
        meta.append(f"Card: {sess.card}")
    if sess.operator:
        meta.append(f"Operator: {sess.operator}")
    meta.append(iso(sess.started))
    P.append(f'<div class="sub">{E(" · ".join(meta))}</div>')
    P.append("</div></header>")

    P.append(f'<div class="banner {bcls}"><div>{E(v)}</div>')
    P.append(f'<div class="det">{s["files_ok"]} of {s["files"]} files matched on '
             f'all {s["destinations"]} destination(s) · {E(sess.hash_name)} · '
             f'each destination re-read from disk</div></div>')

    if sess.notes:
        P.append(f'<div class="notes">{E(sess.notes)}</div>')

    # ---- Summary ---------------------------------------------------------
    P.append('<h2 id="summary">Summary</h2><div class="tiles">')
    P.append(_tile(str(s["sources"]), "Sources"))
    P.append(_tile(str(s["destinations"]), "Destinations"))
    P.append(_tile(str(s["clips"]), "Clips"))
    P.append(_tile(str(s["files"]), "Files"))
    P.append(_tile(human_bytes(s["total_bytes"]), "Source size"))
    P.append(_tile(human_bytes(s["total_bytes_written"]), "Data written"))
    P.append(_tile(human_dur(s["elapsed_s"]), "Elapsed"))
    P.append(_tile(human_rate(s["total_bytes_written"], s["elapsed_s"]), "Average rate"))
    P.append("</div>")
    P.append(f'<p class="sub">File count is listed alongside clip count because a '
             f'card usually carries more than media. {s["hidden_files"]} hidden '
             f'file(s) included; {s["skipped_system"]} system file(s) not copied; '
             f'{s["skipped"]} item(s) skipped for other reasons.</p>')

    # ---- Sources & Destinations ------------------------------------------
    P.append('<h2 id="sd">Sources &amp; Destinations</h2>')
    P.append("<table><tr><th>Source</th><th class=\"n\">Files</th>"
             "<th class=\"n\">Clips</th><th class=\"n\">Size</th>"
             "<th class=\"n\">Backups</th><th>Verification</th></tr>")
    P.append(f'<tr><td><a href="#src">{E(sess.source)}</a></td>'
             f'<td class="n">{s["files"]}</td><td class="n">{s["clips"]}</td>'
             f'<td class="n">{human_bytes(s["total_bytes"])}</td>'
             f'<td class="n">{s["destinations"]}</td>'
             f'<td>{E(sess.hash_name)}, read back from disk</td></tr>')
    P.append("</table>")

    P.append("<table><tr><th>Destination</th><th class=\"n\">Files</th>"
             "<th class=\"n\">Size</th><th class=\"n\">Verified</th>"
             "<th class=\"n\">Failed</th><th>Status</th></tr>")
    for root in sess.dest_roots:
        rs = [d for e in sess.files for d in e.dests if d.dest_root == root]
        good = [d for d in rs if d.ok]
        bad = [d for d in rs if not d.ok]
        size = sum(e.size for e in sess.files
                   if any(d.dest_root == root and d.ok for d in e.dests))
        st = "complete" if not bad else f"{len(bad)} problem(s)"
        P.append(f'<tr><td class="mono">{E(root)}</td><td class="n">{len(rs)}</td>'
                 f'<td class="n">{human_bytes(size)}</td>'
                 f'<td class="n">{len(good)}</td><td class="n">{len(bad)}</td>'
                 f'<td>{_pill("verified") if not bad else _pill("MISMATCH")} {E(st)}</td></tr>')
    P.append("</table>")
    P.append('<p class="sub">In a duplicate-backup workflow the destinations '
             'should show the same file count and the same total size. A '
             'difference there is the first thing to investigate.</p>')

    # ---- Failures --------------------------------------------------------
    bad_files = [e for e in sess.files if not e.ok]
    if bad_files or sess.errors:
        P.append('<h2 id="fail">Failures</h2><table>'
                 "<tr><th>File</th><th>Destination</th><th>Status</th><th>Detail</th></tr>")
        for e in bad_files:
            if e.error:
                P.append(f'<tr><td class="mono">{E(e.rel)}</td><td>—</td>'
                         f'<td>{_pill("ERROR")}</td><td>{E(e.error)}</td></tr>')
            for d in e.dests:
                if not d.ok:
                    P.append(f'<tr><td class="mono">{E(e.rel)}</td>'
                             f'<td class="mono">{E(d.dest_root)}</td>'
                             f'<td>{_pill(d.status)}</td><td>{E(d.note)}</td></tr>')
        for err in sess.errors:
            P.append(f'<tr><td colspan="4">{E(err)}</td></tr>')
        P.append("</table>")

    # ---- Clips -----------------------------------------------------------
    clips = [e for e in sess.files if e.kind == "clip"]
    if clips:
        P.append(f'<h2 id="clips">Clips ({len(clips)})</h2>')
        for e in clips:
            P.append('<div class="clip">')
            if e.thumb_b64:
                P.append(f'<img src="data:image/jpeg;base64,{e.thumb_b64}" alt="">')
            else:
                P.append('<div class="noimg">no still</div>')
            P.append("<div>")
            P.append(f"<h3>{E(e.rel)}</h3>")
            P.append('<dl class="kv">')
            P.append(f"<dt>Size</dt><dd>{human_bytes(e.size)}</dd>")
            vid = e.meta.get("video") or {}
            aud = e.meta.get("audio") or {}
            if e.meta.get("duration"):
                P.append(f"<dt>Duration</dt><dd>{human_dur(e.meta['duration'])}</dd>")
            if vid:
                P.append(f"<dt>Video</dt><dd>{E(str(vid.get('codec')))} "
                         f"{vid.get('width')}×{vid.get('height')} "
                         f"{vid.get('fps')} fps {E(str(vid.get('pix_fmt')))}</dd>")
            if aud:
                P.append(f"<dt>Audio</dt><dd>{E(str(aud.get('codec')))} "
                         f"{aud.get('channels')} ch {aud.get('sample_rate')} Hz"
                         f"{' ' + str(aud.get('bits')) + '-bit' if aud.get('bits') else ''}</dd>")
            if e.meta.get("bitrate"):
                P.append(f"<dt>Bitrate</dt><dd>{e.meta['bitrate'] / 1e6:.1f} Mb/s</dd>")
            for k in ("make", "model", "creation_time"):
                if e.meta.get(k):
                    P.append(f"<dt>{E(k.replace('_', ' ').title())}</dt>"
                             f"<dd>{E(str(e.meta[k]))}</dd>")
            P.append(f"<dt>Modified</dt><dd>{iso(e.mtime)}</dd>")
            P.append(f"<dt>{E(sess.hash_name)}</dt>"
                     f'<dd class="hash">{E(e.src_hash or "—")}</dd>')
            for d in e.dests:
                P.append(f'<dt>{_pill(d.status)}</dt>'
                         f'<dd class="mono">{E(d.dest_root)}'
                         f'{" — " + E(d.note) if d.note else ""}</dd>')
            P.append("</dl></div></div>")

    # ---- Other files -----------------------------------------------------
    others = [e for e in sess.files if e.kind != "clip" and not e.hidden]
    hidden = [e for e in sess.files if e.hidden]
    if others:
        P.append(f'<h2 id="files">Other Files ({len(others)})</h2>')
        P.append("<table><tr><th>File</th><th>Kind</th><th class=\"n\">Size</th>"
                 f"<th>{E(sess.hash_name)}</th><th>Status</th></tr>")
        for e in others:
            st = "verified" if e.ok else next(
                (d.status for d in e.dests if not d.ok), "ERROR")
            P.append(f'<tr><td class="mono">{E(e.rel)}</td><td>{E(e.kind)}</td>'
                     f'<td class="n">{human_bytes(e.size)}</td>'
                     f'<td class="hash">{E((e.src_hash or "—")[:24])}…</td>'
                     f"<td>{_pill(st)}</td></tr>")
        P.append("</table>")
    if hidden:
        hsize = sum(e.size for e in hidden)
        hok = sum(1 for e in hidden if e.ok)
        P.append(f'<h2 id="hidden">Hidden Files</h2><p>{len(hidden)} hidden file(s), '
                 f"{human_bytes(hsize)}, {hok} verified. Summarised rather than "
                 "listed; the JSON manifest has every one.</p>")

    # ---- Skipped ---------------------------------------------------------
    if sess.skipped or sess.skipped_system:
        n = len(sess.skipped) + len(sess.skipped_system)
        P.append(f'<h2 id="skipped">Skipped ({n})</h2><table>'
                 "<tr><th>Item</th><th>Reason</th></tr>")
        for rel, why in sess.skipped:
            P.append(f'<tr><td class="mono">{E(rel)}</td><td>{E(why)}</td></tr>')
        for rel, why in sess.skipped_system:
            P.append(f'<tr><td class="mono">{E(rel)}</td>'
                     f'<td>{E(why)} — expected, use --all-files to include</td></tr>')
        P.append("</table>")

    # ---- Manifests / logs ------------------------------------------------
    if sess.manifests:
        P.append('<h2 id="manifests">Manifests</h2><table><tr><th>Path</th></tr>')
        for m in sess.manifests:
            P.append(f'<tr><td class="mono">{E(m)}</td></tr>')
        P.append("</table>")

    if sess.warnings or sess.infos:
        P.append('<h2 id="notes">Notes</h2><ul>')
        for w in sess.warnings:
            P.append(f"<li><strong>Warning:</strong> {E(w)}</li>")
        for n in sess.infos:
            P.append(f"<li>{E(n)}</li>")
        P.append("</ul>")

    P.append(f'<footer>offload {VERSION} · {E(platform.node())} · '
             f'started {iso(sess.started)} · finished '
             f'{iso(sess.finished or time.time())}<br>'
             + (CACHE_NOTE_MAC if sys.platform == "darwin" else CACHE_NOTE_OTHER)
             + "</footer>")
    P.append("</div></body></html>")
    return "".join(P)


# ===========================================================================
#  VERIFY-ONLY
# ===========================================================================

def find_manifest(target: str) -> str | None:
    """Newest offload manifest in a folder. Older hops keep their own."""
    import glob
    cands = sorted(glob.glob(os.path.join(target, "offload-manifest*.json")))
    if not cands:
        return None
    return max(cands, key=lambda p: os.path.getmtime(p))


def run_verify_only(target: str, hash_override: str | None) -> int:
    """Re-hash a previous offload against its JSON manifest."""
    man = find_manifest(target)
    if not man:
        sys.stderr.write(f"FATAL: no offload-manifest*.json in {target}\n")
        return 3
    print(f"  manifest: {os.path.basename(man)}")
    try:
        with open(man, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        sys.stderr.write(f"FATAL: cannot read manifest: {e}\n")
        return 3

    hash_name = hash_override or data.get("hash_name") or "md5"
    files = data.get("files", [])
    print(f"\nverifying {len(files)} file(s) in {target} with {hash_name}\n")

    ok = missing = bad = live = 0
    t0 = time.time()
    total = 0
    for fe in files:
        rel, expect = fe.get("rel"), fe.get("src_hash")
        path = os.path.join(target, rel)
        if not expect:
            continue
        # `ascmhl create` APPENDS the new generation to ascmhl_chain.xml after we
        # have already copied and hashed it, so its recorded hash is the
        # pre-mutation one and it mismatches for ever after. Measured 2026-09-07:
        # a healthy two-hop archive reported "1 mismatched" on every check.
        # A byte-frozen expectation is simply the wrong model for a file ASC MHL
        # maintains — so report it as live rather than as corruption. The
        # generation files themselves are immutable and still verified, and
        # `ascmhl diff` is the right tool for the history's own integrity.
        if _is_live_ascmhl_file(rel):
            live += 1
            continue
        if not os.path.isfile(path):
            print(f"  MISSING   {rel}")
            missing += 1
            continue
        h = make_hasher(hash_name)
        try:
            with open_nocache(path) as f:
                while True:
                    b = f.read(CFG["chunk"])
                    if not b:
                        break
                    h.update(b)
                    total += len(b)
        except OSError as e:
            print(f"  ERROR     {rel}: {e.strerror}")
            bad += 1
            continue
        if h.hexdigest() == expect:
            ok += 1
        else:
            print(f"  MISMATCH  {rel}")
            bad += 1

    el = time.time() - t0
    extra = (f", {live} live ASC MHL file(s) not byte-checked "
             f"(use `ascmhl diff`)" if live else "")
    print(f"\n  {ok} verified, {bad} mismatched, {missing} missing{extra}"
          f"  ({human_dur(el)}, {human_rate(total, el)})\n")
    return 0 if (bad == 0 and missing == 0) else 2


def _is_live_ascmhl_file(rel: str) -> bool:
    """
    True for ASC MHL bookkeeping that ascmhl rewrites in place.

    Only the chain file mutates; the numbered generation manifests are immutable
    once written, so those stay byte-verified — losing that would give up real
    coverage for no reason.
    """
    parts = rel.replace(os.sep, "/").split("/")
    return "ascmhl" in parts and parts[-1] == "ascmhl_chain.xml"


# ===========================================================================
#  MAIN
# ===========================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="offload",
        description="Verified card offload to one or more destinations.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("source", nargs="?", help="card or folder to copy from")
    p.add_argument("--dest", "-d", action="append", default=[], metavar="DIR",
                   help="destination volume or folder (repeat for 2-3 backups)")
    p.add_argument("--project", default="", help="project name; becomes a folder")
    p.add_argument("--card", default="", help="card name; becomes a folder "
                                             "(default: source folder name)")
    p.add_argument("--operator", default="", help="who ran it (default: your login name)")
    p.add_argument("--title", default="", help="report title")
    p.add_argument("--notes", default="", help="free text for the report")
    p.add_argument("--logo", default="", metavar="PNG", help="logo for the report")
    p.add_argument("--hash", dest="hash_name", default=None,
                   choices=["xxh64", "md5", "sha1", "sha256"],
                   help="default xxh64 if available, else md5")
    p.add_argument("--ascmhl", action="store_true",
                   help="also write a generation-chained ASC MHL manifest "
                        "(needs: pip3 install ascmhl). Recommended when this "
                        "copy feeds a further archive hop.")
    p.add_argument("--no-mhl", action="store_true",
                   help="skip the classic MHL 1.1 file")
    p.add_argument("--flat", action="store_true",
                   help="copy straight into --dest, no PROJECT/CARD folders")
    p.add_argument("--all-files", action="store_true",
                   help="include .DS_Store, .Spotlight-V100 and friends")
    p.add_argument("--no-thumbs", action="store_true",
                   help="skip thumbnail and metadata extraction")
    p.add_argument("--overwrite", action="store_true",
                   help="replace files already at the destination")
    p.add_argument("--dry-run", action="store_true",
                   help="scan and report what would happen; copy nothing")
    p.add_argument("--verify-only", metavar="DIR",
                   help="re-verify a previous offload against its manifest")
    p.add_argument("--json", action="store_true", help="machine-readable stdout")
    p.add_argument("--quiet", "-q", action="store_true", help="no progress lines")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    if args.verify_only:
        return run_verify_only(args.verify_only, args.hash_name)

    if not args.source:
        sys.stderr.write("FATAL: give a source, or use --verify-only DIR\n")
        return 3
    source = os.path.abspath(os.path.expanduser(args.source))
    if not os.path.isdir(source):
        sys.stderr.write(f"FATAL: source is not a directory: {source}\n")
        return 3
    if not args.dest:
        sys.stderr.write("FATAL: at least one --dest is required\n")
        return 3

    hash_name, hw = resolve_hash(args.hash_name)
    card = args.card or os.path.basename(source.rstrip(os.sep)) or "CARD"

    sess = Session(
        source=source,
        dest_roots=[],
        hash_name=hash_name,
        project=args.project,
        card=card,
        operator=args.operator or os.environ.get("USER") or os.environ.get("USERNAME", ""),
        title=args.title,
        notes=args.notes,
        started=time.time(),
        dry_run=args.dry_run,
    )
    sess.infos.extend(hw)
    if hash_name not in CFG["mhl_legal"]:
        sess.warnings.append(
            f"{hash_name} is not in the MHL 1.1 spec, so no .mhl was written. "
            "The JSON manifest still records every hash. Use --hash xxh64, md5, "
            "or sha1 if you need an MHL for a post house.")

    if args.logo:
        try:
            with open(os.path.expanduser(args.logo), "rb") as f:
                sess.logo_b64 = base64.b64encode(f.read()).decode("ascii")
        except OSError as e:
            sess.warnings.append(f"could not read logo: {e.strerror}")

    # ---- scan ------------------------------------------------------------
    sess.files, sess.skipped, sess.skipped_system = scan(source, args.all_files)
    if not sess.files:
        sys.stderr.write(f"FATAL: no files found under {source}\n")
        return 3
    payload = sum(e.size for e in sess.files)

    # ---- resolve destinations, check headroom ----------------------------
    dest_dirs: list[str] = []
    for d in args.dest:
        root = os.path.abspath(os.path.expanduser(d))
        if not os.path.isdir(root):
            sys.stderr.write(f"FATAL: destination is not a directory: {root}\n")
            return 3
        target = root if args.flat else os.path.join(
            root, *(p for p in (sess.project, sess.card) if p))
        try:
            need = payload + CFG["free_space_margin"]
            avail = free_bytes(root)
            if avail < need:
                sys.stderr.write(
                    f"FATAL: {root} has {human_bytes(avail)} free; "
                    f"need {human_bytes(need)}\n")
                return 3
        except OSError as e:
            sess.warnings.append(f"could not check free space on {root}: {e.strerror}")
        if os.path.abspath(target) == os.path.abspath(source):
            sys.stderr.write("FATAL: destination is the source\n")
            return 3
        dest_dirs.append(target)
    sess.dest_roots = dest_dirs

    clips = sum(1 for e in sess.files if e.kind == "clip")
    if not args.quiet:
        print(f"\noffload {VERSION}")
        print(f"  source       {source}")
        for t in dest_dirs:
            print(f"  destination  {t}")
        print(f"  {len(sess.files)} files ({clips} clips), "
              f"{human_bytes(payload)} × {len(dest_dirs)} destination(s) = "
              f"{human_bytes(payload * len(dest_dirs))}")
        print(f"  hash {hash_name}\n")

    if args.dry_run:
        for e in sess.files:
            print(f"  would copy  {e.rel}  ({human_bytes(e.size)})")
        for rel, why in sess.skipped + sess.skipped_system:
            print(f"  would skip  {rel} — {why}")
        print(f"\n  dry run, nothing written\n")
        return 0

    # ---- copy + verify ---------------------------------------------------
    done_bytes = 0
    for i, e in enumerate(sess.files, 1):
        src_path = os.path.join(source, e.rel)
        targets = [(t, os.path.join(t, e.rel)) for t in dest_dirs]
        if not args.quiet:
            pct = 100.0 * done_bytes / payload if payload else 100.0
            label = e.rel if len(e.rel) <= 48 else "…" + e.rel[-47:]
            line = f"  [{pct:5.1f}%] {i}/{len(sess.files)} {label}"
            sys.stdout.write("\r" + line.ljust(76)[:76])
            sys.stdout.flush()
        copy_one(src_path, e, targets, hash_name, args.overwrite)
        verify_one(e, hash_name)
        done_bytes += e.size
    if not args.quiet:
        sys.stdout.write("\r" + " " * 78 + "\r")

    sess.finished = time.time()

    # ---- clip awareness --------------------------------------------------
    if not args.no_thumbs:
        if not have("ffprobe"):
            sess.warnings.append("ffprobe not found, so clips have no metadata "
                                 "or stills in the report.")
        else:
            enrich_clips(sess, want_thumbs=have("ffmpeg"))

    # ---- manifests + reports --------------------------------------------
    base_stamp = dt.datetime.fromtimestamp(sess.started).strftime("%Y%m%d-%H%M%S")
    for t in dest_dirs:
        try:
            os.makedirs(t, exist_ok=True)
            # Stamped, not a fixed name: copying an offload onward to an
            # archive brings the previous hop's manifest with it, and a fixed
            # name would overwrite the record of where the files came from.
            # Resolved per destination, since a same-second second hop would
            # otherwise collide.
            stamp = unique_stamp(t, base_stamp)
            jm = os.path.join(t, f"offload-manifest-{stamp}.json")
            write_json_manifest(sess, jm)
            sess.manifests.append(jm)

            if not args.no_mhl:
                mhl = os.path.join(t, f"{sess.card}-{stamp}.mhl")
                if write_mhl(sess, t, mhl):
                    sess.manifests.append(mhl)

            rp = os.path.join(t, f"offload-report-{stamp}.html")
            with open(rp, "w", encoding="utf-8") as f:
                f.write(render_html(sess))
            sess.manifests.append(rp)

            # Last, so the generation covers the media *and* the manifests and
            # report sitting beside it.
            if args.ascmhl:
                ok, detail = write_ascmhl(sess, t)
                if ok:
                    sess.manifests.append(detail)
                else:
                    sess.warnings.append(
                        f"ASC MHL chain incomplete for {t}: {detail}")
        except OSError as e:
            sess.errors.append(f"could not write manifests to {t}: {e.strerror}")

    if args.json:
        print(json.dumps(session_dict(sess), indent=2))
    else:
        print(render_text(sess))

    return verdict(sess)[1]


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.stderr.write("\ninterrupted — destinations may be incomplete\n")
        sys.exit(3)

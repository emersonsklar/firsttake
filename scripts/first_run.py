#!/usr/bin/env python3
"""
first_run — a practice run on test footage (it takes seconds), before the first real shoot.

    python first_run.py                      # writes to Documents/firsttake-first-run
    python first_run.py --out <folder>
    python first_run.py --open               # also open the offload report in the browser
    python first_run.py --selftest

What it does, in order, on sample footage that ships with it (nothing of yours is read or changed):

  1. makes a practice "camera card" from a short two-camera interview -- a wide shot and a
     tight shot -- with a problem planted in each: the interviewer's wireless channel dead
     (the transmitter-left-off mistake), and the tight camera's audio recorded too hot
  2. offloads the card to two backup folders and verifies every copy
  3. damages one byte of one backup on purpose, and shows the re-check catching it
  4. runs footage QC on the clips and reports what it found, in plain words
  5. syncs the two cameras from their audio alone and checks the answer against the
     known offset

The sample interview is synthetic (see first_run_footage/README.md for how it was made).

It exists so the
first time someone sees a verified offload or a QC report is not on a real card at a real shoot.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):                      # see doctor.py: Windows + pipes
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                         # noqa: BLE001
        pass

HERE = Path(__file__).resolve().parent
PY = sys.executable
FOOTAGE = HERE / "first_run_footage"
STEPS = 5
SYNC_TOLERANCE_S = 0.05


def default_out() -> Path:
    docs = Path.home() / "Documents"
    return (docs if docs.is_dir() else Path.home()) / "firsttake-first-run"


def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(n: int, title: str) -> None:
    say(f"\n── Step {n} of {STEPS}: {title}")


def tool(name: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONUTF8="1")
    return subprocess.run([PY, str(HERE / name), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)


def make_clip(src: Path, path: Path, audio_filter: str) -> None:
    """Copy a sample clip with a fault planted in its audio. The picture is copied, not re-encoded."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src),
           "-map", "0:v", "-map", "0:a", "-c:v", "copy",
           "-af", audio_filter, "-c:a", "pcm_s24le", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError("ffmpeg couldn't make the practice clip: " + r.stderr.strip()[:300])


def verdict_of(r: subprocess.CompletedProcess) -> str:
    return {0: "VERIFIED", 1: "VERIFIED WITH WARNINGS", 2: "FAILED"}.get(r.returncode, "ERROR")


def run_tour(out: Path, open_report: bool) -> int:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        say("ffmpeg isn't set up yet, so the tour can't make practice clips.\n"
            "Run the setup check first:  doctor.py --fix")
        return 2
    truth_file = FOOTAGE / "ground_truth.json"
    if not truth_file.is_file():
        say(f"The sample footage is missing ({FOOTAGE}). Reinstall FirstTake to restore it.")
        return 2
    truth = json.loads(truth_file.read_text(encoding="utf-8"))
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    base = out / stamp
    card = base / "practice-card"
    backups = [base / "backup-drive-1", base / "backup-drive-2"]
    say(f"First run: a practice run on test footage, start to finish.\nEverything goes in: {base}")

    step(1, "a practice camera card")
    media = card / "DCIM" / "100MEDIA"
    clips = {
        # channel 2 is the interviewer's lav; -60 dB on it alone, channel 1 untouched
        "C0001.MOV": (truth["a"]["file"], "pan=stereo|c0=c0|c1=0.001*c1",
                      "camera A, the wide shot: the interviewer's wireless channel is almost "
                      "silent — like a transmitter that was left off"),
        "C0002.MOV": (truth["b"]["file"], "volume=18dB",
                      "camera B, the tight shot: audio recorded far too loud"),
    }
    for name, (src, filt, _) in clips.items():
        make_clip(FOOTAGE / src, media / name, filt)
    say("Made 2 clips from a short two-camera interview, each with a problem planted on purpose:")
    for name, (_, _, why) in clips.items():
        say(f"  • {name}: {why}")
    say("(On a real shoot each camera has its own card; they share one here to keep it short.)")

    step(2, "offload the card to two backups, and verify every copy")
    for b in backups:
        b.mkdir(parents=True, exist_ok=True)
    args = [str(card), "--project", "Practice", "--card", "A001", "--quiet",
            "--operator", os.environ.get("USERNAME") or os.environ.get("USER") or "first-run"]
    for b in backups:
        args += ["--dest", str(b)]
    r = tool("offload.py", *args)
    v = verdict_of(r)
    if v.startswith("VERIFIED"):
        say(f"Result: {v}. Both backups were read back and match the card, byte for byte.")
    else:
        say(f"Result: {v} — the offload itself didn't complete, so the tour stops here.")
        say((r.stdout + r.stderr).strip()[-800:])
        return 2
    reports = sorted((backups[0] / "Practice" / "A001").glob("offload-report-*.html"))
    report = reports[-1] if reports else None
    if report:
        say(f"The offload report (open it in a browser): {report}")
    if sys.platform != "darwin":
        say("On a real shoot on this computer: eject and reconnect each backup drive, then run the\n"
            "re-check (step 3 shows it) before wiping the card. The report explains why.")

    step(3, "damage one backup on purpose, then re-check it")
    victim = backups[1] / "Practice" / "A001" / "DCIM" / "100MEDIA" / "C0001.MOV"
    with open(victim, "r+b") as f:
        f.seek(4096)
        b = f.read(1)
        f.seek(4096)
        f.write(bytes([b[0] ^ 0xFF]))
    say("Changed a single byte inside backup-drive-2's copy of C0001.MOV — the kind of silent\n"
        "damage a failing drive or a bad cable causes, and that you can't see by looking.")
    r = tool("offload.py", "--verify-only", str(backups[1] / "Practice" / "A001"), "--quiet")
    caught = r.returncode == 2
    say(f"Re-check result: {verdict_of(r)}. " +
        ("It found the damaged file. On a real shoot, this is the moment you'd re-copy from the "
         "card instead of wiping it." if caught else
         "⚠️ It did NOT flag the damage — that's a problem with this setup; please report it."))

    step(4, "footage QC on the clips")
    r = tool("shootcheck.py", "--json", "--dir", str(card / "DCIM" / "100MEDIA"))
    try:
        qc = json.loads(r.stdout)
    except ValueError:
        say("QC didn't return a result:\n" + (r.stdout + r.stderr).strip()[-600:])
        return 2
    say(f"QC verdict: {qc.get('verdict')}. It measures the files themselves — exposure, focus,\n"
        "audio levels, frame rate — rather than trusting the camera's monitor.")
    priority = ("peak", "loudness", "clip", "channel", "noise")

    def rank(x):
        check = (x.get("check") or "").lower()
        return (0 if x.get("level") == "FAIL" else 1,
                next((i for i, w in enumerate(priority) if w in check), len(priority)))

    for f in qc.get("files", []):
        audio = sorted((x for x in f.get("findings", [])
                        if x.get("level") in ("FAIL", "WARN")
                        and any(w in (x.get("check") or "").lower() for w in priority)), key=rank)
        say(f"  • {f.get('name')}: " + (audio[0]["message"].strip()[:140] if audio
                                        else "no audio problems found"))
    say("It also flags the practice clips' small size and 8-bit video: QC compares footage with\n"
        "your kit profile, and until you've made one it uses a demanding default (4K, 10-bit).\n"
        "Making your profile is the first thing to do with Claude before a real shoot.")

    step(5, "sync the two cameras from their audio")
    r = tool("sync_cameras.py", "--json", str(media / "C0001.MOV"), str(media / "C0002.MOV"))
    try:
        sync = next(iter(json.loads(r.stdout).values()))
    except (ValueError, StopIteration):
        say("Sync didn't return a result:\n" + (r.stdout + r.stderr).strip()[-600:])
        return 2
    planted = float(truth["b_offset_s"])
    got = sync.get("offset")
    synced = got is not None and abs(got - planted) <= SYNC_TOLERANCE_S
    say("The two cameras share no timecode and nobody clapped. Camera B was started "
        f"{planted:.2f} s after camera A.")
    if got is None:
        say("⚠️ Sync found no match between the two clips — that's a problem with this setup; "
            "please report it.")
    else:
        say(f"Sync measured {got:+.3f} s from the sound alone ({sync['locked']} of "
            f"{sync['total']} probes agree)" +
            (", even with the faults planted in step 1." if synced else
             f" — ⚠️ more than {SYNC_TOLERANCE_S * 1000:.0f} ms off; please report it."))
    say("These clips are too short to measure drift (it needs a couple of minutes); on a real\n"
        "interview the same tool also tells you whether the two cameras' clocks drift apart.")

    say("\n── Done")
    say(f"• Offload report: {report}" if report else "• (no offload report was written)")
    say(f"• Practice files: {base}  (safe to delete)")
    say("Next: ask Claude to set up your kit profile, then plan your first real shoot.")
    if open_report and report:
        webbrowser.open(report.as_uri())
    return 0 if caught and synced else 1


def selftest() -> int:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        rc = run_tour(Path(td), open_report=False)
        ok = rc == 0 and any(Path(td).rglob("offload-report-*.html"))
    print(f"\n{'PASS' if ok else 'FAIL'}: first run completed end to end" + ("" if ok else f" (exit {rc})"))
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=None, help="where to put the practice files")
    ap.add_argument("--open", action="store_true", help="open the offload report when done")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    return run_tour(a.out or default_out(), a.open)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
first_run — a practice run on test footage (it takes seconds), before the first real shoot.

    python first_run.py                      # writes to Documents/firsttake-first-run
    python first_run.py --out <folder>
    python first_run.py --open               # also open the offload report in the browser
    python first_run.py --selftest

What it does, in order, on clips it makes itself (nothing of yours is read or changed):

  1. makes a practice "camera card" with two short clips -- one with dead-silent audio (the
     transmitter-left-off mistake), one recorded too hot
  2. offloads the card to two backup folders and verifies every copy
  3. damages one byte of one backup on purpose, and shows the re-check catching it
  4. runs footage QC on the clips and reports what it found, in plain words

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


def default_out() -> Path:
    docs = Path.home() / "Documents"
    return (docs if docs.is_dir() else Path.home()) / "firsttake-first-run"


def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(n: int, title: str) -> None:
    say(f"\n── Step {n} of 4: {title}")


def tool(name: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONUTF8="1")
    return subprocess.run([PY, str(HERE / name), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)


def make_clip(path: Path, audio_filter: str) -> None:
    """A 6-second 720p clip at 29.97 with a test pattern and a 440 Hz tone."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30000/1001:duration=6",
           "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration=6,{audio_filter}",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s24le", "-ac", "2",
           "-shortest", str(path)]
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
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    base = out / stamp
    card = base / "practice-card"
    backups = [base / "backup-drive-1", base / "backup-drive-2"]
    say(f"First run: a practice run on test footage, start to finish.\nEverything goes in: {base}")

    step(1, "a practice camera card")
    clips = {
        "C0001.MOV": ("volume=-60dB", "audio almost silent — like a wireless mic that was left off"),
        "C0002.MOV": ("volume=18dB", "audio recorded far too loud"),
    }
    for name, (filt, _) in clips.items():
        make_clip(card / "DCIM" / "100MEDIA" / name, filt)
    say("Made 2 six-second clips, each with a problem planted on purpose:")
    for name, (_, why) in clips.items():
        say(f"  • {name}: {why}")

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

    say("\n── Done")
    say(f"• Offload report: {report}" if report else "• (no offload report was written)")
    say(f"• Practice files: {base}  (safe to delete)")
    say("Next: ask Claude to set up your kit profile, then plan your first real shoot.")
    if open_report and report:
        webbrowser.open(report.as_uri())
    return 0 if caught else 1


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

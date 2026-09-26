#!/usr/bin/env python3
"""
build_day_sheets — render 09-DAY-SHEETS.html to day-sheets.pdf, and check it.

Why this exists: day-sheets.pdf had no generator. It was printed by hand, so every
edit to the HTML silently left the page actually carried into a client site stale.
That is the same failure mode KIT.md warns about, in its worst form -- the paper in
your hand disagreeing with the source of truth, with nothing to catch it.

    ./build_day_sheets.py            # render + verify
    ./build_day_sheets.py --check    # verify only; non-zero if the PDF is stale

Exit codes:
    0  PDF is current and passed its checks
    1  PDF is stale, or a check failed
    2  cannot render (no Chrome) -- the HTML is still fine to print by hand
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "09-DAY-SHEETS.html")
PDF = os.path.join(HERE, "day-sheets.pdf")

# Phrases that must survive into the PDF. Not decoration: each is an instruction
# whose absence from the printed sheet has a real cost on the day. Checking the
# rendered PDF rather than the HTML is the entire point -- the HTML being right is
# what we already knew.
MUST_CONTAIN = [
    "offload.py",           # the offload tool, post-2026-09-07
    "VERIFIED",             # the exit condition that gates formatting anything
    "sidecars",             # deleting one breaks the ASC MHL chain
    # The retention condition, stated as the CONCEPT rather than one phrasing. This slot used to
    # assert the literal word "receipt", which pinned the printed sheet to a superseded rule: a
    # checklist corrected to "live AND archive verified" would have FAILED this gate. Assert what
    # the instruction has to convey, and ban the old wording below.
    "archive",              # ... is verified: one half of the retention condition
    "LIVE",                 # ... and the deliverable is live: the other half
    "clap",                 # the only sync point between the two cameras
]

# Instructions that were corrected and must NOT reappear. A stale sheet is worse
# than no sheet, because it is followed with confidence.
MUST_NOT_CONTAIN = [
    "Offload with Resolve",
    "Offload with DaVinci",
    # The superseded retention rule. It is not false, it is INCOMPLETE -- a post house can confirm
    # receipt and still come back weeks later for a reel nobody kept -- and an incomplete rule about
    # formatting cards is followed with confidence.
    "confirms receipt",
]


def find_chrome() -> str | None:
    for c in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/Applications/Chromium.app/Contents/MacOS/Chromium",
              shutil.which("google-chrome"), shutil.which("chromium")):
        if c and os.path.exists(c):
            return c
    return None


def render() -> bool:
    chrome = find_chrome()
    if not chrome:
        print("  no Chrome/Chromium found — cannot render.")
        print("  Open 09-DAY-SHEETS.html in a browser and print to day-sheets.pdf.")
        return False
    subprocess.run([chrome, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={PDF}", "file://" + SRC],
                   capture_output=True, timeout=180)
    if not os.path.exists(PDF):
        print("  render FAILED — no PDF written")
        return False
    print(f"  wrote {os.path.basename(PDF)}")
    return True


def pdf_text() -> str | None:
    """Extract text, or None if we cannot. None must never be read as 'clean'."""
    try:
        import pymupdf
    except ImportError:
        return None
    try:
        with pymupdf.open(PDF) as d:
            return "\n".join(p.get_text() for p in d)
    except Exception:
        return None


def page_count() -> int | None:
    try:
        import pymupdf
        with pymupdf.open(PDF) as d:
            return d.page_count
    except Exception:
        return None


def check() -> int:
    fails = 0

    if not os.path.exists(PDF):
        print("  FAIL  day-sheets.pdf does not exist")
        return 1

    # Staleness is the whole reason for this script.
    if os.path.getmtime(PDF) < os.path.getmtime(SRC):
        print("  FAIL  day-sheets.pdf is OLDER than 09-DAY-SHEETS.html — stale")
        fails += 1
    else:
        print("  ok    PDF is newer than its source")

    n = page_count()
    if n is None:
        print("  ??    page count unknown (pip3 install pymupdf to check)")
    else:
        # Overflow to an extra page silently drops nothing but splits a section
        # across a page break mid-checklist, which is how a step gets missed.
        print(f"  {'ok  ' if n <= 3 else 'WARN'}  {n} page(s)"
              + ("" if n <= 3 else " — unexpectedly long, check for overflow"))

    txt = pdf_text()
    if txt is None:
        print("  ??    cannot extract PDF text (pip3 install pymupdf) — content "
              "NOT verified. Do not treat this as a pass.")
        return 1 if fails else 0

    for phrase in MUST_CONTAIN:
        if phrase.lower() in txt.lower():
            print(f"  ok    PDF contains {phrase!r}")
        else:
            print(f"  FAIL  PDF is missing {phrase!r}")
            fails += 1

    for phrase in MUST_NOT_CONTAIN:
        # Checking the OLD text is gone, not merely that the new text is present.
        # Verifying only the new text is how three of four stale copies survived
        # a fix, twice, on this project.
        if re.search(re.escape(phrase), txt, re.I):
            print(f"  FAIL  PDF still contains superseded text {phrase!r}")
            fails += 1
        else:
            print(f"  ok    superseded text {phrase!r} is gone")

    return 1 if fails else 0


def main(argv: list[str]) -> int:
    if not os.path.exists(SRC):
        print(f"FATAL: {SRC} not found")
        return 2

    if "--check" not in argv:
        print("rendering:")
        if not render():
            return 2

    print("checking:")
    rc = check()
    print("\n  day sheets OK" if rc == 0 else "\n  day sheets NEED ATTENTION")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

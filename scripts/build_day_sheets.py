#!/usr/bin/env python3
"""
build_day_sheets — render a shoot's day-sheet HTML to a PDF, and check the PDF.

Why this exists: day-sheets.pdf had no generator. It was printed by hand, so every
edit to the HTML silently left the page actually carried into a client site stale.
That is the same failure mode KIT.md warns about, in its worst form -- the paper in
your hand disagreeing with the source of truth, with nothing to catch it.

A day sheet belongs to ONE SHOOT, so it lives in that shoot's folder and this has to be pointed
at it. The PDF is written beside the source, never into this skill.

    cd <shoot folder> && /path/to/build_day_sheets.py      # finds 09-DAY-SHEETS.html here
    ./build_day_sheets.py --src <shoot>/09-DAY-SHEETS.html
    FILM_SHOOT_DAY_SHEETS=<path> ./build_day_sheets.py
    ./build_day_sheets.py --src <path> --check             # verify only
    ./build_day_sheets.py --selftest

Exit codes:
    0  PDF is current and passed its checks
    1  PDF is stale, or a check failed
    2  cannot render (no Chrome), or no day-sheet HTML was found -- in either case the
       HTML itself is still fine to print by hand
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# 🔴 A DAY SHEET BELONGS TO ONE SHOOT, NOT TO THIS SKILL.
#
# These used to be `os.path.join(HERE, ...)` — a filename from one operator's working folder, hard
# coded inside the skill. Two things followed, and both only show up for someone who did not write
# it: in a fresh checkout the input can never exist, so the tool is unrunnable and the first
# tutorial that mentions it dead-ends; and when it DID run it wrote a specific shoot's PDF into the
# skill checkout, which is the durable layer holding a per-shoot artefact.
#
# So the source is now a required argument, discovered in this order, and the PDF lands beside it.
SRC = None          # set by resolve_src()
PDF = None

DEFAULT_NAMES = ("09-DAY-SHEETS.html", "day-sheets.html", "DAY-SHEETS.html")


def resolve_src(argv: list[str]) -> str | None:
    """--src PATH, else $FILM_SHOOT_DAY_SHEETS, else a known name in the working directory."""
    for i, a in enumerate(argv):
        if a == "--src" and i + 1 < len(argv):
            return os.path.abspath(argv[i + 1])
        if a.startswith("--src="):
            return os.path.abspath(a.split("=", 1)[1])
    env = os.environ.get("FILM_SHOOT_DAY_SHEETS")
    if env:
        return os.path.abspath(env)
    for name in DEFAULT_NAMES:
        p = os.path.join(os.getcwd(), name)
        if os.path.exists(p):
            return p
    return None


def resolve_pdf(argv: list[str], src: str) -> str:
    """--pdf PATH, else `day-sheets.pdf` beside the source — the name already in use."""
    for i, a in enumerate(argv):
        if a == "--pdf" and i + 1 < len(argv):
            return os.path.abspath(argv[i + 1])
        if a.startswith("--pdf="):
            return os.path.abspath(a.split("=", 1)[1])
    return os.path.join(os.path.dirname(src), "day-sheets.pdf")

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
        print(f"  Open {os.path.basename(SRC)} in a browser and print to "
              f"{os.path.basename(PDF)}.")
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
        print(f"  FAIL  {os.path.basename(PDF)} does not exist")
        return 1

    # Staleness is the whole reason for this script.
    if os.path.getmtime(PDF) < os.path.getmtime(SRC):
        print(f"  FAIL  {os.path.basename(PDF)} is OLDER than "
              f"{os.path.basename(SRC)} — stale")
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
    global SRC, PDF
    SRC = resolve_src(argv)

    if SRC is None or not os.path.exists(SRC):
        # Say what this file IS and where it lives, because the previous message named a path
        # inside the skill that will never exist and left the reader with nowhere to go.
        print("build_day_sheets: no day-sheet HTML to render.\n")
        print("  A day sheet belongs to ONE SHOOT, so it lives in that shoot's folder — not in")
        print("  this skill. Point the tool at it:\n")
        print("      ./build_day_sheets.py --src /path/to/<shoot>/09-DAY-SHEETS.html")
        print("      FILM_SHOOT_DAY_SHEETS=/path/to/sheet.html ./build_day_sheets.py")
        print("      cd <shoot folder> && %s/build_day_sheets.py" % HERE)
        print("\n  Run from a shoot folder it looks for: %s" % ", ".join(DEFAULT_NAMES))
        if SRC is not None:
            print("\n  (looked for %s and it is not there)" % SRC)
        print("\n  references/templates/ has a starting point if you have not written one yet.")
        return 2

    # Beside the source, and KEEPING THE ESTABLISHED NAME.
    #
    # ⚠️ I first named it after the source (`09-DAY-SHEETS.pdf`). That is tidier and it is wrong:
    # a `day-sheets.pdf` already exists in shoot folders, so renaming the target orphans the real
    # printed sheet and makes the tool report "does not exist" with a current PDF sitting beside it.
    # Simplifying a path is not free when something is already on disk at the old one.
    PDF = resolve_pdf(argv, SRC)
    print(f"source: {SRC}")
    print(f"pdf:    {PDF}\n")

    if "--check" not in argv:
        print("rendering:")
        if not render():
            return 2

    print("checking:")
    rc = check()
    print("\n  day sheets OK" if rc == 0 else "\n  day sheets NEED ATTENTION")
    return rc


def selftest() -> int:
    """Resolution and placement only — rendering needs Chrome and a real sheet."""
    import tempfile
    failures = []

    def check(name, cond, detail=""):
        if not cond:
            failures.append(name)
        print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"   {detail}" if detail and not cond
                                                          else ""))

    with tempfile.TemporaryDirectory() as td:
        sheet = os.path.join(td, "09-DAY-SHEETS.html")
        open(sheet, "w", encoding="utf-8").write("<html><body>clap · offload.py · VERIFIED · sidecars · "
                               "archive · LIVE</body></html>")

        check("--src is honoured", resolve_src(["--src", sheet]) == sheet)
        check("--src=PATH form is honoured", resolve_src([f"--src={sheet}"]) == sheet)

        env_before = os.environ.pop("FILM_SHOOT_DAY_SHEETS", None)
        os.environ["FILM_SHOOT_DAY_SHEETS"] = sheet
        check("the environment variable is honoured", resolve_src([]) == sheet)
        os.environ.pop("FILM_SHOOT_DAY_SHEETS")

        cwd = os.getcwd()
        try:
            os.chdir(td)
            # ⚠️ realpath both sides: on macOS a temp dir is /var/... which is a symlink to
            # /private/var/..., and os.getcwd() returns the resolved form. Comparing the raw
            # strings fails on the symlink, not on the lookup, which is a test bug dressed as a
            # code bug.
            check("found by name in the working directory",
                  os.path.realpath(resolve_src([]) or "") == os.path.realpath(sheet))
        finally:
            os.chdir(cwd)

        # 🔴 The regression that motivated this: nothing may be looked for inside the skill.
        check("does NOT look inside the skill", resolve_src([]) is None
              or not str(resolve_src([])).startswith(HERE),
              f"resolved to {resolve_src([])}")

        # A missing sheet is exit 2 with an actionable message, not a claim of staleness.
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = main(["--check"])
        msg = out.getvalue()
        check("no sheet -> exit 2", rc == 2, f"got {rc}")
        check("no sheet -> says it belongs to one shoot", "ONE SHOOT" in msg)
        check("no sheet -> shows how to point at one", "--src" in msg)
        check("no sheet -> does NOT claim the PDF is stale", "stale" not in msg.lower())

        # The PDF lands beside the source, never in the skill.
        with contextlib.redirect_stdout(io.StringIO()):
            main(["--src", sheet, "--check"])
        # `day-sheets.pdf`, NOT a name derived from the source — see resolve_pdf().
        check("PDF keeps the established name, beside the source",
              os.path.realpath(PDF) == os.path.realpath(os.path.join(td, "day-sheets.pdf")),
              f"got {PDF}")
        check("PDF path is NOT inside the skill", not str(PDF).startswith(HERE))

        if env_before is not None:
            os.environ["FILM_SHOOT_DAY_SHEETS"] = env_before

    print(f"\n{'PASS' if not failures else 'FAIL'}: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(selftest())
    sys.exit(main(sys.argv[1:]))

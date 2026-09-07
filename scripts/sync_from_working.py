#!/usr/bin/env python3
"""
sync_from_working — pull tool changes out of a personal working folder into the
skill, and flag the documentation that needs generalising by hand.

The tools exist in two places on purpose for now: a personal working folder used on
a live shoot, and this skill, which is shared. Two copies of an instruction is how a
stale one survives a fix -- so this exists to make the divergence loud instead of
silent.

    ./scripts/sync_from_working.py ~/path/to/workingFolder            # report
    ./scripts/sync_from_working.py ~/path/to/workingFolder --apply    # copy tools over

## What it does and does not do

**Tools it syncs automatically.** They are hardware-agnostic, so a fix on a shoot is
a fix for everyone. After applying, it runs the fault-planting harnesses -- a tool
that silently stops detecting a fault is worse than no tool -- and then the scrub
check.

**Documentation it will NOT sync.** Working docs carry client names, room
measurements, and one kit's numbers. Copying them would leak the client and hand
other operators numbers measured on gear they do not own, which is worse than giving
them nothing. Instead it lists which durable docs changed and leaves you to
generalise the learning: strip the specifics, keep the method, add it to the right
reference file.

Exit codes:
    0  in sync (or applied cleanly)
    1  divergence found in report mode, or a post-apply check failed
    2  could not run
"""

from __future__ import annotations

import argparse
import filecmp
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# skill-relative path -> path within the working folder
TOOLS = {
    "scripts/shootcheck.py": "analyzer/shootcheck.py",
    "scripts/make_fixtures.py": "analyzer/make_fixtures.py",
    "scripts/verify_fixtures.py": "analyzer/verify_fixtures.py",
    "scripts/offload.py": "offload/offload.py",
    "scripts/offload_selftest.py": "offload/selftest.py",
    "scripts/build_day_sheets.py": "build_day_sheets.py",
}

# Durable docs in the working folder. Changes here usually mean a learning was
# written back, and the generic version of that learning belongs in the skill.
# Mapped to where its generalised form most likely goes.
DURABLE_DOCS = {
    "KIT.md": "SKILL.md + references/method.md",
    "kit-CHANGELOG.md": "whichever reference the learning belongs to",
    "04-EQUIPMENT.md": "references/templates/kit-profile-TEMPLATE.md",
    "06-VERIFIED-SPECS.md": "references/onboarding.md (as a thing to MEASURE, "
                            "never as a value to copy)",
    "03-ONSITE-CHECKLIST.md": "references/templates/onsite-checklist-TEMPLATE.md",
    "08-POST-HOUSE-SPEC.md": "references/templates/post-house-spec-TEMPLATE.md",
    "analyzer/README.md": "references/qc-interpretation.md",
    "offload/README.md": "references/offload-protocol.md",
}


def run(cmd: list[str], cwd: str | None = None) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return p.returncode, (p.stdout + p.stderr)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("working", help="path to the personal working folder")
    ap.add_argument("--apply", action="store_true",
                    help="copy diverged tools into the skill")
    ap.add_argument("--since", default="7 days ago",
                    help="how far back to look for changed docs (git date, "
                         "default: '7 days ago')")
    args = ap.parse_args(argv)

    work = os.path.abspath(os.path.expanduser(args.working))
    if not os.path.isdir(work):
        print(f"FATAL: {work} is not a directory")
        return 2

    # ---- tools -----------------------------------------------------------
    print("tools")
    diverged, missing = [], []
    for skill_rel, work_rel in sorted(TOOLS.items()):
        a = os.path.join(ROOT, skill_rel)
        b = os.path.join(work, work_rel)
        if not os.path.exists(b):
            print(f"  ??    {work_rel} not found in the working folder")
            missing.append(work_rel)
            continue
        if not os.path.exists(a):
            print(f"  NEW   {skill_rel} missing here")
            diverged.append((skill_rel, a, b))
            continue
        if filecmp.cmp(a, b, shallow=False):
            print(f"  ok    {skill_rel}")
        else:
            print(f"  DIFF  {skill_rel}")
            diverged.append((skill_rel, a, b))

    if diverged and not args.apply:
        print("\n  diff, then re-run with --apply:")
        for rel, a, b in diverged:
            print(f"    diff -u {a} {b}")

    if diverged and args.apply:
        print("\napplying")
        for rel, a, b in diverged:
            shutil.copy2(b, a)
            print(f"  copied {rel}")

        # A tool is only worth syncing if it still catches what it claims to.
        print("\nharnesses")
        rc1, out1 = run([sys.executable, "offload_selftest.py"], cwd=HERE)
        print("  offload:  " + (out1.strip().split("\n")[-1] if out1 else "?"))
        rc2, out2 = run([sys.executable, "verify_fixtures.py"], cwd=HERE)
        tail = [l for l in out2.strip().split("\n") if l.strip()][-1:] if out2 else []
        print("  analyzer: " + (tail[0] if tail else "? (run make_fixtures.py first)"))

        print("\nscrub")
        rc3, out3 = run([sys.executable, "scrub_check.py"], cwd=HERE)
        for line in out3.strip().split("\n")[-4:]:
            print("  " + line)

        if rc1 or rc2 or rc3:
            print("\n  a post-apply check FAILED — do not commit")
            return 1

    # ---- docs ------------------------------------------------------------
    print("\ndocs changed in the working folder "
          f"(since {args.since!r}) — generalise these by hand")
    rc, out = run(["git", "-C", work, "log", "--since", args.since,
                   "--name-only", "--pretty=format:"], cwd=work)
    changed = {ln.strip() for ln in out.split("\n") if ln.strip()} if rc == 0 else set()
    if rc != 0:
        print("  (working folder is not a git repo — checking mtimes instead)")
        import time
        cutoff = time.time() - 7 * 86400
        changed = {rel for rel in DURABLE_DOCS
                   if os.path.exists(os.path.join(work, rel))
                   and os.path.getmtime(os.path.join(work, rel)) > cutoff}

    hits = sorted(changed & set(DURABLE_DOCS))
    if not hits:
        print("  none")
    else:
        for rel in hits:
            print(f"  {rel}")
            print(f"      -> {DURABLE_DOCS[rel]}")
        print("\n  For each: would this be true on a DIFFERENT shoot with DIFFERENT")
        print("  gear? If yes, write the generic form into the file above. Strip the")
        print("  client, the room, and any number measured on one specific body —")
        print("  a measured value belongs in that operator's own kit profile, and")
        print("  handing it to someone else is how an unverified number gets")
        print("  trusted on set.")

    if diverged and not args.apply:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

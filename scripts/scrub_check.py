#!/usr/bin/env python3
"""
scrub_check — refuse to publish secrets, client names, or internal hostnames.

Run before every push. It scans the repository for credentials and for identifying
strings, and exits non-zero if it finds any.

    ./scripts/scrub_check.py               # scan the repo
    ./scripts/scrub_check.py --staged      # scan only git-staged content
    ./scripts/scrub_check.py --list-rules

## The denylist problem

Client names cannot be listed inside this repository -- a file enumerating them
would leak exactly what it is meant to protect. So private terms are read from
whichever of these exists, and BOTH are gitignored:

    $FILM_SHOOT_DENYLIST        path to a newline-separated file of terms
    .scrub-denylist             same, in the repo root

If neither exists the scan still runs, but it can only catch generic patterns --
credentials, internal-looking hostnames, emails. It will say so rather than
implying a clean bill of health. A tool that reports "clean" when it did not check
is worse than no tool.

Exit codes:
    0  nothing found
    1  findings -- do not push
    2  could not run
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Generic credential shapes. Deliberately broad; a false positive costs ten seconds
# and a miss is permanent, because git history keeps it even after a later delete.
SECRET_RULES: list[tuple[str, str]] = [
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key block"),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS access key id"),
    (r"\bASIA[0-9A-Z]{16}\b", "AWS temporary access key id"),
    (r"aws_secret_access_key\s*=", "AWS secret in config form"),
    (r"\bghp_[A-Za-z0-9]{20,}", "GitHub personal access token"),
    (r"\bgithub_pat_[A-Za-z0-9_]{20,}", "GitHub fine-grained token"),
    (r"\bxox[abprs]-[A-Za-z0-9-]{10,}", "Slack token"),
    (r"\bsk-[A-Za-z0-9]{20,}", "OpenAI-style secret key"),
    (r"\bsk-ant-[A-Za-z0-9-]{20,}", "Anthropic key"),
    (r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}=*", "bearer token"),
    (r"(?i)\b(api[_-]?key|secret[_-]?key|client[_-]?secret|passwd|password)\s*[:=]\s*"
     r"['\"][^'\"]{8,}['\"]", "hardcoded credential"),
    (r"(?i)\bcookie\s*[:=]\s*['\"][^'\"]{20,}['\"]", "hardcoded cookie"),
]

# Things that identify an employer's internal estate. Not secret individually, but
# collectively they map an internal network and have no business in a public-ish repo.
INTERNAL_RULES: list[tuple[str, str]] = [
    (r"\b[\w.-]+\.corp\.[\w.-]+\b", "internal corp hostname"),
    (r"\b[\w.-]+\.a2z\.com\b", "internal hostname"),
    (r"\b[\w.-]+\.aws\.dev\b", "internal hostname"),
    (r"\b[\w.-]+\.amazon\.dev\b", "internal hostname"),
    (r"\bhttps?://[\w.-]*\.internal[\w.-]*/", "internal URL"),
    (r"\b\d{12}\b", "12-digit number — could be a cloud account id"),
    (r"[\w.+-]+@(?!example\.(com|org)\b)[\w-]+\.[\w.]+", "email address"),
]

# Paths never scanned. Binary media and generated artifacts.
SKIP_DIRS = {".git", "__pycache__", "fixtures", "node_modules", ".venv"}
SKIP_EXT = {".mov", ".mp4", ".lrf", ".wav", ".egg", ".pdf", ".png", ".jpg", ".jpeg",
            ".mhl", ".pyc", ".zip", ".dng", ".braw", ".r3d",
            ".gif", ".webp", ".avif", ".mp3", ".aac", ".m4a", ".ico", ".ttf", ".otf"}


def looks_binary(path: str, probe: int = 4096) -> bool:
    """True if the file is not text, judged by content rather than by its name.

    ⚠️ AN EXTENSION LIST ALWAYS MISSES THE NEXT FORMAT, and a scanner that cries wolf gets
    ignored -- which is the only way this gate actually fails. Adding animated GIFs to the
    README produced a confident "email address: 4@RQ.2" at byte 6150 of a GIF: random bytes
    matching the address regex. `.gif` is in the list above now, and this catches whatever is
    next without anyone having to think of it first.
    """
    try:
        with open(path, "rb") as f:
            chunk = f.read(probe)
    except OSError:
        return True
    if b"\x00" in chunk:
        return True
    # a high proportion of bytes outside printable ASCII + common whitespace
    printable = sum(1 for b in chunk if 32 <= b < 127 or b in (9, 10, 13))
    return bool(chunk) and printable / len(chunk) < 0.85

# Known-harmless matches. Each needs a reason, so this list cannot quietly become a
# place to silence real findings.
ALLOW = [
    ("scrub_check.py", "this file defines the patterns it would otherwise match"),
    (".scrub-denylist", "it lists the private terms by definition; gitignored so it "
                        "is never published"),
    ("NOTICE", "the author's own name in his own copyright notice is intended "
               "publication, not a leak"),
    # ⚠️ The reason here USED to be "verbatim Apache-2.0 text; not ours to edit". The
    # project relicensed to all-rights-reserved, which made that reason false and left the
    # exemption standing on nothing -- an ALLOW entry outliving its justification is exactly
    # how this list becomes a place real findings go to die. The author's own name in his own
    # copyright line is still intended publication, which is the reason that actually holds.
    ("LICENSE", "the author's own name in his own copyright line is intended publication"),
]

# Known-harmless VALUES, matched on the snippet rather than on the path.
#
# ⚠️ Prefer this list to ALLOW whenever you can. Exempting a *file* silences every future
# finding in it, which is how a gate stops working without anyone deciding that it should;
# exempting a *value* leaves every other match in that same file still caught. The entries
# below are published constants of third-party software, verifiable by anyone.
ALLOW_VALUES = [
    ("254016000000", "Premiere Pro's ticks-per-second time base — a published constant of "
                     "the application, not an account id. It is asserted against the running "
                     "app in premiere_bridge/probe rather than trusted, so it has to appear "
                     "in the source."),
]


def denylist() -> tuple[list[str], str | None]:
    """Private terms, and where they came from (None = no list found)."""
    for path in (os.environ.get("FILM_SHOOT_DENYLIST"),
                 os.path.join(ROOT, ".scrub-denylist")):
        if path and os.path.isfile(path):
            terms = [ln.strip() for ln in open(path, encoding="utf-8")
                     if ln.strip() and not ln.startswith("#")]
            return terms, path
    return [], None


def files_to_scan(staged: bool) -> list[str]:
    if staged:
        r = subprocess.run(["git", "-C", ROOT, "diff", "--cached", "--name-only",
                            "--diff-filter=ACM"], capture_output=True, text=True)
        names = [n for n in r.stdout.split("\n") if n.strip()]
        return [os.path.join(ROOT, n) for n in names
                if os.path.isfile(os.path.join(ROOT, n))]
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in SKIP_EXT:
                continue
            if looks_binary(os.path.join(dirpath, fn)):
                continue
            out.append(os.path.join(dirpath, fn))
    return sorted(out)


def allowed(rel: str) -> str | None:
    for frag, why in ALLOW:
        if frag in rel:
            return why
    return None


def scan(paths: list[str], terms: list[str]) -> list[tuple[str, int, str, str]]:
    rules = [(re.compile(p), lbl) for p, lbl in SECRET_RULES + INTERNAL_RULES]
    term_rules = [(re.compile(rf"(?i)\b{re.escape(t)}\b"), f"private term {t!r}")
                  for t in terms]
    findings = []
    for p in paths:
        rel = os.path.relpath(p, ROOT)
        if allowed(rel):
            continue
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                for n, line in enumerate(f, 1):
                    if len(line) > 4000:          # minified/base64 blob
                        line = line[:4000]
                    for rx, lbl in rules + term_rules:
                        m = rx.search(line)
                        if m:
                            snippet = m.group(0)
                            if any(v == snippet for v, _why in ALLOW_VALUES):
                                continue
                            if len(snippet) > 60:
                                snippet = snippet[:57] + "..."
                            findings.append((rel, n, lbl, snippet))
        except (OSError, UnicodeError):
            continue
    return findings


def selftest() -> int:
    """Plant content that must be caught and content that must not be, and assert both.

    The point of the negative half is the ALLOW_VALUES exemption. An exemption is only safe if it
    is NARROW, so the test that matters is not "the allowed value passes" — it is "a different
    value of the same shape, in the same file, still fails."
    """
    import tempfile
    failures: list[str] = []
    tmp = tempfile.mkdtemp(prefix="scrub-selftest-")
    try:
        def scan_text(name: str, text: str, terms: list[str] | None = None):
            p = os.path.join(tmp, name)
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            return scan([p], terms or [])

        # --- must be caught
        must_catch = [
            ("aws.txt", "key = AKIAIOSFODNN7EXAMPLE\n", "an AWS-style access key id"),
            ("acct.txt", "account 123456789012 is ours\n", "a 12-digit account id"),
            ("host.txt", "see build.corp.example.com/x\n", "an internal corp hostname"),
            ("mail.txt", "ping someone@somecompany.net\n", "an email address"),
        ]
        for name, text, what in must_catch:
            if not scan_text(name, text):
                failures.append(f"{what} was NOT caught")

        # --- the private-term list must work, case-insensitively
        if not scan_text("term.txt", "shot at AcmeCorp today\n", ["acmecorp"]):
            failures.append("a private term was not caught case-insensitively")

        # --- the allowed value must pass, and a NEIGHBOUR of the same shape must not
        allowed_value = ALLOW_VALUES[0][0]
        if scan_text("ticks_ok.txt", f"const TICKS = {allowed_value};\n"):
            failures.append("the documented ALLOW_VALUES constant was still flagged")

        neighbour = str(int(allowed_value) + 1)
        found = scan_text("ticks_bad.txt",
                          f"const TICKS = {allowed_value};\nconst OTHER = {neighbour};\n")
        if not any(neighbour in snip for _r, _n, _l, snip in found):
            failures.append("a DIFFERENT 12-digit number beside the allowed one was not caught — "
                            "the exemption is too wide to be safe")

        # --- a binary file must be skipped by content, not by extension
        bin_path = os.path.join(tmp, "blob.unknownext")
        with open(bin_path, "wb") as f:
            f.write(b"\x89\x00\xff\xfe" * 64 + b"key=AKIAIOSFODNN7EXAMPLE")
        if not looks_binary(bin_path):
            failures.append("a file full of NUL and high bytes was not judged binary")

        # --- but a plaintext file with the same secret must still be caught
        if not scan_text("plain.txt", "key=AKIAIOSFODNN7EXAMPLE\n"):
            failures.append("a plaintext secret was skipped")

        # --- every ALLOW and ALLOW_VALUES entry must carry a reason
        for frag, why in ALLOW:
            if not why or not why.strip():
                failures.append(f"ALLOW entry {frag!r} has no stated reason")
        for val, why in ALLOW_VALUES:
            if not why or not why.strip():
                failures.append(f"ALLOW_VALUES entry {val!r} has no stated reason")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print(f"SELFTEST FAILED ({len(failures)})")
        for f in failures:
            print("  - " + f)
        return 1
    print("scrub_check selftest: all checks passed")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Refuse to publish sensitive content.")
    ap.add_argument("--staged", action="store_true",
                    help="scan only git-staged content")
    ap.add_argument("--list-rules", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    if args.list_rules:
        for p, lbl in SECRET_RULES:
            print(f"  secret    {lbl}")
        for p, lbl in INTERNAL_RULES:
            print(f"  internal  {lbl}")
        return 0

    terms, src = denylist()
    paths = files_to_scan(args.staged)
    print(f"scrub_check: {len(paths)} file(s)")
    if src:
        print(f"  private-term list: {src} ({len(terms)} term(s))")
    else:
        print("  ⚠️  NO private-term list found — generic patterns only.")
        print("     Client and project names will NOT be detected. Set")
        print("     FILM_SHOOT_DENYLIST or create .scrub-denylist (both gitignored).")

    findings = scan(paths, terms)
    if not findings:
        print("\n  no findings"
              + ("" if src else " — but the private-term check did not run"))
        return 0

    print(f"\n  {len(findings)} finding(s) — DO NOT PUSH\n")
    for rel, n, lbl, snip in findings:
        print(f"  {rel}:{n}")
        print(f"      {lbl}: {snip}")
    print("\n  Remove these, or — if genuinely harmless — add a documented entry to")
    print("  ALLOW_VALUES (exempts one VALUE anywhere; prefer this) or ALLOW (exempts a")
    print("  whole FILE, which silences every future finding in it too).")
    print("  Note: deleting a secret in a later commit does NOT remove it from git")
    print("  history. If one was already pushed, rotate it.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

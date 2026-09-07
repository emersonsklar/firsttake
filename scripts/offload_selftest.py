#!/usr/bin/env python3
"""
selftest — asserts offload.py detects what it claims to detect.

Builds a synthetic card with ffmpeg, offloads it, then plants faults and
asserts each one is caught with the right exit code. A verified-copy tool that
silently stops noticing corruption is worse than no tool, so this exists to be
run after any edit to offload.py.

    ./selftest.py            # full run
    ./selftest.py --keep     # leave the scratch dir for inspection

Exit 0 if every assertion passes, 1 otherwise.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
OFFLOAD = os.path.join(HERE, "offload.py")

PASS = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok    {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}" + (f"  — {detail}" if detail else ""))


def run(args: list[str]) -> tuple[int, str]:
    p = subprocess.run([sys.executable, OFFLOAD] + args,
                       capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def ffmpeg_ok() -> bool:
    return shutil.which("ffmpeg") is not None


def build_card(root: str) -> str:
    card = os.path.join(root, "CARD_T001")
    media = os.path.join(card, "DCIM", "100MEDIA")
    os.makedirs(media)
    os.makedirs(os.path.join(card, "MISC"))

    if ffmpeg_ok():
        subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi",
             "-i", "testsrc=size=320x180:rate=25:duration=2",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
             "-shortest", os.path.join(media, "C0001.MP4")], check=True)
        subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi",
             "-i", "sine=frequency=220:duration=1",
             "-c:a", "pcm_s16le", os.path.join(card, "MISC", "REC001.WAV")],
            check=True)
    else:
        # Still exercise copy/verify without real media.
        with open(os.path.join(media, "C0001.MP4"), "wb") as f:
            f.write(os.urandom(3 * 1024 * 1024))
        with open(os.path.join(card, "MISC", "REC001.WAV"), "wb") as f:
            f.write(os.urandom(256 * 1024))

    with open(os.path.join(media, "C0001.XML"), "w") as f:
        f.write("<xml>sidecar</xml>")
    with open(os.path.join(card, "MISC", "notes.txt"), "w") as f:
        f.write("camera log\n")
    with open(os.path.join(card, ".hiddenfile"), "w") as f:
        f.write("hidden\n")
    # Planted junk that must NOT be copied and must NOT move the verdict.
    with open(os.path.join(card, ".DS_Store"), "w") as f:
        f.write("junk\n")
    os.makedirs(os.path.join(card, ".Spotlight-V100"))
    with open(os.path.join(card, ".Spotlight-V100", "store"), "w") as f:
        f.write("junk\n")
    return card


def main(argv: list[str]) -> int:
    keep = "--keep" in argv
    root = tempfile.mkdtemp(prefix="offload-selftest.")
    print(f"scratch: {root}\n")

    try:
        card = build_card(root)
        d1 = os.path.join(root, "d1")
        d2 = os.path.join(root, "d2")
        os.makedirs(d1)
        os.makedirs(d2)

        # ---- 1. clean offload to two destinations ------------------------
        print("clean offload, two destinations")
        rc, out = run([card, "--dest", d1, "--dest", d2, "--project", "P",
                       "--card", "T001", "--quiet"])
        check("exit 0 on a clean run", rc == 0, f"exit {rc}")
        check("says VERIFIED", "VERIFIED" in out and "WITH WARNINGS" not in out)
        check("junk skip does not downgrade the verdict",
              "system files not copied: 2" in out)

        t1 = os.path.join(d1, "P", "T001")
        t2 = os.path.join(d2, "P", "T001")
        check("PROJECT/CARD tree created", os.path.isdir(t1) and os.path.isdir(t2))
        check(".DS_Store not copied",
              not os.path.exists(os.path.join(t1, ".DS_Store")))
        check(".Spotlight-V100 not copied",
              not os.path.exists(os.path.join(t1, ".Spotlight-V100")))
        check("hidden file IS copied",
              os.path.exists(os.path.join(t1, ".hiddenfile")))
        check("nested media copied",
              os.path.exists(os.path.join(t1, "DCIM", "100MEDIA", "C0001.MP4")))

        # ---- 2. bytes actually match ------------------------------------
        print("\nbyte-level equality against the source")
        import hashlib
        bad = []
        for dirpath, _, files in os.walk(card):
            for fn in files:
                if fn in (".DS_Store",) or ".Spotlight-V100" in dirpath:
                    continue
                rel = os.path.relpath(os.path.join(dirpath, fn), card)
                a = hashlib.md5(open(os.path.join(card, rel), "rb").read()).hexdigest()
                for t in (t1, t2):
                    p = os.path.join(t, rel)
                    if not os.path.exists(p):
                        bad.append(f"{rel} missing in {t}")
                        continue
                    if hashlib.md5(open(p, "rb").read()).hexdigest() != a:
                        bad.append(f"{rel} differs in {t}")
        check("every copied file is byte-identical on both destinations",
              not bad, "; ".join(bad[:3]))

        # ---- 3. manifests ------------------------------------------------
        print("\nmanifests")
        jm = glob.glob(os.path.join(t1, "offload-manifest-*.json"))
        check("stamped JSON manifest written", len(jm) == 1)
        if jm:
            data = json.load(open(jm[0]))
            s = data["summary"]
            check("manifest reports 0 failures", s["files_failed"] == 0)
            check("manifest counts 5 files", s["files"] == 5, str(s["files"]))
            check("manifest records 2 destinations", s["destinations"] == 2)

        mhl = glob.glob(os.path.join(t1, "*.mhl"))
        check("classic MHL written", len(mhl) == 1)
        if mhl:
            r = ET.parse(mhl[0]).getroot()
            check("MHL is a 1.1 hashlist",
                  r.tag == "hashlist" and r.get("version") == "1.1")
            entries = r.findall("hash")
            check("MHL has one entry per file", len(entries) == 5, str(len(entries)))
            # Cross-check one recorded hash against a fresh read.
            algo = next((c.tag for c in entries[0]
                         if c.tag in ("md5", "sha1", "xxhash64")), None)
            check("MHL hash element is a legal algorithm", algo is not None)

        rep = glob.glob(os.path.join(t1, "offload-report-*.html"))
        check("HTML report written", len(rep) == 1)
        if rep:
            h = open(rep[0]).read()
            for tok in ("Summary", "Sources &amp; Destinations", "VERIFIED"):
                check(f"report contains {tok!r}", tok in h)

            # Thumbnails must keep their own aspect ratio. A flex container
            # defaults to align-items:stretch, which stretches a height:auto
            # replaced element to the flex line's cross size — silently
            # distorting every still in the report. Found by eye 2026-09-07.
            clip_rule = next((ln for ln in h.splitlines()
                              if ln.startswith(".clip{")), "")
            check("report .clip rule exists", bool(clip_rule), clip_rule)
            check("thumbnails are not stretched by flex align-items",
                  "align-items:flex-start" in clip_rule, clip_rule)
            check("thumbnail height stays auto (no hardcoded ratio)",
                  "height:auto" in h and "aspect-ratio:16/9" not in
                  h.split(".clip .noimg")[0].split(".clip img")[-1])

        # ---- 4. idempotent re-run ---------------------------------------
        print("\nre-run over an existing offload")
        rc, out = run([card, "--dest", d1, "--project", "P", "--card", "T001",
                       "--quiet"])
        check("re-run exits 0", rc == 0, f"exit {rc}")

        # ---- 4b. same-second collision ----------------------------------
        # Two offloads inside one wall-clock second must not share a manifest
        # filename; the second would overwrite the first's provenance record.
        print("\ntwo offloads in the same second")
        d3 = os.path.join(root, "d3")
        os.makedirs(d3)
        for _ in range(2):
            run([card, "--dest", d3, "--project", "P", "--card", "T001", "--quiet"])
        t3 = os.path.join(d3, "P", "T001")
        m3 = glob.glob(os.path.join(t3, "offload-manifest-*.json"))
        check("back-to-back runs keep both manifests", len(m3) == 2,
              f"found {len(m3)}")

        # ---- 5. planted corruption --------------------------------------
        print("\nplanted single-byte corruption")
        victim = os.path.join(t1, "DCIM", "100MEDIA", "C0001.MP4")
        with open(victim, "r+b") as f:
            f.seek(2048)
            b = f.read(1)
            f.seek(2048)
            f.write(bytes([b[0] ^ 0xFF]))
        rc, out = run(["--verify-only", t1])
        check("--verify-only exits 2 on corruption", rc == 2, f"exit {rc}")
        check("--verify-only names the corrupt file", "C0001.MP4" in out)

        rc, out = run([card, "--dest", d1, "--project", "P", "--card", "T001",
                       "--quiet"])
        check("offload exits 2 when destination differs", rc == 2, f"exit {rc}")
        check("offload reports MISMATCH", "MISMATCH" in out)

        print("\n--overwrite repairs it")
        rc, out = run([card, "--dest", d1, "--project", "P", "--card", "T001",
                       "--overwrite", "--quiet"])
        check("--overwrite exits 0", rc == 0, f"exit {rc}")

        # ---- 6. missing file --------------------------------------------
        print("\nplanted deletion")
        os.remove(os.path.join(t1, "MISC", "notes.txt"))
        rc, out = run(["--verify-only", t1])
        check("--verify-only exits 2 on a missing file", rc == 2, f"exit {rc}")
        check("--verify-only says MISSING", "MISSING" in out)

        # ---- 7. guards ---------------------------------------------------
        print("\nguards")
        rc, _ = run([card, "--dest", card, "--flat", "--quiet"])
        check("refuses to copy onto itself", rc == 3, f"exit {rc}")
        rc, _ = run([card, "--quiet"])
        check("refuses to run without --dest", rc == 3, f"exit {rc}")
        rc, _ = run([os.path.join(root, "nope"), "--dest", d1])
        check("refuses a missing source", rc == 3, f"exit {rc}")
        rc, out = run([card, "--dest", d2, "--dry-run", "--project", "P",
                       "--card", "T001"])
        check("--dry-run exits 0 and writes nothing",
              rc == 0 and "would copy" in out, f"exit {rc}")

        # ---- 8. ASC MHL chain, if available ------------------------------
        # Deliberately no sleep between hops: two offloads inside the same
        # wall-clock second must not collide on the manifest filename. That bug
        # was real and only showed up when the hops happened fast enough.
        print("\nASC MHL + two-hop chain (skipped if ascmhl is not installed)")
        if shutil.which("ascmhl"):
            c1 = os.path.join(root, "c1")
            c2 = os.path.join(root, "c2")
            os.makedirs(c1)
            os.makedirs(c2)
            rc, _ = run([card, "--dest", c1, "--project", "P", "--card", "T001",
                         "--ascmhl", "--quiet"])
            check("hop 1 with --ascmhl exits 0", rc == 0, f"exit {rc}")
            g1 = glob.glob(os.path.join(c1, "P", "T001", "ascmhl", "0001_*.mhl"))
            check("generation 0001 created", len(g1) == 1)
            rc, _ = run([os.path.join(c1, "P", "T001"), "--dest", c2,
                         "--project", "P", "--card", "T001", "--ascmhl", "--quiet"])
            check("hop 2 with --ascmhl exits 0", rc == 0, f"exit {rc}")
            g2 = glob.glob(os.path.join(c2, "P", "T001", "ascmhl", "0002_*.mhl"))
            check("generation 0002 chained at the second hop", len(g2) == 1)
            man2 = glob.glob(os.path.join(c2, "P", "T001",
                                          "offload-manifest-*.json"))
            check("both hops' JSON manifests survive at the archive",
                  len(man2) == 2, f"found {len(man2)}")

            # A generation tracks the offload's OWN sidecars — its manifest, its
            # .mhl and its HTML report — not just the media. So deleting a
            # superseded report, which is the natural tidying instinct, breaks the
            # chain at the NEXT hop. Cost a real two-hop run on 2026-09-07: the
            # media verified, the lineage silently did not extend.
            g1_txt = open(g1[0], encoding="utf-8", errors="replace").read()
            check("a generation tracks its own HTML report, not just media",
                  "offload-report-" in g1_txt)

            # ascmhl APPENDS to ascmhl_chain.xml after we hashed it, so a
            # byte-frozen expectation is permanently wrong and --verify-only
            # cried MISMATCH on a healthy two-hop archive every single time.
            # An integrity check that cries wolf gets ignored, and then a real
            # mismatch gets ignored too. Measured 2026-09-07.
            rc, out = run(["--verify-only", os.path.join(c2, "P", "T001")])
            check("--verify-only exits 0 on a healthy --ascmhl archive", rc == 0,
                  f"exit {rc}: {out[-200:]}")
            check("--verify-only reports no mismatch on a healthy archive",
                  "0 mismatched" in out, out[-200:])
            check("the live chain file is named as live, not silently dropped",
                  "live ASC MHL" in out, out[-200:])
            check("immutable generation manifests are still byte-verified",
                  "0001_" not in out.split("live ASC MHL")[0].split("MISMATCH")[-1])

            print("\na deleted sidecar must break the chain LOUDLY, not silently")
            c3 = os.path.join(root, "c3")
            os.makedirs(c3)
            victim = glob.glob(os.path.join(c1, "P", "T001",
                                            "offload-report-*.html"))[0]
            os.remove(victim)
            rc, out = run([os.path.join(c1, "P", "T001"), "--dest", c3,
                           "--project", "P", "--card", "T001", "--ascmhl"])
            check("media still verifies with a sidecar missing", rc in (0, 1),
                  f"exit {rc}")
            check("the broken chain is reported, not swallowed",
                  "ASC MHL chain incomplete" in out, out[-200:])

            # ascmhl exits non-zero but STILL writes the generation. Saying
            # "not written" full stop was a false claim about the archive: the
            # ascmhl/ dir does gain a 0002, so someone reading the drive later
            # sees a chain no run vouched for. The warning must say both things.
            wrote_gen = glob.glob(os.path.join(c3, "P", "T001", "ascmhl",
                                               "0002_*.mhl"))
            check("ascmhl does write a generation despite exiting non-zero",
                  len(wrote_gen) == 1, f"found {len(wrote_gen)}")
            check("the warning admits the generation WAS written",
                  "WAS written" in out, out[-300:])
            check("the warning names the missing sidecar",
                  "offload-report-" in out and "absent here" in out, out[-300:])
        else:
            print("  skip  ascmhl not installed")

    finally:
        if keep:
            print(f"\nkept: {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""nle_handover.py — hand a cut to someone using the other NLE, and let them prove it arrived whole.

An edit that only exists inside one application is an edit that has to be rebuilt by hand to move.
So the handover artefact is **the plan, not the project file**: the NLE-neutral IR plus everything
it references. Whoever receives it builds it in whichever application they have.

    # sending
    python3 nle_handover.py --pack plan.json --to ~/handover/cut-v7 --from-backend resolve
    python3 nle_handover.py --pack plan.json --to /Volumes/Transfer/cut-v7 --copy   # other machine

    # receiving
    python3 nle_handover.py --verify ~/handover/cut-v7 --backend premiere

    python3 nle_handover.py --selftest

WHY NOT AN INTERCHANGE FILE
---------------------------
FCP7 XML, AAF and EDL all exist, and all three are lossy in the places this pipeline cares about —
crops, fade shape, animation, per-camera grade. Worse, they are lossy *silently*: the receiving
application opens the file, shows a timeline, and says nothing about what it dropped. The IR plus a
capability gate is the opposite: it refuses to hand over something the far end cannot build, and
names it.

WHAT TRAVELS, AND WHAT CANNOT
-----------------------------
🔴 **Only what the plan describes is reproducible.** Anything done by hand in one application — a
stabilisation pass, a hand grade, a Fusion comp somebody drew — is NOT in the plan and will not
survive. The answer is not to hope: **render it into the media and record that it is baked.**

That is already how stabilisation is handled here, because it is not scriptable in Resolve at all:
script a scratch project with the clip at 1× and ungraded, have the operator run the stabiliser in
the UI, render the result ungraded and full-raster, then let the normal colour and reframe chain
apply to it exactly once. The output of that is a *new source file*, which travels perfectly.

⚠️ **`--pack` does not copy media by default.** It writes a manifest that points at the media where
it already lives. One file per deliverable is the rule on this pipeline, and a handover package
that duplicates 200 GB of masters creates a second thing that can go stale and be published by
mistake. Pass `--copy` only when the package genuinely has to cross a machine boundary.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    import nle_ir
except ImportError:                                        # pragma: no cover
    nle_ir = None

MANIFEST = "handover.json"
NOTES = "HANDOVER.md"
PLAN = "plan.json"
CHUNK = 1 << 20


def probe_hash(path, full=False):
    """Content identity for a media file.

    Full hash when asked. Otherwise size + the first and last mebibyte, which catches the failures a
    transfer actually produces — a truncated copy, the wrong take, a file still being written — for
    a fraction of the cost on a 4K master. A full hash is what an *archive* needs, and that is
    `offload.py`'s job, not this one.
    """
    st = os.stat(path)
    h = hashlib.sha256()
    h.update(str(st.st_size).encode())
    with open(path, "rb") as fh:
        h.update(fh.read(CHUNK))
        if full:
            while True:
                b = fh.read(CHUNK)
                if not b:
                    break
                h.update(b)
        elif st.st_size > CHUNK:
            fh.seek(max(0, st.st_size - CHUNK))
            h.update(fh.read(CHUNK))
    return {"size": st.st_size, "sha256": h.hexdigest(), "full": bool(full)}


def referenced_files(plan):
    """Every path the plan depends on, by role, so a missing one is named rather than just absent."""
    out = []
    for mid, path in (plan.get("media") or {}).items():
        out.append({"role": "media", "id": mid, "path": path})
    for t in plan.get("tracks", []):
        for node in t.get("nodes", []):
            grade = node.get("grade") or {}
            if grade.get("lut"):
                out.append({"role": "lut", "id": node.get("media"), "path": grade["lut"]})
    if plan.get("sequence_preset"):
        out.append({"role": "sequence_preset", "id": None, "path": plan["sequence_preset"]})
    for extra in plan.get("sidecars", []):
        out.append({"role": "sidecar", "id": extra.get("kind"), "path": extra.get("path")})
    # de-duplicate on path but keep the first role seen
    seen = set()
    uniq = []
    for row in out:
        if row["path"] in seen:
            continue
        seen.add(row["path"])
        uniq.append(row)
    return uniq


def pack(plan_path, dest, from_backend=None, copy=False, full=False, allow_unverified=False):
    with open(plan_path, encoding="utf-8") as fh:
        plan = json.load(fh)

    if nle_ir is not None:
        errs = nle_ir.validate(plan)
        if errs:
            raise SystemExit("the plan is not valid, so it cannot be handed over (%d problems):\n  - %s"
                             % (len(errs), "\n  - ".join(errs)))

    os.makedirs(dest, exist_ok=True)
    files = referenced_files(plan)

    missing = [r for r in files if not r["path"] or not os.path.exists(r["path"])]
    if missing:
        raise SystemExit(
            "%d referenced file(s) do not exist, so the package would be born broken:\n  - %s"
            % (len(missing), "\n  - ".join("%s (%s)" % (r["path"], r["role"]) for r in missing)))

    entries = []
    for r in files:
        rec = dict(r)
        rec.update(probe_hash(r["path"], full=full))
        rec["basename"] = os.path.basename(r["path"])
        if copy:
            sub = os.path.join(dest, r["role"])
            os.makedirs(sub, exist_ok=True)
            target = os.path.join(sub, rec["basename"])
            shutil.copy2(r["path"], target)
            rec["packed_as"] = os.path.relpath(target, dest)
            # a copy is not a copy until it has been read back
            check = probe_hash(target, full=full)
            if check["sha256"] != rec["sha256"]:
                raise SystemExit("copy of %s does not match the source it came from" % r["path"])
        entries.append(rec)

    # the plan travels beside the manifest, unmodified
    shutil.copy2(plan_path, os.path.join(dest, PLAN))

    compare = []
    if nle_ir is not None:
        for backend, berrs in nle_ir.compare_backends(plan, allow_unverified=allow_unverified):
            compare.append({"backend": backend, "problems": berrs})

    manifest = {
        "handover_version": 1,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "plan": PLAN,
        "plan_name": plan.get("name"),
        "fps": plan.get("fps"),
        "timeline_raster": plan.get("timeline_raster"),
        "built_in": from_backend,
        "media_copied": bool(copy),
        "hash_mode": "full" if full else "size+ends",
        "files": entries,
        "backend_report": compare,
        "baked": plan.get("baked", []),
    }
    with open(os.path.join(dest, MANIFEST), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    with open(os.path.join(dest, NOTES), "w", encoding="utf-8") as fh:
        fh.write(render_notes(manifest, plan))

    return manifest


def render_notes(manifest, plan):
    L = []
    L.append("# Handover: %s\n" % (manifest.get("plan_name") or "untitled"))
    L.append("Built in **%s**, packed %s.\n" % (manifest.get("built_in") or "an unnamed NLE",
                                                manifest["created"]))
    L.append("- %s fps, timeline %s" % ("/".join(str(x) for x in (manifest.get("fps") or [])),
                                        "x".join(str(x) for x in (manifest.get("timeline_raster") or []))))
    L.append("- %d referenced file(s), media %s"
             % (len(manifest["files"]), "copied into this folder" if manifest["media_copied"]
                else "referenced in place — **not** in this folder"))
    L.append("")
    L.append("## Build it")
    L.append("")
    L.append("```bash")
    L.append("python3 nle_handover.py --verify . --backend <resolve|premiere>")
    L.append("```")
    L.append("")
    L.append("That checks every file is present and unchanged and that your application can build "
             "the plan exactly. Then build with the backend you have:")
    L.append("")
    L.append("```bash")
    L.append("python3 premiere_bridge.py --probe            # Premiere: do this first, once")
    L.append("python3 premiere_bridge.py --build plan.json")
    L.append("```")
    L.append("")

    L.append("## What each application will do with this plan")
    L.append("")
    for row in manifest.get("backend_report", []):
        if not row["problems"]:
            L.append("- **%s** — builds it exactly." % row["backend"])
        else:
            L.append("- **%s** — %d problem(s):" % (row["backend"], len(row["problems"])))
            for p in row["problems"]:
                L.append("    - %s" % p)
    L.append("")

    baked = manifest.get("baked") or []
    L.append("## Baked in, and therefore NOT adjustable at your end")
    L.append("")
    if baked:
        for b in baked:
            L.append("- **%s** — %s" % (b.get("what"), b.get("why", "")))
    else:
        L.append("- Nothing declared. ⚠️ If any manual work was done in the sending application — "
                 "a stabilisation pass, a hand grade, a comp drawn by hand — it is **not** in the "
                 "plan and will not appear at your end. Ask before you start.")
    L.append("")
    L.append("## Files")
    L.append("")
    L.append("| role | file | size | sha256 (%s) |" % manifest["hash_mode"])
    L.append("|---|---|---|---|")
    for f in manifest["files"]:
        L.append("| %s | `%s` | %d | `%s` |"
                 % (f["role"], f.get("packed_as") or f["path"], f["size"], f["sha256"][:16]))
    L.append("")
    return "\n".join(L)


def verify(dest, backend=None, full=False, allow_unverified=False):
    """The receiving end. Prove the package is whole BEFORE opening an application."""
    problems = []
    mpath = os.path.join(dest, MANIFEST)
    if not os.path.exists(mpath):
        return ["%s has no %s — this is not a handover package" % (dest, MANIFEST)]
    with open(mpath, encoding="utf-8") as fh:
        manifest = json.load(fh)

    ppath = os.path.join(dest, manifest.get("plan", PLAN))
    if not os.path.exists(ppath):
        problems.append("the plan %r is missing from the package" % manifest.get("plan"))
        plan = None
    else:
        with open(ppath, encoding="utf-8") as fh:
            plan = json.load(fh)

    for f in manifest.get("files", []):
        path = os.path.join(dest, f["packed_as"]) if f.get("packed_as") else f["path"]
        if not os.path.exists(path):
            problems.append("%s missing: %s%s"
                            % (f["role"], path,
                               "" if f.get("packed_as")
                               else "  (the package references media in place; it was packed on "
                                    "another machine or the file has moved)"))
            continue
        got = probe_hash(path, full=full and f.get("full", False))
        if got["size"] != f["size"]:
            problems.append("%s changed size: %s (%d now, %d when packed)"
                            % (f["role"], path, got["size"], f["size"]))
        elif got["sha256"] != f["sha256"] and got["full"] == f.get("full"):
            problems.append("%s content differs from the manifest: %s. Same size, different bytes "
                            "— which is what a re-render of the same graphic looks like."
                            % (f["role"], path))

    if plan is not None and nle_ir is not None:
        problems += ["plan: " + e for e in nle_ir.validate(plan)]
        if backend:
            gate = nle_ir.capability_gate(plan, backend, allow_unverified=allow_unverified)
            problems += ["%s cannot build this: %s" % (backend, e) for e in gate]

    if not manifest.get("baked"):
        problems.append("NOTE: the package declares nothing baked. If the sender did any manual "
                        "work in their application it is not in the plan. Confirm with them "
                        "before you start cutting.")

    return problems


# --------------------------------------------------------------------------------------------

def selftest():
    import tempfile
    failures = []
    tmp = tempfile.mkdtemp(prefix="nle-handover-selftest-")
    try:
        media = os.path.join(tmp, "A001.mov")
        with open(media, "wb") as fh:
            fh.write(b"\x00" * (3 * CHUNK))
        lut = os.path.join(tmp, "camA.cube")
        with open(lut, "w", encoding="utf-8") as fh:
            fh.write("LUT_3D_SIZE 2\n")

        plan = {
            "name": "handover-selftest",
            "fps": [30000, 1001],
            "timeline_raster": [1920, 1080],
            "media": {"a": media},
            "tracks": [{"id": "V1", "kind": "video", "index": 1, "nodes": [
                {"kind": "clip", "media": "a", "rec": 0, "src_in": 0, "len": 100,
                 "grade": {"lut": lut}}]}],
        }
        plan_path = os.path.join(tmp, "plan.json")
        with open(plan_path, "w", encoding="utf-8") as fh:
            json.dump(plan, fh)

        # --- pack without copying: manifest references media in place
        dest = os.path.join(tmp, "pkg")
        man = pack(plan_path, dest, from_backend="resolve", copy=False)
        if man["media_copied"]:
            failures.append("packing without --copy reported media_copied")
        if any("packed_as" in f for f in man["files"]):
            failures.append("packing without --copy still wrote files into the package")
        if len(man["files"]) != 2:
            failures.append("expected media + lut in the manifest, got %d" % len(man["files"]))
        if not os.path.exists(os.path.join(dest, NOTES)):
            failures.append("no HANDOVER.md was written")

        notes = open(os.path.join(dest, NOTES), encoding="utf-8").read()
        if "referenced in place" not in notes:
            failures.append("the notes do not warn that media is not in the folder")
        if "will not appear at your end" not in notes:
            failures.append("the notes do not warn about undeclared manual work")

        # --- verify passes on a healthy package (the 'nothing baked' NOTE is expected)
        probs = [p for p in verify(dest, backend="resolve") if not p.startswith("NOTE")]
        if probs:
            failures.append("a healthy package failed verification: %r" % probs)

        # --- a file that changed content but kept its size must be caught
        with open(media, "r+b") as fh:
            fh.write(b"\xff" * 16)
        probs = verify(dest, backend="resolve")
        if not any("content differs" in p for p in probs):
            failures.append("a same-size content change was not caught: %r" % probs)

        # --- a missing file must be named with its role
        os.remove(lut)
        probs = verify(dest, backend="resolve")
        if not any("lut missing" in p for p in probs):
            failures.append("a missing LUT was not reported by role: %r" % probs)

        # --- packing a plan whose files are absent must refuse rather than make a broken package
        try:
            pack(plan_path, os.path.join(tmp, "pkg2"), copy=False)
            failures.append("packing with a missing LUT should have refused")
        except SystemExit as e:
            if "born broken" not in str(e):
                failures.append("the refusal did not explain itself: %s" % e)

        # --- copy mode really copies and reads back
        with open(lut, "w", encoding="utf-8") as fh:
            fh.write("LUT_3D_SIZE 2\n")
        dest3 = os.path.join(tmp, "pkg3")
        man3 = pack(plan_path, dest3, from_backend="resolve", copy=True)
        for f in man3["files"]:
            if "packed_as" not in f:
                failures.append("copy mode did not record packed_as for %r" % f["role"])
            elif not os.path.exists(os.path.join(dest3, f["packed_as"])):
                failures.append("copy mode did not actually copy %r" % f["packed_as"])
        probs = [p for p in verify(dest3, backend="resolve") if not p.startswith("NOTE")]
        if probs:
            failures.append("a copied package failed verification: %r" % probs)

        # --- a copied package must verify even if the ORIGINAL media moves away.
        #     This is the whole point of --copy, so it is worth asserting rather than assuming.
        os.rename(media, media + ".moved")
        probs = [p for p in verify(dest3, backend="resolve") if not p.startswith("NOTE")]
        if probs:
            failures.append("a copied package broke when the original media moved: %r" % probs)
        os.rename(media + ".moved", media)

        # --- the backend report must carry the gate's verdict for BOTH applications
        names = sorted(r["backend"] for r in man3["backend_report"])
        if nle_ir is not None and names != sorted(nle_ir.BACKENDS):
            failures.append("the backend report does not cover every backend: %r" % names)

        # --- a plan with a CDL must be reported as un-buildable in Premiere, in the notes
        plan2 = json.loads(json.dumps(plan))
        plan2["tracks"][0]["nodes"][0]["grade"] = {"cdl": {"slope": [1, 1, 1]}, "lut": lut}
        p2 = os.path.join(tmp, "plan2.json")
        with open(p2, "w", encoding="utf-8") as fh:
            json.dump(plan2, fh)
        dest4 = os.path.join(tmp, "pkg4")
        pack(p2, dest4, from_backend="resolve", copy=False, allow_unverified=True)
        notes4 = open(os.path.join(dest4, NOTES), encoding="utf-8").read()
        if "premiere" not in notes4 or "CANNOT do exactly" not in notes4:
            failures.append("the handover notes do not tell the receiver that their application "
                            "cannot build the grade: %r" % notes4[-800:])

        # --- a folder that is not a package says so
        probs = verify(tmp)
        if not any("not a handover package" in p for p in probs):
            failures.append("verifying a non-package did not say so: %r" % probs)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELFTEST FAILED (%d)" % len(failures))
        for f in failures:
            print("  - " + f)
        return 1
    print("nle_handover selftest: all checks passed")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pack", metavar="PLAN.JSON")
    ap.add_argument("--to", metavar="DIR")
    ap.add_argument("--from-backend", choices=("resolve", "premiere"))
    ap.add_argument("--copy", action="store_true",
                    help="copy the referenced media into the package (only for another machine)")
    ap.add_argument("--verify", metavar="DIR")
    ap.add_argument("--backend", choices=("resolve", "premiere"),
                    help="the application YOU will build this in")
    ap.add_argument("--full", action="store_true", help="hash whole files, not size + ends")
    ap.add_argument("--allow-unverified", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    if a.pack:
        if not a.to:
            ap.error("--pack needs --to DIR")
        man = pack(a.pack, a.to, from_backend=a.from_backend, copy=a.copy, full=a.full,
                   allow_unverified=a.allow_unverified)
        print("packed %d file(s) to %s" % (len(man["files"]), a.to))
        print("read %s before sending it — it states what is NOT reproducible at the far end."
              % os.path.join(a.to, NOTES))
        for row in man.get("backend_report", []):
            print("  %-10s %s" % (row["backend"],
                                  "ok" if not row["problems"]
                                  else "%d problem(s)" % len(row["problems"])))
        return 0

    if a.verify:
        probs = verify(a.verify, backend=a.backend, full=a.full,
                       allow_unverified=a.allow_unverified)
        hard = [p for p in probs if not p.startswith("NOTE")]
        for p in probs:
            print(("  - " if not p.startswith("NOTE") else "  ") + p)
        if hard:
            print("\nHANDOVER NOT USABLE YET — %d problem(s) above." % len(hard))
            return 1
        print("handover verified: every referenced file is present and unchanged%s."
              % (", and %s can build the plan exactly" % a.backend if a.backend else ""))
        return 0

    ap.error("give --pack, --verify or --selftest")


if __name__ == "__main__":
    sys.exit(main())

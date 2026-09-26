#!/usr/bin/env python3
"""make_export_preset.py — build a Premiere/AME export preset (.epr) for an exact raster and rate.

`EncoderManager.exportSequence()` takes a preset file, and the presets Premiere ships are **pinned
to a raster**, not matched to the sequence:

    Apple ProRes 422 HQ.epr     ADBEVideoMatchSource=false   1920x1080
    Apple ProRes 4444.epr       ADBEVideoMatchSource=false   1920x1080

So handing one of those to a 1080x1920 vertical sequence does not produce a vertical file. Nothing
errors; you get a landscape render of a vertical programme, which is obvious on a monitor and
invisible to a build script that only checks the queue said Complete.

An `.epr` is plain XML, so the honest fix is to generate one for the raster you are actually
delivering, commit it, and assert it came out right:

    python3 make_export_preset.py --template "Apple ProRes 422 HQ.epr" \\
        --raster 1080x1920 --fps 30000/1001 --out assets/presets/prores422hq_1080x1920_2997.epr
    python3 make_export_preset.py --list            # what this machine ships
    python3 make_export_preset.py --selftest

WHY GENERATE RATHER THAN SAVE ONE FROM THE UI
---------------------------------------------
A preset saved by hand lives in one person's user folder, cannot be reviewed, and is the kind of
dependency that makes a build work on one machine only. A generated one is a committed artefact
derived from a file the application itself ships.

⚠️ **The frame rate is stored as TICKS PER FRAME, not as a rate.** `ADBEVideoFPS` on the shipped
29.97 presets reads **8475667200**, which is 254016000000 * 1001/30000 — the same integer the UXP
panel computes. That is a useful independent confirmation of the tick base, and it means a rate
that does not divide the tick base exactly cannot be expressed here either.
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import sys
import xml.etree.ElementTree as ET

TICKS_PER_SECOND = 254016000000

AME_SYSTEM_PRESETS = "/Applications/Adobe Media Encoder */Adobe Media Encoder *.app/Contents/MediaIO/systempresets"

# FourCC -> name, for reporting. An .epr stores the codec as a big-endian FourCC in decimal.
FOURCC = {
    "apch": "Apple ProRes 422 HQ",
    "apcn": "Apple ProRes 422",
    "apcs": "Apple ProRes 422 LT",
    "apco": "Apple ProRes 422 Proxy",
    "ap4h": "Apple ProRes 4444",
    "ap4x": "Apple ProRes 4444 XQ",
    "avc1": "H.264",
}

WANTED = ("ADBEVideoWidth", "ADBEVideoHeight", "ADBEVideoFPS", "ADBEVideoMatchSource",
          "ADBEVideoCodec", "ADBEVideoAspect")


def fourcc_of(value):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    try:
        b = n.to_bytes(4, "big")
        s = b.decode("ascii")
        return s if all(32 <= ch < 127 for ch in b) else None
    except (OverflowError, UnicodeDecodeError):
        return None


def ticks_per_frame(fps):
    num, den = fps
    want = (TICKS_PER_SECOND * den) / num
    if want != int(want):
        raise SystemExit("ERROR: %d/%d does not land on a whole number of ticks (%r). An .epr "
                         "cannot express it." % (num, den, want))
    return int(want)


def read_params(path):
    """Every ParamIdentifier -> ParamValue in an .epr, plus the tree for editing."""
    tree = ET.parse(path)
    out = {}
    for parent in tree.getroot().iter():
        pid = parent.findtext("ParamIdentifier")
        if pid:
            out[pid] = parent
    return tree, out


def describe(path):
    tree, params = read_params(path)
    def val(k):
        el = params.get(k)
        return None if el is None else (el.findtext("ParamValue") or "").strip()
    codec = val("ADBEVideoCodec")
    fc = fourcc_of(codec)
    return {
        "name": os.path.basename(path),
        "width": val("ADBEVideoWidth"),
        "height": val("ADBEVideoHeight"),
        "fps_ticks": val("ADBEVideoFPS"),
        "match_source": val("ADBEVideoMatchSource"),
        "codec": codec,
        "codec_fourcc": fc,
        "codec_name": FOURCC.get(fc, fc or codec),
    }


def find_templates():
    roots = glob.glob(AME_SYSTEM_PRESETS)
    found = []
    for root in roots:
        for p in glob.glob(os.path.join(root, "*", "*.epr")):
            found.append(p)
    return sorted(found)


def resolve_template(name):
    if os.path.exists(name):
        return name
    hits = [p for p in find_templates() if os.path.basename(p) == name]
    if not hits:
        hits = [p for p in find_templates() if name.lower() in os.path.basename(p).lower()]
    if not hits:
        raise SystemExit("ERROR: no shipped preset matches %r. Run --list to see what this "
                         "machine has." % name)
    if len(hits) > 1:
        exact = [p for p in hits if os.path.basename(p) == name]
        if len(exact) == 1:
            return exact[0]
        raise SystemExit("ERROR: %r matches %d presets; name one exactly:\n  %s"
                         % (name, len(hits), "\n  ".join(hits)))
    return hits[0]


def build(template, raster, fps, out, match_source=False):
    src = resolve_template(template)
    before = describe(src)

    tree, params = read_params(src)
    w, h = raster
    tpf = ticks_per_frame(fps)

    changes = {"ADBEVideoWidth": str(w), "ADBEVideoHeight": str(h), "ADBEVideoFPS": str(tpf)}
    if "ADBEVideoMatchSource" in params:
        # Pin explicitly rather than matching. A preset that MATCHES will silently follow a wrong
        # sequence; a preset that PINS makes the delivery raster an assertion the verifier can fail
        # on. Explicit beats adaptive for a deliverable.
        changes["ADBEVideoMatchSource"] = "true" if match_source else "false"

    missing = [k for k in changes if k not in params]
    if missing:
        raise SystemExit("ERROR: %s has no %s — it is not a video preset this tool can retarget."
                         % (os.path.basename(src), ", ".join(missing)))

    for pid, newval in changes.items():
        el = params[pid].find("ParamValue")
        if el is None:
            raise SystemExit("ERROR: %s has no ParamValue for %s" % (src, pid))
        el.text = newval

    name_el = tree.getroot().find(".//PresetName")
    if name_el is not None and name_el.text:
        # Keep the localisation key shape but make the visible tail say what this actually is.
        name_el.text = "%s %dx%d %s" % (name_el.text.rstrip(")"), w, h,
                                        "/".join(str(x) for x in fps)) + ")"

    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    tree.write(out, encoding="UTF-8", xml_declaration=True)

    # Read it back. A write that reported nothing is not evidence the value took.
    after = describe(out)
    problems = []
    if after["width"] != str(w) or after["height"] != str(h):
        problems.append("raster came out %sx%s, asked for %dx%d"
                        % (after["width"], after["height"], w, h))
    if after["fps_ticks"] != str(tpf):
        problems.append("fps came out %s ticks, asked for %d" % (after["fps_ticks"], tpf))
    if after["codec"] != before["codec"]:
        problems.append("the codec changed from %s to %s" % (before["codec"], after["codec"]))
    if problems:
        raise SystemExit("ERROR: the preset did not come out as asked:\n  - "
                         + "\n  - ".join(problems))
    return src, before, after, tpf


# --------------------------------------------------------------------------------------------

def selftest():
    import tempfile
    failures = []
    tmp = tempfile.mkdtemp(prefix="epr-selftest-")
    try:
        # --- a synthetic .epr, so the test does not depend on what this machine has installed
        synth = os.path.join(tmp, "template.epr")
        with open(synth, "w") as fh:
            fh.write("""<?xml version="1.0" encoding="UTF-8"?>
<PremiereData Version="3">
<PresetName>Test Preset</PresetName>
<A><ParamIdentifier>ADBEVideoCodec</ParamIdentifier><ParamValue>1634755432</ParamValue></A>
<B><ParamIdentifier>ADBEVideoWidth</ParamIdentifier><ParamValue>1920</ParamValue></B>
<C><ParamIdentifier>ADBEVideoHeight</ParamIdentifier><ParamValue>1080</ParamValue></C>
<D><ParamIdentifier>ADBEVideoFPS</ParamIdentifier><ParamValue>8475667200</ParamValue></D>
<E><ParamIdentifier>ADBEVideoMatchSource</ParamIdentifier><ParamValue>false</ParamValue></E>
</PremiereData>
""")
        d = describe(synth)
        if d["codec_fourcc"] != "apch" or "ProRes 422 HQ" not in d["codec_name"]:
            failures.append("the codec FourCC did not decode: %r" % d)

        out = os.path.join(tmp, "out.epr")
        src, before, after, tpf = build(synth, (1080, 1920), (30000, 1001), out)
        if (after["width"], after["height"]) != ("1080", "1920"):
            failures.append("raster not retargeted: %r" % after)
        if after["fps_ticks"] != "8475667200":
            failures.append("29.97 did not come out as 8475667200 ticks: %r" % after["fps_ticks"])
        if after["codec"] != before["codec"]:
            failures.append("the codec was not preserved")
        if after["match_source"] != "false":
            failures.append("match_source should be pinned false, got %r" % after["match_source"])

        # --- 25p and 24p must also land on whole ticks
        for fps, want in (((25, 1), 10160640000), ((24, 1), 10584000000),
                          ((24000, 1001), 10594584000)):
            o2 = os.path.join(tmp, "o_%d_%d.epr" % fps)
            _, _, a2, t2 = build(synth, (1080, 1920), fps, o2)
            if t2 != want:
                failures.append("fps %r gave %d ticks, expected %d" % (fps, t2, want))

        # --- a rate that cannot be expressed in whole ticks must be REFUSED.
        #
        # The tick base is 254016000000 = 2^12 * 3^4 * 5^6 * 7^2, chosen so that every real
        # broadcast rate divides it exactly — 30000/1001 and even 30000/1003 both come out whole.
        # So this guard only fires on a numerator carrying a prime the base does not have. 11 fps
        # is not a rate anyone shoots; it is the smallest honest way to prove the check works.
        for absurd in ((11, 1), (13, 1)):
            try:
                ticks_per_frame(absurd)
                failures.append("%r does not divide the tick base but was accepted" % (absurd,))
            except SystemExit:
                pass
        # and the real rates must all pass, so the guard cannot be over-eager
        for real in ((30000, 1001), (24000, 1001), (25, 1), (30, 1), (50, 1), (60000, 1001)):
            try:
                ticks_per_frame(real)
            except SystemExit:
                failures.append("real rate %r was refused" % (real,))

        # --- a template without the video params must be refused, not silently written
        bad = os.path.join(tmp, "audio.epr")
        with open(bad, "w") as fh:
            fh.write('<?xml version="1.0" encoding="UTF-8"?><PremiereData Version="3">'
                     '<A><ParamIdentifier>ADBEAudioBitrate</ParamIdentifier>'
                     '<ParamValue>0</ParamValue></A></PremiereData>')
        try:
            build(bad, (1080, 1920), (30000, 1001), os.path.join(tmp, "nope.epr"))
            failures.append("an audio-only preset was accepted as a video template")
        except SystemExit:
            pass

        # --- a missing template must name the --list escape hatch
        try:
            resolve_template("no-such-preset-anywhere.epr")
            failures.append("a missing template was accepted")
        except SystemExit as e:
            if "--list" not in str(e):
                failures.append("the missing-template error does not mention --list")

        # --- and the real shipped presets, if this machine has them: the point of the tool is
        #     that they are PINNED, so assert that rather than hoping.
        real = [p for p in find_templates() if os.path.basename(p) == "Apple ProRes 422 HQ.epr"]
        if real:
            d = describe(real[0])
            if d["match_source"] != "false":
                failures.append("the shipped ProRes preset now reports match_source=%r; the "
                                "premise of this tool has changed" % d["match_source"])
            if (d["width"], d["height"]) != ("1920", "1080"):
                failures.append("the shipped ProRes preset is %sx%s, not 1920x1080"
                                % (d["width"], d["height"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELFTEST FAILED (%d)" % len(failures))
        for f in failures:
            print("  - " + f)
        return 1
    print("make_export_preset selftest: all checks passed")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true", help="shipped presets on this machine")
    ap.add_argument("--describe", metavar="EPR")
    ap.add_argument("--template", help="a shipped preset name, or a path")
    ap.add_argument("--raster", help="WIDTHxHEIGHT, e.g. 1080x1920")
    ap.add_argument("--fps", help="NUM/DEN, e.g. 30000/1001")
    ap.add_argument("--match-source", action="store_true",
                    help="follow the sequence instead of pinning (not recommended for delivery)")
    ap.add_argument("--out")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    if a.list:
        rows = find_templates()
        if not rows:
            print("no shipped presets found under %s" % AME_SYSTEM_PRESETS)
            return 1
        print("%d shipped preset(s); video ones with their pinned raster:" % len(rows))
        for p in rows:
            try:
                d = describe(p)
            except ET.ParseError:
                continue
            if not d["width"]:
                continue
            print("  %-52s %5sx%-5s match=%-5s %s"
                  % (d["name"][:52], d["width"], d["height"], d["match_source"], d["codec_name"]))
        return 0

    if a.describe:
        d = describe(resolve_template(a.describe))
        for k, v in d.items():
            print("  %-14s %s" % (k, v))
        return 0

    if not (a.template and a.raster and a.fps and a.out):
        ap.error("--template, --raster, --fps and --out are required")

    w, h = (int(x) for x in a.raster.lower().split("x"))
    num, den = (int(x) for x in a.fps.split("/")) if "/" in a.fps else (int(a.fps), 1)

    src, before, after, tpf = build(a.template, (w, h), (num, den), a.out,
                                    match_source=a.match_source)
    print("template %s" % src)
    print("  was  %sx%s  fps_ticks %s  match_source %s  %s"
          % (before["width"], before["height"], before["fps_ticks"], before["match_source"],
             before["codec_name"]))
    print("wrote %s" % a.out)
    print("  now  %sx%s  fps_ticks %s (%d/%d)  match_source %s  %s"
          % (after["width"], after["height"], after["fps_ticks"], num, den,
             after["match_source"], after["codec_name"]))
    print("  read back and confirmed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

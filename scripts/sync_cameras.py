#!/usr/bin/env python3
"""Measure the time offset between recordings that share no timecode.

Two cameras with no common reference are normally synced with a clap. A clap gives
you one point in time and tells you nothing about whether the two clocks agree for
the rest of the take -- which is the expensive failure, because 29.97 and 30.000
drift about one frame every 33 seconds and no single sync point fixes that.

This measures the offset at several places instead, so the spread across probes IS
the drift measurement. It correlates onset envelopes (rectified first difference of
short-term loudness) rather than waveforms, so it works between completely different
microphones -- a lavalier mix against a camera's onboard mics, for example.

    python3 sync_cameras.py REFERENCE OTHER [OTHER ...]

Prints, for each OTHER, the offset to add to its timestamps to land in REFERENCE's
timeline, the number of probes that locked, and the spread between them.

    python3 sync_cameras.py --selftest

Plants known offsets and a known drift in synthetic audio and asserts each is
recovered. Run it after any edit: a measuring tool that silently stops measuring is
worse than no tool.

Requires ffmpeg/ffprobe and numpy.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import wave

try:
    import numpy as np
except ImportError:
    sys.exit("numpy is required: pip3 install numpy")

FPS = 100          # envelope frames per second; 10 ms resolution
SR = 16000         # plenty for a speech envelope
PROBE = 60.0       # seconds per probe window
MIN_SCORE = 8.0    # peak-to-median ratio below which a probe has not locked
AGREE_S = 1.0      # probes within this many seconds of each other are one cluster
MAX_PLAUSIBLE_PPM = 5000.0  # beyond this a "drift" figure is a mismatch, not a clock


def _run(cmd):
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        sys.exit("command failed: %s\n%s" % (" ".join(cmd[:6]), p.stderr.decode()[-600:]))
    return p.stdout


def duration(path):
    out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1", path])
    return float(out.decode().strip().split("\n")[0])


def load_mono(path, tmp):
    """Downmix every channel to one mono 16 kHz track."""
    wav = os.path.join(tmp, "m%d.wav" % abs(hash(path)))
    _run(["ffmpeg", "-y", "-v", "error", "-i", path, "-vn",
          "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", wav])
    with wave.open(wav, "rb") as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return x.astype(np.float32) / 32768.0


def onset_envelope(x):
    """Rectified first difference of short-term loudness, z-scored.

    Loudness in dB rather than linear, so a level difference between the two
    recordings cancels. The first difference keeps only onsets, which is what two
    different microphones in the same room genuinely agree about -- their spectra do
    not.
    """
    hop = SR // FPS
    n = len(x) // hop
    if n < 2:
        return np.zeros(0, np.float32)
    rms = np.sqrt((x[:n * hop].reshape(n, hop) ** 2).mean(axis=1))
    db = 20.0 * np.log10(np.maximum(rms, 1e-7))
    d = np.maximum(np.diff(db, prepend=db[0]), 0.0)
    sd = d.std()
    return ((d - d.mean()) / sd).astype(np.float32) if sd > 0 else d.astype(np.float32)


def locate(ref_env, probe):
    """Slide `probe` over `ref_env`; return (best start in seconds, peak/median)."""
    if len(probe) < 2 or len(ref_env) <= len(probe):
        return None, 0.0
    p = probe - probe.mean()
    sd = p.std()
    if sd == 0:
        return None, 0.0
    p = p / sd
    n = 1 << int(np.ceil(np.log2(len(ref_env) + len(p))))
    c = np.fft.irfft(np.fft.rfft(ref_env, n) * np.conj(np.fft.rfft(p, n)), n)
    c = c[:len(ref_env) - len(p) + 1]
    i = int(np.argmax(c))
    med = float(np.median(np.abs(c)))
    return i / float(FPS), (float(c[i]) / med if med > 0 else 0.0)


def measure(ref_env, other_env, other_dur):
    """Probe at several points. Spread between probes is the drift measurement."""
    starts = [t for t in (30, 90, 180, 300, 450, 600, 900, 1200, 1500, 1800)
              if t + PROBE <= other_dur]
    if not starts:
        starts = [0.0] if other_dur > 5 else []
    rows = []
    for s in starts:
        a = int(s * FPS)
        probe = other_env[a:a + int(PROBE * FPS)]
        at, score = locate(ref_env, probe)
        if at is not None:
            rows.append({"probe_at": s, "found_at": at, "offset": at - s, "score": score})
    scored = [r for r in rows if r["score"] >= MIN_SCORE]

    # Reject probes that fall outside the overlap. If one recording started earlier
    # than the other, its first probes have no counterpart at all and lock on noise;
    # averaging those in produces a confident, wildly wrong offset and an absurd
    # drift figure. Keep the largest cluster of probes that agree with each other
    # instead of trusting every probe that merely scored well.
    locked = []
    if scored:
        offs = np.array([r["offset"] for r in scored])
        best, bestn = None, 0
        for c in offs:
            n = int((np.abs(offs - c) <= AGREE_S).sum())
            if n > bestn:
                best, bestn = float(c), n
        locked = [r for r in scored if abs(r["offset"] - best) <= AGREE_S]
    out = {"probes": rows, "locked": len(locked), "total": len(rows),
           "scored": len(scored), "rejected": len(scored) - len(locked)}
    if locked:
        offs = np.array([r["offset"] for r in locked])
        out["offset"] = float(offs.mean())
        out["spread"] = float(offs.max() - offs.min())
        # drift across the span the probes cover, expressed as parts per million
        span = max(r["probe_at"] for r in locked) - min(r["probe_at"] for r in locked)
        out["span"] = float(span)
        if span >= 120 and len(locked) >= 2:
            lo = min(locked, key=lambda r: r["probe_at"])
            hi = max(locked, key=lambda r: r["probe_at"])
            ppm = float((hi["offset"] - lo["offset"]) / span * 1e6)
            # Two digital recorders differ by tens of ppm, not thousands. A huge
            # figure means the probes disagree, not that a clock is 30% fast.
            if abs(ppm) <= MAX_PLAUSIBLE_PPM:
                out["drift_ppm"] = ppm
            else:
                out["drift_implausible_ppm"] = ppm
    return out


def fmt(t):
    sign = "-" if t < 0 else ""
    t = abs(t)
    return "%s%02d:%02d:%06.3f" % (sign, int(t // 3600), int((t % 3600) // 60), t % 60)


def report(ref, others, as_json=False, quiet=False):
    tmp = tempfile.mkdtemp(prefix="synccam-")
    try:
        ref_env = onset_envelope(load_mono(ref, tmp))
        results = {}
        for o in others:
            d = duration(o)
            r = measure(ref_env, onset_envelope(load_mono(o, tmp)), d)
            r["duration"] = d
            results[o] = r
        if as_json:
            if not quiet:
                print(json.dumps(results, indent=2))
            return results
        print("reference: %s" % os.path.basename(ref))
        for o, r in results.items():
            print("\n%s  (%s)" % (os.path.basename(o), fmt(r["duration"])))
            if not r.get("locked"):
                print("  NO LOCK -- %d/%d probes. No shared audio content?"
                      % (r["locked"], r["total"]))
                continue
            print("  offset  %+.3f s   -> reference_time = this_time %+.3f" % (r["offset"], r["offset"]))
            print("  probes  %d/%d agree, spread %.3f s over a %.0f s span%s"
                  % (r["locked"], r["total"], r["spread"], r.get("span", 0),
                     ("   (%d probe(s) rejected as outliers)" % r["rejected"]) if r.get("rejected") else ""))
            if r.get("rejected"):
                print("          Outliers usually mean the recordings only partly overlap -- one")
                print("          started before the other, so its early probes have no counterpart.")
            if "drift_implausible_ppm" in r:
                print("  drift   NOT REPORTED: probes implied %.0f ppm, which is not a clock difference."
                      % r["drift_implausible_ppm"])
            if "drift_ppm" in r:
                ppm = r["drift_ppm"]
                # 29.97 against 30.000 is 1001/1000, i.e. about 1000 ppm
                note = ("consistent with 29.97 vs 30.000 -- check both cameras' frame rates"
                        if abs(ppm) > 200 else "no meaningful drift")
                print("  drift   %+.0f ppm  (%s)" % (ppm, note))
            if r["spread"] > 0.5:
                print("  WARNING spread above 0.5 s: the offset is not constant, so a single")
                print("          sync point will not hold. Treat the drift figure as real.")
            print("  covers  reference %s to %s"
                  % (fmt(r["offset"]), fmt(r["offset"] + r["duration"])))
        return results
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- #
# self-test: plant known offsets and a known drift, assert each is recovered
# --------------------------------------------------------------------------- #

def _synth(path, seconds, seed, rate=1.0, lead=0.0):
    """Speech-like bursts: random onsets are what the envelope correlates on.

    `rate` stretches the timebase to simulate a clock running fast or slow;
    `lead` prepends silence to simulate starting the recording later.
    """
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    x = np.zeros(n, np.float32)
    t = 0.7
    while t < seconds - 0.5:
        dur = float(rng.uniform(0.25, 1.1))
        amp = float(rng.uniform(0.15, 0.8))
        a = int(t * SR)
        b = min(n, a + int(dur * SR))
        env = np.hanning(max(b - a, 2))[: b - a].astype(np.float32)
        x[a:b] += (rng.standard_normal(b - a).astype(np.float32) * amp * env)
        t += dur + float(rng.uniform(0.15, 0.9))
    if rate != 1.0:
        m = int(len(x) / rate)
        x = np.interp(np.arange(m) / rate, np.arange(len(x)), x).astype(np.float32)
    if lead > 0:
        x = np.concatenate([np.zeros(int(lead * SR), np.float32), x])
    x = np.clip(x, -0.99, 0.99)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((x * 32767).astype(np.int16).tobytes())


def selftest():
    tmp = tempfile.mkdtemp(prefix="synccam-test-")
    fails = []

    def check(name, ok, detail=""):
        print(("  PASS  " if ok else "  FAIL  ") + name + (("  -- " + detail) if detail else ""))
        if not ok:
            fails.append(name)

    try:
        print("sync_cameras self-test")

        # 1. a plain planted offset must come back
        ref = os.path.join(tmp, "ref.wav")
        _synth(ref, 400, seed=1)
        for planted in (12.34, 45.60, 123.45):
            other = os.path.join(tmp, "o%.0f.wav" % (planted * 100))
            # the other camera started LATE by `planted`, so its t=0 is ref t=planted
            _run(["ffmpeg", "-y", "-v", "error", "-ss", "%.3f" % planted, "-i", ref,
                  "-c:a", "pcm_s16le", other])
            r = report(ref, [other], as_json=True, quiet=True)[other]
            got = r.get("offset")
            ok = got is not None and abs(got - planted) <= 0.05
            check("planted offset %.2f s recovered" % planted, ok,
                  "got %s" % ("%.3f" % got if got is not None else "no lock"))

        # 2. unrelated audio must NOT produce a confident answer
        alien = os.path.join(tmp, "alien.wav")
        _synth(alien, 200, seed=999)
        r = report(ref, [alien], as_json=True, quiet=True)[alien]
        check("unrelated audio does not lock confidently",
              r["locked"] < r["total"] or r.get("spread", 9) > 0.5,
              "locked %d/%d spread %.3f" % (r["locked"], r["total"], r.get("spread", -1)))

        # 3. a clock running 1000 ppm fast (29.97 vs 30.000) must be reported as drift
        drifted = os.path.join(tmp, "drift.wav")
        _synth(drifted, 900, seed=1, rate=1000.0 / 1001.0)
        refl = os.path.join(tmp, "refl.wav")
        _synth(refl, 900, seed=1)
        r = report(refl, [drifted], as_json=True, quiet=True)[drifted]
        spread_ok = r.get("spread", 0) > 0.2
        ppm = r.get("drift_ppm")
        ppm_ok = ppm is not None and abs(ppm) > 200
        check("a 1000 ppm clock difference shows up as drift", spread_ok and ppm_ok,
              "spread %.3f s, drift %s ppm" % (r.get("spread", -1),
                                               ("%.0f" % ppm) if ppm is not None else "n/a"))

        # 4. and the no-drift case must NOT claim drift
        r = report(refl, [refl], as_json=True, quiet=True)[refl]
        check("identical input reports ~zero offset and no drift",
              abs(r.get("offset", 9)) <= 0.02 and abs(r.get("drift_ppm", 0)) < 200,
              "offset %.3f, drift %s" % (r.get("offset", -9),
                                         r.get("drift_ppm", "n/a")))

        # 5. PARTIAL OVERLAP -- the case that broke this tool on real files.
        #
        # A lavalier that ran long overlaps only part of the camera's take. The
        # probes inside the overlap lock correctly; the ones past its end have no
        # counterpart and lock on noise. Averaging all of them gave a confident
        # offset that was wrong and a drift figure of -354294 ppm, which is
        # physically impossible and was reported as fact. The fix is to keep the
        # largest cluster of probes that AGREE, so this test asserts two things:
        # the offset is still right, and probes were actually rejected.
        long_ref = os.path.join(tmp, "partial_ref.wav")
        _synth(long_ref, 300, seed=7)
        tail = os.path.join(tmp, "partial_tail.wav")
        _synth(tail, 600, seed=31)         # unrelated material, no counterpart
        head = os.path.join(tmp, "partial_head.wav")
        _run(["ffmpeg", "-y", "-v", "error", "-ss", "40", "-i", long_ref,
              "-c:a", "pcm_s16le", head])  # 260 s that DOES match, at offset 40
        lst = os.path.join(tmp, "partial.txt")
        with open(lst, "w") as fh:
            fh.write("file '%s'\nfile '%s'\n" % (head, tail))
        partial = os.path.join(tmp, "partial.wav")
        _run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
              "-i", lst, "-c:a", "pcm_s16le", partial])
        r = report(long_ref, [partial], as_json=True, quiet=True)[partial]
        got, ppm = r.get("offset"), r.get("drift_ppm")
        ok = (got is not None and abs(got - 40.0) <= 0.05
              and r.get("rejected", 0) > 0
              and (ppm is None or abs(ppm) <= MAX_PLAUSIBLE_PPM))
        check("partial overlap: locks on the overlap and rejects the rest", ok,
              "offset %s, %s of %s scored probes rejected, drift %s ppm"
              % ("%.3f" % got if got is not None else "no lock",
                 r.get("rejected"), r.get("scored"),
                 ("%.0f" % ppm) if ppm is not None else "n/a"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n%d check(s) failed" % len(fails) if fails else "\nall checks passed")
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser(description="Measure offset and drift between recordings with no shared timecode.")
    ap.add_argument("reference", nargs="?", help="the recording whose timeline everything else maps into")
    ap.add_argument("others", nargs="*", help="one or more recordings to locate")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.reference or not a.others:
        ap.error("give a reference and at least one other recording (or --selftest)")
    for p in [a.reference] + a.others:
        if not os.path.exists(p):
            sys.exit("not found: %s" % p)
    report(a.reference, a.others, as_json=a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())

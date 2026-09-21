#!/usr/bin/env python3
"""Attribute speech to speakers by measuring which lavalier was louder.

When a wireless receiver is in split mode, each person is on their own channel. Who
spoke when then stops being an inference about turn-taking and becomes a
measurement: for every word, compare the two channels and take the louder one.

Two things this does that a naive version gets wrong:

  * It checks the channels are actually independent first. Two channels carrying one
    summed mix look like a split until you correlate them, and then every
    attribution is noise. A Pearson r near zero means a real split; near 1 means a
    mix copied twice, and this refuses to guess.

  * It attributes per WORD, not per transcript segment. Speech-to-text segments
    straddle speaker changes, so a segment-level label puts the tail of one person's
    question at the start of the other person's answer. Real example: an answer that
    began "particular wearables?" -- the last two words of the question before it.

It also reports each channel's ISOLATION -- how much louder a person is on their own
lavalier than on the other one. That number decides what cleanup is possible later:
above about 12 dB a plain noise gate will separate the two; below that a gate
chatters and you need these per-word labels to duck the bleed instead. It is worth
measuring on a test recording before the shoot, because it is a microphone placement
problem and no amount of post fixes it.

    python3 diarize_by_channel.py MEDIA --words words.json \
        --channels 3,4 --names Host,Guest --srt out.srt --turns turns.json

`--words` takes a Whisper-style JSON: {"segments":[{"words":[{"word","start","end"}]}]}
which is what `whisper`, `faster-whisper` and `mlx-whisper` all emit with word
timestamps enabled. Pass `--transcribe` instead to run mlx-whisper here, if installed.

    python3 diarize_by_channel.py --selftest

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

SR = 16000
FRAME = 0.02          # 20 ms analysis frames: fine enough for a single word
SMOOTH = 5            # rolling-median width in words; kills single-word flips
CONFIDENT_DB = 3.0    # a word with this much margin is never overridden by neighbours
GATE_OVER_FLOOR = 8.0  # dB above the noise floor before a frame counts as voiced
MIX_R = 0.5           # |r| above this means the channels are not independent


def _run(cmd):
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        sys.exit("command failed: %s\n%s" % (" ".join(cmd[:6]), p.stderr.decode()[-600:]))
    return p.stdout


def extract_pair(media, chans, tmp):
    """Pull two source channels out as one stereo 16 kHz file (0-based indices)."""
    wav = os.path.join(tmp, "pair.wav")
    pan = "pan=stereo|c0=c%d|c1=c%d" % (chans[0], chans[1])
    _run(["ffmpeg", "-y", "-v", "error", "-i", media, "-vn",
          "-filter_complex", "[0:a]%s,aformat=sample_rates=%d:channel_layouts=stereo[a]" % (pan, SR),
          "-map", "[a]", "-c:a", "pcm_s16le", wav])
    with wave.open(wav, "rb") as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return (x.astype(np.float32) / 32768.0).reshape(-1, 2)


def frame_db(x):
    hop = int(FRAME * SR)
    n = len(x) // hop
    rms = np.sqrt((x[:n * hop].reshape(n, hop) ** 2).mean(axis=1))
    return 20.0 * np.log10(np.maximum(rms, 1e-9))


def independence(pair):
    """Pearson r between the channels. Near 0 = a real split; near 1 = one mix."""
    a, b = pair[:, 0], pair[:, 1]
    if a.std() == 0 or b.std() == 0:
        return 1.0
    return float(abs(np.corrcoef(a, b)[0, 1]))


def isolation(dA, dB, floor):
    """Per channel: own-voice level, the other person's bleed, and the gap."""
    gate = floor + GATE_OVER_FLOOR
    loud = np.maximum(dA, dB)
    live = loud > gate
    diff = dA - dB
    out = {}
    for name, own, other, sign in (("A", dA, dB, 1), ("B", dB, dA, -1)):
        mine = live & ((diff * sign) > 6)
        theirs = live & ((diff * sign) < -6)
        if mine.sum() > 10 and theirs.sum() > 10:
            speech = float(np.median(own[mine]))
            bleed = float(np.median(own[theirs]))
            out[name] = {"own_dbfs": speech, "bleed_dbfs": bleed, "isolation_db": speech - bleed}
        else:
            out[name] = None
    return out


def load_words(path):
    data = json.load(open(path))
    segs = data["segments"] if isinstance(data, dict) else data
    words = []
    for s in segs:
        for w in (s.get("words") or []):
            tok = w.get("word", w.get("text", ""))
            if tok.strip() and w.get("start") is not None:
                words.append({"tok": tok, "start": float(w["start"]), "end": float(w["end"])})
    return words


def attribute(words, dA, dB, floor):
    """Label each word by the louder channel over its voiced frames, then smooth."""
    gate = floor + GATE_OVER_FLOOR
    loud = np.maximum(dA, dB)
    n = len(dA)
    raw = []
    for w in words:
        i = max(0, int(w["start"] / FRAME))
        j = min(n, max(int(w["end"] / FRAME) + 1, i + 1))
        if j <= i:
            raw.append(0.0)
            continue
        sl = loud[i:j] > gate
        d = (dA[i:j] - dB[i:j])
        raw.append(float(np.median(d[sl])) if sl.sum() >= 1 else float(np.median(d)))
    raw = np.asarray(raw, dtype=np.float64)
    if len(raw) == 0:
        return [], raw, raw
    # Smooth only where the measurement is weak. A blanket median filter also
    # rewrites words that were measured confidently, and when a turn is shorter than
    # the filter it drags the whole turn to the wrong speaker -- which is how this
    # first shipped, and what the self-test caught.
    k = min(SMOOTH, len(raw) if len(raw) % 2 else max(len(raw) - 1, 1))
    pad = np.pad(raw, (k // 2, k // 2), mode="edge")
    med = np.array([np.median(pad[i:i + k]) for i in range(len(raw))])
    sm = np.where(np.abs(raw) >= CONFIDENT_DB, raw, med)
    return ["A" if v > 0 else "B" for v in sm], raw, sm


def build_turns(words, labels, names, max_gap=8.0):
    tidy = lambda toks: " ".join("".join(toks).split())
    turns = []
    for w, lab in zip(words, labels):
        if turns and turns[-1]["speaker"] == lab and w["start"] - turns[-1]["end"] <= max_gap:
            turns[-1]["end"] = w["end"]
            turns[-1]["toks"].append(w["tok"])
        else:
            turns.append({"speaker": lab, "start": w["start"], "end": w["end"], "toks": [w["tok"]]})
    for t in turns:
        t["text"] = tidy(t.pop("toks"))
        t["name"] = names[0] if t["speaker"] == "A" else names[1]
    return turns


def write_srt(path, words, labels, names, max_len=84, max_dur=6.0, gap=1.6):
    def ts(t):
        ms = int(round((t - int(t)) * 1000)); s = int(t)
        if ms == 1000:
            s += 1; ms = 0
        return "%02d:%02d:%02d,%03d" % (s // 3600, (s % 3600) // 60, s % 60, ms)

    def wrap(x, w=42):
        out, line = [], ""
        for word in x.split():
            if len(line) + len(word) + 1 > w and line:
                out.append(line); line = word
            else:
                line = (line + " " + word).strip()
        if line:
            out.append(line)
        return out[:2]

    tidy = lambda toks: " ".join("".join(toks).split())
    cues, cur = [], None
    for w, lab in zip(words, labels):
        if cur and (lab != cur["speaker"] or w["start"] - cur["end"] > gap
                    or w["end"] - cur["start"] > max_dur
                    or len(tidy(cur["toks"])) + len(w["tok"]) > max_len):
            cues.append(cur); cur = None
        if cur is None:
            cur = {"speaker": lab, "start": w["start"], "end": w["end"], "toks": [w["tok"]]}
        else:
            cur["end"] = w["end"]; cur["toks"].append(w["tok"])
        if w["tok"].strip().endswith((".", "!", "?")) and len(tidy(cur["toks"])) > 34:
            cues.append(cur); cur = None
    if cur:
        cues.append(cur)
    lines, prev = [], None
    for i, c in enumerate(cues, 1):
        who = names[0] if c["speaker"] == "A" else names[1]
        label = (who + ": ") if c["speaker"] != prev else ""
        prev = c["speaker"]
        lines.append("%d\n%s --> %s\n%s\n" % (i, ts(c["start"]), ts(c["end"]),
                                              "\n".join(wrap(label + tidy(c["toks"])))))
    open(path, "w").write("\n".join(lines))
    return len(cues)


def transcribe(media, tmp):
    try:
        import mlx_whisper
    except ImportError:
        sys.exit("--transcribe needs mlx-whisper (pip3 install mlx-whisper), "
                 "or pass --words from any Whisper-compatible tool")
    wav = os.path.join(tmp, "asr.wav")
    _run(["ffmpeg", "-y", "-v", "error", "-i", media, "-vn", "-ac", "1", "-ar", str(SR),
          "-c:a", "pcm_s16le", wav])
    r = mlx_whisper.transcribe(wav, word_timestamps=True, condition_on_previous_text=False,
                               verbose=False)
    p = os.path.join(tmp, "words.json")
    json.dump(r, open(p, "w"))
    return p


def run(media, words_json, chans, names, srt_out, turns_out, force):
    tmp = tempfile.mkdtemp(prefix="diar-")
    try:
        if words_json is None:
            words_json = transcribe(media, tmp)
        words = load_words(words_json)
        pair = extract_pair(media, chans, tmp)
        r = independence(pair)
        dA, dB = frame_db(pair[:, 0]), frame_db(pair[:, 1])
        floor = float(np.percentile(np.maximum(dA, dB), 15))

        print("channels %d/%d  |r| = %.3f  --  %s"
              % (chans[0] + 1, chans[1] + 1, r,
                 "independent, a real split" if r < MIX_R else "CORRELATED"))
        if r >= MIX_R and not force:
            sys.exit("These channels are not independent (|r| = %.3f). They are most likely one\n"
                     "mix copied twice, so per-channel attribution would be noise. Check the\n"
                     "receiver was in split mode. Use --force to override." % r)

        iso = isolation(dA, dB, floor)
        for key, who in (("A", names[0]), ("B", names[1])):
            v = iso[key]
            if not v:
                print("  %-10s isolation could not be measured (too little solo speech)" % who)
                continue
            warn = ""
            if v["isolation_db"] < 12:
                warn = "  <-- LOW: a noise gate will chatter here; duck by label instead"
            print("  %-10s own %6.1f dBFS   bleed %6.1f dBFS   isolation %5.1f dB%s"
                  % (who, v["own_dbfs"], v["bleed_dbfs"], v["isolation_db"], warn))

        labels, raw, sm = attribute(words, dA, dB, floor)
        if not labels:
            sys.exit("no words with timings found in %s" % words_json)
        margin = np.abs(sm)
        print("  %d words, %.1f%% separated by more than 1 dB"
              % (len(labels), 100.0 * float((margin > 1).mean())))
        turns = build_turns(words, labels, names)
        share = {}
        for t in turns:
            share[t["name"]] = share.get(t["name"], 0.0) + (t["end"] - t["start"])
        print("  %d turns  --  %s" % (len(turns),
              "  ".join("%s %.1f min" % (k, v / 60) for k, v in share.items())))
        if turns_out:
            json.dump(turns, open(turns_out, "w"), indent=1)
            print("  wrote %s" % turns_out)
        if srt_out:
            n = write_srt(srt_out, words, labels, names)
            print("  wrote %s (%d cues)" % (srt_out, n))
        return turns
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- #
# self-test
# --------------------------------------------------------------------------- #

def _two_channel_fixture(path, plan, iso_db=16.0, seed=3):
    """plan = [(start, end, 'A'|'B')]. The speaker is loud on their own channel and
    `iso_db` quieter on the other, which is exactly how two lavaliers behave."""
    rng = np.random.default_rng(seed)
    total = max(e for _, e, _ in plan) + 1.0
    n = int(total * SR)
    a = rng.standard_normal(n).astype(np.float32) * 0.0008   # room floor
    b = rng.standard_normal(n).astype(np.float32) * 0.0008
    bleed = 10 ** (-iso_db / 20.0)
    for s, e, who in plan:
        i, j = int(s * SR), int(e * SR)
        sig = (rng.standard_normal(j - i).astype(np.float32)
               * 0.25 * np.hanning(max(j - i, 2))[: j - i].astype(np.float32))
        if who == "A":
            a[i:j] += sig; b[i:j] += sig * bleed
        else:
            b[i:j] += sig; a[i:j] += sig * bleed
    st = np.stack([a, b], axis=1)
    st = np.clip(st, -0.99, 0.99)
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((st * 32767).astype(np.int16).tobytes())


def selftest():
    tmp = tempfile.mkdtemp(prefix="diar-test-")
    fails = []

    def check(name, ok, detail=""):
        print(("  PASS  " if ok else "  FAIL  ") + name + (("  -- " + detail) if detail else ""))
        if not ok:
            fails.append(name)

    try:
        print("diarize_by_channel self-test")
        plan = [(1.0, 3.0, "A"), (3.4, 7.0, "B"), (7.5, 9.0, "A"),
                (9.4, 13.0, "B"), (13.5, 15.5, "A"), (16.0, 19.0, "B")]
        media = os.path.join(tmp, "two.wav")
        _two_channel_fixture(media, plan)

        # one word per 0.4 s inside each planted span, labelled with the truth
        words, truth = [], []
        for s, e, who in plan:
            t = s + 0.05
            while t + 0.35 < e:
                words.append({"word": " w", "start": round(t, 3), "end": round(t + 0.3, 3)})
                truth.append(who)
                t += 0.4
        json.dump({"segments": [{"words": words}]}, open(os.path.join(tmp, "w.json"), "w"))

        pair = extract_pair(media, (0, 1), tmp)
        dA, dB = frame_db(pair[:, 0]), frame_db(pair[:, 1])
        floor = float(np.percentile(np.maximum(dA, dB), 15))
        labels, _, _ = attribute(load_words(os.path.join(tmp, "w.json")), dA, dB, floor)
        acc = sum(1 for g, t in zip(labels, truth) if g == t) / float(len(truth))
        check("planted speakers recovered", acc >= 0.95, "%.0f%% of %d words" % (acc * 100, len(truth)))

        # a real split must read as independent; one mix copied twice must not
        check("independent channels measure as a split", independence(pair) < MIX_R,
              "|r| = %.3f" % independence(pair))
        mono = os.path.join(tmp, "mono_dupe.wav")
        _two_channel_fixture(mono, plan, iso_db=0.0, seed=3)   # 0 dB isolation = same signal
        pm = extract_pair(mono, (0, 1), tmp)
        check("one mix copied twice is caught", independence(pm) >= MIX_R,
              "|r| = %.3f" % independence(pm))

        # isolation must come back near what was planted
        iso = isolation(dA, dB, floor)
        got = iso["A"]["isolation_db"] if iso["A"] else None
        check("planted 16 dB isolation measured back", got is not None and 10 <= got <= 22,
              "got %s dB" % ("%.1f" % got if got is not None else "n/a"))

        # a word straddling a speaker change must not drag the whole turn with it
        straddle = list(words)
        straddle.append({"word": " x", "start": 3.3, "end": 3.6})   # spans A->B boundary
        json.dump({"segments": [{"words": straddle}]}, open(os.path.join(tmp, "w2.json"), "w"))
        l2, _, _ = attribute(load_words(os.path.join(tmp, "w2.json")), dA, dB, floor)
        turns = build_turns(load_words(os.path.join(tmp, "w2.json")), l2, ("A", "B"))
        check("a straddling word does not merge two turns", len(turns) >= len(plan) - 1,
              "%d turns for %d planted spans" % (len(turns), len(plan)))

        srt = os.path.join(tmp, "o.srt")
        ncues = write_srt(srt, load_words(os.path.join(tmp, "w.json")), labels, ("Host", "Guest"))
        body = open(srt).read()
        check("SRT is well formed and names the speakers",
              ncues > 0 and "-->" in body and "Host:" in body and "Guest:" in body,
              "%d cues" % ncues)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n%d check(s) failed" % len(fails) if fails else "\nall checks passed")
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser(description="Attribute speech to speakers by comparing per-speaker channels.")
    ap.add_argument("media", nargs="?")
    ap.add_argument("--words", help="Whisper-style JSON with word timestamps")
    ap.add_argument("--transcribe", action="store_true", help="run mlx-whisper here instead")
    ap.add_argument("--channels", default="1,2",
                    help="1-based source channels carrying the two speakers (default 1,2)")
    ap.add_argument("--names", default="Speaker 1,Speaker 2")
    ap.add_argument("--srt")
    ap.add_argument("--turns")
    ap.add_argument("--force", action="store_true", help="proceed even if the channels look correlated")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.media:
        ap.error("give a media file (or --selftest)")
    if not a.words and not a.transcribe:
        ap.error("pass --words WORDS.json or --transcribe")
    ch = tuple(int(c) - 1 for c in a.channels.split(",")[:2])
    names = tuple((a.names.split(",") + ["Speaker 2"])[:2])
    run(a.media, a.words, ch, names, a.srt, a.turns, a.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())

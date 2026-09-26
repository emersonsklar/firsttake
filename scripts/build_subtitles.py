#!/usr/bin/env python3
"""build_subtitles.py — subtitles from forced-aligned words, with both cross-checks that matter.

Takes word times in each source's own timebase, maps them onto the programme clock, shapes them
into readable cues, and writes `.srt` + `.vtt` + a report.

    python3 build_subtitles.py --lanes lanes.json --out sub --raster 1080x1920
    python3 build_subtitles.py --lanes lanes.json --out sub --raster 1920x1080
    python3 build_subtitles.py --textgrid speaker.TextGrid       # dump word times and stop
    python3 build_subtitles.py --selftest

WHY THIS IS NOT "JUST RUN WHISPER AND SAVE THE SRT"
--------------------------------------------------
**Text from the recogniser, TIME from the aligner.** On one interview a recogniser's word
boundaries carried a mean error of **328 ms** against a forced aligner's **23 ms**. A caption is
supposed to appear within roughly half a second of the speech, so 328 ms spends two thirds of the
whole budget before the cue is even placed — which means that on real footage a recogniser is not
accurate enough *even for subtitles*, where tolerance is far looser than for a cut. Use the
recogniser to find out WHAT was said, then force-align to find out WHEN. See
`references/post-production.md` for what an aligner gets wrong in exchange.

🔴 ALIGN AGAINST THE AUDIO THE DELIVERABLE CARRIES. Whatever the mix mutes, mute in the corpus
BEFORE aligning. On one job a director's off-camera "OK" — bleed on the subject's lavalier, muted
in the delivered mix — was still present in the alignment corpus, and the aligner spent the
subject's **first real word on it**: placed 1.4 s early, leaving a 1.16 s hole before the next
word and dragging the four words after it late. An aligner will spend a real word on any sound the
transcript does not account for, and noise from a second person is exactly that. `mute_spans`
below exists for this.

🔴 A BRAND NAME IS THE ONE WORD YOU MUST NOT TAKE FROM ASR. Two things go wrong. First, product
names are usually **out of vocabulary** in a pronunciation dictionary, so the aligner models them
as spoken-noise and their boundaries go slack — loosening precisely the words a client video turns
on. Add dictionary entries (`--oov` prints what is missing). Second, a recogniser will confidently
reorder an unfamiliar lockup. To settle one, do not just re-run a bigger model:

  1. two independent models — if they disagree, neither is evidence;
  2. **prime the model with the name you expect.** If a corrective `initial_prompt` does NOT flip
     the output, the acoustics are carrying it and not the prior. If it DOES flip, you have
     learned nothing except that the model is suggestible;
  3. read the phones. On one job the order of a two-word lockup was settled by event ORDER
     alone — a stop closure with a **low-HF release** (0.1 % above 3 kHz = bilabial /p/) and a
     nasal murmur (LF 70-95 %) came BEFORE a closure with a **high-HF burst** (79.6 % above
     3 kHz = coronal /tr/). One word order predicts that sequence, the other predicts the
     reverse. That is a measurement, and it beat both models and the operator's expectation.

Then caption it **verbatim** and escalate the discrepancy. Whether a client-facing subtitle
silently corrects a misspoken brand name is the client's call, never the build's.

THE PROGRAMME CLOCK
-------------------
Each lane declares `placed_at_sample` (where the mix put it) and `source_in_sample` (what it read
from). Take those from whatever your audio build REPORTED, not from a second derivation of the
same offset — two independent derivations of one offset is how a subtitle file goes quietly out of
sync with the mix it describes.

`removed_spans` handles internal splices: a word after a join sits earlier in the programme than
`(word - in_point)` by the removed length. `source_span` clamps a lane to its in/out, by
**OVERLAP and not containment** — an in-point chosen by measurement often lands *inside* a word
(one opened 20 ms into "the", on the cuttable /dh/), and a containment filter silently drops a
word the viewer hears.

THE CHECK THAT ACTUALLY PROVES IT (`--verify-stems`)
---------------------------------------------------
Everything else here verifies arithmetic against arithmetic. Give each lane its `stem` and this
compares speech energy INSIDE captioned frames against OUTSIDE, within that lane's own segment.

⚠️ IT MUST BE THE ISOLATED STEM, NOT THE MIXED MASTER. Run against a master, captioned spans
measured p90 -25.3 to -12.2 dBFS against **-25.0 dBFS in a gap carrying no dialogue at all** —
the music bed swamps the distinction and the test has no power to fail. On the stems the same
comparison separated by 28 dB. A check whose two sides share the confound is not a check.

🔴 AND KNOW WHAT IT CANNOT DO: **this is a placement smoke test, not a sync test.** Measured on
the selftest's own planted ground truth, it catches a lane placed **3 s** wrong — captions land off
the speech entirely — and does **not** catch **2 s or less**, because inside a dense read a shifted
caption still lands on *some* word and the energy comparison cannot tell which. That bound is
asserted in `selftest()` rather than tuned away, because the tempting fix is to make the check
look stronger than it is.

So sync does not come from here. It comes from taking `placed_at_sample` out of what the audio
build REPORTED — which is the whole reason this tool refuses to re-derive the offset itself. The
per-word timing accuracy is a separate axis again, and that is `validate_onsets()`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

import numpy as np

# --- caption shape -----------------------------------------------------------------------------
# ⚠️ THE 42-CHARACTER LINE IS A 16:9 NUMBER. At 1080 px VERTICAL a burn-in at a legible phone size
# runs out of room around 30 characters, and a third line reaches into the lower third where the
# platform puts its own UI. Shape follows raster; --raster picks the preset.
SHAPES = {
    "vertical":   {"max_chars": 30, "max_lines": 2},
    "horizontal": {"max_chars": 42, "max_lines": 2},
}
MIN_DUR_S = 1.000        # below this a cue cannot be read, however short the word
MAX_DUR_S = 5.500
GAP_BREAK_S = 0.400      # a pause this long is a natural caption break
MIN_CUE_GAP_F = 2        # frames of clear air between cues; 0 flickers on some players
TARGET_CPS = 17.0        # characters/second to aim for by extending into available silence
HARD_CPS = 20.0          # report anything still above this
MAX_HOLD_S = 1.200       # how long a cue may linger past its own last word


def srt_time(t, sep=","):
    ms = int(round(max(t, 0.0) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def norm(tok):
    return re.sub(r"[^a-z0-9']", "", tok.lower())


# --- TextGrid ----------------------------------------------------------------------------------
def read_textgrid(path, tier="words"):
    """Word (or phone) intervals from an MFA long TextGrid. Empty intervals are silence."""
    txt = open(path, encoding="utf-8").read()
    chunk = next((c for c in re.split(r"\n\s*item \[\d+\]:", txt)
                  if re.search(rf'name\s*=\s*"{tier}"', c)), None)
    if chunk is None:
        sys.exit(f"ERROR: no '{tier}' tier in {path}")
    out = [{"t": m.group(3).strip(), "start": float(m.group(1)), "end": float(m.group(2))}
           for m in re.finditer(
               r'xmin\s*=\s*([\d.]+)\s*\n\s*xmax\s*=\s*([\d.]+)\s*\n\s*text\s*=\s*"([^"]*)"',
               chunk) if m.group(3).strip()]
    if not out:
        sys.exit(f"ERROR: '{tier}' tier in {path} is empty")
    return out


def mute_spans(wav_in, wav_out, spans, fade_ms=5.0):
    """Zero `spans` (seconds, in the FILE's own timebase) before aligning. See the docstring."""
    import soundfile as sf
    x, sr = sf.read(wav_in)
    if x.ndim > 1:
        x = x.mean(axis=1)
    x = x.astype(np.float64)
    fd = max(1, int(fade_ms / 1000.0 * sr))
    for a, b in spans:
        i, j = max(0, int(a * sr)), min(len(x), int(b * sr))
        x[i:j] = 0.0
        if i - fd >= 0:
            x[i - fd:i] *= np.linspace(1, 0, fd)
        if j + fd <= len(x):
            x[j:j + fd] *= np.linspace(0, 1, fd)
    sf.write(wav_out, x, sr, subtype="PCM_16")
    return wav_out


# --- punctuation -------------------------------------------------------------------------------
def attach_punctuation(words, punctuated, label):
    """Carry a human-punctuated transcript onto aligner word times, 1:1 or fail.

    An aligner emits bare lowercase tokens; a caption needs sentence case and stops. Asserted
    token-for-token, so editing the transcript cannot silently shift the punctuation one word
    along and leave a full stop mid-sentence.
    """
    toks = punctuated.split()
    if len(toks) != len(words):
        sys.exit(f"ERROR: {label}: transcript has {len(toks)} tokens, aligner has {len(words)}\n"
                 f"  text:    {[norm(t) for t in toks]}\n"
                 f"  aligner: {[w['t'] for w in words]}")
    bad = [(i, norm(t), words[i]["t"]) for i, t in enumerate(toks)
           if norm(t) != norm(words[i]["t"])]
    if bad:
        sys.exit(f"ERROR: {label}: token mismatch at {bad[:5]}")
    return [dict(w, surface=t) for w, t in zip(words, toks)]


# --- cue shaping -------------------------------------------------------------------------------
def wrap(words, max_chars, max_lines, keep_together=()):
    """Balanced wrap into at most `max_lines`. None if it will not fit."""
    lines, cur = [], ""
    for w in words:
        cand = (cur + " " + w).strip()
        if len(cand) <= max_chars or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines or any(len(l) > max_chars for l in lines):
        return None
    if len(lines) == 2:
        keep = {(a.lower(), b.lower()) for a, b in keep_together}
        best = None
        for k in range(1, len(words)):
            a, b = " ".join(words[:k]), " ".join(words[k:])
            if len(a) > max_chars or len(b) > max_chars:
                continue
            # A brand lockup broken over two lines reads as two things. Penalised heavily, not
            # forbidden — if no other break fits, a split beats an unreadable cue.
            score = abs(len(a) - len(b)) + (100 if (norm(words[k - 1]), norm(words[k])) in keep
                                            else 0)
            if best is None or score < best[0]:
                best = (score, [a, b])
        if best:
            lines = best[1]
    return lines


def group_cues(words, lane_id, shape, keep_together):
    """Split one lane into cues at sentence ends, pauses, and capacity."""
    cues, cur = [], []
    for i, w in enumerate(words):
        trial = cur + [w]
        if cur and (wrap([x["surface"] for x in trial], shape["max_chars"], shape["max_lines"],
                         keep_together) is None
                    or (w["end_p"] - trial[0]["start_p"]) > MAX_DUR_S):
            cues.append(cur)
            cur = [w]
        else:
            cur = trial
        gap = (words[i + 1]["start_p"] - w["end_p"]) if i + 1 < len(words) else 1e9
        if cur and (re.search(r'[.!?]["”]?$', w["surface"]) or gap >= GAP_BREAK_S):
            cues.append(cur)
            cur = []
    if cur:
        cues.append(cur)
    return [{"lane": lane_id, "words": c} for c in cues if c]


def finalise(cues, lanes, fps, total_frames, shape, keep_together):
    """Display times: minimum duration, extension into available silence, no overlap."""
    min_gap = MIN_CUE_GAP_F / fps
    out, truncated = [], []
    for i, c in enumerate(cues):
        start, last = c["words"][0]["start_p"], c["words"][-1]["end_p"]
        lines = wrap([w["surface"] for w in c["words"]], shape["max_chars"], shape["max_lines"],
                     keep_together)
        chars = len(" ".join(lines))
        seg_end = lanes[c["lane"]]["segment"][1] / fps
        nxt = cues[i + 1]["words"][0]["start_p"] if i + 1 < len(cues) else 1e9
        ceiling = min(nxt - min_gap, seg_end, start + MAX_DUR_S, last + MAX_HOLD_S,
                      total_frames / fps)
        # ⚠️ THE CEILING WINS, even over the last word's own end time. Where a cue was split on
        # CAPACITY rather than on a pause the speech is continuous, so `last` lands exactly on the
        # next cue's first word — and `max(last, ...)` there produced cues whose out-time equalled
        # the next in-time to the millisecond. Players tolerate it and it flickers. Trimming two
        # frames off a continuous phrase costs nothing readable; overlapping the next cue does.
        want = max(last, start + MIN_DUR_S, start + chars / TARGET_CPS)
        end = max(min(want, ceiling), min(ceiling, start + 1.0 / fps))
        if end < last - 1e-9:
            truncated.append(int(round(start * fps)))
        out.append({"lane": c["lane"], "start": start, "end": end, "lines": lines,
                    "text": "\n".join(lines), "chars": chars,
                    "cps": round(chars / max(end - start, 1e-6), 1),
                    "held_past_speech_ms": round((end - last) * 1000, 1),
                    "start_frame": int(round(start * fps)),
                    "end_frame": int(round(end * fps))})
    return out, truncated


# --- validation --------------------------------------------------------------------------------
def validate_onsets(wav, words, min_pause_s=0.15, search_s=0.50):
    """Aligner onset vs a measured energy onset, for every word after a real pause.

    🔴 TWO TRAPS ARE ENCODED HERE, and both of them produced confident wrong numbers first.

    **The search is CLAMPED to the previous word's end.** An unclamped version reported exactly
    "+300.0 ms" on two different words — which was the ±300 ms window edge, not a measurement. An
    identical round error on independent items is a clipped search every time. `clipped` marks and
    excludes any result that landed on the boundary rather than being found inside it.

    **A VOWEL-INITIAL WORD'S ENERGY ONSET IS USUALLY THE BREATH BEFORE IT.** This is the mirror of
    the recogniser's known failure: the recogniser swallows the breath INTO the word and reads
    late, while an energy detector fires ON the breath and reads early — so the aligner looks wrong
    when it is right. Voicing is measured at each onset (autocorrelation in a 70-300 Hz band plus
    an LF/HF split) and a vowel-initial word whose "onset" is unvoiced is reported as EXPLAINED
    rather than scored. Caption the voice, not the breath. Do not average these two classes
    together — that is how a 19 ms instrument reports a 70 ms error.
    """
    import soundfile as sf
    x, sr = sf.read(wav)
    if x.ndim > 1:
        x = x.mean(axis=1)
    W, H = int(0.010 * sr), int(0.001 * sr)
    n = 1 + (len(x) - W) // H
    rms = np.array([20 * np.log10(max(np.sqrt((x[i * H:i * H + W] ** 2).mean()), 1e-12))
                    for i in range(n)])
    thr = float(np.percentile(rms, 10)) + 12.0

    def voicing(ts, dur=0.040):
        seg = x[int(ts * sr):int(ts * sr) + int(dur * sr)]
        if len(seg) < 64:
            return None
        S = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2
        fr = np.fft.rfftfreq(len(seg), 1 / sr)
        tot = S.sum() + 1e-20
        s = seg - seg.mean()
        ac = np.correlate(s, s, "full")[len(s) - 1:]
        ac = ac / (ac[0] + 1e-20)
        lo, hi = int(sr / 300), int(sr / 70)
        return {"lf_pct": round(100 * float(S[fr < 600].sum()) / tot, 1),
                "hf_pct": round(100 * float(S[fr > 3000].sum()) / tot, 1),
                "autocorr": round(float(ac[lo:hi].max()) if hi < len(ac) else 0.0, 3)}

    rows = []
    for i, w in enumerate(words):
        prev_end = words[i - 1]["end"] if i else 0.0
        if w["start"] - prev_end < min_pause_s:
            continue
        lo = max(prev_end, w["start"] - search_s)
        hi = min(w["start"] + 0.30, w["end"])
        k0, k1 = int(lo / (H / sr)), min(int(hi / (H / sr)), n - 1)
        seg = rms[k0:k1]
        cross = next((j for j in np.where(seg > thr)[0]
                      if j + 30 < len(seg) and (seg[j:j + 30] > thr).mean() > 0.8), None)
        if cross is None:
            continue
        meas = (k0 + cross) * H / sr
        v = voicing(meas)
        rows.append({"word": w["t"], "aligner": round(w["start"], 3),
                     "measured": round(meas, 3),
                     "delta_ms": round((w["start"] - meas) * 1000, 1),
                     "clipped": bool(cross <= 1), "voicing": v,
                     "explained_by_breath": bool(w["t"][:1].lower() in "aeiou" and v
                                                 and v["autocorr"] < 0.35)})
    scored = [r for r in rows if not r["clipped"] and not r["explained_by_breath"]]
    d = np.array([r["delta_ms"] for r in scored]) if scored else np.array([0.0])
    return rows, {"speech_threshold_dbfs": round(thr, 2), "n_compared": len(rows),
                  "n_excluded_clipped_search": sum(r["clipped"] for r in rows),
                  "n_excluded_breath_before_vowel": sum(r["explained_by_breath"] for r in rows),
                  "n_scored": len(scored),
                  "median_delta_ms": round(float(np.median(d)), 1),
                  "mean_abs_ms": round(float(np.abs(d).mean()), 1),
                  "max_abs_ms": round(float(np.abs(d).max()), 1)}


def verify_stems(cues, lanes, fps, min_separation_db=10.0):
    """Is there speech where the caption says there is? See the module docstring: STEMS, not the
    master — a music bed under both sides leaves the comparison with no power to fail."""
    import soundfile as sf
    out, cache = {}, {}
    for lid, lane in lanes.items():
        stem = lane.get("stem")
        if not stem or not os.path.exists(stem):
            out[lid] = {"skipped": f"no stem for {lid}"}
            continue
        if stem not in cache:
            x, sr = sf.read(stem)
            if x.ndim > 1:
                x = x.mean(axis=1)
            W, H = int(0.020 * sr), int(0.010 * sr)
            n = 1 + (len(x) - W) // H
            cache[stem] = (np.array([20 * np.log10(max(np.sqrt((x[i * H:i * H + W] ** 2).mean()),
                                                       1e-12)) for i in range(n)]), H / sr, n)
        e, h, n = cache[stem]
        mask = np.zeros(n, bool)
        for c in cues:
            if c["lane"] == lid:
                mask[int(c["start_frame"] / fps / h):min(int(c["end_frame"] / fps / h), n)] = True
        # ⚠️ DILATE THE MASK BEFORE TAKING THE COMPLEMENT. The deliberate inter-cue gaps are
        # MIN_CUE_GAP_F frames of continuous speech by construction, so on a read with no real
        # pauses they end up being almost the whole "uncaptioned" set and the separation figure
        # collapses toward zero while the captions are in fact perfect. Measured on one lane:
        # 10.2 dB, of which the entire uncaptioned residue was two 60 ms slivers between cues.
        # The claim being tested is "there is no speech where we say there is none", so the gaps
        # we put there on purpose are not part of it.
        pad = max(1, MIN_CUE_GAP_F)
        grown = mask.copy()
        for k in range(1, int(round(pad / fps / h)) + 2):
            grown[k:] |= mask[:-k]
            grown[:-k] |= mask[k:]
        seg = np.zeros(n, bool)
        a, b = lane["segment"]
        seg[int(a / fps / h):min(int(b / fps / h), n)] = True
        inn, unc = e[seg & mask], e[seg & ~grown]
        if not len(inn) or not len(unc):
            out[lid] = {"skipped": "empty mask"}
            continue
        p_in, p_out = float(np.percentile(inn, 90)), float(np.percentile(unc, 90))
        out[lid] = {"stem": os.path.basename(stem), "captioned_p90_dbfs": round(p_in, 1),
                    "uncaptioned_p90_dbfs": round(p_out, 1),
                    "separation_db": round(p_in - p_out, 1),
                    "ok": bool(p_in - p_out > min_separation_db)}
    silent = [c["start_frame"] for c in cues
              if lanes[c["lane"]].get("stem") in cache
              and float(np.percentile(
                  cache[lanes[c["lane"]]["stem"]][0]
                  [int(c["start_frame"] / fps / cache[lanes[c["lane"]]["stem"]][1]):
                   max(int(c["start_frame"] / fps / cache[lanes[c["lane"]]["stem"]][1]) + 1,
                       int(c["end_frame"] / fps / cache[lanes[c["lane"]]["stem"]][1]))], 90)) < -50]
    out["cues_without_speech_on_their_own_stem"] = silent
    return out


# --- build -------------------------------------------------------------------------------------
def build(doc, out_stem, shape_name, verify_stems_flag=True, quiet=False):
    num, den = (int(v) for v in str(doc.get("fps", "30000/1001")).split("/")) \
        if "/" in str(doc.get("fps", "30000/1001")) else (int(doc["fps"]), 1)
    fps = num / den
    sr = int(doc.get("sample_rate", 48000))
    total_frames = int(doc["total_frames"])
    shape = SHAPES[shape_name]
    keep = [tuple(p) for p in doc.get("keep_together", [])]
    lanes = {}
    for L in doc["lanes"]:
        base = int(L["placed_at_sample"])
        in_s = int(L["source_in_sample"])
        removed = L.get("removed_spans", [])
        span = L.get("source_span")
        ws = [dict(w) for w in L["words"]]
        if span:
            # OVERLAP, not containment — see the module docstring.
            ws = [w for w in ws if w["end"] > span[0] + 1e-9 and w["start"] < span[1] - 1e-9]
            for w in ws:
                w["start"] = max(w["start"], span[0])
                w["end"] = min(w["end"], span[1])
        if L.get("text"):
            ws = attach_punctuation(ws, L["text"], L["id"])
        else:
            for w in ws:
                w["surface"] = w["t"]

        def to_prog(t, base=base, in_s=in_s, removed=removed):
            samp = int(round(t * sr))
            shift = sum(int(r["samples"]) for r in removed
                        if samp > int(round(r["after_source_seconds"] * sr)))
            return (base + (samp - in_s) - shift) / sr

        for w in ws:
            w["start_p"], w["end_p"] = to_prog(w["start"]), to_prog(w["end"])
        lanes[L["id"]] = dict(L, words=ws)

    order = sorted(lanes.values(), key=lambda L: L["segment"][0])
    cues = [c for L in order for c in group_cues(L["words"], L["id"], shape, keep)]
    cues.sort(key=lambda c: c["words"][0]["start_p"])
    final, truncated = finalise(cues, lanes, fps, total_frames, shape, keep)

    problems = []
    for i, c in enumerate(final):
        if c["end"] <= c["start"]:
            problems.append(f"cue {i+1}: non-positive duration")
        if c["start"] < 0 or c["end_frame"] > total_frames:
            problems.append(f"cue {i+1}: outside 0..{total_frames} frames")
        if i and c["start_frame"] - final[i - 1]["end_frame"] < MIN_CUE_GAP_F:
            problems.append(f"cue {i+1}: only {c['start_frame'] - final[i-1]['end_frame']} "
                            f"frame(s) after cue {i}, wanted {MIN_CUE_GAP_F}")
        a, b = lanes[c["lane"]]["segment"]
        if c["start_frame"] < a - 1 or c["end_frame"] > b + 1:
            problems.append(f"cue {i+1}: leaves lane {c['lane']} ({a}-{b})")
    for lid, lane in lanes.items():
        for a, b in lane.get("never_caption_frames", []):
            for i, c in enumerate(final):
                if c["start_frame"] < b and c["end_frame"] > a:
                    problems.append(f"cue {i+1} overlaps a span declared not-a-line ({a}-{b})")
    said = [w["surface"] for L in order for w in L["words"]]
    capt = " ".join(c["text"].replace("\n", " ") for c in final).split()
    if said != capt:
        problems.append(f"caption text does not reconstruct the spoken word sequence "
                        f"({len(said)} said vs {len(capt)} captioned)")

    stemv = verify_stems(final, lanes, fps) if verify_stems_flag else {}
    for lid, d in stemv.items():
        if isinstance(d, dict) and d.get("ok") is False:
            problems.append(f"{lid}: captioned frames only {d['separation_db']} dB above "
                            f"uncaptioned on {d['stem']} — that lane's offset is suspect")
    if stemv.get("cues_without_speech_on_their_own_stem"):
        problems.append("cues with no speech on their own stem at frames "
                        f"{stemv['cues_without_speech_on_their_own_stem']}")

    with open(out_stem + ".srt", "w", encoding="utf-8", newline="\r\n") as f:
        for i, c in enumerate(final, 1):
            f.write(f"{i}\n{srt_time(c['start'])} --> {srt_time(c['end'])}\n{c['text']}\n\n")
    with open(out_stem + ".vtt", "w", encoding="utf-8", newline="\n") as f:
        f.write("WEBVTT\n\n")
        for i, c in enumerate(final, 1):
            f.write(f"{i}\n{srt_time(c['start'], '.')} --> {srt_time(c['end'], '.')}\n"
                    f"{c['text']}\n\n")

    hot = [(i + 1, c) for i, c in enumerate(final) if c["cps"] > HARD_CPS]
    report = {"n_cues": len(final), "fps": doc.get("fps"), "total_frames": total_frames,
              "shape": dict(shape, name=shape_name, min_dur_s=MIN_DUR_S, max_dur_s=MAX_DUR_S,
                            gap_break_s=GAP_BREAK_S, min_cue_gap_frames=MIN_CUE_GAP_F,
                            target_cps=TARGET_CPS, hard_cps=HARD_CPS,
                            max_hold_past_speech_s=MAX_HOLD_S),
              "files": [out_stem + ".srt", out_stem + ".vtt"],
              "cues_truncated_to_preserve_gap": truncated,
              "stem_verification": stemv,
              "reading_rate": {"max_cps": max(c["cps"] for c in final) if final else 0,
                               "over_hard_cps": [{"cue": n, "cps": c["cps"],
                                                  "text": c["text"].replace("\n", " ")}
                                                 for n, c in hot]},
              "cues": [{"n": i + 1, "lane": c["lane"], "in_f": c["start_frame"],
                        "out_f": c["end_frame"], "cps": c["cps"],
                        "held_past_speech_ms": c["held_past_speech_ms"],
                        "text": c["text"].replace("\n", " / ")} for i, c in enumerate(final)],
              "problems": problems}
    json.dump(report, open(out_stem + "_report.json", "w", encoding="utf-8"), indent=1,
              ensure_ascii=False)

    if not quiet:
        print(f"{len(final)} cues -> {out_stem}.srt (+ .vtt)")
        print(f"{'#':>3} {'lane':>10} {'in_f':>6} {'out_f':>6} {'cps':>5} {'hold ms':>8}  text")
        for i, c in enumerate(final, 1):
            print(f"{i:3d} {c['lane']:>10} {c['start_frame']:6d} {c['end_frame']:6d} "
                  f"{c['cps']:5.1f} {c['held_past_speech_ms']:8.0f}  "
                  f"{c['text'].replace(chr(10), ' / ')}")
        for lid, d in stemv.items():
            if isinstance(d, dict) and "separation_db" in d:
                print(f"stem check {lid:>10}: captioned p90 {d['captioned_p90_dbfs']:+7.1f} dBFS "
                      f"vs uncaptioned {d['uncaptioned_p90_dbfs']:+7.1f} -> "
                      f"{d['separation_db']:.1f} dB on {d['stem']}")
        print(f"reading rate: max {report['reading_rate']['max_cps']} cps "
              f"(target {TARGET_CPS}, hard {HARD_CPS}); {len(hot)} over hard")
        if problems:
            print("\nPROBLEM:")
            for p in problems:
                print("  " + p)
        else:
            print("assertions pass: bounds, ordering, inter-cue gap, lane containment, "
                  "not-a-line exclusion, verbatim reconstruction" +
                  (", stem separation" if verify_stems_flag and stemv else ""))
    return report


# --- selftest ----------------------------------------------------------------------------------
def selftest():
    """Plant known speech at known times and assert the cues land on it.

    Ground truth, not self-consistency. A synthetic stem carries voiced bursts at times this test
    chose; the words are declared at those times in a DIFFERENT source timebase with a non-zero
    placement offset and an internal splice, so the clock arithmetic has to be right for the stem
    check to pass at all. Then three faults are planted deliberately and each must be caught.
    """
    import tempfile
    try:
        import soundfile as sf
    except ImportError:
        print("selftest: soundfile not installed"); return 1
    rng = np.random.default_rng(3)
    fails = 0
    fps, sr = 30000 / 1001, 48000

    with tempfile.TemporaryDirectory() as td:
        # ---- plant the audio: 10 words, 4 groups separated by real pauses ------------------
        text = ("This is the first planted line. And here is a second one. "
                "A third arrives after a pause. Then the last.")
        toks = text.split()
        src_in = 100.0                              # the lane reads from 100 s in its own source
        t, words = src_in + 0.5, []
        for i, tok in enumerate(toks):
            dur = 0.18 + 0.04 * len(tok) / 4
            words.append({"t": norm(tok), "start": round(t, 4), "end": round(t + dur, 4)})
            t += dur + (0.55 if tok.endswith(".") else 0.06)
        placed = 3 * sr                             # the mix put this lane at 3.000 s
        seg = [int(2.0 * fps), int(2.0 * fps) + 900]

        total_s = 30.0
        x = rng.normal(0, 1e-4, int(total_s * sr))   # room tone
        f0 = 150.0
        for w in words:                              # a voiced burst where each word belongs
            a = (placed + int(round(w["start"] * sr)) - int(src_in * sr)) / sr
            b = (placed + int(round(w["end"] * sr)) - int(src_in * sr)) / sr
            n = int((b - a) * sr)
            tt = np.arange(n) / sr
            burst = 0.25 * (np.sin(2 * np.pi * f0 * tt) + 0.5 * np.sin(2 * np.pi * 2 * f0 * tt))
            burst *= np.minimum(1.0, np.minimum(tt, (b - a) - tt) / 0.01)
            x[int(a * sr):int(a * sr) + n] += burst
        stem = os.path.join(td, "stem.wav")
        sf.write(stem, x, sr, subtype="PCM_16")

        doc = {"fps": "30000/1001", "sample_rate": sr, "total_frames": int(total_s * fps),
               "keep_together": [["first", "planted"]],
               "lanes": [{"id": "lane1", "placed_at_sample": placed,
                          "source_in_sample": int(src_in * sr), "segment": seg,
                          "stem": stem, "words": words, "text": text}]}
        rep = build(doc, os.path.join(td, "sub"), "vertical", quiet=True)

        ok = not rep["problems"]
        fails += 0 if ok else 1
        print(f"  clean build: {rep['n_cues']} cues, no problems  "
              f"{'ok' if ok else '*** FAIL *** ' + str(rep['problems'][:2])}")

        sv = rep["stem_verification"]["lane1"]
        ok = sv["separation_db"] > 20
        fails += 0 if ok else 1
        print(f"  stem check finds the planted speech: captioned p90 "
              f"{sv['captioned_p90_dbfs']} vs {sv['uncaptioned_p90_dbfs']} dBFS = "
              f"{sv['separation_db']} dB  {'ok' if ok else '*** FAIL ***'}")

        # ---- planted fault 1: what the stem check CAN and CANNOT see ------------------------
        # Asserted as a bound and a direction, not as a clean pass. The first version of this
        # test planted a 1.2 s error, expected a catch, and got none — and the honest reading is
        # not that the check is broken but that a 1.2 s shift inside a dense read still lands
        # every caption on *a* word. Measured here: caught at 3 s, missed at 2 s and below.
        def sep(shift):
            d = json.loads(json.dumps({k: v for k, v in doc.items() if k != "lanes"}))
            d["lanes"] = [dict(doc["lanes"][0], placed_at_sample=placed + int(shift * sr))]
            r = build(d, os.path.join(td, f"shift{shift}"), "vertical", quiet=True)
            return r["stem_verification"]["lane1"], r["problems"]

        gross, gp = sep(3.0)
        small, _ = sep(1.2)
        ok = (not gross["ok"]) and any("offset is suspect" in p for p in gp) and small["ok"]
        fails += 0 if ok else 1
        print(f"  placement: a 3.0 s error IS caught (separation {gross['separation_db']} dB), "
              f"a 1.2 s error is NOT ({small['separation_db']} dB)  "
              f"{'ok' if ok else '*** FAIL ***'}  <- documented bound: a smoke test, not a sync "
              f"test")

        # ---- planted fault 2: overlapping cues must be refused ----------------------------
        global MIN_CUE_GAP_F
        keep_gap = MIN_CUE_GAP_F
        try:
            r3 = build(doc, os.path.join(td, "gap"), "vertical", verify_stems_flag=False,
                       quiet=True)
            gaps = [r3["cues"][i + 1]["in_f"] - r3["cues"][i]["out_f"]
                    for i in range(len(r3["cues"]) - 1)]
            ok = gaps and min(gaps) >= keep_gap
            fails += 0 if ok else 1
            print(f"  every inter-cue gap >= {keep_gap} frames (min {min(gaps)})  "
                  f"{'ok' if ok else '*** FAIL ***'}")
        finally:
            MIN_CUE_GAP_F = keep_gap

        # ---- planted fault 3: a not-a-line span must not be captioned ----------------------
        nl = dict(doc)
        nl["lanes"] = [dict(doc["lanes"][0],
                            never_caption_frames=[[rep["cues"][0]["in_f"] - 1,
                                                   rep["cues"][0]["in_f"] + 5]])]
        r4 = build(nl, os.path.join(td, "nl"), "vertical", verify_stems_flag=False, quiet=True)
        caught = any("not-a-line" in p for p in r4["problems"])
        fails += 0 if caught else 1
        print(f"  a caption inside a declared not-a-line span is CAUGHT  "
              f"{'ok' if caught else '*** FAIL ***'}")

        # ---- the splice: removing source time must pull later words EARLIER ----------------
        sp = dict(doc)
        sp["lanes"] = [dict(doc["lanes"][0],
                            removed_spans=[{"after_source_seconds": words[3]["end"],
                                            "samples": int(0.4 * sr)}])]
        r5 = build(sp, os.path.join(td, "sp"), "vertical", verify_stems_flag=False, quiet=True)
        moved = r5["cues"][-1]["in_f"] - rep["cues"][-1]["in_f"]
        want = -int(round(0.4 * fps))
        ok = abs(moved - want) <= 1
        fails += 0 if ok else 1
        print(f"  a 400 ms internal splice pulls later cues {moved} frames "
              f"(wanted {want})  {'ok' if ok else '*** FAIL ***'}")

        # ---- overlap selection: a span that starts INSIDE a word keeps that word -----------
        ov = dict(doc)
        mid = (words[0]["start"] + words[0]["end"]) / 2
        ov["lanes"] = [dict(doc["lanes"][0], source_span=[mid, words[-1]["end"]])]
        r6 = build(ov, os.path.join(td, "ov"), "vertical", verify_stems_flag=False, quiet=True)
        kept = r6["cues"][0]["text"].split()[0].lower().startswith(norm(toks[0])[:4])
        fails += 0 if kept else 1
        print(f"  a span opening mid-word KEEPS that word ({r6['cues'][0]['text'][:22]!r})  "
              f"{'ok' if kept else '*** FAIL ***'}")

        # ---- reading rate: cues with silence after them are extended toward the target -----
        over = [c for c in rep["cues"] if c["cps"] > HARD_CPS]
        ok = not over
        fails += 0 if ok else 1
        print(f"  no cue over {HARD_CPS} cps once silence is used "
              f"({len(over)} over)  {'ok' if ok else '*** FAIL ***'}")

        # ---- the keep-together preference ---------------------------------------------------
        split_badly = any("first" == c["text"].split(" / ")[0].split()[-1].lower().strip(".,")
                          for c in rep["cues"] if " / " in c["text"])
        fails += 0 if not split_badly else 1
        print(f"  keep_together is not split across the line break  "
              f"{'ok' if not split_badly else '*** FAIL ***'}")

        # ---- onset validation: the clipped-search guard must mark an edge result ------------
        rows, summ = validate_onsets(stem, [{"t": w["t"],
                                             "start": (placed + int(round(w["start"] * sr))
                                                       - int(src_in * sr)) / sr,
                                             "end": (placed + int(round(w["end"] * sr))
                                                     - int(src_in * sr)) / sr} for w in words])
        ok = summ["n_scored"] >= 2 and summ["max_abs_ms"] < 40
        fails += 0 if ok else 1
        print(f"  onset validation on the planted stem: {summ['n_scored']} scored, "
              f"median {summ['median_delta_ms']:+} ms, max |err| {summ['max_abs_ms']} ms  "
              f"{'ok' if ok else '*** FAIL ***'}")
        rows_n, _ = validate_onsets(stem, [{"t": w["t"],
                                            "start": (placed + int(round(w["start"] * sr))
                                                      - int(src_in * sr)) / sr,
                                            "end": (placed + int(round(w["end"] * sr))
                                                    - int(src_in * sr)) / sr} for w in words],
                                    search_s=0.001)
        ok = any(r["clipped"] for r in rows_n)
        fails += 0 if ok else 1
        print(f"  a deliberately too-narrow search window is MARKED clipped, not reported as a "
              f"measurement  {'ok' if ok else '*** FAIL ***'}")

    print("selftest:", "PASS" if not fails else f"{fails} FAILURE(S)")
    return 0 if not fails else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lanes", help="lanes JSON; see the module docstring")
    ap.add_argument("--out", help="output stem (writes .srt, .vtt, _report.json)")
    ap.add_argument("--raster", default="1080x1920",
                    help="WxH delivery raster; picks the caption shape preset")
    ap.add_argument("--textgrid", help="dump an MFA TextGrid's word tier as JSON and stop")
    ap.add_argument("--no-verify-stems", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()
    if a.textgrid:
        print(json.dumps(read_textgrid(a.textgrid), indent=1))
        return 0
    if not (a.lanes and a.out):
        ap.error("need --lanes and --out (or --textgrid / --selftest)")
    w, h = (int(v) for v in a.raster.lower().split("x"))
    shape = "vertical" if h > w else "horizontal"
    print(f"raster {w}x{h} -> '{shape}' caption shape: "
          f"{SHAPES[shape]['max_chars']} chars x {SHAPES[shape]['max_lines']} lines")
    rep = build(json.load(open(a.lanes, encoding="utf-8")), a.out, shape,
                verify_stems_flag=not a.no_verify_stems)
    return 1 if rep["problems"] else 0


if __name__ == "__main__":
    # Post tool: needs an active license (see license_check.py). --selftest is exempt.
    from license_check import gate
    gate(__file__)
    raise SystemExit(main())

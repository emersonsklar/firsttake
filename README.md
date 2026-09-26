<div align="center">

<img src="docs/hero.png" alt="film-shoot — from an empty room to a delivered master" width="100%">

**A Claude Code skill for the shoot you cannot ask to do again.**

Cameras that share no clock. Forty minutes with someone who will not sit down twice. Nobody on set
whose only job is to watch the levels. At the other end: a master, a reviewer's transcript, social
cuts and pull clips — assembled by script, with every number in it measured.

<sub>
<code>python3</code> · <code>ffmpeg</code> · <code>numpy</code> &nbsp;•&nbsp;
Apache-2.0 &nbsp;•&nbsp; the measurement tools work with or without Claude
</sub>

</div>

---

<div align="center">
<img src="docs/pipeline.png" alt="the workflow: prepare, capture, finish" width="100%">
</div>

---

## Act 1 — Prepare

**Profile the kit, scout the location, design the setup, build the run sheet.** The output is a
plan made of literal numbers — *"lantern centre 75, 46 inches out, 30° down, 100%"* — because a
principle has to be re-derived on set and a number does not. Lighting, camera and audio placement,
then the checklists, call order and day sheets that fall out of the same profile. Rooms can be
scouted from photographs before anyone travels.

The reason it starts with profiling: **this is not a gear database.** Every hardware number belongs
to a specific body, card, mic or drive, and almost none survive a kit change. A confident unmeasured
number is worse than no number, because the whole workflow depends on trusting it in a room where
you cannot check it.

📖 `references/onboarding.md` · `references/location-scout.md` · `references/templates/`

---

## Act 2 — Capture

**Rig to the plan, record, check the footage, secure the media.** The plan from Act 1 arrives as
literal numbers so nothing has to be re-derived on set, and the checklists are ordered so the
things that cannot be fixed later happen first.

The reason it is built that way: **nothing may require attention after "rolling."** If you are also
the person on camera you cannot catch a focus hunt or a clipped level, and you cannot fix one
without stopping someone mid-answer. Stakes are asymmetric — forty minutes with a busy executive is
not a reshoot you get to request — so redundancy is weighted heavily and cleverness at zero.

**Checking the footage means checking the files**, not the monitor: log-aware exposure, focus
stability, frame timing and dropped frames, 8-bit hiding in a 10-bit container, per-channel audio.
Cross-camera frame-rate mismatch is the headline check, because it is the one that ruins an edit
silently.

### Securing the media — a copy is not a backup until it has been read back

<div align="center">
<img src="docs/offload.png" alt="read the card once, hash while reading, then re-read every destination from disk" width="94%">
</div>

`offload.py` reads the card **once**, hashes as it reads, then **re-reads every destination from
disk** and compares. MHL 1.1, optional generation-chained ASC MHL, a JSON manifest, and a
self-contained HTML report you can hand to anyone.

The retention rule is deliberately strict, because the failure it guards against is silent and
final: **cards stay until the deliverable is live *and* the archive is verified.** Not until the
copy finished. Not until someone said thanks.

📖 `references/qc-interpretation.md` · `references/offload-protocol.md`

---

## Act 3 — Finish

The largest part of the skill, and the part that keeps teaching. Six stages take a pile of cards
that share no clock to a signed-off master:

| Stage | What happens |
|---|---|
| **Conform** | Place every recording on one timeline with no shared timecode, measure the drift a clap cannot see, and attribute speech per word from a split-mode receiver. |
| **Edit** | Assemble the cut — splice language, fade *shape* as a first-class parameter, speed ramps with the motion model matched to the motion — then build the multicam timeline natively in the NLE through its scripting API and read it back to verify it. |
| **Grade** | Match the bodies with **one corrector each**, then grade to a reference episode without importing that episode's room. |
| **Mix** | Per-speaker restoration and static levelling, cross-talk ducking driven by the word labels, mastered to target — then checked **per phrase**, which is where it actually goes wrong. |
| **Caption and document** | Subtitles with text from the recogniser and **time from a forced aligner**, plus the reviewer's transcript and review document in programme time. |
| **Deliver** | Masters, vertical social cuts with burned-in captions, PR pull clips — one encode per deliverable, and the sidecars kept in step with the picture. |

Reproducing someone else's graphics template runs through the whole of it: measure the reference
rather than guess it — **size from ink height, family from width, weight from stroke thickness.**

📖 `references/post-production.md` · `references/resolve-api.md` · `references/grade-match.md`

---

### Exhibit A — the clap that doesn't save you

<div align="center">
<img src="docs/drift.gif" alt="two clocks drifting apart by one frame every 33 seconds" width="88%">
</div>

Clap at the top of the take. You should still do it — it is free and survives everything. But a clap
gives you **one point in time**, and the expensive failure is not offset, it is **drift**: 29.97 and
30.000 are both sold as "30p" and pull apart about a frame every 33 seconds. Over forty minutes two
angles end up roughly **70 frames** apart and no single sync point fixes it. Add a third body and it
is three pairs of clocks, not one.

`sync_cameras.py` probes in up to ten places, so **the spread between probes is the drift
measurement**:

| reading | means |
|---|---|
| spread under ~50 ms across a long span | one constant offset; a single sync point holds |
| spread growing with probe position | genuine drift — check both cameras' frame rates |
| drift near ±1000 ppm | almost certainly 29.97 against 30.000 |
| no lock | the recordings share no audible content, or one is silent |

It correlates **onset envelopes**, not waveforms — so it works between completely different
microphones, because two mics in one room disagree about spectrum and agree about *when things
start*.

---

### Exhibit B — a vertical cut is a budget, not a crop

<div align="center">
<img src="docs/reframe.gif" alt="a static 9:16 window while the subject sways, with the excursion budget measured" width="88%">
</div>

A seated subject's head swayed **588 px**; the union of head positions spanned **1051 px** of a
**1215 px** window. No static window holds that with margin everywhere.

The answer is not to abandon a centred frame. Set an **excursion budget** in pixels *and* seconds,
then take the crop nearest the compositional ideal that stays inside it — here 48 px for up to
1.25 s, while the subject leans forward and looks *down*, so no gaze room is wanted at that
instant. And 1215 of 3840 px at full height is a **0.889 downscale**, so sharpness never enters the
argument.

⚠️ Probe density has to scale with the span. A fixed probe *count* reused on a longer clip missed
the subject's furthest lean entirely — the sparse answer would have clipped a head by ~68 px.

---

### Exhibit C — on target, and still wrong

<div align="center">
<img src="docs/loudness.png" alt="per-turn levelling is tight while individual phrases sit 4 LU over" width="94%">
</div>

Integrated loudness, true peak, LRA and per-turn levelling all correct — turn-to-turn spread
**1.4 LU** — and one phrase still sat **+2.7 LU** over its speaker's median word. Twelve phrases sat
at **+4 LU or more**, every one of them the same speaker, lifting on question intonation. Nothing in
the chain was looking at a phrase.

And the trap underneath: **a pre-limiter trim on a phrase already pinned at the ceiling barely
changes its loudness.** Thirteen trims of 1.75–4.00 dB delivered **0.00–2.27 dB**, because trimming
ahead of a limiter mostly stops the limiter reducing. Keep it for the crest it buys, then apply the
measured remainder *after* the limiter, where gain is exact.

---

## The through-line

**The failures are quiet.** A sync 40 ms out looks fine on a laptop and wrong on a big screen. A
timeline at the wrong playback rate shows a contented green number while dropping a fifth of the
audio. A guard can report success and do nothing. A check can be written so it cannot fail.

So every claim carries `CONFIRMED` / `LIKELY` / `UNVERIFIED` and a note on how it was established,
and the references read like a post-mortem in places — because those are the expensive ones:

> a still that ignored its own length and silently moved the clip after it · numbered PNGs that
> imported as one image sequence, so 11 of 12 captions never existed · a loudness meter measured in
> the wrong channel layout, 3 dB out · a cached asset keyed on a filename instead of its content ·
> a derived cache of absolute frame numbers that rendered 33 frames long while the build reported
> no problems

---

## Install

```bash
git clone <THIS_REPO> ~/.claude/skills/film-shoot
```

Claude Code picks it up from `~/.claude/skills/`. For a single project instead, clone to
`<project>/.claude/skills/film-shoot`.

```bash
pip3 install numpy xxhash ascmhl pymupdf     # xxhash/ascmhl/pymupdf optional
```

Requirements: `python3`, `ffmpeg`/`ffprobe`, `numpy`. Optional: `xxhash` (faster hashing),
`ascmhl` (generation-chained manifests), `pymupdf` (PDF page verification).

---

## The tools

Useful on their own, with or without Claude.

| | |
|---|---|
| **`shootcheck.py`** | Technical QC for footage and audio. Measured numbers with PASS/WARN/FAIL against explicit thresholds and a stated fix for each. |
| **`offload.py`** | Verified card offload to any number of destinations, with MHL/ASC MHL, JSON manifest and an HTML report. |
| **`sync_cameras.py`** | Places recordings on a reference timeline with no shared timecode, and measures drift. |
| **`diarize_by_channel.py`** | Per-word speaker attribution from a split-mode receiver. |

Plus `build_subtitles.py`, `speed_ramp.py`, `make_proxies.py`, `measure_white_balance.py`,
`conform_graphic.py`, `make_log_view_lut.py`, `fit_clip_trims.py`, `build_day_sheets.py` and a
fixture-based self-test suite.

---

## What it is not

**Not a crew replacement.** It is built for the case where you are the operator *and* the person on
camera, or the only person in the room, and the shoot is not something you can ask to do again.

**Not a style guide.** Where it reproduces a look, it does so by measuring a reference and stating
the numbers, not by applying a rule from a book.

<div align="center">
<sub>Every image here is generated by <code>docs/make_assets.py</code> — plots of the measurements
themselves. No stock art, no screenshots, no footage.</sub>
</div>

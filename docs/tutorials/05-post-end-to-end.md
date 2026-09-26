# 5 — Post, end to end

A verified offload is where the shoot stops being at risk. It is not where it becomes useful.

This is the run order that produced a 14:50 two-person interview programme, its captions, its review
documents and its published encode. Everything up to "assemble" is NLE-neutral: it is `python3` +
`ffmpeg` + `numpy`, and it works whichever application you own.

```
sync ─► transcribe ─► attribute ─► force-align ─► decide the cut ─► colour
     ─► picture plan ─► programme structure ─► audio master ─► assemble ─► verify
     ─► render and measure ─► subtitles ─► review documents ─► deliver
```

**Everything here is measured rather than eyeballed, for the same reason as the rest of the skill: the
failures are quiet.** A sync 40 ms out looks fine on a laptop and wrong on a big screen. A transcript
that attributes one sentence to the wrong person reads as fine until the person quoted notices.

---

## 1. Sync angles that share no clock

```bash
python3 scripts/sync_cameras.py A_CAM_ref.mov B_CAM_0001.mp4 B_CAM_0002.mp4
```

A clap is free and survives everything, and you should still do it — but it gives you **one** point in
time. The expensive failure is not offset, it is **drift**.

The tool correlates **onset envelopes** (the rectified first difference of short-term loudness) rather
than waveforms, which is why it locks a lavalier mix against a camera's onboard mics: two mics in one
room disagree about spectrum and agree about *when things start*. It probes at up to ten places, so
**the spread between probes IS the drift measurement.**

| Reading | Means |
|---|---|
| spread under ~50 ms across a long span | one constant offset; a single sync point holds |
| spread growing with probe position | genuine drift; check both cameras' frame rates |
| drift near ±1000 ppm | almost certainly 29.97 against 30.000 |
| no lock | the recordings share no audible content, or one is silent |

✅ Measured on a real two-camera interview: a 31-minute A-camera and two consecutive B-camera files
locked at **+15.950 s** and **+1677.730 s**, 9/9 and 2/2 probes, spread **0.000 s**, drift **0 ppm**.
Zero drift is worth confirming rather than assuming — it is the thing that would have quietly ruined
the edit.

Two details that saved time:

- **A camera that splits files mid-take may lose nothing at the boundary.** On that shoot the second
  file began exactly where the first ended at 10 ms resolution; elsewhere ~1.4 s was measured lost on
  the same camera family. **Measure the files you have.** Then **butt the successor at its
  predecessor's MEASURED end**, not at its own stated offset — on one job those disagreed by 2 frames
  and honouring the stated offset left a hole.
- **Check a derived file is frame-aligned with the original before reusing timecodes.** A LUT-applied
  proxy is usually a 1:1 re-encode, but "usually" is not a measurement. One run answers it: 0.000 s
  means every timestamp transfers unchanged. On one job all seven probes read 0.000 s at r = 0.999–1.000
  against the originals.

⚠️ **Count frames; never compute length as `duration × fps`.** One manifest declared 1661.781333 s,
which rounds to 49804 frames, and the file holds **49802**. That made a layer read two frames past the
end of a camera, in two independent builds, and no probe caught it — probes sample positions and this
only shows at a boundary.

---

## 2. Transcribe, then attribute by measurement

Use a recogniser to learn **what** was said. It emits word timestamps; those are for reading,
searching and attributing, and **not for cutting** (§4).

```bash
python3 scripts/diarize_by_channel.py interview.mov --words words.json \
    --channels 3,4 --names Host,Guest --srt out.srt --turns turns.json
```

**Check the channels are independent first.** Two channels carrying one summed mix look exactly like a
split until you correlate them. Pearson **r near 0 is a real split; near 1 is one mix copied twice**,
and the tool refuses rather than guessing. A real split measured **|r| = 0.033**.

**Attribute per word, not per segment.** Speech-to-text segments straddle speaker changes: on a real
interview an answer opened with *"particular wearables?"*, which was the last two words of the
question before it. **Smooth only the uncertain words** — a blanket rolling median also rewrites words
that were measured confidently, and when a turn is shorter than the filter it drags the whole turn to
the wrong speaker. The first version did exactly that and scored **79%** on a fixture with short
turns; keeping any word with more than ~3 dB of margin as measured took it to **100%**.

**Read the isolation figure — it decides what post can do.**

| Isolation | What is possible |
|---|---|
| above ~12 dB | a plain noise gate separates the two cleanly |
| below ~12 dB | a gate chatters; you must duck by label instead |

Real measurement: guest **15.3 dB**, host **5.5 dB**. The host's channel could not be gated at all.

⚠️ **Both lavs hear both people, so attribution must be RELATIVE.** Each channel carries the other
speaker about 10 dB down — measured **+8.5 to +10 dB** in favour of whoever is talking. A "who is
speaking" test built on the absolute level of one channel produced a burst list for the host that was
a near-copy of the guest's, because it was crosstalk, and turned the host's −41 dB breath into "clean
silence ends here". **Take the difference between channels, always.**

---

## 3. Force-align for times

A **forced aligner** (Montreal Forced Aligner and similar) is given the words and only has to place
them, so it returns word *and phone* boundaries in the source timebase. Measured on one interview,
its mean word-boundary error was **23 ms** against a recogniser's **328 ms** — and across 4603 cleanly
paired words the recogniser sat **+107 ms later** on average and was more than one frame out on
**68%** of onsets. It is the right instrument and worth the install.

It lies differently, and more dangerously, because its output looks authoritative:

⛔ **An aligner can only place tokens that are present in the transcript.** Anything said that the
transcript lacks — a filler, a false start — has nowhere to go, so the aligner **stretches a
neighbouring word over it and shifts every label after it.** A 345 ms "uh" that the transcript lacked
became a **370 ms label for the word "have"**, and the whole following phrase sat a word off. Four
edits were made on those labels; three overrode the operator's ear, and the operator was right every
time. **A word label that is implausibly long is a report that the LABEL is wrong, not that the word
is long.**

⛔ **Align against the audio the deliverable carries.** This is not limited to the speaker's own
unlisted speech — **any** sound the transcript does not account for gets given a real word. On one job
an off-camera "OK" from behind the camera, bleed on the subject's lavalier and already **muted in the
delivered mix**, was still in the alignment corpus, and the aligner spent the subject's **first word
on it**: 1.4 s early, a 1.16 s hole after it, four words dragged late. **Mute whatever the mix mutes
before you align.** One line of work; a whole class of failure removed.

⛔ **A brand name is the one word you must not take from a recogniser.** Product names are usually out
of vocabulary in a pronunciation dictionary, so an aligner models them as spoken-noise and their
boundaries go slack — loosening exactly the words a client video turns on. Add dictionary entries. And
a recogniser will confidently **reorder** an unfamiliar lockup. To settle one:

1. **Two independent models.** If they disagree, neither is evidence.
2. **Prime the model with the name you expect.** If a corrective prompt does **not** flip the output,
   the acoustics are carrying it rather than the model's prior. If it *does* flip, all you learned is
   that the model is suggestible. This is the step people skip, and it is what makes the other two
   interpretable.
3. **Read the phones, and read them as an ORDER.** A low-HF stop release (0.1% above 3 kHz, a bilabial)
   followed by a nasal murmur (LF 70–95%) came *before* a high-HF burst (79.6% above 3 kHz, a coronal
   cluster). One word order predicts that sequence and the other predicts its reverse, so the sequence
   decides it without identifying a single word. That beat both models and the operator's expectation.

The subject had said it backwards. **Caption it verbatim and escalate the discrepancy in writing** —
whether a client-facing subtitle silently corrects a misspoken product name is the client's call, never
the build's, and nobody watching the cut will notice.

Two more limits: **alignment is not transcription** (do not use an aligner to find out what was said),
and **chunk only in the middle of a pause** — split at the largest internal gap, require ≥1.0 s, cap
padding at `gap/2 − guard`. Observed recogniser error reaches **774 ms**, so a 0.6 s pause does not have
the room, and a 50 s chunk is a lesser evil than a boundary dropped next to a word.

**Keep the division of labour: aligner for TIMES, measured energy for CUTS.**

---

## 4. Decide the cut

### Honour the editor's own trim points

When a transcript has been hand-edited, **the timestamps in that document are the decisions.**
Deriving cuts from your own turn boundaries instead will quietly reinstate material the editor removed
— in one case pulling **18 seconds of between-takes chatter** back into the cut because the automatic
turn ran past where the editor had ended it.

### Check for orphaned reactions after every removal

When a question and answer are cut, the *next* line often still refers to them. One kept line opened
*"Yeah, I guess it's giving the customer the comfort and control…"* — a direct response to an answer
that no longer existed. Grep the first sentence of every turn following a cut for back-references
("Yeah", "that", "absolutely", "it") and check each one.

### Find the real boundary by energy, on the speaker's own channel

Recogniser word timings are systematically wrong in a way that matters: the recogniser swallows the
breath before a word into the word, so an in-point lands early and an out-point lands late. Six
failures measured on one interview — a teaser in-point **0.81 s late**, its out-point **0.37 s early**,
a sentence whose last word the recogniser ended at 76.200 actually releasing at **76.35**, one
question's opening word placed **0.88 s early** inside the false start before it.

So validate every in and out against an **energy-onset check**: the first point where short-term energy
rises ~12 dB above the preceding floor, at 10 ms resolution, on that speaker's own channel. That needs
three gates or it confidently returns the wrong event:

1. 🔴 **Require voicing AFTER the onset, not at it** — ≥40% of the following 150 ms voiced. On one clip
   the "nearest ≥12 dB onset" came back **23 dB over floor, stable across every threshold tried, and
   agreeing with the recogniser to 0.4 ms.** It was a **20 ms lip click** followed by an inhale; the
   word began **380 ms later**, and the recogniser had locked onto the same click. **Two methods
   agreeing is only evidence when they cannot fail the same way.** A click is HF-dominant:
   first-difference energy fraction **0.185** against ≤0.01 for a vowel.
2. **Require the onset be the right speaker.** With only a voicing gate the detector jumped to the
   *other* speaker's question bleeding onto this mic — voiced 86%, f0 134 Hz, real speech, wrong
   person. **Pitch does not discriminate** (ranges overlap); relative level between the two lavs does.
3. **Separate speech from breath by margin over floor.** 6 dB over floor means "not silence"; a word
   sits **18–22 dB** over that speaker's own floor while breaths, lip noise and chair creak sit at
   **4–7 dB**. Detecting at 6 dB turned a breath 40 ms after the last word into "the next speaker
   starts here" and cut the tail off every clip.

⚠️ **And a level test cuts off a word-final fricative.** "users." ends at 819.79, but its final /z/ runs
819.68–819.78 at **−50 dB** with an HF fraction of 0.18 while the vowel before it sits at **−32 dB**. An
out-point finder that tracks level declares the word finished at the vowel. One short shipped with the
final /z/ of a word clipped by **120 ms** for exactly this reason. Extend through a trailing
HF-dominant segment, bounded (~0.3 s) — and **do not stop at the first quiet window**, because a
fricative dips at its onset before the turbulence builds (one read **−62 dB** for one 20 ms window then
−48 dB for the next five). Require ~60 ms of sustained quiet before concluding the word is over.

⚠️ **A detector's cap is not a measurement.** An out-point finder returned exactly `+0.050 s` for two
unrelated clips — its search cap. **When two independent inputs give byte-identical answers, suspect the
code before believing the result.**

### 🔴 Verify a dialogue cut by TRANSCRIBING what it produces

Removing a few words from a sentence looks like a timing problem and is really a *which-word-is-this*
problem. Three instruments disagreed on one 1.5 s stretch:

| | first word's onset | second word's onset |
|---|---|---|
| recogniser | 1599.840 | 1601.280 |
| forced aligner | 1600.420 | **1601.090** |
| **measured / verified** | **1600.42** | **1601.39** |

The first attempt trusted an energy envelope; a recogniser run over the delivered master showed a whole
word had survived. The second trusted the aligner's 1601.090 and would have sliced **210 ms** off the
front of the next word. **What settled it was running ASR over the delivered audio from each candidate
boundary** — from the chosen one it reads clean, with nothing before it. That answers the only question
that matters: what does a listener hear. Do it before rendering; each wrong attempt cost a full audio
rebuild.

Then **pin the corrected boundaries in a small MEASURED table applied *after* any alignment pass**, so
a measurement outranks both the recogniser and the aligner. Write the evidence down next to the
correction — for one word, "the /b/ closure runs 1585.145–1585.200 and the vowel begins 1585.200, with
the closing consonant carrying 66% of its energy above 2.5 kHz in its last third". That note is why the
next person does not re-litigate it, and it is the difference between a correction and a fudge factor.

### 🔴 A cut across a source discontinuity MUST change the visible angle

This defect **shipped four times on one programme, and every other check passed the whole time.**

When an edit splices ranges that sit far apart in the recording — reordered answers, spliced retakes —
the source position leaps at each seam. If the same angle is on screen either side, the subject visibly
jumps: posture, hands and head position all differ. One programme had seams where the source moved
**25 minutes** and the same tight single ran straight across them.

The fix is grammatical: **cut angle at the seam.** A dissolve is out of grammar for this format, and
two wide crops 2% apart in zoom do not read as a change.

**Make it a check, because eyes miss it.** Walk the visible picture frame by frame, find every point
where the visible source frame does not advance by exactly one, and assert the visible (lane, source,
framing) changed there. On that programme the check found **33 seams and 4 uncovered**, reported by
frame with the size of the leap. Negative-test it by restoring the old shot data.

Two practical notes:

- **The cheapest fix is usually already in the plan.** Three of the four bad seams sat **2.3–2.9 s**
  before the tight shot was *going* to hand to the wide anyway. Pulling that handover onto the seam
  cost nothing and changed no grammar.
- **Where no other angle is scheduled, a short reaction shot is the idiomatic cover** — the shortest
  existing island in that programme was **3.50 s**, and it existed for exactly this. Check the reference
  episodes: they use these sparingly (1.5–3% of dialogue), so one is in keeping and a run of them is not.

⚠️ **Snap boundaries onto the seam; do not arithmetic-shift them.** Removing 33 frames moves every later
boundary by 1.101 s, and doing that by subtraction left one boundary **3 ms** off its seam — a one-frame
same-angle join, which is the exact defect the rule prevents.

### Frames are the grid; samples are derived from them

At 29.97 one frame is **1601.6 samples** at 48 kHz — not an integer, so the two grids never line up.
Decide every boundary in **frames**, then derive the sample offset from the cumulative frame count.
Rounding each piece independently accumulates drift between picture and sound across a long programme.

---

## 5. Colour: identify, match, then trim per clip

`references/grade-match.md` is the full method. Three stages.

**(a) Find out what you actually recorded.** Container metadata is not evidence — one camera's files
were tagged `bt709` and contained log. Three cheap checks:

| Check | What it tells you |
|---|---|
| where 10-bit Y floors | S-Log3 defines black at code **95**; Rec.709 floors at **64**. A file floored at 95 is log whatever the tag says |
| codes per stop on a neutral ladder | measured **76.2** against S-Log3's published 76.66 — an identification, not an impression |
| fit a candidate vendor LUT to the camera's own review render | the correct LUT fits at **0.007 RMS**; no LUT at all fits at 0.079 |

🔴 **When those are confounded, the thing that works is a CONFIRMED clip from the SAME camera.** On a
walk-around roll with no chart and no neutral ladder, the floor test was swamped by codec undershoot
(a known-log clip read min 53 against a nominal 95). A control clip from the same body, format confirmed
three ways, sat in the *middle* of the distribution of the eleven clips in question and settled it in one
pass. ⛔ **Never use one log format as the yardstick for another** — S-Log3 is a far flatter curve than
D-Log M, so D-Log M correctly looks more contrasty *while still being log*. That reasoning produced a
confident, documented, wrong retraction that reached a deliverable's spec and nearly re-graded twelve
clips. And ⚠️ **run the transform on the control before calling it harmful**: "the LUT crushes p1 from
26.7 to 7.7" sounds damning until the same LUT takes the confirmed clip 20.7 → 4.0. That is simply what
the LUT does.

**(b) Match the two bodies: vendor LUT per camera, then one static ASC CDL per camera on top.** Prefer a
CDL over a corrected LUT — four readable numbers, human-adjustable, surviving a round trip. **A single
static CDL is valid for a whole take**, because each camera's skin luma wanders as the subject moves but
the *difference between the cameras* does not. Measured across 16 matched instants, skin agreement went
from **ΔRGB 22.5/255 RMS to 6.7/255**.

Note what is *not* claimed: `power = 1.0` was left alone deliberately, because the chart said one sign
and the face's own contrast said the other. Both instruments were confounded, so the honest move was to
record the disagreement and settle it by eye on a real cut.

**(c) One corrector cannot neutralise more than one white balance.**

```bash
python3 scripts/measure_white_balance.py CLIP1.mov CLIP2.mov ... --json > cast.json
python3 scripts/fit_clip_trims.py --measurements cast.json --lut vendor.cube --out-dir out/cubes
```

Twelve b-roll clips from one camera through one interview-fitted CDL measured, as each clip's own
near-neutral cast in dB relative to green:

```
interiors   R +0.55 .. +1.31   B -1.40 .. -2.28     warm / orange
exterior    R -0.79            B +0.36              cool / blue
the interview setup                                 neutral, as fitted
```

Those are **three lighting conditions, not three grades going wrong.** Per-clip trims took the 3.6 dB
spread to ≤**0.36 dB**.

- ⚠️ **Estimate the neutral point from the least-saturated MID-TONES, not grey-world.** Averaging the
  frame assumes the scene averages to grey, which is false in a way that actively misleads — one clip is
  a deliberately multicoloured wall, another mostly blue sky, and grey-world would correct the sky out of
  the sky. Keep the 25th–85th luma percentile, then the least-saturated 20% of that. Report the mask's
  pixel count and mean saturation so a collapsed mask is visible.
- **Correct in the space you measured, which means the trim goes in the LUT.** The cast is measured
  *after* the input transform, but an NLE runs a node's corrector *before* that node's LUT, on log. So
  compose `clip_cube(x) = trim(input_LUT(x))`. One shot, no iteration, and both render paths load the
  identical cube so they cannot disagree.
- **Match the black point too. "Washed out" is a lifted black floor.** Those clips ran p1 = 0.4–13.5 of
  255; landing them all at ~4 is what made them look like one piece. Divide by `(1 − black)` so pulling
  the blacks down does not darken the image.
- **Do not match exposure.** Scene brightness legitimately differs; matching mid-grey across a dark
  display and a bright corridor lifts the dark ones into mush.
- ⚠️ **Verify stability PER CHANNEL before fitting anything static.** A first pass pooled the channel
  spread with the temporal spread and reported "the cast moves across the shot" on 8 of 12 clips. Per
  channel the worst drift was **0.63 dB** and most were under 0.1 — entirely static. **A spread that
  mixes two axes is not a spread.**

---

## 6. Picture plan and programme structure

Decide **what is on screen at every frame with no NLE involved** — a per-frame map, then contiguous
runs. That stage is the only part of the assembly that can be checked without the application running,
so keep it that way and self-test it. On one job it was diffed clip-for-clip against an independently
frame-verified build — **both lanes identical, 27 clips** — before anything was written to the NLE.

Then the programme structure: the segments and their frame boundaries (teaser, title card, body, end
card), which is the one place the running order lives.

Two rules that came out of getting them wrong:

- **An explicit duration beats a derived one once the premise changes.** A title card's length was
  derived from the music. That was right while the music was a short jingle that had to fit inside the
  card; once the music became an 84 s song starting before the card and continuing past it, the two
  became independent and the card wanted an explicit frame count. **Reverse the rule visibly, keeping
  the original text, rather than editing it silently.**
- **A pre-rendered substitute needs its OWN length checked.** A shot was repointed at a 135-frame
  retime of an 1107-frame source, and two separate length checks compared the retime against the
  *original's* frame count and reported "one of these numbers is wrong and every cut depends on it".
  Both numbers were right; the comparison was a category error. **Probe the file the pipeline will
  open**, and do not skip the check for the substituted file.

### Synthetic camera moves, if the coverage forces one

⚠️ This is a coverage lesson first (tutorial 2). If you do need the push:

- **Use a filter that samples at subpixel positions.** `zoompan` rounds its crop rectangle to whole
  pixels; at a slow rate the edge holds for several frames then snaps a full pixel about five times a
  second, and it reads exactly as handheld shake. Measured on a frozen frame, where any frame-to-frame
  difference is pure geometry error: `zoompan` jitter sd **0.586**, supersampling it 4× got to **0.161**,
  and `perspective` with `interpolation=linear` reached **0.006** — 92× better than `zoompan` and far
  cheaper than supersampling.
- **Push at a constant RATE, not a constant amount.** A flat 10% per shot makes a 9-second shot race and
  a 50-second shot crawl. About **0.3% per second, capped near 12%**, makes every shot feel the same.
- **Enforce a minimum shot length** — around **2.5 s**, absorbing anything shorter into its neighbour —
  and place cuts ~**0.15 s before** the new voice starts so the angle does not arrive late.

### Speed effects

```bash
python3 scripts/speed_ramp.py --src shot.mov --frames 135 --speed 4 --out fast.mov
```

The order is not negotiable: **stabilise at 1× → speed up + motion blur → grade → crop/scale.**
Stabilising after the speed-up cannot work, because the blur is built by averaging N source frames so
the wobble is baked into a smear with no sharp feature left to track.

🔴 **Match the motion model to the motion.** `vidstab` fails structurally on a centred forward dolly:
libvidstab models translation and rotation with **no zoom term**, the radially expanding frame swamps its
global fit, and its translation estimate comes out attenuated. It removed 13–15% of the amplitude, **left
the 1.83 Hz walking fundamental exactly where it was**, and cost 5–8 points of high-frequency detail plus
a forced 8-bit round trip. The NLE's own **similarity** stabiliser fits scale and moved the dominant
frequency to 10.95 Hz — the bounce *gone* rather than smaller. ⚠️ A 4-corner/perspective mode is the
wrong reach on a one-point corridor: it removed *less* bob and increased shear 12%. And **a straightness
test cannot detect that** — a homography maps straight lines to straight lines, so collinearity is a
projective invariant and the test is structurally blind.

**Render the intermediate ungraded and full-raster** so the normal per-clip chain applies downstream
exactly once, and **write down which source frame the intermediate's frame 0 is** — prove it by PSNR
against the raw reference at offsets 0/1/2/5 and confirm the peak is at 0 and falls off monotonically.

---

## 7. The audio master

Target **−16 LUFS integrated, −1.5 dBTP** for online spoken word (−14 for a punchy podcast, −19 to −23
for broadcast, **−14** for social shorts).

⚠️ **If previous episodes exist, measure them before assuming the house target.** Three reference
episodes of one series measured **−21 to −22.4 LUFS** — five dB quieter, and quiet enough that a viewer
reaches for the volume. Measure, report, let the operator decide; do not silently match a back catalogue
and do not silently ignore it.

The chain, per speaker, then mix, then master:

```
highpass=f=80, afftdn=nr=12:nf=-25:tn=1, deesser=i=0.1
→ per-turn static trim → one static gain per speaker
→ mix → alimiter=limit=-1.5dB:level=disabled:latency=true
```

🔴 **There is no `loudnorm` and no `dynaudnorm` in that chain, and that is the point.** Both were
recommended by an earlier version of this method. On quiet lavalier material they do audible damage, and
the operator hears it as clipping at peaks that never approach full scale:

- **`loudnorm` silently falls back to *dynamic* mode** when it cannot reach the target linearly inside
  the true-peak ceiling — exactly the case on quiet source (RMS ≈ −40 dBFS, crest 21.6 dB). It then rides
  the programme's gain: **18.7 dB of movement inside a single six-second window.** Long-term crest
  collapsed 20.5 → 12.7 dB while per-window crest was unchanged, which is how you tell gain riding from
  limiting.
- **`dynaudnorm` does the same thing one stage earlier** — 18 dB over a minute and **4–6 dB inside one
  spoken phrase.**

⚠️ **`alimiter`'s `level` option defaults to `true`.** Written as the bare `alimiter=limit=-1.0dB`, the
final limiter does not protect the master — it *auto-levels it back up* until the peak reaches the limit,
throwing the loudness target away. Measured: **−15.4 LUFS / −0.4 dBTP instead of −16 / −1.5.** Always
`level=disabled`, and `latency=true` so the look-ahead does not time-shift the master against the
picture.

So **set the level statically** and let a true-peak limiter catch only overs. Reach the target by
iterating the static gain against a *measured render*, not by asking a filter to hit it for you.

- **Per speaker only if you actually have per-speaker channels.** If the recording is one summed mix,
  split by **time** using the turn boundaries and measure each speaker's segments separately. Do not
  trust `aselect` inside a single `loudnorm` pass — it silently measures the whole stream and returns
  identical numbers for every speaker.
- **A per-turn trim is safe; a per-phrase one is not.** Level each speaking turn with a constant and
  change it only inside measured silence between turns. Nothing then moves while anyone is speaking.
- **Check the splices, not just the average.** Measure integrated loudness a few seconds either side of
  every cut. Under ~1 dB is ideal; 2–3 dB is usually genuine speaker-to-speaker difference.
- **Do not chase a crest figure below ~14.5 dB at these targets** — that is the arithmetic ceiling once a
  window touches the ceiling.

Delivered on one 15-minute programme: turn-to-turn spread **1.4 LU p5–p95 for the host and 0.8 LU for the
guest**, LRA from **18–22 on the raw lavalier channels to 4.1**, with no compression inside any phrase at
all.

### Splices: a butt join clicks, 20 ms of crossfade does not

Joining two pieces of dialogue at a sample boundary steps the waveform, and that step is audible even
when both sides are quiet. Measured across one programme, the worst click was **−26.5 dBFS** — plainly
audible. A **20 ms raised-cosine crossfade** at every join took the worst to **−44.1 dBFS**, below the
≈**−45 dBFS** audibility floor for this material.

- **At a dialogue↔silence boundary, fade one side only.** Crossfading into real silence pulls the tail of
  the outgoing speech down and sounds like a swallowed word.
- ⚠️ **20 ms is right between words and wrong inside one.** Removing material from *within* a phone needs
  a removal snapped to a whole number of **pitch periods** and a shorter crossfade (~12 ms): measured
  notches of **2.3–2.6 dB** against a **7.5 dB median** at the same speaker's own natural word junctions,
  i.e. quieter than joins a listener already accepts. ⛔ **A 1-frame fade is not a safe standing rule** —
  applied inside a phone it still clicked (2.95) *and* attenuated the following consonant by 3.4 dB.
- ✅ **The cleanest join lands in real silence on both sides.** Given a choice, prefer that over a clever
  mid-phone splice.

### Fade SHAPE is a first-class parameter

An operator called a music fade "very abrupt", so it was lengthened. Three times. It stayed abrupt,
because **length was the wrong axis.**

| through the fade | equal-power | dB-linear to −60 | knee (−24 dB at 80%) |
|---|---|---|---|
| 25% | −0.7 | −14.9 | −7.5 |
| **50%** | **−2.9** | **−29.8** | **−15.0** |
| 70% | −6.6 | −41.6 | −21.0 |
| 85% | −12.1 | −50.8 | −24.7 |

An equal-power fade sits at essentially full level for most of its length and then steps out, and that
step is what the ear reports. Lengthening it just holds full level for longer. Switching to dB-linear
over-corrected and named the axis exactly — the next note was "disappears too early". **Both complaints
are the same quantity read from opposite sides.**

✅ **So specify a fade by the level at 25/50/70/85%, not by a duration and a curve name.** "1.5 s
equal-power" does not describe what a listener hears; "−15 dB by halfway" does. And **know why
equal-power is the default**: it is correct for a *crossfade*, where the two halves must sum to constant
power. A fade to nothing has no such constraint, so carrying the default across is the actual mistake.

⚠️ **An envelope cannot fade material that was never laid down.** A bed assembled only up to a segment
boundary produced a stem that went silent **63 frames** before the configured end, while the config and
the report both claimed otherwise. Derive the material's extent and the envelope from one value.

### The programme can be on target and still have a word that jumps

Every figure below was measured on a programme whose integrated loudness, true peak, LRA and per-turn
levelling were **all correct**. The operator still flagged a single word, and both things were true at
once.

Per-turn levelling cannot see a phrase. Measure **momentary (400 ms, K-weighted) loudness per word
against the distribution of that speaker's own words** and the picture changes: the flagged phrase was
**+2.7 LU** over that speaker's median word and the loudest in the first 45 seconds. Grouping every word
≥2.5 LU over into phrases found **twelve at +4 LU or more, every one of them the host**, seven of them
question phrases — he lifts on question intonation. The guest's loudest was +3.9. **That asymmetry is
invisible in any per-turn statistic.**

- ⚠️ **K-weight it; do not use RMS.** Plain RMS called the flagged phrase unremarkable. Mixing the two
  metrics in one argument is how you "prove" a real complaint wrong.
- ⚠️ **Check your own loudness maths against a second implementation.** Hand-written K-weighting with one
  mistyped coefficient reads plausibly and is wrong everywhere. Reconcile against `ffmpeg`'s `ebur128` on
  the same file and refuse to continue if they differ by more than ~0.5 LU.

🔴 **And the expensive one: a pre-limiter trim on a phrase pinned at the ceiling barely changes its
loudness.** Thirteen phrase trims of **1.75–4.00 dB**, applied where a dialogue editor would put them,
delivered between **0.00 and 2.27 dB**, median **0.4 dB** — two delivered nothing measurable at all.
Every one of those phrases peaked within 2 dB of the ceiling, so trimming ahead of the limiter mostly
**stops the limiter reducing**. It is a fixed point: re-measure, re-propose, get the same trim and the
same non-result.

Keep the pre-limiter trim anyway, because it buys what gain after the limiter cannot — whole-programme
crest **13.50 → 13.82 dB** and limiter engagement **23.79% → 21.68%** of samples. Then apply the
**remainder** (asked minus delivered, measured) *after* the limiter, folded into the **limiter's own gain
curve** rather than per stem, because that curve is already applied identically to every stem and to the
master, which is what makes the stems sum to the master exactly.

⚠️ **Cap, don't flatten.** A question naturally lifts. Bringing phrases under a cap (+2.5 LU over the
speaker's median) is levelling; driving them all to the median is the compression this chain deliberately
removed, and it cost 2.4 dB of crest when a process did it.

⚠️ **Two references, because one cannot explain both complaints.** How far a phrase sits over the
speaker's median *across the programme* explains the systematically hot ones; how far over the median of
that speaker's words *in the previous ten seconds* explains the one that only stands out locally — at
+2.7 LU globally it is unremarkable, and a global-only rule would have trimmed it 0.25 dB and not fixed
what was heard.

### Music and stock assets

- **A watermarked preview is not a delivery asset** — but **do not trust a speech-band ratio to find the
  watermark.** A 366× speech-band spike in one track was a bass-only passage (spectral flatness 0.0004).
- **Speech-to-text on music hallucinates.** One run returned *"Thank you."* over instrumental music,
  twice. Never let a recogniser decide whether a track has a voice in it.
- ⚠️ **A content-correlation check cannot tell a licensed master from its own preview** — they are the
  same song. Verify that swap by **bitrate and bandwidth** (one preview was 50 kbps and rolled off at
  8.7 kHz).
- **Trim music to the clips you need.** `atrim=start_sample=` is sample-identical rather than
  re-resampled; one long mostly-silent stem shipped 128 MB where two trims were 2.3 MB.

---

## 8. Assemble, verify, render — then measure the render

Assembly is tutorial 6. Two rules belong here because they are about the deliverable, not the
application:

🔴 **"Complete" is not "correct."** After any render, measure the file rather than trusting the queue:
frame count against the programme's own total; frame rate (29.97 delivered as 30.000 drifts a frame every
33 s and reads as a lip-sync fault ten minutes in); that it is **one movie and not an image sequence**;
and integrated loudness **measured in the channel layout that ships**.

**Find "what has not been rendered yet" with the verifier, not with memory or mtimes.** Asked to include
"the outstanding changes", the reliable answer came from running the structural verifier against the last
build: it reported exactly one failure — a graphics clip whose **content hash** no longer matched its
regenerated source art, because the graphics cache saw no reason to re-render it. ⚠️ **mtimes lie here.**
A data file was timestamped 7 minutes *newer* than the build record, which reads as "the edit changed
after the build"; the clip-by-clip comparison against that same data passed and every seam was covered, so
the timestamp was noise. **A content hash is signal; a modification time is not.**

---

## 9. Subtitles: text from the recogniser, TIME from the aligner

```bash
python3 scripts/build_subtitles.py --lanes lanes.json --out sub --raster 1920x1080
python3 scripts/build_subtitles.py --lanes lanes.json --out sub --raster 1080x1920
```

A caption should appear within roughly half a second of the speech, and a recogniser's mean word-boundary
error of **328 ms** spends two thirds of that budget before the cue is placed. **On real footage a
recogniser is not accurate enough even for subtitles**, where the tolerance is far looser than for a cut.

- **Shape follows raster.** At 1080 px **vertical**, a burn-in at a legible phone size runs out of room
  around **30** characters, and a third line reaches into the lower third where the platform puts its own
  UI. Two lines, and hold each cue toward a ~**17 cps** reading rate by extending into the silence after
  it (capped, never past the segment end).
- ⚠️ **Where a cue is split on CAPACITY rather than on a pause, the speech is continuous** — so its last
  word lands exactly on the next cue's first word and you get cues whose out-time equals the next in-time
  to the millisecond. Players tolerate that and it flickers. Let the ceiling win.
- 🔴 **Measure the pause in PROGRAMME time, not source time.** If the audio has an internal splice, the
  pause you break on must be one that survives into the deliverable. On one VO 367 ms had been removed:
  the source showed **425 ms** between two words, the programme showed **58 ms**.
- **Forced alignment can introduce overlapping cues.** Re-timing to the aligner is right — it moved
  **82%** of word onsets by more than a frame (median +80 ms) — but at speaker changes it correctly places
  the incoming speaker's first word *before* the outgoing speaker's last word ends, because they really do
  talk over each other. The words overlap; **an SRT must not.** Seven cue ends needed pulling back on a
  178-cue programme. Clamp the earlier cue's OUT to the later cue's IN, never touch the text, and assert
  zero overlaps. Nothing else catches this — the render was correct, the transcript read correctly, frame
  counts and loudness all passed.
- **Select words by OVERLAP with a span, not containment.** An in-point chosen by measurement often lands
  *inside* a word, and a containment filter silently drops a word the viewer hears.
- **Restore punctuation by a 1:1 token join, asserted.** An aligner emits bare lowercase tokens; assert
  token-for-token against a transcript written for that exact span, or a later transcript edit shifts the
  punctuation one word along and leaves a full stop mid-sentence.

### The check that proves the offsets, and the two ways it lies

Cue arithmetic verified against cue arithmetic proves nothing. Open the **isolated stems** and compare
speech energy inside captioned frames against outside, per lane.

- ⚠️ **Not the mixed master.** Against a master, captioned spans measured p90 −25.3 to −12.2 dBFS against
  **−25.0 dBFS in a gap carrying no dialogue at all** — the music bed swamps the distinction and the check
  has no power to fail. On the stems the same comparison separated by **35 dB**.
- ⚠️ **Dilate the caption mask before taking its complement.** Deliberate 2-frame inter-cue gaps are
  continuous speech by construction, so on a dense read they become almost the entire "uncaptioned" set:
  one lane read **10.2 dB** while its captions were perfect, and **226 dB** with those slivers excluded. A
  gap the build inserted on purpose is not evidence against the build.
- 🔴 **Know what it cannot do: it is a placement smoke test, not a sync test.** Against planted ground
  truth it catches a lane placed **3 s** wrong and misses **2 s and under**, because inside a dense read a
  shifted caption still lands on *some* word. Sync therefore comes from taking the placement out of what
  the audio build **reported**, never from a second derivation of the same offset.

---

## 10. What the reviewer gets

A cut nobody can comment on precisely gets vague comments. Two documents make the difference, and both
share one trap: **they must speak in the delivered programme's time, not the source's.**

### Write TWO documents, not one

**The internal one shows its work:** every proposed cut as strikethrough, re-recorded answers with their
evidence, source timecodes, which file each decision came from, and the messaging validation. It exists
so a human can find the mistakes before anyone outside sees them.

**The client one carries only what they can act on:** no process language, no repo paths, no "generated"
(telling them a paragraph was generated invites them to discount it rather than read it), no strikethrough
and no re-record markers, every time in programme time, and **nothing typed that the cut determines** —
one hand-made draft claimed a "0:30 head", a "10 s title card" and a 14:19 runtime against an actual 0:24,
6.0 s and 14:50.

- ⚠️ **Generate option letters and clip numbers from list position.** Deleting options by hand left a
  document whose title list began at "Option B" and whose clip list began at "Candidate Short #2". Nothing
  signals sloppiness that fast.
- ✅ **The split that makes the client document safe to re-run:** keep the curated editorial — titles,
  description, social copy, which clips someone picked — in a separate data file that passes through
  verbatim, and let the generator compute only the timings around it. Then every re-edit is one command and
  nobody's wording is lost.
- ⚠️ **Read a reviewer's comment anchors as well as their tracked changes.** On one episode the reviewer's
  clip picks were recorded only as two "This one" comments attached to table rows; a regeneration looking
  only at tracked changes would have dropped both.

### A source range can be delivered more than once

⚠️ A cold-open teaser lifted from inside the interview appears **twice** in the programme, so a
source→programme lookup returning the first match silently dropped the entire cold open. **Return every
match.** And ⚠️ **only map segments whose dialogue is actually heard** — a title card's picture bed came
from a stretch where both people were talking, and the delivered card is silent, so taking the span at face
value put 32 words into a card that plays as silence.

### Validate the cut against the client's own brief

Extract the messaging goals from the pre-brief and mark each covered / partial / not covered in the
approved edit. This is the highest-value thing in the document: on one programme it surfaced a launch claim
the client had asked for that **never gets said on camera** — a pickup-line decision, made before delivery
rather than after.

- ⚠️ **An extractor that finds nothing must fail loudly.** One handled a single client's layout and
  returned zero goals for the next, printing *"0 of 0 extracted key messages are covered"* —
  indistinguishable from a pass. **Any "N of M" where M is 0 is a broken reader, not a clean sheet.**
- **Chapter anchors go stale when material is trimmed.** Snap them forward to the first kept word with a
  bound (~5 s) and **refuse past the bound** — a bigger jump means the content itself was cut and a human
  has to re-label it.

---

## 11. Deliver

- **Subtitles must never lag the picture.** A render and its sidecar carry the same version. **Do not
  reason about whether the timings moved — regenerate and diff.** Identical bytes → no cue moved and you
  have effectively relabelled; different bytes → regeneration was required anyway. Either way the correct
  file lands and no judgement is needed. ✅ Gate it at the render, which is the moment a version becomes
  something a person watches: fail the render's own checks when no subtitle file exists for that version.
- **Keep ONE encode per deliverable, and delete the superseded ones in the same breath.** Four bitrates of
  one cut accumulated on one job, and every later edit multiplied the stale ones. **Each is a wrong answer
  sitting in a shared folder, ready to be published.** Regenerable intermediates should not outlive the
  build that produced them.
- When a transport limit forces a smaller file, **render it from the NLE at a lower bitrate** rather than
  transcoding the deliverable, and **put the bitrate in the filename** so two encodes of one cut can never
  be confused.
- **Master and upload are two different files.** ProRes 422 HQ for one 14:50 programme measured
  **23.0 GB** (862 084 bytes/frame, 206.7 Mbps); the upload wants high-bitrate H.264.
- ⚠️ **Identify an archive folder by CONTENT, not by name.** Rebuilding audio needed lavalier files that
  were only on the archive drive, in folders named after people. Trusting those names would have been a
  coin flip on a swap that rebuilds the entire mix wrong, with only a downstream seam correlation to catch
  it. **Hash a file that still exists in both places, and check every duration against the sync manifest.**
  Both took seconds and neither depends on a label somebody typed.

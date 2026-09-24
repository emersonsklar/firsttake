# After the cards are safe

The rest of this skill ends at a verified offload. That is the point where the shoot
stops being at risk, not the point where it becomes useful. This covers the stretch
after that: getting two angles onto one timeline, turning speech into text someone can
search and read, and cutting something a client can watch.

Everything here is measured rather than eyeballed, for the same reason the rest of the
skill is: the failures are quiet. A sync that is 40 ms out looks fine on a laptop and
wrong on a big screen. A transcript that attributes one sentence to the wrong person
reads as fine until the person quoted notices.

---

## Sync two cameras that share no timecode

Consumer bodies rarely share a clock. The usual answer is a clap at the top of every
take, and you should still do that — it is free and it survives everything. But a clap
gives you **one** point in time, and the expensive failure is not offset, it is
**drift**: 29.97 and 30.000 are both called "30p" and pull apart about one frame every
33 seconds, so over a 40-minute interview the angles end up roughly 70 frames apart and
no single sync point fixes it.

```bash
python3 scripts/sync_cameras.py NINJA_ref.mov DJI_0001.mp4 DJI_0002.mp4
```

This correlates **onset envelopes** — the rectified first difference of short-term
loudness — rather than waveforms. That matters: it works between completely different
microphones, because two mics in one room disagree about spectrum but agree about *when
things start*. A lavalier mix against a camera's onboard mics locks fine.

It probes at up to ten places and reports the spread between them, so **the spread is
the drift measurement**. Read it like this:

| reading | means |
|---|---|
| spread under ~50 ms across a long span | one constant offset; a single sync point holds |
| spread growing with probe position | genuine drift; check both cameras' frame rates |
| drift near ±1000 ppm | almost certainly 29.97 against 30.000 |
| no lock | the two recordings share no audible content, or one is silent |

Measured example from a real two-camera interview: a 31-minute A-camera and two
consecutive B-camera files locked at +15.950 s and +1677.730 s, 9/9 and 2/2 probes,
spread **0.000 s**, drift **0 ppm**. Zero drift is worth confirming rather than
assuming — it is the thing that would have quietly ruined the edit.

Two details that saved time:

- **A camera that splits files mid-take does not necessarily lose time at the
  boundary.** On that shoot the second file began exactly where the first ended, at
  10 ms resolution. Elsewhere in this project a ~1.4 s loss per split was measured on
  the same camera family. So measure the specific files you have; do not carry another
  shoot's figure across, and be suspicious if the files are camera-generated *review*
  or *proxy* renders rather than the card originals.
- **Check a derived file is frame-aligned with the original before reusing
  timecodes.** A LUT-applied proxy is usually a 1:1 re-encode, but "usually" is not a
  measurement. One `sync_cameras.py` run answers it — 0.000 s means every timestamp
  transfers unchanged.

---

## Attribute speech to speakers by measurement

If the receiver was in **split mode**, each person is on their own channel and who
spoke when is a measurement, not a guess about turn-taking.

```bash
python3 scripts/diarize_by_channel.py interview.mov --words words.json \
    --channels 3,4 --names Host,Guest --srt out.srt --turns turns.json
```

`--words` takes a Whisper-style JSON with word timestamps, which `whisper`,
`faster-whisper` and `mlx-whisper` all produce. `--transcribe` will run mlx-whisper
directly if it is installed.

### Check the channels are independent first

Two channels carrying one summed mix look exactly like a split until you correlate
them. Pearson **r near 0 is a real split; near 1 is one mix copied twice**, and in that
case per-channel attribution is noise dressed up as data. The tool refuses rather than
guessing. A real split measured **|r| = 0.033**.

### Attribute per word, not per segment

Speech-to-text segments straddle speaker changes. Label a whole segment and you get the
tail of one person's question glued to the front of the other's answer — on a real
interview, an answer that opened with *"particular wearables?"*, which was the last two
words of the question before it. Per-word labels, with smoothing only where the margin
is thin, remove the whole class of error.

**Smooth only the uncertain words.** A blanket rolling median also rewrites words that
were measured confidently, and when a turn is shorter than the filter it drags the
entire turn to the wrong speaker. This is not hypothetical: the first version did
exactly that and scored 79% on a fixture with short turns. Keeping any word with more
than ~3 dB of margin as measured, and smoothing only the rest, took it to 100%.

### ⚠️ Recogniser word timings are NOT edit points

Word timestamps are accurate enough to read by, search by and attribute by. **They are
not accurate enough to cut on**, and the error is systematic rather than random: the
recogniser swallows the breath before a word into the word, so an in-point lands early
and an out-point lands late by a variable amount.

Six failures measured on one interview. A teaser in-point was **0.81 s late**, its
out-point **0.37 s early** — both by exactly that mechanism. A sentence whose last word
the recogniser ended at 76.200 actually releases at **76.35**. One question's opening
word was placed **0.88 s early**, inside the false start before it, so a cut made on that
timestamp would have clipped the word it was supposed to begin on.

⚠️ **AND THE INVERSE, WHICH BITES WHEN YOU VALIDATE AN ALIGNER.** The recogniser swallows the
breath *into* the word and reads **late**; an energy detector fires *on* the breath and reads
**early**. So when you check an aligner against an energy onset, a **vowel-initial word** will
routinely look like the aligner's worst error when the aligner is right and the detector is wrong.
Measured on one programme: every disagreement above 30 ms was "I" or "I'm", and at each the
"onset" was unvoiced — autocorrelation 0.29 and 0.22, against 0.74 where the same word was
correctly detected. **Caption, and cut, the voice rather than the breath**, and report the two
classes separately: averaged together they turned a 19 ms instrument into a 70 ms one.

**Validate every in and out against an energy-onset check** — the first point where
short-term energy rises ~12 dB above the preceding floor, measured at 10 ms resolution on
the speaker's *own* channel. That is a different question from "where does the recogniser
think the word starts," and it is the one an edit depends on.

Where a measured value has to override the recogniser, **write the measurement and its
evidence down next to the correction.** Three words on this programme needed that: for
one, the `/b/` closure runs 1585.145–1585.200 and the vowel begins 1585.200, with the
closing consonant carrying 66% of its energy above 2.5 kHz in its last third. That note
is why the next person does not re-litigate it — and it is the difference between a
correction and a fudge factor.

### A forced aligner is a better instrument than a recogniser — and it lies differently

If word times matter, a **forced aligner** (Montreal Forced Aligner and similar) beats an
ASR transcript: it is given the words and only has to place them, so it returns word *and
phone* boundaries in the source timebase. It is the right tool and worth the install.

But it fails in a way that is far more dangerous than ASR's systematic lateness, because
its output looks authoritative:

⛔ **An aligner can only place tokens that are present in the transcript.** If the speaker
says something the transcript does not contain — a filler, a false start, a breath-word —
the aligner has nowhere to put it, so it **stretches a neighbouring word over it and
shifts every label after it.** On one interview a 345 ms "uh" that the transcript lacked
turned into a 370 ms label for the word "have", and the whole following phrase sat a word
off. Four separate edits were made on those labels; three of them overrode the operator's
ear, and the operator was right every time.

**So: a word label that is implausibly long is a report that the LABEL is wrong, not that
the word is long.** Check the audio at that point for something unlisted before trusting
anything downstream of it. An aligner's confidence is about fit to the text you gave it,
not about whether that text is what was said.

⛔ **ALIGN AGAINST THE AUDIO THE DELIVERABLE CARRIES.** The rule above is usually read as being
about the *speaker's* unlisted speech. It is not limited to that: **any** sound the transcript does
not account for will be given a real word. On one job a director's off-camera "OK" — bleed on the
subject's lavalier, and already muted in the delivered mix — was still present in the alignment
corpus, and the aligner spent the subject's **first word on it**, 1.4 s early, leaving a 1.16 s hole
before the next word and dragging the four after it late. **Mute whatever the mix mutes before you
align, then align.** It is one line of work and it removes a whole class of failure.

⛔ **A BRAND NAME IS THE ONE WORD YOU MUST NOT TAKE FROM A RECOGNISER.** Product names are usually
**out of vocabulary** in a pronunciation dictionary, so an aligner models them as spoken-noise and
their boundaries go slack — loosening exactly the words a client video turns on. Add dictionary
entries. And a recogniser will confidently *reorder* an unfamiliar lockup: on one job the subject's
"my Open Ultra earbuds" was the reverse of the client's actual "Ultra Open Earbuds", and the
question of who was wrong took three independent checks to settle:

1. **two independent models.** Both returned the same order. If they disagree, neither is evidence.
2. **prime the model with the name you expect.** A corrective `initial_prompt` carrying the
   *correct* product name did **not** flip the output — so the acoustics were carrying it, not the
   model's prior. Had it flipped, the only thing learned would be that the model is suggestible.
   This step is what makes the other two interpretable, and it is the one people skip.
3. **read the phones, and read them as an ORDER.** A stop closure whose release is **low-HF**
   (0.1 % above 3 kHz — a bilabial /p/) followed by a nasal murmur (LF 70–95 %) came *before* a
   closure with a **high-HF burst** (79.6 % above 3 kHz — a coronal /tr/). One word order predicts
   that sequence and the other predicts its reverse, so the sequence alone decides it without
   needing to identify a single word. That beat both models and the operator's expectation.

The subject had said it backwards. **Caption it verbatim and escalate the discrepancy** — whether a
client-facing subtitle silently corrects a misspoken brand name is the client's call, never the
build's. Flag it in writing, because nobody watching the cut will notice.

Two related limits worth knowing before relying on one:

- **Alignment is not transcription.** Do not use an aligner to find out what was said.
- **Aligner boundaries are for TIMES, not for CUTS.** Measured on one programme, at a
  one-frame budget only **42 %** of the aligner's *correct* in-points had a frame boundary
  landing in genuine adjacent silence. Still validate against an energy onset.

### Removing a disfluency: find out which KIND it is first

"Uh" names at least three different objects and each needs a different edit. They sound
alike, and **no amount of refining the wrong edit converges** — that cost five rounds and
four wrong attempts on one VO:

| kind | what it is | the edit |
|---|---|---|
| **inserted filler** | a discrete schwa between words | cut it out |
| **filled pause** | a normal word stretched flat | shorten its phones |
| **prolonged vowel** | one word held | splice *inside* the vowel, period-aligned |

What actually identified each — none of it visible to an aligner, an envelope, or a
spectral-centroid boundary detector:

- **filled pause → F0 flatness against the speaker's own distribution.** 8.5 Hz of pitch
  movement where she normally uses 17.9. Not amplitude, not duration.
- **prolonged vowel → continuous unarticulated voicing** with a steady centroid and no
  consonantal structure.
- ⚠️ **Amplitude shape cannot tell voiced from unvoiced.** A vowel tail was confidently
  misread as a `/p/` plosive; the autocorrelation peak said 0.425 at F0 158 Hz with 95 % of
  the energy below 1 kHz. Use an autocorrelation peak in a 70–350 Hz band plus an LF/HF
  split, not the shape of the envelope.

**When a cut does not fix a reported disfluency, question what kind of object it is rather
than moving the cut.**

**The cheap process lesson, which beat all of the above: put candidate audio in front of
the operator early.** Six A/B clips cut from the raw source resolved in one round what
three rounds of spectral analysis got wrong. Their ear is the instrument; measurement's job
is to *implement* the answer, not to find it.

### Read the isolation figure, because it decides what post can do

The tool reports, per channel, how much louder each person is on their own lavalier
than on the other one:

| isolation | what is possible |
|---|---|
| **above ~12 dB** | a plain noise gate separates the two cleanly |
| **below ~12 dB** | a gate chatters; you must duck by label instead |

Real measurement from one interview: the guest had **15.3 dB** and the host **5.5 dB**.
The host's channel could not be gated at all — it heard the guest almost as loudly as
it heard the host. That is a **microphone placement problem, and no amount of
processing fixes it.** Measure it on a test recording before the shoot, not after.

---

## Loudness: normalise per speaker, then master the mix

Target **−16 LUFS integrated, −1.5 dBTP** for online spoken word. Alternatives if
asked: −14 for a punchy podcast, −19 to −23 for broadcast.

**If previous episodes exist, measure them before assuming the house target.** Three
reference episodes of one series measured **−21 to −22.4 LUFS** — five dB quieter than
the online-spoken-word target, and quiet enough that a viewer reaches for the volume.
That is worth telling the operator, and it is their call: on this one he kept −16
knowing the back catalogue sits lower. Measure, report, let them decide — do not
silently match a back catalogue and do not silently ignore it.

Per speaker, then mix, then master:

```
highpass=f=80, afftdn=nr=12:nf=-25:tn=1, deesser=i=0.1
→ per-turn static trim → one static gain per speaker
→ mix → alimiter=limit=-1.5dB:level=disabled:latency=true
```

⚠️ **There is no `loudnorm` and no `dynaudnorm` in that chain, and that is the point.**
An earlier version of this file recommended both. On quiet lavalier material they
produce audible damage, and the operator hears it as clipping at peaks that never
approach full scale. Both findings are measured, on a real interview:

- **`loudnorm` silently falls back to *dynamic* mode** when it cannot reach the target
  linearly inside the true-peak ceiling — which is exactly the case on quiet source
  (RMS ≈ −40 dBFS, crest 21.6 dB). It then rides the programme's gain: **18.7 dB of
  movement inside a single six-second window.** Long-term crest collapsed 20.5 → 12.7 dB
  while per-window crest was unchanged, which is how you tell gain riding from limiting.
- **`dynaudnorm` does the same thing one stage earlier** — 18 dB over a minute and 4–6 dB
  *inside one spoken phrase*. It is not the gentle fixed boost it looks like.

So set the level **statically** and let a true-peak limiter catch only overs. Reach the
target by iterating the static gain against a *measured render*, not by asking a filter
to hit it for you.

- **Per speaker only if you actually have per-speaker channels.** Split mode gives you
  that. If the recording is one summed mix, you must split by **time** instead, using
  the turn boundaries — and then measure each speaker's segments separately. Do not
  trust `aselect` inside a single `loudnorm` pass; it silently measures the whole
  stream and returns identical numbers for every speaker.
- ⚠️ **`alimiter`'s `level` option defaults to `true`.** Written as the bare
  `alimiter=limit=-1.0dB` this file used to show, the final limiter does not protect the
  master — it *auto-levels it back up* until the peak reaches the limit, throwing the
  loudness target away. Measured: −15.4 LUFS / −0.4 dBTP instead of −16 / −1.5. Always
  `level=disabled`, and `latency=true` so the look-ahead does not time-shift the master
  against the picture.
- **A move-gain-per-turn trim is safe; a move-gain-per-phrase one is not.** Level each
  speaking turn with a constant, and change it only inside measured silence between
  turns. Nothing then moves while anyone is speaking, which is the property the dynamic
  filters above cannot give you.
- **Check the splices, not just the average.** Measure integrated loudness a few
  seconds either side of every cut. Under ~1 dB is ideal; 2–3 dB is usually genuine
  speaker-to-speaker difference rather than an error, but you want to have looked.
- **Do not chase a crest figure below ~14.5 dB at these targets.** At −16 LUFS and
  −1.5 dBTP that is the arithmetic ceiling: once a window touches the ceiling its crest
  is `ceiling − RMS`, and RMS is pinned by the loudness target. The only lever is the
  loudness target itself.

Two things worth catching here because they are recording faults, not mix faults:

- **True peak above 0 dBTP on a camera track.** One real channel measured **+0.09
  dBTP** over the whole file — already over full scale before any processing. Check
  *where* the over is before blaming the recorder: on that shoot the single over sat
  outside every range that reached the edit, and inside the edit the camera path was
  flat clean gain with the body's limiter never engaging.
- **A very high loudness range** (LRA 18–22 on raw lavalier channels) means big level
  swings, and a long interview is uncomfortable at that spread. **Bring it down by
  levelling turns and mixing, not with a dynamics filter** — see the warning above. That
  route reached **LRA 4.1** on a 15-minute programme with no compression inside any
  phrase at all.

---

## Rough cuts

Cutting from an edit decision list is straightforward; two things caused real damage.

**Honour the editor's own trim points.** When a transcript has been hand-edited, the
timestamps in that document are the decisions. Deriving cuts from your own turn
boundaries instead will quietly reinstate material the editor removed — in one case
pulling 18 seconds of between-takes chatter back into the cut, because the automatic
turn ran past where the editor had ended it.

**Check for orphaned reactions after every removal.** When a question and answer are
cut, the *next* line often still refers to them. One kept line opened "Yeah, I guess
it's giving the customer the comfort and control…" — a direct response to an answer
that no longer existed. Grep the first sentence of every turn that follows a cut for
back-references ("Yeah", "that", "absolutely", "it") and check each one.

### ⚠️ A cut across a source discontinuity MUST change the visible angle

This is the defect that shipped four times on one programme, and every other check passed
the whole time.

When an edit splices ranges that sit far apart in the recording — reordered answers,
spliced retakes — the source position **leaps** at each seam. If the same angle is on
screen either side of that seam, the subject visibly jumps: posture, hands and head
position all differ between takes. One programme had seams where the source moved
**25 minutes** and the same tight single ran straight across them.

The fix is grammatical, not technical: **cut angle at the seam.** A change of framing
absorbs the discontinuity, which is exactly why editors cut on a jump rather than near it.
Nothing else hides it — a dissolve is out of grammar for this format, and two wide crops
2% apart in zoom do not read as a change.

**Make it a check, because eyes miss it.** Walk the visible picture frame by frame,
find every point where the visible source frame does not advance by exactly one, and
assert the visible (lane, source, framing) changed there. On the programme above this
found 33 seams and, before the fix, 4 uncovered — reported by frame with the size of the
leap. Negative-test it by restoring the old shot data: a check that has never failed has
never been tested.

Two practical notes from applying it:

- **The cheapest fix is usually already in the plan.** Three of the four bad seams sat
  2.3–2.9 s before the tight shot was *going* to hand to the wide anyway. Pulling that
  handover earlier onto the seam cost nothing and changed no grammar.
- **Where no other angle is scheduled, a short reaction shot is the idiomatic cover** —
  3–4 s on the listener. Check the reference episodes first: they use these sparingly
  (1.5–3% of dialogue), so one is in keeping and a run of them is not.

Note what the operator could see and the checks could not: the clips were exactly where
the plan said, every render probe found its requested frame, the grade and graphics were
right. **No check was asking whether a cut changed the framing**, which is the only
property that matters here.

### Frames are the grid; samples are derived from them

At 29.97 one frame is **1601.6 samples** at 48 kHz — not an integer, so the two grids
never line up. Decide every boundary in **frames**, then derive the sample offset from the
cumulative frame count. Rounding each piece independently accumulates drift between
picture and sound across a long programme.

Two counting rules that belong beside it:

- **Count frames; never compute length as `duration × fps`.** That read **2 frames past
  EOF** on a real camera file (49804 against a true 49802) and the bug had survived into a
  second build because both used the same arithmetic. Use the measured frame count.
- **Butt a split file's successor at its predecessor's measured end**, not at a computed
  timestamp. See the sync section above: a camera that splits mid-take may lose nothing at
  the boundary, or may lose ~1.4 s — measure the files you have.

### Splices: a butt join clicks, 20 ms of crossfade does not

Joining two pieces of dialogue at a sample boundary steps the waveform, and that step is
audible as a click even when both sides are quiet. Measured across one programme's
splices, the worst click was **−26.5 dBFS** — plainly audible. A **20 ms raised-cosine
crossfade** at every join took the worst to **−44.1 dBFS**, which is below the ≈ **−45
dBFS** audibility floor for this material.

Two details:

- **At a dialogue↔silence boundary, fade one side only.** Crossfading into real silence
  pulls the tail of the outgoing speech down with it and sounds like a swallowed word.
- **Measure the splices, do not assume them.** The figure to report is peak sample
  difference across each join, per splice, before and after.

⚠️ **20 ms is right between words and wrong inside one.** Removing material from *within* a
phone needs two different habits:

- **Snap the removal to a whole number of pitch periods.** Do that and the join becomes
  inaudible: measured notches of **2.3–2.6 dB** against a **7.5 dB median** at the same
  speaker's own natural word junctions — three times shallower than a real boundary, i.e.
  quieter than the joins a listener already accepts. Use a short crossfade (~12 ms), not 20.
- ⛔ **A 1-frame fade is not a safe standing rule.** Applied inside a phone it was measured
  to be *actively harmful*: it still clicked (2.95) **and** attenuated the following `/ð/` by
  3.4 dB. Fade lengths are per-context; do not promote one to a default.

**The cleanest join in that job landed in real silence on both sides.** When there is a
choice of where to cut, prefer both ends in silence over a clever mid-phone splice — the
measurement will reward it.

### Cutting where the language is awkward

The interesting cuts are not the clean ones. A real example: *"does… how do you think…"* —
the speaker starts a question, abandons it, and restarts. Moving the cut to start on
*"how do you think"* is right, and it still sounded wrong, because **the abruptness had
two causes and fixing either alone left it audible:**

1. the four-syllable false start was being clipped mid-syllable rather than removed
   cleanly, and
2. the two sides differed by **+30.9 dB** in level, so the join announced itself even
   after the words were right.

**Always check both when a cut sounds abrupt: the boundary and the level step.** A
transition that is correct on the transcript can still be wrong on the waveform. Audit
these deliberately — go through every splice and ask what a listener hears, not whether
the text now reads well.

### Synthetic camera moves, and why you may need them

With one camera on each person you can cut on dialogue. With a tight single on the
*guest* and only a wide two-shot for the host, every host question plays as the same
wide frame, and the usual fix is a slow push-in on the wide.

⚠️ **This is a coverage lesson first.** If both people matter, plan a second angle.
Faking movement in post is always worse than having shot it.

If you do need the push:

- **Use a filter that samples at subpixel positions.** `zoompan` rounds its crop
  rectangle to whole pixels; at a slow rate the edge holds for several frames then
  snaps a full pixel, about five times a second, and it reads exactly as handheld
  shake. Measured on a frozen frame, where any frame-to-frame difference is pure
  geometry error: `zoompan` jitter sd **0.586**, supersampling it 4× got to **0.161**,
  and `perspective` with `interpolation=linear` reached **0.006** — 92× better than
  `zoompan` and far cheaper than supersampling.
- **Push at a constant rate, not a constant amount.** A flat 10% per shot makes a
  9-second shot race and a 50-second shot crawl. About **0.3% per second, capped near
  12%**, makes every shot feel the same.
- **Enforce a minimum shot length.** Cutting on every speaker change turns a
  one-second interjection into a flash cut. Around 2.5 s, absorbing anything shorter
  into its neighbour, reads naturally. Placing cuts ~0.15 s *before* the new voice
  starts stops the angle arriving late.

---

## Fade SHAPE is a first-class parameter, not a default to inherit

An operator called a music fade "very abrupt", so it was lengthened. Three times. It stayed abrupt,
because length was the wrong axis.

An **equal-power (cosine)** fade is only **−2.9 dB at its halfway point** and −12.2 dB at 85%, then
plunges in its last 15%. The music sits at essentially full level for most of the fade and then
steps out — and that step is what the ear reports. Lengthening it just holds full level for longer.

Switching to **linear-in-dB** over the same span over-corrected, and named the axis exactly:
**−29.8 dB at halfway**, and the next note was "disappears too early". **Both complaints are the
same quantity read from opposite sides.** What worked was a knee — dB-linear to a shallow floor
(−24 dB) over the first 80%, then an equal-power taper to true zero: **−15.0 dB at halfway.**

| through the fade | equal-power | dB-linear to −60 | knee (−24 dB at 80%) |
|---|---|---|---|
| 25% | −0.7 | −14.9 | −7.5 |
| **50%** | **−2.9** | **−29.8** | **−15.0** |
| 70% | −6.6 | −41.6 | −21.0 |
| 85% | −12.1 | −50.8 | −24.7 |

**So specify a fade by the level at 25/50/70/85%, not by a duration and a curve name.** "1.5 s
equal-power" does not describe what a listener hears; "−15 dB by halfway" does.

⚠️ **And know why equal-power is the default.** It is correct for a **crossfade**, where the two
halves must sum to constant power. A fade to *nothing* has no such constraint, so carrying the
default across is the actual mistake. Keep equal-power for crossfades, lane edges and returns.

⚠️ **An envelope cannot fade material that was never laid down.** A bed assembled only up to a
segment boundary produced a stem that went silent 63 frames before the configured end, while the
config and the report both claimed otherwise. Three things disagreed and only measuring the
rendered stem caught it — derive the material's extent and the envelope from one value.

### Where a fade may end is a measurement about the voice, not the edit

"End it a couple of beats before she starts talking" needs three numbers before it is actionable:
her **voiced** onset, the beat length, and whether there is room. Here there wasn't — her line began
45 frames after the segment boundary and two beats was 45.6 frames — so the clearance had to come
out of the *shape* rather than the position.

⚠️ **Decide which sound "starts talking" means, by measurement.** The audible material 16 frames
before her first word was a **breath**: broadband, with LF−HF only 6–9 dB and the low band at the
noise floor, against 20–23 dB and 25 dB more low end once voiced. Reference the voice and leave the
breath — cutting to the breath would have clipped her.

---

## Accepting a supplied graphic: measure it, and the last frame proves nothing

Three vertical end cards were delivered for one 5-second slot and the first two were unusable:

- one was **608×1080** despite a filename reading `1080x1920` — a 1.78× upscale of pure text and a
  logo;
- one had the right raster but its **animation collapsed into 0.70 s** and then froze for 6.6 s **on
  an unfinished graphic** — a bare plus sign where the brand lockup belongs.

That second one is the instructive failure: **a supplied file's final frame is not evidence its
animation completed, only evidence of where it stopped.** Checking the end and seeing "a card" is
not checking that the card is finished.

Three cheap checks, in order, before accepting any supplied motion graphic:

1. **Raster against the filename.** They disagreed here, and the filename was the optimistic one.
2. **Constant frame rate?** All three deliveries were **variable** — 30.31, 62.49, and finally a
   clean 30.000 CFR. Note `r_frame_rate` may report a container timebase (`600/1`); use
   `avg_frame_rate` and the timestamp intervals.
3. **Profile frame-to-frame difference across the whole file** and print where motion is. Three
   lines of numpy, and it located both failures precisely: `static 0-22, MOVING 22-132, static
   132-239` is a healthy card; `static 0-47, MOVING 48-91, static 92-502` is a broken one.

When conforming: **frame rate FIRST** (frame indices mean nothing in a VFR file), then crop, then
scale, then trim by frame number. Crop before scaling if the raster is a pixel off exact — 1216
against an ideal 1215 would otherwise apply a 0.08% anamorphic squeeze. And start the trim on the
**first moving frame** rather than at a nominal "cut one second": the delivered file opened with
0.77 s of dead hold, so a literal 1 s cut would have begun 8 frames inside the animation.

---

## Speed effects: stabilise first, and match the motion model to the motion

Speeding a handheld or walking shot up multiplies the residual bob. The order is not negotiable:

    stabilise at 1x  ->  speed up + motion blur  ->  grade  ->  crop/scale

**Stabilising after the speed-up cannot work.** The blur is built by averaging N source frames into
each output frame, so the wobble is baked into a smear and there is no sharp feature left to track.

**Build the blur, do not just drop frames.** A plain frame-skip reads as a fast-forward. Average
groups of N frames so each output frame carries the whole group's movement — `tmix=frames=N` then
select every Nth. ⚠️ Select the **last** frame of each window (`eq(mod(n,N),N-1)`), not index 0:
index 0 is the one frame with no history, so `not(mod(n,N))` leaves the first frame of every step
unblurred.

### Quantify the wobble before treating it

Fit inter-frame displacement to `d(p) = t + s·p`, so the uniform scale `s` absorbs the intended
dolly and `t` is the leftover translation; then high-pass `t`. **Validate the estimator by injecting
a known shift** — here 0.40 px was recovered as 0.31, so every figure read ~23% conservative, which
is the right direction to be wrong in.

The result: **1.83 Hz, sharply narrowband, 36% of energy in the 0.8–3 Hz band.** That is a walking
cadence, and knowing it is a *narrowband* fundamental rather than broadband noise is what makes the
next decision possible.

### The motion model is the whole story

🔴 **`vidstab` fails on a centred forward dolly, structurally: libvidstab models translation and
rotation with NO ZOOM TERM.** The frame is expanding radially, that expansion swamps its global fit,
and its translation estimate comes out attenuated. It removed 13–15% of the amplitude, **left the
1.83 Hz fundamental exactly where it was**, and cost 5–8 points of high-frequency detail plus a
forced 8-bit round trip (libvidstab is 8-bit only).

The NLE's own **similarity** stabiliser fits scale — the term vidstab lacks — and moved the dominant
frequency to 10.95 Hz. The bounce *gone* rather than smaller, for less sharpness cost. What remains
reads as texture rather than as someone walking.

**So: ask what your stabiliser can represent before blaming the footage.** And prefer removing a
narrowband fundamental over shaving amplitude off it — the frequency signature is what the eye
recognises as amateur.

### Two things to check on any stabilised shot

- ⚠️ **Did it eat the intended motion?** On a dolly the forward travel *is* a scale change, so a
  stabiliser that fits scale can cancel the thing the shot depends on. Watch the `s` term.
- ⚠️ **A 4-corner / perspective mode is the wrong reach on a one-point corridor.** Measured, it
  removed *less* bob and increased shear 12% with 31% more frame-to-frame variability. **And a
  straightness test cannot detect that** — a homography maps straight lines to straight lines, so
  collinearity is a projective invariant and the test is structurally blind. Measure departure from
  rigidity instead: `‖A_affine − A_similarity‖`, plus the residual of an affine fit.

### Render the intermediate ungraded and full-raster

A watchable candidate is graded, cropped and scaled; dropping one of those into the timeline applies
the grade twice and the reframe twice. Render the deliverable version at **full raster with no
colour and no crop** — speed and blur only — and let the normal per-clip chain apply downstream
exactly once. Keep the clip's **id** unchanged if any per-clip data is keyed on it, or the trim
silently reverts to a shared default.

⚠️ And write down **which source frame the intermediate's frame 0 is.** Getting that wrong shifts
the shot by seconds. Prove it rather than asserting it: render 1x from the intermediate, PSNR it
against the raw reference at offsets 0/1/2/5, and confirm the peak is at 0 and falls off
monotonically.

---

## Music and stock assets

- **A watermarked preview is not a delivery asset.** Check for the audible watermark
  directly — but **do not trust a speech-band ratio to find it.** A 366× speech-band spike
  in one track was a bass-only passage (spectral flatness 0.0004), not a voice tag.
- **Speech-to-text on music hallucinates.** One run returned *"Thank you."* over
  instrumental music, twice. Never let a recogniser decide whether a track has a voice in it.
- ⚠️ **A content-correlation check cannot tell a licensed master from its own preview** —
  they are the same song. Verify that swap by **bitrate and bandwidth** (one preview here
  was 50 kbps and rolled off at 8.7 kHz), not by correlation.
- **Trim music to the clips you need.** Laying one long, mostly-silent stem works but ships
  a 128 MB asset where two sample-accurate trims are 2.3 MB. Use `atrim=start_sample=` so
  the trim is sample-identical rather than re-resampled.

---

## Assembling the project, and grading it

Two separate references, because both are deep enough to mislead from memory:

- **`resolve-api.md`** — handing a post house an assembled Resolve project rather than
  clips and a document. Read its playback-frame-rate section before anything else; that
  one shipped broken.
- **`grade-match.md`** — making two cameras match, identifying what log format you
  actually recorded, and deriving framing from a reference cut instead of from a
  compositional rule.

---

## What the reviewer gets

A cut nobody can comment on precisely gets vague comments. Two documents make the
difference, and both have one trap in common: **they must speak in the delivered
programme's time, not the source's.**

- **A transcript in programme time.** A timestamp in the A-camera's timebase is useless to
  someone watching the render — by then the material has been trimmed, reordered and
  spliced. Map every word through the programme structure and state in the document that
  the timestamps match the render frame for frame. Carry the recogniser caveat above into
  the document itself, so nobody downstream cuts from it.
  - ⚠️ **A source range can be delivered more than once.** A cold-open teaser lifted from
    inside the interview appears twice in the programme, so a source→programme lookup that
    returns the first match silently dropped the entire cold open. Return *every* match.
  - ⚠️ **Only map segments whose dialogue is actually heard.** A title card's picture bed
    came from a stretch where both people were talking; the delivered card is silent. Taking
    the span at face value put 32 words into a card that plays as silence.
### Write TWO review documents, not one

The document that lets you audit the edit and the document you send the client are not
the same document, and converting the first into the second by hand goes wrong the same
way every time. Generate both.

**The internal one shows its work:** every proposed cut as strikethrough, re-recorded
answers with their evidence, source timecodes, which file each decision came from, and
the messaging validation. It exists so a human can find the mistakes before anyone
outside sees them.

**The client one carries only what they can act on:**

- **No process language.** No "proposed", no "generated", no repo paths. They are being
  asked to choose, not to audit how the options were produced — and telling them a
  paragraph was generated invites them to discount it rather than read it.
- **No strikethrough and no re-record markers.** They are watching the assembled cut, so
  material you dropped is invisible to them and the fact that two answers were reshot is
  not their problem. A transcript of what they will actually hear beats a transcript of
  what was shot with the removals marked.
- **Every time in programme time.** Covered above, and it is the single most common way
  the client document goes stale.
- **Nothing typed that the cut determines.** One hand-made draft claimed a "0:30 head", a
  "10 s title card" and a 14:19 runtime against an actual 0:24, 6.0 s and 14:50 — three
  numbers wrong because they were typed once and then the cut moved.
- ⚠️ **Generate option letters and clip numbers from list position.** Deleting options by
  hand left a document whose title list began at "Option B" and whose clip list began at
  "Candidate Short #2". Nothing else in the document signals sloppiness that fast.

**The split that makes the client document safe to re-run:** keep the curated editorial —
titles, description, social copy, which clips someone picked — in a separate data file
that passes through verbatim, and let the generator compute only the timings around it.
Then every re-edit is one command and nobody's wording is lost. Without that split you
either regenerate and destroy a colleague's copy, or hand-edit and re-introduce every
problem above.

When a colleague reviews that document, **read their tracked changes *and* their comment
anchors before regenerating.** On one episode the social reviewer's choices were recorded
only as two "This one" comments attached to table rows; a regeneration that looked only
at tracked changes would have silently dropped both picks.

- **A review document that validates the cut against the client's own brief.** Extract the
  messaging goals from the pre-brief and mark each one covered / partial / not covered in
  the approved edit. This is the highest-value thing in the document: on one programme it
  surfaced a launch claim the client had asked for that never gets said on camera — a
  pickup-line decision, made before delivery rather than after.
  - ⚠️ **An extractor that finds nothing must fail loudly.** Mine handled one client's
    document layout and returned zero goals for the next, which printed as *"0 of 0
    extracted key messages are covered"* — indistinguishable from a pass. Any "N of M"
    where M is 0 is a broken reader, not a clean sheet.
  - **Chapter anchors go stale when material is trimmed.** Three anchors pointed inside
    removed material. Snap them forward to the first kept word with a bound (~5 s) and
    **refuse past the bound** — a bigger jump means the content itself was cut and a human
    has to re-label it.

---

## Subtitles: text from the recogniser, TIME from the aligner

A caption is supposed to appear within roughly half a second of the speech. A recogniser's mean
word-boundary error, measured on one interview, was **328 ms** against a forced aligner's **23 ms**
— so the recogniser spends two thirds of the whole budget before the cue is even placed. **On real
footage a recogniser is not accurate enough even for subtitles**, where the tolerance is far looser
than for a cut. Use it to learn *what* was said; force-align to learn *when*.
`scripts/build_subtitles.py` does the mapping, shaping and both cross-checks.

### Shape follows raster, and the 42-character line is a 16:9 number

At 1080 px **vertical** a burn-in at a legible phone size runs out of room around **30**
characters, and a third line reaches into the lower third — where the platform puts its own UI.
Two lines, and hold each cue toward a ~17 cps reading rate by **extending into the silence after
it** (capped, and never past the segment end); a cue lingering into a pause is normal practice.
Keep brand lockups off the line break unless nothing else fits.

⚠️ **Where a cue is split on CAPACITY rather than on a pause, the speech is continuous** — so its
last word lands exactly on the next cue's first word, and taking the later of the two produces
cues whose out-time equals the next in-time to the millisecond. Players tolerate that and it
flickers. Let the ceiling win: trimming two frames off a continuous phrase costs nothing readable.

### 🔴 Measure the pause in PROGRAMME time, not source time

Cue breaks go at pauses. If the audio has an internal splice, the pause you must break on is the
one **that survives into the deliverable**. On one VO 367 ms had been removed, and grouping on the
source-time gap put a caption break at a pause that no longer existed: the source showed 425 ms
between two words, the programme showed **58 ms**. Same family as "never measure a boundary
relative to the thing you are moving" — measure the silence the viewer actually hears.

### The check that proves the offsets, and the two ways it lies

Cue arithmetic verified against cue arithmetic proves nothing. Open the **isolated stems** and
compare speech energy inside captioned frames against outside, per lane.

⚠️ **Not the mixed master.** Run against a master, captioned spans measured p90 −25.3 to −12.2 dBFS
against **−25.0 dBFS in a gap carrying no dialogue at all** — the music bed swamps the distinction
and the check has no power to fail. On the stems the same comparison separated by 35 dB.

⚠️ **Dilate the caption mask before taking its complement.** The deliberate 2-frame inter-cue gaps
are continuous speech by construction, so on a read with no real pauses they become almost the
entire "uncaptioned" set and the figure collapses — one lane read **10.2 dB** while its captions
were perfect, and its whole uncaptioned residue was two 60 ms slivers between cues. With those
excluded the same lane read **226 dB**. A gap the build inserted on purpose is not evidence
against the build.

🔴 **And know what it cannot do: it is a placement smoke test, not a sync test.** Against planted
ground truth it catches a lane placed **3 s** wrong and misses **2 s and under**, because inside a
dense read a shifted caption still lands on *some* word. Sync therefore comes from taking the
placement out of what the audio build **reported** — never from a second derivation of the same
offset, which is how a subtitle file goes quietly out of sync with the mix it describes.

### Two smaller things that each cost a word

- **Select words by OVERLAP with the span, not containment.** An in-point chosen by measurement
  often lands *inside* a word — one opened 20 ms into "the", on the cuttable /dh/ — and a
  containment filter silently drops a word the viewer hears.
- **Restore punctuation by a 1:1 token join, asserted.** An aligner emits bare lowercase tokens; a
  caption needs sentence case and stops. Assert token-for-token against a transcript written for
  that exact span, or a later transcript edit shifts the punctuation one word along and leaves a
  full stop mid-sentence. Capitalise the first word of a mid-sentence pickup: a caption that opens
  lowercase reads as a typo.

---

## The verification trap this stage is full of

Every check above tests the property being claimed. That sounds obvious and it is the
single easiest thing to get wrong — see `method.md`, "Verify the property you are
actually claiming." The push-in shipped visibly broken because it was verified with
four still frames, which confirmed the image got tighter. Tightening was never in
doubt. Smoothness was, and a still frame cannot show motion.

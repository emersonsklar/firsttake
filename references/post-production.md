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

## The verification trap this stage is full of

Every check above tests the property being claimed. That sounds obvious and it is the
single easiest thing to get wrong — see `method.md`, "Verify the property you are
actually claiming." The push-in shipped visibly broken because it was verified with
four still frames, which confirmed the image got tighter. Tightening was never in
doubt. Smoothness was, and a still frame cannot show motion.

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

### 🔴 An energy step is not a word, and agreeing with the recogniser is not corroboration

The energy check above needs three gates, or it confidently returns the wrong event. Measured on
one interview while cutting a social short:

The "nearest ≥12 dB onset" to a hint came back **23 dB over floor, stable across every threshold
tried, and agreeing with the recogniser to 0.4 ms.** It was a **20 ms lip click** followed by an
inhale; the word began **380 ms later**. The agreement was not independent confirmation — the
recogniser had locked onto the same click. **Two methods agreeing is only evidence when they
cannot fail the same way.**

1. **Require voicing AFTER the onset, not at it** — ≥40% of the following 150 ms voiced. That
   still admits a word starting with a voiceless fricative (whose energy step is ~100 ms late)
   while rejecting clicks and breaths. A click is HF-dominant: first-difference energy fraction
   **0.185**, against ≤0.01 for a vowel.
2. **Require the onset be the RIGHT speaker.** With only a voicing gate the detector jumped to the
   *other* speaker's preceding question bleeding onto this mic — voiced 86%, f0 134 Hz, real
   speech, wrong person. **Pitch does not discriminate** (ranges overlap); relative level between
   the two lavs does.
3. **Separate speech from breath by margin over floor.** 6 dB over floor means "not silence"; a
   word sits **18–22 dB** over that speaker's own floor while breaths, lip noise and chair creak
   sit at **4–7 dB**. Detecting at 6 dB turned a breath 40 ms after the last word into "the next
   speaker starts here" and cut the tail off every clip.

⚠️ **BOTH LAVS HEAR BOTH PEOPLE, so attribution must be relative.** Each channel carries the other
speaker about 10 dB down — measured +8.5 to +10 dB in favour of whoever is actually talking. A
"who is speaking" test built on the absolute level of one channel produced a burst list for the
host that was a near-copy of the guest's, because it was crosstalk, and turned the host's −41 dB
breath into "clean silence ends here". Take the **difference** between channels, always.

⚠️ **A CONSTANT IS NOT A MEASUREMENT.** An out-point finder returned exactly `+0.050 s` for two
unrelated clips: that was its search cap, not the audio. **When two independent inputs give
byte-identical answers, suspect the code before believing the result.**

### Let the aligner and the energy detector argue, and read the disagreements

They disagreed by **340 ms** at one boundary (the aligner was right — the detector had the lip
click) and by **190 ms** at another (the energy measurement was right). Neither is authoritative.
Run both; every disagreement is either a bug or a real acoustic event, and both are worth knowing.
Keep the division of labour: **aligner for TIMES, measured energy for CUTS.**

⚠️ An aligner normalises its tokens — lowercase, punctuation stripped — so captions built straight
from its output read "know i almost went in the shower with my". Take **timings from the aligner
and spelling from the transcript**, joined in order with the normalised forms asserted equal, so a
silent misalignment cannot put one word's text on another word's clock.

### 🔴 A ZERO-LENGTH GAP BETWEEN TWO SPEAKERS IS AN ARTEFACT, NOT AN EDIT

An aligner once reported one speaker's last word ending and the next speaker's first word beginning
at **the same instant**. Measured on the delivered stems, the real gap was **620 ms**; at the other
end of the same cut it was 270 ms early on a word onset.

Two people do not hand over with no air. **Treat a 0.000 gap as a red flag**, and read levels either
side of the claimed boundary — a real gap shows both tracks dropping 20–40 dB. An aligner reaches
for a butt-join exactly where it is least sure, which is the turn change you were about to cut on.

### 🔴 TRANSCRIBING THE AUDIO IS NECESSARY AND NOT SUFFICIENT: read the CAPTION across the join

A dialogue cut was verified by transcribing the delivered render — clean, nothing surviving. The
**subtitle still contained a word the render removes**, reading *"It I like the idea as well"* over
audio that says only *"I like the idea as well"*.

The mechanism is worth understanding because it is not obvious: the transcript decided **which words
are inside the programme using the RECOGNISER's times**, and only then re-timed them with the
aligner. The recogniser was 0.27 s early on that word, which put a removed word inside the kept
range. Picture, mix, and ASR of the render all passed.

So check both clocks across any cut: **what a listener hears, and what a reader sees.** They come
from different instruments and usually only one of them is being measured.

⚠️ **And when a measurement and a machine disagree, correct the machine's number — do not move the
edit.** The tempting repair was to shift the cut earlier so the bad recogniser time fell inside the
removed span; that would have clipped the previous speaker, whose decay was still at −25 dBFS 60 ms
before it. Pin the measured time in whatever override table the transcript builder has, so a
measurement outranks both the recogniser and the aligner.

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

#### 🔴 Moving a handover onto one seam can strip the cover off another

The fix above — *move the nearest scheduled handover onto the seam* — is right, and it is
also how you introduce a jump cut somewhere else. The handover you move was already
covering something.

A later cut on the same programme hit this exactly. The new seam fell 142 frames before an
existing splice whose own cover was the shot boundary the repair wanted to move. Pulling
that boundary back onto the new seam was the *nearer* of two candidate repairs — half the
displacement — and it would have left the old splice playing one angle across a 26-frame
jump, **in the last four seconds of an already-approved programme, minutes away from the
edit that caused it.**

So a tool that repairs the shot list must validate against **every splice the programme
will have after the edit**, not just the one it is fixing:

- **Derive the seam list from the new range list**, not from a constant and not from the
  old one. Everything downstream of the cut shifts.
- **Build both candidate repairs** — push the preceding handover forward, pull the
  following one back — and let validation choose. Distance is a tie-breaker, never a
  reason. Here the shorter move was the wrong one.
- **Refuse to run if the plan on disk already fails the check.** Then a pre-existing
  uncovered seam reports itself instead of being built on, and a mistake in the seam
  derivation shows up as "the approved plan is broken" rather than as a silent pass.
- **Say which repair was chosen, which was rejected, and why** — in the tool's output and
  in the shoot's notes. "33 shots → 33, no cut added" is the line that tells a reviewer
  the grammar did not change.

Negative-test it the same way: replay the *previous* cut through the new code and assert it
reproduces the shipped shot list byte for byte. That is the only evidence that generalising
the rule did not change an approved edit.

#### The cover's length is usually not yours to choose

Two seams close together pin the shot between them: it is exactly the distance between
them, and no version of the edit makes it longer. Extending the cover past the second seam
would leave *that* seam uncovered.

This matters because "make the cutaway longer" is a natural note to give and sometimes an
impossible one to take. Work out the constraint before promising anything — and say which
it is, because the answer is often **not** the reason anyone assumes. On one programme the
cover was suspected of being short because it came from a 9:16 social cut; it was actually
pinned by a pause trim in the sign-off, and the 16:9 version came out the same length for
the same reason.

The other thing that can be spoken for is a **framing**. A two-line sign-off between two
people needs the wide that holds both of them; if the wide has to be on the far side of a
seam, it cannot also be the cover for it. Check what each framing is *for* in the closing
minute before assigning one to a repair.

#### Measure visibility per framing, at delivery geometry, against the motion floor

A mean absolute frame difference means nothing on its own — a talking head differs from its
own next frame. Report it as a **ratio over the adjacent-frame difference measured at the
same place on the same angle**. Near 1.0 the jump would read as ordinary motion; several
times the floor and it will be seen.

And measure it **through the delivery window**. The same two frames of the same camera read
**6.11** through a 1215×2160 vertical crop and **3.91** through the 16:9 window the
programme actually delivers, because the vertical crop discards the part of frame the
subject moves across. A number measured on a picture nobody will watch is not evidence
about the picture they will.

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

Separate references, because each is deep enough to mislead from memory:

- **`nle-backends.md`** — **start here.** How one cut targets either DaVinci Resolve or
  Premiere Pro: the shared plan, what each application can and cannot do, and handing a
  job to someone using the other one. Everything above this section is NLE-neutral and
  works whichever you have.
- **`resolve-api.md`** — the Resolve dialect. Read its playback-frame-rate section before
  anything else; that one shipped broken.
- **`premiere-uxp.md`** — the Premiere dialect. Read its transaction and in/out sections
  first; both differ from Resolve in ways that produce a plausible wrong timeline.
- **`grade-match.md`** — making two cameras match, identifying what log format you
  actually recorded, and deriving framing from a reference cut instead of from a
  compositional rule.

---

## Mix auditions for a client: derive them, and move one axis at a time

A partner listens to a cut and asks for the balance changed. Expect two or three rounds, and build
for that rather than remixing each time.

### Derive the variants from the stems; do not re-run the audio build

🔴 The audio build writes the master and the stems **at the paths the live NLE project references.**
Re-running it with one value changed is the obvious route and it clobbers the shipping mix. (Worse,
overwriting media under an open project has wedged a render here badly enough to need a force-quit.)

Derive instead, and it is exact rather than approximate, provided you check the premise:

* **Assert the stems still sum to the master, every run.** The build reports this once; that report
  is about *the build that wrote it*, not about the files on disk now. Re-measured each time: they
  summed to 1.25 of a 24-bit LSB, which is the master's own quantisation and nothing else. Keep the
  stems **32-bit float** so their sum carries no per-file rounding.
* **Assert the round trip.** Re-apply the *shipped* setting to the reverted stem and require it to
  reproduce the file on disk — 0.0 LSB, an exact identity. If un-doing and re-doing is not an
  identity, none of the other variants can be trusted, and that one check licenses the whole set.
* Rebuild every curve from the **same config through the same frame→sample quantiser** the mix used,
  and reuse the shipped breakpoint frames, so no new ramp is invented and nothing steps audibly.

### 🔴 "Constant power" is a property of the control, not of the signal

A constant-power pan law has `gl² + gr² = 2` at every position, so pan 0 is exactly identity and no
position is intrinsically louder. But what it preserves is `L²gl² + R²gr²`, which equals `L² + R²`
**only where the two channels carry equal power** — and real stereo material does not. Measured on
one job: re-panning moved overall loudness by 0.008 LU (nothing) and the section-to-section **step by
0.50 LU**. Small, but it is the number the creative decision was made on, so report it rather than
assuming the law protects it.

Two more from the same job:

- **Inverting a pan is exact** — divide by the same `gl`/`gr` — for any pan strictly inside ±1. It is
  not a mono fold-down and does not need to be.
- **A constant-power pan barely changes the mono fold-down** (0.02 dB), so on a single-speaker phone
  a pan change is *inaudible*. If a partner is auditioning one, say so: level is the cue that
  survives mono, and a pan is always the second cue.

### Translate the request into a parameter, and say which reading you took

*"Balanced audio in the earbuds — equal gain in both ears"* is **pan = 0**. It is **not** "make the
section measure 0.0 dB L−R", which would take *unequal* gain to cancel the recording's own ~1 dB
imbalance — the opposite of what was asked. Those two readings produce different files. Name the one
you implemented, and report the residual so nobody later mistakes the material's own character for
an error. (The operator's ruling on this job: *"having the natural difference is great."*)

### Move one axis at a time, and price the trade

Two requests arrived together — louder earbuds *and* a different speaker pan. Varying both at once
returns a verdict you cannot attribute, so the set held one while moving the other.

And **name what a request costs before they choose.** "Make the quiet device louder" spends the
level contrast that makes a handoff read: the measured step fell 9.06 → 7.26 → 5.26 LU as the quiet
side came up. Raising the *loud* side instead buys it back — the version that shipped ended up
**more** distinct than the original while still granting everything asked for. That option only
appeared because the cost was on the table as a number.

### The risk they name deserves its own measurement

*"I'm sensitive to it overwhelming her speaking"* is answerable. Compare the music against **her
voice alone**, over only the short blocks where the dialogue track is above its own speech
threshold — 61 % of the section here. A section average is the wrong statistic: it is dragged down
by the listening pauses where the music is *supposed* to be exposed.

    shipped  −15.13 dB under her line
    chosen   −10.97 dB under her line      warn above about −8 dB

That turns "is this too much?" into a number they can hold against a version they already approved.

### 🔴 An audition is not a mix

The variants are trims on a finished stem, so any solve the real build runs — compensation,
normalisation — has **not** re-run. When one is chosen, put its numbers in the config, re-run the
build properly, and then **verify the shipping mix against the audition rather than assuming it
matches**: here it landed within 0.3 LU and 0.3 dB, with the residual explained by the re-solved
compensation and master trim. Keep the superseded values in the config so the previous deliverable
stays regenerable.

Shipping a re-weighted master instead is how a design ends up existing only in a file that nothing
can regenerate.

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

---

## Cutting a vertical social short out of a long interview

The format, measured off a reference the client supplied: **hook (the strongest soundbite, cold,
pulled from inside the clip) → series card with the jingle → the whole clip → the official end
card with the jingle**, captions burned in over both picture sections and never over a card. The
hook repeats later in context, deliberately.

### A static vertical crop of a seated subject may not exist

A 9:16 window out of a 16:9 frame is **1215 px of 3840** at full height — scaled to 1080x1920 that
is a 0.889 DOWNSCALE, so there is no upscaling and the softness question does not arise. The real
constraint is movement: on one interview the subject's head swayed **588 px** and her head's full
extent spanned **1051 px** of the 1215 px window, so no static window held her with margin for the
whole clip.

Do not abandon a centred frame for that. Set an **excursion budget** in pixels *and* seconds and
take the crop nearest the compositional ideal that stays inside it. The budget has to be set by
rendering the extremes and looking: at **48 px** the hair kisses the edge and reads as a tight
portrait; at **152 px** the cheek is visibly cut. Also check what the subject is doing during the
excursion — hers happened while she leaned forward and looked *down*, so the "leave room in front
of the gaze" rule did not apply at that instant and the centred frame won on the merits.

⚠️ **PROBE DENSITY MUST SCALE WITH THE SPAN, or the measurement lies.** A fixed probe COUNT
gives a different answer on a different clip length, silently. A crop chosen from 92 probes across
a 23 s clip was reused when that clip was extended to 62 s — the same 92 probes then being one
every 0.68 s. It reported an excursion of **22 px**; at 4 probes/second the real envelope appears
and the correct window is 20 px away, because the sparse pass had **missed her furthest lean
entirely**. The sparse answer would have clipped her head by ~68 px. Use a RATE
(`max(floor, span_s * per_second)`), and treat any excursion reported as "1 of N probes" as
**unmeasured** rather than small — it is one sample. And **re-measure framing whenever the in/out
points move**: a crop is a property of the span, not of the shot.

⚠️ **Measure head extent; never scale the face box.** A symmetric `face_cx ± 1.55·face_w/2`
reported that 1051 px union as **1152 px** — an impossible framing — because the skin centre sat a
consistent **77 px left** of the face-box centre. Measured skin width / face width was 1.47.

⚠️ **A crop measured on one take does not transfer to another.** A CONFIRMED crop from the same
shoot, same camera, same subject was measured on a *standing* take and was 700 px wrong for the
*seated* interview. This is the conditional-durable trap: record the configuration the value
depends on.

### Reproducing a burned-in caption style: width finds the size, STROKE finds the weight

Extends "width solves the size, ink height picks the family". Width could **not** separate Regular
from Bold — −0.8% vs +1.4% against the reference. **Median horizontal stem run length** did,
cleanly: reference **8.0 px**, Regular 8.0, Bold 12.0. Ink coverage agrees (0.334 / 0.313 / 0.446)
but is weaker, because compression blur inflates it.

**Infer the block's anchor from cues of different line counts.** Comparing 2-line and 3-line cues
showed fixed line slots at a 92 px pitch with the block growing *upward* — bottom-anchored, with
the last baseline constant. A single example cannot reveal that, and guessing it wrong moves every
short cue.

Two more things worth measuring rather than assuming: whether the plate is drawn **only with text**
(on the reference it vanished entirely in the gaps between cues — check a caption-free frame), and
the plate's opacity, which is best read off a **bright** background feature crossing its edge. Note
that a plate that looks strong over a bright arm is nearly invisible over dark clothing at the same
alpha; that is the subject, not an error.

### The two faded overlays inside the body, and placing type by BASELINE

The format carries more than the cards. Shortly after the series card clears, the speaker's
**identity** fades in (name / role / company), fades out, and then a label for **the question
being answered** fades in. Plain white left-aligned text, **no plate**, sitting below the caption
band. Measured off a reference episode rather than designed:

| beat | measured | |
|---|---|---|
| gap after the card | 0.96 s | "shortly after" |
| identity | **3.92 s** | 0.20 s fade each way, the rest held |
| gap | 1.32 s | |
| question label | **4.80 s** | 0.16 s fade each way |

⚠️ **The durations you are given are hints, exactly like the timecodes.** "Approx 4 seconds" was
right; "roughly 6 seconds" was **4.80**. And once a client says *"we'll always want to do this"*,
make the tool **exit when the text is missing** rather than building a short without it — that is
the difference between a standing pattern and a thing someone has to remember.

⚠️ **ASCENDER IS NOT CAP HEIGHT, so type must be placed by its baseline.** A name measured 84 px
of ink where `HXM` measured 79: the `d` ascender is taller than a capital. And lines descend by
different amounts — a comma, a `g`, and a word with neither are three different offsets — so a
block placed by ink-top jitters line to line as the text changes. Ask the **font** where the
baseline is (PIL's `anchor="ls"`), never the cropped bitmap's height.

⚠️ **A bbox taken from a supersampled face is in supersampled pixels.** Where text is rendered at
`size × SS` and downsampled, using a raw `textbbox` puts every line **SS times** as far from its
baseline as intended. Divide at the point of use.

**When your text needs a line the reference does not have, derive the pitch from a rule that
reproduces the ones it does.** A long role title would not fit the reference's measure and wrapped
to two lines, making a four-line block out of a three-line pattern. Expressing the layout as ink
GAPS — one value after the name, one between body lines — reproduced both of the reference's
observed baseline pitches exactly, which is what earns the right to use the same rule for the new
one. And **bottom-anchor the block so it grows upward**: an extra line then cannot walk into the
bottom of frame.

Two judgement calls worth stating, because both went against the instinct:

- **Do not abbreviate to fit until you have checked the abbreviation fits.** It did not — so
  wrapping kept the information at no cost. "The same wording as the long-form video" is usually
  the client's actual requirement.
- **A template's own inconsistency is a measurement.** The reference's two overlays sit at left
  insets 47 px apart. They are never on screen together, so it is invisible; reproduce it rather
  than tidying it into something the house does not actually do.

**Assert the overlay is ABSENT where it should be, not only present where it should be.** The gap
between two elements is exactly where a fade that never reaches zero, or a one-frame overlap,
becomes visible — and a presence-only check passes on both faults.

⚠️ **VALIDATE A DETECTION FLOOR ON YOUR OWN FOOTAGE BEFORE TRUSTING IT.** The white-ink coverage
threshold that cleanly separated three elements on the reference had to be re-checked against our
own material, where the background and wardrobe differ. Across every frame of a render with no
overlays the band peaked at **0.00103** against a **0.0015** floor — a 1.46× margin, thin — while
an overlay reads **0.08–0.10**, a 65–100× separation. Know which of the two margins is the tight
one before quoting either.

⚠️ **Check for a scrim, and record its ABSENCE as a finding.** There is no gradient behind these
overlays on the reference — the bottom band's luma is flat on overlay-free frames — so plain white
text is the treatment. It was legible here only because the guest wore black against a mid
background. On a light shirt against a light wall this needs revisiting, which makes it a
**wardrobe and lighting** note as much as a graphics one.

**Reuse the one alpha-track mechanism, and express the fades as discrete per-frame alpha steps.**
A second builder for "but this one fades" would be a second thing that can be one frame wrong
without saying so. The proven concat-with-explicit-durations path takes each fade frame as a
one-frame entry unchanged. Put them on their **own track** rather than compositing into the
caption track when the caption artefact is already in an approved deliverable — they never share
a row of pixels, and two artefacts verified independently stay independently verifiable.

### Matching a partner lockup: a wide logotype cannot be height-matched to a compact one

An **8.31:1** partner wordmark set to the reference partner's *height* made the lockup **85% of
frame width**. Width-limit it into the reference's own lockup envelope instead. Prefer lifting a
brand mark from your own official card and **downscaling** over upscaling a small asset — and
cross-check the result against the reference's own copy of that mark, which should agree to a few
pixels if they are the same artwork.

### ffmpeg: two filters that report success and do nothing

1. **A chain of per-cue image overlays.** With single-frame PNG inputs, an overlay that starts
   *disabled* never reads its secondary input, so by the time its `enable` window arrives the image
   has hit EOF with nothing buffered and the picture passes straight through. Only the cues live at
   `n=0` rendered — **2 of 12** — with every frame count correct. `-loop 1` fixed those two, but
   looped images default to **25 fps** and drifted against a 29.97 main, dropping two more. **Build
   ONE full-length alpha track and composite once.**
2. **`ebur128` prints its summary at INFO level**, so `-v error` suppresses exactly the lines being
   parsed and every loudness figure comes back empty. Same family as `-map a:0` (invalid syntax,
   also silent). A measurement function must **fail loudly when it parses nothing** rather than
   returning NaN — a report reading "true peak ?" shipped once for this reason.

Also: **stream-copy-concatenated ProRes carries discontinuous timestamps.** Feeding that into a
filter graph made the encoder drop frames — 1037, then 1032, of 1043 — while both inputs verified
at exactly 1043, and `ffprobe -count_frames` and a raw decode disagreed with each other about the
result's length. That disagreement is the signature. Force `-fps_mode cfr` on the output.

### Resolve cannot start on a locked Mac

`scriptapp("Resolve")` **blocks forever** — no timeout, no error — and it *launches* Resolve rather
than attaching to it, so on a locked machine both processes sit at 0% CPU indefinitely. `pgrep`
matching "Resolve" is not evidence it was already running: compare its elapsed time against your
own process. `screencapture` on a locked Mac returns only the desktop picture, which is how to
confirm. No script can work around it.

---

## Deliverables: versions, sidecars, and how many copies to keep

### Subtitles must never lag the picture — and regenerating IS the check

A render and its subtitle sidecar must carry the same version. When they drift, a sidecar labelled
for a different cut is indistinguishable from one that is actually wrong, and someone eventually
hands out the pair. On one interview the picture reached v27 while the SRT still said v25.

**Do not reason about whether the timings moved — regenerate and diff.** A transcript build is
cheap and deterministic, so build it at the new version and compare against the previous file:

- **identical bytes** → no cue moved; you have effectively just relabelled;
- **different bytes** → regeneration was required anyway.

Either way the correct file lands and no judgement is needed.

Knowing the mechanism helps you *predict* the answer but must not replace the diff. A transcript
generated from word timings plus programme structure does not read the multicam/angle plan — so
changing which ANGLE is on screen, or swapping graphics artwork, **cannot** move a cue. Only a
programme-structure or word-timing change can. Confirmed on that job: the regenerated v27 came out
byte-identical to v25.

**Gate it at the render**, which is the moment a version becomes something a person watches: fail
the render's own checks when no subtitle file exists for the version being rendered.

### Find "what hasn't been rendered yet" with the verifier, not with memory or mtimes

Asked to include "the outstanding changes", the reliable answer came from running the structural
verifier against the last build. It reported exactly one failure: a graphics clip whose **content
hash** no longer matched its regenerated source art, because the graphics cache saw no reason to
re-render it. A `--rebuild-graphics` flag was the fix.

⚠️ **mtimes lie here.** A data file was timestamped 7 minutes NEWER than the build record, which
reads as "the edit changed after the build". The verifier's clip-by-clip comparison against that
same data passed and every seam was covered, so the timestamp was noise — a backup write or a
no-op save moves it. **A content hash is signal; a modification time is not.**

### Keep ONE encode per deliverable, and delete the superseded ones in the same breath

Four bitrates of the same cut accumulated on one job — upload master, two review sizes, and a
small transport copy for a chat client — and every later edit multiplied the stale ones. They are
not harmless clutter: **each is a wrong answer sitting in a shared folder, ready to be published.**
After any revision, remove the superseded copies as part of the same task and say what was removed.
Regenerable intermediates (per-segment renders, concatenated scratch) should not outlive the build
that produced them.

When a transport limit forces a smaller file, render it **from the NLE at a lower bitrate** rather
than transcoding the deliverable, so the picture stays NLE-native — and put the bitrate in the
filename so two encodes of one cut can never be confused or overwrite each other.

---

## Loudness: the programme can be on target and still have a word that jumps

Every figure below was measured on a 15-minute two-person interview whose integrated loudness,
true peak, LRA and per-turn levelling were all correct. The operator still flagged a single
word. Both things were true at once, and that is the point.

### Per-turn levelling is the right tool and it cannot see a phrase

Levelling one static trim per speaking turn, referenced to that speaker's own median turn,
worked: delivered turn-to-turn spread was **1.4 LU p5–p95 for the host and 0.8 LU for the
guest**, with no turn more than +1.3/−0.6 LU out. The flagged word's own turn measured **+0.6
LU**. It is correct at turn scale.

Inside a turn, nothing was looking. Measure **momentary (400 ms, K-weighted) loudness per
word against the distribution of that speaker's own words** and the picture changes: the
flagged phrase was **+2.7 LU** over his median word and the loudest word in the first 45
seconds. Grouping every word ≥2.5 LU over into phrases found **twelve at +4 LU or more, every
one of them the host**, seven of them question phrases — he lifts on question intonation. The
guest's loudest was +3.9. That asymmetry is invisible in any per-turn statistic.

⚠️ **K-WEIGHT IT, DON'T USE RMS.** Plain RMS called the flagged phrase unremarkable. K-weighting
is the curve the delivery target is specified in and the one that corresponds to what a
listener judges. Mixing the two metrics in one argument is how you "prove" a real complaint wrong.

⚠️ **CHECK YOUR OWN LOUDNESS MATHS AGAINST A SECOND IMPLEMENTATION BEFORE USING IT.** Hand-written
K-weighting with one mistyped coefficient reads plausibly and is wrong everywhere. Reconcile
against `ffmpeg`'s `ebur128` on the same file and refuse to continue if they differ by more than
about 0.5 LU.

### 🔴 A pre-limiter trim on a phrase that is pinned at the ceiling barely changes its loudness

This is the expensive one. Thirteen phrase trims of **1.75–4.00 dB**, applied where a dialogue
editor would put them — before the limiter — delivered between **0.00 and 2.27 dB**, a median of
0.4 dB. Two delivered nothing measurable at all.

The cause: every one of those phrases peaked within 2 dB of the ceiling, so trimming ahead of
the limiter mostly **stops the limiter reducing** instead of making the phrase quieter. It is a
fixed point — re-measure, re-propose, get the same trim and the same non-result.

Keep the pre-limiter trim anyway, because it buys something gain after the limiter cannot:
whole-programme crest **13.50 → 13.82 dB** and limiter engagement **23.79% → 21.68%** of samples.
Then apply the **remainder** (asked minus delivered, measured) *after* the limiter. Post-limiter
gain is exact and downward-only, so it converges in one pass and cannot create a peak.

Fold that remainder into the **limiter's own gain curve** rather than applying it per stem: that
curve is already applied identically to every stem and to the master, which is what makes the
stems sum to the master exactly, and a second application point is a second place for that
invariant to break.

⚠️ **Cap, don't flatten.** A question naturally lifts. Bringing phrases under a cap (+2.5 LU over
the speaker's median here) is levelling; driving them all to the median is the compression this
chain deliberately removed, and it cost 2.4 dB of crest when a process did it.

⚠️ **Two references, because one cannot explain both complaints.** How far a phrase sits over the
speaker's median *across the programme* explains the systematically hot ones. How far it sits over
the median of that speaker's words *in the previous ten seconds* explains the one that only stands
out locally — at +2.7 LU globally it is unremarkable, and a global-only rule would have trimmed it
0.25 dB and not fixed what was actually heard.

⚠️ **"Words peaking over −2 dBFS" is not comparable across builds** whose limiter threshold moved.
It read 24% then 48% and looked like a regression while limiting had actually gone *down* — the
threshold had moved past the fixed measuring line.

---

## Verify a dialogue cut by TRANSCRIBING what it produces

Removing a few words from a sentence looks like a timing problem and is really a
*which-word-is-this* problem. Three instruments disagreed on one 1.5 s stretch:

| | "during" onset | "especially" onset |
|---|---|---|
| recogniser | 1599.840 | 1601.280 |
| forced aligner | 1600.420 | **1601.090** |
| measured / verified | **1600.42** | **1601.39** |

An **energy envelope** tells you where energy is, not which word owns it — reading it cost a cut
that left a whole word ("time") audible. A **forced aligner** tells you where a word is and is
confidently wrong at exactly the hard places — trusting its 1601.090 would have sliced 210 ms off
the front of "especially". Neither error announced itself.

**What settled it was running ASR over the delivered audio from each candidate boundary.** From
the chosen one it reads "especially as you start thinking about", clean, nothing before it. That
answers the only question that matters: what does a listener hear. Do that before rendering, not
after; each wrong attempt here cost a full audio rebuild.

Then pin the corrected boundaries in a small MEASURED table applied **after** any alignment pass,
so a measurement outranks both the recogniser and the aligner.

### Forced alignment can introduce overlapping subtitle cues

Re-timing to the aligner is right — it moved **82% of word onsets by more than a frame** (median
+80 ms) on this shoot. But at speaker changes it correctly places the incoming speaker's first
word *before* the outgoing speaker's last word ends, because they really do talk over each other.
The words overlap; **an SRT must not**, or most players show two captions at once.

Seven cue ends needed pulling back on a 178-cue programme. Clamp the earlier cue's OUT time to the
later cue's IN time, never touch the text, and assert zero overlaps afterwards. Nothing else
catches this: the render was correct, the transcript read correctly, frame counts and loudness all
passed. Only a sidecar-specific check found it.

---

## A stale DERIVED cache of absolute frame numbers

A programme module refused to run when the *source* file stated music positions in absolute frames
that no longer fitted a shortened programme — a good guard, and it fired. It did not check the
**derived** cache built from those numbers, which was three cuts stale. The build then reported "no
problems" and rendered **33 frames too long**; only the render verifier caught it.

**Anything that caches absolute frame numbers needs the same guard as the file it came from.**
Prefer caching the rule and deriving the frames, and where a cache must hold absolute positions,
validate it against the current programme length before use.

---

## Identify which offload folder is which source by content, not by name

Rebuilding audio needed lavalier files that had been cleared off the internal disk and were only
on the archive drive, in folders named after the people. Trusting those names would have been a
coin flip on a swap that rebuilds the entire mix wrong, with only a seam correlation downstream
to catch it.

Confirm it twice and cheaply: **hash a file that still exists in both places** (one surviving copy
was byte-identical to the archive's, which named the folder), and **check every duration against
the sync manifest**. Both took seconds and neither depends on a label somebody typed.

---

## A cut inside one angle needs the other angle over it

Removing words from the middle of a sentence leaves a splice where the picture is continuous —
same framing, same subject, one frame later in a different place. That reads as a glitch, not an
edit. The fix is to change angle **exactly on the splice frame**, so the discontinuity arrives with
a reason.

The house pattern is measurable rather than invented. On one programme every existing source
discontinuity already coincided with an angle change — 12.179 s, 27.194 s, 76.243 s, 854.153 s of
interview time all landed on a shot boundary — so a new splice inherits the same rule, and a
verifier can assert it: **every seam carries an angle change, and no two adjacent shots share an
angle.**

🔴 **PREFER PULLING AN EXISTING HANDOVER ONTO THE SEAM OVER INVENTING AN ISLAND.** A cut that
removes a span can take the only *other* angle in that stretch with it, leaving the same shot either
side of the new seam. The instinct is to insert a short cutaway. First look at what is already
scheduled: on one cut a tight→wide change sat **3.04 s** late, and three earlier seams on the same
programme were fixed the same way (2.3–2.9 s late). Moving a cut that already exists is free;
inventing one adds a cut the edit never asked for, and it has to be justified to whoever approved
the edit.

Only invent an island when nothing else is scheduled anywhere near the seam. For length, take the
precedent rather than a feeling: the shortest existing island in that
programme was **3.50 s**, and it existed to cover exactly this. A cutaway to the listener while the
speaker finishes a question is natural material, so the camouflage costs nothing dramatically.

⚠️ **SNAP BOUNDARIES ONTO THE SEAM, DO NOT ARITHMETIC-SHIFT THEM.** Removing 33 frames means every
later shot boundary moves by 1.101 s, and doing that by subtraction left one boundary **3 ms** off
its seam — a one-frame same-angle join, which is the exact defect the rule exists to prevent. After
any shift, snap each boundary within a tolerance onto the seam frame and assert the coincidence.

---

## One string, one home: shared copy must be read, not copied

An episode's on-screen title belongs on the long-form card and on every short cut from it. Holding
it in two files means holding two answers, and the divergence is silent — one gets edited, the other
ships with last week's wording.

So the derived artefact **reads** the canonical string from the project that owns it rather than
keeping its own copy, and fails loudly if it is missing. The per-item field it replaced is kept as
provenance and explicitly marked as no longer used, because deleting it would lose the record of
what the card used to say.

A useful side effect worth predicting so it does not look like a bug: once several cuts share one
title, **their cards become byte-identical**. That is the invariant working, not a caching error.

---

## When a client asks for a "standing" pattern, make the tool refuse to ship without it

*"We'll also want to always do this"* is a different instruction from *"add this here"*. The second
is an edit; the first is a change to what a deliverable **is**.

Implement it so the pattern cannot be omitted: the builder exits when the new required input is
missing, rather than quietly producing the old shape. On one project that meant two already-built
cuts could no longer be rebuilt until someone wrote their missing text — which is the correct
outcome, and much better than discovering months later that half the set has the feature and half
does not.

A standing pattern that depends on somebody remembering is not standing.

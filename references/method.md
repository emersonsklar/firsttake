# Method — measure, don't assert

The techniques here are gear-independent. Every one of them replaced a guess with a
number on real work.

---

## A grey card and zebras replace a light meter

Twenty seconds, no extra hardware, and it is the exposure decision rather than a
proxy for it.

1. Hold an 18% grey card at the subject's face position, angled at the camera,
   tilted until any sheen disappears.
2. Set a custom zebra to the IRE value the log curve puts middle grey at, **for
   that camera's curve** — look up the manufacturer's published transfer function,
   don't reuse another camera's number.
3. Dial light output until the card *just* zebras.
4. Confirm on a waveform, not by eye.

**Correctly exposed log looks flat and dull ungraded.** If it looks right on an
ungraded monitor, it is overexposed. Any warning that a log image "looks wrong" is
worthless without a number.

**Overexposing log by a stop buys real signal-to-noise. Raising ISO does not** — it
moves the number without improving the signal.

### One geometry trick worth knowing

Put the subject as far off the background as the key is in front of them, and the
background lands about **2 stops** under, for free, with no background light needed
for level. Verify with a second zebra rather than trusting the arithmetic.

### The conditional-durable trap

Some facts are true *only given a configuration*. Real example: *walking a colour
chart toward the wide camera overexposes it* — but only because the key was nested
with that camera. Record the fact **and its condition**, or it gets applied when it
does not hold, or missed when it does.

---

## Confidence labels, and why they are the portable asset

Every hardware claim carries `CONFIRMED` / `LIKELY` / `UNVERIFIED` and how it was
established. This is more valuable than any single value, because it tells a reader
which numbers they may lean on.

Rank of evidence, best first:

1. Measured on this body/drive/mic, dated.
2. The manufacturer's own documentation for that exact model.
3. Multiple independent third parties agreeing.
4. One third party.
5. Inference from a similar model — this is `UNVERIFIED`, always.

**Datasheets are wrong often enough to check.** Two caught here: a lux figure that
was for the fresnel attachment rather than the bundled reflector (4.5× overstated),
and an assumed log base ISO that was two stops off, which quadrupled the real light
requirement.

---

## Give literal numbers, not principles

Someone shooting a few times a year cannot reconstruct a setup from theory while a
subject waits. "Key at 46 inches, centre 75 inches high, 30° down, 100% output" is
usable. "Position the key to taste, respecting inverse-square" is not.

State the number, then the reason in one clause if the reason changes what they'd
do. Never make them re-derive on set.

---

## The write-back protocol

After any shoot or test, for each finding ask: **would this be true on a different
shoot with the same kit?**

**Durable — goes in the kit profile:**
- A device fact verified or disproven, with label and method
- A trap: a setting whose obvious value is wrong, or two settings that interact
- A hardware constraint: real max height, unsupported card, needed adapter
- A physical constant: dB per knob step, GB per minute, a fixture's real output.
  These outlive every opinion built on them.
- A tool fix or recalibrated threshold → plus a fixture and an assertion
- An instruction found wrong → fix it *everywhere*, then record why it was wrong

**Not durable — stays in the shoot folder:** room dimensions, wall colour, this
client's chairs, the actual output percentages, who was in the room.

---

## The failure mode this structure exists to prevent

A geometry change invalidated one assumption, and that assumption was baked into
instructions in **four files** written at different times. Fixing the file someone
pointed at left three others wrong — twice in a row.

Two rules follow:

1. **When a premise changes, grep for the concept, not the phrasing you just
   edited.** Search for the idea: offload, exposure, distance, whatever it is.
2. **When verifying a fix, check the old text is GONE**, not merely that the new
   text is present.

A corollary for generated artifacts: **anything printed or exported from a source
file will go stale silently.** Give it a build script that regenerates *and asserts*
— page count, required phrases present, superseded phrases absent. A hand-made PDF
downstream of a source document is a stale copy waiting to happen.

---

## Never fix a check without adding a test

A measuring tool that silently stops detecting a fault is worse than no tool,
because it is trusted. Every measurement tool here ships a fault-planting harness; run
them after any edit.

Two lessons from building those fixtures:

- **Verify a synthetic fixture actually decodes before trusting a pass.** A clip
  built from a mismatched codec/pixel-format pair produces a file that does not
  decode, and that looks exactly like an analyser bug.
- **Plant faults as contiguous regions, not scattered pixels.** Lossy encoding
  smooths scattered pixels away; a planted 6% clip measured 1.5%. The fixture was
  wrong, not the tool. Real defects are regions anyway — a window, a forehead.

And when a fixture and a threshold disagree, **check which one is miscalibrated
before changing the threshold.** One transition fixture here used a 30× swing where
real footage showed 1.5×; the fixture was wrong, and loosening the threshold to
match it would have masked real faults.

---

## Verify the property you are actually claiming

A check can be real, pass honestly, and still be worthless, because it measures
something adjacent to the claim.

A slow push-in on a locked-off wide shot shipped visibly broken. It was verified by
sampling four still frames and confirming the image got progressively tighter. It did.
Tightening was never the question — **smoothness** was, and a single frame cannot
show motion. The filter was rounding its crop rectangle to whole pixels, holding for
five frames and then snapping one, about five times a second. On screen it read as
handheld shake. One frame-to-frame difference measurement would have caught it
instantly; four still frames could not have, no matter how carefully they were read.

This is a different failure from not measuring at all, and it is harder to notice
because there *is* a measurement and it *does* pass:

- **Name the failure you are afraid of, then ask whether the check could see it.**
  "Is it smooth" is a question about change between frames, so the evidence has to
  span frames.
- **Static evidence cannot settle a dynamic claim**, and a summary statistic cannot
  settle a claim about a specific moment.
- Elsewhere in this project the same shape appeared as a hardcoded conclusion printed
  next to a live calculation. The check and the claim had drifted apart.

---

## A guard must assert the PROPERTY, not the creative choice

The sharpest guard failure is not a check that misses a fault. It is a check that **fails a correct
build**, because it pinned a creative decision's literal values instead of the invariant those
values were an instance of.

An audio build asserted *"the earbud sections must be LEFT-biased and the speaker section
RIGHT-biased."* True of the design it was written for, and it passed for four versions. Then the
client asked for the earbuds **centred**, and the guard failed a build that was exactly right: a
centred pan leaves those sections at the source recording's **own −1.32 dB right bias**, which is
not left-biased and never could be.

That is the dangerous shape, because of what happens next. **A guard that encodes taste has to be
edited every time the taste changes, and the moment you are editing it is the moment you least want
it to argue with you.** The overwhelming temptation is to delete it or loosen it to nothing, and
then it never catches anything again.

The fix is to ask what the creative choice was an *instance* of:

| don't assert | assert |
|---|---|
| "this section is left-biased" | "this section is displaced from **its own** unprocessed balance in the direction the config asks, by roughly the amount that control produces" |
| "the gain here is −14 dB" | "the two sections differ by the step the config declares" |
| "shot 3 is the wide" | "the angle **changed** across this splice" |

Each right-hand version survives every future decision, and each still fails loudly on the fault
the left-hand one was written for. The tell that you have written the left-hand kind: **the guard
restates a number that also appears in your config.** A check and the thing it checks must not have
the same author's intent baked into both, or it is a tautology with extra steps — see also
"A cross-check must assert agreement, not track disagreement."

⚠️ And two corollaries worth having in advance:

- **Identity cases must be asserted as identities.** "Neutral" settings are where a sign-based guard
  quietly has no opinion. A pan of 0 is not "roughly centred", it is an **exact** no-op, and it is
  worth asserting to 0.00 — that one assertion is what proved the whole rewrite landed.
- **The unprocessed material is not neutral, and that is not a fault.** The reference for "did my
  control do what it claims" is the material's own measured state, never zero. A guard that demands
  zero is demanding you *correct the source*, which is usually the opposite of what was asked.

---

## A pass that doing nothing would also produce is not a pass

The sharpest version of the trap above. Before believing a green result, ask: **if the
step I am verifying had not run at all, would this check still pass?** If yes, the check
is measuring the wrong thing. Five failures on one project, all the same shape:

- **`0 of 0 covered.`** A document reader handled one client's layout and silently
  extracted nothing from the next. The report read as a clean sheet. Any `N of M` where M
  is 0 must fail loudly.
- **Every probe scored 0.000.** Read as "all 27 frames are wrong." The real cause was an
  ffmpeg invocation missing its input argument, producing no output at all. Assert that a
  measurement produced *output* before interpreting its value.
- **A build reported no problems with 28 crops silently unapplied.** A helper that split
  clips had dropped one field, so the framing data never reached the timeline. Nothing
  errored, because nothing looked.
- **A transcript missing its entire cold open.** A source→programme lookup returned only
  the first match, and that stretch of source is delivered twice. Output existed, looked
  plausible, and was short by a whole segment.
- **"The two cameras match to 0.0001."** I had compared a left-half mask against a
  whole-frame one. The real figure was 100× worse.

Two habits fall out of this:

1. **Negative-test the check itself.** Break the thing deliberately and confirm the check
   goes red. A check that has never failed has never been tested.
2. **A number that good on a first attempt is a bug report.** 0.0001 agreement, 1.0000
   correlation, 100% coverage, zero drift — each of those has been a broken measurement
   here at least once. Treat a suspiciously perfect result as an alarm, then go and earn it.

### An answer sitting on a search-window edge is not an answer

The most recognisable form of "a number that good is a bug report", and it has its own tell.
A boundary check reported exactly **"+300.0 ms"** on two different words, to one decimal. That was
not a measurement — it was the ±300 ms search window's edge, reported twice because the real
answer lay outside it both times. **An identical round error across independent items is a clipped
search, every time.**

Two habits:

1. **Return where the answer was found, and whether it was found at a boundary.** A result that
   landed on the first or last position of its window must be flagged and excluded, not averaged in.
2. **Clamp the window to something meaningful, then widen it.** That same search was also reaching
   *backwards past the previous word's end*, so on one word its "onset" was the tail of the word
   before. Widening alone would have made that worse; the fix was widening **and** clamping.

Once both were done, the two 300 ms "errors" became one −23 ms (correct all along) and one genuine
disagreement with an explanation. Nothing about the underlying audio had changed.

Corollary about the instrument: **do not measure through a transformation you added.** I
reported a master clipping at +0.93 dBFS with 3636 full-scale samples. The overs were
created by my own stereo→mono downmix adding 3 dB. Per channel the file measured −2.09
dBFS with zero overs. Measure the thing that ships, in the form it ships.

---

## A cross-check must assert agreement, not track disagreement

The previous section is about a check that cannot see the failure. This one is about a
check that **sees the failure, reports it every single run, and still lets it ship** —
because what it asserts is that the discrepancy has not *changed*.

A fast ffmpeg proxy was built alongside the NLE render as a cheap way to watch the cut.
It disagreed with the NLE by a clean ~1.15× on every graded clip. That was measured,
written down, labelled "unexplained but stable", and guarded by a tripwire asserting the
gain had not moved since the previous build. **It never moved. Eight review renders were
approved that did not show the deliverable**, and the cause turned out to be a stale baked
LUT that had never been compared against the CDL it claimed to implement (`grade-match.md`
has the colour specifics).

Three rules, all of which generalise past colour:

1. **Derive, do not duplicate.** Two implementations of one transform, in two formats,
   with nothing comparing them back to each other, will eventually disagree — and the
   disagreement will look like a mystery rather than a bug. Generate the second from the
   first at run time.
2. **"Unexplained but stable" is a decision to ship the error indefinitely.** Where a
   correct absolute value is knowable — two pipelines rendering the same edit *should*
   agree — assert **that**. Keep change-detection for quantities that genuinely have no
   known-good value, and say which mode you are in.
3. **Never classify test cases by the quantity under test.** This check split frames into
   "graded" and "control" by *whether their gain deviated from unity*. So the moment the
   two pipelines were reconciled, every frame became a control, the graded set went empty,
   and the check could never fire again. **A test that disappears when it starts passing is
   not a test.** Label cases from the structure that defines them — here, which shots carry
   a grade according to the edit — not from the measurement.

And the corollary that reaches backwards: **any measurement taken through an unvalidated
pipeline is provisional.** A confident, well-documented colour identification made through
this proxy reversed completely once the proxy was fixed. Before trusting a chain to measure
something, validate the chain against something independent — and keep enough of the
measurement (region, inputs, pipeline) that it can be re-run when the chain changes.

---

## Never measure a boundary relative to the thing you are moving

An earcon had to finish before the speaker's next word. The build found her speech onset by
searching **forward from the earcon's end** — so the onset it reported *moved when the earcon
moved*. Placing the earcon five frames earlier looked like it bought 167 ms of clearance and
delivered 52 ms, because the 167 had been computed against an "onset" that was merely the first
thing found after the earcon's **old** position.

The fix is one line of thinking: **measure the boundary independently of the object being
positioned**, then compare. Her real onset was a fixed property of the recording all along —
per-frame RMS rising 12 dB above a floor taken from a span verified silent.

**The general form: a reference must not be a function of the variable.** It shows up wherever a
search window, a threshold or a mask is derived from the thing under test — and it always reads as
"the fix didn't work as much as predicted" rather than as a measurement error.

---

## Compare like with like, and let a guard that cannot decide escalate

Two failures from one small check, both worth generalising.

**It compared a peak against an RMS.** A guard meant to stop a mute span from silencing real
dialogue compared the span's **peak** against the lane's **RMS** 90th percentile. Speech peaks run
10–15 dB above its RMS, so every span looked loud enough to be a line, and it refused a correct
edit — reporting 4.7 dB of headroom where the honest RMS-to-RMS figure was 23.2. **A ratio between
two different statistics is not a ratio.** Name the statistic on both sides of every threshold.

**And some questions a measurement genuinely cannot settle.** Off-mic direction bleeding onto a lav
sat only ~8 dB below the subject's own voice, which is unremarkable for someone speaking in a small
room. **Level alone cannot tell a director from a line.** The honest design is not a looser
threshold and not a hard refusal:

- pass it on measurement where the measurement is decisive;
- otherwise require an **explicit human assertion with a reason**, recorded in the config;
- and have the report state *which* of the two happened — `"measured"` versus `"ASSERTED by the
  operator; the level test cannot settle it"`.

That keeps the guard catching accidental spans without blocking verified-correct work, and it keeps
the distinction visible to whoever reads the build a month later. A check that cannot decide should
**escalate and record**, not silently widen.

---

## A guard's reference window must be where the signal actually is

A check written to catch a music bed masking a voiceover reported the bed as **"within −203.6 dB"**
of her voice. That absurdity was luck: the same two faults could as easily have produced a plausible
number and a green tick.

1. **It referenced the wrong span.** The window it was handed was the deliberate *silence after* the
   VO, so "her speech level" measured −240 dBFS, the gate accepted every block, and the comparison
   was against nothing at all.
2. **Its gate was a noise floor**, which accepts the **decay of a final syllable**. A decaying word
   is ~20 dB below speech proper, so the tail set the reference and the check reported masking at the
   one place a fade is always closest. Gate on being within ~15 dB of the speaker's **own
   90th-percentile** level instead.

And the premise was backwards before either of those: it failed any fade that merely *overlapped*
the VO, reasoning that it would bury her. Measured, separation **improved** monotonically as the fade
descended (+10.7 → +30.3 dB) — a bed dying away under the end of a line *exposes* a voice. **Assert
the thing you fear (proximity while she is speaking), not a proxy for it (overlap).**

⚠️ **A PER-SECTION MEASUREMENT NEEDS A PER-SECTION REFERENCE, and this one recurs.** Two variants of
it inside two days, the second written *immediately after* fixing the first — so treat it as an
attractor rather than a slip. A balance check compared each section of a mix against **one
whole-programme** figure of +1.38 dB. But the sections came from two different source recordings with
genuinely different balances (−1.32 dB and +1.51 dB), so the pooled number was dominated by whichever
section was loudest and was the wrong reference for either. It reported a 2.70 dB error that did not
exist. **A section average is also the wrong statistic for anything deliberately quiet inside that
section** — a music-under-dialogue margin averaged over a section is dragged down by the pauses where
the music is *meant* to be exposed; measure it only on the blocks where the voice is actually present.

⚠️ **And a crashed build is not distinguishable from a build that reported a problem** if you grep
for both patterns and count — both give 1. That cost a round of reading a *stale* report and
believing the previous build's numbers. Check the exit status, or assert the output file's mtime
moved.

---

## Check the length of the file you are actually reading

A shot was repointed at a pre-rendered retime of its source clip — 135 frames standing in for 1107.
Two separate length checks then compared the retime against the **original's** measured frame count
and reported "one of these numbers is wrong and every cut depends on it". Both numbers were right;
the comparison was a category error.

- **Probe the file the pipeline will open**, not the one the manifest describes.
- ⚠️ **Do not skip the check for the substituted file** — that drops the guard on the one file every
  cut now depends on. Measure the substitute's own length instead.
- Count decoded frames; never `duration × fps`.

---

## Don't build a classifier on a weak signal

Tempting pattern: take a noisy metric, add logic to decide what the user *meant*,
suppress the warning when the logic says it is benign.

This was tried on focus stability — classify whether sharpness dips sat at the ends
of a clip (subject entering/leaving) or in the middle (autofocus hunting), and
downgrade the former. It flipped verdict depending on which frames got sampled,
because encoders smear ramp frames across a transition.

**A coin-flip guarding an unrecoverable failure is worse than a warning you have to
read.** What shipped instead: report both numbers — variation over all samples, and
over the steadiest run — and let the human classify in one glance. When a
classifier's inputs are a handful of noisy samples of a content-dependent proxy,
report the numbers.

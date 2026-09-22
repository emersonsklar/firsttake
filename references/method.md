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
because it is trusted. Both tools here ship fault-planting harnesses; run them after
any edit.

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

Corollary about the instrument: **do not measure through a transformation you added.** I
reported a master clipping at +0.93 dBFS with 3636 full-scale samples. The overs were
created by my own stereo→mono downmix adding 3 dB. Per channel the file measured −2.09
dBFS with zero overs. Measure the thing that ships, in the form it ships.

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

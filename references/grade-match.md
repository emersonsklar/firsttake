# Making two cameras match, and grading to a reference

Two bodies from different manufacturers, both shooting log, cut together in one interview
will not match. The difference is largest exactly where it is most visible — skin. This is
a measurement job, and the measurements are cheap if you shot one thing: **a colour chart
in frame on both cameras at the same instant.**

⚠️ **Shoot that chart.** Hold a ColorChecker at the subject's face position, in the
subject's light, and let both cameras see it in the same take. Eleven seconds of it is
enough. Without it, everything below becomes eyeballing with extra steps.

---

## First, find out what you actually recorded

Container metadata is not evidence. One camera's files here were tagged `bt709` and
contained **D-Log M**; anything trusting that tag applies a Rec.709 interpretation to log
and the image is wrong in a way that looks like a bad grade.

Three independent checks, all cheap:

| Check | What it tells you |
|---|---|
| **Where 10-bit Y floors** | S-Log3 defines black at code **95**; D-Log M also sits at 95; Rec.709 floors at **64**. A file floored at 95 is log whatever the tag says. |
| **Codes per stop on a neutral ladder** | Measured **76.2** against S-Log3's published 76.66 — that is an identification, not an impression. The other camera measured ~120 codes/stop, pure log through the mids. |
| **Fit a candidate vendor LUT to the camera's own review render** | The correct LUT fits at **0.007 RMS**; no LUT at all fits at 0.079. The vendor D-Log M LUT fit its camera at 0.009. |

If the body also writes a baked-in-look review file, that file is a free answer key.

### When the cheap checks are confounded, use a CONFIRMED clip from the SAME camera

On a second project all three checks above failed on the *same camera model*, because the footage
was a walk-around B-roll roll rather than a controlled setup: no chart in frame, no neutral ladder,
and **the 10-bit floor test was swamped by codec undershoot** — the known-log clip read min 53
against a nominal 95, and so did the known-S-Log3 clip. The measurement was dominated by noise,
not by the transfer function.

🔴 **The thing that works is a control: a clip from the SAME camera whose format is CONFIRMED.**
Not another camera, and not an abstract idea of what log looks like. Measured ungraded, per-frame
luma percentiles:

| clip | std | p1 | p50 | p99 |
|---|---|---|---|---|
| **same camera, CONFIRMED log** | 56.8 | **20.7** | 133.3 | 214.0 |
| the clips in question (11 of them) | 34.6–61.2 | **19.0–41.3** | 58.7–151.7 | 165.7–254.0 |

The control sits in the *middle* of the distribution of the clips in question, and **not one of
the eleven has a black floor approaching 0** — which a display-referred capture of a sunlit
exterior with deep shade certainly would. One pass, settled.

**The black floor is the robust discriminator, and the question is "near zero or not"** — not its
exact value, which codec undershoot ruins. That is why the floor test failed as a *threshold* test
("is it at code 95?") and works as a *comparative* one.

⚠️ **THE TRAP THAT COST A DAY, and it is worth more than the method.** An earlier version of this
section said to compare the contrast signature against **the other camera's** raw log, and
concluded the clip in question "does not have a log signature" because it was much more contrasty
than the reference. That reasoning was wrong: **the two cameras used different log curves**, and
one was far flatter than the other, so the suspect clip correctly looked more contrasty *while
still being log*. **Using one log format as the yardstick for another guarantees the wrong
answer.** It produced a confident, documented, wrong retraction that reached a deliverable's spec
and came close to re-grading twelve clips the wrong way.

⚠️ **Run the transform on the control before calling it harmful.** "Applying the LUT crushes the
blacks from p1 26.7 to 7.7" sounds damning — until the same LUT is applied to the CONFIRMED clip,
*where it is the correct transform*, and takes it 20.7 → 4.0. **That is simply what the LUT does.**
A transform's effect is only evidence of misapplication if it differs from its effect on
known-good material.

⚠️ **A wrong container tag is not evidence of the opposite.** This camera tags log footage
`bt709`. "The tag is wrong" and "the footage is Rec.709" are different claims, and sliding from
the first to the second is how the day started.

### A format transform and a look match are different arguments

Keep these apart, because conflating them makes a taste disagreement look like a technical error —
which is exactly what happened above:

- the **input LUT** is a *format transform*. It is either right or wrong, and a CONFIRMED
  same-camera control decides it.
- the **CDL** is a *look match*, fitted under one set of lights to sit beside another camera. Its
  applicability to footage shot somewhere else is a judgement for the operator on a real cut, and
  no measurement settles it.

The "the graded b-roll looks warm and crushed" observation was real; it was about the CDL, fitted
in an interview room and applied unchanged to daylight exteriors. **Ask which of the two you are
arguing about before reaching for a measurement.**

⚠️ **Do not inherit a profile identification across clips without saying so.** A whole roll was
graded as log on the strength of one clip plus a shared filename marker, and when that clip's
confirmation was (wrongly, as it turned out) retracted, twelve clips were implicated at once. Same
camera, same day, same metadata is **circumstantial**: label it `LIKELY`, keep the inheritance
visible in one place so it can be revoked in one place — and where a group might genuinely differ
(a handheld walk-around versus a locked-off interview), **measure each group rather than assuming
the set is homogeneous.**

---

## The recipe

**Vendor LUT per camera, then one static ASC CDL per camera on top.**

Prefer a CDL over baking a corrected LUT: it is four readable numbers (slope, offset,
power, saturation), a human can adjust it, and it survives a round trip through other
software. Write it as `.ccc`.

**A single static CDL is valid for a whole take.** Each camera's skin luma wanders over
thirty minutes as the subject moves, but the *difference between the cameras* does not —
so what a per-shot correction would be chasing is the subject's pose, not the cameras.
Measured across 16 matched instants, skin agreement went from **ΔRGB 22.5/255 RMS to
6.7/255** with one static CDL per camera.

Note what is *not* claimed: `power = 1.0` was left alone deliberately. The chart said the
wide camera wanted `power ≈ 1.09`; the face's own contrast said the opposite sign. Both
instruments were confounded, so the honest move was to record the disagreement and settle
it by eye on a real cut, rather than fit to whichever one was measured last.

---

## One corrector cannot neutralise more than one white balance

A camera-match CDL is fitted under *one* lighting setup. Applied unchanged to footage shot
somewhere else it will be wrong, and wrong in a way that reads as "the grade is off" rather than as
"this is the wrong tool". Measured on twelve b-roll clips from one camera — offices and a sunlit
exterior — through a chain carrying a single interview-fitted CDL, as the cast of each clip's own
near-neutral pixels in dB relative to green:

    interiors   R +0.55 .. +1.31   B -1.40 .. -2.28     warm / orange
    exterior    R -0.79            B +0.36              cool / blue
    the interview setup            neutral, as fitted

The operator watching it said the outside looked "nice and blue and crisp", the interiors "warm and
orange-cast", the interview "cooler". Those are **three lighting conditions, not three grades going
wrong.** Per-clip trims took the 3.6 dB spread to ≤0.36 dB.

### Estimate the neutral point from the least-saturated mid-tones

⚠️ **Not grey-world.** Averaging the frame assumes the scene's own colours average to grey, which
is false here in a way that actively misleads: one clip is a deliberately multicoloured wall,
another is mostly blue sky. Grey-world would correct the sky out of the sky.

Two stages, both load-bearing:
1. keep only **mid-luma** pixels — the 25th to 85th percentile of that frame's own luma. Drops
   crushed shadows, where chroma noise dominates, and clipped highlights, where channels are pinned
   and the ratio is meaningless.
2. of those, keep the **least-saturated 20%** — most likely a wall, floor, paper or concrete.

Report the mask's pixel count and mean saturation, so a mask that collapsed or found nothing
neutral is visible rather than silent. State the result `LIKELY`: this is not a grey card, and on a
frame with no neutral surface it degrades to the least-coloured thing present.

### Correct in the space you measured, which means the trim goes in the LUT

The cast is measured **after** the input transform, so the correction has to be applied after it —
measure and correct in the same space and it is one shot with no iteration. But an NLE runs a
node's corrector **before** that node's LUT, on the log signal, so a display-space trim cannot live
in the CDL.

Compose it into the LUT instead, per clip:

    clip_cube(x) = trim(input_LUT(x))

    trim:  out = ((in * gain_c) - black) / (1 - black)     per channel, clipped

`gain_c = 10^(-cast_c/20)` neutralises the measured cast. The shared CDL stays untouched, no second
node is needed, and both render paths load the identical cube so they cannot disagree.

### Match the black point too. "Washed out" is a lifted black floor

Cast is only half of why shots fail to cut together. These clips ran p1 = 0.4–13.5 of 255; landing
them all at ~4 is what made them look like one piece, and it is the difference between the
operator's "crisp" and "washed". Divide by `(1 - black)` afterwards so pulling the blacks down does
not darken the whole image.

**Do not match exposure.** Scene brightness legitimately differs — a dark display is not a bright
corridor — and matching mid-grey across them lifts the dark ones into mush. Correcting the neutral
point is what makes shots cut together; flattening every hue or every level is not. A sky stays
blue because sky is not in the neutral mask.

### Verify stability per channel BEFORE fitting anything static

A static per-clip correction is only valid if the cast does not drift within the shot. ⚠️ Compute
that **per channel**: a first pass pooled the channel spread with the temporal spread and reported
"the cast moves across the shot" on 8 of 12 clips. Per channel the worst drift was 0.63 dB and most
were under 0.1 — entirely static, and the warning was an artefact of the statistic. **A spread that
mixes two axes is not a spread.**

Sample several frames per clip, not one: a dolly or pan changes what is in shot.

### And expose one knob for the ear

Full neutralisation is right for consistency and occasionally wrong for a shot whose warmth is
motivated — a tungsten-lit vignette, firelight. Keep a per-clip `strength` (1.0 = fully neutral,
0.0 = leave it) that a re-fit **preserves**, because it is a judgement and re-measuring must not
silently discard it.

---

## Grading to a reference episode, without importing its room

If there is a previous episode of the same series, it is a reference — but only partly.
One reference here was shot on a **completely different set**: dark studio, deep-blue
backdrop. Its whole-frame statistics (0.64 mean saturation, +0.30 blue in the shadows)
describe that set, not a house grade. Lift those and you have graded your room into
someone else's room.

**Only two things transfer: skin, and the black point.** From that reference: skin luma
**0.4582** (sd 0.038 over 90 frames), RGB 8-bit **(155, 108, 90)**, blacks reaching 0
(p0.1 = 0.0034).

If there is no reference at all, a defensible target for a correctly exposed face in
BT.709 is around **0.60 in code value (≈60 IRE)**. `UNVERIFIED` — that is the industry
rule of thumb as I have it, not something I have confirmed against a published source or
measured here. Measure a reference frame if you have one; treat the number as a starting
point, not an authority.

---

## The measurement traps, all of which cost real time

- ⚠️ **Never measure contrast or chroma on a target smaller than the lens can resolve.**
  A 24-patch chart is only ~22 px per patch on a wide camera. Measured there it reported a
  0.44-stop shadow lift that the large bars say is not there.
- ⚠️ **Use ratios of averages, not averages of ratios.** Per-pixel skin *saturation* fails
  the same way one level up: the face is 3× smaller on the wide camera, so its chroma
  averages down, and chasing that drove `sat` to 1.24 and would not converge.
- ⚠️ **Compare like-built masks.** I once reported the two cameras matching to 0.0001 and
  it was nonsense — I had compared a left-half chroma mask against a whole-frame one. The
  real figure was ΔY median +0.0123. **A number that good on a first attempt is a bug
  report.**
- ⚠️ **A skin mask picks up more than skin.** An early "skin sits at 0.408" was really
  0.506; the mask was including shadowed neck and hands. Constrain the mask to the face
  region, and print the pixel count so a mask that collapsed is visible.
- ⚠️ **Record WHERE a colour measurement sampled, and THROUGH WHICH CHAIN.** A profile was
  declared CONFIRMED on an "83.41 → 43.35 ΔRGB" improvement. The script was ad-hoc and did
  not survive, the measurement boxes were never written down, and the numbers lived on only
  in a comment — so the figure could never be reproduced or checked. It was later found to
  have been measured through a miscalibrated transform, and corrected it **reversed**. A
  colour number without its sampling region and its pipeline is an opinion with a decimal
  point.

### ⛔ Express one grade once — the trap that cost eight review renders

A fast ffmpeg proxy was kept alongside the NLE build as a cheap cross-check. For **eight
builds it rendered ~1.15× darker than the NLE on every graded clip.** The gap was noticed,
measured, labelled "unexplained", and guarded by a check that asserted **the gain had not
moved since the last build.** It never moved. It was wrong the whole time, and the operator
approved eight renders that did not show the deliverable.

**Cause: the grade existed twice.** The builder held the ASC CDL as four numbers and gave
them to the NLE live; the proxy read a pre-baked `.cube` of "the same" CDL. The cube did
not implement it — 0.3944 at mid-grey where the CDL says 0.4542, a ratio of 0.868, and
**1 / 0.868 = 1.152** against the measured 1.1543. Nothing ever compared the two
representations against each other.

- **Derive, do not duplicate.** Generate the second representation from the first at run
  time. With `power = 1.0` an ASC CDL is affine — slope/offset plus a saturation matrix —
  and trilinear interpolation of an affine function is *exact*, so a generated 33-node cube
  reproduces the arithmetic to 0.0001/255. There is no accuracy argument for a stale bake.
- **A stray bake is contagious.** Where the bad cube came from was never established, which
  means anything else that ever consumed it carries the same error. Renaming it `BROKEN_*`
  and writing down why beats deleting it.
- The general form of this failure is in `method.md` — *a cross-check must assert
  agreement, not track disagreement.*

⚠️ And one claim this episode destroyed, worth not repeating: **"the corrector runs before
the LUT, so CDL order is critical" was tested through the bad bake.** Re-measured properly,
the two orders land 1.26 and 1.25/255 from the NLE — with `power = 1.0` and gentle
slope/offset, **order barely matters.** Implement the NLE's order because it is what the
NLE does, not because of a number measured through a broken chain.

---

## Reproducing a graphics template someone else designed

Same discipline again: title cards and lower thirds have to match what the series has been
shipping, and the only reliable source is a delivered file. Build an ink mask by
colour-keying (near-white type, and the brand colour separately at alpha > 200), take
row/column run-length bands **per panel**, and you recover every element's bounding box to
the pixel. On one series two independently delivered cards agreed to **1–2 px on every
shared number**, which is what makes them a template rather than one episode's accident.

**Width solves the size; height picks the typeface.** For each reference string, find the
size whose rendered ink width matches the measured width — then use ink **height** at that
size to choose between faces, because width alone cannot discriminate (any face scales to
hit a width). On a real lower third, matching widths gave plausible answers for two
different families; ink height was exact for one on all three role strings and 1–3 px short
for the other every time. That settled it.

Three traps:

- ⚠️ **Never substitute a typeface silently.** A stand-in was 7–11% wider than the real
  face, which changes every line break on the card. A squeeze factor fitted to the
  reference (0.93, later 0.896 on better data) gets the metrics close and is still an
  approximation — when the real font arrives, validate it and delete the squeeze. Here the
  real face reproduced the reference to **−0.2%** with no squeeze at all, which also
  retroactively confirmed the whole measurement chain.
- ⚠️ **A supplied spec can be internally inconsistent with your own assets, and measuring
  the reference is how you tell which part to trust.** One spec listed "Partner logo:
  height 628px, width auto". The partner's wordmark is 8.31:1, so at 628 tall it would be
  **5217 px wide — 2.7× the frame**, and nothing on any delivered card is 628 px tall. Read
  as the placed artwork's own intrinsic height it is consistent with everything. State the
  arithmetic when you flag this; it converts an argument into a decision.
- **Solve the reference's own line breaks before believing a text measure.** A spec gave a
  940 px headline box; at the spec's size the reference's first line fits in 745 px and the
  next word would take it to 1029, so a plain greedy wrap breaks exactly where the
  delivered card breaks. That agreement is what proves the measure is real rather than a
  designer's hand-break.

## Framing: measure the reference, do not apply the rule

A related job, same discipline. Asked to crop a 4K wide shot into a single, I applied the
classical **lookroom** rule — subject offset toward the side they are facing — and the
operator said it looked wrong. He was right. Measured across three reference episodes of
the same series, the house style **centres its singles**: face centre-x **0.413–0.518**,
head height **0.311–0.520**. My framing sat at face centre-x 0.314 and head height 0.562 —
off-centre against every reference, and 8% tighter than the tightest one.

Two lessons worth more than the numbers:

- **Derive framing by measuring the reference cut, not by applying a compositional rule.**
  Face centre-x and head-height-as-fraction-of-frame are two numbers per frame and they
  settle the argument in minutes.
- **Watch what a secondary goal is costing you.** What pushed the subject off-centre was
  my trying to crop a plant out of frame. Checking the references, one of their own singles
  has a plant across 25% of the frame. Subject placement outranks tidying the background,
  and I had it backwards.

⚠️ **State a house rule only from more than one episode.** I wrote ten rules from a
single reference episode and then measured three. **Five failed outright:**

| Claimed from one episode | Measured across three |
|---|---|
| "on their own single 66% ± 2 of their speaking time" — called *remarkably stable* | **29.9 / 53.0 / 55.2 / 86.5%** across four speakers. A 2.9× spread. |
| "there are no reaction shots; the rule is absolute" | **13** in one episode (10.6% of the host's speaking time), **11** in another, one running 11.35 s |
| "cut to the speaker's single about 5.0 s into an answer — the single most important number" | medians **2.82 / 5.01 / 13.16 s**. A 4.7× spread, and crew-specific: derive it from how tight your wide is. |
| "the lower third is one left-to-right list across the frame" | three episodes, **three different designs** |
| "1920×1080 at 23.976 is the delivery format" | one episode is **25 fps** |

What *did* hold across all three is worth more than any of the above, because it survived
contact with more data: the wide shot is the hub (87–90% of cuts touch it), cuts lead the
incoming voice by 0.14–0.23 s and land within 0.08–0.12 s of a gap between words, and
there are hard cuts only — zero dissolves, zero B-roll, zero cutaways in 62 minutes.

One episode is an example. Three is a grammar.

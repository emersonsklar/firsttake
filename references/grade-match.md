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

---

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

⚠️ **State a house rule only from more than one episode.** From a single reference I
asserted four rules — "no reaction shots," "own-single 66% ± 2," "cut about 5 s in" —
and three more episodes contradicted all of them: 13 reaction shots in one, own-single
29.9–86.5%, median delay 13.2 s against the 2.8 s I had claimed. One episode is an
example. Three is a grammar.

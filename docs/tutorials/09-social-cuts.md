# 9 — Deriving social cuts from a long interview

A short is not a new video. It is **material that already exists, reframed** — and every number in it comes out
of the parent programme or off a reference episode rather than being designed.

The shape, measured off a reference the client supplied:

```
hook (the strongest soundbite, cold, pulled from INSIDE the clip)
  → series card + jingle
  → the whole clip           [identity overlay, then question overlay]
  → the official end card + jingle
```

Captions are burned in over both picture sections and **never over a card**. The hook repeats later in context,
deliberately — the viewer hears it twice.

---

## Where a short comes from

🔴 **It must come from inside the approved programme, not from the rushes.** When mining a transcript for
candidates, **intersect every one with the kept ranges of the edit.** Two of the strongest candidates on one
interview — a privacy/consent answer and a partnership answer — sat in ranges the cut drops. **A short taken
from material that is not in the published episode does not match the video it drives traffic to.**

⚠️ **The review document's "key soundbite" is not automatically the hook.** On one clip it was **18.3 s of a
25.4 s short**, so using it cold would have replayed almost the whole piece before the title card. Use the
payoff line as the hook and keep the long quotation for post copy, where length costs nothing.

⚠️ **Check the answer actually ends where you think.** One short was extended from 11:56.75 to **12:36.12**
because her answer genuinely ends there — the next line was the **host**, not her. Read the speaker labels, not
the sentence rhythm.

And note what a length change costs: that extension took the short from 34 s to **74.3 s**, more than double the
others. Still within the platform ceilings, and no longer a scroll-stopper length. **Say that out loud when the
operator asks for the extension.**

---

## One plan file, and nothing recomputes a boundary

```bash
python3 scripts/measure_edit_points.py          # where the words really start and stop
python3 scripts/measure_framing.py --short 1    # where she is, and the 9:16 window
python3 scripts/framing_candidates.py --short 1 --cx 1252   # judge the window on real frames
python3 scripts/plan_short.py --short 1         # -> out/short1_plan.json, the one truth
python3 scripts/align_words.py --short 1        # forced alignment -> per-short word times
python3 scripts/make_card.py --short 1          # the intro card
python3 scripts/make_captions.py --short 1      # one RGBA layer per cue
python3 scripts/measure_lower_thirds.py         # the reference's identity + question overlays
python3 scripts/make_lower_thirds.py --short 1
python3 scripts/build_audio_short.py --short 1  # dialogue slice + jingle, -14 LUFS
python3 scripts/render_preview.py --short 1     # ffmpeg PREVIEW — checks the edit only
python3 scripts/verify_short.py --short 1 --render out/preview/...
python3 scripts/build_resolve_short.py --short 1 --version 1
```

(That is the real per-project pipeline for a set of shorts; the generic tools it leans on —
`build_subtitles.py`, `conform_graphic.py`, `nle_ir.py` — live in this skill's `scripts/`.)

### Building the same short in Premiere instead

The last line above is the only Resolve-specific step. Everything before it is `python3` + `ffmpeg`,
so a Premiere-only editor runs the identical pipeline and swaps the final command for a translation
of the same authoritative plan:

```bash
python3 scripts/emit_ir.py --short 1 --gate \
    --pregraded out/graded/<graded-intermediate>.mov --pregraded-base <first source frame> \
    --preset assets/presets/<raster>_<rate>.epr \
    --out out/short1_ir_premiere.json
python3 <skill>/scripts/premiere_bridge.py --build out/short1_ir_premiere.json
```

🔴 **The grade has to be rendered into the media first.** Premiere can script neither a CDL nor a
LUT, so `--pregraded` points at an intermediate carrying the grade already, and `--pregraded-base`
says which source frame it starts on so every `src_in` is rebased. Get that second argument wrong and
every clip is off by thousands of frames — and the cut still plays, which is why the tool refuses to
run without it. Render the intermediate **full raster** so the reframe still happens on the timeline
and stays verifiable there.

This was done for real on short 1 and the two renders compared: **mean 0.83 codes of 255** against
the shipped Resolve master, with the graphics-only frames at 0.01–0.06 codes. See tutorial 6 and
`references/premiere-uxp.md`.

✅ **`out/short<N>_plan.json` is authoritative for every frame boundary, and nothing downstream recomputes
one.** The audio build, the caption cues, the timeline and the verifier all read it. That is deliberate: two
implementations deriving the same number is how a 13% render error stayed put for eight builds on the sibling
project (tutorial 7, D4).

The plan's own rules, applied there and nowhere else:

| | |
|---|---|
| **pre-air** | a small number of frames of room tone before the first word, capped by the measured silence actually available |
| **post-air** | frames after the last word, capped so the cut lands **before the next speaker's onset** — a frame containing someone starting to talk is a clipped word, not a tail |
| **hook** | measured boundaries, its own air; tighter pre-air (a hook should start on the word) and looser post-air (the line must land before the card takes over) |
| **card** | an explicit frame count, **not** derived from the jingle |
| **end card** | **measured off the asset.** Never hardcoded — drop a different cut in and the total re-fits |

And take the frame *containing* a time by **floor, not round** — rounding up starts the clip after the onset and
clips the attack.

---

## Framing: a static vertical crop of a seated subject may not exist

A 9:16 window out of a 16:9 4K frame is **1215 px of 3840** at full height. Scaled to 1080×1920 that is a
**0.889 DOWNSCALE**, so there is no upscaling and the softness question does not arise.

The real constraint is movement. On one interview the subject's head swayed **588 px** and her head's full
extent spanned **1051 px** of the 1215 px window — so **no static window held her with margin for the whole
clip.**

Do not abandon a centred frame for that. **Set an excursion budget in pixels *and* seconds, and take the crop
nearest the compositional ideal that stays inside it.** The budget has to be set by rendering the extremes and
looking: at **48 px** the hair kisses the edge and reads as a tight portrait; at **152 px** the cheek is visibly
cut.

Also check **what the subject is doing during the excursion.** Hers happened while she leaned forward and looked
*down*, so the "leave room in front of the gaze" rule did not apply at that instant and the centred frame won on
the merits — face at **51.7%** of frame width against the reference episode's own subject at **50.6%**. The
gaze-correct alternative cut into her cheek and ear at that lean; the only fully safe window put her at **62%**,
the wrong side of frame for someone looking screen right. **Both were rendered and looked at.**

⚠️ **PROBE DENSITY MUST SCALE WITH THE SPAN, or the measurement lies.** A crop chosen from **92 probes across a
23 s clip** was reused when that clip was extended to **62 s** — the same 92 probes then being one every 0.68 s.
It reported an excursion of **22 px**; at **4 probes per second** (249 probes) the real envelope appears and the
correct window is **20 px away**, because the sparse pass had **missed her furthest lean entirely.** The sparse
answer would have clipped her head by ~**68 px.**

- Use a **rate**: `max(floor, span_s * per_second)`.
- Treat any excursion reported as "1 of N probes" as **unmeasured** rather than small — it is one sample.
- ✅ **Re-measure framing whenever the in/out points move. A crop is a property of the span, not of the shot.**
- Keep the rejected value in the data with the reason, as `known_bad`.

⚠️ **Measure head extent; never scale the face box.** A symmetric `face_cx ± 1.55·face_w/2` reported that
1051 px union as **1152 px** — an impossible framing — because the skin centre sat a consistent **77 px left** of
the face-box centre. Measured skin width / face width was **1.47**.

⚠️ **A crop measured on one take does not transfer to another.** A CONFIRMED crop from the same shoot, same
camera, same subject was measured on a **standing** take and was **700 px wrong** for the *seated* interview.
This is the conditional-durable trap: record the configuration the value depends on.

---

## The cards: read the title, do not copy it

✅ **The short's title card carries the parent programme's on-screen title, read from the project that owns it**
— not a per-short copy. Two copies of a string are two answers and the divergence is silent: one gets edited and
the other ships with last week's wording. Keep the per-short field as provenance, explicitly marked as no longer
used, because deleting it would lose the record of what the card used to say.

**Predictable side effect, worth stating before anyone reports it as a bug: the shorts' intro cards become
byte-identical** (136553 bytes each on one job). Same episode, same title. That is the invariant working.

### Reproducing a card family across two rasters

🔴 **MEASURED ON ONE SERIES: THE 9:16 CARD IS THE 16:9 CARD'S ELEMENTS AT IDENTICAL PIXEL SIZE, RE-CENTRED AND
STACKED.** Not scaled — identical.

| element | 9:16 | 16:9 |
|---|---|---|
| programme wordmark | 330 × 36 | 330 × 36 |
| series line 1 | 497 × 69 | 497 × 70 |
| series line 2 | 258 × 72 | 258 × 72 |
| headline lines | 770 / 615 wide, 88–89 ink | 770 / 615 wide, 88 ink |
| logo lockup | 609 × 125 | 609 × 125 |

So the vertical card is a **re-arrangement, not a re-typesetting**: the same type sizes, a different stack order,
and one element (a horizontal rule) that the landscape version renders vertically. Rebuilding it as "the
landscape card at 56%" would have got every size wrong.

⚠️ **Check whether yours already matches before rebuilding it.** Asked to move a landscape card onto a new
template, the honest first step is to measure the card you already have. On one project it **already matched** —
type size exact, pitch 120 both, lockup gap 112 against 113, left panel within 1 px, background already the
identical flat colour. The only difference was that the reference's headline ran to three lines and ours to two,
which is a property of the title's length, not of the template. **A whole rebuild saved by one measurement.**

⚠️ **NEVER COMPARE INK HEIGHTS OF DIFFERENT STRINGS WHEN FITTING A SIZE.** Our headline measured **66 px** of
ink and the reference's **88**, which reads as "their type is 33% bigger" and is false: every one of their lines
contains a descender and ours did not. **Fit a size by rendering the reference's own strings in your face** —
that settled it in one command, with all three of their lines reproducing at exactly their ink heights and
widths within 0.9%.

⚠️ **A background that looks like a gradient may be flat.** The reference cards read as a subtle vertical
gradient and measure **flat** — RGB(22, 29, 38), sd 0.45 across the whole field (PNG dither), **one code** of
drift top to bottom, in both rasters. Sample a strip clear of ink at several heights before modelling a ramp
that is not there. Worth checking your own artwork the same way: on that project the landscape card was already
that exact flat colour while the vertical one carried a real gradient, because its bed had been lifted from a
reference **video** rather than a card.

### 🔴 Two caps on one fit, and the one that binds is not the one you meant

A partner wordmark was fitted `max_w=430, max_h=73`. The height was the intended target, taken from the
reference. The width cap had been copied from the reference's own partner mark — a **compact** logo — and this
one is an **8.3:1 logotype**, so **width bound first and the mark landed at 430 × 52, never reaching its 73 px
target.** It shipped at **71% of the intended height across every deliverable**, and the reviewer could see
something was wrong without being able to name it.

✅ **State which cap you expect to bind, and assert it.** If the height is the target, the width cap exists only
as a safety rail and should be set where it cannot engage — or the fit should fail loudly when it does.

⚠️ **A wide logotype cannot be height-matched to a compact one** in the first place: an 8.31:1 wordmark set to
the reference partner's *height* made the lockup **85% of frame width**. Width-limit it into the reference's own
lockup envelope instead. And prefer lifting a brand mark from your own official card and **downscaling** over
upscaling a small asset — then cross-check against the reference's own copy of that mark, which should agree to
a few pixels if it is the same artwork.

⚠️ **Hand-set references give you a RANGE, not a formula.** Three cards from the same designer put the partner
mark at **0.72, 0.80 and 0.84** of the programme mark's lockup height. That 15% spread is not noise to be
averaged away — it is the designer's eye responding to each logo. Ours sat at **0.515**, outside the range in all
three cases, which **is** the finding; *"the correct ratio is 0.78"* would not have been. And **equal height is
not equal weight**: the three reference marks had an ink density of 0.295–0.32 where ours was **0.586**, nearly
twice as dense, which argues for the low end of the range. Ink *area* is not the invariant either — it spread
**77%** across the three references against height's 15%. **Use the range to bound the answer and put the final
call in front of a person.**

✅ **Confirm a swapped brand mark in the RENDER, not in the code path.** When an official mark replaced an
earlier cut, the ink was isolated out of the finished frames and correlated against both source files: 0.9998–0.9999
against the official against 0.9994–0.9995 against the superseded, with ink aspect nearer the official file's
8.289 than the old 8.308. ⚠️ The two files are near-identical artwork, **so a high absolute correlation proves
nothing on its own — which of the two is HIGHER is the discriminator.**

---

## Burned-in captions: match the style by measurement

Timings come from the aligner, text from the recogniser (tutorial 5 §9). Forced alignment moved the recogniser
by a **median +70 ms with 75% of onsets more than a frame out**, which is why burned-in captions need it.

Shape for the raster: at 1080 px vertical, a legible phone size runs out of room around **30 characters**, and a
third line reaches into the lower third where the platform puts its own UI.

**Reproducing a supplied caption style — width finds the size, STROKE finds the weight.** This extends "width
solves the size, ink height picks the family". Width could **not** separate Regular from Bold: −0.8% vs +1.4%
against the reference. **Median horizontal stem run length** did, cleanly:

| | reference | Regular | Bold |
|---|---|---|---|
| median stem run | **8.0 px** | 8.0 | 12.0 |
| ink coverage | 0.334 | 0.313 | 0.446 |

Ink coverage agrees but is the weaker signal, because compression blur inflates it.

**Infer the block's anchor from cues of different line counts.** Comparing 2-line and 3-line cues showed fixed
line slots at a **92 px pitch** with the block growing *upward* — **bottom-anchored, with the last baseline
constant.** A single example cannot reveal that, and guessing it wrong moves every short cue.

Two more things to measure rather than assume: whether the plate is drawn **only where there is text** (on the
reference it vanished entirely in the gaps between cues — check a caption-free frame), and the plate's opacity,
read off a **bright** background feature crossing its edge. Note that a plate which looks strong over a bright
arm is nearly invisible over dark clothing at the same alpha; that is the subject, not an error.

⚠️ **Where a cue is split on CAPACITY rather than on a pause, the speech is continuous**, so its last word lands
exactly on the next cue's first word and you get cues whose out-time equals the next in-time to the millisecond.
Players tolerate that and it flickers. Let the ceiling win — trimming two frames off a continuous phrase costs
nothing readable.

⚠️ **Verbatim captions keep false starts.** If the reference video is verbatim too, leave them and **ask**. It
is on-screen copy going to a public platform, so it is the operator's call, not the build's.

---

## The two faded overlays inside the body

The format carries more than the cards. Shortly after the series card clears, the speaker's **identity** (name /
role / company) fades in and out, and then a label for **the question being answered** fades in. Plain white,
left-aligned, **no plate**, below the caption band. All of it **measured off a reference episode** rather than
designed:

| beat | measured | |
|---|---|---|
| gap after the card | 0.96 s | "shortly after" |
| **identity** | **3.92 s** | 0.20 s fade each way, the rest held |
| gap | 1.32 s | |
| **question label** | **4.80 s** | 0.16 s fade each way |

⚠️ **The durations you are given are hints, exactly like the timecodes.** "Approx 4 seconds" was right;
"roughly 6 seconds" was **4.80**.

✅ **And once the operator says *"we'll always want to do this"*, make the tool EXIT when the text is missing**
rather than building a short without it. On one job two further shorts became unbuildable until someone wrote
their question text — which is the correct outcome, and much better than discovering months later that half the
set has the feature and half does not.

**The question label must not restate the title card.** The card labels the *answer*; the blurb labels the
*question*. One short's options deliberately avoided the card's own key word for that reason.

### Placing type by BASELINE

⚠️ **ASCENDER IS NOT CAP HEIGHT.** A 14-character name measured **84 px** of ink where `HXM` measured **79** —
the `d` ascender is 5 px taller than a capital. And lines descend by different amounts: a comma, a `g`, and a
word with neither are three different offsets, so a block placed by ink-top **jitters line to line as the text
changes.** Ask the **font** where the baseline is (PIL's `anchor="ls"`), never the cropped bitmap's height.

⚠️ **A bbox taken from a supersampled face is in supersampled pixels.** Where text is rendered at `size × SS`
and downsampled, a raw `textbbox` puts every line **SS times** as far from its baseline as intended. Divide at
the point of use.

**When your text needs a line the reference does not have, derive the pitch from a rule that reproduces the ones
it does.** A long role title would not fit the reference's measure and wrapped to two lines, making a four-line
block out of a three-line pattern. Expressing the layout as ink **GAPS** — 48 px after the name, 24 px between
body lines — reproduced **both** of the reference's observed baseline pitches (106 and 82) exactly, which is what
earns the right to use the same rule for the new one. The fourth line is then not a free parameter.

✅ **Bottom-anchor the block so it grows upward**, and assert the clearance: on one job the four-line block
reached y=1344, still **142 px clear** of the lowest caption plate at y=1205 — asserted rather than assumed.

Two judgement calls worth stating, because both went against the instinct:

- ⚠️ **Do not abbreviate to fit until you have checked the abbreviation fits.** It did not — the house
  abbreviation would not fit on one line either — so wrapping kept the information at no cost. *"The same
  wording as the long-form video"* is usually the operator's actual requirement.
- ⚠️ **A template's own inconsistency is a measurement.** The reference's two overlays sit at left insets
  **47 px apart**. They are never on screen together, so it is invisible; reproduce it rather than tidying it
  into something the house does not actually do.

### Build them as one alpha track, and verify absence as well as presence

✅ **Reuse the one alpha-track mechanism, and express the fades as discrete per-frame alpha steps.** A second
builder for "but this one fades" would be a second thing that can be one frame wrong without saying so. The
proven concat-with-explicit-durations path takes each fade frame as a one-frame entry unchanged.

Put them on their **own track** rather than compositing into the caption track when the caption artefact is
already in an approved deliverable — they never share a row of pixels, and two artefacts verified independently
stay independently verifiable.

✅ **Assert the overlay is ABSENT where it should be, not only present where it should be.** The gap between the
two elements is exactly where a fade that never reaches zero, or a one-frame overlap, becomes visible — and a
presence-only check passes on both faults.

⚠️ **VALIDATE A DETECTION FLOOR ON YOUR OWN FOOTAGE BEFORE TRUSTING IT.** The white-ink coverage threshold that
cleanly separated three elements on the reference had to be re-checked against our own material, where the
background and wardrobe differ. Across every frame of a render with **no** overlays the band peaked at
**0.00103** against a **0.0015** floor — a **1.46× margin, thin** — while an overlay reads **0.08–0.10**, a
65–100× separation. **Know which of the two margins is the tight one before quoting either.**

⚠️ **Check for a scrim, and record its ABSENCE as a finding.** There is no gradient behind these overlays on the
reference — the bottom band's luma is flat on overlay-free frames (94 → 108 across y 1200–1920) — so plain white
text is the treatment. It was legible here **only because the guest wore black against a mid background.** On a
light shirt against a light wall this needs revisiting, which makes it a **wardrobe and lighting note as much as
a graphics one.**

---

## Preview, verify, then render out of the NLE

- **The ffmpeg preview checks the EDIT, not the picture**, and its filename should say so. It exists so the cut
  can be checked when the NLE is unavailable, and as the cross-check the build diffs itself against.
- ✅ **The preview must composite everything the deliverable does.** Take the overlay track from the build rather
  than rebuilding it, so the preview cannot disagree with the deliverable about when the overlays appear. **A
  preview missing part of the edit is not a preview of the edit**, which is that file's only justification for
  existing.
- ✅ **The verifier's job is to assert the artefacts agree with EACH OTHER**, which is the failure no single
  script can see. One shorts verifier runs **120 checks** on the artefacts and **140** with `--render`, and was
  **negative-tested four ways** — a 1 px crop disagreement, a 1-frame gap in the plan, a cue dragged onto a card,
  and an out-point extended over the next speaker's onset. All four caught, clean again after restoring. A later
  version reached 435 checks and was negative-tested five ways for the overlays.
- **Render the deliverable out of the NLE.** On one build the ffmpeg path agreed with the NLE on geometry to
  r ≥ 0.9989 and was **still 14–18% out in the grade** — until both paths applied the log-fitted CDL *before* the
  vendor LUT, after which they agreed to a mean 1.3 codes of 255 and r = 0.9996. So the divergence was a
  **CDL-order bug, not an ffmpeg fact** — and the rule still stands, because now the diff is a real cross-check
  rather than a formality.
- **Regenerate graphics first, build second.** Overwriting a media file while the built project is open wedges
  the next render that touches it: no file, no error.

Delivery for social: 1080×1920 H.264, ~**38 Mbps**, AAC 320 kbps 48 kHz stereo, **−14.0 LUFS**, comfortably
inside the platforms' size and length limits. The headroom is there to survive their re-encode on ingest rather
than to be seen directly.

**One encode per short**, and delete the superseded ones in the same breath. And if a folder is no longer part of
the pipeline, *"stop saving there"* is not *"delete what is there"* — a synced folder may be shared with someone.
Ask.

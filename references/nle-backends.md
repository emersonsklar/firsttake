# Two NLEs, one cut

The post pipeline here builds a project **by script** and hands it over assembled. That started
against DaVinci Resolve. It now also targets Premiere Pro, because the person who shot the footage
is not always the person who finishes it, and an edit that only exists in one application's dialect
is an edit that has to be rebuilt by hand to move.

**Read this before either `resolve-api.md` or `premiere-uxp.md`.** Those two are dialects. This is
what they are dialects *of*.

---

## The honest answer about "100% compatibility"

Asked for feature-for-feature parity, the true answer has two halves, and conflating them is how a
pipeline quietly ships two different cuts:

- ✅ **Output parity is achievable.** Both applications can be driven to produce the same programme:
  the same clips, from the same source frames, at the same timeline positions, on the same lanes,
  with the same reframes, fades, animation, burned-in graphics and delivery encode.
- ❌ **Mechanism parity is not, and pretending otherwise is the danger.** The two disagree about
  which operations exist. Some things Resolve cannot script that Premiere can (keyframes,
  transitions, writing the frame rate). And things Premiere cannot script at all that Resolve can:
  **colour — neither a CDL nor a LUT** — and a retime. Neither list is going to shrink to nothing.

So parity is defined by a **shared intermediate representation**, not by either application's
feature list, and enforced by a gate that refuses to approximate:

```
     planners  ──►  timeline IR (JSON)  ──┬──►  Resolve backend   (Python, live API)
    (measured                             │
     numbers)                             └──►  Premiere backend  (UXP panel + job folder)
                                          │
                                   capability gate
                          "this backend cannot do this EXACTLY — stop"
```

🔴 **The rule the whole design exists to enforce: a backend that cannot do something must FAIL,
never approximate.** The expensive failure on this kind of pipeline has never been *unsupported*. It
is a build that reports success having quietly dropped the ease on a fade, flattened a keyframed
push-in to its first value, or rendered the vendor LUT without the camera-match grade in front of
it. All three of those look like a finished timeline.

`nle_ir.CAPABILITIES` therefore carries three states, not two — `True`, `False`, and
`"UNVERIFIED"` — and **`UNVERIFIED` blocks by default.** Passing `--allow-unverified` is how you
say out loud that you are building on something unproven.

---

## What each backend can do

`UNVERIFIED` means *the API declares it and nobody here has watched it work yet* — run the probe
below and promote them one at a time, as each is actually seen to work end to end.

✅ was earned by rebuilding a real 2228-frame vertical short in Premiere from the same plan that
built the shipped Resolve master, rendering it, and comparing frames: **mean 0.83 codes of 255**,
correlation ≥ 0.998, and the graphics-only frames (no crop, no grade) at **0.01–0.06 codes**, which
is what proves the comparison was frame-aligned rather than merely plausible.

| IR feature | Resolve | Premiere | Notes |
|---|---|---|---|
| place a clip at a frame on a lane | ✅ | ✅ | out point is EXCLUSIVE in both.  Resolve batches; Premiere must set in/out on the ProjectItem then place, one clip at a time |
| crop / reframe | ✅ | ✅ | read back 88.88888549 against 88.888889 asked.  both derived from the crop rectangle in source pixels, never from each other |
| linear fade | ✅ | `UNVERIFIED` | Resolve `SetFades` (dict form only); Premiere opacity keyframes |
| **shaped** fade | ❌ | `UNVERIFIED` | Resolve's `SetFades` is linear only — a shaped fade there needs a Fusion comp |
| keyframed animation | ⚠️ Fusion comp | `UNVERIFIED` native | Resolve has **no** keyframe API on a timeline item; Premiere has `ComponentParam` keyframes |
| transitions | ⚠️ Fusion comp | `UNVERIFIED` native | Resolve's `AddTransition` returned `None` for all 7 signatures tried |
| disable one clip | ✅ | `UNVERIFIED` | in Resolve use `SetClipEnabled`; `SetTrackEnable` returns `True` and does nothing |
| full-length alpha graphics track | ✅ | ✅ |  the portable answer for titles and captions in both |
| ASC CDL | ✅ | 🔴 **no** | pre-grade the media; there is no scripted colour in Premiere at all |
| LUT | ✅ | 🔴 **no** | Resolve: only from inside its own library. Premiere: **also impossible** — `createSetInputLUTIDAction` wants an opaque ID, and Lumetri's 130 parameters include no string-typed one |
| retime / speed | ✅ | 🔴 **no** | no setter exists; retime the media first |
| set the timeline frame rate | ⚠️ preset only | ✅ writable, and EXACT | Resolve refuses every format; Premiere exposes `ticksPerFrame` as an integer |
| render | ✅ | ✅ |  both: pass the "whole programme" flag explicitly, because in/out marks persist |

### Read this row twice

**Colour.** A per-camera CDL is the preferred camera match here — four readable numbers, adjustable
by a human, surviving a round trip through other software. **Premiere can script neither a CDL nor a
LUT**, so a plan carrying either is rejected, and baking the CDL into a cube is not enough on its own
because the cube cannot be attached either.

The only route is to **render the grade into the media** and declare it in the plan's `baked` list.
Done once for real: the A-camera's own chain (CDL cube then vendor log→709 cube, both tetrahedral —
the same chain the shipped master used) rendered to a full-raster intermediate, so the reframe still
happens on the timeline and stays verifiable there. What you lose is a colourist's ability to nudge
four numbers at the far end, which is exactly why `HANDOVER.md` states it.

---

## Why the IR stores a crop rectangle

Because Zoom/Pan/Tilt is one application's parameterisation, and putting it in the shared artefact
bakes that application into the plan. The IR stores the primitive fact — the crop window in source
pixels, plus both rasters — and each backend derives its own numbers in one unit-tested place.

The payoff is a cross-check that neither application can give you alone:

> **Resolve's Pan, divided by the timeline width, equals Premiere's normalised Position offset —
> exactly, on both axes, at any crop aspect ratio.**

Two independent parameterisations of the same intent must agree. That invariant is asserted over
five crops in `nle_ir.py --selftest`, and it caught a real bug in the vertical axis within one run
of being written. Resolve's own version of this — a scale factor shipped as `1.0`, correct at exactly
2× zoom and wrong everywhere else — survived the first reframe anyone tested, because there was
nothing to disagree with it.

This is the general lesson, and it is worth more than the geometry: **a cross-check must derive the
same quantity two independent ways and assert agreement.** A tripwire that only asks "did this
change?" held a 13% render error steady across eight builds on this project.

---

## Three workflows, all supported

### 1. Premiere only — someone who has never installed Resolve

✅ **Nothing upstream of the timeline needs Resolve.** Everything that measures, decides or
generates is `python3` + `ffmpeg` + `numpy`: footage and audio QC, verified offload, syncing angles
that share no clock, drift measurement, per-word speaker attribution, white balance and per-clip
LUT fitting, log→view LUTs, speed ramps with real motion blur, graphic conforming, forced-aligned
subtitles, day sheets. Read `post-production.md` straight through — every section above
"Assembling the project" applies unchanged.

Two things a Premiere-only user should do differently:

- **Take the grade already rendered into the media.** Premiere can attach neither a CDL nor a cube
  from a script, so the correction has to be baked in before the timeline. Fit it in log, compose it
  with the vendor transform, render a full-raster intermediate, and declare it `baked`. Same picture,
  one fewer adjustable knob at the far end.
- **Retime before the timeline, not on it.** There is no speed setter. `speed_ramp.py` produces an
  exact-length retimed file with real motion blur, which is a better answer than a timeline
  percentage anyway.

Everything else is the same commands with `--backend premiere`.

### 2. Resolve only

Unchanged. The IR and the gate are additions, not a migration; the Resolve backend still drives the
Python API directly. The one new habit worth picking up is running
`nle_ir.py --compare plan.json` before promising anybody a handover, so you learn what the other
application will refuse *before* you have offered.

### 3. Mixed — start in one, finish in the other

This is what `nle_handover.py` is for, and the important design decision is that **the handover
artefact is the plan, not the project file.**

```bash
# sending, after cutting in Resolve
python3 nle_ir.py --compare plan.json                 # what will the far end refuse?
python3 nle_handover.py --pack plan.json --to ~/handover/cut-v7 --from-backend resolve

# receiving, with only Premiere
python3 nle_handover.py --verify ~/handover/cut-v7 --backend premiere
python3 premiere_bridge.py --probe                    # once, on this machine
python3 premiere_bridge.py --build ~/handover/cut-v7/plan.json
```

`--pack` writes a manifest, a copy of the plan, and a `HANDOVER.md` that states in plain language
what the receiving application will build exactly, what it will refuse, and what has been baked
into the media and is therefore no longer adjustable. `--verify` is run by the *receiver* and
refuses to let them start on a package that is missing a file or whose media has changed under it.

⚠️ **`--pack` does not copy media by default**, because one file per deliverable is the rule here
and a package duplicating a folder of masters creates a second thing that can go stale and be
published by mistake. Pass `--copy` when it genuinely has to cross a machine boundary — and note
that copy mode reads every copy back and compares it, because a copy is not a copy until it has
been read back.

#### 🔴 The thing that does not travel

**Only what the plan describes is reproducible.** Manual work done inside either application — a
stabilisation pass, a hand grade, a comp somebody drew — is not in the plan and will not appear at
the far end. Neither application lets a script read those back out reliably.

So do not hope. **Render it into the media and declare it.** That is already the established
pattern for stabilisation, which is not scriptable in Resolve at all (measured: it appears in none
of the 32 properties `GetProperty()` returns, and `SetProperty` refuses all twelve plausible key
names) and is a manual effect in Premiere too:

> script a scratch project with the clip at 1× and ungraded → the operator runs the stabiliser in
> the UI → script the export, rendered **ungraded and full-raster** → the normal colour and reframe
> chain then applies to that new source exactly once.

The output is a new source file, which travels perfectly. Put an entry in the plan's `baked` list
saying what was done and why, and it appears in `HANDOVER.md` under *"Baked in, and therefore NOT
adjustable at your end"*. If that list is empty, `--verify` says so as a NOTE rather than staying
quiet — an empty list is much more often an omission than a fact.

---

## Running the Premiere backend

### Once, per machine

1. Enable developer mode for UXP plugins in Premiere's preferences.
2. Load `scripts/premiere/uxp/` as an unpackaged plugin (the UXP Developer Tool's *Add Plugin* →
   pick its `manifest.json`, then *Load*).
3. Open the panel: **Window → Extensions → film-shoot timeline**.
4. If the panel reports no native `fs`, press **Job folder…** and pick
   `~/.film-shoot/premiere-jobs` (create it first). With `localFileSystem: "fullAccess"` granted it
   usually finds real paths by itself.

### Every time

```bash
# 1. establish the facts. NOT optional.
python3 scripts/premiere_bridge.py --probe --out probe.json

# 2. gate the plan before it goes anywhere
python3 scripts/nle_ir.py --validate plan.json --backend premiere

# 3. build, then read the timeline back and diff it against the plan
python3 scripts/premiere_bridge.py --build plan.json
python3 scripts/premiere_bridge.py --readback --with-components --out seen.json
```

The probe wants **one clip sitting on V1 of an open sequence**, because that is the only way to
learn this build's intrinsic component and parameter names. Without it the probe reports
`INCOMPLETE` and says so rather than passing with a gap.

### The probe's job, concretely

It replaces five guesses with five measurements: ticks per second; whether an exact 29.97 survives
`FrameRate`; every video filter's match name; every component and parameter on a real clip, by
display name; and whether `getVideoTrack()` is 0- or 1-based. A sixth, `probe_placement`, writes
into a scratch sequence to settle whether the out point is inclusive or exclusive — worth the one
disposable sequence, because guessing it makes every clip exactly one frame short, which survives
casual review and destroys frame-accurate graphics.

---

## What does not change when the NLE does

These came out of Resolve work and are not about Resolve. They cost time in both dialects.

- **A setter that returns without complaint is not evidence the setting took.** Read it back and
  compare. Resolve's playback frame rate shipped at 24 on a 29.97 timeline — 0.8008× playback, about
  a fifth of the audio dropped — because the build asked, was refused, and did not look.
- **When a create call hands back a handle, the application may not have switched to it.** Assert
  the app's own idea of what is current before doing anything that depends on it. Resolve's
  `CreateProject` returned a valid object, listed it, imported media into it happily, and failed
  several stages later at `CreateEmptyTimeline()` — so the symptom appeared nowhere near the cause.
- **A plausible timeline is the failure mode.** Clips that *slid* still tile, so a no-gaps check
  cannot see them. Compare each item's start against the position it was asked for, item by item.
- **Cache rendered assets on their CONTENT, not their filename or length.** Two versions of that bug
  shipped here: keyed on frame count, then on frame count + filename. A graphic rebuilt in a
  corrected typeface has the same name and the same length and different pixels. Have the *verifier*
  recompute the hash from the artwork on disk and assert the timeline references it, then
  negative-test it against the previous version, where it must fail.
- **A derived cache of absolute frame numbers will outlive its truth.** A stale one rendered 33
  frames long while the build reported no problems.
- **Hash each export against the previous ones and fail loudly on a match.** A render came back
  byte-identical, reported Complete, after a setting had been changed — PSNR = inf on every frame.
  A setting that produced an identical file did not take.
- **A modal dialog blocks the entire plugin host, and no script can dismiss it.** On the Resolve
  side, setting a Fusion `Loader.Clip` popped one and locked a live session until a human cleared
  it. Assume the same of any UXP call that might want to ask something.
- **Anything absolute in a path will not resolve on the other machine.** That is half the reason a
  handover is an assembled project plus its media, not a project full of absolute references to
  someone's own scratch disk.

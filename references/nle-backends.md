# Two NLEs, one cut

The post pipeline here builds a project **by script** and hands it over assembled. That started
against DaVinci Resolve. It now also targets Premiere Pro, because the person who shot the footage
is not always the person who finishes it, and an edit that only exists in one application's dialect
is an edit that has to be rebuilt by hand to move.

**Read this before either `resolve-api.md` or `premiere-uxp.md`.** Those two are dialects. This is
what they are dialects *of*.

---

## ✅ Use DaVinci Resolve if you have a choice

Not a preference — a materially shorter path with less that can go wrong:

| | Resolve | Premiere |
|---|---|---|
| how a build is driven | a **Python module**, from a shell | a **UXP panel inside the app**, fed a job folder |
| manual steps per build | open the app | open the app and load the plugin (build logic lives in Python now, so a **reload is only needed when the panel itself changes**) |
| colour | scripted — CDL and LUT | 🔴 **not scriptable at all.** Pre-render the grade into the media |
| retime | scripted | 🔴 not scriptable. Retime the media first |
| transitions | Fusion comp | native — proven by measuring a render |
| animation, exact 29.97 | Fusion comp / committed preset | native, and exact |

Premiere is better at two things and worse at the ones that cost you time. If nobody is forcing the
choice, **Resolve finishes the job with fewer moving parts.**

## 💸 You do not have to buy anything to finish a video

The two backends above drive an application you own. Neither is required.

| what you have | what you get |
|---|---|
| **Resolve Studio** | everything: scripted build, scripted colour, editable timeline |
| **Premiere Pro** | scripted build and an editable timeline; the grade must be baked into the media first (early access) |
| **Resolve free** | ⚠️ probably no scripted build — see below. The ffmpeg route works |
| **nothing at all** | ✅ a full-quality master via `assemble_ffmpeg.py`, proven to 1.08 codes of 255 against a delivered programme. No editable timeline |

⚠️ **Resolve free: the scripting API is not the problem, external scripting probably is.** Contrary
to the usual folk wisdom, Blackmagic's own `Developer/Scripting/README.md` says the API "cover[s] a
common superset of functions for both the **Free and Studio** versions" — Studio-only *functions*
return False rather than being absent. But the preference that lets an outside script connect is
documented as a Studio one: *"**In DaVinci Resolve Studio**, Preferences > System > General, you can
configure: External scripting"*.

🔴 **That is UNVERIFIED and labelled so on purpose.** Suggestive wording in a README is not a
measurement, and it has not been tested on a free installation. Settle it in one command on a
machine that has one:

```bash
python3 scripts/resolve_connect.py --check     # prints the product name and whether connect worked
```

If that connects on free Resolve, external scripting is **not** Studio-gated, the note above is
wrong, and it should be corrected here. If it returns None on a running, unblocked free Resolve,
the answer is settled the other way.

**If a free-Resolve backend is ever needed, the shape is already known.** Internal scripting —
Workspace > Console, Workspace > Scripts, render scripts — is not described as Studio-gated
anywhere. That is exactly how the Premiere backend works: a runtime inside the application, taking
its orders from a job folder on disk. Premiere has no external scripting either, and
`premiere_bridge.py` is the transport to copy.

## Finishing with no NLE at all

Everything except the timeline assembly is already ffmpeg and needs no NLE, no licence and no
application: offload and verification, QC, sync, transcription and alignment, subtitles, loudness
mastering, LUT fitting, log→view LUTs, speed ramps with real motion blur, graphic conforming, and
the day sheets. The plan — the same IR both backends build from — is just JSON.

So the assembly can be done with ffmpeg too, and the result is a real deliverable.

**What you give up is the editable timeline, not the quality.** You cannot nudge a cut by hand
afterwards; you change the plan and re-assemble. For a scripted programme that is often no loss at
all.

### 🔴 The one condition, and it is not fussiness

**The grade must be baked into the media ONCE, before assembly — never re-applied during it.**

This is measured, and it was expensive. An ffmpeg pipeline built from the same edit structure agreed
with Resolve on geometry to **r ≥ 0.9989** and was still **14–18% out in the grade**. Eight review
renders that had already been approved were not showing what the NLE produced. Two implementations
of one colour transform will not agree, and nothing warns you — the picture looks fine.

So there must be exactly one colour pipeline:

```bash
# 1. compose the CDL into the camera's vendor LUT, so ONE transform does everything
python3 scripts/bake_cdl_into_lut.py --lut vendor.cube --cdl shot.cdl --out baked.cube --verify-key A

# 2. apply that single LUT once, producing a graded intermediate at FULL raster
#    (full raster, so the reframe still happens at assembly and stays verifiable there)

# 3. assemble the plan against the graded media, with ffmpeg doing no colour maths at all
```

Declare the result in the plan's `baked` list. That is the same discipline Premiere already needs,
because Premiere cannot script colour either — so this path is not a lesser sibling, it is the
majority case.

### ✅ It is a real backend now: `assemble_ffmpeg.py`

```bash
python3 scripts/assemble_ffmpeg.py --plan cut.json --out master.mov
python3 scripts/assemble_ffmpeg.py --selfcheck     # generate media, assemble, MEASURE the result
```

**Proven on a delivered programme.** A shipped 2228-frame vertical short was rebuilt from its own
plan and compared to the delivered master frame by frame: **mean absolute difference 1.08 codes of
255**, max 1.38, zero frames over 5. Part of that residual is the reference's own H.264 40 Mbps loss.

Supported: `clip`, `crop`, `fade_linear`, `alpha_track`, `clip_disable`, `render`.
Refused with reasons: `cdl`, `lut` (bake the grade first — see above), `fade_shaped`, `keyframes`,
`transition`, `speed`. The gate refuses them by name rather than approximating.

🔴 **Why the verification is bigger than the assembly.** The other two backends can be interrogated —
ask the application what it built. A file cannot be asked anything, so this is the backend that can
be wrong while looking completely right. `--selfcheck` builds sources whose **luma encodes the clip
index and the source frame number** and reads it back per frame, so the assertion is "source frame S
is at timeline frame T". It caught an in-point shifted by 2 frames — identical placement, identical
length, invisible to any count-based check.

⚠️ And one bug got past even that, which is worth knowing if you touch this: a stream-copy concat
leaves discontinuous timestamps, `overlay` aligns by timestamp, and one frame vanished at the first
boundary — a 94-frame card came out 93, everything after sat one frame early, the total was still
exactly right, and the file played perfectly. Only comparing against a known-good master showed it.

## ⚠️ Premiere support is EARLY ACCESS

It works — a real 2228-frame vertical short was rebuilt from the same plan that built the shipped
Resolve master and matched it to a mean **0.83 codes of 255** — but treat it as early access, and
expect to meet its edges:

- **Two capabilities are simply absent**: colour and retime. The gate refuses a plan that needs
  them rather than approximating, so you find out before a render, not after. (Transitions ARE
  supported — but note that nothing in the timeline can verify one, so their geometry is checked by
  rendering and measuring luma. See `PREMIERE-STATUS.md`.)
- **A failed build leaves a half-finished sequence.** Each clip is its own transaction and there is
  no rollback across a build, so delete the sequence and start again rather than inspecting a mix
  of two runs.
- **Anything not yet proven is refused by default.** `UNVERIFIED` blocks unless you pass
  `--allow-unverified` deliberately. That is the feature, not an obstacle. As of 2026-09-26 nothing
  is left UNVERIFIED for Premiere: every capability is either proven on a real timeline or marked
  absent, so a plan either builds exactly or is refused by name.
- **It has been run on exactly one machine and one version** — Premiere Pro 26.5.1.1 on macOS. Run
  `premiere_bridge.py --probe` on yours before trusting any of it; the probe exists because several
  of this API's behaviours were not what its own type declarations implied.

Resolve, by contrast, has driven every delivered programme on this project.

## ⚠️ CapCut is EARLY ACCESS: proven at 30 fps on one machine and one version

CapCut is free and is what a lot of people finishing a social cut already have open, so it is a
fourth backend: `capcut_draft.py`. It has **no scripting API at all** — no module, no plugin
runtime, no CLI — so the backend does not drive the application. It **writes the draft file**
(`draft_info.json`) into CapCut's projects folder, and CapCut opens it like any draft of its own.
The format is undocumented; it was learned from what CapCut writes and from the open-source
CapCutAPI / pyJianYingDraft projects, whose code is not used.

```bash
python3 scripts/capcut_draft.py --probe                    # app, version, and what its drafts look like
python3 scripts/capcut_draft.py --build plan.json
# open it in CapCut, let it save, close it — then read back what CapCut KEPT:
python3 scripts/capcut_draft.py --readback <name> --plan plan.json
# export by hand from CapCut's Export dialog, then measure the file:
python3 scripts/capcut_draft.py --check-export export.mp4 --plan plan.json
python3 scripts/capcut_draft.py --make-probe ~/capcut-probe     # the capability probe, see below
```

**Where it stands — CapCut 9.6.0, macOS, 2026-10-25.** The probe below was built, opened, saved by
CapCut, read back unchanged, exported at 1080×1920 30/1 and measured on every frame:

| | CapCut | evidence |
|---|---|---|
| place a clip, in-points | ✅ | all 120 frames on their planted source frame, residual 0.25 codes |
| crop / reframe | ✅ | the cropped band and none of its neighbours; numbers read back to every digit |
| linear fade | ✅ | within 0.03 opacity of the Premiere backend's ramp on every frame |
| disable one clip | ✅ | black for all 20 frames |
| alpha graphics — **PNG** | ✅ | an opaque RGBA PNG strip drew at exactly 235 on all 120 frames |
| alpha graphics — **ProRes 4444** | 🔴 refused | placed and faded exactly, but drew at luma **191 where 235 was written**, untagged and tagged bt709 alike |
| 29.97, 23.976, 59.94 | 🔴 no | re-timed to a whole rate on save — see below |

A capability is promoted only with **both** halves: the readback (what CapCut kept) and the
measured export (what it draws). A readback of a draft CapCut has never saved reads back this
tool's own file, and says so as a NOTE rather than a pass. If a CapCut build stores drafts
encrypted — some CapCut and JianYing versions have — nothing can be read back and `--probe` says so.

🔴 **CapCut does not keep 29.97 — measured on 9.6.0.** A draft written at 30000/1001 opened and
played fine, and was saved back at **30.0 with every cut re-snapped to the 30 fps grid**: 150-frame
clips came back 151, in-points moved a frame, the audio picked up a one-frame retime, and the fade
keyframes were *not* moved, so opacity and picture disagreed. It looked right. Only `--readback`
saw it. The gate therefore refuses a fractional rate (`fractional_fps`); conform to 30 first.

**Proving it draws what the plan means.** A readback proves CapCut kept the numbers, not that it
draws them as the IR intends. `--make-probe DIR` writes a 30 fps plan whose media encodes its own
frame number as luma, with a crop that has to move the picture, a disabled clip and a fading alpha
card. Build it, export it from CapCut, and `--check-export` asserts *source frame S is at timeline
frame T* on every frame, after fitting out any range conversion. It measures the alpha card's
opaque level separately from its ramp, so a colour shift is reported as colour and not as fifty
frames of wrong timing — which is what it did before it knew to.

What the format forces, and what the gate therefore refuses:

- **Time is microseconds, truncated.** That is CapCut's own convention, measured: it saves frame 80
  at 30 fps as 2666666. Each boundary comes from its absolute frame number so nothing drifts, and
  source and timeline are converted independently, as CapCut does — so their durations can differ
  by 1 µs at speed 1.0. That is not a retime; a speed other than 1, or a source that ends on the
  wrong frame, is.
- **The bottom video track is magnetic.** CapCut forces its clips to start at 0 and close up, so a
  gap there is refused (`main_track_gap`). Put a clip of black in the plan instead.
- **Picture clips are written silent.** The plan's audio is on its audio tracks; otherwise the
  camera sound would play under the mix.
- **Titles as PNG, not ProRes 4444.** The 4444 card is drawn about 19% too dark — every title's
  colour wrong while its placement and fade are perfect, which is the failure that looks finished.
  `--build` refuses a ProRes graphic by name. The probe keeps one, so re-running `--make-probe`
  on a newer CapCut re-measures it.
- **Refused by name:** CDL and LUT (bake the grade, as for Premiere), transitions (store effects
  that must be downloaded on that machine), keyframes, shaped fades, retime, and the render —
  CapCut exports only from its own dialog, which is why `--check-export` exists.

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

**Nothing is `UNVERIFIED` here any more.** Every Premiere row is either proven on a real timeline or
marked absent, so a plan either builds exactly or is refused by name. `UNVERIFIED` remains the
resting state for anything added in future, and it blocks by default.

Two kinds of evidence earned the ✅s, and they are different:

- **placement, crop, alpha tracks, render** — by rebuilding a real 2228-frame vertical short from the
  same plan that built the shipped Resolve master and comparing frames: mean **0.83 codes of 255**,
  correlation ≥ 0.998, graphics-only frames at **0.01–0.06 codes** (which is what proves the
  comparison was frame-aligned rather than merely plausible).
- **fades, animation, disable** — by a purpose-built synthetic timeline
  (`verify_premiere_backend.py`), because that short's fades are **rendered into its alpha tracks**
  and so exercised nothing. A capability test has to plant the thing it tests.

| IR feature | Resolve | Premiere | Notes |
|---|---|---|---|
| place a clip at a frame on a lane | ✅ | ✅ | out point is EXCLUSIVE in both.  Resolve batches; Premiere must set in/out on the ProjectItem then place, one clip at a time |
| crop / reframe | ✅ | ✅ | read back 88.88888549 against 88.888889 asked.  both derived from the crop rectangle in source pixels, never from each other |
| linear fade | ✅ | ✅ | keyframes landed at frames [0, 12, 71, 89] exactly.  Resolve `SetFades` (dict form only); Premiere opacity keyframes |
| **shaped** fade | ❌ | ✅ | interpolation read [5,5,5,5] vs the linear clip's [0,0,0,0].  Resolve's `SetFades` is linear only — a shaped fade there needs a Fusion comp |
| keyframed animation | ⚠️ Fusion comp | ✅ native | a Scale ramp 100→120 that moves.  Resolve has **no** keyframe API on a timeline item; Premiere has `ComponentParam` keyframes |
| transitions | ⚠️ Fusion comp | 🔴 **not implemented** |  Resolve's `AddTransition` returned `None` for all 7 signatures tried |
| disable one clip | ✅ | ✅ |  in Resolve use `SetClipEnabled`; `SetTrackEnable` returns `True` and does nothing |
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
3. Open the panel: **Window → Extensions → FirstTake timeline**.
4. If the panel reports no native `fs`, press **Job folder…** and pick
   `~/.firsttake/premiere-jobs` (create it first). With `localFileSystem: "fullAccess"` granted it
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

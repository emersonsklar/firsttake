# 6 — Assembling the timeline by script, in either NLE

You can hand over an **assembled project** rather than a folder of clips and a document describing what
you wanted. That is worth doing: the timeline is the product.

It also means a cut that exists only in one application's dialect has to be rebuilt by hand to move. So
the plan is the artefact, and the application is a backend.

```
     planners  ──►  timeline IR (JSON)  ──┬──►  Resolve backend   (Python, live API)
    (measured                             │
     numbers)                             └──►  Premiere backend  (UXP panel + job folder)
                                          │
                                   capability gate
                          "this backend cannot do this EXACTLY — stop"
```

**Read `references/nle-backends.md` before either dialect file.** Those two are dialects; that one is what
they are dialects *of*.

---

## The honest answer about parity

- ✅ **Output parity is achievable.** Both applications can be driven to produce the same programme: the
  same clips, from the same source frames, at the same timeline positions, on the same lanes, with the same
  reframes, fades, animation, burned-in graphics and delivery encode.
- ❌ **Mechanism parity is not, and pretending otherwise is the danger.** They disagree about which
  operations exist. Neither list will shrink to nothing.

🔴 **The rule the whole design exists to enforce: a backend that cannot do something must FAIL, never
approximate.** The expensive failure has never been *unsupported*. It is a build that reports success
having quietly dropped the ease on a fade, flattened a keyframed push-in to its first value, or rendered
the vendor LUT without the camera-match grade in front of it. **All three look like a finished timeline.**

`nle_ir.CAPABILITIES` therefore carries three states, not two — `True`, `False` and `"UNVERIFIED"` — and
**`UNVERIFIED` blocks by default.** `--allow-unverified` is how you say out loud that you are building on
something unproven.

---

## What each backend can do

| IR feature | Resolve | Premiere | Notes |
|---|---|---|---|
| place a clip at a frame on a lane | ✅ | `UNVERIFIED` | Resolve batches; Premiere must set in/out on the ProjectItem then place, one clip at a time |
| crop / reframe | ✅ | `UNVERIFIED` | both derived from the crop rectangle in source pixels, never from each other |
| linear fade | ✅ | `UNVERIFIED` | Resolve `SetFades` (dict form only); Premiere opacity keyframes |
| **shaped** fade | ❌ | `UNVERIFIED` | Resolve's `SetFades` is linear only — a shaped fade there needs a Fusion comp |
| keyframed animation | ⚠️ Fusion comp | `UNVERIFIED` native | Resolve has **no** keyframe API on a timeline item |
| transitions | ⚠️ Fusion comp | `UNVERIFIED` native | Resolve's `AddTransition` returned `None` for all 7 signatures tried |
| disable one clip | ✅ | `UNVERIFIED` | in Resolve use `SetClipEnabled`; `SetTrackEnable` returns `True` and does nothing |
| full-length alpha graphics track | ✅ | `UNVERIFIED` | the portable answer for titles and captions in both |
| ASC CDL | ✅ | 🔴 **no** | bake it into the LUT, or pre-grade the media |
| LUT | ✅ | 🔴 **no** | Resolve: only from inside its own library. Premiere declares an `InputLUTID` setter that wants an opaque **ID, not a path**, and nothing documents how to obtain one for a cube you generated — so `nle_ir` declares `lut: False` and the gate refuses. ⚠️ `references/nle-backends.md`'s own table still shows this row as `UNVERIFIED`; the code is the authority |
| retime / speed | ✅ | 🔴 **no** | no setter exists; retime the media first |
| set the timeline frame rate | ⚠️ preset only | `UNVERIFIED` ✅ writable | Resolve refuses every format; Premiere exposes `ticksPerFrame` as an integer |
| render | ✅ | `UNVERIFIED` | both: pass the "whole programme" flag explicitly, because in/out marks persist |

Every `UNVERIFIED` means *the API declares it and nobody here has watched it work yet.* Run the probe and
promote them **one at a time**, as each is actually seen to work end to end.

### Read the CDL row twice

A per-camera CDL is the preferred camera match here — four readable numbers, adjustable by a human,
surviving a round trip. Premiere cannot take one from a script, so a plan carrying a CDL is **rejected**,
and the workaround is to bake it into a per-camera cube fitted *in log* and applied before the vendor
transform:

```bash
python3 scripts/bake_cdl_into_lut.py --lut vendor.cube \
    --slope 1.195045 1.187831 1.210953 \
    --offset -0.038757 -0.037940 -0.039563 \
    --power 1.0 1.0 1.0 --saturation 0.926712 \
    --out camA_baked.cube --size 65
```

🔴 **The order is the whole point, and it is not the order the node graph suggests.** Inside a Resolve node
the corrector runs **before** that node's LUT, so a CDL on a node carrying a log→709 LUT operates on
**log**. Bake it the other way round and you get a plausible picture that is wrong everywhere.

That is not a downgrade: measured, an ffmpeg path and the NLE agreed to a mean **1.3 codes of 255** and
*r* = **0.9996** once both applied the log-fitted CDL first. The one thing you lose is a human's ability to
nudge four numbers at the far end, so say so on the handover.

⚠️ **Two lessons about how to read a bake's error table**, both learned by getting them wrong. *Judge a LUT
on a domain a camera can reach* — a uniform-random row across the whole RGB cube read mean 0.14 / max 22.8
codes and is meaningless, because it is dominated by hard-saturated corners no sensor produces, while the
real measured chart patches read mean **0.05** / max **0.29**. And *verify against the right camera* —
pointed at a whole measurement file the verifier scooped up 844 triplets including a second body's values
and reported **2.07 codes**; scoped to the right camera's own 24 patches the same cube reads **0.05**.

---

## Why the IR stores a crop rectangle

Because Zoom/Pan/Tilt is one application's parameterisation, and putting it in the shared artefact bakes
that application into the plan. The IR stores the primitive fact — **the crop window in source pixels, plus
both rasters** — and each backend derives its own numbers in one unit-tested place:

```
PX = timeline_w / crop_w
Resolve : Zoom = src_w/crop_w   Pan   = d * PX                        [TIMELINE pixels]
Premiere: Scale = 100 * PX      Pos.x = 0.5 + d * PX / timeline_w     [normalised, .5 = centred]
```

The payoff is a cross-check neither application can give you alone:

> ✅ **Resolve's Pan, divided by the timeline width, equals Premiere's normalised Position offset —
> exactly, on both axes, at any crop aspect ratio.**

That invariant is asserted over five crops in `nle_ir.py --selftest`, and **it caught a real bug in the
vertical axis within one run of being written**: `d / crop_h` is algebraically identical to
`d * PX / timeline_w` horizontally and wrong vertically whenever the crop's aspect differs from the
timeline's, so the short form passes every horizontal test. Resolve's own version of this bug — a scale
factor shipped as `1.0`, correct at exactly 2× zoom and wrong everywhere else — survived the first reframe
anyone tested, **because there was nothing to disagree with it.**

🔴 The general lesson, worth more than the geometry: **a cross-check must derive the same quantity two
independent ways and assert agreement.** A tripwire that only asks "did this change?" held a 13% render
error steady across eight builds on this project.

---

## Three workflows

### 1. Premiere only — someone who has never installed Resolve

✅ **Nothing upstream of the timeline needs Resolve.** Footage and audio QC, verified offload, syncing
angles with no shared clock, drift measurement, per-word speaker attribution, white balance and per-clip
LUT fitting, log→view LUTs, speed ramps with real motion blur, graphic conforming, forced-aligned
subtitles, day sheets — all `python3` + `ffmpeg` + `numpy`. Read `references/post-production.md` straight
through; everything above "Assembling the project" applies unchanged.

Two things to do differently:

- **Take the grade as LUTs, not CDLs.** The backend refuses a CDL rather than dropping one.
- **Retime before the timeline, not on it.** There is no speed setter; `speed_ramp.py` produces an
  exact-length retimed file with real motion blur, which is a better answer than a timeline percentage
  anyway.

### 2. Resolve only

Unchanged — the IR and the gate are additions, not a migration. The one new habit: run
`nle_ir.py --compare plan.json` **before promising anybody a handover**, so you learn what the other
application will refuse before you have offered.

### 3. Mixed — start in one, finish in the other

The important design decision is that **the handover artefact is the plan, not the project file.**

```bash
# sending, after cutting in Resolve
python3 scripts/nle_ir.py --compare plan.json                 # what will the far end refuse?
python3 scripts/nle_handover.py --pack plan.json --to ~/handover/cut-v7 --from-backend resolve

# receiving, with only Premiere
python3 scripts/nle_handover.py --verify ~/handover/cut-v7 --backend premiere
python3 scripts/premiere_bridge.py --probe                    # once, on this machine
python3 scripts/premiere_bridge.py --build ~/handover/cut-v7/plan.json
```

`--pack` writes a manifest, a copy of the plan, and a `HANDOVER.md` stating in plain language what the
receiving application will build exactly, what it will refuse, and what has been baked into the media and
is therefore no longer adjustable. `--verify` is run by the **receiver** and refuses to let them start on a
package that is missing a file or whose media has changed under it.

**Why not an interchange file?** FCP7 XML, AAF and EDL are all lossy in exactly the places that matter here
— crops, fade shape, animation, grade — and lossy **silently**: the receiving application opens it, shows a
timeline, and says nothing about what it dropped.

⚠️ **`--pack` does not copy media by default**, because one file per deliverable is the rule and a package
duplicating a folder of masters creates a second thing that can go stale and be published by mistake. Pass
`--copy` when it genuinely has to cross a machine boundary — and note that copy mode reads every copy back
and compares it, because a copy is not a copy until it has been read back.

#### 🔴 The thing that does not travel

**Only what the plan describes is reproducible.** Manual work done inside either application — a
stabilisation pass, a hand grade, a comp somebody drew — is not in the plan and will not appear at the far
end. Neither application lets a script read those back out reliably.

So do not hope. **Render it into the media and declare it.** That is already the established pattern for
stabilisation, which is not scriptable in Resolve at all (measured: absent from all 32 properties
`GetProperty()` returns, and `SetProperty` refuses all twelve plausible key names) and is a manual effect in
Premiere too:

> script a scratch project with the clip at 1× and ungraded → the operator runs the stabiliser in the UI →
> script the export, rendered **ungraded and full-raster** → the normal colour and reframe chain then
> applies to that new source exactly once.

Put an entry in the plan's `baked` list saying what was done and why, and it appears in `HANDOVER.md` under
*"Baked in, and therefore NOT adjustable at your end"*. If that list is empty, `--verify` says so as a NOTE
rather than staying quiet — an empty list is much more often an omission than a fact.

---

## Running the Premiere backend

### Once, per machine

1. Enable developer mode for UXP plugins in Premiere's preferences.
2. Load `scripts/premiere/uxp/` as an unpackaged plugin (UXP Developer Tool → *Add Plugin* → its
   `manifest.json` → *Load*).
3. Open the panel: **Window → Extensions → film-shoot timeline**, and press *Start listening*.
4. If the panel reports no native `fs`, press **Job folder…** and pick `~/.film-shoot/premiere-jobs`
   (create it first). With `localFileSystem: "fullAccess"` granted it usually finds real paths itself.

**Why a job folder and not a socket:** a file written and read back is the same verification discipline used
everywhere else here, and the job and its result are the log when a build goes wrong at 1 a.m.

### Every time

```bash
python3 scripts/premiere_bridge.py --probe --out probe.json     # NOT optional
python3 scripts/nle_ir.py --validate plan.json --backend premiere
python3 scripts/premiere_bridge.py --build plan.json
python3 scripts/premiere_bridge.py --readback --with-components --out seen.json
```

The probe wants **one clip sitting on V1 of an open sequence**, because that is the only way to learn this
build's intrinsic component and parameter names. Without it the probe reports `INCOMPLETE` and says so
rather than passing with a gap.

It replaces five guesses with five measurements: ticks per second; whether an exact 29.97 survives
`FrameRate`; every video filter's match name; every component and parameter on a real clip by display name;
and whether `getVideoTrack()` is 0- or 1-based. A sixth, `probe_placement`, writes into a scratch sequence
to settle whether the out point is inclusive or exclusive — worth one disposable sequence, because guessing
it makes **every clip exactly one frame short**, which survives casual review and destroys frame-accurate
graphics.

⚠️ **`--build` refuses to treat *ok with warnings* as success**, because on this pipeline the warnings are
the interesting part.

### Premiere's three sharpest traps

Full detail in `references/premiere-uxp.md`; these are the ones that cost an afternoon each.

- 🔴 **A handle acquired before the lock is dead by the time you use it.** A `ClipProjectItem` from `.cast()`
  used inside `executeTransaction` threw *"The script object is no longer valid."* The split is: **await
  outside, cast and `create*Action` inside.**
- 🔴 **Both callbacks are synchronous, so you cannot `await` inside either.** Awaiting commits an **empty
  transaction**, which returns without error and does nothing.
- 🔴 **Source in/out lives on the ProjectItem, not on the placement call**, so **two clips cut from the same
  file cannot be batched.** Batch them and every placement takes the *last* in/out applied — and the
  timeline still tiles perfectly with no gaps, so a gap check cannot see it.

And two runtime facts that invalidate the obvious implementation: `Constants.ProjectItemType` is
**`undefined` at runtime** despite appearing in the declarations (a *sequence* item reports `type === 1`, the
same value a clip reports — duck-type the cast result instead), and `AE.ADBE Opacity` exposes **two params
both named "Blend Mode"**, so "take the first match by name" is a coin toss. The backend **throws on an
ambiguous name** rather than picking one.

---

## Resolve's three sharpest traps

Full detail in `references/resolve-api.md`. Read its playback-frame-rate section before anything else; that
one shipped broken.

- ⚠️ **`timelinePlaybackFrameRate` defaults to 24 and `SetSetting` refuses every format offered.** A 29.97
  timeline then plays at **0.8008×**, dropping ~20% of the audio, with a contented green **24** in the
  toolbar. ✅ A project settings preset does carry it — export one from a correctly configured project,
  **commit the `.drpst`**, and apply it immediately after `CreateProject`.
- 🔴 **`CreateProject` does not always make the new project current.** It returned a valid object, listed it,
  imported media into it happily — while `GetCurrentProject()` still answered "Untitled Project" — and the
  first call to fail was `CreateEmptyTimeline()`, returning `None` several stages later. **Call
  `LoadProject` explicitly and assert the current project's name.**
- ⚠️ **`MarkIn` and `MarkOut` PERSIST on the project.** A review render came back reporting **Complete**, a
  valid MP4 at the right resolution and rate, containing **8 frames out of 26688**, because a verifier had
  left the marks set hours earlier. Pass `SelectAllFrames` on every full render.

---

## Verify by reading the timeline back

**A build script that reports no problems has told you about its own intentions, not about the project.**
Check in this order:

1. **Frame rate, first, before anything else.**
2. Track layout, and that no camera audio came in.
3. **Clip by clip against the plan** — source file, source in/out, record position, track. ⚠️ **A plausible
   timeline is the failure mode**: clips that *slid* still tile, so a no-gaps check cannot see them. Compare
   each item's start against the position it was asked for, **item by item**.
4. **Render probe frames and correlate them against the source**, so placement is confirmed on picture
   rather than on metadata. Correlate frame-to-frame **differences**, not frames: any two frames of static
   content correlate ≈1.0 whether or not they are the same frame, and a tight crop against a wide shot of
   the same instant reads ≈0.05. If the clip is cropped, apply the same crop to the source first, and
   **assert the comparison produced output at all** — a broken invocation scores 0.000 on every clip, which
   reads identically to "every frame is wrong."

### And things that are true whichever application you drive

- **A setter that returns without complaint is not evidence the setting took.** Read it back and compare.
- **When a create call hands back a handle, the application may not have switched to it.**
- **Cache rendered assets on their CONTENT, not their filename or length.** Two versions of that bug shipped
  here: keyed on frame count, then on frame count + filename. A graphic rebuilt in a corrected typeface has
  the same name, the same length, and different pixels. Have the **verifier** recompute the hash from the
  artwork on disk and assert the timeline references it, then negative-test it against the previous version,
  where it must fail.
- **Hash each export against the previous ones and fail loudly on a match.** A render came back
  byte-identical, reported Complete, after a setting had been changed — PSNR = inf on every frame.
- **A modal dialog blocks the entire plugin host, and no script can dismiss it.** On the Resolve side,
  setting a Fusion `Loader.Clip` popped one and locked a live session until a human cleared it. Assume the
  same of any UXP call that might want to ask something.
- **Anything absolute in a path will not resolve on the other machine.** That is half the reason a handover
  is an assembled project plus its media, not a project full of absolute references to someone's scratch
  disk. ⚠️ Related: a built project references its rendered media by absolute path, so **moving or renaming
  the project folder takes those clips offline.** Rebuild rather than relinking by hand, and keep the old
  folder until the rebuild is verified.
- ⚠️ **Do not overwrite a media file while the built project is open.** It wedges the next render that
  touches it — no file, no error — and costs an application restart. Regenerate graphics first, build second.

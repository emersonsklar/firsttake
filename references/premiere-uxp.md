# Assembling a project with the Premiere Pro UXP API

The companion to `resolve-api.md`. Same goal — hand over an **assembled** project rather than a
folder of clips and a document describing what you wanted — in the other application, because not
everyone editing this footage uses the same NLE.

Read `nle-backends.md` first for how the two fit together. This file is the Premiere dialect and
its traps.

> ⚠️ **EARLY ACCESS.** This backend works and has reproduced a shipped master to a mean 0.83 codes
> of 255 — and it is newer, thinner and more manual than the Resolve one. Colour, retime and
> transitions are absent; a build is driven from a panel that needs reloading after any change to it;
> and everything here has been exercised on one machine and one version. **If you have a choice of
> application, `nle-backends.md` recommends Resolve, and means it.**

**Provenance, because it decides how much you may trust each line below.**

| Label | What established it |
|---|---|
| `CONFIRMED` | Measured on the installed application or read out of files it ships |
| `DECLARED` | Stated in Adobe's own `premierepro.d.ts` — authoritative that a member *exists*, silent on how it behaves |
| `UNVERIFIED` | Not yet run here. **Behaviour is UNVERIFIED until the probe has run**, without exception |

`CONFIRMED` facts on this page were taken from **Premiere Pro 26.5.1.1** (the 2026 release) and
from the type declarations at `github.com/adobe/premierepro-types`.

🔴 **Nothing in the Premiere backend is trusted until `premiere_bridge.py --probe` has run against
the Premiere build in front of you, and `nle_ir.CAPABILITIES["premiere"]` still says `UNVERIFIED`
for every entry until someone has watched one work end to end.** The probe is not a formality; it
exists because this is precisely the situation in which a confident unmeasured claim does the most
damage — a build that reports success and lays a timeline nobody has looked at.

---

## The shape of the thing: UXP is not a scripting bridge

`CONFIRMED`. Resolve ships a Python module, so a build script runs in a shell and drives the
application. Premiere's automation lives **inside** the app:

- UXP is a JavaScript runtime hosting a panel. There is no CLI, no socket you may open to it, and
  no `osascript` route.
- The plugin host is at `…/Adobe Premiere Pro 2026.app/Contents/UXP/plugins/`, and the release
  ships at least one first-party UXP plugin, so this is the supported surface and not a preview.
- That shipped plugin declares `"manifestVersion": 6`, `"app": "premierepro"` with a `minVersion`,
  and a `dependencies` map naming internal modules including `dvascripting` and `MZ.Scripting`.
  Copy that shape; a manifest version the host does not recognise simply does not load.
- ExtendScript is the previous generation and is not the route. The old scripting guide is
  unsupported.

**So the backend is a panel, and Python talks to it through a job folder on disk** — see
`scripts/premiere_bridge.py` and `scripts/premiere/uxp/`. One manual step: open the panel, press
*Start listening*. That is not a worse bargain than Resolve's, which also needs the application
open and — measured — sitting on the right window, or `CreateProject` hands back a project the
application has not actually switched to.

Grant `"localFileSystem": "fullAccess"` in `requiredPermissions` and a native `fs` module can read
and write real paths; without it, the panel must be handed a folder through a picker once.

---

## Three places Premiere is genuinely better than Resolve

Worth stating plainly, because the assumption running the other way would cost a day.

### 1. Keyframes are native. Nothing has to become a comp.

`DECLARED`. In Resolve a timeline item has **no keyframe API at all** — 32 properties, every one
static — so a push-in, a transition or an animated title has to be built as a Fusion comp, with
the spline connected before the keyframes are written or the effect silently does not move.

Premiere exposes `ComponentParam` with `createAddKeyframeAction`, `createSetTimeVaryingAction`,
`createSetInterpolationAtKeyframeAction`, `getKeyframeListAsTickTimes`, `isTimeVarying`, and an
`areKeyframesSupported()` you can ask first. So a reframe that moves, or a fade with a *shape*, is
an ordinary effect parameter rather than a composition.

That also means the IR's `fade_shaped` capability — which Resolve **cannot** do, because `SetFades`
is linear only — is expressible here.

### 2. Transitions are scriptable.

`DECLARED`. Resolve's `AddTransition` returned `None` for all seven signatures tried, which is why
transitions on that side are Fusion comps. Premiere declares `TransitionFactory`,
`AddTransitionOptions`, `VideoTransition`, and `VideoClipTrackItem.createAddVideoTransitionAction()`.

### 3. The frame rate is writable — and is EXACT. `CONFIRMED`

Measured on 26.5.1 via the probe, and this is the headline result:

| rate | ticks/frame | exact straight from the float? | Δ seconds at frame 26688 |
|---|---|---|---|
| 29.97 DF | 8475667200 | **yes** | **0** |
| 30.000 | 8467200000 | yes | 0 |
| 23.976 | 10594584000 | yes | 0 |
| 25 | 10160640000 | yes | 0 |

`TickTime.TIME_ONE_SECOND.ticksNumber` reads **254016000000**, matching the constant the backend
derives from. So a 29.97 programme lands on an exact frame at programme length, and
`FrameRate.createWithValue(30000/1001)` already produces the right integer without the corrective
write — which is kept anyway, because it costs nothing and guards the rates nobody has measured.

This is the mirror image of the worst trap in `resolve-api.md`, where
`timelinePlaybackFrameRate` **refuses every format offered** — `29.97`, `29.970`, `30000/1001`,
`30`, float, empty string, before and after media, on a fresh project — so a project left at its
default 24 plays a 29.97 timeline at 0.8008×, dropping about a fifth of the audio while showing a
contented green `24`. There, the only way in is a committed settings preset.

The mechanism here:

- `SequenceSettings.setVideoFrameRate(FrameRate)` is synchronous, committed via
  `Sequence.createSetSettingsAction(settings)` inside a transaction.
- `FrameRate.createWithValue(value: number)` takes a **float**, so 30000/1001 arrives as
  29.97002997…; but
- `FrameRate.ticksPerFrame` is **read/write**, and ticks are integers.

So even if a float were rounded, the rational can be restored exactly:

```js
ticksPerFrame = TICKS_PER_SECOND * den / num        // 29.97 DF -> 8475667200
```

with `TICKS_PER_SECOND = 254016000000`. ⚠️ That constant is **asserted, not assumed** — the probe
reads `TickTime.TIME_ONE_SECOND.ticksNumber` and fails loudly if it differs, because every frame
position in the build is derived from it and the symptom of a wrong value would be drift, not an
error.

**Read the rate back anyway.** The rule Resolve taught applies unchanged and is not about Resolve:
*a setter that returns without complaint is not evidence the setting took.* The backend re-reads
`getSettings().getVideoFrameRate().ticksPerFrame` and refuses to place a single clip if it is not
the number asked for.

---

## Placing clips

### Edits are Actions inside a LOCKED, synchronous transaction

`CONFIRMED` the hard way. The shape that works:

```js
const editor = ppro.SequenceEditor.getEditor(seq);      // await everything OUTSIDE
project.lockedAccess(() => {
  project.executeTransaction((compound) => {
    const clip = ppro.ClipProjectItem.cast(item);       // cast INSIDE
    compound.addAction(clip.createSetInOutPointsAction(inTick, outTick));
  }, "undo label");
});
```

🔴 **A handle acquired before the lock is dead by the time you use it.** A `ClipProjectItem` from
`.cast()` used inside `executeTransaction` threw **"The script object is no longer valid."** UXP's
handles to Premiere objects do not survive the async boundaries between acquiring and using them.
`project.lockedAccess(cb)` freezes the project for the duration, and a transaction may be opened
inside it — so the split is: **await outside, cast and `create*Action` inside.**

🔴 **Both callbacks are synchronous, so you cannot `await` inside either.** Awaiting inside commits
an empty transaction, which returns without error and does nothing — the Premiere-shaped member of
the same family as Resolve's "calls that report success and do nothing". The backend's helper
asserts its callback actually ran, because an empty transaction is otherwise indistinguishable
from a successful one.

### `ProjectItem` is a base type — almost everything needs a cast

🔴 `CONFIRMED`. This one bit three times in one afternoon.

```
ProjectItem                     name and type are PROPERTIES (not getName()), plus getId(),
                                getParentBin() — and that is nearly all of it
FolderItem.cast(item)           getItems()
ClipProjectItem.cast(item)      getMediaFilePath(), getInPoint(mediaType), getOutPoint(mediaType),
                                createSetInOutPointsAction(), createSetInputLUTIDAction(),
                                createSetScaleToFrameSizeAction(), getContentType()
```

Calling `it.getName()` fails loudly, which is the *good* outcome. The dangerous one was the same
mistake inside a `try/catch` in the read-back path: it set every clip's media path to `null`, so a
plan-versus-timeline diff would have passed **without comparing anything**.

⚠️ **Do not switch on `ProjectItem.type` to decide what something is.** Measured: `Constants.ProjectItemType`
is **`undefined` at runtime** despite appearing in the declarations, and a *sequence* project item
reports `type === 1` — the same value a clip reports. Duck-type the cast **result** instead
(`typeof f.getItems === "function"`). To tell a sequence from media, use
`ClipProjectItem.getContentType()` against `Constants.ContentType.SEQUENCE` (= 2).

### Runtime Constants do not match the declarations

`CONFIRMED` on 26.5.1. Always use the named member; never a literal, and never the declaration's
apparent ordering.

| Constant | Runtime |
|---|---|
| `InterpolationMode` | `LINEAR: 0, HOLD: 4, BEZIER: 5, TIME: 6, TIME_TRANSITION_START: 7, TIME_TRANSITION_END: 8` — **not** the declaration's order |
| `ExportType` | **strings**, e.g. `IMMEDIATELY: "com.adobe.mezzanine.export.immediately"` — not integers |
| `TrackItemType` | `EMPTY: 0, CLIP: 1, TRANSITION: 2, PREVIEW: 3, FEEDBACK: 4` |
| `MediaType` | members exist but their values are **opaque objects**, not numbers. Pass them through; do not compare them |
| `ContentType` | `MEDIA: 1, SEQUENCE: 2, ANY: 4294967295` |
| `ProjectItemType` | 🔴 **absent entirely** |

### Source in/out lives on the ProjectItem, not on the placement call

🔴 `DECLARED`, and the highest-risk difference from Resolve. Resolve's clip dict carries
`startFrame` / `endFrame` / `recordFrame` / `trackIndex` together, so a whole timeline goes in one
`AppendToTimeline`. Premiere's `createOverwriteItemAction(projectItem, time, videoTrackIndex,
audioTrackIndex)` has **no in/out arguments**; the in and out points belong to the ProjectItem
(`createSetInPointAction`, `createSetOutPointAction`, `createSetInOutPointsAction`).

**So two clips cut from the same file cannot be batched.** Set in/out, place, set in/out, place —
sequentially, one item at a time. Batch them and every placement takes the *last* in/out applied,
and the timeline still tiles perfectly with no gaps, so a gap check cannot see it. That is the same
failure signature as Resolve's over-long still sliding the next clip: a plausible timeline that
does not match the plan. Compare each item's start against the position it was asked for.

### Settled by the probe

| Question | Answer, measured on 26.5.1 |
|---|---|
| Is the out point **inclusive or exclusive**? | ✅ **EXCLUSIVE.** An `in..out` span of 100 frames delivers exactly 100 frames, `landsOnAWholeFrame: true`. Same convention as Resolve's `endFrame`, so `src_in + len` is right in both. |
| Does a clip land where it is asked? | ✅ Asked frame 700, `startFrames: 700`. In/out read back as 0 and 100 frames with exact ticks. |
| Is `getVideoTrack(i)` **0- or 1-based**? | ✅ **0-based.** `getVideoTrack(0)` returns the track named `"Video 1"` and reports its own index as 0. |

Still open, and the reason `keyframes` stays `UNVERIFIED`:

| Question | Why it cannot be assumed |
|---|---|
| Are effect keyframe times **clip-relative or sequence-absolute**? | The same trap as Fusion's `COMPN_GlobalStart` reading as *minus* the clip's source frame: keyframes land thousands of frames outside the clip, nothing moves, nothing errors. The IR carries `keyframe_time_base` so this is a stated decision rather than a guess. |

### Look parameters up by name — and refuse an ambiguous one

🔴 `CONFIRMED`, and it invalidates the obvious implementation. `AE.ADBE Opacity` exposes three
params of which **indices 1 and 2 are BOTH named `"Blend Mode"`**, and `AE.ADBE Motion` param 3 is
named `" "` — a single space. So:

- display names are **not unique**, and "take the first match" is a coin toss that happens to land
  correctly for `Opacity`, `Position` and `Scale`;
- some params cannot be addressed by name at all.

The backend throws on an ambiguous name rather than picking one, because writing to the wrong of two
identically named params yields a timeline that renders with the effect set on something nobody
chose. Confirmed present and keyframable by name: `AE.ADBE Motion` → `Position` (0), `Scale` (1);
`AE.ADBE Opacity` → `Opacity` (0). Motion also exposes native `Crop Left/Top/Right/Bottom` (7–10),
a second route to a reframe.

⚠️ **Component order does not match the UI.** On a real clip, Opacity is component **0** and Motion
is component **1** — inverted from the Effect Controls panel. Anything indexing components
positionally is wrong.

### Two traps Premiere hands you an explicit answer to

Both of these cost real time on the Resolve side and are a parameter here:

- **Numbered stills importing as one image sequence.** `cue001.png … cue012.png` in one folder
  collapsed into a single pool clip in Resolve, so eleven of twelve captions never existed and the
  import reported success. `Project.importFiles(paths, suppressUI, targetBin, asNumberedStills)`
  takes the behaviour as its fourth argument — pass `false`. (Better still, keep doing what works
  in both: one full-length ProRes 4444 alpha track laid as a single clip, which removes every
  per-cue placement error at once. Assert the file really carries alpha — a 4444 written without it
  composites as an opaque black rectangle over the whole frame.)
- **"Scale to Frame Size" silently invalidating a crop.** `createSetScaleToFrameSizeAction()`
  exists. The backend must never call it, and the verifier should assert it is off, because every
  Motion Scale number assumes 100% means one source pixel per sequence pixel.

---

## Crops and reframes

Both backends derive their parameters from the same primitive — **the crop window in source pixels**
— and never from each other's units. See the header of `scripts/nle_ir.py`. One factor does it:

```
PX = timeline_w / crop_w
Resolve : Zoom = src_w/crop_w   Pan   = d * PX                        [TIMELINE pixels]
Premiere: Scale = 100 * PX      Pos.x = 0.5 + d * PX / timeline_w     [normalised, .5 = centred]
```

where `d` is the crop centre's offset from the source centre. Both axes take the same form.

⚠️ On the **vertical** axis it must still be `PX / timeline_h`, never `1 / crop_h`. Those two are
algebraically identical horizontally — where `PX / timeline_w == 1 / crop_w` — so writing it the
short way passes every horizontal test and is wrong on the vertical whenever the crop's aspect
ratio differs from the timeline's. I wrote that bug; the cross-backend invariant in the selftest
caught it in one run.

That invariant is the thing to keep: **Resolve's Pan divided by the timeline width equals
Premiere's normalised Position offset, exactly, on both axes at any crop aspect.** It is asserted
over five crops, so the two backends cannot drift apart without a test going red. Resolve's own
`s` factor shipped as 1.0 once, which is correct at exactly 2× zoom and wrong everywhere else — a
bug a cross-check between two independent parameterisations would have caught immediately.

Look parameters up **by display name, not by index.** `Component.getParam(i)` is indexed, and the
declarations say the meaning of an index is "defined exclusively by the component itself" — so an
index is a guess that survives until a Premiere update. `ComponentParam.displayName` is readable,
and the probe dumps every component and parameter on a real clip, which is how "which one is Scale"
becomes a fact.

---

## Grading: Premiere cannot be scripted to apply colour at all

🔴 `CONFIRMED` on 26.5.1, from three directions. This is the real capability gap, and it is not a
naming problem.

| route | result |
|---|---|
| ASC CDL | no entry point anywhere — searched CDL, slope, offset, power, saturation |
| `ClipProjectItem.createSetInputLUTIDAction()` | wants an opaque **ID**, not a path, and nothing documents how to obtain one for a cube you generated |
| `AE.ADBE Lumetri` parameters | **130 parameters, ZERO accept a string.** `LUTAsset` (128) and `LookAsset` (129) exist but accept neither number, string nor boolean, and are not keyframable |
| `ProjectColorSettings` | only graphics-white luminance |

### And no, there is no QE back door

Worth settling, because it is the obvious next question. The old **QE** ("quality engineering") DOM
was an undocumented ExtendScript surface with far deeper reach than the public one, reached via
`app.enableQE()` and then a global `qe`. It is **not in UXP**, measured rather than assumed:

- `ppro.QE`, `ppro.qe`, `ppro.enableQE`, `ppro.getQE`, `ppro.Advanced*`, `ppro.Internal`,
  `ppro.Debug` — every one `undefined`
- `globalThis.qe`, `globalThis.app`, `globalThis.QE` — `undefined`. UXP has no `app` object, which
  is what `enableQE()` used to hang off
- `require("premierepro-advanced" | "premierepro-internal" | "premierepro-qe")` — not available

⚠️ The declarations cannot answer this question either way, since QE never appeared in a declaration
file. The way to settle it is to diff the **runtime** key list against the declared one:

```js
Object.keys(ppro)                     // 72 on 26.5.1
Object.getOwnPropertyNames(proto)     // plus the prototype
```

against the 73 names in `premierepro.d.ts`. The difference is where a hidden API would be. What it
actually turns up is mundane: **one undeclared member, `Application` (a function), which returns an
object exposing only the module's own surface**, plus `SequenceEvent`, `eventRoot` and
`constructor`. And **five declared members absent at runtime** — `ComponentParam`, `MogrtText`,
`MogrtComment`, `TimeDisplay`, `ProjectColorSettings` — all instance-only types, the same pattern
as `ComponentParam`.

### So the grade has to be in the media

`nle_ir` declares `cdl: False` **and `lut: False`** for this backend, and the gate refuses a plan
carrying either rather than dropping it. Dropping it silently would render the vendor log→709
transform alone — or nothing at all — which looks like a washed-out picture rather than like a
failure. That is exactly what happened on the Resolve side when `SetCDL` was called without its
`NodeIndex` key.

**The only route is to pre-grade the media and declare it.** Bake the CDL into the vendor cube with
`bake_cdl_into_lut.py`, render the graded source once, and put an entry in the plan's `baked` list so
`HANDOVER.md` tells the far end the grade is no longer adjustable. Render the intermediate
**full-raster and ungraded-of-everything-else**, so the reframe still happens on the timeline and is
still verifiable there.

Why a per-camera CDL is what you are giving up: it is four readable numbers a human can adjust, and
it survives a round trip through other software. A baked intermediate does not.

Both steps are needed, and the gate accepts the result only when the node carries no grade at all:

1. **Bake the CDL into the LUT** — `scripts/bake_cdl_into_lut.py`, which composes
   `baked(x) = vendor_lut(cdl(x))` into one cube. That is the order Resolve uses anyway, since a
   node's corrector runs before its own LUT.
2. **Render that cube into the media**, because Premiere cannot attach the cube either.

### What the bake actually costs, measured

A-camera S-Log3 CDL folded into the vendor log→709 cube at a 65³ grid, against the analytic
composition:

| domain | mean | max |
|---|---|---|
| **real measured A-cam chart patches** | 0.05 codes | **0.29** |
| neutral axis, 0.05–0.95 | 0.04 | 0.56 |
| aggressive synthetic image around real values | 0.19 | 14.3 |
| uniform random across the whole RGB cube | 0.14 | 22.8 |

And the file itself is right: **ffmpeg's result through the baked cube matches an independent
evaluation of the same cube to 0.006 codes mean, 0.045 max**, which is what proves the dialect
parses and the channel ordering is not transposed.

⚠️ **Two lessons about how to read those rows, both learned by getting them wrong.**

First, *judge a LUT on a domain a camera can reach.* The uniform row looks alarming and is
meaningless: it is dominated by hard-saturated corners where a log→709 transform is steepest and no
sensor produces values. A test that fails on unreachable inputs teaches you to ignore the test.

Second, *verify against the right camera.* Pointed at a whole measurement file, the verifier scooped
up 844 triplets including a second body's D-Log M values and reported 2.07 codes — a number about
feeding one camera's encoding through another camera's transform. Scoped to the A-camera's own 24
patches, the same cube reads 0.05. Hence `--verify-key`.

The residual is grid interpolation and falls with grid size (mean 0.48 → 0.13 → 0.03 codes at 33³,
65³, 129³). 65³ is the default because it is where real-footage error drops below a code while the
file stays a sane size.

For LUTs themselves: `ProjectItem.createSetInputLUTIDAction(stringLUTID)` is `DECLARED`, and note
it takes an **ID, not a path**. How an ID is obtained for a cube you generated yourself is
`UNVERIFIED` and is on the probe's list. Expect this to rhyme with Resolve, where `SetLUT` refuses
any path outside its own LUT library and the library is cached at load, so a file dropped in behind
its back needs `RefreshLUTList()`. Also expect the parser to be fussy: a UTF-8 em-dash in a
*comment line* made Resolve's `SetLUT` return `False` on all twelve clips.

Also missing: **no speed setter.** `getSpeed()` and `isSpeedReversed()` exist; nothing writes them.
`speed: False`, and a retime must be done to the media before it reaches the timeline.

---

## Rendering

`DECLARED`. `EncoderManager.getManager()` — **synchronous**, unlike most of this API — then
`exportSequence(sequence, exportType, outputFile, presetFile, exportFull)`, with
`Constants.ExportType` offering `IMMEDIATELY`, `QUEUE_TO_AME` and `QUEUE_TO_APP`. There is also
`encodeFile` / `encodeProjectItem`, an `isAMEInstalled` flag, and render lifecycle events
(`EVENT_RENDER_COMPLETE`, `EVENT_RENDER_ERROR`, …) worth listening to rather than polling.

**Pass `exportFull = true`, deliberately.** Sequence in/out points persist, exactly as Resolve's
`MarkIn`/`MarkOut` do on the project — and there, a review render came back reporting *Complete*, a
valid MP4 at the right resolution and rate, containing **8 frames out of 26688**, because a verifier
had left the marks set hours earlier.

The rule that survives the change of application: **"Complete" is not "correct."** Measure the
delivered file, never the queue's opinion of it —

- frame count against the programme's own total, which is the check that caught the above;
- frame rate, because 29.97 delivered as 30.000 drifts a frame every 33 s and reads as a lip-sync
  fault ten minutes in;
- that it is **one movie and not an image sequence**;
- integrated loudness, measured **in the channel layout that ships** — a mono stem laid on a stereo
  track is dual-mono and therefore +3 dB under BS.1770, which is how a mix written at −14.0 LUFS
  rendered at −11.0 with both artefacts internally consistent and nothing comparing them.

---

## Verify by reading the timeline back

Unchanged from the Resolve side, because it was never really about Resolve: **a build script that
reports no problems has told you about its own intentions.** The `readback` op dumps the active
sequence — every clip's media path, start, end, duration, in/out, disabled state, speed, and
optionally its whole component chain — so Python can diff it against the plan that asked for it.

Check in this order:

1. **Frame rate, first, before anything else.**
2. Track layout, and that no camera audio arrived with the picture.
3. Clip by clip against the plan: source file, source in/out, record position, track.
4. **Render probe frames and correlate them against the source**, so placement is confirmed on
   picture rather than on metadata. Correlate frame-to-frame *differences*, not frames: any two
   frames of static content correlate ≈1.0 whether or not they are the same frame, and a tight crop
   against a wide shot of the same instant reads ≈0.05. And assert the comparison produced output
   at all — a broken invocation scores 0.000 on every clip, which reads identically to "every frame
   is wrong."

`--build` refuses to treat *ok with warnings* as a success, because on this pipeline the warnings
are the interesting part.

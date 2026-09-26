# Assembling a project with the Resolve scripting API

You can hand a post house an **assembled** project rather than a folder of clips and a
document describing what you wanted. Everything below was measured against DaVinci
Resolve **Studio** — scripting is a Studio feature, the free version has no API — while
building a two-camera interview: two angles, cropped reframes, animated titles,
transitions and a per-camera grade, laid entirely from Python.

It is worth doing. It is also full of calls that **return success and do nothing**, which
is why this file exists. Every fact here cost a debug cycle at least once.

```python
import DaVinciResolveScript as dvr      # Studio only
resolve = dvr.scriptapp("Resolve")
pm = resolve.GetProjectManager()
proj = pm.CreateProject("MyProject")
```

---

## ⚠️ The playback frame rate trap — check this before blaming anything else

A freshly created project sets `timelinePlaybackFrameRate` to **24**, and setting
`timelineFrameRate` to your real rate does **not** change it. On a 29.97 timeline:

- playback runs at **24 / 29.97 = 0.8008×**
- about **20% of the audio is dropped**, which sounds exactly like crackle
- Resolve shows a contented green **24** in the toolbar. It is not struggling. It is
  doing what it was told.

`SetSetting` and `SetSettings` both refuse the value — every format tried (`29.97`,
`29.970`, `30000/1001`, `30`, float, empty string), on a fresh project, before media was
added, and after setting the timeline rate.

✅ **A project settings preset does carry it.** Export a preset from a correctly
configured project once, commit the `.drpst`, and apply it immediately after
`CreateProject`:

```python
proj.ImportProjectSettingsPreset("assets/MyShoot-2997.drpst")   # if the install lacks it
proj.SetProjectSettingsPreset("MyShoot-2997")                   # 24 -> 29.97
```

Committing the preset file matters — otherwise the build depends on one machine's Resolve
install. Manual fallback: **Project Settings (gear, bottom right) → Master Settings →
Playback frame rate**.

This shipped to an operator. The false trails were expensive — 4K decode load, a runaway
`ffprobe` of mine saturating the SSD, clipping that turned out to be an artifact of my own
stereo→mono downmix, CoreAudio and five virtual HAL drivers. The render itself was clean
throughout (−2.09 dBFS, no overs, matching the stems), so the content was never at fault.
Two operator observations identified it: **Fairlight and Fusion sounded fine** (neither
composites the multi-track video), and **the timecode advanced only 13:03 during a 16.37 s
screen recording** — 0.80×, which points straight at 24/29.97.

An earlier version of this note said the rate was "not writable through the API." That was
generalised from one method to the whole API and it was wrong; the operator pushed back and
the preset route turned up immediately. **If a setting refuses to write, look for another
mechanism before concluding it cannot be done.**

### The rule this earns

**`SetSetting` returning `False` is not noise. Read every setting back and compare.**

The build had asked for 29.97, been refused, and dismissed it as non-writable — shipping a
project that played at 0.8×. The same mistake hid `perfProxyMediaMode`, which turns out to
be the UI's Proxy Handling toggle. A verifier that checks the playback rate **first**, before
anything else, costs three lines.

---

## Placing clips

| Fact | Consequence |
|---|---|
| **`endFrame` is EXCLUSIVE** | `endFrame = src_in + frames`. Confirmed by planting three known lengths. Get it wrong and every clip is a frame short. |
| `recordFrame` + `trackIndex` in the clip dict place a clip exactly, **gaps preserved** | This is how a sparse layer — 19 islands of tight shot over a continuous wide — gets laid in one pass. |
| **`GetSourceStartFrame()` under-reports by 1 on drop-frame media** | 9 of 17 clips on one timeline. Not a placement error: rendered and correlated, the picture matched the **requested** frame at r = 0.9999 against 0.996 for either neighbour. Tolerate 1 frame; flag anything larger. |
| **An imported still is 1 frame and gets a fixed 150-frame timeline length** | Asking for more is silently **clamped** — a title card came out 150 frames instead of 180. No project setting controls it. Render graphics to exact-length **ProRes 4444** clips (alpha) instead. |
| **A container can lie about its frame rate** | One delivered end card declares `r_frame_rate=600/1`; it is really 228 frames over 7.525 s with irregular intervals. Count frames (`nb_frames`), never trust the declared rate, and never compute length as `duration × fps` — that read 2 frames past EOF on a real file. |

---

## Calls that report success and do nothing

This is the category that wastes a day, because the build prints no problems.

- **`SetTrackEnable` returns `True` and has no effect.** Use `SetClipEnabled` on each item
  instead. A "disabled" track that is still compositing invalidates every A/B comparison
  you then make against it.
- **`SetFades` takes a dict.** `SetFades({"fadeIn": n, "fadeOut": n})`. The positional form
  returns `False` and silently does nothing.
- **`AddTransition` returned `None` for all seven signatures tried.** Build transitions as
  Fusion comps.
- ⚠️ **Setting `Loader.Clip` pops a modal file dialog that blocks the ENTIRE API** — every
  subsequent call returns `None` until a human dismisses it, and no script can dismiss it.
  This locked up a live Resolve session and needed the operator to clear it by hand. Do not
  use `Loader` at all; its absolute paths would not resolve on the post house's machine
  anyway.
- **`DeleteProject` refuses to delete the currently loaded project** and reports failure
  in a way easy to read as "gone." Load a throwaway first, then delete.
- 🔴 **`GetProperty("AnyNameAtAll")` returns `False`** — not `None`, not an error. **It cannot be
  used to test whether a property exists**, and a probe that concludes "the key is there, it is
  just set to False" is reading its own noise. Only `SetProperty` returning `True` is evidence
  that a key exists.
- 🔴 **A render can be a byte-identical duplicate of the previous one, reported as Complete.**
  Two exports of the same timeline, taken after changing a setting in the UI between them, came
  back with the same size, the same `framemd5` and **PSNR = inf on every frame**. The setting's
  analysis had not been re-run, and nothing said so. Every downstream metric then agreed to four
  decimal places, which is the only reason it was caught. **Hash each export against the previous
  ones and fail loudly on a match** — a setting that produced an identical file did not take.

---

## Anything that moves must be a Fusion comp

**A timeline item has no keyframe API.** It exposes 32 properties and every one of them is
static. Transform, crop and grade values can be *set*, not animated. So a push-in, a
transition or an animated title has to be a comp:

```python
item.AddFusionComp()
comp = item.GetFusionCompByIndex(1)
```

Three things make this work, and each one fails silently if you skip it:

- **Connect a spline BEFORE writing keyframes.** `comp.BezierSpline()` for a scalar,
  `comp.Path()` for a Point input like `Transform.Center`; connect it, *then*
  `tool.Input[frame] = value`. Writing to an un-animated input just sets a static value,
  and the effect simply does not move.
- **The comp's timebase is `COMPN_RenderStart`..`COMPN_RenderEnd` = `0..dur−1`.**
  `COMPN_GlobalStart` reads as **minus** the clip's source frame (−4783 on one clip);
  keyframes written against it land thousands of frames outside the comp and nothing
  happens.
- **Fusion's parameters are not in the units you expect.** `DirectionalBlur.Length` is
  not a fraction of frame width. Calibrated by rendering one picture at six values and
  measuring mean horizontal gradient: `0.000→3.948, 0.096→0.755, 0.200→0.464,
  0.400→0.310, 0.700→0.183, 1.000→0.136`. Matching a reference's 184 px smear at 1920
  needed **Length ≈ 0.45**, not the 0.096 that `184/1920` implies — a 4.7× error that
  read on screen as "the effect is barely visible." Calibrate against a measurement of
  the thing you are matching.

✅ **Fusion comps survive save → export `.drp` → import, named tools included.** Verified
by round trip and confirmed independently by the post house. That is what makes an
editable title a deliverable rather than a picture.

**Put the comp on the clip that is actually VISIBLE at that frame.** On a layered
timeline the topmost enabled lane is what a viewer sees; a transition comp attached to the
clip underneath it does nothing for as long as the upper lane covers it. Decompose each
side of a transition by topmost lane and index the animation curve by **absolute position
in the transition**, so a side split across two lanes still gets the right part of the
curve.

---

## Crops and reframes: express them relatively

A crop written as **Zoom / Pan / Tilt** is resolution-independent, so the same numbers
give identical framing on a 1080p proxy and a 4K original, and the post house can switch
between them freely. A crop written in pixels does not.

⚠️ **Pan and Tilt are in TIMELINE pixels, so a source-pixel offset has to be scaled:**

```python
zoom = src_w / float(crop_w)
s    = zoom * timeline_w / float(src_w)        # == 1.0 only when zoom == 2.0
pan  = -((crop_x + crop_w / 2.0) - src_w / 2.0) * s
tilt =  ((crop_y + crop_h / 2.0) - src_h / 2.0) * s
```

I shipped this without `s` once. It is correct at exactly 2× and wrong everywhere else,
so it survived the first reframe tested and misplaced every other one.

---

## Grading from a script

- **Within a node, the corrector runs BEFORE the node's LUT.** So a CDL set on a node
  carrying a log→709 LUT operates on **log**, which is usually what you want for a
  camera-match trim — but it is the opposite of what the node graph's left-to-right
  reading suggests. Verify on a frame before trusting either reading.
- Prefer a per-camera **ASC CDL** over a baked LUT: it is four readable numbers, it
  survives a round trip through other software, and a human can adjust it. See
  `grade-match.md` for deriving one.

### Loading a LUT you generated yourself

Two hard walls, both of which return `False` with no explanation:

- 🔴 **`SetLUT` refuses any path outside Resolve's own LUT library.** Not a permissions problem and
  not a path-format problem — it simply will not load a cube from elsewhere. Write generated cubes
  into `<LUT library>/<yourproject>/` and call **`project.RefreshLUTList()`** afterwards: the
  library is cached at load and a file dropped in behind its back is not found.
- 🔴 **The parser wants ASCII.** A UTF-8 em-dash in a *comment line* made `SetLUT` return `False`
  on all twelve clips. Match the vendor dialect exactly: **ASCII only, CRLF line endings,
  tab-separated triplets**, a blank line either side of `LUT_3D_SIZE`. ffmpeg's `lut3d` splits on
  arbitrary whitespace and ignores CR, so one file in that dialect still feeds both pipelines —
  which is what you want, so that the two cannot diverge.

⚠️ The library path contains spaces, and **ffmpeg's `lut3d` fails on spaces**. So write the cube
once to a space-free working directory for ffmpeg, copy it into the library for the NLE, and
**assert the two copies are byte-identical**. One bake, two locations, one definition of the grade.

### What is NOT scriptable: stabilisation

Measured on 21.1.0.17 against a real timeline item: stabilisation appears in **none** of the 32
keys `GetProperty()` returns, and `SetProperty` returns `False` for `Stabilization`,
`StabilizationEnabled`, `StabEnabled`, `Stabilize`, `StabilizationMode`, `StabMode`, `CameraLock`,
`StabilizationStrength`, `StabStrength`, `StabilizationSmooth`, `StabilizationZoom` and
`Smoothing`. `MediaPoolItem.GetClipProperty()` has nothing either.

It is worth the one manual step anyway, because the NLE's stabiliser is materially better than
`vidstab` on some motion (see `post-production.md`). The shape that works: **script a scratch
project containing the clip at 1x and ungraded, have the operator run the stabiliser in the UI,
then script the export and everything downstream.** Render the intermediate ungraded and
full-raster so the normal colour and reframe chain still applies to it exactly once.

---

## Verify the built timeline by reading it back

A build script that reports no problems has told you about its own intentions, not about
the project. Read the timeline back and check, in this order:

1. **Playback frame rate**, first, before anything else.
2. Track layout, and that no camera audio came in.
3. Clip-by-clip against the plan: source file, source in/out, record position, track.
4. **Render probe frames and correlate them against the source**, so placement is
   confirmed on picture rather than on metadata.

For step 4, use **motion correlation** — correlate frame-to-frame *differences*, not
frames. Plain pixel correlation between a tight crop and a wide shot of the same instant
reads ≈0.05, and any two frames of static content correlate ≈1.0 regardless of whether
they are the same frame, so plain correlation cannot answer "is this the frame I asked
for." If the clip is cropped, apply the same crop to the source before comparing, and
**assert the comparison actually produced output** — a broken ffmpeg invocation scores
0.000 on every clip, which reads identically to "every frame is wrong."

---

## Rendering out of the project

⚠️ **`MarkIn` and `MarkOut` PERSIST on the project.** Anything that rendered a short probe
window earlier — a verifier, an experiment — leaves them set, and the next render silently
delivers that window instead of the programme. Measured: a review render came back with the
queue reporting **Complete**, a valid MP4, the right resolution and the right frame rate,
and **8 frames out of 26688**. Pass `SelectAllFrames` (and set the marks explicitly as belt
and braces) on every full render.

That is the general shape of this whole stage: **"Complete" is not "correct."** After any
render, measure the file rather than trusting the queue —

- frame count against the programme's own total, which is the check that caught the above;
- frame rate, because 29.97 delivered as 30.000 drifts a frame every 33 s and reads as a
  lip-sync fault ten minutes in;
- that it is **one movie and not an image sequence** — the delivery format on this project
  has been PNG at least once, which makes every probe read as a failed render;
- integrated loudness, on the file that will actually be watched.

## Cache rendered assets on their CONTENT, not their filename

Graphics get rendered to intermediate clips and then cached, and the cache key is where
this goes wrong. Two versions of the same bug shipped on one project:

1. **Keyed on frame count** — switching to different artwork of the same length reused the
   old render, so a superseded lower third survived a source change.
2. **Keyed on frame count + filename** — re-rendering *the same filename* with different
   artwork also reused it. That is exactly what happens when a graphic is rebuilt in a
   corrected typeface: same name, same length, different pixels, stale clip.

Key on a hash of the source file. Then have the **verifier** recompute that hash from the
artwork on disk and assert the timeline references it — without that, every other check
passes just as happily with the superseded graphic, because nothing was looking at which
artwork it was. Negative-test it against the previous project version, where it must fail.

## If you use FCPXML instead

A round trip through FCPXML works and needs no Studio licence, with two traps:

- **Resolve ignores `srcEnable="video"`.** Picture clips must reference video-only asset
  twins, or the camera's embedded audio arrives with them.
- **A clip's `start` is in the asset's own timecode domain.** Declaring zero-based assets
  when a camera carries `15:49:12;18` drop-frame made Resolve refuse the import outright:
  *"No overlap between specified target timecodes."*

---

## Five more calls that report success and are wrong (measured 2026-09-23, Studio 21.1.0.17)

From building a 9:16 short with generated cards and burned-in captions.

### A STILL IGNORES `endFrame`, and the collision is silent

A PNG appended with `startFrame: 0, endFrame: 94` landed **150 frames** long — the still
clamp — and `SetClipProperty("Duration", "94")` did not change it. The next clip, appended at
its own `recordFrame`, then collided with the over-long still and **Resolve moved it**: the
track read 0 / 116 / **266** / 893 against a plan of 0 / 116 / **210** / 893.

⚠️ And the usual guards all passed. `AppendToTimeline` returned the full count, the track
reported no holes, and the last clip still ended exactly on the programme length. The only
symptom was a later stage noticing a clip at a position it had no plan entry for. **A "tiles
with no gap" check cannot see clips that slid, because they still tile.** Compare each item's
`GetStart()` against the plan position it was asked for, item by item.

The fix is not to fight it: **render a still to a clip of exactly the right length** and let
the frame count be a fact about the file, verified with `ffprobe -count_frames` before Resolve
sees it.

### Numbered stills in one folder import as an IMAGE SEQUENCE

`cue001.png` … `cue012.png` in one directory collapsed into **one** pool clip. Eleven of
twelve captions therefore never existed, and `ImportMedia` reported success for the one clip
it did make. Either break the numeric pattern in the filenames, or — better for captions —
build **one full-length ProRes 4444 alpha track** and lay it as a single clip. That also
removes every per-cue placement error at once.

⚠️ Assert the track really carries alpha (`pix_fmt` containing `a`, e.g. `yuva444p10le`). A
4444 file written without alpha composites as an opaque black rectangle over the whole frame.

### `SaveProject` is on the PROJECT MANAGER, not the Project

`project.SaveProject` is `None`, so calling it raises `TypeError` — *after* an otherwise
completely successful build. Use `projectManager.SaveProject()`.

### `SetCDL` needs `NodeIndex`

```python
cdl = dict(numbers); cdl["NodeIndex"] = "1"
g.SetCDL(cdl)
```

Without the key it returns False and the grade is simply **absent**. The vendor log→709 LUT
then renders alone, which looks like a washed-out picture rather than like a failure. And
there is no `GetCDL`, so this can only be confirmed by rendering.

### Loudness must be measured in the CHANNEL LAYOUT that ships

Resolve lays a **mono** clip onto a **stereo** track as dual-mono, which is **+3 dB** under
ITU-R BS.1770 because both channels carry the same signal. A mix written and measured as mono
at −14.0 LUFS rendered at **−11.0**. Two artefacts, each internally consistent, disagreeing
by 3 dB, with nothing comparing them. Write the stem in the delivery layout, and **assert the
render and the stem agree** rather than trusting either alone.

### The ffmpeg grade divergence is an operation-ORDER bug, not an ffmpeg fact

Worth knowing, because it changes how much the ffmpeg cross-check is worth. The 14–18% grade
divergence that made ffmpeg renders untrustworthy on an earlier project does **not** reproduce
when both paths apply the **log-fitted CDL before the vendor LUT** — which is Resolve's own
order (a node's corrector runs before its LUT). Measured on five picture frames of one short:
mean |difference| **1.3 codes of 255**, luma correlation **r = 0.9996**, mean luma within
**0.3%**. Keep shipping the NLE render, but a diff this tight is a real cross-check rather
than a formality.

---

## `CreateProject` does not always make the new project current

Measured 2026-09-25, Studio 21.1.0.17, with Resolve sitting on the **Project Manager** window.

`CreateProject(name)` returned a valid project object and the project appeared in the list —
while `GetCurrentProject()` still answered **"Untitled Project"**. Media then imported into the
returned object perfectly happily: bins were created, clips resolved, frame counts and
resolutions all read back correct. The first call to fail was `CreateEmptyTimeline()`, which
returned **None** with no further information, several stages into the build.

So the symptom appears nowhere near the cause, and everything between them looks healthy.

```python
proj = pm.CreateProject(name)          # object, and in the list
if not pm.LoadProject(name):           # ⚠️ do this explicitly
    sys.exit(...)
cur = pm.GetCurrentProject()
if cur is None or cur.GetName() != name:
    sys.exit(...)                      # and assert it, or later stages act elsewhere
proj = cur
```

The general shape of this one is the reusable part: **when a create call hands back a handle,
that is not evidence the application switched to it.** Assert the application's own idea of
what is current before doing anything that depends on it.

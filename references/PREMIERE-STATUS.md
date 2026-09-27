# Premiere backend — where it stands, and what to do next

⚠️ **EARLY ACCESS.** `nle-backends.md` recommends DaVinci Resolve and means it. This file is the
working state of the Premiere backend, for whoever picks it up next.

Last updated 2026-09-27. Everything below was measured on **Premiere Pro 26.5.1.1**, macOS.

---

## Capability state — `nle_ir.CAPABILITIES["premiere"]`

| | capability | evidence |
|---|---|---|
| ✅ | `clip` | placed at an exact frame, read back. Out point is **EXCLUSIVE**, same as Resolve's `endFrame` |
| ✅ | `crop` | Scale read back 88.88888549 against 88.888889 asked |
| ✅ | `alpha_track` | two full-length alpha tracks at frame 0 |
| ✅ | `render` | 2228 frames exactly, 1080x1920, 30000/1001, ProRes 422 HQ |
| ✅ | `fade_linear` | opacity keyframes at frames [0, 12, 71, 89], values [0, 100, 100, 0] |
| ✅ | `fade_shaped` | interpolation [5,5,5,5] against the linear clip's [0,0,0,0] |
| ✅ | `keyframes` | a Scale ramp 100 → 120 that moves |
| ✅ | `clip_disable` | reads back `disabled: true` |
| ✅ | `transition` | 12 frames asked, a **12-frame ramp rendered**, 50% luma exactly at the cut |
| 🔴 | `cdl`, `lut` | **impossible.** No scripted colour of any kind — see below |
| 🔴 | `speed` | no setter exists; retime the media first |

Two kinds of evidence earned those ✅s, and the difference matters: placement/crop/alpha/render came
from **rebuilding a real 2228-frame vertical short** from the same plan that built the shipped
Resolve master (mean **0.83 codes of 255**, graphics-only frames at 0.01–0.06). Fades, animation and
disable came from a **purpose-built synthetic timeline**, because that short's fades are rendered
into its alpha tracks and exercised nothing.

---

## 🔴 Running the capability check

```bash
# the panel version in --ping must match main.js, or you are testing old code
python3 scripts/verify_premiere_backend.py --make-media          # once; five synthetic clips
python3 scripts/verify_premiere_backend.py --preset <some.epr>   # build, read back, RENDER, judge
```

It needs **a project open in Premiere**. Generate the `.epr` with `make_export_preset.py` — the
shipped ProRes preset is pinned to 1920x1080.

🔴 **Always pass `--preset`.** Without it the transition's geometry is not checked at all, and the
run says so. See the next section for why that is not optional.

---

## 🔴 A transition cannot be verified from the timeline. Only from a render.

This is the most expensive thing learned about this API, so it is first.

- **`getTrackItems(TrackItemType.TRANSITION, false)` returns an array of the right length with
  `null` in every slot.** The count is real — a control track reports `0` — so it can tell you a
  transition *exists*. Its start and duration are unreachable, and `getStartTime()` on one throws
  `Cannot read properties of null`. There is no `hasTransition()` either.
- **`createRemoveVideoTransitionAction` is a silent no-op.** Removing at `TRANSITIONPOSITION_START`
  and at `_END` both committed without complaint and left the count at 1. So it cannot be used to
  probe which end a transition sits on.
- So the harness **renders and measures luma per frame**. The outgoing clip is disabled, which makes
  the dissolve a clean ramp off the black floor whose length and midpoint are both measurable:
  *12 frames asked, ramp spans 342..354, 50% exactly at the cut at 348.*

⚠️ **The trap this caught, and it is a real one:** a centred transition takes **half its duration
from each side of the cut**, so the outgoing clip needs a tail handle. Asking for 12 frames where
the outgoing clip used its whole source rendered **6 frames, entirely on one side** — with no
warning, the clip length unchanged, and the transition count reading exactly 1. It was indistinguishable
from a correct build by every check available on the timeline, and the first run of this test
reported it as ✅ CONFIRMED.

⚠️ **And lengthening the media did not fix it.** Premiere kept the cached 90-frame metadata from the
earlier import of the same filename, so the handle existed on disk and not in the project. The test
layout now makes the outgoing clip shorter than its source under *either* length Premiere might
believe, rather than depending on a re-import.

---

## What this API does that its own declarations do not say

Each of these cost a round trip. They are the reason `--probe` exists.

- 🔴 **`hasTransition()` DOES NOT EXIST.** The declarations carry a docstring saying *"Returns true if
  trackItem has transition"* — attached to `createRemoveVideoTransitionAction`, a mismatched comment
  in Adobe's own file. Calling it throws *"is not a function"*. A transition is its **own track
  item**: `track.getTrackItems(Constants.TrackItemType.TRANSITION, false)` — which yields a count
  and nothing else, because every item in it is `null`. See the section above.
- 🔴 **Transition position constants are on `VideoTransition`**, not `Constants`:
  `ppro.VideoTransition.TRANSITIONPOSITION_START` / `_END`. `Constants.TransitionPosition` is absent
  at runtime.
- 🔴 **`Keyframe.position` must be set explicitly.** `createKeyframe(value)` returns a keyframe at
  the default position, so four opacity keyframes all land at the same instant and each replaces the
  last — *asked 4, read back 1*, reported as a successful build.
- 🔴 **Interpolation belongs on the keyframe**, via `setTemporalInterpolationMode` with
  `Keyframe.INTERPOLATION_MODE_*`. It is **async**, so it must happen before the transaction.
  And a segment takes its shape from the keyframe it **leaves from** — shaping only the far end of a
  ramp reads back as `[0, 5, 0, 5]` and changes nothing visible.
- 🔴 **`ProjectItem` is a base type.** `name` and `type` are *properties*; `getMediaFilePath`,
  `getItems` and the in/out setters need `ClipProjectItem.cast()` / `FolderItem.cast()`. Duck-type
  the cast **result** — `Constants.ProjectItemType` is absent at runtime, and a *sequence* reports
  `type === 1` exactly as a clip does.
- 🔴 **A handle is invalidated by a project MUTATION**, not merely by an `await`. `applyCrop` gets
  away with awaiting a component chain because nothing changes in between; the audio sweep reusing an
  editor acquired before 2228 frames were placed threw *"The script object is no longer valid"*.
- 🔴 **`VideoFilterComponent` is declared `{}`** — an opaque handle. Parameters only exist once the
  filter is attached to a chain and read back as a `Component`.
- 🔴 **Parameter display names are not unique.** `AE.ADBE Opacity` has *two* params called
  `"Blend Mode"`; `AE.ADBE Motion` param 3 is named `" "`. And a name says nothing about type —
  matching "Input LUT" and handing it a string gives *"Illegal Parameter type"*.
- ⚠️ **Runtime `Constants` differ from the declarations**: `ExportType` values are strings,
  `InterpolationMode` is `LINEAR:0, HOLD:4, BEZIER:5`, `MediaType` values are opaque objects.
- 🔴 **`render` returns when Premiere ACCEPTS the export, not when the file is done** — ok in half a
  second for a job that takes a minute. Anything measuring the output must poll its frame count
  first, or it measures a truncated file and reports a content bug that is really a race.
- ⚠️ **Shipped `.epr` presets are PINNED**, not match-source: `Apple ProRes 422 HQ.epr` is
  1920x1080, so it renders a vertical sequence landscape. Generate one — `make_export_preset.py`.
- ✅ **29.97 is EXACT**: ticks/frame 8475667200, Δ0 seconds at frame 26688. `ticksPerFrame` is
  read/write. This is the opposite of Resolve, where the rate cannot be written at all.

## No scripted colour, established three ways

1. No ASC CDL entry point anywhere.
2. `createSetInputLUTIDAction` wants an opaque **ID**, not a path, with nothing documenting how to
   get one.
3. `AE.ADBE Lumetri` exposes **130 parameters and not one accepts a string**. `LUTAsset` and
   `LookAsset` exist but accept neither number, string nor boolean and are not keyframable.

And **no QE back door**: `ppro.QE/qe/enableQE/getQE/Advanced*`, `globalThis.qe/app/QE`, and
`require("premierepro-advanced"|"-internal"|"-qe")` are all absent. QE was ExtendScript; UXP has no
`app` object.

**The only route is to pre-render the grade into the media** and declare it in the plan's `baked`
list. `emit_ir.py --pregraded PATH --pregraded-base FRAME` does the rebasing; render the intermediate
**full raster** so the reframe still happens on the timeline and stays verifiable there.

---

## Architecture, and the reload situation

- The panel is `scripts/premiere/uxp/main.js`. Python talks to it through a job folder
  (`~/.film-shoot/premiere-jobs`) — see `premiere_bridge.py`. It **self-listens** on load.
- ⚠️ **A change to the panel needs a manual reload in the UXP Developer Tool.** There is no
  programmatic reload; `@adobe/uxp-devtools-cli` did not work on a first attempt and needs UDT's
  service running regardless.
- `PANEL_VERSION` is bumped on every edit so a stale panel is visible in `--ping`. **Always check the
  version before believing a result** — one debugging round was spent on a build running old code.
- The `call` and `program` ops exist so that **exploring the API and building both need no panel
  edit**. `program` is a small interpreter over declarative steps; `premiere_program.py` emits them.
  It enforces the transaction rule: a call inside a transaction returning a promise **throws**,
  because awaiting there commits an empty transaction.
- ✅ **The full build IS ported.** `premiere_program.py` emits the whole thing — sequence, raster,
  import, placement, crop, fades, animation, disable, transitions, audio — and asserts every
  read-back in Python. **Changing build logic now needs no reload.** Run it with
  `premiere_program.py --plan <ir.json> --run`, or `--verify-live` for a self-contained check.
- The panel keeps three things, and the line is "fixed primitive, not build logic":
  - `strip_audio` — `TrackItemSelection.createEmptySelection(cb)` hands its selection to a
    **callback**, which a declarative step list cannot express. It reports counts and refuses
    nothing; the decision to fail is made in Python.
  - `readback` — describing a component chain is a nested loop over runtime counts. The emitter uses
    it to resolve component and parameter **indices**, then compiles against them.
  - `render` — one call, no logic.
- `opBuild` remains in the panel as the older path. Both build the capability timeline to the same
  five CONFIRMED verdicts; `verify_premiere_backend.py --via-program` is the comparison.
- ⚠️ **A build is ~6 round trips regardless of size** — the loops unroll in Python at emit time, so a
  200-clip cut costs the same number of trips as a 5-clip one. The exception is the media index,
  which reads every item in the project root: that grows as a scratch project accumulates imports,
  and was 100+ items (200+ steps) after a day of test builds. Harmless, but it is why a fresh
  project indexes faster.
- ⚠️ `activeProject()` runs at the start of **every** program, so a closed project surfaces as "no
  active project" reported against whichever phase came next — which reads like a bug in that phase.
  `run_build` pre-flights it in one cheap call. This is also what a **crash** looks like from out
  here; Premiere died mid-build once during this work.

## Standing rules this backend is written under

- A backend that cannot do something must **FAIL, never approximate**. `UNVERIFIED` blocks unless
  `--allow-unverified` is passed deliberately. `False` blocks always — which is the distinction that
  stopped `transition`'s own test from running until it was moved to `UNVERIFIED`.
- **"ok with warnings" is a failed build.** `premiere_bridge.build()` raises on it. That rule is what
  caught the keyframe-position bug.
- Every op reports **what it read back**, never what it intended.
- A failed build leaves a **half-finished sequence** — no rollback across a build. Delete it and
  start again rather than reading a mix of two runs.

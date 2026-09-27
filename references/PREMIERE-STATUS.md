# Premiere backend — where it stands, and what to do next

⚠️ **EARLY ACCESS.** `nle-backends.md` recommends DaVinci Resolve and means it. This file is the
working state of the Premiere backend, for whoever picks it up next.

Last updated 2026-09-26. Everything below was measured on **Premiere Pro 26.5.1.1**, macOS.

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
| ⏳ | `transition` | **IMPLEMENTED, NOT YET PROVEN.** See "next step" below |
| 🔴 | `cdl`, `lut` | **impossible.** No scripted colour of any kind — see below |
| 🔴 | `speed` | no setter exists; retime the media first |

Two kinds of evidence earned those ✅s, and the difference matters: placement/crop/alpha/render came
from **rebuilding a real 2228-frame vertical short** from the same plan that built the shipped
Resolve master (mean **0.83 codes of 255**, graphics-only frames at 0.01–0.06). Fades, animation and
disable came from a **purpose-built synthetic timeline**, because that short's fades are rendered
into its alpha tracks and exercised nothing.

---

## 🔴 THE NEXT STEP, precisely

`transition` is implemented and `UNVERIFIED`. To finish it:

```bash
# 1. the panel on disk is 1.7.0-transitions2 — RELOAD it in the UXP Developer Tool
python3 scripts/verify_premiere_backend.py --make-media     # once; five synthetic clips
python3 scripts/verify_premiere_backend.py                  # build, read back, judge
```

It needs **a project open in Premiere**. If all five read CONFIRMED, promote `transition` to `True`
in `nle_ir.CAPABILITIES["premiere"]` and nothing else.

⚠️ **The open question.** The test's transition clip sits at frame 360 with **nothing before it**, so
the dissolve may come out single-sided, or Premiere may refuse it outright. `setForceSingleSided`
exists in the API, which suggests the distinction is real. If the transition count reads 0, that is
the first thing to check — and the fix is a test timeline with **two adjacent clips** rather than an
isolated one.

---

## What this API does that its own declarations do not say

Each of these cost a round trip. They are the reason `--probe` exists.

- 🔴 **`hasTransition()` DOES NOT EXIST.** The declarations carry a docstring saying *"Returns true if
  trackItem has transition"* — attached to `createRemoveVideoTransitionAction`, a mismatched comment
  in Adobe's own file. Calling it throws *"is not a function"*. A transition is its **own track
  item**: `track.getTrackItems(Constants.TrackItemType.TRANSITION, false)`. Better evidence anyway —
  a count and a position rather than a boolean.
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
- ⏳ **The full build has NOT been ported to `program` yet.** `opBuild` in the panel is still the
  working path, so build changes still cost a reload. Porting it is the remaining piece of that work,
  and it is what would make reloads genuinely rare.

## Standing rules this backend is written under

- A backend that cannot do something must **FAIL, never approximate**. `UNVERIFIED` blocks unless
  `--allow-unverified` is passed deliberately. `False` blocks always — which is the distinction that
  stopped `transition`'s own test from running until it was moved to `UNVERIFIED`.
- **"ok with warnings" is a failed build.** `premiere_bridge.build()` raises on it. That rule is what
  caught the keyframe-position bug.
- Every op reports **what it read back**, never what it intended.
- A failed build leaves a **half-finished sequence** — no rollback across a build. Delete it and
  start again rather than reading a mix of two runs.

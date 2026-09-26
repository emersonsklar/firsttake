# 7 — A field guide to quiet failures

Every case below is real, shipped or nearly shipped, and passed every check that existed at the time. They
are collected here because the shapes recur — the specific application changes and the shape does not.

**The question that catches most of them:** *if the step I am verifying had not run at all, would this check
still pass?* If yes, the check is measuring the wrong thing.

---

## Triage by symptom

| What you are seeing | Go to |
|---|---|
| The render says Complete and the file is wrong | [A1](#a1--8-frames-of-26688-reported-complete), [A2](#a2--a-byte-identical-render-reported-complete), [A6](#a6--loudness-3-db-out-in-the-wrong-channel-layout) |
| Playback is wrong but the app looks happy | [A3](#a3--a-project-playing-at-08008-with-a-contented-green-24) |
| Clips are in the wrong places and nothing errored | [A4](#a4--a-still-that-ignored-its-own-length-and-slid-the-next-clip), [A5](#a5--numbered-pngs-imported-as-one-image-sequence) |
| The build says "no problems" and the output is the wrong length | [B1](#b1--a-stale-derived-cache-of-absolute-frame-numbers) |
| An old version of an asset is on the timeline | [B2](#b2--a-cached-asset-keyed-on-filename-instead-of-content) |
| A call returned success and had no effect | [C1](#c1--calls-that-report-success-and-do-nothing), [C2](#c2--two-ffmpeg-filters-that-report-success-and-do-nothing) |
| A guard has never fired and you are not sure it can | [C3](#c3--a-guard-whose-window-equalled-its-own-minimum), [D3](#d3--a-test-that-disappears-when-it-starts-passing) |
| A number looks suspiciously perfect | [D1](#d1--numbers-too-good-to-be-true), [D2](#d2--an-answer-sitting-on-a-search-window-edge) |
| Two pipelines disagree and you wrote the disagreement down | [D4](#d4--unexplained-but-stable-eight-approved-renders-that-did-not-show-the-deliverable) |
| The picture jumps and every check is green | [E1](#e1--four-jump-cuts-that-shipped-with-every-other-check-passing) |
| A guard is failing a build you know is correct | [F1](#f1--a-guard-that-encoded-taste-and-failed-a-correct-build) |
| The machine seems to have died | [G1](#g1--a-locked-mac-presents-as-three-things-at-once) |

---

## A — "Complete" is not "correct"

### A1 — 8 frames of 26688, reported Complete

**Symptom.** A review render came back with the queue reporting **Complete**, a valid MP4, the right
resolution, the right frame rate — and **8 frames out of 26688.**

**Why it was invisible.** `MarkIn`/`MarkOut` **persist on the project.** A verifier had rendered a short probe
window hours earlier and left them set. Every property anyone thought to check was correct.

**The check.** Pass the "whole programme" flag explicitly on every full render (`SelectAllFrames` in Resolve,
`exportFull = true` in Premiere), *and* **measure the delivered file's frame count against the programme's
own total.** That is the check that caught it.

### A2 — A byte-identical render, reported Complete

**Symptom.** Two exports of the same timeline, taken after changing a setting in the UI between them, came
back with the same size, the same `framemd5` and **PSNR = inf on every frame.**

**Why it was invisible.** The setting's analysis had not been re-run, and nothing said so. Every downstream
metric then agreed to four decimal places, which is the only reason it was caught at all.

**The check.** **Hash each export against the previous ones and fail loudly on a match.** A setting that
produced an identical file did not take.

### A3 — A project playing at 0.8008× with a contented green "24"

**Symptom.** Audible crackle on playback. Roughly **20% of the audio** dropped — measured later from a screen
capture as 8–9 ms of silence every 42 ms, 20.4% of samples missing.

**Why it was invisible.** A freshly created project sets `timelinePlaybackFrameRate` to **24**, and setting
`timelineFrameRate` does not change it. `SetSetting` and `SetSettings` refuse every format offered (`29.97`,
`29.970`, `30000/1001`, `30`, float, empty string, before and after media). The build asked, was refused, and
**did not look.** The toolbar shows a contented green `24` — the application is not struggling, it is doing
what it was told. The render itself was clean throughout, so the content was never at fault.

The false trails were expensive: 4K decode load, a runaway `ffprobe` saturating the SSD, a clipping report
that turned out to be an artefact of the diagnostician's own stereo→mono downmix, CoreAudio, five virtual HAL
drivers. What identified it was two operator observations: **the audio-only and compositing pages sounded
fine** (neither composites the multi-track video), and **the timecode advanced only 13:03 during a 16.37 s
screen recording** — 0.80×, which points straight at 24/29.97.

**The check.** ✅ **Read every setting back and compare, and check the playback rate FIRST, before anything
else.** Three lines. And the fix is a committed project settings preset applied immediately after
`CreateProject`.

⚠️ **A second lesson lives inside this one.** An earlier note said the rate was "not writable through the
API." That was generalised from one method to the whole API and it was wrong; the operator pushed back and the
preset route turned up immediately. **If a setting refuses to write, look for another mechanism before
concluding it cannot be done.**

### A4 — A still that ignored its own length and slid the next clip

**Symptom.** A PNG appended with `startFrame: 0, endFrame: 94` landed **150 frames** long — the still clamp —
and `SetClipProperty("Duration", "94")` did not change it. The next clip, appended at its own `recordFrame`,
then collided with the over-long still and **the application moved it**: the track read 0 / 116 / **266** /
893 against a plan of 0 / 116 / **210** / 893.

**Why it was invisible.** ⚠️ **Every usual guard passed.** `AppendToTimeline` returned the full count, the
track reported no holes, and the last clip still ended exactly on the programme length. **A "tiles with no
gap" check cannot see clips that slid, because they still tile.** The only symptom was a later stage noticing
a clip at a position it had no plan entry for.

**The check.** Compare each item's `GetStart()` against the plan position it was **asked for**, item by item.
And do not fight the clamp: **render a still to a clip of exactly the right length**, verified with
`ffprobe -count_frames` before the NLE sees it, so the frame count is a fact about the file.

### A5 — Numbered PNGs imported as one image sequence

**Symptom.** `cue001.png` … `cue012.png` in one directory collapsed into **one** pool clip. **Eleven of
twelve captions therefore never existed**, and `ImportMedia` reported success for the one clip it did make.

**The check.** Break the numeric pattern, or pass the "not a numbered still sequence" argument where the API
offers one. ✅ **Better in both applications: build ONE full-length ProRes 4444 alpha track and lay it as a
single clip**, which removes every per-cue placement error at once. ⚠️ Then **assert the track really carries
alpha** (`pix_fmt` containing `a`, e.g. `yuva444p10le`) — a 4444 file written without alpha composites as an
opaque black rectangle over the whole frame.

### A6 — Loudness 3 dB out, in the wrong channel layout

**Symptom.** A mix written and measured as **mono** at **−14.0 LUFS** rendered at **−11.0**.

**Why it was invisible.** The NLE lays a mono clip onto a stereo track as **dual-mono**, which is **+3 dB**
under ITU-R BS.1770 because both channels carry the same signal. **Two artefacts, each internally consistent,
disagreeing by 3 dB, with nothing comparing them.**

**The check.** Write the stem in the **delivery layout**, and assert the render and the stem agree (within
1 dB) rather than trusting either alone.

⚠️ The same family: **do not measure through a transformation you added.** A master was reported clipping at
+0.93 dBFS with 3636 full-scale samples. The overs were created by the measurement's own stereo→mono downmix
adding 3 dB; per channel the file measured **−2.09 dBFS with zero overs.** Measure the thing that ships, in the
form it ships.

---

## B — Caches that outlive their truth

### B1 — A stale derived cache of absolute frame numbers

**Symptom.** The build **reported "no problems"** and rendered **33 frames too long.** Only the render
verifier caught it.

**Why it was invisible.** A module correctly refused to run when the *source* data stated music positions in
absolute frames that no longer fitted a shortened programme — a good guard, and it fired. It did **not** check
the **derived** cache built from those numbers, which was three cuts stale.

**The check.** **Anything that caches absolute frame numbers needs the same guard as the file it came from.**
Prefer caching the rule and deriving the frames; where a cache must hold absolute positions, validate it
against the current programme length before use.

### B2 — A cached asset keyed on filename instead of content

**Symptom.** A superseded graphic survived a source change and stayed on the timeline.

**Why it was invisible.** Two versions of the same bug shipped. Keyed on **frame count**: switching to
different artwork of the same length reused the old render. Keyed on **frame count + filename**: re-rendering
*the same filename* with different artwork also reused it — which is exactly what happens when a graphic is
rebuilt in a corrected typeface. **Same name, same length, different pixels.**

**The check.** Key on a hash of the source file, and have the **verifier** recompute that hash from the
artwork on disk and assert the timeline references it. Without that, every other check passes just as happily
with the superseded graphic, because nothing was looking at which artwork it was. Negative-test it against the
previous project version, where it must fail.

⚠️ And when you are asked "what has not been rendered yet", **run the verifier, not your memory.** On one job
it reported exactly one failure: a graphics clip whose content hash no longer matched its regenerated source
art. ⚠️ **mtimes lie here.** A data file was timestamped 7 minutes *newer* than the build record, reading as
"the edit changed after the build"; the clip-by-clip comparison passed and every seam was covered, so the
timestamp was noise — a backup write or a no-op save moves it. **A content hash is signal; a modification time
is not.**

---

## C — Calls and guards that report success and do nothing

### C1 — Calls that report success and do nothing

The category that wastes a day, because the build prints no problems.

| Call | What actually happens |
|---|---|
| `SetTrackEnable` | returns `True` and has **no effect**. Use `SetClipEnabled` per item. A "disabled" track still compositing invalidates every A/B comparison you then make against it |
| `SetFades` positional | returns `False` and silently does nothing. It takes a **dict**: `SetFades({"fadeIn": n, "fadeOut": n})` |
| `AddTransition` | returned `None` for **all seven** signatures tried |
| `SetCDL` without `NodeIndex` | returns `False` and **the grade is simply absent** — the vendor log→709 LUT then renders alone, which looks like a washed-out picture rather than like a failure. And there is no `GetCDL`, so this can only be confirmed by rendering |
| `SetLUT` with a path outside the LUT library | returns `False`. Not permissions, not path format. Write generated cubes into the library and call `RefreshLUTList()` — the library is cached at load |
| `SetLUT` with a non-ASCII byte anywhere in the cube | returned `False` on all twelve clips. A UTF-8 em-dash **in a comment line** did it |
| `GetProperty("AnyNameAtAll")` | returns `False` — not `None`, not an error. **It cannot be used to test whether a property exists.** Only `SetProperty` returning `True` is evidence |
| `project.SaveProject` | is `None`, so calling it raises `TypeError` *after* an otherwise completely successful build. It lives on the **project manager** |
| Awaiting inside a UXP `executeTransaction` | commits an **empty transaction**, which returns without error and does nothing |

**The check, for all of them.** Read the thing back and compare. The backend helper for the transaction case
**asserts its callback actually ran**, because an empty transaction is otherwise indistinguishable from a
successful one.

### C2 — Two ffmpeg filters that report success and do nothing

**A chain of per-cue image overlays.** With single-frame PNG inputs, an overlay that starts *disabled* never
reads its secondary input, so by the time its `enable` window arrives the image has hit EOF with nothing
buffered and the picture passes straight through. **Only the cues live at `n=0` rendered — 2 of 12 — with
every frame count correct.** Adding `-loop 1` fixed those two, but looped images default to **25 fps** and
drifted against a 29.97 main, dropping two more. ✅ **Build ONE full-length alpha track and composite once.**

**`ebur128` prints its summary at INFO level**, so `-v error` suppresses exactly the lines being parsed and
every loudness figure comes back empty. Same family as `-map a:0` (invalid syntax, also silent). ✅ **A
measurement function must fail loudly when it parses nothing** rather than returning NaN — a report reading
`true peak ?` shipped once for this reason.

Also: **stream-copy-concatenated ProRes carries discontinuous timestamps.** Feeding that into a filter graph
made the encoder drop frames — 1037, then 1032, of 1043 — while both inputs verified at exactly 1043, and
`ffprobe -count_frames` and a raw decode **disagreed with each other** about the result's length. That
disagreement is the signature. Force `-fps_mode cfr` on the output.

### C3 — A guard whose window equalled its own minimum

**Symptom.** A field a check populates simply stayed empty for a whole file, everywhere. Nothing looked wrong.

**Why it was invisible.** The fricative test called a voicing routine on one 20 ms hop = **960 samples**,
against that routine's own `len < SR//50` = **960** floor, via a caller guard of `len(seg) > SR // 50`. Exactly
equal, so false at **every** window, so the HF measurement was 0.0 for the whole file and **the test could
never fire.**

**The check.** ✅ **When a check silently never triggers, compare its window against its own minimum.** And
negative-test every guard: a check that has never failed has never been tested.

Also in this family: ⚠️ **a crashed build and a build that reported a problem are not distinguishable** if you
grep for both patterns and count — both give 1. That cost a round of reading a **stale** report and believing
the previous build's numbers. Check the exit status, or assert the output file's mtime moved.

---

## D — Measurements that were never measurements

### D1 — Numbers too good to be true

**"The two cameras match to 0.0001."** A left-half chroma mask had been compared against a whole-frame one.
The real figure was 100× worse.

**"Every probe scored 0.000."** Read as "all 27 frames are wrong". The real cause was an ffmpeg invocation
missing its input argument, producing no output at all.

**"0 of 0 covered."** A document reader handled one client's layout and silently extracted nothing from the
next. The report read as a clean sheet.

**A transcript missing its entire cold open.** A source→programme lookup returned only the first match, and
that stretch of source is delivered twice.

**A build reported no problems with 28 crops silently unapplied.** A helper that split clips had dropped one
field, so the framing data never reached the timeline. Nothing errored, because nothing looked.

**The rules.** ✅ **Assert a measurement produced output before interpreting its value.** ✅ **Any `N of M`
where M is 0 must fail loudly.** ✅ **A number that good on a first attempt is a bug report** — 0.0001
agreement, 1.0000 correlation, 100% coverage, zero drift: each of those has been a broken measurement here at
least once. Treat a suspiciously perfect result as an alarm, then go and earn it.

### D2 — An answer sitting on a search-window edge

**Symptom.** A boundary check reported exactly **"+300.0 ms"** on two different words, to one decimal.

**Why it was invisible.** That was the ±300 ms search window's **edge**, reported twice because the real
answer lay outside it both times. 🔴 **An identical round error across independent items is a clipped search,
every time.** The same shape produced exactly `+0.050 s` from an out-point finder on two unrelated clips —
its search cap, not the audio.

**The check.** **Return where the answer was found, and whether it was found at a boundary**; flag and exclude
a result that landed on the first or last position of its window rather than averaging it in. And **clamp the
window to something meaningful, then widen it** — that same search was also reaching *backwards past the
previous word's end*, so widening alone would have made it worse. Once both were done, the two 300 ms "errors"
became one −23 ms (correct all along) and one genuine disagreement with an explanation. Nothing about the
underlying audio had changed.

### D3 — A test that disappears when it starts passing

A check split frames into "graded" and "control" **by whether their gain deviated from unity** — i.e. by the
quantity under test. So the moment the two pipelines were reconciled, every frame became a control, the graded
set went empty, and **the check could never fire again.**

✅ **Never classify test cases by the quantity under test.** Label cases from the structure that defines them
— here, which shots carry a grade according to the edit.

### D4 — "Unexplained but stable": eight approved renders that did not show the deliverable

**Symptom.** A fast ffmpeg proxy, built alongside the NLE render as a cheap way to watch the cut, disagreed
with the NLE by a clean **~1.15×** on every graded clip.

**Why it was invisible.** The gap was noticed, measured, written down, labelled *"unexplained but stable"* —
and guarded by a tripwire asserting **the gain had not moved since the previous build.** It never moved.
**Eight review renders were approved that did not show the deliverable.**

**Root cause: the grade existed twice.** The builder held the ASC CDL as four numbers and gave them to the NLE
live; the proxy read a pre-baked `.cube` of "the same" CDL. The cube did not implement it — **0.3944 at
mid-grey where the CDL says 0.4542**, a ratio of 0.868, and **1 / 0.868 = 1.152** against the measured 1.1543.
Nothing ever compared the two representations.

**The checks.**

1. 🔴 **Derive, do not duplicate.** Generate the second representation from the first at run time. With
   `power = 1.0` an ASC CDL is affine, and trilinear interpolation of an affine function is *exact*, so a
   generated 33-node cube reproduces the arithmetic to 0.0001/255. **There is no accuracy argument for a stale
   bake.**
2. 🔴 **"Unexplained but stable" is a decision to ship the error indefinitely.** Where a correct absolute
   value is knowable — two pipelines rendering the same edit *should* agree — assert **that**. Keep
   change-detection for quantities that genuinely have no known-good value, and say which mode you are in.
3. **A stray bake is contagious.** Where the bad cube came from was never established, so anything else that
   ever consumed it carries the same error. Renaming it `BROKEN_*` and writing down why beats deleting it.

⚠️ **And the corollary that reaches backwards: any measurement taken through an unvalidated pipeline is
provisional.** A confident, well-documented colour identification made through this proxy **reversed
completely** once the proxy was fixed. Keep enough of every measurement — region, inputs, pipeline — that it
can be re-run when the chain changes. **A colour number without its sampling region and its pipeline is an
opinion with a decimal point.**

---

## E — Checks that were never asked for

### E1 — Four jump cuts that shipped, with every other check passing

**Symptom.** The subject visibly jumps at a cut: posture, hands and head position all differ.

**Why it was invisible.** The programme splices ranges up to **25 minutes** apart, and where the same angle
ran across a seam the discontinuity was naked. **Every other check passed the whole time** — the clips were
exactly where the plan said, every render probe found its requested frame, the grade and graphics were right.
**No check was asking whether a cut changed the framing**, which is the only property that matters here.

**The check.** Walk the visible picture frame by frame, find every point where the visible source frame does
not advance by exactly one, and assert the visible (lane, source, framing) **changed** there. 33 seams found;
4 uncovered before the fix, 0 after. Negative-test it against the previous shot data, where it must name all
four by frame with the size of the leap.

### E2 — A push-in verified with four still frames

**Symptom.** A slow push-in on a locked-off wide shot shipped visibly broken, reading on screen as handheld
shake.

**Why it was invisible.** It was verified by sampling four still frames and confirming the image got
progressively tighter. It did. **Tightening was never the question — smoothness was, and a single frame cannot
show motion.** The filter was rounding its crop rectangle to whole pixels, holding for five frames and then
snapping one, about five times a second.

**The check.** 🔴 **Name the failure you are afraid of, then ask whether the check could see it.** "Is it
smooth" is a question about change *between* frames, so the evidence has to span frames. One frame-to-frame
difference measurement would have caught it instantly. **Static evidence cannot settle a dynamic claim, and a
summary statistic cannot settle a claim about a specific moment.**

### E3 — A guard whose reference window was where the signal was not

**Symptom.** A check written to catch a music bed masking a voiceover reported the bed as **"within
−203.6 dB"** of her voice.

**Why it was invisible-ish.** That absurdity was luck — the same two faults could as easily have produced a
plausible number and a green tick.

1. **It referenced the wrong span.** The window it was handed was the deliberate *silence after* the VO, so
   "her speech level" measured −240 dBFS, the gate accepted every block, and the comparison was against
   nothing at all.
2. **Its gate was a noise floor**, which accepts the **decay of a final syllable**. A decaying word is ~20 dB
   below speech proper, so the tail set the reference and the check reported masking at the one place a fade is
   always closest. Gate on being within ~15 dB of the speaker's **own 90th-percentile** level instead.

And the premise was backwards before either: it failed any fade that merely *overlapped* the VO. Measured,
separation **improved** monotonically as the fade descended (+10.7 → +30.3 dB) — a bed dying away under the end
of a line *exposes* a voice. ✅ **Assert the thing you fear (proximity while she is speaking), not a proxy for
it (overlap).**

⚠️ **A per-section measurement needs a per-section reference, and this one recurs.** Two variants inside two
days, the second written *immediately after* fixing the first, so treat it as an attractor rather than a slip.
A balance check compared each section of a mix against **one whole-programme** figure of +1.38 dB; the sections
came from two source recordings with genuinely different balances (−1.32 dB and +1.51 dB), so the pooled number
was dominated by whichever section was loudest and was the wrong reference for either. It reported a 2.70 dB
error that did not exist.

⚠️ **And a ratio between two different statistics is not a ratio.** A guard meant to stop a mute span
silencing real dialogue compared the span's **peak** against the lane's **RMS** 90th percentile. Speech peaks
run 10–15 dB above its RMS, so every span looked loud enough to be a line and it refused a correct edit —
reporting 4.7 dB of headroom where the honest RMS-to-RMS figure was **23.2**. Name the statistic on both sides
of every threshold.

### E4 — A reference that was a function of the variable

**Symptom.** "The fix didn't work as much as predicted." An earcon had to finish before the speaker's next
word; moving it five frames earlier looked like it bought **167 ms** of clearance and delivered **52 ms**.

**Why it was invisible.** The build found her speech onset by searching **forward from the earcon's end**, so
the onset it reported *moved when the earcon moved*. The 167 had been computed against an "onset" that was
merely the first thing found after the earcon's **old** position.

**The check.** 🔴 **Measure the boundary independently of the object being positioned**, then compare. Her real
onset was a fixed property of the recording all along — per-frame RMS rising 12 dB above a floor taken from a
span verified silent. **The general form: a reference must not be a function of the variable.**

---

## F — Guards that fail correct work

### F1 — A guard that encoded taste and failed a correct build

**Symptom.** A guard failed a build that was exactly right.

**Why it is the most dangerous shape here.** An audio build asserted *"the earbud sections must be LEFT-biased
and the speaker section RIGHT-biased."* True of the design it was written for, and it passed for four versions.
Then the client asked for the quiet device **centred**, and a centred pan leaves those sections at the source
recording's **own −1.32 dB right bias**, which is not left-biased and never could be.

🔴 **A guard that encodes taste has to be edited every time the taste changes, and the moment you are editing
it is the moment you least want it to argue with you.** The overwhelming temptation is to delete it, and then
it never catches anything again.

**The fix — ask what the creative choice was an *instance* of:**

| don't assert | assert |
|---|---|
| "this section is left-biased" | "this section is displaced from **its own** unprocessed balance in the direction the config asks, by roughly the amount that control produces" |
| "the gain here is −14 dB" | "the two sections differ by the step the config declares" |
| "shot 3 is the wide" | "the angle **changed** across this splice" |

**The tell that you have written the left-hand kind: the guard restates a number that also appears in your
config.** That is a tautology with extra steps.

Two corollaries worth having in advance:

- **Identity cases must be asserted as identities.** A pan of 0 is not "roughly centred", it is an **exact**
  no-op, and asserting it to 0.00 is what proved a whole rewrite landed. "Neutral" settings are where a
  sign-based guard quietly has no opinion.
- **The unprocessed material is not neutral, and that is not a fault.** The reference for "did my control do
  what it claims" is the material's own measured state, never zero. A guard demanding zero is demanding you
  *correct the source*, which is usually the opposite of what was asked.

### F2 — A guard that could not decide, and should have escalated

Off-mic direction bleeding onto a lav sat only ~**8 dB** below the subject's own voice, which is unremarkable
for someone speaking in a small room. **Level alone cannot tell a director from a line.** The honest design is
neither a looser threshold nor a hard refusal:

- pass it on measurement where the measurement is decisive;
- otherwise require an **explicit human assertion with a reason**, recorded in the config;
- and have the report state *which* of the two happened — `"measured"` versus `"ASSERTED by the operator; the
  level test cannot settle it"`.

✅ **A check that cannot decide should escalate and record, not silently widen.**

### F3 — Do not build a classifier on a weak signal

Tempting pattern: take a noisy metric, add logic to decide what the user *meant*, suppress the warning when
the logic says it is benign.

Tried on focus stability — classify whether sharpness dips sat at the ends of a clip (subject entering or
leaving) or in the middle (autofocus hunting), and downgrade the former. **It flipped verdict depending on
which frames got sampled**, because encoders smear ramp frames across a transition.

🔴 **A coin-flip guarding an unrecoverable failure is worse than a warning you have to read.** What shipped
instead: report both numbers — variation over all samples, and over the steadiest run — and let the human
classify in one glance.

---

## G — Environment failures that mimic hardware faults

### G1 — A locked Mac presents as three things at once

**Symptom.** A build process at **0% CPU for 19 minutes**, its output file **0 bytes**, and `screencapture`
returning **only the desktop wallpaper**.

**Why it is confusing.** `scriptapp("Resolve")` **blocks for ever** — no timeout, no error — and it *launches*
the application rather than attaching, so on a locked machine both processes sit at 0% indefinitely. `pgrep`
matching the app name is **not** evidence it was already running: compare its elapsed time against your own
process. No script can work around it; the machine has to be unlocked.

⚠️ **And a killed build leaves its project behind**, so the next version number may already be taken. Check
the list rather than assuming the next number is free.

### G2 — A managed laptop blocking external storage, exactly like a dying drive

**Symptom.** A drive mounts, its model and serial read correctly, and every open of it fails — **even as
root**. Indistinguishable from failing hardware.

**Why it is confusing.** On a managed machine this is USB device-control policy, not a privacy/TCC setting and
not the disk. **Check the approval path before diagnosing hardware.**

### G3 — An empty card reporting gigabytes used

A wiped 1 TB exFAT card reported **3.1 GB** used while `du` and `find` showed under 400 KB. That is allocation
structure on a large volume. **Trust `find`/`du` over the volume counter** before concluding a card did not
clear.

---

## H — Two structural bugs worth naming, because they destroy other work

- **A per-item tool that writes a shared file must merge or namespace.** Two instances in one session, both of
  which destroyed earlier work and surfaced much later: a framing measurer run for item 2 rewrote the whole
  measurements file and **deleted item 1's entry**, breaking item 1's verifier long after the run that caused
  it; and an alignment step wrote a shared words file, so aligning one clip **overwrote another's word
  timings.** Neither errored. **The tell is a tool that takes a `--which` argument and writes a file named for
  the project.**
- **Two copies of one string are two answers, and the divergence is silent.** A derived artefact must **read**
  the canonical string from whatever owns it and fail loudly if it is missing, keeping the superseded field as
  provenance marked no-longer-used. ✅ Predictable side effect worth stating in advance so it does not look like
  a bug: once several cuts share one title, **their cards become byte-identical.** That is the invariant
  working, not a caching error.
- **Delete `__pycache__/` after reverting a planted fault.** Stale bytecode made a restored file look broken
  and nearly sent a correct fix back for rework.

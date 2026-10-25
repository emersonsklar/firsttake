# Tools

Standalone. Plain Python and ffmpeg — every one takes `--help`, most take `--selftest`, and none needs a model, an API key or a network. An assistant makes them easier to drive; it is not required to run any of them.

| | |
|---|---|
| `shootcheck.py` | technical QC for footage and audio |
| `make_fixtures.py` / `verify_fixtures.py` | build clips with exact planted faults, assert each is caught |
| `offload.py` | verified card offload with MHL / ASC MHL manifests and an HTML report |
| `offload_selftest.py` | plants corruption, deletions and chain breaks, asserts each is caught |
| `build_day_sheets.py` | render a day-sheet HTML to PDF and assert the result |
| `sync_cameras.py` | measure offset **and drift** between recordings with no shared timecode |
| `diarize_by_channel.py` | attribute speech to speakers by comparing per-speaker channels; reports microphone isolation |
| `measure_white_balance.py` | per-clip colour cast from each clip's own near-neutral pixels |
| `fit_clip_trims.py` | turn those measurements into per-clip white balance + black point, baked into per-clip LUTs |
| `speed_ramp.py` | speed a shot up with real motion blur to an exact output length; solves the highest ramp peak a source allows |
| `conform_graphic.py` | vet and conform a supplied motion graphic — raster, VFR, and where the animation actually is |
| `build_subtitles.py` | subtitles from forced-aligned words: programme-clock mapping, cue shaping to the delivery raster, and two cross-checks |
| `bake_cdl_into_lut.py` | fold an ASC CDL into a vendor LUT so the grade survives an application that cannot script a CDL |
| `nle_ir.py` | the NLE-neutral timeline plan: validate it, compare what each application can build, derive each one's crop parameters |
| `premiere_program.py` | emit a Premiere build as a declarative PROGRAM, so build logic lives in Python and the panel stops needing reloads |
| `verify_premiere_backend.py` | prove on a real timeline what the Premiere backend can do, with synthetic media |
| `premiere_bridge.py` | drive Premiere Pro from Python through the UXP panel in `premiere/uxp/` — **`--probe` first** |
| `nle_handover.py` | pack a cut for someone using the other NLE, and let them verify it arrived whole |
| `scrub_check.py` | refuse to publish credentials or client-identifying material |
| `doctor.py` | is this computer ready? plain-English checklist; `--fix` installs what's missing (asks first) |
| `first_run.py` | one-minute tour on practice footage: offload, verify, catch a planted fault, QC |

## Run the harnesses after any edit

A measuring tool that silently stops detecting a fault is worse than no tool,
because it is trusted.

```bash
python3 offload_selftest.py
python3 make_fixtures.py && python3 verify_fixtures.py
python3 sync_cameras.py --selftest
python3 diarize_by_channel.py --selftest
python3 measure_white_balance.py --selftest
python3 fit_clip_trims.py --selftest
python3 speed_ramp.py --selftest
python3 conform_graphic.py --selftest
python3 build_subtitles.py --selftest
python3 bake_cdl_into_lut.py --selftest
python3 make_export_preset.py --selftest
python3 nle_ir.py --selftest
python3 premiere_bridge.py --selftest
python3 nle_handover.py --selftest
python3 premiere_program.py --selftest
python3 verify_premiere_backend.py --selftest
python3 build_day_sheets.py --selftest
python3 scrub_check.py --selftest
python3 doctor.py --selftest
python3 first_run.py --selftest
```

The three NLE harnesses need neither application installed. `nle_ir` asserts that both backends'
crop maths agree on the same crop rectangle — two independent derivations of one intent, which is
how a units bug in the vertical axis was caught within a minute of being written.
`premiere_bridge` stands in a fake panel to exercise the transport, and asserts that *ok with
warnings* is treated as a failed build. `nle_handover` plants a file that changes content while
keeping its size, which is exactly what a re-rendered graphic looks like.

The four post tools plant ground truth too, and two of them document a known weakness rather
than hiding it:

* `measure_white_balance` plants an exact cast on a synthetic frame and asserts recovery — tightly
  on a **multi-surface** frame, which is what the estimator is for, and as a **bound with a
  direction** on a uniform grainy field, where "least saturated" is structurally biased toward
  zero and can only be reduced, not removed. It also checks the case grey-world gets wrong.
* `fit_clip_trims` runs the whole chain — cast → fit → bake → sample the cube off-node — and
  asserts a tinted grey comes back neutral, that composition is exact, and that the cube is
  ASCII+CRLF (Resolve's parser fails silently on a non-ASCII byte).
* `speed_ramp` asserts the branch arithmetic tiles the source with no gap or overlap, that the
  select expression takes the **last** frame of each blur window rather than the unblurred index 0,
  and that a ramp reaches a higher peak than a constant speed for the same source budget.
* `build_subtitles` plants voiced bursts at times it chose, declares the words in a *different*
  source timebase behind a non-zero placement offset and a 400 ms internal splice, and asserts the
  cues land on the planted speech — so the clock arithmetic has to be right for the check to pass
  at all. Then it plants faults: a wrong placement, a caption inside a span declared not-a-line, a
  span opening mid-word, and a deliberately too-narrow search window. It also records a **bound**:
  the stem check catches a 3 s placement error and does **not** catch 1.2 s, because inside a dense
  read a shifted caption still lands on some word. That is asserted rather than tuned away — the
  tempting fix is to make the check look stronger than it is.
* `conform_graphic` generates a healthy card and a **broken** one — animation collapsed into a
  fraction of a second, then frozen — and asserts the profiler tells them apart. That is the check
  that caught a real broken delivery.

The post-stage harnesses plant ground truth rather than checking self-consistency:
`sync_cameras` plants known offsets and a 1000 ppm clock difference and asserts the
offset and the drift both come back, plus a **partly-overlapping** pair — the case that
once produced a confident wrong offset and −354294 ppm of "drift" — and asserts both that
the offset is right and that the non-overlapping probes were rejected;
`diarize_by_channel` plants a two-lavalier
conversation with known speakers and known isolation and asserts the attribution,
the isolation figure, and its refusal to run on one mix copied twice.

`shootcheck.py` also cross-checks its own pixel maths against ffmpeg's independent
`signalstats` filter:

```bash
python3 shootcheck.py --verify-mapping fixtures/good_tight.mov
```

## If you also keep a working copy elsewhere

Some of these tools exist **twice on purpose**: once here, in the shared skill, and once in a
personal working folder that goes out on a shoot. That is a deliberate trade — the copy in your
hand on set can be edited at 2am without touching anything shared — and it carries the obvious
cost. **Two copies of an instruction is how a stale one survives a fix.**

🔴 **The stale one is usually the working copy, and that is the one used on a shoot.** A real
instance: `shootcheck.py`'s D-Log M black floor was corrected here from code 64 to 95 — the value
is the shadow-crush threshold, so at 64 the check was blind on B-camera footage — and the working
copy still had 64 afterwards. The fix landed in the shared tool and *not* in the one pointed at
actual cards.

There is a tool for this, and it is the answer rather than a `diff` loop:

```bash
./sync_from_working.py ~/path/to/workingFolder            # report what diverged
./sync_from_working.py ~/path/to/workingFolder --apply    # pull the working copy's changes in
```

It syncs **working → skill**, runs the fault-planting harnesses afterwards (a tool that silently
stops detecting a fault is worse than no tool) and then the scrub check, and it lists the
documentation that needs generalising by hand rather than pretending it can do that for you.

⚠️ It does **not** push the other way. When a fix is made *here* — as the D-Log M one was — copy it
out deliberately, and diff first so you can see you are propagating only that fix:

```bash
diff shootcheck.py ~/path/to/workingFolder/analyzer/shootcheck.py
```

To check every shared tool at once rather than the two someone happened to list:

```bash
for f in *.py; do
  w=~/path/to/workingFolder/analyzer/"$f"
  [ -f "$w" ] && { diff -q "$f" "$w" >/dev/null || echo "DIFFERS: $f"; }
done
```

One canonical home is still the right end state. Until then, the rule that keeps this safe is that
**a divergence must be loud** — which is what `sync_from_working.py` is for.

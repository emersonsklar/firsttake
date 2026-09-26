# Tools

Standalone. Useful with or without Claude.

The post tools (from `sync_cameras.py` down, except `scrub_check.py`) need an active license —
see `license_check.py`. Offload, QC, fixtures, day sheets and the on-set LUT never do, every
`--selftest` is exempt, and `nle_handover.py --verify` is free so a receiver never needs one.
Friends and collaborators with read access to this private repo need nothing: the first post
tool they run confirms that access with GitHub and records a friend license.

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
| `premiere_bridge.py` | drive Premiere Pro from Python through the UXP panel in `premiere/uxp/` — **`--probe` first** |
| `nle_handover.py` | pack a cut for someone using the other NLE, and let them verify it arrived whole |
| `scrub_check.py` | refuse to publish credentials or client-identifying material |
| `license_check.py` | activate a license, start a project, and the gate the post tools call — **never** blocks offload or QC |

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
python3 nle_ir.py --selftest
python3 premiere_bridge.py --selftest
python3 nle_handover.py --selftest
python3 scrub_check.py --selftest
python3 license_check.py --selftest
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

These files may exist twice — once here, once in a personal working folder. Two
copies of an instruction is how a stale one survives a fix. Check before trusting
either:

```bash
for f in shootcheck.py offload.py; do diff -q "$f" /path/to/working/copy/"$f"; done
```

Pick one canonical home when you get the chance.

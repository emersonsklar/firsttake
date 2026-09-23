# Tools

Standalone. Useful with or without Claude.

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
| `scrub_check.py` | refuse to publish credentials or client-identifying material |

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
```

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

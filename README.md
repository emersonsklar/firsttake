# film-shoot

A Claude Code skill for running a video shoot you are personally responsible for, and
finishing it — camera and audio settings, lighting placement, location scouting from
photos, on-set checklists, footage QC, verified card offload with chain of custody, and
then the post stage: syncing angles that share no timecode, speaker attribution,
loudness, grading two cameras to match, and assembling a project a post house can
finish.

It ships four measurement tools that are useful on their own, with or without Claude.

## What it covers

| Stage | What the skill does | Read |
|---|---|---|
| **Before** | Measure your own kit and write a profile. Read a room from photos. Lighting and camera numbers you can follow on set without re-deriving them. | `onboarding.md`, `location-scout.md` |
| **On the day** | Checklists built around one constraint: nothing may require attention after "rolling." | `templates/` |
| **Straight after** | QC the files rather than the monitor. Offload the cards so a copy is *verified*, not merely made. | `qc-interpretation.md`, `offload-protocol.md` |
| **Post** | Sync angles with no shared timecode and measure **drift**, attribute speech per word, set loudness statically per speaker, cut, and build the reviewer's transcript and review document. | `post-production.md` |
| **Finish and hand off** | Match two cameras with one CDL each, grade to a reference, and assemble the project by script so the post house only finishes it. | `grade-match.md`, `resolve-api.md` |

The through-line is the same at every stage: **measure it, because the failures are
quiet.** A sync 40 ms out looks fine on a laptop. A timeline at the wrong playback rate
shows a contented green number while dropping a fifth of the audio.

## Install

```bash
git clone <THIS_REPO> ~/.claude/skills/film-shoot
```

Claude Code picks it up from `~/.claude/skills/`. For a single project instead, clone
to `<project>/.claude/skills/film-shoot`.

Requirements: `python3`, `ffmpeg`/`ffprobe`, `numpy`. Optional: `xxhash` (faster
hashing), `ascmhl` (generation-chained manifests), `pymupdf` (PDF page verification).

```bash
pip3 install numpy xxhash ascmhl pymupdf
```

## What it is not

**It is not a gear database.** Every hardware number belongs to a specific body,
card, mic or drive, and almost none survive a kit change. A confident unmeasured
number is worse than no number, because the entire workflow depends on trusting it on
set where you cannot re-derive it.

So the first thing the skill does with a new user is help them **measure their own
kit** and write their own profile — see `references/onboarding.md`. Every claim
carries a `CONFIRMED` / `LIKELY` / `UNVERIFIED` label and a note on how it was
established.

## Tools

| | |
|---|---|
| `scripts/shootcheck.py` | Technical QC for footage and audio. Measured numbers with PASS/WARN/FAIL against explicit thresholds, and a stated fix for each. Log-aware exposure, focus stability, frame-timing/dropped-frame detection, 8-bit-in-a-10-bit-container detection, per-channel audio. Cross-camera frame-rate mismatch is the headline check. |
| `scripts/offload.py` | Verified card offload to any number of destinations. Reads the card once, hashes as it reads, then re-reads every destination from disk and compares. Writes MHL 1.1, optional generation-chained ASC MHL, a JSON manifest, and a self-contained HTML report. |
| `scripts/sync_cameras.py` | Locates recordings in a reference recording's timeline when they share no timecode. Correlates onset envelopes, so it works across different microphones. Probes repeatedly, so the spread between probes **measures drift** — the failure a single clap cannot see. |
| `scripts/diarize_by_channel.py` | Attributes speech to speakers by comparing per-speaker channels, per word rather than per transcript segment. Verifies the channels are genuinely independent first, and reports each microphone's **isolation**, which decides what audio cleanup is possible at all. |
| `scripts/build_day_sheets.py` | Renders a day-sheet HTML to PDF and asserts the result — page count, required phrases present, superseded phrases absent. |
| `scripts/scrub_check.py` | Refuses to publish credentials, client names, or internal hostnames. Run before every push. |

Every measurement tool ships a fault-planting harness, because a tool that silently
stops detecting a fault is worse than no tool. They plant ground truth rather than
checking self-consistency — 63 + 52 + 7 + 6 assertions:

```bash
python3 scripts/offload_selftest.py        # plants corruption, deletions, chain breaks
python3 scripts/make_fixtures.py           # builds clips with exact planted values
python3 scripts/verify_fixtures.py         # asserts each planted fault is caught
python3 scripts/sync_cameras.py --selftest # plants known offsets and a 1000 ppm drift
python3 scripts/diarize_by_channel.py --selftest  # plants speakers and isolation
```

## Publishing safely

This repository must contain no credentials and no client-identifying material.
`scrub_check.py` enforces it:

```bash
python3 scripts/scrub_check.py            # whole repo
python3 scripts/scrub_check.py --staged   # only what you are about to commit
```

Client and project names cannot be listed in the repo — a file enumerating them
would leak exactly what it protects. Put them in a gitignored `.scrub-denylist`, or
point `$FILM_SHOOT_DENYLIST` at a file outside the repo. Without one, the scan runs
but says plainly that it could not check private terms.

Suggested pre-push hook:

```bash
printf '#!/bin/sh\npython3 scripts/scrub_check.py || exit 1\n' > .git/hooks/pre-push
chmod +x .git/hooks/pre-push
```

## Where the hard-won parts are

Most of the value is in the traps, not the happy paths:

- `references/method.md` — measuring instead of asserting, confidence labels, the
  write-back protocol, and why a clever classifier on a weak signal was built and
  then deliberately abandoned
- `references/offload-protocol.md` — why `F_NOCACHE` is best-effort not a guarantee,
  how to force a genuine device read without `sudo`, and why deleting an offload's
  own manifest breaks the provenance chain one hop later
- `references/qc-interpretation.md` — separating the flags your test setup explains
  from the flags that are real
- `references/location-scout.md` — reading a room from a photo, the seat-height trap
  that silently invalidates lighting numbers, why an on-axis soft key is the worst
  case for anyone wearing glasses, and how coverage decides what the edit can do
- `references/post-production.md` — after the cards are safe: syncing angles with no
  timecode, speaker attribution as a measurement, loudness per speaker, and the
  rough-cut traps (honouring an editor's own trims, orphaned reactions, why speech
  recogniser timings must never be cut on, and why a subpixel filter is the difference
  between a push-in and apparent camera shake)
- `references/resolve-api.md` — assembling a project for a post house by script: the
  playback frame rate that defaults to 24 and makes a 29.97 timeline play at 0.8× with
  audio that sounds like crackle, the calls that return success and do nothing, and the
  one that pops a modal dialog and blocks the entire API
- `references/grade-match.md` — identifying what log format you actually recorded when
  the container tag is wrong, one static CDL per camera instead of a baked LUT, why a
  colour measurement that looks perfect on the first attempt is a bug report, and how to
  reproduce a graphics template from a delivered file (width solves the size, ink height
  picks the typeface)

## Licence

**Apache License 2.0** — see `LICENSE` and `NOTICE`.

Permissive: use it, modify it, ship it in something commercial, no obligation to
open your changes. Chosen over MIT for the explicit patent grant, which makes it
easier for anyone whose employer reviews dependencies. Developed independently on
personal time.

# film-shoot

A Claude Code skill for running a video shoot you are personally responsible for —
camera and audio settings, lighting placement, location scouting from photos, on-set
checklists, footage QC, and verified card offload with chain of custody.

It ships two measurement tools that are useful on their own, with or without Claude.

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
| `scripts/build_day_sheets.py` | Renders a day-sheet HTML to PDF and asserts the result — page count, required phrases present, superseded phrases absent. |
| `scripts/scrub_check.py` | Refuses to publish credentials, client names, or internal hostnames. Run before every push. |

Both measurement tools ship fault-planting harnesses, because a tool that silently
stops detecting a fault is worse than no tool:

```bash
python3 scripts/offload_selftest.py     # plants corruption, deletions, chain breaks
python3 scripts/make_fixtures.py        # builds clips with exact planted values
python3 scripts/verify_fixtures.py      # asserts each planted fault is caught
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
  that silently invalidates lighting numbers, and why an on-axis soft key is the worst
  case for anyone wearing glasses

## Licence

**Apache License 2.0** — see `LICENSE` and `NOTICE`.

Permissive: use it, modify it, ship it in something commercial, no obligation to
open your changes. Chosen over MIT for the explicit patent grant, which makes it
easier for anyone whose employer reviews dependencies. Developed independently on
personal time.

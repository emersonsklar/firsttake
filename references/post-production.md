# After the cards are safe

The rest of this skill ends at a verified offload. That is the point where the shoot
stops being at risk, not the point where it becomes useful. This covers the stretch
after that: getting two angles onto one timeline, turning speech into text someone can
search and read, and cutting something a client can watch.

Everything here is measured rather than eyeballed, for the same reason the rest of the
skill is: the failures are quiet. A sync that is 40 ms out looks fine on a laptop and
wrong on a big screen. A transcript that attributes one sentence to the wrong person
reads as fine until the person quoted notices.

---

## Sync two cameras that share no timecode

Consumer bodies rarely share a clock. The usual answer is a clap at the top of every
take, and you should still do that — it is free and it survives everything. But a clap
gives you **one** point in time, and the expensive failure is not offset, it is
**drift**: 29.97 and 30.000 are both called "30p" and pull apart about one frame every
33 seconds, so over a 40-minute interview the angles end up roughly 70 frames apart and
no single sync point fixes it.

```bash
python3 scripts/sync_cameras.py NINJA_ref.mov DJI_0001.mp4 DJI_0002.mp4
```

This correlates **onset envelopes** — the rectified first difference of short-term
loudness — rather than waveforms. That matters: it works between completely different
microphones, because two mics in one room disagree about spectrum but agree about *when
things start*. A lavalier mix against a camera's onboard mics locks fine.

It probes at up to ten places and reports the spread between them, so **the spread is
the drift measurement**. Read it like this:

| reading | means |
|---|---|
| spread under ~50 ms across a long span | one constant offset; a single sync point holds |
| spread growing with probe position | genuine drift; check both cameras' frame rates |
| drift near ±1000 ppm | almost certainly 29.97 against 30.000 |
| no lock | the two recordings share no audible content, or one is silent |

Measured example from a real two-camera interview: a 31-minute A-camera and two
consecutive B-camera files locked at +15.950 s and +1677.730 s, 9/9 and 2/2 probes,
spread **0.000 s**, drift **0 ppm**. Zero drift is worth confirming rather than
assuming — it is the thing that would have quietly ruined the edit.

Two details that saved time:

- **A camera that splits files mid-take does not necessarily lose time at the
  boundary.** On that shoot the second file began exactly where the first ended, at
  10 ms resolution. Elsewhere in this project a ~1.4 s loss per split was measured on
  the same camera family. So measure the specific files you have; do not carry another
  shoot's figure across, and be suspicious if the files are camera-generated *review*
  or *proxy* renders rather than the card originals.
- **Check a derived file is frame-aligned with the original before reusing
  timecodes.** A LUT-applied proxy is usually a 1:1 re-encode, but "usually" is not a
  measurement. One `sync_cameras.py` run answers it — 0.000 s means every timestamp
  transfers unchanged.

---

## Attribute speech to speakers by measurement

If the receiver was in **split mode**, each person is on their own channel and who
spoke when is a measurement, not a guess about turn-taking.

```bash
python3 scripts/diarize_by_channel.py interview.mov --words words.json \
    --channels 3,4 --names Host,Guest --srt out.srt --turns turns.json
```

`--words` takes a Whisper-style JSON with word timestamps, which `whisper`,
`faster-whisper` and `mlx-whisper` all produce. `--transcribe` will run mlx-whisper
directly if it is installed.

### Check the channels are independent first

Two channels carrying one summed mix look exactly like a split until you correlate
them. Pearson **r near 0 is a real split; near 1 is one mix copied twice**, and in that
case per-channel attribution is noise dressed up as data. The tool refuses rather than
guessing. A real split measured **|r| = 0.033**.

### Attribute per word, not per segment

Speech-to-text segments straddle speaker changes. Label a whole segment and you get the
tail of one person's question glued to the front of the other's answer — on a real
interview, an answer that opened with *"particular wearables?"*, which was the last two
words of the question before it. Per-word labels, with smoothing only where the margin
is thin, remove the whole class of error.

**Smooth only the uncertain words.** A blanket rolling median also rewrites words that
were measured confidently, and when a turn is shorter than the filter it drags the
entire turn to the wrong speaker. This is not hypothetical: the first version did
exactly that and scored 79% on a fixture with short turns. Keeping any word with more
than ~3 dB of margin as measured, and smoothing only the rest, took it to 100%.

### Read the isolation figure, because it decides what post can do

The tool reports, per channel, how much louder each person is on their own lavalier
than on the other one:

| isolation | what is possible |
|---|---|
| **above ~12 dB** | a plain noise gate separates the two cleanly |
| **below ~12 dB** | a gate chatters; you must duck by label instead |

Real measurement from one interview: the guest had **15.3 dB** and the host **5.5 dB**.
The host's channel could not be gated at all — it heard the guest almost as loudly as
it heard the host. That is a **microphone placement problem, and no amount of
processing fixes it.** Measure it on a test recording before the shoot, not after.

---

## Loudness: normalise per speaker, then master the mix

Target **−16 LUFS integrated, −1.5 dBTP** for online spoken word. Alternatives if
asked: −14 for a punchy podcast, −19 to −23 for broadcast.

Per speaker, then mix, then master:

```
highpass=f=80, afftdn=nr=12:nf=-25:tn=1, deesser=i=0.1
→ per-turn static trim → one static gain per speaker
→ mix → alimiter=limit=-1.5dB:level=disabled:latency=true
```

⚠️ **There is no `loudnorm` and no `dynaudnorm` in that chain, and that is the point.**
An earlier version of this file recommended both. On quiet lavalier material they
produce audible damage, and the operator hears it as clipping at peaks that never
approach full scale. Both findings are measured, on a real interview:

- **`loudnorm` silently falls back to *dynamic* mode** when it cannot reach the target
  linearly inside the true-peak ceiling — which is exactly the case on quiet source
  (RMS ≈ −40 dBFS, crest 21.6 dB). It then rides the programme's gain: **18.7 dB of
  movement inside a single six-second window.** Long-term crest collapsed 20.5 → 12.7 dB
  while per-window crest was unchanged, which is how you tell gain riding from limiting.
- **`dynaudnorm` does the same thing one stage earlier** — 18 dB over a minute and 4–6 dB
  *inside one spoken phrase*. It is not the gentle fixed boost it looks like.

So set the level **statically** and let a true-peak limiter catch only overs. Reach the
target by iterating the static gain against a *measured render*, not by asking a filter
to hit it for you.

- **Per speaker only if you actually have per-speaker channels.** Split mode gives you
  that. If the recording is one summed mix, you must split by **time** instead, using
  the turn boundaries — and then measure each speaker's segments separately. Do not
  trust `aselect` inside a single `loudnorm` pass; it silently measures the whole
  stream and returns identical numbers for every speaker.
- ⚠️ **`alimiter`'s `level` option defaults to `true`.** Written as the bare
  `alimiter=limit=-1.0dB` this file used to show, the final limiter does not protect the
  master — it *auto-levels it back up* until the peak reaches the limit, throwing the
  loudness target away. Measured: −15.4 LUFS / −0.4 dBTP instead of −16 / −1.5. Always
  `level=disabled`, and `latency=true` so the look-ahead does not time-shift the master
  against the picture.
- **A move-gain-per-turn trim is safe; a move-gain-per-phrase one is not.** Level each
  speaking turn with a constant, and change it only inside measured silence between
  turns. Nothing then moves while anyone is speaking, which is the property the dynamic
  filters above cannot give you.
- **Check the splices, not just the average.** Measure integrated loudness a few
  seconds either side of every cut. Under ~1 dB is ideal; 2–3 dB is usually genuine
  speaker-to-speaker difference rather than an error, but you want to have looked.
- **Do not chase a crest figure below ~14.5 dB at these targets.** At −16 LUFS and
  −1.5 dBTP that is the arithmetic ceiling: once a window touches the ceiling its crest
  is `ceiling − RMS`, and RMS is pinned by the loudness target. The only lever is the
  loudness target itself.

Two things worth catching here because they are recording faults, not mix faults:

- **True peak above 0 dBTP on a camera track.** One real channel measured **+0.09
  dBTP** over the whole file — already over full scale before any processing. Check
  *where* the over is before blaming the recorder: on that shoot the single over sat
  outside every range that reached the edit, and inside the edit the camera path was
  flat clean gain with the body's limiter never engaging.
- **A very high loudness range** (LRA 18–22 on raw lavalier channels) means big level
  swings, and a long interview is uncomfortable at that spread. **Bring it down by
  levelling turns and mixing, not with a dynamics filter** — see the warning above. That
  route reached **LRA 4.1** on a 15-minute programme with no compression inside any
  phrase at all.

---

## Rough cuts

Cutting from an edit decision list is straightforward; two things caused real damage.

**Honour the editor's own trim points.** When a transcript has been hand-edited, the
timestamps in that document are the decisions. Deriving cuts from your own turn
boundaries instead will quietly reinstate material the editor removed — in one case
pulling 18 seconds of between-takes chatter back into the cut, because the automatic
turn ran past where the editor had ended it.

**Check for orphaned reactions after every removal.** When a question and answer are
cut, the *next* line often still refers to them. One kept line opened "Yeah, I guess
it's giving the customer the comfort and control…" — a direct response to an answer
that no longer existed. Grep the first sentence of every turn that follows a cut for
back-references ("Yeah", "that", "absolutely", "it") and check each one.

### Synthetic camera moves, and why you may need them

With one camera on each person you can cut on dialogue. With a tight single on the
*guest* and only a wide two-shot for the host, every host question plays as the same
wide frame, and the usual fix is a slow push-in on the wide.

⚠️ **This is a coverage lesson first.** If both people matter, plan a second angle.
Faking movement in post is always worse than having shot it.

If you do need the push:

- **Use a filter that samples at subpixel positions.** `zoompan` rounds its crop
  rectangle to whole pixels; at a slow rate the edge holds for several frames then
  snaps a full pixel, about five times a second, and it reads exactly as handheld
  shake. Measured on a frozen frame, where any frame-to-frame difference is pure
  geometry error: `zoompan` jitter sd **0.586**, supersampling it 4× got to **0.161**,
  and `perspective` with `interpolation=linear` reached **0.006** — 92× better than
  `zoompan` and far cheaper than supersampling.
- **Push at a constant rate, not a constant amount.** A flat 10% per shot makes a
  9-second shot race and a 50-second shot crawl. About **0.3% per second, capped near
  12%**, makes every shot feel the same.
- **Enforce a minimum shot length.** Cutting on every speaker change turns a
  one-second interjection into a flash cut. Around 2.5 s, absorbing anything shorter
  into its neighbour, reads naturally. Placing cuts ~0.15 s *before* the new voice
  starts stops the angle arriving late.

---

## The verification trap this stage is full of

Every check above tests the property being claimed. That sounds obvious and it is the
single easiest thing to get wrong — see `method.md`, "Verify the property you are
actually claiming." The push-in shipped visibly broken because it was verified with
four still frames, which confirmed the image got tighter. Tightening was never in
doubt. Smoothness was, and a still frame cannot show motion.

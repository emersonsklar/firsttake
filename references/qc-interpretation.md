# Reading shootcheck output

`scripts/shootcheck.py` prints measured numbers with PASS / WARN / FAIL against
explicit thresholds. It has no opinion on whether the footage looks good. A READY
verdict means "no technical defect detected," not "good footage."

Your job when reporting to the user is to **separate the flags their setup explains
from the flags that are real**, and to say which is which. Relaying a raw NOT READY
verdict on a picture-only test is unhelpful; so is waving away a real defect.

---

## Always run both cameras in one command

```bash
scripts/shootcheck.py CAM_A.mov CAM_B.mp4 --role prores --role wide \
    --profile slog3 --profile dlogm -v
```

The cross-camera section needs two files and catches **frame-rate mismatch between
angles**, which is the single most expensive error in post. 29.97 and 30.000 are both
called "30p" and drift about one frame every 33 seconds when mixed.

Check the role and profile guesses echoed in the header. A wrong role applies the
wrong chroma and bitrate expectations.

---

## Flags that are usually the test, not the kit

| Flag | When it is expected |
|---|---|
| `channel balance` / `peak level` FAIL, all channels silent | Picture-only test with no mic connected. The tool says so in its own fix text. |
| `exposure level` WARN, brightest region low | Shot without the real lights. Says nothing about the real setup. |
| Empty channels 1/2 with signal on 3/4 | Normal for a body embedding a stereo source into a 4-channel HDMI stream. Not a dead mic. |
| Quiet transmitter WAVs (~−19 dBFS) | Correct — those recorders apply no gain. Use `--role transmitter`. |
| `loudness` INFO reading very low | Raw takes with pauses read low by nature. Judge by peak and noise floor. |
| Room-mic peak-to-noise below a lav's | A table mic on built-in capsules is a rescue track, not a mix source. Use `--role roommic`. |

**Do not silence these in the tool.** On a real take each one is exactly the alarm
you want.

---

## Flags to take seriously

- **Frame-rate mismatch across angles.** Hard fail. Unfixable cheaply.
- **Frame timing irregular / dropped frames.** Nothing else in the file records
  this; the recorder may have flagged it on screen and nowhere else.
- **8-bit content in a 10-bit container.** The container lies. A 10-bit ProRes file
  fed an 8-bit signal is still a 10-bit file. The tell is code values clustering on
  multiples of 4.
- **Dual-mono on a two-person shoot.** Means the receiver merged two speakers into
  one track and post cannot separate them. Catch it before the interview, not after.
- **Highlight clipping.** Gone for good.
- **Rotation flag set.** Catches an accidental vertical recording.

---

## Focus stability — read both numbers

Two figures are reported: variation across all samples, and variation across the
**steadiest contiguous run**, plus how many frames were treated as transitions.

Every real take contains transitions — the colour chart leaves frame, someone sits
down, someone walks off — and each dips the sharpness. A clip that measured **36%
overall measured 4.9% over its settled run**; the whole-clip figure alone would have
sent someone hunting a non-problem at a client site.

So:

- **High overall, low settled** → consistent with the subject entering or leaving
  frame. Still watch the settled section at 100%; this distinguishes *where* the
  dips are, not what caused them.
- **High overall, high settled** → take it seriously. That is the autofocus-hunting
  signature, and it is the exact failure nobody can catch from in front of the
  camera.

Severity is deliberately not softened by the second figure. See the last section of
`method.md` for why that classifier was built and then abandoned.

Sampling: the default is 18 frames, because a settled run needs at least 5 samples
inside it and 9 across a take containing a chart hold cannot supply that. Cost is
about 0.45 s per sample.

---

## Absolute sharpness is low-confidence

`acutance` cannot distinguish soft focus from shallow depth of field, heavy
diffusion, or a plain wall filling the frame. Treat a low value as "look at it,"
never as "the lens was soft." Check the reported sharpest region is where the face
actually is — that sanity check catches more than the number does.

---

## Exposure, log-aware

Correctly exposed log looks flat and dull. The tool reports frame-median IRE and the
brightest region's median IRE as a proxy for a lit face, against per-profile anchors
taken from the manufacturers' published transfer functions.

If a user says the footage "looked right" on an ungraded monitor, that is evidence it
is **over**exposed, not evidence it is fine.

Anything within a stop of the target usually grades fine; nominal exposure is not a
failure. Say so rather than implying a WARN is a reshoot.

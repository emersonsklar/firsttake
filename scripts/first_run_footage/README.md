# Sample footage for `first_run.py`

A 16-second, two-camera interview. **It is synthetic**: the woman, her voice, the
interviewer's voice and the room were all generated on a laptop. No real person is shown or
heard, and nothing here came from a client shoot.

| File | Camera | Picture | Audio |
|---|---|---|---|
| `A_wide.mov` | A, wide | 1280×720, 29.97 fps, H.264 8-bit, TC 14:02:10:00 | 2 ch, wireless receiver in split mode: ch 1 the interviewee's lav, ch 2 the interviewer's |
| `B_tight.mov` | B, tight | 1280×720, 29.97 fps, H.264 8-bit, TC 09:41:37:12 | 2 ch, on-camera stereo mic: both voices, room reverb, more hiss |

These originals are **clean**. `first_run.py` plants its faults on copies — a dead
interviewer channel on A, audio 18 dB too hot on B — so the ground truth below stays true.

## Ground truth

`ground_truth.json` is the answer key. Camera B was started **1.37 s after** camera A and
kept rolling past A's end, so the two only partly overlap; the timecodes are unrelated, as
they are on two cameras nobody jam-synced. `first_run.py` checks `sync_cameras.py` against
it. The clips are too short to measure drift, and the tool says so rather than guessing.

The two cameras' looks differ on purpose — B is cooler, greener and contrastier — so there
is something to match when grading.

## How it was made

All models are Apache-2.0, run locally on Apple Silicon:

1. **Dialogue** — Kokoro-82M (kokoro-onnx), voices `am_michael` (interviewer) and
   `af_heart` (interviewee), mixed on one timeline with per-speaker stems.
2. **Still** — Z-Image-Turbo (mflux, 8-bit), one 1920×1088 frame of the room. The tight
   shot is a native-resolution crop of the same frame, so it is the same person in the
   same light.
3. **Picture** — Wan2.2-S2V-14B (GGUF Q5_K_M, ComfyUI, 4-step lightx2v LoRA), each angle
   animated separately from its still, driven by the interviewee's stem so her lips follow
   her words and she listens during the question. 16 fps, interpolated to 29.97.
4. **Camera files** — ffmpeg: each camera's audio built from the stems (lav channels with
   bleed and room tone for A; reverb, hiss and a nearer interviewer for B), its own look and
   grain, its own timecode, and its own roll window.

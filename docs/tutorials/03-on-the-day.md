# 3 — On the day

One constraint dominates every trade-off when the operator is also the talent:

🔴 **Nothing may require attention after "rolling."** They cannot catch a focus hunt or a clipped
level, and cannot fix one without stopping the subject. So **weight redundancy heavily and weight
cleverness at zero.**

With crew, the calculation changes: someone can watch a meter, live monitoring is a real option, the
redundancy bar is lower, and checklists become assignments. Ask which situation you are in before
recommending anything.

---

## Rig to the plan, and read the numbers off the sheet

The day sheet carries literal numbers because the plan was written that way (tutorial 2). Rigging is
then transcription, not design. Two things to re-measure on arrival rather than assume:

- **Seat height, with a person sitting in the chair.** The cushion compresses under load. Every
  lighting number downstream depends on eye height, and the plan's down angle has a ~35° ceiling.
- **The background's real distance to the nearest obstruction**, which is what the "as far off the
  wall as the key is in front" geometry needs.

If either differs from the plan, recompute the fixture height with the formula rather than nudging by
eye:

```
lantern_height_target = eye_height + distance * tan(target_angle)
```

---

## Before "rolling" — the gate

Everything in this list is cheap, and every item is a defect that is unrecoverable or expensive
afterwards.

| Check | The failure it prevents |
|---|---|
| **Both cameras write the same frame rate** — read it off the file, not the menu | 29.97 against 30.000 drifts ~1 frame every 33 s; over 40 minutes the angles end up ~70 frames apart and no single sync point fixes it |
| **The wireless receiver is in SPLIT, not merged** — two meters, not one | Merged mode sums two speakers onto one track and **post cannot separate them.** Catch it before the interview, not after |
| **Log is actually engaged, and HDMI is still at full resolution** | One body forces HDMI to 1080p when log is enabled via its dedicated menu |
| **The limiter is where you decided it should be** | If it cannot be defeated, the camera track is sync-and-fallback only |
| **Cards: right type, right speed, and a reader for them exists in the bag** | A new body arrived once with correct cards and no reader anywhere in the kit — the camera was perfect and nothing could be got off it |
| **Dual-slot / mirrored recording on, where the body offers it** | One body mirrors to both cards with no codec restriction and continues on the survivor if one errors |
| **Rotation flag** — shoot two seconds and check | Catches an accidental vertical recording |
| **Tilt lock engaged** if the head's counterbalance range does not cover the loaded body | A slow droop mid-take, which the operator cannot see from in front of the camera |
| **A clap at the top of every take** | It is free, it survives everything, and it is the only sync point between two bodies with no shared clock |
| **A colour chart in frame on BOTH cameras at the same instant** | Eleven seconds. Without it, matching the two bodies is eyeballing with extra steps |

⚠️ **Power redundancy is usually less proven than media redundancy.** On one body, connecting DC with
the camera off is documented and **battery failover if mains drops mid-record is not documented at
all.** Record that as an open risk and test it, rather than assuming symmetry with the card story.

---

## The tasks that cannot be pre-done

Some things need the subject physically in the chair, and they are exactly the ones that get skipped
because everything else was finished the day before. Put them on the sheet as a separate block, at the
top, under a heading that says the subject must be present:

- **Register face detection / lock focus on the actual face**, where the body offers it. One camera's
  face-priority AF **locks focus rather than hunting** when it loses a face, which is the behaviour you
  want — but it has to be set up on the person.
- **Check glasses reflections on the monitor, with them seated.** A soft key near the lens axis
  reflects as a bright rectangle dead centre in each lens, and the operator cannot see it from in
  frame. Mitigate before rolling.
- **Place the lavs and measure isolation.** Run a test recording and read the per-channel figure:
  above ~12 dB a plain noise gate will separate the two speakers; below that a gate chatters and post
  must duck by label instead. On one real interview the guest measured **15.3 dB** and the host
  **5.5 dB** — the host's channel heard the guest almost as loudly as the host, which is a
  **microphone placement problem that no amount of processing fixes.** Measure it before the
  interview, not after.
- **Grey card at the face position**, then set exposure by zebra.
- **Confirm the frame with the person in it** — a subject who leans forward in answer one has left the
  frame you set on an empty chair.

---

## While rolling

Nothing to do, by design. The two things worth noticing without intervening:

- **A subject who leans back** in a deep chair has left a shallow focus plane. That was supposed to
  be solved by chair choice; if it was not, note the take.
- **Ask whether a retake is possible at all.** A subject who cannot be asked back changes every
  trade-off toward redundancy, and it should have been asked in prep.

---

## At wrap, in this order

1. **Run `shootcheck.py` on one clip from each camera in a single command** before anything is
   packed. The cross-camera checks need two files.
2. **Verified offload, with the tool, not with Finder.** See tutorial 4. A drag gives you no record of
   what the bytes were when they left the card.
3. ⛔ **Format nothing.** The card is one of your two copies.
4. **Drive and cards travel in separate bags.**

⚠️ **The retention rule depends on who finishes the job, and it is easy to leave stale.** Where the
footage goes to an external recipient, the trigger is *"until they confirm receipt"* — not "until the
copy finished". Where **you** are the post house and the deliverable is a published video, that
trigger never fires, and the rule must be restated: **cards stay until the deliverable is live and
the archive is verified.** Do not read the first version as satisfied when the second situation
applies.

---

## Debrief, same week

Run the write-back protocol from tutorial 1 while the shoot is fresh. Specifically:

- Any device fact verified or disproven → the durable layer, with a label and the method.
- Any trap (a setting whose obvious value is wrong, two settings that interact) → the durable layer
  **and** the checklist template, so it appears on the next sheet.
- Any physical constant measured — dB per step, GB per minute, a fixture's real output, a channel's
  room-tone floor — → the durable layer. These outlive every opinion built on them.
- Anything about **this** room, these chairs, these output percentages → the shoot folder only.

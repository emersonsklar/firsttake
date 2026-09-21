# On-site checklist — <CLIENT>, <LOCATION>, <DATE>

Fill every blank with a **measured number** before this goes on set. A blank or a
principle is a decision deferred to the worst possible moment.

`⛔ BLOCKING` = do not proceed past this.

---

# PRE-DEPARTURE

- [ ] **All lithium batteries in carry-on.** Cannot be checked.
- [ ] Media in every device, and the *right* media — check the kit profile for cards
      that are unsupported or that fall back to a slower mode.
- [ ] Wireless transmitters **emptied** if they overwrite oldest-first when full.
- [ ] Grey card / colour chart.
- [ ] AC adapters for anything without a usable internal battery.
- [ ] Gaffer tape, extension, power strip.
- [ ] Spare cable for any single-point-of-failure signal path.

---

# SETUP DAY

## Room assessment on arrival

- [ ] Outlet locations and count.
- [ ] **Can the overheads be switched off?** Motion sensors? If they stay on, note
      their colour temperature — office LED is often ~4000 K, not tungsten.
- [ ] Windows: direction, blinds, and what the light will do at *shoot* time.
- [ ] Subject-to-background distance: ______
- [ ] Background colour and reflectance — this can move background light by more
      than a stop.
- [ ] **Ask for the air handling to be switched off during takes.** Biggest single
      lever on the noise floor, free if you ask before people sit down.
- [ ] **Measure actual seated eye height in the real chairs**: ______
      Every lighting number below assumes it.

## Lighting

| | |
|---|---|
| Key height (centre) | |
| Key distance from faces | |
| Resulting down angle | |
| Hard ceiling on down angle | |
| Output % | |
| Colour temp | |
| Power | |

- [ ] Sandbag on the leg **directly under** the fixture, touching it. A centre-hook
      weight adds no restoring lever arm — use it in addition, never instead.
- [ ] Background lights: ______ from the background, ______ % output, ______ K.
      **Parallel, not crossed** — crossed lights spill on faces and double shadows.

## Exposure — grey card, not eye

- [ ] Grey card at the subject's face, angled at the camera, tilted till sheen goes.
- [ ] Zebra set to ______ % (from this camera's published transfer function).
- [ ] Dial output until the card *just* zebras.
- [ ] Confirm on the waveform.

## Camera placement

| | Camera A | Camera B |
|---|---|---|
| Height | | |
| Distance | | |
| Angle | | |
| Lens / focal length | | |
| Aperture, shutter, ISO | | |
| Depth of field at that setting | | |

- [ ] ⛔ **Check both frames: neither camera is in the other's shot.**

## Device settings

Work the kit profile's menu paths literally, in order. Then:

- [ ] ⛔ Recorder input reads the **expected resolution and frame rate**. If not,
      the camera's output settings are wrong — stop and fix.
- [ ] ⛔ **No LUT on the record path.** Display-only LUTs are fine.
- [ ] ⛔ Audio: correct number of meters for the number of speakers. Two speakers
      showing one meter means they are being merged and cannot be separated in post.
- [ ] ⛔ Preset actually **selected**, not merely edited and stored.
- [ ] Manual white balance to the fixtures' measured colour temp, same on every body.
- [ ] Wi-Fi and Bluetooth **off** on anything near a wireless audio link.
- [ ] Lock the controls.

## Shoot the test and run the analyzer

```bash
scripts/shootcheck.py CAM_A.mov CAM_B.mp4 --role prores --role wide -v
scripts/shootcheck.py TX1.WAV TX2.WAV --role transmitter --role transmitter
```

- [ ] ⛔ Do not leave setup day until `cross-camera frame rate` PASSes and neither
      camera FAILs.

### ⛔ Measure each microphone's isolation while you still can fix it

Ten seconds of work that post cannot undo. With the receiver in **split mode**, have
each person speak alone for five seconds, then:

```bash
scripts/diarize_by_channel.py TEST.mov --words words.json --channels 3,4 \
    --names PERSON_A,PERSON_B
```

- [ ] ⛔ **Isolation at least ~12 dB on every channel.** Below that a noise gate
      chatters and the two voices cannot be separated later at all. It is a
      microphone **placement** problem — move the lav, do not plan to fix it in post.
      One real shoot went out with 5.5 dB on one channel and 15.3 dB on the other.
- [ ] ⛔ **True peak below 0 dBTP on every recorded track.** One real camera track
      measured +0.09 dBTP, i.e. already clipped before any processing.
- [ ] Channels measure as **independent** (`|r|` near 0). If `|r|` is near 1 the
      receiver is summing both mics into both channels — split mode is not actually on,
      and per-speaker audio is gone.
- [ ] If there is no shared timecode, confirm the angles agree over time, not just at
      one clap: `scripts/sync_cameras.py CAM_A.mov CAM_B.mp4` and check the **spread**
      between probes, which is the drift figure.

---

# SHOOT DAY

## Fast re-setup

- [ ] Stands to the floor marks taped yesterday.
- [ ] ⛔ **Visually confirm on each device: resolution, frame rate, ISO, shutter,
      white balance.** Devices do not always remember.
- [ ] Ask again for the air handling off.
- [ ] Tape every cable run.

## ⛔ The things that CANNOT be done in advance

These need the subject physically present, which is exactly why they get skipped.

- [ ] **Lock autofocus onto the subject** — register their face if the body supports
      it, set subject-shift sensitivity to its most locked-on value, place the focus
      area on them. **Then verify it holds**: have them talk and gesture for 30
      seconds while you watch. If focus drifts, re-register.
- [ ] **Mic on the subject**, capsule up toward the chin, cable taped.
- [ ] **Each person states their name after the clap, every take.** This is how post
      maps audio channels to people; routing cannot be assumed.

## Per-take ritual — verbatim, every time

1. Lights on, at final positions and output.
2. **Grey card at the seat**, confirm target IRE.
3. Roll the backup audio recorder.
4. Roll the primary recorder **by hand**. Confirm it reads REC and the right format.
5. Roll the second camera. Confirm its indicator.
6. Confirm every wireless transmitter is recording, and the meter count is right.
7. **Colour chart at the seat**, angled at camera A, 5 s; then rotate to camera B,
   5 s. ⛔ **Do not walk it closer to a camera** if a light is nested with that
   camera — moving it in moves it toward the light and clips the white patch.
8. Chart out of frame.
9. ⛔ **Clap once**, clearly, in view of both cameras. If there is no shared
   timecode, this is the only sync point.
10. ⛔ Each person says their name.
11. Set focus.
12. Begin.

## During the take

- Glance at the primary recorder between questions and confirm it still reads REC.
  **Look, don't touch.**

## If you have one helper who knows nothing about cameras

Give them exactly this, written down:

1. "When I say rolling, look at <SCREEN>. Tell me if it says REC in red. If it
   doesn't, say so immediately."
2. "During the interview, if that screen stops saying REC, or anything turns red,
   interrupt us. Don't wait."
3. "Don't touch any cable, tripod, or light."

Do not give them a camera to operate.

## Wrap and offload

- [ ] ⛔ **Format nothing until the recipient confirms receipt.**
- [ ] Offload with `scripts/offload.py`, `--ascmhl`. Must end **VERIFIED**.
      Never drag in Finder. See `offload-protocol.md`.
- [ ] ⛔ **Never delete an offload's sidecars.**
- [ ] Drive and cards in separate bags.
- [ ] Run the analyzer on the real footage **before leaving the building**, while a
      retake is still theoretically possible.

## Failure playbook

Fill this in before the day. For each single point of failure, the answer is either
a spare or a decision made in advance — never troubleshooting in front of a subject.

| If this happens | Do this |
|---|---|
| Primary recorder stops | |
| Key light dies | |
| A transmitter drops | Say nothing, keep going, fix at the break |
| A camera overheats | |
| Media fills | |
| Room too loud | |

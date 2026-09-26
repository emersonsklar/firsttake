# 2 — Prep: from "I have a shoot in three weeks" to a run sheet

The output of prep is a **set of literal numbers** the operator can rig to without thinking, plus a
short list of things that must be measured on site before the plan is trusted.

Budget: the kit interview is 10 minutes. The measurements are 60–90 minutes with the gear on a
table, once. Scouting from photographs is an hour. The day sheets are generated.

---

## Step 1 — Does a kit profile exist?

If not, run `references/onboarding.md` before recommending anything. If one does exist, **read it
first**: it records what is settled and must not be re-litigated, and what was already corrected
once.

Then make the shoot folder — `shoots/<YYYY-MM-DD>-<client>/` — and copy the templates from
`references/templates/`:

| Template | For |
|---|---|
| `kit-profile-TEMPLATE.md` | the durable layer (once per operator, not per shoot) |
| `onsite-checklist-TEMPLATE.md` | the thing carried into the room |
| `post-house-spec-TEMPLATE.md` | what the recipient gets, and the disclosures |

Ask only the questions that gate real work: **seated or standing, session length, crew or solo, what
the recipient wants, room facts.**

---

## Step 2 — Measurements that pay for themselves

Each of these has burned someone. Record the number, the date, and `CONFIRMED`.

### Camera and recorder

- [ ] **Does enabling log change the HDMI output?** On one popular body, enabling log via the
      dedicated menu silently forces HDMI to **1080p**. That would have recorded an entire interview
      in HD. The workaround there was a picture profile instead. Verify on the body.
- [ ] **Can it record internally *and* output clean video at full resolution?** Many cannot. If not,
      that is a redundancy decision to make deliberately and to disclose.
- [ ] **Log mode's base ISO** — walk ISO down in log until it stops. One body's S-Log3 bottom stop
      measured **200**, not the assumed 800. Two stops; it quadrupled the light requirement.
- [ ] **Real bitrate and GB per minute** of the delivery format. Record one minute, divide. One
      4K/30p all-intra format measured **300 Mbps (~48 min per 128 GB)**, not the 240 assumed.
      ProRes 422 HQ at 4K30 is **6.2 GB/min** → 1 TB ≈ 2.7 h.
- [ ] **The frame rate the file really carries.** `ffprobe` it. A menu reading "30" usually writes
      **29.97**, and mixing 29.97 with 30.000 drifts about **one frame every 33 s**.
- [ ] **Can the audio limiter be disabled?** If not, the camera track is sync-and-fallback only.
- [ ] **Overheating** — run it for the length of the real session at the real settings. One compact
      body survived **40.9 min** at 4K30 log with no thermal cut; that is a measurement, not a
      guarantee for another body.
- [ ] **Does it split files, and does it lose time at the boundary?** One camera family splits every
      16 GiB. On one shoot **~1.4 s was lost** at each split; on another shoot the same family lost
      **nothing** (the second file began exactly where the first ended, at 10 ms resolution).
      Measure the files you have.

### Audio

- [ ] **Level calibration.** Set a gain figure, record speech at working distance, measure peak and
      noise floor with `shootcheck.py`, and record the mapping. One body's record level 26 → 6 moved
      peaks 0.0 → **−12.6 dBFS** and the floor −39.9 → **−63.9 dBFS**.
- [ ] **dB per step on any coarse knob.** One recorder moves **17 dB per step** — a single click is
      the difference between usable and unusable.
- [ ] **Relative sensitivity of each mic you might mix.** A lavalier measured **>20 dB** quieter
      than the same transmitter's built-in capsule. ⚠️ And note the *correction* to that finding: the
      manufacturer's own numbers have them within ~2 dB, so the 20 dB deficit was **a fault, not a
      spec limit** — a partly-seated unlocking 3.5 mm plug, or a pad. **A measurement that
      contradicts the manufacturer's arithmetic by 20 dB should prompt "what is broken?" rather than
      "this capsule is weak."**
- [ ] **Does dual-channel mode conflict with a safety feature?** On one wireless system the safety
      channel works only in **merged** mode, making it mutually exclusive with per-speaker split.
      Two meters = split. That choice decides whether post can separate the speakers at all.
- [ ] **Card compatibility on separate recorders** — some accept SD/SDHC ≤32 GB only, whatever the
      capacity printed on the card.

### Lights, stands, drives

- [ ] **Real output at a real distance**, not the datasheet lux (often quoted with a fresnel).
- [ ] **Stand max height and real load.** One item sold as a "light stand" measured **29"** — a
      tabletop stand. Check whether a heavy modifier's centre of mass sits forward of the mount.
- [ ] **A head's payload vs its counterbalance range.** One head is rated 10 kg payload and
      **0–3 kg counterbalance**; a ~3.7–4 kg loaded body sits outside the counterbalance range, so
      **engage the tilt lock** or it droops slowly while the operator is in frame.
- [ ] **Colour temperature each fixture actually produces**, to set manual WB.
- [ ] **Uncached read and sustained write for every drive in the chain.** A rate above the drive's
      real capability means you timed RAM (see tutorial 4).
- [ ] **Is the drive SMR?** Fine for write-once archiving, bad for anything that rewrites. One
      dropped from **110 to 88 MB/s within a single transfer** as its cache filled.
- [ ] **Managed/corporate laptop:** does it block external storage until the device is approved? On a
      managed Mac this is **indistinguishable from a dying drive** — the volume enumerates, model and
      serial read correctly, and every open fails even as root. Check the approval path before
      diagnosing hardware.

### One lens fact worth its own line

⚠️ **A varifocal zoom is not parfocal.** Set focal length, then focus, then roll. Never reframe by
zooming.

---

## Step 3 — Scouting a room from photographs

`references/location-scout.md` is the full method. **Photos support a recommendation; they do not
replace measuring on site.** Give the verdict, then name the two or three numbers that must be
measured before the plan is trusted.

Read the photo in this order — start with what would ruin a take, not with aesthetics:

1. **Things post cannot cheaply remove.** Vertical objects on the background (drape stanchions,
   poles, downpipes) — a pale vertical line on a dark background lands between the subjects and reads
   as a mistake. Light leaks — on a dark background a leak becomes the brightest thing in frame and
   arrives at a different colour temperature. Signage, lit banners, screens. Wall furniture. Anything
   reflective that can see the key.
2. **The background's tonal behaviour.** Deciding question: **which one fails more gracefully if it
   is slightly wrong?** A light wall lit imperfectly is still gradeable. A dark background with a
   visible pole or a colour-cast leak is a reframe or a paint-out.
3. **Ceiling and overheads.** Dropped acoustic tile is good news for the noise floor. Recessed
   downlights directly above the subjects put a hot toplight on heads at ~4000 K — ask whether they
   switch off, and whether motion sensors defeat that.
4. **Chairs, as a technical choice.** Reject anything that **swivels or rolls** (the subject rotates
   out of the focus plane mid-answer, unrecoverable when the operator is also on camera), anything
   deep or reclining (people lean back, and at a long focal length wide open the total depth of field
   can be a handful of inches), anything **height-adjustable** (the height drifts and invalidates
   every lighting number), mismatched pairs, glossy frames.

### ⚠️ The seat-height trap

Lighting plans get written against an assumed seated eye height. **Low upholstered lounge chairs
seat 15–17" against 18–19" for an occasional chair, so eye height often lands 42–45" rather than the
~47" that gets assumed** — and a soft cushion compresses further under load.

That moves the key's down angle by several degrees, and down angle has a hard ceiling: past roughly
**35°**, seated subjects get eye-socket shadows. Recompute rather than estimate:

```
down_angle            = atan((lantern_height - eye_height) / horizontal_distance)
lantern_height_target = eye_height + distance * tan(target_angle)
```

To raise the angle, move the fixture **further out**, not just higher — tilting more is what causes
the shadows.

### Wardrobe

Two constraints are technical and non-negotiable; the rest is taste.

- **No fine repeating patterns** — tight stripes, small checks, herringbone, tweed. They alias into
  moiré, worst on a wide lens where the pattern is small in frame. This is the single most common
  avoidable wardrobe fault.
- **No highly saturated red** on a 4:2:0 camera — chroma smears at edges.

Then tonal, driven by the background: on a dark background recommend mid-tones and lighter and
forbid black/charcoal (a dark suit on a dark background has no separation and no fix); on a light or
cream background recommend mid-to-deep saturated solids and avoid white, cream and pale grey. Always:
two subjects should not wear the same colour, do not match the upholstery, off-white rather than
bright white shirts, no large polished watch faces near a big soft source.

⚠️ **Ask in advance whether anyone wears glasses.** A soft key near the lens axis — nested directly
above the wide camera, say — is the worst possible geometry: it reflects as a bright rectangle dead
centre in each lens, and **an operator who is also on camera cannot see it happening.** Mitigate
before rolling: move the key slightly off-axis, raise it within the down-angle ceiling, or tilt the
temples down a fraction. Check it on the monitor with them seated.

### Coverage is decided here, not in post

With a tight angle on each person the edit cuts on dialogue. With a tight angle on the guest and only
a wide two-shot covering the host, **every host question plays as the same static wide frame**, and
the usual remedy is a synthetic push-in on the wide, which is always worse than having shot a second
angle.

- If both people will be quoted, **plan a second angle.**
- A wide that has to double as the host's coverage **needs room to be cropped into** — frame it a
  little looser and keep the host off the extreme edge.

---

## Step 4 — Turn the plan into literal numbers

Someone shooting a few times a year cannot reconstruct a setup from theory while a subject waits.

| Usable | Not usable |
|---|---|
| "Key at 46 inches out, centre 75 inches high, 30° down, 100% output" | "Position the key to taste, respecting inverse-square" |

State the number, then the reason in one clause only if the reason changes what they would do.

Two pieces of geometry worth having in the numbers:

- **Grey card + zebras replace a light meter.** Hold an 18% grey card at the subject's face position,
  angled at the camera and tilted until any sheen disappears; set a custom zebra to the IRE value
  *that camera's curve* puts middle grey at; dial output until the card *just* zebras; confirm on a
  waveform. For one curve, 18% grey reads **41.1 IRE** nominal and **48.5 IRE** at +1 stop — look up
  your own camera's published transfer function, do not reuse another camera's number.
- **Put the subject as far off the background as the key is in front of them** and the background
  lands about **2 stops** under, for free, with no background light needed for level. Verify with a
  second zebra rather than trusting the arithmetic.

✅ **Correctly exposed log looks flat and dull ungraded.** If it looks right on an ungraded monitor it
is overexposed. And overexposing log by a stop buys real signal-to-noise; **raising ISO does not** —
it moves the number without improving the signal.

---

## Step 5 — Diagrams and day sheets, generated

Put the room's measurements into the diagram generator's `SCENE` block rather than describing the
layout in prose, and the geometry checks run for free and catch a plan that does not fit. Ask for:
room width and depth, ceiling height, background-to-obstruction distance, outlet positions, window
positions and orientation, door swing. **Measured seat height beats all of them.**

```bash
# A day sheet belongs to ONE SHOOT, so point the tool at that shoot's HTML.
cd <shoot folder> && python3 <skill>/scripts/build_day_sheets.py     # finds the sheet here
python3 scripts/build_day_sheets.py --src <shoot>/09-DAY-SHEETS.html # or name it
python3 scripts/build_day_sheets.py --src <path> --check             # verify only
```

`build_day_sheets.py` exists because the PDF previously had no generator: it was printed by hand, so
every edit to the HTML silently left the sheet actually carried into a client site stale. It asserts
the page count and asserts that specific instructions survived into the **rendered PDF**, not into
the HTML — the HTML being right is what you already knew.

Two layout lessons that generalise:

- **Constraining a figure's height letterboxes it** and wastes page width, shrinking every label for
  no reason. Size by width and let the aspect ratio set the height.
- ⚠️ **CSS multicol silently discards overflow.** It dropped the tail of a sheet with no warning
  while still reporting the right page count. An explicit grid fails honestly by paginating.

---

## Step 6 — Rehearse the parts nobody rehearses

- **Shoot a short test at the real settings** and run `shootcheck.py` on one clip from **each**
  camera in a single command (tutorial 4). The cross-camera checks need two files and catch the most
  expensive error in post.
- **Do a full verified offload rehearsal.** It is the step nobody practises and the one that runs at
  the end of a long day when everyone is tired.
- ⚠️ **Plan to have a colour chart in shot on both cameras at the same instant.** Eleven seconds of
  setup, and it is the only reason a camera match is measurable rather than eyeballed (tutorial 5).
- **Time the real chain once**, with the real card in the real reader, and write the number into the
  kit profile. Then wrap time is arithmetic instead of anxiety.

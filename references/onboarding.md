# Onboarding a new operator — build their kit profile

The output of this is **their** `KIT.md` plus a verified-specs file. Do not hand
anyone another operator's numbers. Copy
`templates/kit-profile-TEMPLATE.md` and fill it in as you go.

Budget: the interview is 10 minutes. The measurements are 60–90 minutes with the
gear on a table, and they replace a whole class of on-set surprises.

---

## 1. Interview — what they have and how they work

Ask these. Do not guess.

**Kit**
- Camera body/bodies, and lens(es) with widest aperture
- External recorder, if any, and how it connects (HDMI? SDI? micro-HDMI?)
- Cards and drives: model, capacity, and what the camera writes to
- Audio: mics, wireless system, any separate recorder
- Lights, with modifiers, and how they are powered
- Stands and mounts — including their real load rating

**How they work**
- Are they on camera themselves, or behind it?
- Crew, or solo?
- How often do they shoot? (Drives how much you explain vs prescribe.)
- Can they ask for a reshoot? (Drives every redundancy trade-off.)
- Who receives the footage, and in what format?

**Deliverable**
- Codec, resolution, frame rate the recipient actually wants — ask them, don't assume
- Log or Rec.709? LUTs baked or not?
- 29.97 vs 30.000 — these are both called "30p" and are not interchangeable

---

## 2. Measurements that pay for themselves

Each of these has burned someone. Do them on the actual gear, record the number and
the date, label `CONFIRMED`.

### Camera and recorder

- [ ] **Does enabling log change the HDMI output?** Set log, then read the
      recorder's input resolution. On at least one popular body, enabling log via
      the dedicated menu silently forces HDMI to 1080p — it would have recorded an
      entire interview in HD. Workaround there was a picture profile instead.
      **Verify on the body; do not assume.**
- [ ] **Can it record internally *and* output clean video at full resolution?**
      Many cannot. If not, that is a redundancy decision to make deliberately, and
      to disclose to the post house.
- [ ] **Log mode's base ISO** — the bottom of its ISO range, not the camera's
      native base. Set log, walk ISO down until it stops. Getting this wrong by two
      stops quadruples the light you need.
- [ ] **Actual bitrate and GB per minute** of the delivery format. Record one
      minute, divide. Then compute card/drive runtime from it.
- [ ] **Frame rate the file really carries.** `ffprobe` it. A menu reading "30"
      often writes 29.97.
- [ ] **Can the audio limiter be disabled?** If not, treat the camera track as sync
      and fallback only.
- [ ] **Overheating**: run it for the length of the real session, at the real
      settings, and time to shutdown if it happens.

### Audio

- [ ] **Level calibration**: set a gain figure, record speech at working distance,
      measure peak and noise floor with `shootcheck.py`. Record the mapping between
      the dial position and the resulting dBFS.
- [ ] **dB per step** on any coarse knob. One recorder here moves **17 dB per
      step** — a single click is the difference between usable and unusable.
- [ ] **Relative sensitivity of each mic** you might mix. A lavalier measured
      >20 dB quieter than the same transmitter's built-in mic; mixing them with no
      input trim is unfixable in post.
- [ ] **Does the wireless system's dual-channel mode conflict with any safety
      feature?** On one system the safety channel works only in merged mode, making
      it mutually exclusive with per-speaker separation.
- [ ] **Card compatibility** on separate recorders — some accept SD/SDHC only, not
      SDXC, regardless of capacity.

### Lights and stands

- [ ] **Real output at a real distance**, not the datasheet figure. Datasheet lux is
      often quoted with a fresnel or spot attachment, several times the output of
      the standard reflector.
- [ ] **Stand max height and real load.** One "light stand" here measured **29"** —
      a tabletop stand. Check whether a heavy modifier's centre of mass sits well
      forward of the mount, which multiplies the overturning moment.
- [ ] **Colour temperature each fixture actually produces**, to set manual WB.

### Drives and cards

- [ ] **Uncached read and sustained write** for every drive in the chain. See
      `offload-protocol.md` — a rate above the drive's real capability means you
      measured RAM.
- [ ] **Is the drive SMR?** SMR is fine for write-once archiving and bad for
      anything that rewrites. Sustained writes slow down as its cache fills; one
      here dropped from 110 to 88 MB/s within a single transfer.
- [ ] **Managed/corporate laptop:** does it block external storage until the device
      is approved? On a managed Mac this is indistinguishable from a dying drive —
      the drive enumerates fine, model and serial read correctly, and every open of
      it fails even as root. Check the approval path before diagnosing hardware.

---

## 3. Write it down, then prove it is written down

Fill in the profile template with every number, its date, and its confidence
label. Then, before you rely on it:

- Re-read it as if you were on set with 40 minutes and a waiting subject. Anything
  requiring re-derivation gets rewritten as a literal number.
- Check the numbers are consistent across files. If two documents disagree, the
  disagreement is the finding.

---

## 4. First test shoot

Have them shoot a short test at the real settings, then:

```bash
# one clip from EACH camera in the same command — the cross-camera checks
# need two files and catch the most expensive error in post
scripts/shootcheck.py CAM_A.mov CAM_B.mp4 --role prores --role wide -v
scripts/shootcheck.py TX1.WAV TX2.WAV --role transmitter --role transmitter
```

Then a full verified offload, per `offload-protocol.md`. **Do not skip the offload
rehearsal** — it is the step nobody practises and the one that runs at the end of a
long day when everyone is tired.

---

# Existing operator, one new piece of gear

Do **not** rebuild their profile. Run this instead. It is short because most of the
kit is unchanged — but a new body drags six dependencies behind it, and a camera can
be entirely functional while the shoot is still impossible.

## 1. Inventory what actually arrived, not what the kit normally ships with

Have them read the items off. Office loans and used buys arrive incomplete, and the
missing piece is rarely the camera.

## 2. Walk the whole pipeline, not just the camera

This is the step that catches the expensive gaps, because everything upstream of
them works perfectly.

| Link | The question | The failure it prevents |
|---|---|---|
| **Power** | Batteries — how many, what state? Charger? AC adapter? | A body with no battery is inert |
| **Media** | Cards of the right type *and speed* | Won't record the top format |
| **A reader for that media** | Does one exist in the kit? | **The camera records fine and the offload chain is blocked at step one** |
| **Audio path** | What physically connects their existing mics to this body — and which input is *better*, not merely possible? | A camera track nobody can use |
| **Mounting** | What points does it actually provide — cold shoe, ¼"-20, ⅜"-16? Verified by looking. | A mic with nowhere to go |
| **Support** | Is the head's payload rating above the *loaded* weight, with monitor, handle, grip and lens? | A slow sag mid-take, or a fall |
| **Offload tooling** | Does their script already handle this card type and folder structure? | A verified copy that silently isn't |

The reader row is not hypothetical. On this material a new cinema body arrived with
correct cards and no CFexpress reader anywhere in the kit — the camera was perfect
and nothing could be got off it.

## 3. Ask what settled decisions this invalidates — the highest-value step

Kit profiles carry a *do-not-re-litigate* list. New gear routinely removes the exact
blocker that produced one of those entries, and nobody thinks to look, because the
entry reads as closed.

Re-read the settled list against the new gear and reverse anything now unblocked.
Mark it **REVERSED**, keep the original text visible, and say which blocker was
removed — a decision that quietly changes meaning is worse than one that never
existed.

Worked example from this material: *"Canon C300 deferred — the only available
lenses are manual-focus primes with no electronic contacts, and he is in frame and
cannot pull focus."* An autofocus zoom arrived. That single purchase removed the
whole basis of the deferral, and the body went from excluded to a candidate to
**replace** the previous A-camera — a far bigger conclusion than "they own another
camera now."

## 4. Batteries deserve their own paragraph

- **Full to dead in a week is not self-discharge.** Lithium loses roughly 2–3% a
  *month*. Treat it as a failed cell, a charge that never took, or something left
  drawing on it — and check whether it was left mounted, since a body in standby
  drains continuously.
- **Read the pack's own health data** where the system reports it. Intelligent packs
  expose remaining capacity and a degradation figure; that number, not a guess,
  decides whether a pack stays in the bag.
- **Retire a suspect pack. Do not demote it to "spare."** A spare that cannot be
  trusted occupies the slot where a real spare should be, and it will be reached for
  on the one day it matters.
- **Third-party packs are fine from reputable pro brands, with one proviso:** check
  they charge on the charger the user already owns, and that they report health data.
  A pack that needs its own charger costs the saving back and adds an item to carry.
- **When redistributing older packs to a second kit, buy the replacements first and
  prove them on a full session.** Otherwise a delayed order leaves the primary kit
  with nothing known-good.

## 5. Write it back

New body → the kit profile, with the gaps listed as blockers rather than notes.
Anything reversed → the changelog, dated, with the blocker that was removed. Then
re-run the first-test-shoot step above for the new body only; its numbers are its
own and none of the old camera's transfer.

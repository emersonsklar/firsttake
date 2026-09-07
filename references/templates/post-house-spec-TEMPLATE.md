# Delivery spec — <PROJECT>

Shot <DATE>. Everything marked **(measured)** comes from a technical test on the
actual bodies, recorders, cards and microphones used — not from spec sheets.
Anything not marked measured is a stated intention.

Send this **before** the shoot. Its real purpose is to surface the recipient's
objections while they can still change how you shoot, not to document what you
already did.

---

## Video

| | Camera A | Camera B | Proxies |
|---|---|---|---|
| Device | | | |
| Codec | | | |
| Container | | | |
| Resolution | | | |
| Frame rate | **(measured)** | **(measured)** | |
| Bit depth | | | |
| Chroma | | | |
| Colour / log | | | |
| Data rate | | | |
| Rotation flag | | | |

State the frame rate as **29.97 or 30.000 explicitly** — both get called "30p" and
they drift about a frame every 33 seconds when mixed. Say whether it was verified by
measurement or assumed from a menu label.

No LUT is applied. Files are camera-original and untouched.

---

## Audio

| | Device | Format | Rate / depth | Channels | Role |
|---|---|---|---|---|---|
| Primary, speaker 1 | | | | | Deliverable |
| Primary, speaker 2 | | | | | Deliverable |
| Reference | camera track | | | | Sync + fallback |
| Third copy | | | | | Rescue only |

---

## Sync

State plainly whether there is **shared timecode between cameras**. If not:

1. A hand clap at the top of every take, in view of both cameras.
2. Immediately after, **each person states their name** — so channels can be mapped
   to people without relying on assumed routing.

---

## Colour reference

A colour chart is held at the subject's position under final lighting, angled at
each camera in turn, ~5 s each. Held at the *subject distance* so it sits under the
same light the subject does — it is therefore smaller in a wide frame, but correctly
exposed rather than clipped.

Exposure was set with an 18% grey card to ______ IRE.

---

## Things to know before you ingest

List every non-obvious behaviour you measured. Be specific about which are measured.
Common candidates worth checking on your own kit:

1. **Container colour tags that lie.** Some cameras tag log footage as Rec.709. If
   ingest trusts the tag it will apply the wrong interpretation.
2. **File splitting.** Many cameras split at a fixed size. State the interval and
   **how much time is lost at each boundary**, if any — measure it, don't assume zero.
3. **Wireless transmitter recordings are often gain-free and will sound quiet.**
   State this explicitly or a good take gets rejected as a bad one.
4. **Self-segmenting audio** that needs concatenating, and whether those files carry
   embedded timestamps at all.
5. **Which HDMI/embedded audio channels actually carry signal.** Empty channels 1–2
   with content on 3–4 is normal for some bodies and looks like a dead mic.
6. **Non-defeatable limiters** on a camera track — makes it sync/fallback, not a mix
   source.
7. **Whether a camera-card backup of the hero angle exists.** If the body cannot
   record internally while outputting clean video, say so plainly — see the question
   below.
8. **Timestamp quirks.** Say whether to trust `creation_time` or file modification
   dates; recorders that write mtime in UTC read as local and appear to be in the
   future.

---

## Questions for you

Ask the ones whose answers would change how you shoot. Real examples worth asking:

1. **Is a single recording of the hero angle acceptable**, or do you want redundancy
   at lower quality? (State the actual trade-off available on your body.)
2. Drop-frame vs non-drop timecode — any preference?
3. Do you want the proxy files?
4. Do you want the backup room-audio track?
5. Do you want a reference conversion LUT included?

---

## Backup protocol on our side

- Offloaded with a checksum-verified tool — never a Finder drag. The card is read
  once and hashed as it is read, then **every destination is re-read from disk** and
  compared against that hash.
- **Manifests you can verify against independently:** classic **MHL 1.1** (readable
  by common DIT tools), a generation-chained **ASC MHL** history recording every hop
  from card to working drive to archive, a JSON manifest of every file with its hash,
  and a single-file HTML report.
- **No source card or drive is formatted until you confirm receipt.**
- Drive and cards travel separately. A further copy is archived after delivery is
  confirmed.

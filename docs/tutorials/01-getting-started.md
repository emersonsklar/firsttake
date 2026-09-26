# 1 — The mental model

This skill is method and tools. It is **not** a gear list, and the difference is the whole point:
almost every hardware number in a shoot plan belongs to one specific body, card, mic or drive, and
almost none of them survive a kit change.

So the first thing to do with a new operator is find out what they actually have and measure it.
Never carry a number across from another kit.

---

## The one rule

**Never state a hardware fact you have not measured, cited, or been told. Label which it is.**

| Label | Means |
|---|---|
| `CONFIRMED` | Measured on this operator's own body/drive/mic, stated in that model's own documentation, or reported by the operator from the object in their hands |
| `LIKELY` | Independent third-party sources agree, nobody has measured it here |
| `UNVERIFIED` | Plausible, untested. May not be true. |

**An unmeasured number is worse than no number.** The operator trusts this workflow on set, forty
minutes before a subject arrives, and cannot re-derive anything there. A confident wrong figure gets
used; "measure this, here is how" gets measured.

### "Fact" is wider than "number"

The two errors on record here were both **topology**, not arithmetic, so a rule about numbers caught
neither:

- a cinema camera's handle unit was asserted to include a microphone holder. It does not — but the
  camera ships one that bolts elsewhere, so a correct observation got turned into an unnecessary
  purchase.
- a missing adapter cable was called a blocker on a body that has a 3.5 mm input and needs no
  adapter.

🔴 **For that class of fact, ask before you research.** The operator is holding the object. *"Look at
the handle — is there a mic holder on it?"* settles in one line what twenty minutes of searching may
not, and it **outranks a datasheet**, because a datasheet describes a configuration and they have
theirs. Asking counts as `CONFIRMED`. Go to the web for specs they cannot see: a sustained write
rate, a payload rating, a base ISO, a Wh figure.

### Datasheets are wrong often enough to check

Two caught on this material:

| Printed | Measured | Cost of believing it |
|---|---|---|
| Fixture lux at 0.5 m (quoted with the fresnel attachment) | 4.5× lower with the bundled reflector | Underlit key |
| Log-mode base ISO, assumed | Two stops lower | Quadrupled the real light requirement |

---

## Three layers, and the dangerous middle one

After any shoot or test, ask of every finding: **would this be true on a different shoot with the
same kit?**

| Answer | Where it goes | Examples |
|---|---|---|
| **Yes** → durable | the operator's kit profile / verified-specs file, with a label and how it was established | dB per knob step, GB per minute, a fixture's real output, a defeatable limiter, a stand's real max height |
| **No** → shoot-specific | that shoot's folder | room dimensions, wall colour, the client's chairs, the actual output percentages, who was in the room |
| **Only given a configuration** → conditional-durable | the durable layer, *with the condition stated* | *"walking a colour chart toward the wide camera overexposes it"* — true only because the key was nested with that camera |

⚠️ The conditional-durable case is where the damage happens. A fact recorded without its condition
gets applied when the condition does not hold, or missed when it does. A confirmed 9:16 crop measured
on a **standing** take was 700 px wrong for the **seated** interview from the same shoot, same camera,
same subject.

---

## The write-back protocol, and the failure it exists to prevent

A geometry change invalidated one assumption. That assumption was baked into instructions in **four
files** written at different times. Fixing the file someone pointed at left three others wrong —
twice in a row.

Two rules follow, and they are cheap:

1. **When a premise changes, grep for the CONCEPT, not the phrasing you just edited.** Search for
   the idea — offload, exposure, distance, retention — not the sentence. One sweep for the *concept*
   found seven sites where grepping the phrase found five, and the two extra were exactly where the
   new finding belonged.
2. **When verifying a fix, check the OLD text is gone**, not merely that the new text is present.

Corollary for anything generated: **a printed or exported artefact downstream of a source file goes
stale silently.** Give it a build script that regenerates *and asserts* — page count, required
phrases present, superseded phrases absent. A day-sheet PDF printed by hand once left the page
actually carried into a client site disagreeing with its own source.

---

## Never fix a check without adding a test

A measuring tool that silently stops detecting a fault is worse than no tool, because it is trusted.
Every measurement tool here ships a fault-planting harness. Run them after any edit:

```bash
cd scripts
python3 offload_selftest.py
python3 make_fixtures.py && python3 verify_fixtures.py
python3 sync_cameras.py --selftest
python3 diarize_by_channel.py --selftest
python3 measure_white_balance.py --selftest
python3 fit_clip_trims.py --selftest
python3 speed_ramp.py --selftest
python3 conform_graphic.py --selftest
python3 build_subtitles.py --selftest
python3 bake_cdl_into_lut.py --selftest
python3 nle_ir.py --selftest
python3 premiere_bridge.py --selftest
python3 nle_handover.py --selftest
python3 scrub_check.py --selftest
```

The three NLE harnesses need neither application installed.

Two lessons from building those fixtures, both of which cost a debug cycle:

- **Verify a synthetic fixture actually decodes before trusting a pass.** A clip built from a
  mismatched codec/pixel-format pair produces a file that does not decode, and that looks exactly
  like an analyser bug.
- **Plant faults as contiguous regions, not scattered pixels.** Lossy encoding smooths scattered
  pixels away — a planted 6% defect measured 1.5%. The fixture was wrong, not the tool. Real defects
  are regions anyway: a window, a forehead.

And when a fixture and a threshold disagree, **find out which one is miscalibrated before changing
the threshold.** One transition fixture used a 30× sharpness swing where real footage showed 1.5×;
loosening the threshold to match it would have masked real faults.

---

## Where to go next

| You have | Read |
|---|---|
| A shoot in three weeks | [2 — Prep](02-prep-to-run-sheet.md) |
| A shoot tomorrow | [3 — On the day](03-on-the-day.md) |
| Cards in a reader | [4 — QC and verified offload](04-qc-and-verified-offload.md) |
| A verified offload and a deliverable to make | [5 — Post, end to end](05-post-end-to-end.md) |
| A plan and an NLE | [6 — Assembling the timeline by script](06-timeline-by-script.md) |
| Something that says it worked and didn't | [7 — Quiet failures](07-quiet-failures.md) |
| A chat window and small changes to make | [8 — Working conversationally](08-working-conversationally.md) |
| An approved programme and a brief for shorts | [9 — Social cuts](09-social-cuts.md) |

The reference files under `references/` are the depth behind all of this. Route to them; do not read
them all.

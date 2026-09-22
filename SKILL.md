---
name: film-shoot
description: Use when planning, prepping, running, or debriefing a video shoot where the user is responsible for the recording — camera and audio settings, lighting placement, on-set checklists, footage QC, verified card offload and archiving, or a delivery spec for a post house. Also use for the post work on that footage — syncing angles that share no timecode, transcripts and speaker attribution, loudness and audio mastering, cutting, matching two cameras' colour or identifying a log format, and assembling a DaVinci Resolve project for a post house. Also use when the user says they have a shoot coming up, asks for a checklist or set diagram, wants test footage analysed, or wants to offload and verify cards. Works with any camera, recorder, mic and drive; it measures the user's own gear rather than assuming a kit.
---

# Film shoot

Help someone run a shoot they are personally responsible for, and make the result
verifiable rather than hopeful.

**This skill carries method and tools, not a gear list.** Every hardware number
belongs to a specific body, card, mic or drive, and almost none of them survive a
kit change. Your first job with a new user is to find out what they actually have
and measure it — never to reuse a number from another kit.

---

## The one rule that makes this safe to reuse

**Never state a hardware fact you have not measured, cited, or been told. Label which it is.**

| Label | Means |
|---|---|
| `CONFIRMED` | Measured on this user's actual body/drive/mic, stated in that model's own documentation, or **reported by the operator from the object in their hands** |
| `LIKELY` | Consistent across independent third-party sources, not yet measured here |
| `UNVERIFIED` | Plausible, untested. May not be true. |

A confident unmeasured number is worse than no number, because the whole point of
this workflow is that the user trusts it on set and cannot re-derive it there.
When you don't know, say "measure this, here is how" — see `references/method.md`.

**"Fact" is wider than "number."** It covers **which ports exist, what came in the
box, and what connects to what** — the facts easiest to state confidently from
memory and get wrong. Two real errors on this material: asserting that a cinema
camera's handle unit included a microphone holder (it does not), and calling a
missing adapter cable a blocker when that body has a 3.5 mm input and needs no
adapter. Both were topology, not arithmetic, so a rule about *numbers* did not
catch either.

**For that kind of fact, ask before you research.** The operator is holding the
object. *"Look at the handle — is there a mic holder on it?"* settles in one line
what twenty minutes of searching may not, and it outranks a datasheet, because the
datasheet describes a configuration and they have theirs. Asking counts as
CONFIRMED. Reach for the web when the question is a spec they cannot see — a
sustained write rate, a payload rating, a base ISO, a Wh figure.

Corollary: **manufacturer datasheets are wrong often enough to check.** One
published lux figure on record here was for a different reflector; one log-mode base
ISO was two stops off the assumed value. Measured on the body beats printed.

---

## Start here, in order

1. **Does the user have a kit profile yet?** Look for a `KIT.md` or equivalent in
   their working folder. If not, run `references/onboarding.md` — an interview plus
   a short list of measurements that produces their own profile. Do not skip this
   and do not substitute another kit's numbers.
2. **Read their kit profile before recommending anything.** It records what is
   settled and must not be re-litigated, and what was already corrected once.
3. **Per shoot, make a folder** — `shoots/<YYYY-MM-DD>-<client>/` — and keep
   everything shoot-specific inside it. Copy the templates from
   `references/templates/`.
4. **Ask only the questions that gate real work:** seated or standing, session
   length, crew or solo, what the post house wants, room facts.

---

## Route to the right reference

| Need | Read |
|---|---|
| How to measure instead of assume; the write-back protocol | `references/method.md` |
| Setting up a new user, **or an existing user adding one new piece of gear** | `references/onboarding.md` |
| **Photos of a space** — which wall, which chairs, where things go, wardrobe advice | `references/location-scout.md` |
| Copying cards safely, archiving, chain of custody | `references/offload-protocol.md` |
| Reading `shootcheck` output without over- or under-reacting | `references/qc-interpretation.md` |
| **After the offload** — syncing angles, transcripts, speaker attribution, loudness, rough cuts, reviewer documents | `references/post-production.md` |
| **Assembling a project for a post house** — the Resolve scripting API, Fusion animation, crops that survive proxy/original | `references/resolve-api.md` |
| **Two cameras that don't match**, identifying a log format, grading to a reference episode, deriving framing, reproducing a supplied graphics template | `references/grade-match.md` |
| Templates to copy per shoot | `references/templates/` |
| The tools themselves | `scripts/README.md` |

Do not read all of these. Route.

---

## Adapt to who you are helping

Ask, or infer from their profile, and change the advice accordingly:

- **Solo operator who is also on camera.** The hard constraint: *nothing may
  require attention after "rolling."* They cannot catch a focus hunt or a clipped
  level, and cannot fix one without stopping the subject. Weight redundancy
  heavily; weight cleverness at zero. Give literal numbers ("key at 46", 30° down,
  100%"), never principles to re-derive on set.
- **Operator with crew.** Someone can watch a meter, so live monitoring is a real
  option and the redundancy bar is lower. Checklists become assignments.
- **How often they shoot.** Someone doing this a few times a year has no muscle
  memory and needs exact menu paths. Someone shooting weekly wants the reasoning
  and will resent being walked through basics.
- **Stakes.** A subject who cannot be asked for a reshoot changes every trade-off
  toward redundancy. Ask whether a retake is possible.

---

## Measure, don't assert

`scripts/shootcheck.py` exists because monitors lie. Run it on real files rather
than reasoning about whether footage is probably fine. Roles matter
(`--role transmitter`, `--role roommic`) because some sources are legitimately
quiet and judging them against a camera-track target cries wolf on the one file
that gets delivered.

`scripts/offload.py` exists because a copy that "looked fine" is not a copy.

`scripts/sync_cameras.py` exists because a clap tells you the offset at one instant
and nothing about whether the two clocks agree for the rest of the take. It probes
repeatedly, so the spread between probes measures drift.

`scripts/diarize_by_channel.py` exists because when a receiver is in split mode, who
spoke when is a measurement rather than an inference — and because the same data
tells you each microphone's isolation, which decides what cleanup is possible at all.

When you compute something, compute it. Do not put a hardcoded conclusion next to
a calculation — that has caused a real error on this material before.

---

## Writing learnings back — the point of the structure

After any shoot or test, ask of each finding: **would this be true on a different
shoot with the same kit?**

- **Yes** → it belongs in the user's durable layer (kit profile / verified specs),
  with a confidence label and how it was established.
- **No** → it stays in the shoot folder. Room dimensions, wall colour, this
  client's chairs, who was in the room.
- **True only given a configuration** → record the fact *and the condition*, or
  someone will apply it when the condition does not hold.

**When a premise changes, grep for the concept across every file, not for the
phrasing you just edited.** On this material one geometry change left the same
wrong instruction in four files, and fixing the one someone pointed at missed three
others — twice. When verifying a fix, check the *old* text is gone, not merely that
the new text is present.

**Never fix a check without adding a test.** A tool that silently stops detecting a
fault is worse than no tool.

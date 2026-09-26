# 8 — Working conversationally: making small edits by chat

Most of the work on a finished programme arrives as a sentence. *"Can you cut 'during that time as as' at
around 00:00:50:07?"* *"That fade is very abrupt."* *"The partner's product sounds quite loud compared with
what she was saying."*

None of those is a specification. Each is a **pointer to a real problem**, and the job is to turn it into a
measurement, implement it, and report back in numbers — without handing the operator any verification chores.

---

## Rule 1 — Timecodes and durations are SEARCH HINTS, not values

🔴 **The operator's positions are close but approximate. Measure the real boundary near them and report the
delta.**

Worked examples, all from one programme:

| He said | Measured | What it means |
|---|---|---|
| remove the phrase at `00:00:50:07` | source frames **47958–47991** (1600.1986–1601.2997, 33 frames, 1.101 s) | the hint located the phrase; the boundaries came from measurement |
| the identity overlay runs "approx 4 seconds" | **3.92 s** | right |
| the second overlay runs "roughly 6 seconds" | **4.80 s** | a 25% error, and it was the one that mattered |
| the pull clip runs ~11:21 → ~12:36 | programme frames **20382–22638** (00:11:19:12 → 00:12:34:18) | anchored on the **words**, not the timecodes |
| head trim of 20 frames | **21** lands 3 frames before the speech onset instead of 4.3 | told him, he took 21, and the config records how to flip it back |

⚠️ **The in-point of that pull clip was 400 ms later than the recogniser said.** The recogniser put the first
word at 621.70; the previous turn ends 621.26, both tracks sit at −65 to −76 dBFS through 622.09, and his
speech begins at **622.10**. The clip opens 0.28 s before that and ends 0.30 s after the last word, 0.09 s
clear of the following bleed. **Always say what you measured and how far it was from the hint.**

---

## Rule 2 — Their ear is the instrument; measurement implements the answer

The cheapest process lesson on this material, and it beat three rounds of spectral analysis:

✅ **Put candidate audio in front of the operator early.** On one disfluency, **six A/B clips cut from the raw
source resolved in one round** what three rounds of analysis got wrong.

That case is worth the whole rule. An operator reported a filler. Four attempts, three wrong, each confidently
measured:

1. removed a 30 ms schwa after a word — wrong object, no audible change;
2. blamed a consonant for being eaten by the crossfade — wrong, and he could hear the consonant perfectly well
   and said so;
3. shortened a drawn-out nasal — a real finding, and not what he was hearing;
4. **correct:** the following word is stretched to a **350 ms unarticulated vowel**.

🔴 **"Uh" names at least three different objects, and no amount of refining the wrong edit converges.**

| kind | what it is | the edit |
|---|---|---|
| **inserted filler** | a discrete schwa between words | cut it out |
| **filled pause** | a normal word stretched flat | shorten its phones |
| **prolonged vowel** | one word held | splice *inside* the vowel, period-aligned |

What identified each — none of it visible to an aligner, an envelope, or a spectral-centroid detector: a
**filled pause** by F0 flatness against the speaker's own distribution (**8.5 Hz** of movement where she
normally uses **17.9**), a **prolonged vowel** by continuous unarticulated voicing with a steady centroid.
⚠️ **Amplitude shape cannot tell voiced from unvoiced** — a vowel tail was confidently misread as a plosive;
use an autocorrelation peak in a 70–350 Hz band plus an LF/HF split.

**So: when a cut does not fix a reported disfluency, question what KIND of object it is rather than moving the
cut.**

---

## Rule 3 — "Cut these words" is a measured edit plus camouflage

The measurement is tutorial 5 §4. The part that lives here is what the operator does *not* have to ask for:

1. **Find the real boundaries** with the aligner and an energy onset, and **transcribe the delivered audio
   from each candidate boundary** before rendering. On one cut, three instruments disagreed by up to 300 ms and
   what settled it was ASR over the result: from the chosen boundary it reads clean with nothing before it.
2. **Cover the splice.** Removing words from the middle of a sentence leaves a splice where the picture is
   continuous — same framing, same subject, one frame later in a different place. That reads as a glitch, not
   an edit. **Change angle exactly on the splice frame**, so the discontinuity arrives with a reason.
3. **Take the length from precedent, not from feeling.** On one programme every existing source discontinuity
   already coincided with an angle change (12.179 s, 27.194 s, 76.243 s, 854.153 s of interview time all landed
   on a shot boundary), and the shortest existing island was **3.50 s** — it existed for exactly this job. So
   the new cut inherited a 3.50 s cutaway to the listener while the speaker finishes a question, which is
   natural material and costs nothing dramatically.
4. **Assert it afterwards:** every seam carries an angle change, and no two adjacent shots share an angle. On
   that build all six seams passed.
5. ⚠️ **Snap boundaries onto the seam; do not arithmetic-shift them.** Removing 33 frames moves every later
   boundary by 1.101 s, and doing that by subtraction left one boundary **3 ms** off — a one-frame same-angle
   join, the exact defect the rule prevents.

Then **fix the pre-existing consequences you can see**: after a removal, check the next turn for a
back-reference to material that no longer exists.

⚠️ And when a cut sounds abrupt, **check both causes.** On one splice the four-syllable false start was being
clipped mid-syllable *and* the two sides differed by **+30.9 dB** in level. Fixing either alone left it
audible. A transition that is correct on the transcript can still be wrong on the waveform.

---

## Rule 4 — Ask when a described operation contradicts its own literal text

The operator asked for a "capitalization change" to an on-screen title and typed a string that also **dropped a
word.** Asked rather than guessed; he confirmed every word stays.

✅ **A described operation that contradicts the literal text is worth exactly one question**, especially for
on-screen copy that will be published.

And the mirror-image case: ✅ **when they say *"we'll also want to always do this"*, that is not an edit, it is
a change to what the deliverable IS.** Implement it so the pattern cannot be omitted — the builder **exits**
when the new required input is missing, rather than quietly producing the old shape. On one project that meant
two already-built cuts could no longer be rebuilt until someone wrote their missing text, which is the correct
outcome. **A standing pattern that depends on somebody remembering is not standing.**

---

## Rule 5 — Derive auditions from the stems; move one axis at a time

A partner listens and asks for the balance changed. Expect two or three rounds and build for that.

🔴 **Do not re-run the audio build with one value changed.** It writes the master and the stems **at the paths
the live NLE project references**, so a re-run clobbers the shipping mix — and overwriting media under an open
project has wedged a render badly enough to need a force-quit.

Derive instead, which is exact rather than approximate, **provided you check the premise every run**:

- **Assert the stems still sum to the master.** The build reports this once; that report is about *the build
  that wrote it*, not about the files on disk now. Re-measured each time, they summed to **1.25 of a 24-bit
  LSB** — the master's own quantisation and nothing else. Keep the stems **32-bit float** so their sum carries
  no per-file rounding.
- **Assert the round trip.** Re-apply the *shipped* setting to the reverted stem and require it to reproduce
  the file on disk — **0.0 LSB, an exact identity**. If un-doing and re-doing is not an identity, none of the
  other variants can be trusted, and that one check licenses the whole set.
- Rebuild every curve from the **same config through the same frame→sample quantiser** the mix used, and reuse
  the shipped breakpoint frames, so no new ramp is invented and nothing steps audibly.

### Translate the request into a parameter, and say which reading you took

*"Balanced audio in the earbuds — equal gain in both ears"* is **pan = 0**. It is **not** "make the section
measure 0.0 dB L−R", which would take *unequal* gain to cancel the recording's own ~1 dB imbalance — the
opposite of what was asked. Those two readings produce different files. **Name the one you implemented and
report the residual**, so nobody later mistakes the material's own character for an error. (The operator's
ruling on that job: *"having the natural difference is great."*)

### Move one axis at a time, and price the trade

Two requests arrived together — louder earbuds *and* a different speaker pan. Varying both at once returns a
verdict you cannot attribute, so the set held one while moving the other.

✅ **And name what a request costs before they choose.** "Make the quiet device louder" spends the level
contrast that makes a handoff read: the measured step fell **9.06 → 7.26 → 5.26 LU** as the quiet side came up.
Raising the *loud* side instead buys it back — and the version that shipped ended up **more** distinct than the
original while granting everything asked for. **That option only appeared because the cost was on the table as
a number.**

### The risk they name deserves its own measurement

*"I'm sensitive to it overwhelming her speaking"* is answerable. Compare the music against **her voice alone**,
over only the short blocks where the dialogue track is above its own speech threshold — 61% of the section
here. A section average is the wrong statistic: it is dragged down by the listening pauses where the music is
*supposed* to be exposed.

```
shipped  −15.13 dB under her line
chosen   −10.97 dB under her line      warn above about −8 dB
```

That turns "is this too much?" into a number they can hold against a version they already approved.

🔴 **An audition is not a mix.** The variants are trims on a finished stem, so any solve the real build runs —
compensation, normalisation — has **not** re-run. When one is chosen, put its numbers in the config, re-run the
build properly, and then **verify the shipping mix against the audition rather than assuming it matches**: here
it landed within 0.3 LU and 0.3 dB, with the residual explained by the re-solved compensation. Keep the
superseded values in the config so the previous deliverable stays regenerable.

⚠️ And be explicit about what a pan change will and will not do: a constant-power pan **barely changes the mono
fold-down (0.02 dB)**, so on a single-speaker phone it is *inaudible*. If a partner is auditioning one, say so
— level is the cue that survives mono.

---

## Rule 6 — Keep version numbers in step across every artefact

One shared version number across project, timeline, render and subtitles. Legibility over hashes.

- **A render and its sidecar must carry the same version.** When they drift, a sidecar labelled for a different
  cut is **indistinguishable from one that is actually wrong**, and someone eventually hands out the pair. On
  one interview the picture reached v27 while the SRT still said v25.
- ✅ **Do not reason about whether the timings moved — regenerate and diff.** A transcript build is cheap and
  deterministic: identical bytes → no cue moved and you have effectively relabelled; different bytes →
  regeneration was required anyway. Either way the correct file lands and **no judgement is needed.**
- Knowing the mechanism helps you *predict* the answer and must not replace the diff. A transcript generated
  from word timings plus programme structure does not read the angle plan, so changing **which angle** is on
  screen, or swapping graphics artwork, **cannot** move a cue. Confirmed on that job: the regenerated v27 came
  out byte-identical to v25.
- ✅ **Gate it at the render**, which is the moment a version becomes something a person watches: fail the
  render's own checks when no subtitle file exists for the version being rendered.
- ⚠️ **A killed build leaves its project behind**, so the next version number may already be taken. Check the
  list; do not assume.

---

## Rule 7 — Never hand the operator a verification chore

- **Review copies come out of the NLE, not out of a proxy.** An ffmpeg render is a cross-check, not a
  deliverable, and its filename should say so.
- **Never ask the operator to confirm something a measurement can settle.** If a claim needs checking, check
  it and report the number. If a claim genuinely cannot be settled by measurement — level alone cannot tell a
  director's off-mic word from a line — **say that explicitly and ask one specific question**, recording the
  answer in the config as an operator assertion with its reason.
- **One encode per deliverable.** Do not leave two review copies of one cut in a shared folder for someone
  else to disambiguate: each is a wrong answer ready to be published. If one is deliberately kept for A/B
  listening, say so in writing **and say when it should be deleted.**
- **Report what you removed** in the same message as the change.

---

## The shape of a good reply

1. What he asked, in one line.
2. What was measured, and **how far it was from his hint**.
3. What changed, with frame numbers and the version.
4. What was checked, and the numbers — including anything that came out *worse* than expected.
5. Anything still open, and whose call it is.

Example, compressed from a real one: *removed 33 frames (1.101 s) at source 47958–47991, which is 1.7 s from
the timecode you gave; the recogniser and the aligner both had the second boundary wrong in opposite directions,
so I transcribed the result from each candidate and the chosen one reads clean. The splice is same-angle, so it
now carries a 3.50 s cutaway starting exactly on the seam — the same length as the existing island that covers
the other seam. All six seams carry an angle change and no adjacent pair shares one. Render is v30 at 26655
frames, −16.1 LUFS / −4.5 dBTP, subtitles regenerated at v30 and byte-identical to v29, so no cue moved.*

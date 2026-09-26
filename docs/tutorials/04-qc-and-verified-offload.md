# 4 — Footage QC and verified offload

Two tools, in this order, and both exist because a monitor and a progress bar are not evidence.

```bash
# 1. QC, before anything is packed — one clip from EACH camera in ONE command
python3 scripts/shootcheck.py CAM_A.mov CAM_B.mp4 --role prores --role wide \
        --profile slog3 --profile dlogm -v

# 2. the audio, judged against the right target
python3 scripts/shootcheck.py TX1.WAV TX2.WAV --role transmitter --role transmitter

# 3. the offload
./scripts/offload.py /Volumes/CARD --dest /Volumes/WORKING \
    --project "PROJECT" --card A001 --operator NAME \
    --title "PROJECT — A001" --notes "what this card holds" --ascmhl
```

---

## `shootcheck.py` — measures, does not opine

A `READY` verdict means **"no technical defect detected"**, not "good footage". It has no opinion on
lighting, background or framing.

| Exit | Meaning |
|---|---|
| 0 | READY — no WARN, no FAIL |
| 1 | USABLE WITH RESERVATIONS — a WARN, no FAIL |
| 2 | NOT READY — at least one FAIL |
| 3 | tool error, nothing analysed |

### Always run both cameras in one command

The cross-camera section needs two files, and it catches **frame-rate mismatch between angles** —
the single most expensive error in post. 29.97 and 30.000 are both called "30p".

**Check the role and profile guesses echoed in the header.** A wrong role applies the wrong chroma and
bitrate expectations, and a wrong profile applies the wrong exposure anchors.

| `--role` | For |
|---|---|
| `tight` / `wide` | picture, by coverage |
| `prores` | an external recorder's intermediate |
| `transmitter` | a wireless transmitter's own internal recording — **legitimately quiet**, no gain applied |
| `roommic` | a table mic on built-in capsules — a rescue track, not a mix source |
| `audio` / `unknown` | anything else |

| `--profile` | For |
|---|---|
| `slog3`, `dlogm`, `unknown` | per-curve IRE anchors taken from the manufacturers' published transfer functions |

### Reading the output without over- or under-reacting

Your job when reporting is to **separate the flags the setup explains from the flags that are real,
and say which is which.** Relaying a raw NOT READY on a picture-only test is unhelpful; so is waving
away a real defect.

**Usually the test, not the kit:**

| Flag | When it is expected |
|---|---|
| `channel balance` / `peak level` FAIL, all channels silent | picture-only test, no mic connected — the tool says so in its own fix text |
| `exposure level` WARN, brightest region low | shot without the real lights; says nothing about the real setup |
| Empty channels 1/2 with signal on 3/4 | normal for a body embedding a stereo source into a 4-channel HDMI stream. Not a dead mic |
| Quiet transmitter WAVs (~−19 dBFS) | correct. Use `--role transmitter` |
| `loudness` INFO reading very low | raw takes with pauses read low by nature. Judge by peak and noise floor |
| Room-mic peak-to-noise below a lav's | expected. Use `--role roommic` |

⚠️ **Do not silence these in the tool.** On a real take each one is exactly the alarm you want.

**Take seriously:**

- **Frame-rate mismatch across angles.** Hard fail, unfixable cheaply.
- **Frame timing irregular / dropped frames.** Nothing else in the file records this. The recorder may
  have flagged it on screen and nowhere else.
- **8-bit content in a 10-bit container.** The container lies; the tell is code values clustering on
  multiples of 4.
- **Dual-mono on a two-person shoot.** The receiver merged two speakers and post cannot separate them.
- **Highlight clipping.** Gone for good.
- **Rotation flag set.** An accidental vertical recording.

### Focus stability — read both numbers

Two figures are reported: variation across all samples, and variation across the **steadiest
contiguous run**, plus how many frames were treated as transitions.

Every real take contains transitions — a chart leaving frame, someone sitting down, someone walking
off — and each dips the sharpness. One real clip measured **36% overall and 4.9% over its settled
run**; the whole-clip figure alone would have sent someone hunting a non-problem at a client site.

| Reading | Means |
|---|---|
| high overall, low settled | consistent with the subject entering or leaving frame. Still watch the settled section at 100% |
| high overall, **high settled** | take it seriously. That is the autofocus-hunting signature, and the exact failure nobody can catch from in front of the camera |

Severity is deliberately **not** softened by the second figure. The obvious version — classify where
the dips sit and downgrade end-of-clip ones — was built and abandoned, because it flipped verdict
depending on which frames got sampled: encoders smear ramp frames across a transition. **A coin-flip
guarding an unrecoverable failure is worse than a warning you have to read.**

Sampling defaults to **18 frames** because a settled run needs at least 5 samples inside it and 9
across a take containing a chart hold cannot supply that. Cost is ~**0.45 s per sample**.

### Two things the sharpness numbers cannot do

- ⚠️ **`acutance` cannot distinguish soft focus from shallow depth of field, heavy diffusion, or a
  plain wall filling the frame.** Treat a low value as "look at it". The more useful sanity check is
  whether the reported sharpest region is where the face actually is.
- 🔴 **A Laplacian-variance sharpness score ranks BACKGROUNDS, not subjects.** Measured on two shots
  of the same object minutes apart: the one with the head against foliage and a textured floor scored
  **23.7** at the delivery crop against **7.0** for the same head against a plain wall — "sharper by
  240%" — and the operator looked at both and correctly picked the 7.0. The score was measuring the
  foliage. **Report such scores as within-clip; never rank across clips on them alone.**

### Exposure, log-aware

The tool reports frame-median IRE and the brightest region's median IRE as a proxy for a lit face.
Correctly exposed log looks flat and dull: **if the operator says the footage "looked right" on an
ungraded monitor, that is evidence it is over-exposed.** Anything within a stop of target usually
grades fine — say so rather than implying a WARN is a reshoot.

### Cross-check the tool itself

```bash
python3 scripts/shootcheck.py --verify-mapping fixtures/good_tight.mov
```

That checks its own pixel maths against ffmpeg's independent `signalstats`.

---

## `offload.py` — a copy that "looked fine" is not a copy

### Standing rules

1. ⛔ **Never format a source card until the deliverable is safe.** If someone else finishes the job,
   that means *until they confirm receipt* — not "until the copy finished". If **you** finish the job,
   there is no confirmation to wait for, so the rule is **cards stay until the deliverable is live
   and the archive is verified.**
2. **Never drag in Finder.** A drag gives you no record of what the bytes were when they left the
   card.
3. **One verified copy is not a backup.** Card plus one copy is two copies, which is why rule 1
   matters. Two `--dest` flags fan out in one pass — the card is read once, so the second backup is
   nearly free.
4. **Drive and cards travel in separate bags.**
5. ⛔ **Never delete an offload's sidecars** — its manifest, `.mhl`, and HTML report. See below.

### What makes a verification real rather than decorative

Three properties, and the third is the one homemade copy scripts miss:

1. **The source is read once and hashed as it is read**, so the recorded hash describes the bytes that
   actually left the card — not a later re-read.
2. **Every destination is read back independently** and compared.
3. **Caching is suppressed for those reads.** Otherwise the verification read comes back from RAM and
   proves nothing about what is on the disk.

⚠️ **On macOS `F_NOCACHE` is best-effort, not a guarantee.** It stops these reads populating the cache
but does **not** invalidate pages already resident. Measured: a drive whose true uncached read was
**3.32 GB/s** returned **10.43 GB/s** on a repeat read with `F_NOCACHE` set — 3× its physical rate.

✅ **So sanity-check throughput against the drive's real capability. A rate above it means you timed
RAM.** To force a genuine device read without `sudo`:

```bash
diskutil unmount /Volumes/X && diskutil mount diskNsM   # drops that volume's cache
```

(`purge` needs a password; unmount/remount does not.)

### A hand-copy is an unverified hop

If anything is staged by hand before the tool runs — dragging audio to the laptop first, say — the
tool will faithfully verify whatever arrived, **corruption included, and report VERIFIED.** It can
only vouch for hops it performs.

Point it at the device: mount the recorder or transmitter as a drive and offload from there. If local
staging is unavoidable, make the local folder the `--dest` of its own verified run so the chain starts
at the device.

### Two stages, one chain

A typical chain is **card → fast working drive (on the day) → slow archive (at home)**. Use `--ascmhl`
on both hops so the lineage is one verifiable chain rather than two unrelated snapshots.

```bash
# stage 2, at home
./scripts/offload.py "/Volumes/WORKING/PROJECT/A001" --dest /Volumes/ARCHIVE \
    --project "PROJECT" --ascmhl
```

Every run must end **VERIFIED**. Exit 1 means read the warning; **exit 2 is a real mismatch — stop.**

### ASC MHL, and the trap inside it

⛔ **A generation tracks the offload's own sidecars, not just the media.** Measured: generation 1 of a
4-clip card listed **7 paths** — the clips plus its manifest, its `.mhl` and its HTML report. So
deleting a superseded report, which is the natural tidying instinct after a re-run, **breaks the chain
at the next hop.** The media still verifies; only the provenance is lost, and you find out one hop
later. A tidy-up of three superseded reports broke all three chains.

Two related behaviours:

- **`ascmhl create` can write the generation and still exit non-zero.** On a broken chain it exits 10
  *after* the new generation is on disk, so the archive gains a generation no run vouched for. Report
  that honestly — do not say "not written" when a file was written. That false statement about an
  archive's contents is the most dangerous kind of bug in a provenance tool.
- **A previous broken hop hides the gap**, because it leaves a clean generation behind it while the
  real gap sits further back. Scan the whole history.

There is **no `ascmhl verify`** — the commands are `create`, `diff`, `flatten`, `info`. For a periodic
archive check:

```bash
./scripts/offload.py --verify-only "/Volumes/ARCHIVE/PROJECT/A001"   # our manifest
ascmhl diff "/Volumes/ARCHIVE/PROJECT/A001"                          # the ASC MHL history
```

`--verify-only` deliberately does **not** byte-check `ascmhl_chain.xml`, because `ascmhl` rewrites
that file in place and a frozen hash would mismatch for ever. It reports it as *live*; the numbered
generation manifests are immutable and still are verified.

✅ **The general rule, worth more than the fix: never record a hash for a file that something else owns
and rewrites.** Exclude it or re-hash it after the writer is done. A false alarm in a trust tool is
nearly as expensive as a missed one — it trains you to ignore the alarm.

### Plan the time, measured

Throughput depends on the slowest link, usually the card, and it is not the number you get testing
from an internal SSD.

| | Rate |
|---|---|
| To an NVMe-over-Thunderbolt SSD, source = internal SSD | 1.47 GB/s — **not** a shoot-day figure |
| To an 8 TB SMR archive drive, 18.6 GB | 110 MB/s |
| To the same drive, 27.5 GB | **88 MB/s** — slower on the longer run |
| Read-back from that drive | ~180 MB/s |

**SMR drives get slower within a single transfer** as their cache fills, so budget the sustained
figure. Reads run roughly 2× writes, so re-verifying an archive is cheap next to making it. A
40-minute two-camera interview is ~273 GB, so the cold-archive hop is about **50 minutes** at 88 MB/s.

### Odd things that look like failures and are not

- **An empty exFAT card can report gigabytes used.** A wiped 1 TB card reported **3.1 GB** while `du`
  and `find` showed under 400 KB. That is allocation structure. Trust `find`/`du` over the volume
  counter before concluding a card did not clear.
- **Some wireless transmitters' recordings are legitimately quiet** — their internal recorders apply
  no gain. Use `--role transmitter`, and tell the recipient, or a good take gets rejected.

---

## Then, and only then, proxies

```bash
./scripts/make_proxies.py "/Volumes/WORKING/PROJECT/A001"
./scripts/make_proxies.py "/Volumes/WORKING/PROJECT/A001" --lut look.cube --only review
```

Three design choices worth understanding, because each is a rule:

- **It is not part of `offload.py`.** That tool's promise is "every byte that left the card arrived at
  every destination". A proxy is a derived file; hashing one into that manifest would blur what
  VERIFIED means. Encoding also takes minutes, and the offload should finish fast so the card can be
  released.
- **It reads the destination, not the card.** Proxies are made from bytes that have already been
  verified.
- ⛔ **Proxies land OUTSIDE the card folder.** An ASC MHL generation tracks every file in its folder,
  so anything dropped into the card folder gets absorbed into the next hop's generation and travels to
  the archive for ever. **The card folder must stay exactly what came off the card.**

Two flavours: a **review** H.264 with the LUT baked in (a reviewer looking at flat log will misjudge
exposure every time) and an **editorial** ProRes Proxy with **no** LUT (the grade is a later stage's
job, and a proxy that already carries one fights it).

---

## Run the harness after any edit to either tool

```bash
python3 scripts/offload_selftest.py      # plants corruption, deletions and chain breaks
python3 scripts/make_fixtures.py && python3 scripts/verify_fixtures.py
```

The offload self-test's older version asserted that two-hop chaining worked, and passed, **because
nothing in it ever deleted a file between hops.** That is the shape of a harness that has stopped
testing: it exercises the path you already believe in.

# Offload protocol — getting footage off a card, provably

The end of a shoot day is when people are tired and the footage is least replaceable.
This is the step to make mechanical.

Tool: `scripts/offload.py`. Run `scripts/offload_selftest.py` after any edit to it.

---

## Standing rules

1. ⛔ **Never format a source card until the recipient confirms receipt.** Not "until
   the copy finished" — until they say they have it.
2. **Never drag in Finder.** A drag gives you no record of what the bytes were when
   they left the card.
3. **One verified copy is not a backup.** Card plus one copy is two copies, which is
   why rule 1 matters so much; ideally fan out to two destinations in one pass with
   two `--dest` flags — the card is read once, so the second backup is nearly free.
4. **Drive and cards travel in separate bags.**
5. ⛔ **Never delete an offload's sidecars** — its manifest, `.mhl`, and HTML report.
   See the ASC MHL section: generations track them, so deleting one breaks the chain.

---

## Two stages

A typical chain is **card → fast working drive (on the day) → slow archive (at
home)**. Use `--ascmhl` on both hops so the lineage is one verifiable chain rather
than two unrelated snapshots.

```bash
# stage 1, on the day
./offload.py /Volumes/CARD --dest /Volumes/WORKING \
    --project "PROJECT" --card A001 --operator NAME \
    --title "PROJECT — A001" --notes "what this card holds" --ascmhl

# stage 2, at home
./offload.py "/Volumes/WORKING/PROJECT/A001" --dest /Volumes/ARCHIVE \
    --project "PROJECT" --ascmhl
```

Every run must end **VERIFIED**. Exit 1 means read the warning; exit 2 is a real
mismatch — stop.

---

## What makes a verification real rather than decorative

Three properties, and the third is the one homemade copy scripts miss:

1. **The source is read once and hashed as it is read**, so the recorded hash
   describes the bytes that actually left the card — not a later re-read.
2. **Every destination is read back independently** and compared.
3. **Caching is suppressed for those reads.** Otherwise the verification read comes
   back from RAM and proves nothing about what is on the disk.

⚠️ **On macOS, `F_NOCACHE` is best-effort, not a guarantee.** It stops these reads
populating the cache but does **not** invalidate pages already resident, and a read
can still be served from them. Measured: a drive whose true uncached read was
3.32 GB/s returned **10.43 GB/s** on a repeat read with `F_NOCACHE` set — 3× its
physical rate.

**So sanity-check throughput against the drive's real capability. A rate above it
means you timed RAM.** To force a genuine device read without `sudo`:

```bash
diskutil unmount /Volumes/X && diskutil mount diskNsM   # drops that volume's cache
```

(`purge` needs a password; unmount/remount does not.)

---

## A hand-copy is an unverified hop

If anything is staged by hand before the tool runs — dragging audio to the laptop
first, say — the tool will faithfully verify whatever arrived, corruption included,
and report VERIFIED. **It can only vouch for hops it performs.**

Point it at the device: mount the recorder or transmitter as a drive and offload
from there. If local staging is unavoidable, make the local folder the `--dest` of
its own verified run so the chain starts at the device.

---

## ASC MHL, and the trap in it

Both a classic MHL 1.1 file and (with `--ascmhl`) a generation-chained ASC MHL
history are written. Classic MHL is universally readable but is a single snapshot;
ASC MHL records lineage across hops.

⛔ **A generation tracks the offload's own sidecars, not just the media.** Measured:
generation 1 of a 4-clip card listed **7 paths** — the clips plus its manifest, its
`.mhl` and its HTML report. So deleting a superseded report, the natural tidying
instinct after a re-run, **breaks the chain at the next hop.** The media still
verifies; only the provenance is lost, and you find out one hop later.

Two related behaviours:

- **`ascmhl create` can write the generation and still exit non-zero.** On a broken
  chain it exits 10 *after* the new generation is on disk — so the archive gains a
  generation that no run vouched for. Report that honestly; do not say "not written"
  when a file was written.
- **A previous broken hop hides the gap**, because it leaves a clean generation
  behind it while the real gap sits further back. Scan the whole history.

There is **no `ascmhl verify`** — the commands are `create`, `diff`, `flatten`,
`info`. For a periodic archive check:

```bash
./offload.py --verify-only "/Volumes/ARCHIVE/PROJECT/A001"   # our manifest
ascmhl diff "/Volumes/ARCHIVE/PROJECT/A001"                  # the ASC MHL history
```

`--verify-only` deliberately does **not** byte-check `ascmhl_chain.xml`: `ascmhl`
rewrites that file in place, so a frozen hash would mismatch for ever. It reports it
as *live*. The numbered generation manifests are immutable and still are verified.

**General rule worth more than the fix: never record a hash for a file that
something else owns and rewrites.** Exclude it or re-hash it after the writer is
done. A false alarm in a trust tool is nearly as expensive as a missed one — it
trains you to ignore the alarm.

---

## Plan the time, measured

Throughput depends on the slowest link, which is usually the card, and it is not
the number you get testing from an internal SSD.

Measured on one real chain, all-in (write plus verification re-read):

| | Rate |
|---|---|
| To an NVMe-over-Thunderbolt SSD, source = internal SSD | 1.47 GB/s — *not* a shoot-day figure |
| To an 8 TB SMR archive drive, 18.6 GB | 110 MB/s |
| To the same drive, 27.5 GB | 88 MB/s — slower on the longer run |
| Read-back from that drive | ~180 MB/s |

**SMR drives get slower within a single transfer** as their cache fills, so budget
the sustained figure, not the opening one. Reads run roughly 2× writes, so
re-verifying an archive is cheap next to making it.

**Measure your own chain once and write the number into your kit profile.** Then
wrap time is arithmetic instead of anxiety.

---

## Odd things that look like failures and are not

- **An empty exFAT card can report gigabytes used.** A wiped 1 TB card reported
  3.1 GB while `du` and `find` showed under 400 KB. That is allocation structure.
  Trust `find`/`du` over the volume counter before concluding a card did not clear.
- **Recordings from some wireless transmitters are legitimately quiet** — their
  internal recorders apply no gain. Use `--role transmitter` so they are judged
  correctly, and tell the post house, or a good take gets rejected.

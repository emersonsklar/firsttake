# Tools

Standalone. Useful with or without Claude.

| | |
|---|---|
| `shootcheck.py` | technical QC for footage and audio |
| `make_fixtures.py` / `verify_fixtures.py` | build clips with exact planted faults, assert each is caught |
| `offload.py` | verified card offload with MHL / ASC MHL manifests and an HTML report |
| `offload_selftest.py` | plants corruption, deletions and chain breaks, asserts each is caught |
| `build_day_sheets.py` | render a day-sheet HTML to PDF and assert the result |
| `scrub_check.py` | refuse to publish credentials or client-identifying material |

## Run the harnesses after any edit

A measuring tool that silently stops detecting a fault is worse than no tool,
because it is trusted.

```bash
python3 offload_selftest.py
python3 make_fixtures.py && python3 verify_fixtures.py
```

`shootcheck.py` also cross-checks its own pixel maths against ffmpeg's independent
`signalstats` filter:

```bash
python3 shootcheck.py --verify-mapping fixtures/good_tight.mov
```

## If you also keep a working copy elsewhere

These files may exist twice — once here, once in a personal working folder. Two
copies of an instruction is how a stale one survives a fix. Check before trusting
either:

```bash
for f in shootcheck.py offload.py; do diff -q "$f" /path/to/working/copy/"$f"; done
```

Pick one canonical home when you get the chance.

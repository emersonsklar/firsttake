# Setting this up for someone — instructions for the assistant

Read this when someone asks you to set up, install, or check this skill, or when a tool fails in a
way that looks like setup (missing program, "module not found", a crash printing a symbol) rather
than a problem with their footage.

ℹ️ Written for Claude Code, and nothing in it is specific to Claude: the steps are shell commands
and `scripts/doctor.py`. Any coding assistant that can run a terminal can follow this file. Only
the last step — installing into `~/.claude/skills/` so the skill loads by itself — is Claude Code's
own convention, and it is optional; the tools work from wherever the folder sits.

**Assume the person has never used a terminal.** They may not know what Python or a PATH is, and
they don't need to. Do the work yourself, one step at a time, and say in one plain sentence what
each step is for. Never install anything or change a setting until they've said yes to that
specific thing — tell them what it is, where it comes from, and roughly how big it is.

## 0. Find Python

The tools need **Python 3.12 or newer**.

- **Windows:** run `py -3.12 --version`. Use `py -3.12` for every command below. If that fails,
  try `python --version`; the Microsoft Store "python" stub doesn't count.
  Not installed → ask, then: `winget install -e --id Python.Python.3.12`
- **macOS:** run `python3 --version`. If it's older than 3.12 or missing, ask, then either
  `brew install python@3.12` (if `brew` exists) or point them to the installer at
  https://www.python.org/downloads/ — for someone non-technical the installer is usually easier
  than installing Homebrew first.

After installing Python, a new window or a new Claude session may be needed before it's found.

## 1. Run the setup check

From this folder: `<python> scripts/doctor.py --json`

It reports Python, the Windows UTF-8 setting, the Python packages, ffmpeg, whether the skill is
installed where Claude Code looks, the license, and whether DaVinci Resolve or Premiere Pro is
present. Each failing item says what it means and what to do. Summarise it for the person in plain
words — what's ready, what isn't, and what you propose to do about it.

## 2. Fix what it can

Ask first, listing the fixes it offered. Then: `<python> scripts/doctor.py --fix --yes`

It installs the Python packages (per user), turns on UTF-8 mode on Windows (per user), and
installs ffmpeg through winget or Homebrew. Some of these only take effect in a new window: if a
re-run of the check still shows a fixed item as missing, ask them to start a new Claude session
and pick up from step 1.

**If the check says "the ffmpeg that runs first is incomplete":** another app (often ImageMagick)
put a partial ffmpeg ahead of the complete one. The check says exactly which list to change —
follow it. ⚠️ On Windows the whole SYSTEM Path is searched before the account's Path, so when the
partial copy is in the system list, only adding the complete one to the SYSTEM list (above it)
works; moving things in the account's list, or moving the partial one to the end of the system
list, does not. That needs an administrator, so walk them through it rather than doing it
yourself. Renaming the partial `ffmpeg.exe` also works.

## 3. Install it where Claude looks

If the check says it isn't installed for Claude: `<python> scripts/doctor.py --install-skill`

That copies this folder to `~/.claude/skills/film-shoot` (a previous copy is kept as a backup).
Tell them to start a new Claude session afterwards so it's picked up.

## 4. Activate the license

Ask for the license key from their purchase receipt email. Never guess, invent, or reuse a key.
Then: `<python> scripts/license_check.py activate <their key>`

It needs an internet connection once. If it refuses, read its message to them as written: it
always says what to do next. People with access to the private GitHub repo need no key.
Shoot prep, card offload and footage QC work without a license, so a problem here never blocks
them from protecting footage.

## 5. The first run

`<python> scripts/first_run.py --open`

About a minute. It makes practice clips, offloads them to two backups with verification, damages
one backup on purpose to show the re-check catching it, and runs footage QC. Walk them through
what they're seeing, especially the offload report it opens. The practice clips are deliberately
flawed, so QC saying NOT READY is the expected result.

## 6. What's next

Their kit profile: `references/onboarding.md`. That's the real start, and it's what makes QC
judge their footage against their own gear rather than a default.

## Never

- Edit, bypass, or work around `scripts/license_check.py`. If it's wrong, they email the address
  it prints.
- Hold up an offload, a card verification or a QC pass over setup or licensing.
- Change a system-wide setting, or install anything without a clear yes for that item.

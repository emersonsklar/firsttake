#!/usr/bin/env python3
"""
doctor — is this computer ready for FirstTake? Says what's missing, in plain English, and can
fix most of it.

    python doctor.py                    # check everything, change nothing
    python doctor.py --fix              # check, then offer to fix what it can (asks first)
    python doctor.py --fix --yes        # fix without asking -- for an assistant, AFTER they agreed
    python doctor.py --install-skill    # copy this folder to where Claude Code looks for skills
    python doctor.py --json             # machine-readable, for an assistant
    python doctor.py --selftest

Written for someone who has never opened a terminal: every problem comes with what it means and
what to do, and an assistant can run the fixes on their behalf. Run it first on a new computer, and
again whenever a tool fails in a way that looks like setup rather than footage.

Nothing here needs an assistant, or Claude specifically. The one Claude-shaped check is whether the
folder sits in `~/.claude/skills/`, which is only how Claude Code finds a skill by itself — and it
reports INFO rather than a failure on a computer that has no `~/.claude` at all.

⚠️ This file must keep running on an OLD Python -- diagnosing "your Python is too old" is part of
its job -- so it avoids syntax newer than 3.8, even though the tools themselves need 3.12.

What it never does: touch footage, change a system (as opposed to per-user) setting, or install
anything without the person's say-so. Fixes are per-user: Python packages with `pip --user`, the
UTF-8 setting in the user environment, ffmpeg through winget or Homebrew.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

# The tools print ✓, →, Δ and emoji. On Windows a piped stdout defaults to cp1252 and a print of
# any of those raises -- which would make the doctor crash while diagnosing exactly that.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                         # noqa: BLE001
        pass

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                                            # the skill folder
MIN_PYTHON = (3, 12)
SKILL_NAME = "firsttake"                                     # must match SKILL.md's name:
PACKAGES = [("numpy", "numpy"), ("soundfile", "soundfile"), ("PIL", "Pillow")]
FFMPEG_FILTERS = ("lut3d", "ebur128")
IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"
ICON = {OK: "✅", WARN: "⚠️ ", FAIL: "❌", INFO: "ℹ️ "}


class Check:
    def __init__(self, name, level, summary, meaning="", todo="", fix=None, fix_label=""):
        self.name, self.level, self.summary = name, level, summary
        self.meaning, self.todo = meaning, todo
        self.fix, self.fix_label = fix, fix_label             # fix: callable that returns (ok, msg)

    def as_dict(self):
        return {"name": self.name, "level": self.level, "summary": self.summary,
                "meaning": self.meaning, "todo": self.todo,
                "fixable": self.fix is not None, "fix": self.fix_label}


def run(cmd, timeout=600):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def python_cmd():
    """How a person (or an assistant) should start Python on this computer."""
    if IS_WIN:
        return "py -3.12" if shutil.which("py") else "python"
    return "python3"


# ---- individual checks ------------------------------------------------------------------------
def check_python():
    v = sys.version_info[:3]
    have = ".".join(map(str, v))
    if v[:2] >= MIN_PYTHON:
        return Check("Python", OK, f"Python {have}", todo=f"Start the tools with: {python_cmd()}")
    install = ("winget install -e --id Python.Python.3.12" if IS_WIN else
               "brew install python@3.12   (or the installer from python.org)")
    return Check("Python", FAIL, f"Python {have} is too old — the tools need 3.12 or newer",
                 meaning="Some tools (the footage QC among them) won't even start on this version.",
                 todo=f"Install Python 3.12: {install}. Then run this check again with the new one"
                      f"{' (py -3.12 doctor.py)' if IS_WIN else ''}.")


def check_utf8():
    if not IS_WIN:
        return None
    if sys.flags.utf8_mode or os.environ.get("PYTHONUTF8") == "1" or _user_env("PYTHONUTF8") == "1":
        return Check("Text output (Windows)", OK, "UTF-8 mode is on")

    def fix():
        code, out = run(["setx", "PYTHONUTF8", "1"])
        return code == 0, ("UTF-8 mode turned on for your user account. It applies to newly "
                           "opened windows and new sessions." if code == 0 else out.strip())
    return Check("Text output (Windows)", FAIL, "UTF-8 mode is off",
                 meaning="Several tools print symbols like ✓ and →. With Windows' default text "
                         "setting, those crash the tool when an assistant runs it.",
                 todo="Turn on Python's UTF-8 mode for your user account (a single setting).",
                 fix=fix, fix_label="set PYTHONUTF8=1 for this user (setx)")


def _user_env(name):
    if not IS_WIN:
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def check_packages():
    missing = [pip for mod, pip in PACKAGES if importlib.util.find_spec(mod) is None]
    if not missing:
        return Check("Python packages", OK, "numpy, soundfile and Pillow are installed")
    req = ROOT / "requirements.txt"

    def fix():
        target = ["-r", str(req)] if req.is_file() else missing
        code, out = run([sys.executable, "-m", "pip", "install", "--user", "--upgrade", *target])
        still = [pip for mod, pip in PACKAGES if importlib.util.find_spec(mod) is None]
        if code == 0:
            return True, "Installed. (If a check still fails, open a new window and re-run.)"
        return False, out.strip()[-600:]
    return Check("Python packages", FAIL, "Missing: " + ", ".join(missing),
                 meaning="The analysis tools are built on these; without them they stop at startup.",
                 todo=f"{python_cmd()} -m pip install --user -r requirements.txt",
                 fix=fix, fix_label="pip install --user " + " ".join(missing))


def _other_ffmpegs(first):
    """Other ffmpeg installs this computer has, beyond the one that runs when you type ffmpeg."""
    candidates = []
    if IS_WIN:
        la = os.environ.get("LOCALAPPDATA", "")
        candidates += [Path(la) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe",
                       Path("C:/ffmpeg/bin/ffmpeg.exe"),
                       Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "ffmpeg" / "bin" / "ffmpeg.exe"]
        la_pkgs = Path(la) / "Microsoft" / "WinGet" / "Packages"
        if la_pkgs.is_dir():
            candidates += list(la_pkgs.glob("Gyan.FFmpeg*/**/bin/ffmpeg.exe"))
    else:
        candidates += [Path("/opt/homebrew/bin/ffmpeg"), Path("/usr/local/bin/ffmpeg")]
    out = []
    for c in candidates:
        try:
            if c.is_file() and c.resolve() != Path(first).resolve():
                out.append(str(c))
        except OSError:
            pass
    return out


def _path_lists():
    """(system, user) PATH entries on Windows, as the registry holds them, variables expanded."""
    if not IS_WIN:
        return [], []
    import winreg

    def read(root, key):
        try:
            with winreg.OpenKey(root, key) as k:
                raw = winreg.QueryValueEx(k, "Path")[0]
        except OSError:
            return []
        return [os.path.expandvars(e).rstrip("\\/") for e in raw.split(";") if e.strip()]
    return (read(winreg.HKEY_LOCAL_MACHINE,
                 r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
            read(winreg.HKEY_CURRENT_USER, "Environment"))


def path_order_advice(partial_dir, full_dir, system, user):
    """What to change so full_dir's ffmpeg is found before partial_dir's.

    ⚠️ Windows searches the ENTIRE system list before the user list. So when the partial copy is in
    the system list, no reordering of the user list can fix it -- the full one has to be added to
    the system list, above the partial one. Advice that just says "move it ahead in your PATH"
    sends people to the wrong list (it did: that's how this function came to exist).
    """
    def norm(d):
        return os.path.normcase(os.path.normpath(str(d)))
    sysn, usern = [norm(e) for e in system], [norm(e) for e in user]
    pd, fd = norm(partial_dir), norm(full_dir)
    where_partial = "system" if pd in sysn else ("user" if pd in usern else None)
    where_full = "system" if fd in sysn else ("user" if fd in usern else None)
    sys_ui = ("Start → type \"Edit the system environment variables\" → Environment Variables → "
              "under SYSTEM variables (the lower list), edit Path")
    user_ui = ("Start → type \"Edit environment variables for your account\" → edit Path "
               "(the upper list)")
    if where_partial == "system" and where_full != "system":
        return (f"Add {full_dir} to the SYSTEM Path, above {partial_dir}. Windows reads the whole "
                f"system list before your account's list, so changing your account's list can't "
                f"fix this — even with the partial one moved to the end of the system list. "
                f"Needs an administrator. ({sys_ui}.)")
    if where_partial and where_partial == where_full:
        ui = sys_ui if where_partial == "system" else user_ui
        return f"In the {where_partial.upper()} Path, move {full_dir} above {partial_dir}. ({ui}.)"
    if where_partial == "user" and where_full is None:
        return f"Add {full_dir} to your account's Path, above {partial_dir}. ({user_ui}.)"
    return (f"Make {full_dir} come before {partial_dir} in PATH — or rename or remove the partial "
            f"ffmpeg.exe. An assistant can walk you through it.")


def check_ffmpeg():
    ff, fp = shutil.which("ffmpeg"), shutil.which("ffprobe")
    install = "winget install -e --id Gyan.FFmpeg" if IS_WIN else "brew install ffmpeg"

    def fix():
        if IS_WIN:
            if not shutil.which("winget"):
                return False, "winget isn't available. Install ffmpeg from https://www.gyan.dev/ffmpeg/builds/"
            code, out = run(["winget", "install", "-e", "--id", "Gyan.FFmpeg",
                             "--accept-package-agreements", "--accept-source-agreements", "--silent"], 1800)
        else:
            if not shutil.which("brew"):
                return False, "Homebrew isn't installed. Get it from https://brew.sh, then: brew install ffmpeg"
            code, out = run(["brew", "install", "ffmpeg"], 1800)
        return code == 0, ("ffmpeg installed. Open a new window (or a new session) so it's "
                           "found, then re-run this check." if code == 0 else out.strip()[-600:])

    if not ff:
        return Check("ffmpeg", FAIL, "ffmpeg isn't installed",
                     meaning="ffmpeg reads and writes every video and audio file the tools touch.",
                     todo=install, fix=fix, fix_label=install)
    ff_dir = Path(ff).resolve().parent
    beside = [ff_dir / "ffprobe", ff_dir / "ffprobe.exe"]
    if not fp or not any(p.exists() for p in beside) and Path(fp).resolve().parent != ff_dir:
        others = _other_ffmpegs(ff)
        if others:
            if IS_WIN:
                system, user = _path_lists()
                todo = path_order_advice(Path(ff).parent, Path(others[0]).parent, system, user)
                todo += " Or rename that partial ffmpeg.exe (it only affects the app it came with)."
            else:
                todo = (f"Put {Path(others[0]).parent} before {Path(ff).parent} in your PATH, or "
                        "remove the partial copy.")
            return Check("ffmpeg", FAIL, f"The ffmpeg that runs first ({ff}) is incomplete",
                         meaning="It has no ffprobe beside it — often a partial copy bundled with "
                                 "another app (ImageMagick does this). A complete ffmpeg is "
                                 f"installed at {others[0]}, but the partial one is found first.",
                         todo=todo)
        return Check("ffmpeg", FAIL, "ffmpeg is installed but ffprobe isn't",
                     meaning="ffprobe comes with every complete ffmpeg; the tools need both.",
                     todo=install, fix=fix, fix_label=install)
    code, out = run([ff, "-hide_banner", "-filters"], 60)
    missing = [f for f in FFMPEG_FILTERS if (" " + f + " ") not in out]
    version = run([ff, "-hide_banner", "-version"], 60)[1].splitlines()[:1]
    if missing:
        return Check("ffmpeg", FAIL, "ffmpeg is missing features: " + ", ".join(missing),
                     meaning="This ffmpeg was built without filters the colour and loudness tools use.",
                     todo=install + "  (a full build)", fix=fix, fix_label=install)
    # "ffmpeg version 9.0.2-full_build-www.gyan.dev Copyright ..." -> "ffmpeg 9.0.2-full_build-www.gyan.dev"
    parts = version[0].split() if version else []
    label = f"ffmpeg {parts[2]}" if len(parts) > 2 and parts[1] == "version" else "ffmpeg"
    return Check("ffmpeg", OK, label + " (with ffprobe)")


def skills_dir():
    return Path.home() / ".claude" / "skills"


def _tree_digest(folder):
    h = hashlib.sha256()
    for rel in ("SKILL.md", "BUILD.txt"):
        p = folder / rel
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "fixtures", ".DS_Store")


def install_skill(target_root=None):
    target_root = target_root or skills_dir()
    dest = target_root / SKILL_NAME
    if dest.resolve() == ROOT:
        return True, f"Already running from {dest}."
    target_root.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        backup = target_root / f"{SKILL_NAME}.previous-{time.strftime('%Y%m%d-%H%M%S')}"
        dest.rename(backup)
        note = f" The copy that was there is kept at {backup}."
    else:
        note = ""
    shutil.copytree(ROOT, dest, ignore=IGNORE)
    return True, f"Installed to {dest}.{note} Start a new Claude session to pick it up."


def check_skill():
    dest = skills_dir() / SKILL_NAME
    if ROOT == dest.resolve():
        return Check("Installed for Claude", OK, f"Running from {dest}")
    if (dest / "SKILL.md").is_file():
        same = _tree_digest(dest) == _tree_digest(ROOT)
        if same:
            return Check("Installed for Claude", OK, f"Installed at {dest}")
        return Check("Installed for Claude", WARN, f"A different version is installed at {dest}",
                     meaning="Claude uses the copy in that folder, not this one.",
                     todo="Install this version over it (the old one is kept as a backup).",
                     fix=install_skill, fix_label=f"copy this folder to {dest}")
    if (ROOT / ".git").exists():
        return Check("Installed for Claude", INFO, "Running from a git checkout",
                     meaning="Claude Code finds skills in " + str(skills_dir()) + ".",
                     todo=f"Either clone into {dest}, or run: {python_cmd()} doctor.py --install-skill")
    # 🔴 Not installed is only a FAILURE for someone who uses Claude Code. The tools are plain
    # Python and ffmpeg and run from wherever this folder sits — no model, no API key, no network —
    # so telling a shell-only user their setup is broken would be simply untrue.
    # The tell is whether they have a ~/.claude at all.
    if not (Path.home() / ".claude").exists():
        return Check("Installed for Claude", INFO, "Not installed as a skill, and that is fine here",
                     meaning="No ~/.claude on this computer, so Claude Code does not look like part "
                             "of this setup. Every tool runs standalone from this folder; the skill "
                             "folder only matters for an assistant to load the method by itself.",
                     todo=f"If you do want that: {python_cmd()} doctor.py --install-skill")
    return Check("Installed for Claude", FAIL, "Not installed where Claude looks for skills",
                 meaning=f"Claude Code finds skills in {skills_dir()}. Until it's there, Claude "
                         "won't know to use it. (The tools themselves work from here regardless.)",
                 todo=f"{python_cmd()} doctor.py --install-skill",
                 fix=install_skill, fix_label=f"copy this folder to {dest}")


def _first_existing(paths):
    for p in paths:
        if Path(p).exists():
            return str(p)
    return None


def check_editors():
    pf = os.environ.get("ProgramFiles", "C:/Program Files")
    pd = os.environ.get("PROGRAMDATA", "C:/ProgramData")
    if IS_WIN:
        resolve = _first_existing([Path(pf) / "Blackmagic Design" / "DaVinci Resolve" / "Resolve.exe"])
        api = _first_existing([Path(pd) / "Blackmagic Design" / "DaVinci Resolve" / "Support" /
                               "Developer" / "Scripting" / "Modules" / "DaVinciResolveScript.py"])
        premiere = next(iter(sorted((Path(pf) / "Adobe").glob("Adobe Premiere Pro*"))), None) \
            if (Path(pf) / "Adobe").is_dir() else None
    elif IS_MAC:
        resolve = _first_existing(["/Applications/DaVinci Resolve/DaVinci Resolve.app"])
        api = _first_existing(["/Library/Application Support/Blackmagic Design/DaVinci Resolve/"
                               "Developer/Scripting/Modules/DaVinciResolveScript.py"])
        premiere = next(iter(sorted(Path("/Applications").glob("Adobe Premiere Pro*"))), None)
    else:
        resolve = api = premiere = None
    found = []
    if resolve:
        found.append("DaVinci Resolve" + (" (scripting module found)" if api else ""))
    if premiere:
        found.append(Path(premiere).name)
    if found:
        # ⚠️ "Found" is not "usable": the preference that lets an outside script connect to Resolve
        # is documented under STUDIO, so the free edition may not be drivable from a shell at all.
        # That caveat is not repeated here — a green line should stay one line, and the renderer
        # only shows `meaning` for problems. It lives in references/nle-backends.md, and
        # `resolve_connect.py --check` answers it on the machine in question.
        return Check("Editing app", OK, " and ".join(found))
    return Check("Editing app", INFO, "No DaVinci Resolve or Premiere Pro found",
                 meaning="Not needed. It buys you an EDITABLE TIMELINE at the end; without one, "
                         "`assemble_ffmpeg.py` builds the same plan into a full-quality master — "
                         "measured at 1.08 codes of 255 against a delivered programme. Everything "
                         "else here never needed an editing application.")


def all_checks():
    checks = [check_python(), check_utf8(), check_packages(), check_ffmpeg(), check_skill(),
              check_editors()]
    return [c for c in checks if c is not None]


# ---- reporting --------------------------------------------------------------------------------
def verdict(checks):
    blocking = [c for c in checks if c.level == FAIL]
    if not blocking:
        return "Ready."
    return f"Not ready yet: {len(blocking)} thing{'s' if len(blocking) > 1 else ''} to fix."


def print_report(checks):
    print(f"FirstTake setup check — {platform.system()} {platform.release()}, "
          f"Python {platform.python_version()}\n")
    for c in checks:
        print(f"{ICON[c.level]} {c.name}: {c.summary}")
        if c.level in (FAIL, WARN, INFO) and c.meaning:
            print(f"     {c.meaning}")
        if c.level in (FAIL, WARN) and c.todo:
            print(f"     → {c.todo}")
    print(f"\n{verdict(checks)}")
    fixable = [c for c in checks if c.fix and c.level in (FAIL, WARN)]
    if fixable:
        print("Can fix automatically: " + "; ".join(c.fix_label for c in fixable) +
              f"\n  → run: {python_cmd()} doctor.py --fix")


def apply_fixes(checks, assume_yes):
    fixable = [c for c in checks if c.fix and c.level in (FAIL, WARN)]
    if not fixable:
        return
    print("\nFixes available:")
    for c in fixable:
        print(f"  • {c.name}: {c.fix_label}")
    if not assume_yes:
        if not sys.stdin.isatty():
            print("\nNot applied: this isn't an interactive window. Ask the person first, then "
                  "re-run with --fix --yes.")
            return
        if input("\nApply these? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Nothing changed.")
            return
    for c in fixable:
        try:
            ok, msg = c.fix()
        except Exception as e:                                # noqa: BLE001
            ok, msg = False, f"{type(e).__name__}: {e}"
        print(f"{'✅' if ok else '❌'} {c.name}: {msg}")


# ---- selftest ---------------------------------------------------------------------------------
def selftest():
    import tempfile
    failures = []

    def check(name, cond):
        print(f"  {'ok ' if cond else 'FAIL'} {name}")
        if not cond:
            failures.append(name)

    checks = all_checks()
    check("every check returns a level", all(c.level in ICON for c in checks))
    check("every failing check says what to do", all(c.todo for c in checks if c.level == FAIL))
    check("json report is serialisable", bool(json.dumps([c.as_dict() for c in checks])))
    check("verdict ready when nothing fails",
          verdict([Check("x", OK, "")]) == "Ready." and verdict([Check("x", FAIL, "")]) != "Ready.")

    with tempfile.TemporaryDirectory() as td:
        skills = Path(td) / "skills"
        ok, msg = install_skill(skills)
        dest = skills / SKILL_NAME
        check("install_skill copies the skill", ok and (dest / "SKILL.md").is_file()
              and (dest / "scripts" / "doctor.py").is_file())
        check("install_skill leaves out .git and caches",
              not (dest / ".git").exists() and not list(dest.rglob("__pycache__")))
        (dest / "marker.txt").write_text("old", encoding="utf-8")
        ok, msg = install_skill(skills)
        backups = list(skills.glob(SKILL_NAME + ".previous-*"))
        check("reinstall keeps the previous copy as a backup",
              ok and len(backups) == 1 and (backups[0] / "marker.txt").is_file())
        check("reinstall writes a clean copy", not (dest / "marker.txt").exists())

    # PATH-order advice: the system list always wins, whatever the order inside the user list
    im, fl = r"C:\Program Files\ImageMagick", r"C:\Users\x\WinGet\Links"
    # 🔴 A shell-only user must not be told their setup is broken. The tools need no assistant at
    #    all, so "not installed as a skill" is INFO when there is no ~/.claude, and FAIL when there
    #    is one (because then they clearly do use Claude Code and it will not find the skill).
    #
    # ⚠️ ROOT is patched as well as home. Without that, this repo's own .git sends check_skill()
    #    down the "running from a git checkout" branch and the INFO assertion passes for entirely
    #    the wrong reason — which is what the second assertion here caught.
    import unittest.mock as _mock
    with tempfile.TemporaryDirectory() as _d:
        fake_home, fake_root = Path(_d) / "home", Path(_d) / "elsewhere"
        fake_home.mkdir()
        fake_root.mkdir()                                   # no .git in it
        def _skill_check(home_has_claude):
            if home_has_claude:
                (fake_home / ".claude").mkdir(exist_ok=True)
            with _mock.patch.object(Path, "home", staticmethod(lambda: fake_home)), \
                 _mock.patch.object(sys.modules[__name__], "ROOT", fake_root):
                return check_skill()
        c = _skill_check(False)
        check("no ~/.claude -> not installed is INFO, not a failure", c.level == INFO)
        check("...and it says the tools still run", "standalone" in (c.meaning or ""))
        c2 = _skill_check(True)
        check("with ~/.claude present -> it IS a failure", c2.level == FAIL)
        check("...and even then it says the tools still work",
              "regardless" in (c2.meaning or ""))

    a = path_order_advice(im, fl, system=[r"C:\Windows", im], user=[fl])
    check("partial in SYSTEM (even last), full in user -> add full to SYSTEM",
          "SYSTEM Path" in a and "can't" in a)
    a = path_order_advice(im, fl, system=[im, fl], user=[])
    check("both in system -> reorder the system list", "SYSTEM Path, move" in a)
    a = path_order_advice(im, fl, system=[], user=[im, fl])
    check("both in user -> reorder the user list", "USER Path, move" in a)
    a = path_order_advice(im, fl, system=[], user=[im])
    check("partial in user, full nowhere -> add full to the user list", "account's Path" in a)

    # the doctor must stay runnable on an old Python: no syntax newer than 3.8
    import ast
    src = Path(__file__).read_text(encoding="utf-8")
    try:
        ast.parse(src, feature_version=(3, 8))
        check("parses as Python 3.8", True)
    except SyntaxError as e:
        check(f"parses as Python 3.8 ({e})", False)

    print(f"\n{'PASS' if not failures else 'FAIL'}: {len(failures)} failure(s)")
    return 1 if failures else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fix", action="store_true", help="offer to fix what can be fixed")
    ap.add_argument("--yes", action="store_true", help="with --fix: don't ask (the person already agreed)")
    ap.add_argument("--install-skill", action="store_true", help="copy this folder to Claude's skills folder")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if a.install_skill:
        ok, msg = install_skill()
        print(("✅ " if ok else "❌ ") + msg)
        return 0 if ok else 1
    checks = all_checks()
    if a.json:
        print(json.dumps({"verdict": verdict(checks), "python_command": python_cmd(),
                          "checks": [c.as_dict() for c in checks]}, indent=2))
    else:
        print_report(checks)
    if a.fix:
        apply_fixes(checks, a.yes)
    return 0 if not any(c.level == FAIL for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())

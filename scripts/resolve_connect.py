#!/usr/bin/env python3
"""resolve_connect — one place that knows how to reach DaVinci Resolve, and what to say when it
cannot.

    import resolve_connect
    r = resolve_connect.connect()            # raises ResolveUnavailable with a usable message
    if not resolve_connect.is_studio(r):
        ...                                  # a Studio-only feature is about to be attempted

    python3 resolve_connect.py --check       # report what this machine has
    python3 resolve_connect.py --selftest

## 🔴 WHY THIS EXISTS: the failure it replaces sent people looking for the wrong thing

`scriptapp("Resolve")` returning `None` was reported as *"Resolve must be running, and if its UI is
showing a modal dialog EVERY API call returns None until it is dismissed -- check for one."* Both
those causes are real. But for somebody on the **free** version there is no dialog to find and
nothing they can do to the app that will help, and the message sends them hunting anyway.

## Free vs Studio, from Blackmagic's own README

`Developer/Scripting/README.md`, shipped with the application, says two things that matter:

  ✅ "The DaVinci Resolve scripting APIs cover a common superset of functions for both the **Free
     and Studio** versions of the application." — the API is NOT Studio-only, contrary to the usual
     folk wisdom. Studio-only *functions* return False rather than being absent.

  ⚠️ "**In DaVinci Resolve Studio**, Preferences > System > General, you can configure: External
     scripting, i.e. whether external scripts can connect to Resolve (None, Local, or Network)."
     That preference is what allows `scriptapp()` to connect at all, and it is documented under
     Studio.

⚠️ **UNVERIFIED and deliberately labelled so.** Suggestive wording in a README is not a
measurement, and it could not be tested on the machine this was written on, which runs Studio. What
is certain is the shape of the answer: if `scriptapp()` returns None on a running, unblocked free
Resolve, then external scripting is the Studio-gated part. `--check` prints what this machine says
so the question can be settled on a machine that has the free version.

**Internal** scripting is NOT described as Studio-gated anywhere in that README — Workspace >
Console, Workspace > Scripts, and render scripts. So the escape hatch, if one is ever needed, is the
same architecture the Premiere backend already uses: a script running INSIDE the application, taking
its orders from a job folder on disk. Premiere has no external scripting either.

## And nobody needs a paid application to finish a video

If Resolve cannot be driven, `references/nle-backends.md` describes the route that needs no NLE at
all: assemble with ffmpeg from the same plan. The trade-off is real and is stated there — no
editable timeline afterwards, and the grade must be **baked into the media once** rather than
re-applied during assembly. That condition is not fussiness: an ffmpeg pipeline that re-implemented
the colour transform agreed with Resolve on geometry to r >= 0.9989 and was still 14-18% out in the
grade, and eight approved review renders were not showing what the NLE produced.
"""

from __future__ import annotations

import argparse
import os
import platform
import sys

# Where Blackmagic puts the scripting module, per platform. Checked in order; the environment
# variable wins, because a non-default install is the case a hardcoded path cannot know about.
_MODULE_PATHS = {
    "Darwin": ["/Library/Application Support/Blackmagic Design/DaVinci Resolve/"
               "Developer/Scripting/Modules"],
    "Windows": [os.path.expandvars(r"%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve"
                                   r"\Support\Developer\Scripting\Modules")],
    "Linux": ["/opt/resolve/Developer/Scripting/Modules",
              "/home/resolve/Developer/Scripting/Modules"],
}

STUDIO_NAME = "DaVinci Resolve Studio"

# What to do instead, said once so every message can point at it.
NO_NLE_ROUTE = ("You do not need a paid application to finish a video: see "
                "references/nle-backends.md, 'Finishing with no NLE at all'. It assembles the same "
                "plan with ffmpeg and delivers a correct master. What you give up is an editable "
                "timeline afterwards, not quality.")


class ResolveUnavailable(RuntimeError):
    """Resolve could not be driven. The message says why, and what to do instead."""


def module_paths() -> list[str]:
    paths = []
    env = os.environ.get("RESOLVE_SCRIPT_API")
    if env:
        paths.append(os.path.join(env, "Modules"))
    paths.extend(_MODULE_PATHS.get(platform.system(), []))
    return paths


def _import_module():
    """The scripting module, or None with the paths that were tried."""
    for p in module_paths():
        if p and os.path.isdir(p) and p not in sys.path:
            sys.path.append(p)
    try:
        import DaVinciResolveScript as dvr           # noqa: PLC0415
        return dvr
    except ImportError:
        return None


def connect(_scriptapp=None):
    """A live Resolve handle, or ResolveUnavailable with every cause worth checking, in order.

    `_scriptapp` is for the selftest: the real one needs a running application.
    """
    if _scriptapp is None:
        dvr = _import_module()
        if dvr is None:
            raise ResolveUnavailable(
                "Cannot import DaVinciResolveScript, so Resolve cannot be driven from here.\n"
                "  Looked in:\n    " + "\n    ".join(module_paths() or ["(no known path)"]) + "\n"
                "  If Resolve is installed somewhere else, set RESOLVE_SCRIPT_API to its "
                "Developer/Scripting folder.\n"
                "  If Resolve is not installed at all: " + NO_NLE_ROUTE)
        _scriptapp = dvr.scriptapp

    r = _scriptapp("Resolve")
    if r is None:
        raise ResolveUnavailable(
            "scriptapp('Resolve') returned None: the application is there but will not accept a "
            "connection. In the order worth checking:\n"
            "  1. Resolve is not running, or is still starting up / quitting.\n"
            "  2. You have the FREE version. External scripting is configured under "
            "'Preferences > System > General', which Blackmagic's own scripting README documents "
            "for Resolve STUDIO. If so there is nothing to fix in the app — see below.\n"
            "  3. Its UI is showing a MODAL DIALOG. While one is up, every API call returns None "
            "until it is dismissed.\n"
            "  4. The Mac is LOCKED. scriptapp() blocks forever rather than failing.\n"
            "  5. External scripting is set to None in Preferences (Studio only).\n"
            "  " + NO_NLE_ROUTE)

    # A blocked UI answers the handshake and then fails everything after it, which is worse than
    # refusing outright because a build gets part-way in before dying.
    try:
        page = r.GetCurrentPage()
    except Exception as e:                                        # noqa: BLE001
        raise ResolveUnavailable(f"Resolve answered but GetCurrentPage() raised {e!r}. Its UI is "
                                 f"probably blocked — look for a dialog.") from None
    if page is None:
        raise ResolveUnavailable(
            "Resolve is connected but its UI is blocked (GetCurrentPage() returned None) — look "
            "for a modal dialog, and check the machine is not locked. Every call would return None "
            "from here, so a build would fail part-way through rather than at the start.")
    return r


def product_name(r) -> str:
    """'DaVinci Resolve Studio' or 'DaVinci Resolve'. Empty string if it cannot be read."""
    try:
        return str(r.GetProductName() or "")
    except Exception:                                             # noqa: BLE001
        return ""


def is_studio(r) -> bool:
    """🔴 Absence of evidence is not free: an unreadable product name must not read as Studio, or a
    Studio-only feature is attempted anyway and fails somewhere less obvious."""
    return product_name(r).strip().lower() == STUDIO_NAME.lower()


def require_studio(r, feature: str) -> None:
    """Refuse a Studio-only feature by name, and say what to do instead."""
    if is_studio(r):
        return
    got = product_name(r) or "an unreadable product name"
    raise ResolveUnavailable(
        f"{feature} needs DaVinci Resolve Studio, and this is {got}.\n"
        f"  Studio-only API calls do not raise — they return False or None, so a build that "
        f"ignored this would report success and be wrong.\n"
        f"  " + NO_NLE_ROUTE)


def describe() -> str:
    """What this machine can actually do. Answers the free-vs-Studio question with a measurement
    instead of an inference, which is the whole point of shipping it."""
    lines = [f"platform: {platform.system()}"]
    paths = module_paths()
    lines.append("scripting module search path:")
    for p in paths:
        lines.append(f"  {'found  ' if os.path.isdir(p) else 'missing'} {p}")
    if _import_module() is None:
        lines.append("import DaVinciResolveScript: FAILED — Resolve cannot be driven from here.")
        lines.append("")
        lines.append(NO_NLE_ROUTE)
        return "\n".join(lines)
    lines.append("import DaVinciResolveScript: ok")
    try:
        r = connect()
    except ResolveUnavailable as e:
        lines.append("connect: FAILED")
        lines.append("  " + str(e).replace("\n", "\n  "))
        return "\n".join(lines)
    name = product_name(r)
    lines.append(f"connect: ok")
    lines.append(f"product: {name or '(unreadable)'}")
    lines.append(f"studio:  {is_studio(r)}")
    try:
        lines.append(f"version: {r.GetVersionString()}")
    except Exception:                                             # noqa: BLE001
        pass
    lines.append("")
    lines.append("⚠️ If this reads 'DaVinci Resolve' (not Studio) AND connect succeeded, then "
                 "external scripting is NOT Studio-gated and the note in this file's docstring "
                 "should be corrected. That is worth reporting.")
    return "\n".join(lines)


def selftest() -> int:
    """Needs no Resolve. The messages and the classification are the things that go wrong."""
    failures = []

    def check(name, cond, detail=""):
        if not cond:
            failures.append(name)
        print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"   {detail}" if detail and not cond
                                                          else ""))

    class Fake:
        def __init__(self, name, page="Edit"):
            self._name, self._page = name, page

        def GetProductName(self):
            if self._name is RuntimeError:
                raise RuntimeError("no")
            return self._name

        def GetCurrentPage(self):
            return self._page

    studio, free = Fake(STUDIO_NAME), Fake("DaVinci Resolve")
    check("Studio is recognised", is_studio(studio))
    check("free is NOT recognised as Studio", not is_studio(free))
    check("trailing whitespace does not change the answer", is_studio(Fake(" DaVinci Resolve  ")) is False
          and is_studio(Fake(f"  {STUDIO_NAME} ")) is True)

    # 🔴 an unreadable product name must not pass as Studio
    check("an unreadable product name is not Studio", not is_studio(Fake(RuntimeError)))
    check("an empty product name is not Studio", not is_studio(Fake("")))

    # require_studio: allows Studio, refuses everything else, and always offers the alternative
    try:
        require_studio(studio, "SetCDL")
        check("require_studio allows Studio", True)
    except ResolveUnavailable:
        check("require_studio allows Studio", False)
    for label, fake in (("free", free), ("unreadable", Fake(RuntimeError))):
        try:
            require_studio(fake, "SetCDL")
            check(f"require_studio refuses {label}", False, "it allowed it")
        except ResolveUnavailable as e:
            msg = str(e)
            check(f"require_studio refuses {label}", True)
            check(f"...naming the feature ({label})", "SetCDL" in msg)
            check(f"...and pointing at the no-NLE route ({label})", "nle-backends.md" in msg)
            check(f"...and saying a Studio-only call returns False rather than raising ({label})",
                  "return False" in msg)

    # connect(): None must mention the FREE version, not only the modal dialog
    try:
        connect(_scriptapp=lambda _n: None)
        check("connect refuses a None handle", False, "it returned")
    except ResolveUnavailable as e:
        msg = str(e)
        check("connect refuses a None handle", True)
        check("...mentions the free version", "FREE" in msg)
        check("...still mentions the modal dialog", "MODAL DIALOG" in msg)
        check("...mentions a locked Mac", "LOCKED" in msg)
        check("...offers the no-NLE route", "nle-backends.md" in msg)
        # the old message named ONLY the dialog, which is the bug this replaces
        check("...does not put the dialog first",
              msg.index("FREE") < msg.index("MODAL DIALOG"),
              "a free user has no dialog to find; that cause must not lead")

    # a blocked UI is a distinct failure from a refused connection
    try:
        connect(_scriptapp=lambda _n: Fake(STUDIO_NAME, page=None))
        check("a blocked UI is refused", False, "it returned a handle")
    except ResolveUnavailable as e:
        check("a blocked UI is refused", True)
        check("...and says a build would fail part-way", "part-way" in str(e))

    # a healthy connection comes back
    got = connect(_scriptapp=lambda _n: studio)
    check("a healthy connection returns the handle", got is studio)

    # GetCurrentPage raising is handled rather than escaping as a traceback
    class Raises(Fake):
        def GetCurrentPage(self):
            raise RuntimeError("boom")
    try:
        connect(_scriptapp=lambda _n: Raises(STUDIO_NAME))
        check("a raising GetCurrentPage is refused", False)
    except ResolveUnavailable as e:
        check("a raising GetCurrentPage is refused", "blocked" in str(e))

    check("module search path is non-empty on this platform", bool(module_paths()))

    print(f"\n{'PASS' if not failures else 'FAIL'}: {len(failures)} failure(s)")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="what this machine can do")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.check:
        print(describe())
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

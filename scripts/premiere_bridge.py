#!/usr/bin/env python3
"""premiere_bridge.py — drive Premiere Pro from Python, through the UXP panel.

Resolve hands you `DaVinciResolveScript`, so a build script runs in a shell and talks to the
application. Premiere has no such door: UXP is a JavaScript runtime inside the app with no CLI and
no socket you may open to it. So the backend is a panel (`premiere/uxp/`) and this is the other
half — it writes a job file, waits for the panel to write a result beside it, and returns it.

    python3 premiere_bridge.py --ping
    python3 premiere_bridge.py --probe                     # DO THIS FIRST, every new Premiere build
    python3 premiere_bridge.py --build plan.json
    python3 premiere_bridge.py --readback --out seen.json
    python3 premiere_bridge.py --render out.mov --preset delivery.epr
    python3 premiere_bridge.py --selftest

The one manual step is opening the panel and pressing **Start listening**. That is not worse than
Resolve, which needs the application open and — measured — sitting on the right window, or
`CreateProject` hands back a project the app has not switched to.

WHY A JOB FOLDER AND NOT A SOCKET
---------------------------------
A UXP plugin can be granted `localFileSystem: "fullAccess"`, and a file that is written and then
read back is the same verification discipline used everywhere else here. A socket would also need
a network permission in the manifest, would die with the panel, and would leave nothing on disk to
read afterwards when a build goes wrong at 1 a.m. The job and its result are the log.

⚠️ **The panel's result is a report, not a receipt.** Every op reports what it read back out of
the application rather than what it asked for, and `--build` fails on any mismatch. A build that
says `ok` with warnings has placed something somewhere you did not plan.
"""

import argparse
import json
import os
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    import nle_ir
except ImportError:                                        # pragma: no cover
    nle_ir = None

DEFAULT_JOBDIR = os.path.expanduser("~/.film-shoot/premiere-jobs")
DEFAULT_TIMEOUT = 600.0


class PanelTimeout(RuntimeError):
    pass


class PanelError(RuntimeError):
    pass


class Bridge(object):
    def __init__(self, jobdir=DEFAULT_JOBDIR, timeout=DEFAULT_TIMEOUT, poll=0.4, quiet=False):
        self.jobdir = os.path.abspath(os.path.expanduser(jobdir))
        self.timeout = timeout
        self.poll = poll
        self.quiet = quiet
        os.makedirs(self.jobdir, exist_ok=True)

    # -- plumbing -----------------------------------------------------------------------------

    def _say(self, msg):
        if not self.quiet:
            sys.stderr.write(msg + "\n")
            sys.stderr.flush()

    def send(self, job, timeout=None):
        """Write a job, wait for its result, return the parsed result."""
        jid = "%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:6])
        name = "job-%s.json" % jid
        path = os.path.join(self.jobdir, name)
        result_path = path[:-len(".json")] + ".result.json"

        # Write to a temporary name and rename, so the panel can never read a half-written job.
        tmp = path + ".partial"
        with open(tmp, "w") as fh:
            json.dump(job, fh, indent=2)
        os.rename(tmp, path)
        self._say("-> %s (%s)" % (name, job.get("op")))

        deadline = time.time() + (self.timeout if timeout is None else timeout)
        while time.time() < deadline:
            if os.path.exists(result_path):
                # the panel writes with a single write; still, retry a truncated parse once
                for _ in range(3):
                    try:
                        with open(result_path) as fh:
                            return json.load(fh)
                    except ValueError:
                        time.sleep(0.15)
                raise PanelError("result file %s never parsed as JSON" % result_path)
            time.sleep(self.poll)

        raise PanelTimeout(
            "no result after %.0f s.\n"
            "  Check, in this order:\n"
            "    1. Is the film-shoot panel open in Premiere?  Window > Extensions\n"
            "    2. Is it 'listening'?  The button must read 'Stop listening'.\n"
            "    3. Is its job folder %s ?\n"
            "    4. Is Premiere showing a modal dialog? A modal blocks the whole plugin host, and\n"
            "       no script can dismiss it — the Resolve side of this project lost a session to\n"
            "       exactly that.\n"
            "  The job is still at %s and will run whenever the panel starts listening."
            % ((self.timeout if timeout is None else timeout), self.jobdir, path))

    # -- ops ----------------------------------------------------------------------------------

    def ping(self):
        return self.send({"op": "ping"}, timeout=30.0)

    def probe(self):
        return self.send({"op": "probe"}, timeout=120.0)

    def readback(self, with_components=False):
        return self.send({"op": "readback", "with_components": bool(with_components)})

    def render(self, output, preset, queue_to_ame=False):
        return self.send({"op": "render", "output": os.path.abspath(output),
                          "preset": os.path.abspath(preset),
                          "queue_to_ame": bool(queue_to_ame)})

    def build(self, plan, allow_unverified=False):
        """Gate the plan, then build it, then refuse to call a warning a success."""
        if nle_ir is not None:
            errs = nle_ir.validate(plan)
            errs += nle_ir.capability_gate(plan, "premiere", allow_unverified=allow_unverified)
            if errs:
                raise PanelError(
                    "plan rejected before it was sent (%d problems):\n  - %s"
                    % (len(errs), "\n  - ".join(errs)))
        else:
            self._say("WARNING: nle_ir not importable, so the plan was NOT validated.")

        result = self.send({"op": "build", "plan": plan})
        if not result.get("ok"):
            raise PanelError("build failed: %s" % result.get("error"))
        warnings = result.get("warnings") or []
        if warnings:
            raise PanelError(
                "the build reported ok AND %d warnings, which means the timeline is not what the\n"
                "plan says. Treating that as a failure:\n  - %s"
                % (len(warnings), "\n  - ".join(warnings)))
        return result


# --------------------------------------------------------------------------------------------
# reading the probe report — the gate between "the API declares it" and "it works here"
# --------------------------------------------------------------------------------------------

def summarise_probe(rep):
    """Turn a probe report into the handful of lines that decide whether to trust the backend."""
    lines = []
    ok = True

    host = rep.get("host") or {}
    lines.append("host: %s %s" % (host.get("name"), host.get("version")))

    tps = rep.get("checks", {}).get("ticksPerSecond") or {}
    if tps.get("matches") is True:
        lines.append("ticks/second: %s  CONFIRMED" % tps.get("ticksNumber"))
    else:
        ok = False
        lines.append("ticks/second: %r  🔴 does not match the panel's constant" % tps)

    for row in rep.get("checks", {}).get("frameRateRoundTrip", []):
        aw = row.get("afterWrite") or {}
        ff = row.get("fromFloat") or {}
        frames = row.get("frames") or {}
        exact = all(v.get("ticksExact") for v in frames.values()) if frames else False
        state = "exact" if exact else "NOT EXACT"
        if not exact:
            ok = False
        lines.append("%-18s float->%s  write->%s  frame ticks %s"
                     % (row.get("label"),
                        "exact" if ff.get("exactFromFloat") else "rounded",
                        "ok" if aw.get("writable") else "REFUSED",
                        state))

    chain = rep.get("checks", {}).get("firstClipChain")
    if not chain:
        ok = False
        lines.append("🔴 no clip was inspected, so the intrinsic parameter names are still "
                     "unknown. Put one clip on V1 and probe again.")
    else:
        names = []
        for comp in chain.get("components", []):
            params = [p.get("displayName") for p in comp.get("params", [])]
            names.append("%s [%s]" % (comp.get("matchName"), ", ".join(str(p) for p in params)))
        lines.append("components on the first clip:")
        for n in names:
            lines.append("    " + n)
        flat = " ".join(names).lower()
        for needed in ("scale", "position", "opacity"):
            if needed not in flat:
                ok = False
                lines.append("    🔴 no parameter named %r — the crop/fade code looks params up "
                             "by display name and will refuse to guess an index." % needed)

    base = rep.get("checks", {}).get("trackIndexBase") or []
    for row in base:
        lines.append("getVideoTrack(%s) -> reported index %r name %r"
                     % (row.get("askedFor"), row.get("reportedIndex"), row.get("name")))

    enc = rep.get("checks", {}).get("encoder") or {}
    lines.append("AME installed: %r" % enc.get("isAMEInstalled"))

    for note in rep.get("notes", []):
        lines.append("note: " + note)

    return ok, lines


# --------------------------------------------------------------------------------------------
# selftest — exercise the transport with a stand-in panel, no Premiere required
# --------------------------------------------------------------------------------------------

def selftest():
    import shutil
    import tempfile
    import threading

    failures = []
    tmp = tempfile.mkdtemp(prefix="premiere-bridge-selftest-")
    try:
        b = Bridge(jobdir=tmp, timeout=6.0, poll=0.05, quiet=True)

        # --- a stand-in panel: answer every job that appears
        stop = threading.Event()
        answered = []

        def fake_panel(reply):
            while not stop.is_set():
                for n in sorted(os.listdir(tmp)):
                    if not (n.startswith("job-") and n.endswith(".json")):
                        continue
                    if n.endswith(".result.json") or n.endswith(".partial"):
                        continue
                    rp = os.path.join(tmp, n[:-len(".json")] + ".result.json")
                    if os.path.exists(rp):
                        continue
                    with open(os.path.join(tmp, n)) as fh:
                        job = json.load(fh)
                    answered.append(job)
                    with open(rp, "w") as fh:
                        json.dump(reply(job), fh)
                stop.wait(0.03)

        t = threading.Thread(target=fake_panel, args=(lambda j: {"ok": True, "op": j["op"],
                                                                "echo": j.get("op")},),
                             daemon=True)
        t.start()
        r = b.ping()
        if not r.get("ok") or r.get("echo") != "ping":
            failures.append("ping round trip: got %r" % (r,))
        stop.set(); t.join(timeout=2)

        # --- a timeout must name the four things to check, not just say "timeout"
        b2 = Bridge(jobdir=tmp, timeout=0.3, poll=0.05, quiet=True)
        try:
            b2.send({"op": "ping"})
            failures.append("a job with no panel should have raised PanelTimeout")
        except PanelTimeout as e:
            msg = str(e)
            for needed in ("listening", "job folder", "modal"):
                if needed not in msg:
                    failures.append("timeout message does not mention %r" % needed)

        # --- a half-written job must never be visible as a job
        names = [n for n in os.listdir(tmp) if n.endswith(".partial")]
        if names:
            failures.append("a .partial file was left behind: %r" % names)

        # --- the build gate must refuse before anything is sent
        if nle_ir is not None:
            plan = {"name": "x", "fps": [30000, 1001], "timeline_raster": [1080, 1920],
                    "media": {"a": "/tmp/a.mov"},
                    "tracks": [{"id": "V1", "kind": "video", "index": 1, "nodes": [
                        {"kind": "clip", "media": "a", "rec": 0, "src_in": 0, "len": 10,
                         "speed": 0.5}]}]}
            before = len(answered)
            b3 = Bridge(jobdir=tmp, timeout=0.3, poll=0.05, quiet=True)
            try:
                b3.build(plan, allow_unverified=True)
                failures.append("a plan needing a speed change should have been refused")
            except PanelError as e:
                if "Retime the media" not in str(e):
                    failures.append("speed refusal did not explain the workaround: %s" % e)
            if len(answered) != before:
                failures.append("a rejected plan was still sent to the panel")

        # --- ok-with-warnings must be treated as failure
        stop2 = threading.Event()

        def warn_panel():
            while not stop2.is_set():
                for n in sorted(os.listdir(tmp)):
                    if not (n.startswith("job-") and n.endswith(".json")):
                        continue
                    if n.endswith(".result.json") or n.endswith(".partial"):
                        continue
                    rp = os.path.join(tmp, n[:-len(".json")] + ".result.json")
                    if os.path.exists(rp):
                        continue
                    with open(rp, "w") as fh:
                        json.dump({"ok": True, "warnings": ["clip slid to frame 266"]}, fh)
                stop2.wait(0.03)

        t2 = threading.Thread(target=warn_panel, daemon=True)
        t2.start()
        if nle_ir is not None:
            good = {"name": "x", "fps": [30000, 1001], "timeline_raster": [1080, 1920],
                    "media": {"a": "/tmp/a.mov"},
                    "tracks": [{"id": "V1", "kind": "video", "index": 1, "nodes": [
                        {"kind": "clip", "media": "a", "rec": 0, "src_in": 0, "len": 10}]}]}
            b4 = Bridge(jobdir=tmp, timeout=5.0, poll=0.05, quiet=True)
            try:
                b4.build(good, allow_unverified=True)
                failures.append("ok-with-warnings should NOT have counted as a successful build")
            except PanelError as e:
                if "not what the\nplan says" not in str(e) and "warnings" not in str(e):
                    failures.append("warning refusal message was unclear: %s" % e)
        stop2.set(); t2.join(timeout=2)

        # --- probe summary: a report missing the clip inspection must not read as healthy
        ok, lines = summarise_probe({"host": {"name": "Premiere Pro", "version": "26.5.1"},
                                     "checks": {"ticksPerSecond": {"matches": True,
                                                                   "ticksNumber": 254016000000}},
                                     "notes": []})
        if ok:
            failures.append("a probe report with no clip inspected was summarised as healthy")
        if not any("no clip was inspected" in l for l in lines):
            failures.append("probe summary did not say the clip inspection was missing")

        ok2, _ = summarise_probe({
            "host": {"name": "Premiere Pro", "version": "26.5.1"},
            "checks": {
                "ticksPerSecond": {"matches": True, "ticksNumber": 254016000000},
                "frameRateRoundTrip": [{"label": "29.97 drop-frame",
                                        "fromFloat": {"exactFromFloat": True},
                                        "afterWrite": {"writable": True},
                                        "frames": {"f1": {"ticksExact": True}}}],
                "firstClipChain": {"components": [
                    {"matchName": "AE.ADBE Motion",
                     "params": [{"displayName": "Position"}, {"displayName": "Scale"}]},
                    {"matchName": "AE.ADBE Opacity",
                     "params": [{"displayName": "Opacity"}]}]},
                "encoder": {"isAMEInstalled": True}},
            "notes": []})
        if not ok2:
            failures.append("a complete, healthy probe report was summarised as unhealthy")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELFTEST FAILED (%d)" % len(failures))
        for f in failures:
            print("  - " + f)
        return 1
    print("premiere_bridge selftest: all checks passed")
    return 0


# --------------------------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--jobdir", default=DEFAULT_JOBDIR)
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--out", help="write the raw result JSON here")
    ap.add_argument("--ping", action="store_true")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--build", metavar="PLAN.JSON")
    ap.add_argument("--readback", action="store_true")
    ap.add_argument("--with-components", action="store_true")
    ap.add_argument("--render", metavar="OUTPUT")
    ap.add_argument("--preset", metavar="PRESET.EPR")
    ap.add_argument("--queue-to-ame", action="store_true")
    ap.add_argument("--allow-unverified", action="store_true",
                    help="build even though the Premiere backend's capabilities are unproven")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    b = Bridge(jobdir=a.jobdir, timeout=a.timeout)
    print("job folder: %s" % b.jobdir)

    try:
        if a.ping:
            res = b.ping()
        elif a.probe:
            res = b.probe()
        elif a.build:
            with open(a.build) as fh:
                plan = json.load(fh)
            res = b.build(plan, allow_unverified=a.allow_unverified)
        elif a.readback:
            res = b.readback(with_components=a.with_components)
        elif a.render:
            if not a.preset:
                ap.error("--render needs --preset")
            res = b.render(a.render, a.preset, queue_to_ame=a.queue_to_ame)
        else:
            ap.error("give one of --ping --probe --build --readback --render --selftest")
    except (PanelTimeout, PanelError) as e:
        print("\n%s" % e)
        return 1

    if a.out:
        with open(a.out, "w") as fh:
            json.dump(res, fh, indent=2)
        print("result written to %s" % a.out)

    if a.probe:
        healthy, lines = summarise_probe(res)
        print("")
        for l in lines:
            print("  " + l)
        print("")
        if healthy:
            print("PROBE OK — the facts the backend needs are all present. Flip the "
                  "'premiere' entries in nle_ir.CAPABILITIES from UNVERIFIED to True only for "
                  "the ones you have now actually seen work end to end.")
            return 0
        print("PROBE INCOMPLETE — do not trust the Premiere backend yet. The lines above marked "
              "🔴 say what is missing.")
        return 1

    print(json.dumps(res, indent=2)[:4000])
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    # Post tool: needs an active license (see license_check.py). --selftest is exempt.
    from license_check import gate
    gate(__file__)
    sys.exit(main())

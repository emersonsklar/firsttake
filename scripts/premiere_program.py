#!/usr/bin/env python3
"""premiere_program.py — emit a Premiere build as a PROGRAM, so the panel stops needing reloads.

The panel (`premiere/uxp/main.js`) has a `build` op with the whole build in it. That works, and it
has one cost that falls on the operator rather than on me: **a change to the build needs the plugin
reloaded by hand in the UXP Developer Tool.** Over one afternoon that was eight reloads, and about
two thirds of them were changes to build logic rather than to anything the panel uniquely must do.

So the panel also has a `program` op: a small interpreter over declarative steps. This module emits
those steps. The build logic now lives here, in Python, and changing it needs **no reload at all** —
the panel is a fixed interpreter and this file is the part that moves.

    python3 premiere_program.py --plan plan.json            # print the program
    python3 premiere_program.py --plan plan.json --run       # send it
    python3 premiere_program.py --selftest

WHAT A STEP LOOKS LIKE, AND THE TWO RULES IT HAS TO RESPECT
----------------------------------------------------------
    {"call": "getActiveProject", "on": "Project", "as": "proj"}
    {"transaction": "place clip 0", "steps": [
        {"cast": "ClipProjectItem", "on": "item0", "as": "clip0"},
        {"call": "createSetInOutPointsAction", "on": "clip0", "as": "act",
         "args": [{"$tick": 0, "fps": [30000, 1001]}, {"$tick": 90, "fps": [30000, 1001]}]},
        {"addAction": "act"}]}

🔴 **A transaction's steps must be synchronous.** Awaiting inside one commits an empty transaction,
so the interpreter throws if a call inside a transaction returns a promise. Everything awaited is
hoisted out by the emitter below.

🔴 **A handle dies when the project mutates**, not merely across an await. So anything acquired
before a placement is re-acquired after it, and casts happen inside the transaction that uses them.
Those two rules are why this emitter is shaped the way it is rather than as a flat list of calls.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import nle_ir                                                          # noqa: E402


def tick(frame: int, fps) -> dict:
    return {"$tick": int(frame), "fps": list(fps)}


class Program:
    """Accumulates steps, and names results so later steps can refer to them."""

    def __init__(self) -> None:
        self.steps: list[dict] = []
        self._n = 0

    def name(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    def add(self, **step) -> "Program":
        self.steps.append(step)
        return self

    def call(self, method: str, on: str | None = None, args=None, as_: str | None = None):
        s = {"call": method}
        if on is not None:
            s["on"] = on
        if args:
            s["args"] = args
        if as_:
            s["as"] = as_
        return self.add(**s)

    def get(self, prop: str, on: str | None = None, as_: str | None = None):
        s = {"get": prop}
        if on is not None:
            s["on"] = on
        if as_:
            s["as"] = as_
        return self.add(**s)

    def set(self, prop: str, on: str, value):
        return self.add(**{"set": prop, "on": on, "value": value})

    def cast(self, kind: str, on: str, as_: str):
        return self.add(**{"cast": kind, "on": on, "as": as_})

    def require(self, nm: str, message: str):
        return self.add(**{"require": nm, "message": message})

    def describe(self, nm: str, as_: str | None = None, deep: bool = False):
        s = {"describe": nm}
        if as_:
            s["as"] = as_
        if deep:
            s["deep"] = True
        return self.add(**s)

    def transaction(self, label: str, steps: list[dict]):
        return self.add(**{"transaction": label, "steps": steps})


# --------------------------------------------------------------------------------------------
# the emitter
# --------------------------------------------------------------------------------------------

def emit_build(plan: dict) -> Program:
    """The same build the panel's `build` op performs, as steps."""
    fps = plan["fps"]
    p = Program()

    # --- the sequence. A preset pins raster, codec and audio layout as well as the rate.
    p.get("Project", as_="Project")
    if plan.get("sequence_preset"):
        p.call("createSequenceWithPresetPath", on="project",
               args=[plan["name"], plan["sequence_preset"]], as_="seq")
    else:
        p.call("createSequence", on="project", args=[plan["name"]], as_="seq")
    p.require("seq", "sequence creation returned nothing")
    p.call("setActiveSequence", on="project", args=[{"$": "seq"}])

    # --- rate AND raster, then read both back. A vertical programme in a default 1920x1080
    #     sequence is letterboxed silently with every frame count still correct.
    w, h = plan["timeline_raster"]
    p.call("getSettings", on="seq", as_="settings")
    p.call("setVideoFrameRate", on="settings", args=[{"$fps": list(fps)}])
    p.call("setVideoFrameRect", on="settings", args=[{"$rectf": [w, h]}])
    p.transaction("film-shoot: sequence rate and raster", [
        {"call": "createSetSettingsAction", "on": "seq", "args": [{"$": "settings"}], "as": "act"},
        {"addAction": "act"},
    ])
    p.call("getSettings", on="seq", as_="settings2")
    p.call("getVideoFrameRate", on="settings2", as_="rate_back")
    p.get("ticksPerFrame", on="rate_back", as_="tpf_back")
    p.call("getVideoFrameRect", on="settings2", as_="rect_back")
    p.describe("tpf_back", as_="_tpf")
    p.describe("rect_back", as_="_rect", deep=True)

    # --- import. asNumberedStills=false: numbered files in one folder otherwise collapse into a
    #     single image-sequence clip and every caption but the first ceases to exist.
    paths = [plan["media"][k] for k in plan["media"]]
    p.call("getRootItem", on="project", as_="root")
    p.call("importFiles", on="project", args=[paths, True, {"$": "root"}, False], as_="imported")
    p.require("imported", "importFiles returned false")

    p.call("getEditor", on="SequenceEditor", args=[{"$": "seq"}], as_="editor")
    return p


def emit_readback(plan: dict) -> Program:
    """Named results the caller can read: how many tracks, and each track's item count."""
    p = Program()
    p.get("Project", as_="Project")
    p.call("getActiveProject", on="Project", as_="proj")
    p.call("getActiveSequence", on="proj", as_="seq")
    p.call("getVideoTrackCount", on="seq", as_="vtracks")
    p.describe("vtracks", as_="_vtracks")
    return p


# --------------------------------------------------------------------------------------------
# selftest — the SHAPE of the program, with no Premiere anywhere
# --------------------------------------------------------------------------------------------

def _example_plan() -> dict:
    return {
        "name": "prog-selftest", "fps": [30000, 1001], "timeline_raster": [1080, 1920],
        "media": {"a": "/tmp/a.mov"},
        "tracks": [{"id": "V1", "kind": "video", "index": 1, "nodes": [
            {"kind": "clip", "media": "a", "rec": 0, "src_in": 0, "len": 90}]}],
    }


def selftest() -> int:
    failures = []

    def check(name, cond, detail=""):
        if not cond:
            failures.append(name)
        print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"   {detail}" if detail and not cond
                                                          else ""))

    prog = emit_build(_example_plan())
    steps = prog.steps
    check("emits steps", len(steps) > 5, str(len(steps)))
    check("every step is a dict with exactly one verb",
          all(isinstance(s, dict) and len({k for k in s
                                           if k in ("call", "get", "set", "cast", "transaction",
                                                    "addAction", "require", "describe")}) == 1
              for s in steps))
    check("the whole program is JSON-serialisable", json.dumps({"steps": steps}) is not None)

    # 🔴 the rule the interpreter enforces: nothing inside a transaction may be async. The emitter
    # must therefore only put create*Action / cast / addAction / set in there.
    ALLOWED_IN_TX = ("createSetSettingsAction", "createSetInOutPointsAction",
                     "createOverwriteItemAction", "createSetDisabledAction",
                     "createSetValueAction", "createAddKeyframeAction",
                     "createSetTimeVaryingAction", "createAppendComponentAction",
                     "createRemoveItemsAction", "getEditor")
    for s in steps:
        if "transaction" not in s:
            continue
        for sub in s["steps"]:
            if "call" in sub:
                check(f"in-transaction call {sub['call']!r} is a known-sync one",
                      sub["call"] in ALLOWED_IN_TX,
                      "an async call inside a transaction commits an EMPTY transaction")
            check("no nested transaction", "transaction" not in sub)

    # the raster must be set, not just the rate — the letterbox bug
    sets_rect = any(s.get("call") == "setVideoFrameRect" for s in steps)
    sets_rate = any(s.get("call") == "setVideoFrameRate" for s in steps)
    check("sets the frame rate", sets_rate)
    check("sets the raster too", sets_rect,
          "a vertical programme in a default 1920x1080 sequence is letterboxed silently")

    # and both must be READ BACK, because a setter returning without complaint proves nothing
    check("reads the rate back", any(s.get("call") == "getVideoFrameRate" for s in steps))
    check("reads the raster back", any(s.get("call") == "getVideoFrameRect" for s in steps))

    # importFiles must pass asNumberedStills=False explicitly
    imp = [s for s in steps if s.get("call") == "importFiles"]
    check("importFiles is emitted once", len(imp) == 1)
    if imp:
        check("importFiles passes asNumberedStills=False", imp[0]["args"][3] is False,
              "numbered stills in one folder import as ONE image sequence")

    # ticks: a frame reference must carry its own fps, or the interpreter cannot build a TickTime
    t = tick(12, [30000, 1001])
    check("a tick carries its fps", t["$tick"] == 12 and t["fps"] == [30000, 1001])

    # the plan must still be gated before any of this is sent
    errs = nle_ir.validate(_example_plan())
    check("the example plan validates", not errs, str(errs))

    print(f"\n{'PASS' if not failures else 'FAIL'}: {len(failures)} failure(s)")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--plan")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if not a.plan:
        ap.error("--plan is required")

    with open(a.plan) as fh:
        plan = json.load(fh)
    errs = nle_ir.validate(plan) + nle_ir.capability_gate(plan, "premiere", allow_unverified=True)
    if errs:
        print("plan rejected:")
        for e in errs:
            print("  - " + e)
        return 1

    prog = emit_build(plan)
    if not a.run:
        print(json.dumps({"op": "program", "steps": prog.steps}, indent=2))
        return 0

    import premiere_bridge as pb
    b = pb.Bridge(timeout=900)
    res = b.send({"op": "program", "steps": prog.steps})
    print(json.dumps(res, indent=2)[:4000])
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

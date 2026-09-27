#!/usr/bin/env python3
"""premiere_program.py — the Premiere build, as a PROGRAM, so changing it needs no panel reload.

The panel (`premiere/uxp/main.js`) has a `build` op with the whole build inside it. That works, and
it has one cost that falls on the operator rather than on me: **a change to the build needs the
plugin reloaded by hand in the UXP Developer Tool.** Over one afternoon that was eight reloads, and
about two thirds were changes to build logic rather than to anything the panel uniquely must do.

So the panel also has a `program` op: a small interpreter over declarative steps. This module emits
those steps and drives them. **The build logic now lives here, in Python.** Changing how a clip is
placed, how a fade is shaped, what is asserted afterwards — none of it touches the panel.

    python3 premiere_program.py --plan plan.json            # print the phases, send nothing
    python3 premiere_program.py --plan plan.json --run      # build it
    python3 premiere_program.py --selftest

WHAT THE PANEL STILL OWNS, AND WHY
----------------------------------
Three things, all of them fixed primitives rather than build logic:

1. `strip_audio` — `TrackItemSelection.createEmptySelection(cb)` hands its selection to a CALLBACK,
   and a declarative step list cannot say "and then, inside that callback…". It reports counts and
   refuses nothing; the decision to fail is made here.
2. `readback` — describing a component chain is a nested loop over counts that are only known at
   runtime. Used here to resolve component and parameter INDICES, which is exactly the discovery
   this module then compiles against.
3. `render` — one call, no logic.

THE TWO RULES THE EMITTER IS SHAPED BY
--------------------------------------
🔴 **A transaction's steps must be synchronous.** Awaiting inside one commits an empty transaction,
so the interpreter throws if a call inside a transaction returns a promise. Everything awaited is
hoisted out.

🔴 **A handle dies when the project MUTATES**, not merely across an await. So the editor, the track
and the project item are re-acquired after every placement rather than held across the loop. That is
why this emits so many small steps instead of a tidy few.

WHY IT IS PHASED
----------------
Component and parameter indices are not knowable until the clips exist. So the build is a handful of
round trips rather than one: create and import, index the media, place, describe the chains, apply
effects, then audio. Six trips for a build of any size — the loops unroll here, at emit time, so a
200-clip cut costs the same number of round trips as a 5-clip one.
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

TICKS_PER_SECOND = 254016000000


def tick(frame: int, fps) -> dict:
    return {"$tick": int(frame), "fps": list(fps)}


def ticks_per_frame(fps) -> int:
    num, den = int(fps[0]), int(fps[1])
    if TICKS_PER_SECOND * den % num:
        raise ValueError(f"{num}/{den} does not divide the tick base evenly")
    return TICKS_PER_SECOND * den // num


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
        s: dict = {"call": method}
        if on is not None:
            s["on"] = on
        if args:
            s["args"] = args
        if as_:
            s["as"] = as_
        return self.add(**s)

    def get(self, prop: str, on: str | None = None, as_: str | None = None):
        s: dict = {"get": prop}
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
        s: dict = {"describe": nm}
        if as_:
            s["as"] = as_
        if deep:
            s["deep"] = True
        return self.add(**s)

    def transaction(self, label: str, steps: list[dict]):
        return self.add(**{"transaction": label, "steps": steps})

    # -- shorthands the phases below lean on ------------------------------------------------

    def active_sequence(self, as_: str = "seq"):
        """Re-acquire the sequence. Cheap, and safe after a mutation."""
        self.call("getActiveSequence", on="project", as_=as_)
        self.require(as_, "no active sequence")
        return self

    def track_item(self, seq: str, v_index: int, item_index: int, as_: str):
        """The Nth CLIP item on a video track, re-fetched from scratch."""
        tr = self.name("tr")
        items = self.name("items")
        self.call("getVideoTrack", on=seq, args=[v_index], as_=tr)
        self.require(tr, f"no video track at index {v_index}")
        self.call("getTrackItems", on=tr,
                  args=[{"$const": "Constants.TrackItemType.CLIP"}, False], as_=items)
        self.get(str(item_index), on=items, as_=as_)
        self.require(as_, f"no clip at index {item_index} on video track {v_index}")
        return tr


# --------------------------------------------------------------------------------------------
# helpers shared with the panel's build, kept here so the geometry has ONE definition
# --------------------------------------------------------------------------------------------

def track_index_for(track: dict, plan: dict) -> int:
    """The plan carries 1-based indices because that is what an editor sees on screen."""
    base = plan.get("track_index_base", 0)
    return track["index"] - 1 + base


def video_nodes(plan: dict):
    """(track, node, node_ordinal, where) for every video node, in placement order."""
    for track in plan["tracks"]:
        if track.get("kind") != "video":
            continue
        for n, node in enumerate(track["nodes"]):
            yield track, node, n, f"{track['id']}#{n}"


def audio_nodes(plan: dict):
    for track in plan["tracks"]:
        if track.get("kind") != "audio":
            continue
        for n, node in enumerate(track["nodes"]):
            yield track, node, n, f"{track['id']}#{n}"


def fade_points(node: dict, plan: dict) -> list[dict]:
    """The opacity keyframes a fade becomes.

    🔴 The shape goes on BOTH ends of a ramp. A segment takes its interpolation from the keyframe it
    LEAVES FROM, so shaping only the far end of each pair reads back as [0, 5, 0, 5] and changes
    nothing visible. That passed a weaker assertion once; it is not allowed to again.
    """
    pts: list[dict] = []
    last = node["len"] - 1
    fade = node["fade"]
    if fade.get("in"):
        shape = fade["in"].get("shape")
        pts.append({"f": 0, "v": 0, "shape": shape})
        pts.append({"f": fade["in"]["frames"], "v": 100, "shape": shape})
    if fade.get("out"):
        shape = fade["out"].get("shape")
        pts.append({"f": last - fade["out"]["frames"], "v": 100, "shape": shape})
        pts.append({"f": last, "v": 0, "shape": shape})
    return pts


def keyframe_base(node: dict, plan: dict) -> int:
    """Clip-relative or sequence-absolute? The plan says, because guessing puts a fade thousands of
    frames outside the clip where nothing moves and nothing errors."""
    return node["rec"] if plan.get("keyframe_time_base") == "sequence" else 0


TRANSITION_MATCH = {
    "cross_dissolve": "AE.ADBE Cross Dissolve New",
    "dip_to_black": "AE.ADBE Dip To Black",
    "dip_to_white": "AE.ADBE Dip To White",
}

# Every node key this emitter ACTS ON. Anything else stops the build before a step is sent — the
# same refusal the panel makes, moved to where it can be made without a reload. Keys beginning
# with "_" are commentary.
#
# 🔴 `speed` is deliberately NOT here, and neither is `grade`. Both are listed in the panel's
# HANDLED_NODE_KEYS, which means a plan carrying either builds without it and reports success —
# the panel's list records "a key I recognise", not "a key I do something about". That is the same
# failure as a capability gate checked against what the API can do rather than what the code does.
# Anything this file cannot perform is refused BY NAME below, with the reason.
HANDLED_NODE_KEYS = {"kind", "media", "rec", "len", "src_in", "src_raster", "crop", "fade",
                     "keyframes", "disabled", "transition"}

UNSUPPORTED_NODE_KEYS = {
    "grade": ("Premiere has NO scripted colour of any kind — no CDL entry point, and Lumetri "
              "exposes 130 parameters of which none accepts a string. Pre-render the grade into "
              "the media and declare it in the plan's `baked` list."),
    "speed": ("`getSpeed()` exists and there is no setter anywhere in this API. Retime the media "
              "first; a clip laid at the wrong speed is the right length and the wrong content."),
}


class BuildError(RuntimeError):
    pass


# --------------------------------------------------------------------------------------------
# phase 1 — the sequence, its rate and raster, and the import
# --------------------------------------------------------------------------------------------

def emit_sequence(plan: dict) -> Program:
    fps = plan["fps"]
    w, h = plan["timeline_raster"]
    p = Program()

    if plan.get("sequence_preset"):
        p.call("createSequenceWithPresetPath", on="project",
               args=[plan["name"], plan["sequence_preset"]], as_="seq")
    else:
        p.call("createSequence", on="project", args=[plan["name"]], as_="seq")
    p.require("seq", "sequence creation returned nothing")
    p.call("setActiveSequence", on="project", args=[{"$": "seq"}])

    # 🔴 Set the RASTER as well as the rate. A default sequence is 1920x1080, and a vertical
    # programme built into it is letterboxed with no warning — every frame count and the audio
    # stay correct, so nothing downstream notices.
    p.call("getSettings", on="seq", as_="settings")
    p.call("setVideoFrameRate", on="settings", args=[{"$fps": list(fps)}])
    p.call("setVideoFrameRect", on="settings", args=[{"$rectf": [w, h]}])
    p.transaction("film-shoot: sequence rate and raster", [
        {"call": "createSetSettingsAction", "on": "seq", "args": [{"$": "settings"}], "as": "act"},
        {"addAction": "act"},
    ])

    # and READ BOTH BACK, because a setter that returned without complaint proves nothing
    p.call("getSettings", on="seq", as_="back")
    p.call("getVideoFrameRate", on="back", as_="rate")
    p.get("ticksPerFrame", on="rate", as_="out_ticks_per_frame")
    p.call("getVideoFrameRect", on="back", as_="rect")
    p.get("width", on="rect", as_="out_raster_w")
    p.get("height", on="rect", as_="out_raster_h")
    p.call("getVideoTrackCount", on="seq", as_="out_video_tracks")
    p.call("getAudioTrackCount", on="seq", as_="out_audio_tracks")

    # --- import. asNumberedStills=False: numbered files in one folder otherwise collapse into a
    #     single image-sequence clip and every caption but the first ceases to exist.
    paths = [plan["media"][k] for k in plan["media"]]
    p.call("getRootItem", on="project", as_="root")
    p.call("importFiles", on="project", args=[paths, True, {"$": "root"}, False], as_="imported")
    p.require("imported", "importFiles returned false")
    p.call("getItems", on="root", as_="items")
    p.describe("items", as_="out_items")
    return p


def check_sequence(env: dict, plan: dict) -> dict:
    """Assert the sequence is what was asked for. These are the two refusals worth having."""
    fps = plan["fps"]
    want_tpf = ticks_per_frame(fps)
    got_tpf = _value(env, "out_ticks_per_frame")
    if got_tpf != want_tpf:
        raise BuildError(
            f"sequence frame rate is {got_tpf} ticks/frame, not the {want_tpf} asked for. Refusing "
            f"to build on it — a 29.97 programme on a 30.000 timeline drifts a frame every 33 s and "
            f"reads as a lip-sync fault ten minutes in, not as a build error.")

    want_w, want_h = plan["timeline_raster"]
    got_w, got_h = _value(env, "out_raster_w"), _value(env, "out_raster_h")
    if (got_w, got_h) != (want_w, want_h):
        raise BuildError(
            f"sequence raster is {got_w}x{got_h}, not the {want_w}x{want_h} asked for. Refusing to "
            f"build — a vertical programme in a landscape sequence is letterboxed silently, with "
            f"every frame count still correct.")

    return {"ticksPerFrame": got_tpf, "raster": [got_w, got_h],
            "videoTrackCount": _value(env, "out_video_tracks"),
            "audioTrackCount": _value(env, "out_audio_tracks")}


# --------------------------------------------------------------------------------------------
# phase 2 — which project item is which file
# --------------------------------------------------------------------------------------------

def emit_media_index(count: int) -> Program:
    """Read each root item's name.

    `ProjectItem.name` is a PROPERTY, not a method, and needs no cast — which matters, because
    `getMediaFilePath` lives on ClipProjectItem and a *sequence* in the same bin reports
    `type === 1` exactly as a clip does. Names are enough: the plan's media basenames are distinct.
    """
    p = Program()
    p.call("getRootItem", on="project", as_="root")
    p.call("getItems", on="root", as_="items")
    for i in range(count):
        it = f"it{i}"
        p.get(str(i), on="items", as_=it)
        p.get("name", on=it, as_=f"out_name_{i}")
    return p


def resolve_media(env: dict, count: int, plan: dict) -> dict[str, int]:
    """basename -> index in the root item list."""
    by_name: dict[str, int] = {}
    for i in range(count):
        nm = _value(env, f"out_name_{i}")
        if isinstance(nm, str):
            by_name.setdefault(nm, i)

    out: dict[str, int] = {}
    for key, path in plan["media"].items():
        base = os.path.basename(path)
        if base not in by_name:
            raise BuildError(
                f"media {key!r} ({base}) is not in the project after importing it. The project "
                f"holds: {sorted(by_name)}. A build that guessed an index here would place the "
                f"wrong footage and tile perfectly.")
        out[key] = by_name[base]
    return out


# --------------------------------------------------------------------------------------------
# phase 3 — placement
# --------------------------------------------------------------------------------------------

def emit_placement(plan: dict, media_index: dict[str, int]) -> Program:
    fps = plan["fps"]
    p = Program()
    placed_at: list[tuple[str, int, int]] = []          # (where, v_index, ordinal on that track)
    per_track: dict[int, int] = {}

    for track, node, n, where in video_nodes(plan):
        # Named refusals FIRST, so the reason is the error rather than a generic "unknown key".
        for key, why in UNSUPPORTED_NODE_KEYS.items():
            if node.get(key):
                raise BuildError(f"{where}: the plan asks for {key!r}, which this backend CANNOT "
                                 f"do. {why}")
        unknown = {k for k in node
                   if k not in HANDLED_NODE_KEYS and k not in UNSUPPORTED_NODE_KEYS
                   and not k.startswith("_")}
        if unknown:
            raise BuildError(
                f"{where}: the plan carries {sorted(unknown)}, which this build does not "
                f"implement. Refusing rather than building without it. Implement it, or mark the "
                f"feature unsupported in nle_ir.CAPABILITIES so the gate refuses the plan first.")
        if node["kind"] not in ("clip", "graphic"):
            raise BuildError(
                f"{where}: node kind {node['kind']!r} is defined in the IR but NOT IMPLEMENTED "
                f"here. Refusing rather than failing obscurely.")

        v_index = track_index_for(track, plan)
        idx = media_index[node["media"]]

        # 🔴 Re-acquire the item and the editor for EVERY clip: the previous placement mutated the
        # project, which invalidates both. And in/out lives on the ProjectItem, not on the
        # placement call, so it must be set immediately before placing — batching them would place
        # every clip with the LAST in/out applied, and the timeline would still tile perfectly.
        root = p.name("root")
        items = p.name("items")
        item = p.name("item")
        p.call("getRootItem", on="project", as_=root)
        p.call("getItems", on=root, as_=items)
        p.get(str(idx), on=items, as_=item)
        p.require(item, f"{where}: project item {idx} vanished")

        in_tick = tick(node["src_in"], fps)
        out_tick = tick(node["src_in"] + node["len"], fps)     # EXCLUSIVE, as Resolve's endFrame is
        p.transaction(f"film-shoot: in/out {where}", [
            {"cast": "ClipProjectItem", "on": item, "as": "clip"},
            {"call": "createSetInOutPointsAction", "on": "clip",
             "args": [in_tick, out_tick], "as": "act"},
            {"addAction": "act"},
        ])

        seq = p.name("seq")
        ed = p.name("ed")
        p.active_sequence(seq)
        p.get("SequenceEditor", as_="SE")
        p.call("getEditor", on="SE", args=[{"$": seq}], as_=ed)
        p.require(ed, f"{where}: no sequence editor")
        p.transaction(f"film-shoot: place {where}", [
            {"call": "createOverwriteItemAction", "on": ed,
             "args": [{"$": item}, tick(node["rec"], fps), v_index, v_index], "as": "act"},
            {"addAction": "act"},
        ])

        # read back where it LANDED. A clip that slid still tiles, so a no-gaps check cannot see it.
        ordinal = per_track.get(v_index, 0)
        per_track[v_index] = ordinal + 1
        seq2 = p.name("seq")
        got = p.name("placed")
        p.active_sequence(seq2)
        p.track_item(seq2, v_index, ordinal, got)
        st = p.name("start")
        du = p.name("dur")
        p.call("getStartTime", on=got, as_=st)
        p.get("ticksNumber", on=st, as_=f"out_start_{where}")
        p.call("getDuration", on=got, as_=du)
        p.get("ticksNumber", on=du, as_=f"out_dur_{where}")

        if node.get("disabled"):
            p.transaction(f"film-shoot: disable {where}", [
                {"call": "createSetDisabledAction", "on": got, "args": [True], "as": "act"},
                {"addAction": "act"},
            ])

        placed_at.append((where, v_index, ordinal))

    p.placed_at = placed_at            # type: ignore[attr-defined]
    return p


def check_placement(env: dict, plan: dict) -> list[dict]:
    fps = plan["fps"]
    tpf = ticks_per_frame(fps)
    out = []
    for _track, node, _n, where in video_nodes(plan):
        got = _value(env, f"out_start_{where}")
        want = node["rec"] * tpf
        if got != want:
            raise BuildError(
                f"{where}: asked for frame {node['rec']} ({want} ticks) and it landed at "
                f"{got} ticks. A clip that SLID still tiles, so a no-gaps check cannot see this.")
        dur = _value(env, f"out_dur_{where}")
        want_dur = node["len"] * tpf
        if dur != want_dur:
            raise BuildError(
                f"{where}: asked for {node['len']} frames and the clip is {dur / tpf:g}. "
                f"Source in/out did not take, or a transition ate handles it did not have.")
        out.append({"where": where, "startTicks": got, "durationTicks": dur})
    return out


# --------------------------------------------------------------------------------------------
# phase 4 — resolve component and parameter indices from a read-back
# --------------------------------------------------------------------------------------------

def resolve_chain(components: list[dict], want_component: str, want_param: str,
                  where: str) -> tuple[int, int]:
    """Find (componentIndex, paramIndex) by name, and refuse ambiguity.

    🔴 Parameter display names are NOT unique — `AE.ADBE Opacity` has two params called
    "Blend Mode", and `AE.ADBE Motion` param 3 is named " ". Picking the first match would set an
    effect on a parameter nobody chose, so an ambiguous name is an error, not a coin toss.
    """
    comp_hits = [c for c in components
                 if want_component.lower() in str(c.get("matchName", "")).lower()
                 or want_component.lower() in str(c.get("displayName", "")).lower()]
    if not comp_hits:
        names = [c.get("matchName") for c in components]
        raise BuildError(f"{where}: no {want_component} component on the clip. It has: {names}")
    comp = comp_hits[0]

    hits = [pp for pp in comp.get("params", [])
            if str(pp.get("displayName", "")).lower() == want_param.lower()]
    if len(hits) > 1:
        raise BuildError(
            f"{where}: parameter name {want_param!r} is ambiguous on {comp.get('matchName')} — it "
            f"matches indices {[h['index'] for h in hits]}. Address it by index and record which "
            f"one, because picking the first would set the effect on a parameter nobody chose.")
    if not hits:
        raise BuildError(
            f"{where}: {comp.get('matchName')} has no parameter named {want_param!r} on this "
            f"build. The probe report lists the real names — do not guess an index.")
    return int(comp["index"]), int(hits[0]["index"])


# --------------------------------------------------------------------------------------------
# phase 5 — crop, fades, keyframes, transitions
# --------------------------------------------------------------------------------------------

def emit_effects(plan: dict, placed_at, chains: dict[str, list[dict]]) -> Program:
    fps = plan["fps"]
    p = Program()
    at = {w: (v, o) for (w, v, o) in placed_at}

    for track, node, _n, where in video_nodes(plan):
        if not (node.get("crop") or node.get("fade") or node.get("keyframes")
                or node.get("transition")):
            continue
        v_index, ordinal = at[where]
        comps = chains.get(where)
        if comps is None:
            raise BuildError(f"{where}: no component chain came back for this clip, so an effect "
                             f"cannot be addressed without guessing an index.")

        seq = p.name("seq")
        clip = p.name("clip")
        p.active_sequence(seq)
        tr = p.track_item(seq, v_index, ordinal, clip)

        if node.get("crop"):
            _emit_crop(p, plan, node, comps, clip, where)
        if node.get("fade"):
            _emit_fade(p, plan, node, comps, clip, where, fps)
        if node.get("keyframes"):
            _emit_keyframes(p, plan, node, comps, clip, where, fps)
        if node.get("transition"):
            _emit_transition(p, node, clip, tr, where, fps)

    return p


def _chain(p: Program, clip: str, comp_index: int, param_index: int, where: str):
    """chain -> component -> param, named. getComponentChain is async, so it stays out of a lock."""
    ch = p.name("chain")
    comp = p.name("comp")
    par = p.name("param")
    p.call("getComponentChain", on=clip, as_=ch)
    p.require(ch, f"{where}: no component chain")
    p.call("getComponentAtIndex", on=ch, args=[comp_index], as_=comp)
    p.require(comp, f"{where}: no component at index {comp_index}")
    p.call("getParam", on=comp, args=[param_index], as_=par)
    p.require(par, f"{where}: no parameter at index {param_index}")
    return par


def _emit_crop(p: Program, plan: dict, node: dict, comps, clip: str, where: str) -> None:
    """A crop is a rectangle in SOURCE pixels, turned into Scale and Position here.

    PX = timeline_w / crop_w is the only number either backend needs; Resolve gets Zoom and Pan
    from the same rectangle. Keeping one definition is what makes the two agree exactly.
    """
    geo = nle_ir.crop_to_premiere(node["crop"], node["src_raster"], plan["timeline_raster"])
    c_i, s_i = resolve_chain(comps, "motion", "Scale", where)
    _, p_i = resolve_chain(comps, "motion", "Position", where)

    scale = _chain(p, clip, c_i, s_i, where)
    pos = _chain(p, clip, c_i, p_i, where)
    kfs = p.name("kfscale")
    kfp = p.name("kfpos")
    p.call("createKeyframe", on=scale, args=[geo["scale_percent"]], as_=kfs)
    p.call("createKeyframe", on=pos, args=[{"$pointf": geo["position"]}], as_=kfp)
    p.transaction(f"film-shoot: crop {where}", [
        {"call": "createSetValueAction", "on": scale, "args": [{"$": kfs}, True], "as": "a1"},
        {"addAction": "a1"},
        {"call": "createSetValueAction", "on": pos, "args": [{"$": kfp}, True], "as": "a2"},
        {"addAction": "a2"},
    ])
    # Read BOTH back — a set that returned nothing looks exactly like a set that worked.
    #
    # 🔴 And Position is not optional to check. Two different crop rectangles of the same WIDTH
    # produce the identical Scale, so asserting Scale alone cannot tell them apart: the live check
    # has two crops that both read 177.63, and only Position distinguishes them. Asserting Scale by
    # itself would have been blind to exactly the axis this project has already got wrong once.
    # `getValueAtTime(...).value` on Position is an ARRAY [x, y].
    p.get("TickTime", as_="TT")
    p.get("TIME_ZERO", on="TT", as_="t0")
    got_s = p.name("gotscale")
    p.call("getValueAtTime", on=scale, args=[{"$": "t0"}], as_=got_s)
    p.get("value", on=got_s, as_=f"out_scale_{where}")
    got_p = p.name("gotpos")
    pv = p.name("posval")
    p.call("getValueAtTime", on=pos, args=[{"$": "t0"}], as_=got_p)
    p.get("value", on=got_p, as_=pv)
    p.describe(pv, as_=f"out_pos_{where}")


def _emit_keyframe_set(p: Program, param: str, pts: list[dict], base: int, fps, label: str,
                       where: str) -> None:
    """Build every keyframe, THEN commit them.

    🔴 `createKeyframe(value)` returns a keyframe at the DEFAULT position. Four opacity keyframes
    that never had `.position` set all land at the same instant and each replaces the last — asked
    4, read back 1, reported as a successful build. And `setTemporalInterpolationMode` is ASYNC, so
    it has to happen out here, before the lock, which is why they are fully built first.
    """
    built = []
    for i, pt in enumerate(pts):
        kf = p.name("kf")
        p.call("createKeyframe", on=param, args=[pt["v"]], as_=kf)
        p.set("position", on=kf, value=tick(base + pt["f"], fps))
        shaped = bool(pt.get("shape")) and pt["shape"] != "linear"
        mode = ("Keyframe.INTERPOLATION_MODE_BEZIER" if shaped
                else "Keyframe.INTERPOLATION_MODE_LINEAR")
        p.call("setTemporalInterpolationMode", on=kf, args=[{"$const": mode}])
        built.append(kf)

    steps = [{"call": "createSetTimeVaryingAction", "on": param, "args": [True], "as": "tv"},
             {"addAction": "tv"}]
    for i, kf in enumerate(built):
        steps.append({"call": "createAddKeyframeAction", "on": param,
                      "args": [{"$": kf}], "as": f"k{i}"})
        steps.append({"addAction": f"k{i}"})
    p.transaction(f"film-shoot: {label} {where}", steps)

    times = p.name("times")
    p.call("getKeyframeListAsTickTimes", on=param, as_=times)
    p.describe(times, as_=f"out_{label}_times_{where}")
    p.call("isTimeVarying", on=param, as_=f"out_{label}_tv_{where}")


def _emit_fade(p: Program, plan: dict, node: dict, comps, clip: str, where: str, fps) -> None:
    c_i, p_i = resolve_chain(comps, "opacity", "Opacity", where)
    param = _chain(p, clip, c_i, p_i, where)
    p.call("areKeyframesSupported", on=param, as_=f"out_fade_kfok_{where}")
    _emit_keyframe_set(p, param, fade_points(node, plan), keyframe_base(node, plan), fps,
                       "fade", where)


def _emit_keyframes(p: Program, plan: dict, node: dict, comps, clip: str, where: str, fps) -> None:
    for spec in node["keyframes"]:
        pname = spec["param"]
        c_i, p_i = resolve_chain(comps, "motion", pname, where)
        param = _chain(p, clip, c_i, p_i, where)
        pts = [{"f": pt["frame"], "v": pt["value"], "shape": pt.get("shape")}
               for pt in spec["points"]]
        _emit_keyframe_set(p, param, pts, keyframe_base(node, plan), fps, f"kf_{pname}", where)


def _emit_transition(p: Program, node: dict, clip: str, tr: str, where: str, fps) -> None:
    """⚠️ A centred transition takes HALF its duration from each side of the cut, so both clips
    need handles across it. A clip with no handle silently truncates the transition instead — 12
    frames asked, 6 rendered, on one side only, with the clip length unchanged and the count
    reading 1. Nothing in this API reports it; only a render shows it."""
    before = p.name("tbefore")
    p.call("getTrackItems", on=tr,
           args=[{"$const": "Constants.TrackItemType.TRANSITION"}, False], as_=before)
    p.describe(before, as_=f"out_trans_before_{where}")

    for side in ("in", "out"):
        spec = node["transition"].get(side)
        if not spec:
            continue
        kind = spec.get("type", "cross_dissolve")
        match = TRANSITION_MATCH.get(kind)
        if not match:
            raise BuildError(f"{where}: no Premiere match name for transition type {kind!r}. "
                             f"Known: {sorted(TRANSITION_MATCH)}")
        p.get("TransitionFactory", as_="TF")
        vt = p.name("vt")
        opts = p.name("opts")
        p.call("createVideoTransition", on="TF", args=[match], as_=vt)
        p.require(vt, f"{where}: createVideoTransition({match}) returned nothing")
        p.add(**{"new": "AddTransitionOptions", "as": opts})
        p.call("setApplyToStart", on=opts, args=[side == "in"])
        p.call("setDuration", on=opts, args=[tick(spec["frames"], fps)])
        p.transaction(f"film-shoot: transition {side} {where}", [
            {"call": "createAddVideoTransitionAction", "on": clip,
             "args": [{"$": vt}, {"$": opts}], "as": "act"},
            {"addAction": "act"},
        ])

    after = p.name("tafter")
    p.call("getTrackItems", on=tr,
           args=[{"$const": "Constants.TrackItemType.TRANSITION"}, False], as_=after)
    p.describe(after, as_=f"out_trans_after_{where}")


def check_effects(env: dict, plan: dict) -> list[dict]:
    """Assert every effect became what it was asked to be. This is where 'ok with warnings' dies."""
    out = []
    for _track, node, _n, where in video_nodes(plan):
        if node.get("crop"):
            geo = nle_ir.crop_to_premiere(node["crop"], node["src_raster"],
                                          plan["timeline_raster"])
            got = _value(env, f"out_scale_{where}")
            if got is None or abs(float(got) - geo["scale_percent"]) > 0.01:
                raise BuildError(f"{where}: asked Scale {geo['scale_percent']:.6f}, read back "
                                 f"{got}. A crop that did not take reframes nothing and errors "
                                 f"nowhere.")
            # 🔴 Position too, on BOTH axes. Same-width crops share a Scale, so Scale alone cannot
            # tell two different framings apart — and the vertical axis is the one this project got
            # wrong once (d / crop_h instead of d * PX / timeline_h).
            gp = _number_array(env, f"out_pos_{where}")
            if gp is None or len(gp) != 2:
                raise BuildError(f"{where}: Position did not read back as an [x, y] pair, got "
                                 f"{gp!r}. Without it a crop is only half verified.")
            for axis, g, w in zip("xy", gp, geo["position"]):
                if abs(g - w) > 1e-4:
                    raise BuildError(
                        f"{where}: asked Position {axis}={w:.6f}, read back {g:.6f}. The crop is "
                        f"the right SIZE and in the wrong PLACE, which no frame count or duration "
                        f"check can see.")
            out.append({"where": where, "scale": got, "position": gp})

        if node.get("fade"):
            want = len(fade_points(node, plan))
            _check_keyframes(env, where, "fade", want)
            if _value(env, f"out_fade_kfok_{where}") is False:
                raise BuildError(f"{where}: Opacity reports keyframes unsupported.")
            out.append({"where": where, "fadeKeyframes": want})

        for spec in node.get("keyframes", []) or []:
            _check_keyframes(env, where, f"kf_{spec['param']}", len(spec["points"]))
            out.append({"where": where, "param": spec["param"], "points": len(spec["points"])})

        if node.get("transition"):
            b = _described_length(env, f"out_trans_before_{where}")
            a = _described_length(env, f"out_trans_after_{where}")
            asked = len([s for s in ("in", "out") if node["transition"].get(s)])
            if a - b != asked:
                raise BuildError(
                    f"{where}: asked for {asked} transition(s) and the track gained {a - b}. "
                    f"createAddVideoTransitionAction returns without complaint, so the count is "
                    f"the only thing that knows a transition exists — and it is the ONLY thing it "
                    f"knows. Duration and side need a render to verify.")
            out.append({"where": where, "transitionsAdded": a - b})
    return out


def _check_keyframes(env: dict, where: str, label: str, want: int) -> None:
    got = _described_length(env, f"out_{label}_times_{where}")
    if got != want:
        raise BuildError(
            f"{where}: asked for {want} {label} keyframes, read back {got}. `createKeyframe` "
            f"returns a keyframe at the DEFAULT position, so keyframes whose .position was not set "
            f"all land at one instant and each replaces the last. A fade with one keyframe does "
            f"not fade.")
    if _value(env, f"out_{label}_tv_{where}") is not True:
        raise BuildError(f"{where}: {label} parameter is not timeVarying, so the keyframes are a "
                         f"static value with extra steps.")


# --------------------------------------------------------------------------------------------
# phase 6 — audio
# --------------------------------------------------------------------------------------------

def emit_audio(plan: dict, media_index: dict[str, int]) -> Program:
    fps = plan["fps"]
    p = Program()
    for track, node, _n, where in audio_nodes(plan):
        a_index = track_index_for(track, plan)
        idx = media_index[node["media"]]
        root = p.name("root")
        items = p.name("items")
        item = p.name("item")
        p.call("getRootItem", on="project", as_=root)
        p.call("getItems", on=root, as_=items)
        p.get(str(idx), on=items, as_=item)
        p.require(item, f"{where}: audio media not found")
        p.transaction(f"film-shoot: audio in/out {where}", [
            {"cast": "ClipProjectItem", "on": item, "as": "clip"},
            {"call": "createSetInOutPointsAction", "on": "clip",
             "args": [tick(node["src_in"], fps),
                      tick(node["src_in"] + node["len"], fps)], "as": "act"},
            {"addAction": "act"},
        ])
        seq = p.name("seq")
        ed = p.name("ed")
        p.active_sequence(seq)
        p.get("SequenceEditor", as_="SE")
        p.call("getEditor", on="SE", args=[{"$": seq}], as_=ed)
        p.transaction(f"film-shoot: audio place {where}", [
            {"call": "createOverwriteItemAction", "on": ed,
             "args": [{"$": item}, tick(node["rec"], fps), a_index, a_index], "as": "act"},
            {"addAction": "act"},
        ])
    return p


def wanted_audio_tracks(plan: dict) -> list[int]:
    return sorted({track_index_for(t, plan) for t in plan["tracks"] if t.get("kind") == "audio"})


# --------------------------------------------------------------------------------------------
# reading a program's results
# --------------------------------------------------------------------------------------------

def _value(env: dict, name: str):
    """A named result's plain value, out of the interpreter's describeValue envelope."""
    d = env.get(name)
    if d is None:
        return None
    if isinstance(d, dict):
        if "value" in d:
            return d["value"]
        if d.get("type") == "null":
            return None
    return d


def _number_array(env: dict, name: str):
    """A described array of numbers, as plain floats. Position reads back as [x, y]."""
    d = env.get(name) or {}
    if isinstance(d, dict) and d.get("type") == "array":
        out = []
        for item in d.get("items") or []:
            if not isinstance(item, dict) or "value" not in item:
                return None
            out.append(float(item["value"]))
        return out
    return None


def _described_length(env: dict, name: str):
    d = env.get(name) or {}
    if isinstance(d, dict) and "value" in d and isinstance(d["value"], dict):
        d = d["value"]
    return d.get("length") if isinstance(d, dict) else None


# --------------------------------------------------------------------------------------------
# the driver
# --------------------------------------------------------------------------------------------

def run_build(bridge, plan: dict, verbose: bool = True) -> dict:
    """Build `plan` through the `program` op. Raises BuildError on anything that did not take."""
    def send(label: str, prog: Program) -> dict:
        if verbose:
            print(f"  {label}: {len(prog.steps)} steps")
        res = bridge.send({"op": "program", "steps": prog.steps})
        if not res.get("ok"):
            raise BuildError(f"{label} failed: {res.get('error')}\n"
                             f"  trace tail: {json.dumps(res.get('trace', [])[-3:])[:600]}")
        return res.get("env", {})

    report: dict = {"ok": True, "phases": []}

    # ⚠️ Pre-flight. `activeProject()` runs at the START of every program, so a closed project turns
    # into "no active project" reported against whichever phase happened to be next — which reads
    # like a bug in that phase. It is worth one cheap round trip to say what is actually wrong.
    # (This is not hypothetical: Premiere crashed mid-build once and the failure surfaced as an
    # error on "place clips", 66 steps in.)
    ping = bridge.send({"op": "call", "chain": [
        {"get": "Project"}, {"call": "getActiveProject", "describe": True}]})
    final = (ping.get("final") or {}) if ping.get("ok") else {}
    if final.get("type") == "null":
        raise BuildError(
            "Premiere has no open project, so there is nothing to build into. Open or create one "
            "and run this again. (If a build just died, check whether the application is still "
            "running — this is what a crash looks like from out here.)")

    env = send("sequence + import", emit_sequence(plan))
    report["sequence"] = check_sequence(env, plan)
    n_items = _described_length(env, "out_items") or 0
    report["phases"].append({"sequence": report["sequence"], "projectItems": n_items})

    env = send("index media", emit_media_index(n_items))
    media_index = resolve_media(env, n_items, plan)
    report["mediaIndex"] = media_index

    prog = emit_placement(plan, media_index)
    env = send("place clips", prog)
    report["placed"] = check_placement(env, plan)

    needs_effects = any(n.get("crop") or n.get("fade") or n.get("keyframes")
                        or n.get("transition") for _t, n, _i, _w in video_nodes(plan))
    if needs_effects:
        if verbose:
            print("  read back component chains")
        seen = bridge.send({"op": "readback", "with_components": True})
        chains = _chains_by_where(seen, plan, prog.placed_at)     # type: ignore[attr-defined]
        env = send("effects", emit_effects(plan, prog.placed_at, chains))  # type: ignore
        report["effects"] = check_effects(env, plan)

    audio = list(audio_nodes(plan))
    if audio:
        send("place audio", emit_audio(plan, media_index))

    keep = wanted_audio_tracks(plan)
    strip = bridge.send({"op": "strip_audio", "keep": keep})
    if not strip.get("ok"):
        raise BuildError(f"strip_audio failed: {strip.get('error')}")
    if strip.get("straysRemaining"):
        raise BuildError(
            f"{strip['straysRemaining']} audio item(s) the plan never asked for are still on the "
            f"timeline after trying to remove {strip.get('strays')}. Refusing to call this build "
            f"done — a render would carry camera audio, which measures as a programme roughly 15 LU "
            f"quieter than its own mix and is easy to miss.")
    report["audio"] = {"requestedTracks": keep, "strays": strip.get("strays"),
                       "straysRemaining": strip.get("straysRemaining")}

    save = Program()
    save.call("save", on="project")
    send("save", save)
    return report


def _chains_by_where(seen: dict, plan: dict, placed_at) -> dict[str, list[dict]]:
    """Map each node's `where` to its clip's component chain, from a readback."""
    by_track: dict[int, list[dict]] = {}
    for t in seen.get("tracks", []):
        by_track[t.get("askedIndex")] = t.get("nodes") or []
    out: dict[str, list[dict]] = {}
    for where, v_index, ordinal in placed_at:
        nodes = by_track.get(v_index) or []
        if ordinal < len(nodes):
            out[where] = nodes[ordinal].get("components") or []
    return out


# --------------------------------------------------------------------------------------------
# selftest — the SHAPE of every phase, with no Premiere anywhere
# --------------------------------------------------------------------------------------------

def _example_plan() -> dict:
    return {
        "name": "prog-selftest", "fps": [30000, 1001], "timeline_raster": [1080, 1920],
        "track_index_base": 0, "keyframe_time_base": "clip",
        "media": {"a": "/tmp/a.mov", "b": "/tmp/b.mov", "mix": "/tmp/mix.wav"},
        "baked": [{"what": "nothing", "why": "selftest"}],
        "tracks": [
            {"id": "V1", "kind": "video", "index": 1, "nodes": [
                {"kind": "clip", "media": "a", "rec": 0, "src_in": 0, "len": 90,
                 "fade": {"in": {"frames": 12, "shape": "linear"},
                          "out": {"frames": 18, "shape": "ease"}}},
                {"kind": "clip", "media": "b", "rec": 90, "src_in": 10, "len": 70,
                 "src_raster": [3840, 2160], "crop": [0, 0, 1215, 2160],
                 "transition": {"in": {"frames": 12, "type": "cross_dissolve"}}},
            ]},
            {"id": "A1", "kind": "audio", "index": 1, "nodes": [
                {"kind": "clip", "media": "mix", "rec": 0, "src_in": 0, "len": 160},
            ]},
        ],
    }


SYNC_IN_TX = {"createSetSettingsAction", "createSetInOutPointsAction", "createOverwriteItemAction",
              "createSetDisabledAction", "createSetValueAction", "createAddKeyframeAction",
              "createSetTimeVaryingAction", "createAppendComponentAction",
              "createRemoveItemsAction", "createAddVideoTransitionAction",
              "createRemoveVideoTransitionAction", "getEditor"}

VERBS = {"call", "get", "set", "cast", "transaction", "addAction", "require", "describe", "new"}


def selftest() -> int:
    failures = []

    def check(name, cond, detail=""):
        if not cond:
            failures.append(name)
        print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"   {detail}" if detail and not cond
                                                          else ""))

    plan = _example_plan()
    media_index = {"a": 0, "b": 1, "mix": 2}
    chains = {
        "V1#0": [{"index": 0, "matchName": "AE.ADBE Opacity", "displayName": "Opacity",
                  "params": [{"index": 0, "displayName": "Opacity"},
                             {"index": 1, "displayName": "Blend Mode"}]}],
        "V1#1": [{"index": 1, "matchName": "AE.ADBE Motion", "displayName": "Motion",
                  "params": [{"index": 0, "displayName": "Position"},
                             {"index": 1, "displayName": "Scale"}]}],
    }
    place = emit_placement(plan, media_index)
    phases = {
        "sequence": emit_sequence(plan),
        "index": emit_media_index(3),
        "place": place,
        "effects": emit_effects(plan, place.placed_at, chains),   # type: ignore[attr-defined]
        "audio": emit_audio(plan, media_index),
    }

    for label, prog in phases.items():
        steps = prog.steps
        check(f"{label} emits steps", bool(steps), str(len(steps)))
        check(f"{label} steps each have exactly one verb",
              all(len(VERBS & set(s)) == 1 for s in steps),
              str([s for s in steps if len(VERBS & set(s)) != 1][:2]))
        check(f"{label} is JSON-serialisable", json.dumps(steps) is not None)

        # 🔴 the rule the interpreter enforces: nothing inside a transaction may be async.
        for s in steps:
            if "transaction" not in s:
                continue
            check(f"{label}: no nested transaction",
                  all("transaction" not in sub for sub in s["steps"]))
            for sub in s["steps"]:
                if "call" in sub:
                    check(f"{label}: in-transaction call {sub['call']!r} is known-sync",
                          sub["call"] in SYNC_IN_TX,
                          "an async call inside a transaction commits an EMPTY transaction")

    seq_steps = phases["sequence"].steps
    check("sets the frame rate", any(s.get("call") == "setVideoFrameRate" for s in seq_steps))
    check("sets the raster too", any(s.get("call") == "setVideoFrameRect" for s in seq_steps),
          "a vertical programme in a 1920x1080 sequence is letterboxed silently")
    check("reads the rate back", any(s.get("call") == "getVideoFrameRate" for s in seq_steps))
    check("reads the raster back", any(s.get("call") == "getVideoFrameRect" for s in seq_steps))
    imp = [s for s in seq_steps if s.get("call") == "importFiles"]
    check("importFiles is emitted once", len(imp) == 1)
    if imp:
        check("importFiles passes asNumberedStills=False", imp[0]["args"][3] is False,
              "numbered stills in one folder import as ONE image sequence")

    # --- placement: in/out must be set immediately before the placement it belongs to
    p_steps = place.steps
    order = [s["transaction"] for s in p_steps if "transaction" in s]
    check("every placement is preceded by its own in/out",
          order == ["film-shoot: in/out V1#0", "film-shoot: place V1#0",
                    "film-shoot: in/out V1#1", "film-shoot: place V1#1"], str(order))

    # 🔴 the editor must be re-acquired per clip: a handle dies when the project mutates
    check("the editor is re-acquired for every placement",
          len([s for s in p_steps if s.get("call") == "getEditor"]) == 2,
          "one editor held across placements throws 'the script object is no longer valid'")
    check("the project item is re-fetched for every placement",
          len([s for s in p_steps if s.get("call") == "getItems"]) >= 2)

    # --- ticks: a frame reference must carry its own fps
    t = tick(12, [30000, 1001])
    check("a tick carries its fps", t["$tick"] == 12 and t["fps"] == [30000, 1001])
    check("29.97 divides the tick base exactly", ticks_per_frame([30000, 1001]) == 8475667200)

    # --- fades: the shape goes on BOTH ends of a ramp
    pts = fade_points(plan["tracks"][0]["nodes"][0], plan)
    check("a fade in and out is four keyframes", len(pts) == 4, str(pts))
    check("both ends of the out-ramp carry the shape",
          pts[2]["shape"] == "ease" and pts[3]["shape"] == "ease",
          "a segment takes its shape from the keyframe it LEAVES FROM")
    eff = phases["effects"].steps
    modes = [s["args"][0]["$const"] for s in eff
             if s.get("call") == "setTemporalInterpolationMode"]
    check("every keyframe gets an explicit interpolation mode", len(modes) == 4, str(modes))
    check("the eased pair is BEZIER on both ends",
          sum("BEZIER" in m for m in modes) == 2, str(modes))
    check("every keyframe gets an explicit position",
          len([s for s in eff if s.get("set") == "position"]) == 4,
          "createKeyframe returns one at the DEFAULT position: 4 asked, 1 read back")

    # --- crop geometry comes from nle_ir, not from a second copy of the maths
    n1 = plan["tracks"][0]["nodes"][1]
    geo = nle_ir.crop_to_premiere(n1["crop"], n1["src_raster"], plan["timeline_raster"])
    vals = [s["args"][0] for s in eff
            if s.get("call") == "createKeyframe" and isinstance(s["args"][0], (int, float))]
    check("the crop's Scale comes from nle_ir", any(abs(v - geo["scale_percent"]) < 1e-9
                                                    for v in vals), str(vals))

    # --- ambiguity must be refused rather than resolved by picking the first
    try:
        resolve_chain([{"index": 0, "matchName": "AE.ADBE Opacity",
                        "params": [{"index": 0, "displayName": "Blend Mode"},
                                   {"index": 3, "displayName": "Blend Mode"}]}],
                      "opacity", "Blend Mode", "V1#0")
        check("an ambiguous parameter name is refused", False, "it returned a choice")
    except BuildError as e:
        check("an ambiguous parameter name is refused", "ambiguous" in str(e))

    # --- an unimplemented node key must stop the build before a step is sent
    bad = json.loads(json.dumps(plan))
    bad["tracks"][0]["nodes"][0]["reverse"] = True
    try:
        emit_placement(bad, media_index)
        check("an unhandled node key stops the build", False, "it emitted steps anyway")
    except BuildError as e:
        check("an unhandled node key stops the build", "reverse" in str(e))

    # 🔴 and a key the PANEL lists as "handled" but implements nothing for must still be refused.
    #    `speed` sits in the panel's HANDLED_NODE_KEYS with no setter anywhere in the API, so a
    #    plan carrying it would have built silently at normal speed.
    bad = json.loads(json.dumps(plan))
    bad["tracks"][0]["nodes"][0]["speed"] = 0.5
    try:
        emit_placement(bad, media_index)
        check("speed is refused even though the panel 'handles' it", False, "it emitted steps")
    except BuildError as e:
        check("speed is refused even though the panel 'handles' it", "no setter" in str(e))

    # --- and a grade must be refused by name, because Premiere has no scripted colour at all
    bad = json.loads(json.dumps(plan))
    bad["tracks"][0]["nodes"][0]["grade"] = {"lut": "/tmp/x.cube"}
    try:
        emit_placement(bad, media_index)
        check("a grade is refused with the reason", False, "it emitted steps anyway")
    except BuildError as e:
        check("a grade is refused with the reason", "no scripted colour" in str(e).lower())

    # --- the assertions: each must FAIL on a read-back that did not take
    tpf = ticks_per_frame(plan["fps"])
    good_env = {f"out_start_V1#{i}": {"type": "number", "value": n["rec"] * tpf}
                for i, n in enumerate(plan["tracks"][0]["nodes"])}
    good_env.update({f"out_dur_V1#{i}": {"type": "number", "value": n["len"] * tpf}
                     for i, n in enumerate(plan["tracks"][0]["nodes"])})
    check("a correct placement read-back passes", bool(check_placement(good_env, plan)))

    slid = dict(good_env)
    slid["out_start_V1#1"] = {"type": "number", "value": 91 * tpf}
    try:
        check_placement(slid, plan)
        check("a clip that slid one frame is refused", False, "it passed")
    except BuildError as e:
        check("a clip that slid one frame is refused", "SLID" in str(e))

    short = dict(good_env)
    short["out_dur_V1#1"] = {"type": "number", "value": 58 * tpf}
    try:
        check_placement(short, plan)
        check("a clip shortened by a transition is refused", False, "it passed")
    except BuildError as e:
        check("a clip shortened by a transition is refused", "frames" in str(e))

    eff_env = {
        "out_scale_V1#1": {"type": "number", "value": geo["scale_percent"]},
        "out_pos_V1#1": {"type": "array", "length": 2,
                         "items": [{"type": "number", "value": geo["position"][0]},
                                   {"type": "number", "value": geo["position"][1]}]},
        "out_fade_kfok_V1#0": {"type": "boolean", "value": True},
        "out_fade_times_V1#0": {"type": "array", "length": 4},
        "out_fade_tv_V1#0": {"type": "boolean", "value": True},
        "out_trans_before_V1#1": {"type": "array", "length": 0},
        "out_trans_after_V1#1": {"type": "array", "length": 1},
    }
    check("a correct effects read-back passes", bool(check_effects(eff_env, plan)))

    one_kf = dict(eff_env)
    one_kf["out_fade_times_V1#0"] = {"type": "array", "length": 1}
    try:
        check_effects(one_kf, plan)
        check("four keyframes asked and one read back is refused", False, "it passed")
    except BuildError as e:
        check("four keyframes asked and one read back is refused", "does not fade" in str(e))

    static = dict(eff_env)
    static["out_fade_tv_V1#0"] = {"type": "boolean", "value": False}
    try:
        check_effects(static, plan)
        check("keyframes that are not timeVarying are refused", False, "it passed")
    except BuildError as e:
        check("keyframes that are not timeVarying are refused", "static value" in str(e))

    no_trans = dict(eff_env)
    no_trans["out_trans_after_V1#1"] = {"type": "array", "length": 0}
    try:
        check_effects(no_trans, plan)
        check("a transition that was not added is refused", False, "it passed")
    except BuildError as e:
        check("a transition that was not added is refused", "gained 0" in str(e))

    wrong_crop = dict(eff_env)
    wrong_crop["out_scale_V1#1"] = {"type": "number", "value": 100.0}
    try:
        check_effects(wrong_crop, plan)
        check("a crop that did not take is refused", False, "it passed")
    except BuildError as e:
        check("a crop that did not take is refused", "did not take" in str(e))

    # 🔴 the right SIZE in the wrong PLACE. Two crops of equal width share a Scale, so this is the
    #    case a Scale-only assertion cannot see — and the vertical axis is one this project has
    #    already got wrong (d / crop_h instead of d * PX / timeline_h).
    for axis, bad_pos in (("x", [0.5, geo["position"][1]]),
                          ("y", [geo["position"][0], 0.5 if geo["position"][1] != 0.5 else 0.25])):
        moved = dict(eff_env)
        moved["out_pos_V1#1"] = {"type": "array", "length": 2,
                                 "items": [{"type": "number", "value": bad_pos[0]},
                                           {"type": "number", "value": bad_pos[1]}]}
        try:
            check_effects(moved, plan)
            check(f"a crop offset on {axis} is refused", False, "it passed")
        except BuildError as e:
            check(f"a crop offset on {axis} is refused", "wrong PLACE" in str(e))

    no_pos = dict(eff_env)
    no_pos["out_pos_V1#1"] = {"type": "null"}
    try:
        check_effects(no_pos, plan)
        check("a Position that did not read back is refused", False, "it passed")
    except BuildError as e:
        check("a Position that did not read back is refused", "half verified" in str(e))

    # --- media that did not import must be named, not guessed past
    try:
        resolve_media({"out_name_0": {"type": "string", "value": "a.mov"}}, 1, plan)
        check("missing media is refused by name", False, "it passed")
    except BuildError as e:
        check("missing media is refused by name", "b.mov" in str(e))

    # --- and the plan must still be gated before any of this is sent
    errs = nle_ir.validate(_example_plan())
    check("the example plan validates", not errs, str(errs))

    print(f"\n{'PASS' if not failures else 'FAIL'}: {len(failures)} failure(s)")
    return 1 if failures else 0


FIXTURES = os.path.join(HERE, "fixtures", "premiere")


def live_plan() -> dict:
    """A plan covering the three paths `verify_premiere_backend.py` does NOT: a CROP, a second
    video track, and AUDIO.

    The capability check proves fades, animation, disable and transitions. It has no crop, one video
    track and no audio — so running it through this emitter leaves the crop geometry, the multi-track
    index arithmetic and the audio placement/sweep unexercised. This covers them.

    A VERTICAL timeline from LANDSCAPE sources on purpose: that is the reframe the real programme
    does, it makes the crop do visible work, and it is the case a default 1920x1080 sequence
    letterboxes silently.
    """
    m = {k: os.path.join(FIXTURES, f"cap_{k}.mov") for k in ("a", "b")}
    m["tone"] = os.path.join(FIXTURES, "cap_tone.wav")
    return {
        "name": "program-live-check", "fps": [30000, 1001], "timeline_raster": [1080, 1920],
        "track_index_base": 0, "keyframe_time_base": "clip", "media": m,
        "baked": [{"what": "nothing", "why": "synthetic clips; this checks the emitter, not a cut"}],
        "tracks": [
            {"id": "V1", "kind": "video", "index": 1, "name": "PICTURE", "nodes": [
                # a 0.5625-aspect window out of a 1920x1080 source, offset from centre so a
                # Position error cannot hide behind a symmetrical crop
                {"kind": "clip", "media": "a", "rec": 0, "len": 60, "src_in": 0,
                 "src_raster": [1920, 1080], "crop": [520, 0, 608, 1080]},
                # 🔴 offset on BOTH axes, and a crop shorter than the source, so Position.y is not
                # 0.5. A full-height crop is centred vertically whatever the maths does, which
                # means a y-axis error reads back correct — and the y axis is the one this project
                # has actually got wrong (d / crop_h instead of d * PX / timeline_h).
                {"kind": "clip", "media": "b", "rec": 60, "len": 60, "src_in": 0,
                 "src_raster": [1920, 1080], "crop": [520, 40, 495, 880]},
            ]},
            {"id": "V2", "kind": "video", "index": 2, "name": "OVERLAY", "nodes": [
                {"kind": "clip", "media": "b", "rec": 0, "len": 120, "src_in": 0,
                 "fade": {"in": {"frames": 10, "shape": "linear"}}},
            ]},
            {"id": "A1", "kind": "audio", "index": 1, "name": "MIX", "nodes": [
                {"kind": "clip", "media": "tone", "rec": 0, "len": 60, "src_in": 0},
            ]},
        ],
    }


def verify_live(bridge) -> int:
    """Build `live_plan` and let run_build's own assertions be the verdict.

    🔴 `run_build` RAISES on anything that did not take — a crop whose Scale read back wrong, a clip
    that slid, a keyframe count short of what was asked, stray camera audio still on the timeline.
    So a clean run is the verification, and there is nothing extra to trust here.
    """
    plan = live_plan()
    missing = [p for p in plan["media"].values() if not os.path.exists(p)]
    if missing:
        print("missing fixture media:")
        for p in missing:
            print("  " + p)
        print("Run: python3 verify_premiere_backend.py --make-media")
        return 2

    errs = nle_ir.validate(plan) + nle_ir.capability_gate(plan, "premiere")
    if errs:
        print("the live plan does not validate:")
        for e in errs:
            print("  - " + e)
        return 1

    print("building the live check (crop + second video track + audio) through the emitter...")
    try:
        report = run_build(bridge, plan)
    except BuildError as e:
        print(f"\n🔴 LIVE CHECK FAILED\n{e}")
        return 1

    print("\nwhat the build read back:")
    print(f"  sequence     {report['sequence']['raster']} raster, "
          f"{report['sequence']['ticksPerFrame']} ticks/frame")
    for row in report["placed"]:
        print(f"  placed       {row['where']} at {row['startTicks']} ticks, "
              f"{row['durationTicks']} long")
    for row in report.get("effects", []):
        print(f"  effect       {row}")
    print(f"  audio        {report['audio']}")

    # the crop numbers are asserted inside run_build; print them so the run is readable
    for _t, node, _n, where in video_nodes(plan):
        if node.get("crop"):
            geo = nle_ir.crop_to_premiere(node["crop"], node["src_raster"],
                                          plan["timeline_raster"])
            print(f"  crop {where}    asked scale {geo['scale_percent']:.6f}, "
                  f"position {[round(x, 6) for x in geo['position']]}")

    print("\n✅ the emitter built crop, a second video track and audio, and every read-back "
          "matched what the plan asked for.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--plan")
    ap.add_argument("--verify-live", action="store_true",
                    help="build a synthetic plan covering crop, a second video track and audio")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--allow-unverified", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    if a.verify_live:
        import premiere_bridge as pb
        return verify_live(pb.Bridge(timeout=1800))

    if not a.plan:
        ap.error("--plan is required")

    with open(a.plan) as fh:
        plan = json.load(fh)
    errs = nle_ir.validate(plan) + nle_ir.capability_gate(
        plan, "premiere", allow_unverified=a.allow_unverified)
    if errs:
        print("plan rejected:")
        for e in errs:
            print("  - " + e)
        return 1

    if not a.run:
        media_index = {k: i for i, k in enumerate(plan["media"])}
        place = emit_placement(plan, media_index)
        for label, prog in (("sequence", emit_sequence(plan)),
                            ("index", emit_media_index(len(plan["media"]))),
                            ("place", place),
                            ("audio", emit_audio(plan, media_index))):
            print(f"--- {label}: {len(prog.steps)} steps")
        print("(effects need a read-back to resolve component indices; use --run)")
        return 0

    import premiere_bridge as pb
    b = pb.Bridge(timeout=1800)
    try:
        report = run_build(b, plan)
    except BuildError as e:
        print(f"\nBUILD FAILED\n{e}")
        return 1
    print("\n" + json.dumps(report, indent=2)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())

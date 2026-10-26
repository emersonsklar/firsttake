#!/usr/bin/env python3
"""capcut_draft.py — build the plan as a CapCut draft. Free, and no scripting API needed.

    python3 capcut_draft.py --probe                            # DO THIS FIRST, every CapCut update
    python3 capcut_draft.py --build plan.json
    python3 capcut_draft.py --readback <draft name or folder> --plan plan.json
    python3 capcut_draft.py --check-export exported.mp4 --plan plan.json
    python3 capcut_draft.py --selftest                         # no CapCut, no media

The fourth backend beside Resolve, Premiere and ffmpeg, building the same IR. CapCut has no
scripting API at all — no module, no plugin runtime, no CLI. What it does have is a project format
on disk: every draft is a folder holding a `draft_info.json` that describes the whole timeline. So
this backend writes that file, and CapCut opens the result like any draft it made itself.

The format was learned from what the application writes and from two open-source projects that
reverse-engineered it (CapCutAPI and pyJianYingDraft). None of their code is used here; it is an
independent writer of the same file, built only from the IR.

## 🔴 The format is undocumented, and it belongs to someone else

That is the whole risk of this backend, and the design follows from it:

- **A capability is promoted only when CapCut's own export was MEASURED doing it** — see
  `nle_ir.CAPABILITIES["capcut"]` for the evidence on each. Anything new starts UNVERIFIED and
  blocks until `--allow-unverified` says out loud that the build is unproven.
- **`--probe` before trusting anything.** It finds the app, its version and its drafts folder, and
  reads the drafts CapCut itself has saved, to learn whether this version still writes plain JSON.
  Some CapCut and JianYing builds have shipped ENCRYPTED drafts; if yours does, this backend cannot
  read anything back and says so rather than guessing.
- **`--readback` reads what CapCut KEPT, not what was written.** Open the draft in CapCut, let it
  save, close it, then read it back. A draft CapCut has never saved can only show you your own
  file, and `--readback` says so instead of reporting a pass.

## 🔴 Three traps, all in the format

1. **Time is in MICROSECONDS, and CapCut keeps only whole frame rates.** A 30 fps frame is
   33333.33 us, so every boundary is truncated — CapCut's own convention, measured — from its
   ABSOLUTE frame number, never by adding durations, so a long timeline cannot drift. A clip's
   source and timeline spans can then differ by 1 us at speed 1.0; CapCut writes that itself.
   A 29.97 draft is re-timed to 30 on save, so the gate refuses fractional rates.
2. **The bottom video track is the main track, and it is magnetic.** Its clips are forced to start
   at 0 and close up. The gate refuses a plan with a gap there (`main_track_gap`) rather than build
   one that slides and still tiles.
3. **A video clip carries its own camera audio.** The IR lays the mastered mix explicitly, so every
   picture clip is written at volume 0 — otherwise the camera sound plays under the mix, which is
   the same trap Premiere has.

## What it does not do

- **No scripted export.** CapCut renders only from its own Export dialog. Export by hand, then
  `--check-export` measures the file against the plan: frames, raster, rate.
- **No colour, no transitions, no keyframes, no retime.** The gate refuses each by name. Bake the
  grade into the media as for Premiere and ffmpeg; CapCut's transitions are store effects that
  must already be downloaded on the machine, so they do not travel in a plan.

## Windows

Text files are written UTF-8, paths go into the draft as absolute paths with forward slashes kept
as the OS gives them, and the default drafts folder is found under %LOCALAPPDATA%.
"""

from __future__ import annotations

import argparse
import copy
import glob
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import nle_ir                                                          # noqa: E402

BACKEND = "capcut"
SEC = 1_000_000

DRAFT_FILE = "draft_info.json"     # CapCut. JianYing, the mainland edition, calls it draft_content
META_FILE = "draft_meta_info.json"
MARKER = ".firsttake-draft.json"   # ours: what was written, so a readback knows if CapCut saved

# Written into the draft so CapCut treats it as a known version. These are the values CapCutAPI's
# drafts carry and CapCut opens; `--probe` prints what YOUR CapCut writes, and if it differs these
# are the two numbers to update.
DRAFT_VERSION = 360000
DRAFT_NEW_VERSION = "138.0.0"
DRAFT_APP_VERSION = "6.5.0"

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".heic"}
PHOTO_DURATION_US = 10_800_000_000   # what CapCut gives a still: three hours

# Tolerance on the floats CapCut stores. They are written from doubles and read back as doubles;
# anything past 1e-6 of a half-canvas is a change somebody made, not a rounding.
GEOM_TOL = 1e-6


class CapCutError(RuntimeError):
    """The plan cannot become a draft, or the draft is not the plan. The message says which."""


# --------------------------------------------------------------------------------------------
# time — frames in the plan, microseconds in the draft
# --------------------------------------------------------------------------------------------

def frames_to_us(frames: int, fps) -> int:
    """Frame number -> microseconds, TRUNCATED, in exact integer arithmetic.

    Truncated because that is what CapCut does — measured on 9.6.0, which saved frame 80 at 30 fps
    as 2666666, not 2666667. Writing its own convention means a draft CapCut re-saves comes back
    with the same numbers, so any difference a readback finds is a change, not a rounding style.

    🔴 Always pass an ABSOLUTE frame number. Converting a duration and adding it to a converted
    start accumulates error per clip; converting both ends from frame numbers cannot drift, and two
    clips that meet in frames meet exactly in microseconds.
    """
    num, den = int(fps[0]), int(fps[1])
    return int(frames) * den * SEC // num


def us_to_frames(us: int, fps) -> int:
    """Microseconds -> the nearest frame number. The inverse of frames_to_us on every frame."""
    num, den = int(fps[0]), int(fps[1])
    return (2 * int(us) * num + den * SEC) // (2 * den * SEC)


def node_times(node: dict, fps) -> dict:
    """The two timeranges a node becomes, both in microseconds.

    Both spans are converted from their own frame numbers, independently — again CapCut's own
    convention, measured: it saved a 40-frame clip as 1333333 us on the timeline and 1333334 us of
    source, at speed 1.0. So a 1 us difference between the two is truncation, not a retime; a
    retime is a speed that is not 1, or a source that ends on the wrong FRAME.
    """
    rec, ln, src = int(node["rec"]), int(node["len"]), int(node["src_in"])
    start, s_start = frames_to_us(rec, fps), frames_to_us(src, fps)
    return {"target": {"start": start, "duration": frames_to_us(rec + ln, fps) - start},
            "source": {"start": s_start, "duration": frames_to_us(src + ln, fps) - s_start}}


# --------------------------------------------------------------------------------------------
# media
# --------------------------------------------------------------------------------------------

def ffprobe_media(path: str) -> dict:
    """Raster and duration of one file — what a CapCut material has to declare."""
    if not os.path.isfile(path):
        raise CapCutError(f"media is not on disk: {path}")
    if os.path.splitext(path)[1].lower() in IMAGE_EXT:
        kind = "photo"
    else:
        kind = "video"
    tool = shutil.which("ffprobe")
    if not tool:
        raise CapCutError("ffprobe is not on PATH; a CapCut material must declare its raster and "
                          "duration. `python3 scripts/doctor.py` will say how to install it.")
    r = subprocess.run([tool, "-v", "error", "-show_entries",
                        "stream=codec_type,codec_name,width,height", "-show_entries", "format=duration",
                        "-of", "json", path], capture_output=True, text=True)
    if r.returncode != 0:
        raise CapCutError(f"ffprobe could not read {path}: {(r.stderr or '').strip()[:300]}")
    d = json.loads(r.stdout)
    streams = d.get("streams") or []
    vid = next((s for s in streams if s.get("codec_type") == "video"), None)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    secs = float((d.get("format") or {}).get("duration") or 0.0)
    return {
        "kind": kind if vid else "audio",
        "width": int(vid.get("width") or 0) if vid else 0,
        "height": int(vid.get("height") or 0) if vid else 0,
        "duration_us": PHOTO_DURATION_US if kind == "photo" else int(round(secs * SEC)),
        "has_audio": has_audio,
        "codec": (vid.get("codec_name") or "") if vid else "",
    }


# --------------------------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------------------------

def gate(plan: dict, allow_unverified: bool = False) -> tuple[list[str], list[str]]:
    """(errors, notes). The render block is a NOTE here, not an error: there is nothing in a draft
    to carry it, and the export it describes is the manual step `--check-export` measures."""
    notes = []
    errs = nle_ir.validate(plan)
    gated = dict(plan)
    if gated.pop("render", None):
        notes.append("the plan's render block is not part of a CapCut draft: export by hand from "
                     "CapCut, then run --check-export on the file.")
    errs += nle_ir.capability_gate(gated, BACKEND, allow_unverified=allow_unverified)
    return errs, notes


def media_checks(plan: dict, info: dict) -> list[str]:
    """Refusals that need the media measured, which the IR alone cannot know."""
    errs = []
    fps = plan["fps"]
    tw, th = plan["timeline_raster"]
    for t in plan["tracks"]:
        for n, node in enumerate(t.get("nodes", [])):
            where = "track %r node %d" % (t.get("id"), n)
            m = info[node["media"]]
            end_us = frames_to_us(int(node["src_in"]) + int(node["len"]), fps)
            if m["kind"] != "photo" and end_us > m["duration_us"] + frames_to_us(1, fps) // 2:
                errs.append("%s: needs the source up to %.3f s and %s is %.3f s long. CapCut would "
                            "end the clip early and the timeline would still tile."
                            % (where, end_us / SEC, node["media"], m["duration_us"] / SEC))
            if t.get("kind") == "video":
                if m["kind"] == "audio":
                    errs.append("%s: %s has no picture" % (where, node["media"]))
                    continue
                if node.get("crop") is None and m["width"] and m["height"]:
                    want, got = tw / float(th), m["width"] / float(m["height"])
                    if abs(got - want) / want > 0.005:
                        errs.append(
                            "%s: %s is %dx%d in a %dx%d timeline with no crop. CapCut FITS it and "
                            "letterboxes; ffmpeg would stretch it. Give the node a crop so every "
                            "backend frames it the same way."
                            % (where, node["media"], m["width"], m["height"], tw, th))
                # 🔴 MEASURED on CapCut 9.6.0, twice: a ProRes 4444 graphic draws DIM — white written
                # at 235 drew at 191, untagged and tagged bt709 alike — while a PNG still beside it
                # drew at exactly 235. Placement and opacity were right, so nothing looks broken
                # except the colour of every title. Refused until a CapCut version draws it right.
                # The probe is exempt: it carries a ProRes card precisely to re-measure this.
                if node["kind"] == "graphic" and m.get("codec") == "prores" \
                        and not plan.get("_capcut_probe"):
                    errs.append("%s: %s is ProRes. CapCut 9.6 draws ProRes 4444 graphics about 19%% "
                                "too dark (white 235 drew at 191, measured). Use a PNG still, or "
                                "re-run --make-probe on a newer CapCut." % (where, node["media"]))
                if node.get("src_raster") and m["width"] and \
                        list(node["src_raster"]) != [m["width"], m["height"]]:
                    errs.append("%s: src_raster says %r and %s measures %dx%d. The crop was "
                                "measured on a different file than the one being placed."
                                % (where, list(node["src_raster"]), node["media"],
                                   m["width"], m["height"]))
    return errs


# --------------------------------------------------------------------------------------------
# writing the draft
# --------------------------------------------------------------------------------------------

def _id() -> str:
    return str(uuid.uuid4()).upper()


def geometry(node: dict, plan: dict) -> dict:
    """The `clip` block of a video segment: no reframe, or the IR crop in CapCut's units."""
    scale, tx, ty = 1.0, 0.0, 0.0
    if node.get("crop") is not None:
        g = nle_ir.crop_to_capcut(node["crop"], node["src_raster"], plan["timeline_raster"])
        scale, (tx, ty) = g["scale"], g["transform"]
    return {"alpha": 1.0, "flip": {"horizontal": False, "vertical": False}, "rotation": 0.0,
            "scale": {"x": scale, "y": scale}, "transform": {"x": tx, "y": ty}}


def fade_points(node: dict) -> list[tuple[int, float]]:
    """(clip-relative frame, opacity) — the same frames the Premiere backend proved, [0, n, .., last]."""
    pts = []
    last = int(node["len"]) - 1
    fade = node.get("fade") or {}
    if fade.get("in"):
        pts += [(0, 0.0), (int(fade["in"]["frames"]), 1.0)]
    if fade.get("out"):
        pts += [(last - int(fade["out"]["frames"]), 1.0), (last, 0.0)]
    return pts


def alpha_keyframes(node: dict, fps) -> list[dict]:
    """A fade as opacity keyframes, offset from the segment's start in microseconds.

    The offset is taken between two ABSOLUTE conversions, so a keyframe sits on the same rounded
    grid as the segment boundary it is measured from.
    """
    pts = fade_points(node)
    if not pts:
        return []
    rec = int(node["rec"])
    base = frames_to_us(rec, fps)
    return [{
        "id": _id(), "material_id": "", "property_type": "KFTypeAlpha",
        "keyframe_list": [{
            "curveType": "Line", "graphID": "", "id": _id(),
            "left_control": {"x": 0.0, "y": 0.0}, "right_control": {"x": 0.0, "y": 0.0},
            "time_offset": frames_to_us(rec + f, fps) - base, "values": [v],
        } for f, v in pts],
    }]


def _segment(node: dict, material_id: str, speed_id: str, times: dict, render_index: int) -> dict:
    return {
        "id": _id(), "material_id": material_id,
        "target_timerange": times["target"], "source_timerange": times["source"],
        "speed": 1.0, "extra_material_refs": [speed_id],
        "render_index": render_index, "track_render_index": render_index,
        "visible": True, "reverse": False, "track_attribute": 0,
        "last_nonzero_volume": 1.0, "common_keyframes": [], "keyframe_refs": [],
        "enable_adjust": True, "enable_color_curves": True, "enable_color_wheels": True,
        "enable_lut": True, "enable_color_correct_adjust": False,
        "enable_color_match_adjust": False, "enable_smart_color_adjust": False,
    }


def _skeleton(plan: dict, draft_id: str, duration_us: int) -> dict:
    tw, th = plan["timeline_raster"]
    num, den = plan["fps"]
    plat = {"app_id": 359289, "app_source": "cc", "app_version": DRAFT_APP_VERSION,
            "os": "windows" if os.name == "nt" else "mac"}
    return {
        "id": draft_id, "name": plan["name"], "version": DRAFT_VERSION,
        "new_version": DRAFT_NEW_VERSION,
        # 🔴 the exact rate, not a rounded one. 29.97 written as 30 drifts a frame every 33 s.
        "fps": num / float(den),
        "is_drop_frame_timecode": False,
        "duration": duration_us,
        "canvas_config": {"width": int(tw), "height": int(th), "ratio": "original",
                          "background": None},
        "color_space": 0,
        "config": {
            # Magnetism off. The gate already refuses a gapped main track; this keeps CapCut from
            # closing one up when somebody first touches the timeline by hand.
            "maintrack_adsorb": False,
            "adjust_max_index": 1, "attachment_info": [], "combination_max_index": 1,
            "export_range": None, "extract_audio_last_index": 1, "lyrics_recognition_id": "",
            "lyrics_sync": True, "lyrics_taskinfo": [], "material_save_mode": 0,
            "multi_language_current": "none", "multi_language_list": [],
            "multi_language_main": "none", "multi_language_mode": "none",
            "original_sound_last_index": 1, "record_audio_last_index": 1,
            "sticker_max_index": 1, "subtitle_keywords_config": None,
            "subtitle_recognition_id": "", "subtitle_sync": True, "subtitle_taskinfo": [],
            "system_font_list": [], "use_float_render": False, "video_mute": False,
            "zoom_info_params": None,
        },
        "cover": None, "create_time": 0, "update_time": 0, "extra_info": None,
        "free_render_index_mode_on": False, "group_container": None,
        "keyframe_graph_list": [],
        "keyframes": {k: [] for k in ("adjusts", "audios", "effects", "filters", "handwrites",
                                      "stickers", "texts", "videos")},
        "last_modified_platform": dict(plat), "platform": dict(plat),
        "lyrics_effects": [], "mutable_config": None, "path": "", "relationships": [],
        "render_index_track_mode_on": False, "retouch_cover": None, "source": "default",
        "static_cover_image_path": "", "time_marks": None,
        "uneven_animation_template_info": None,
        "materials": {k: [] for k in (
            "ai_translates", "audio_balances", "audio_effects", "audio_fades",
            "audio_track_indexes", "audios", "beats", "canvases", "chromas", "color_curves",
            "common_mask", "digital_humans", "drafts", "effects", "flowers", "green_screens",
            "handwrites", "hsl", "images", "log_color_wheels", "loudnesses",
            "manual_deformations", "material_animations", "material_colors",
            "multi_language_refs", "placeholders", "plugin_effects", "primary_color_wheels",
            "realtime_denoises", "shapes", "smart_crops", "smart_relights",
            "sound_channel_mappings", "speeds", "stickers", "tail_leaders", "text_templates",
            "texts", "time_marks", "transitions", "video_effects", "video_trackings", "videos",
            "vocal_beautifys", "vocal_separations")},
        "tracks": [],
    }


def draft_from_plan(plan: dict, info: dict) -> dict:
    """The whole `draft_info.json`, from the plan and the measured media. Pure — writes nothing."""
    fps = plan["fps"]
    vids = sorted([t for t in plan["tracks"] if t["kind"] == "video"], key=lambda t: t["index"])
    auds = sorted([t for t in plan["tracks"] if t["kind"] == "audio"], key=lambda t: t["index"])
    end = max(int(n["rec"]) + int(n["len"]) for t in plan["tracks"] for n in t.get("nodes", []))
    draft = _skeleton(plan, _id(), frames_to_us(end, fps))
    mats = draft["materials"]

    vmat, amat = {}, {}
    for mid in sorted({n["media"] for t in vids for n in t["nodes"]}):
        m, path = info[mid], os.path.abspath(plan["media"][mid])
        vmat[mid] = _id()
        mats["videos"].append({
            "id": vmat[mid], "material_id": vmat[mid], "local_material_id": "",
            "material_name": os.path.basename(path), "path": path, "media_path": "",
            "remote_url": None, "type": m["kind"], "duration": m["duration_us"],
            "width": m["width"], "height": m["height"],
            "category_id": "", "category_name": "local", "check_flag": 63487,
            "audio_fade": None, "crop_ratio": "free", "crop_scale": 1.0,
            "crop": {"upper_left_x": 0.0, "upper_left_y": 0.0, "upper_right_x": 1.0,
                     "upper_right_y": 0.0, "lower_left_x": 0.0, "lower_left_y": 1.0,
                     "lower_right_x": 1.0, "lower_right_y": 1.0},
        })
    for mid in sorted({n["media"] for t in auds for n in t["nodes"]}):
        m, path = info[mid], os.path.abspath(plan["media"][mid])
        amat[mid] = _id()
        mats["audios"].append({
            "id": amat[mid], "local_material_id": amat[mid], "music_id": amat[mid],
            "name": os.path.basename(path), "path": path, "remote_url": None,
            "duration": m["duration_us"], "type": "extract_music", "category_name": "local",
            "category_id": "", "check_flag": 1, "app_id": 0, "copyright_limit_type": "none",
            "effect_id": "", "formula_id": "", "intensifies_path": "", "is_ai_clone_tone": False,
            "is_text_edit_overdub": False, "is_ugc": False, "query": "", "request_id": "",
            "resource_id": "", "search_id": "", "source_from": "", "source_platform": 0,
            "team_id": "", "text_id": "", "tone_category_id": "", "tone_category_name": "",
            "tone_effect_id": "", "tone_effect_name": "", "tone_platform": "",
            "tone_second_category_id": "", "tone_second_category_name": "", "tone_speaker": "",
            "tone_type": "", "video_id": "", "wave_points": [],
        })

    def speed() -> str:
        sid = _id()
        mats["speeds"].append({"curve_speed": None, "id": sid, "mode": 0, "speed": 1.0,
                               "type": "speed"})
        return sid

    # Video tracks bottom first: the first is CapCut's main track, and list order is layer order.
    for ri, t in enumerate(vids):
        segs = []
        for node in sorted(t["nodes"], key=lambda n: int(n["rec"])):
            s = _segment(node, vmat[node["media"]], speed(), node_times(node, fps), ri)
            s.update({
                "clip": geometry(node, plan),
                "uniform_scale": {"on": True, "value": 1.0},
                "hdr_settings": {"intensity": 1.0, "mode": 1, "nits": 1000},
                # 🔴 picture clips are SILENT: the plan's audio is on its audio tracks.
                "volume": 0.0,
                "visible": not node.get("disabled"),
                "common_keyframes": alpha_keyframes(node, fps),
            })
            segs.append(s)
        draft["tracks"].append({"id": _id(), "type": "video", "name": str(t["id"]),
                                "is_default_name": False, "attribute": 0, "flag": 0,
                                "segments": segs})
    for t in auds:
        segs = []
        for node in sorted(t["nodes"], key=lambda n: int(n["rec"])):
            s = _segment(node, amat[node["media"]], speed(), node_times(node, fps), 0)
            s.update({"clip": None, "hdr_settings": None, "volume": 1.0})
            segs.append(s)
        draft["tracks"].append({"id": _id(), "type": "audio", "name": str(t["id"]),
                                "is_default_name": False, "attribute": 0, "flag": 0,
                                "segments": segs})
    return draft


def meta_from_plan(plan: dict, info: dict, draft: dict, folder: str, root: str) -> dict:
    """`draft_meta_info.json`: what CapCut's project list shows. Nothing in it affects the cut."""
    now_us = int(time.time() * SEC)
    by_kind = {"video": 0, "photo": 0, "audio": 0}
    imported = []
    for mid, path in sorted(plan["media"].items()):
        m = info.get(mid)
        if not m:
            continue
        imported.append({
            "id": _id(), "file_Path": os.path.abspath(path),
            "extra_info": os.path.basename(path), "metetype": {"audio": "music"}.get(m["kind"],
                                                                                    m["kind"]),
            "duration": m["duration_us"], "width": m["width"], "height": m["height"],
            "create_time": now_us // SEC, "import_time": now_us // SEC,
            "import_time_ms": now_us, "item_source": 1, "md5": "", "type": by_kind[m["kind"]],
            "roughcut_time_range": {"duration": -1, "start": -1},
            "sub_time_range": {"duration": -1, "start": -1},
        })
    return {
        "draft_id": draft["id"], "draft_name": os.path.basename(folder),
        "draft_fold_path": folder, "draft_root_path": root, "draft_cover": "",
        "draft_materials": [{"type": 0, "value": imported}] +
                           [{"type": k, "value": []} for k in (1, 2, 3, 6, 7, 8)],
        "draft_materials_copied_info": [], "draft_segment_extra_info": [],
        "draft_is_invisible": False, "draft_need_rename_folder": False,
        "draft_removable_storage_device": "", "draft_new_version": "", "draft_type": "",
        "draft_timeline_materials_size_": 0,
        "tm_draft_create": now_us, "tm_draft_modified": now_us, "tm_draft_removed": 0,
        "tm_duration": draft["duration"],
    }


def _sha(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def build(plan: dict, root: str, name: str | None = None, replace: bool = False,
          allow_unverified: bool = False, probe_media=ffprobe_media, verbose: bool = True) -> dict:
    errs, notes = gate(plan, allow_unverified=allow_unverified)
    if errs:
        raise CapCutError("the plan cannot be built as a CapCut draft:\n  - " + "\n  - ".join(errs))
    info = {mid: probe_media(path) for mid, path in plan["media"].items()
            if any(n["media"] == mid for t in plan["tracks"] for n in t.get("nodes", []))}
    errs = media_checks(plan, info)
    if errs:
        raise CapCutError("the media does not support this plan:\n  - " + "\n  - ".join(errs))

    root = os.path.abspath(os.path.expanduser(root))
    if not os.path.isdir(root):
        raise CapCutError(f"no CapCut drafts folder at {root}. Run --probe to find yours, or pass "
                          f"--drafts-root.")
    folder = os.path.join(root, name or plan["name"])
    if os.path.exists(folder):
        # 🔴 Never overwrite a draft somebody made by hand. Ours carry a marker; anything without
        # one is refused outright, and ours only go with --replace.
        if not os.path.isfile(os.path.join(folder, MARKER)):
            raise CapCutError(f"{folder} exists and was not written by this tool. Pick another "
                              f"--name; it will not be touched.")
        if not replace:
            raise CapCutError(f"{folder} exists (an earlier build). Pass --replace to rebuild it, "
                              f"after closing it in CapCut — an open draft is saved over the new "
                              f"one when CapCut closes it.")
        shutil.rmtree(folder)

    draft = draft_from_plan(plan, info)
    meta = meta_from_plan(plan, info, draft, folder, root)
    os.makedirs(folder)
    for fname, body in ((DRAFT_FILE, draft), (META_FILE, meta)):
        with open(os.path.join(folder, fname), "w", encoding="utf-8") as fh:
            json.dump(body, fh, ensure_ascii=False, indent=2)
    marker = {"plan": plan["name"], "written": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "draft_sha256": _sha(os.path.join(folder, DRAFT_FILE))}
    with open(os.path.join(folder, MARKER), "w", encoding="utf-8") as fh:
        json.dump(marker, fh, indent=2)

    # Read our own file back before claiming anything: a draft that does not survive our own
    # reader will not survive CapCut's.
    problems = [p for p in readback(folder, plan) if not p.startswith("NOTE")]
    if problems:
        raise CapCutError("the draft as written does not match the plan:\n  - "
                          + "\n  - ".join(problems))
    if verbose:
        n = sum(len(t["segments"]) for t in draft["tracks"])
        print(f"  wrote {folder}")
        print(f"  {len(draft['tracks'])} track(s), {n} segment(s), "
              f"{us_to_frames(draft['duration'], plan['fps'])} frames")
        for note in notes:
            print("  NOTE: " + note)
    return {"folder": folder, "draft": draft, "notes": notes}


# --------------------------------------------------------------------------------------------
# reading it back
# --------------------------------------------------------------------------------------------

def load_draft(folder: str) -> dict:
    path = os.path.join(folder, DRAFT_FILE)
    if not os.path.isfile(path):
        alt = os.path.join(folder, "draft_content.json")
        if os.path.isfile(alt):
            path = alt
        else:
            raise CapCutError(f"no {DRAFT_FILE} in {folder}")
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CapCutError(
            f"{path} is not plain JSON ({len(raw)} bytes, starts {raw[:8]!r}). This CapCut build "
            f"stores drafts ENCRYPTED, so nothing it saved can be read back and this backend "
            f"cannot verify a build on it. --probe reports which versions do this.")


def readback(folder: str, plan: dict) -> list[str]:
    """Diff the draft on disk against the plan. Problems, plus NOTEs that are not failures.

    🔴 Only meaningful AFTER CapCut has opened and saved the draft. Until then it reads back the
    file this tool wrote, which proves the writer and nothing about the application — so that case
    is reported as a NOTE and never as a pass.
    """
    out = []
    d = load_draft(folder)
    fps = plan["fps"]
    marker = os.path.join(folder, MARKER)
    if os.path.isfile(marker):
        with open(marker, encoding="utf-8") as fh:
            m = json.load(fh)
        if m.get("draft_sha256") == _sha(os.path.join(folder, DRAFT_FILE)):
            out.append("NOTE: CapCut has not saved this draft since it was written, so this reads "
                       "back the writer, not the application. Open it in CapCut, let it save, "
                       "close it, and read it back again.")

    want_fps = int(fps[0]) / float(fps[1])
    if abs(float(d.get("fps") or 0) - want_fps) > 1e-6:
        out.append("the draft's rate is %r and the plan's is %d/%d: CapCut re-timed the timeline, "
                   "and that moves cuts as well as drifting a frame every 33 s. Every frame "
                   "position below is suspect for that reason first." % (d.get("fps"), fps[0], fps[1]))
    cc = d.get("canvas_config") or {}
    if [cc.get("width"), cc.get("height")] != list(plan["timeline_raster"]):
        out.append("the canvas is %rx%r and the plan says %r"
                   % (cc.get("width"), cc.get("height"), list(plan["timeline_raster"])))

    mats = d.get("materials") or {}
    paths = {m["id"]: m.get("path", "") for m in (mats.get("videos") or []) + (mats.get("audios") or [])}
    speeds = {s["id"]: s.get("speed") for s in mats.get("speeds") or []}

    for kind in ("video", "audio"):
        want = sorted([t for t in plan["tracks"] if t["kind"] == kind], key=lambda t: t["index"])
        got = [t for t in d.get("tracks") or [] if t.get("type") == kind]
        names = [t.get("name") for t in got]
        if all(str(t["id"]) in names for t in want):
            pairs = [(t, got[names.index(str(t["id"]))]) for t in want]
        else:
            out.append("NOTE: %s track names were not kept, so tracks are matched by order" % kind)
            pairs = list(zip(want, got))
        if len(got) != len(want):
            out.append("%d %s track(s) in the draft, %d in the plan" % (len(got), kind, len(want)))
        if kind == "video" and got and want and got[0] is not pairs[0][1]:
            out.append("track %r is not the draft's FIRST video track, so it is not CapCut's main "
                       "track and every layer above it is in the wrong order" % want[0]["id"])
        for tw, tg in pairs:
            out += _diff_track(tw, tg, plan, paths, speeds, kind)
    return out


def _diff_track(tw: dict, tg: dict, plan: dict, paths: dict, speeds: dict, kind: str) -> list[str]:
    out = []
    fps = plan["fps"]
    nodes = sorted(tw["nodes"], key=lambda n: int(n["rec"]))
    segs = sorted(tg.get("segments") or [], key=lambda s: s["target_timerange"]["start"])
    if len(segs) != len(nodes):
        out.append("track %r: %d segment(s) in the draft, %d node(s) in the plan — one is missing "
                   "or one was split" % (tw["id"], len(segs), len(nodes)))
    for i, (node, s) in enumerate(zip(nodes, segs)):
        w = "track %r node %d" % (tw["id"], i)
        tr, sr = s["target_timerange"], s.get("source_timerange") or {}
        rec = us_to_frames(tr["start"], fps)
        ln = us_to_frames(tr["start"] + tr["duration"], fps) - rec
        src = us_to_frames(sr.get("start", -SEC), fps)
        if rec != int(node["rec"]):
            out.append("%s starts at frame %d, the plan says %d. A clip that slid still tiles."
                       % (w, rec, int(node["rec"])))
        if ln != int(node["len"]):
            out.append("%s is %d frames, the plan says %d" % (w, ln, int(node["len"])))
        if src != int(node["src_in"]):
            out.append("%s takes the source from frame %d, the plan says %d — same place, same "
                       "length, different picture" % (w, src, int(node["src_in"])))
        s_end = us_to_frames(sr.get("start", 0) + sr.get("duration", 0), fps)
        if abs(sr.get("duration", 0) - tr["duration"]) > 1 or s_end != src + ln \
                or abs(float(s.get("speed", 1.0)) - 1.0) > 1e-9 \
                or any(abs(float(speeds.get(r, 1.0) or 1.0) - 1.0) > 1e-9
                       for r in s.get("extra_material_refs") or [] if r in speeds):
            out.append("%s: source %r us against target %r us at speed %r — that is a RETIME"
                       % (w, sr.get("duration"), tr["duration"], s.get("speed")))
        want_path = os.path.realpath(os.path.abspath(plan["media"][node["media"]]))
        got_path = paths.get(s.get("material_id"), "")
        if not got_path or os.path.realpath(got_path) != want_path:
            out.append("%s plays %r, the plan says %r" % (w, got_path, want_path))
        if kind == "video":
            out += _diff_picture(w, node, s, plan)
    return out


def _diff_picture(w: str, node: dict, s: dict, plan: dict) -> list[str]:
    out = []
    fps = plan["fps"]
    if abs(float(s.get("volume", 1.0))) > 1e-9:
        out.append("%s has volume %r. A picture clip's camera audio would play under the mix."
                   % (w, s.get("volume")))
    if bool(s.get("visible", True)) == bool(node.get("disabled")):
        out.append("%s visible=%r, the plan says disabled=%r"
                   % (w, s.get("visible"), bool(node.get("disabled"))))
    want, got = geometry(node, plan), s.get("clip") or {}
    for axis in ("x", "y"):
        for key in ("scale", "transform"):
            a = float((got.get(key) or {}).get(axis, float("nan")))
            b = float(want[key][axis])
            if not abs(a - b) <= GEOM_TOL:
                out.append("%s %s.%s is %r, the crop says %r" % (w, key, axis, a, b))
    kfs = {k.get("property_type"): k for k in s.get("common_keyframes") or []}
    pts = fade_points(node)
    alpha = kfs.get("KFTypeAlpha")
    if pts and not alpha:
        out.append("%s should fade and carries no opacity keyframes" % w)
    elif pts:
        base = s["target_timerange"]["start"]
        rec = us_to_frames(base, fps)
        seen = [(us_to_frames(base + k["time_offset"], fps) - rec, float(k["values"][0]))
                for k in sorted(alpha["keyframe_list"], key=lambda k: k["time_offset"])]
        if seen != [(f, float(v)) for f, v in pts]:
            out.append("%s opacity keyframes are %r, the fade says %r" % (w, seen, pts))
        if any(k.get("curveType", "Line") != "Line" for k in alpha["keyframe_list"]):
            out.append("%s: a fade keyframe is no longer linear" % w)
    elif alpha:
        out.append("%s carries opacity keyframes the plan does not ask for" % w)
    return out


# --------------------------------------------------------------------------------------------
# the probe — establish the facts on THIS machine before trusting a build
# --------------------------------------------------------------------------------------------

def default_drafts_root() -> str:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~/AppData/Local"))
        return os.path.join(base, "CapCut", "User Data", "Projects", "com.lveditor.draft")
    return os.path.expanduser("~/Movies/CapCut/User Data/Projects/com.lveditor.draft")


def app_version() -> tuple[str | None, str | None]:
    """(where the app is, its version) — or (None, None)."""
    if sys.platform == "darwin":
        app = "/Applications/CapCut.app"
        plist = os.path.join(app, "Contents", "Info.plist")
        if os.path.isfile(plist):
            with open(plist, "rb") as fh:
                return app, plistlib.load(fh).get("CFBundleShortVersionString")
    elif os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", "")
        hits = sorted(glob.glob(os.path.join(base, "CapCut", "Apps", "*", "CapCut.exe")))
        if hits:
            return hits[-1], os.path.basename(os.path.dirname(hits[-1]))
    return None, None


def probe(root: str) -> dict:
    app, ver = app_version()
    rep = {"app": app, "version": ver, "drafts_root": root,
           "root_exists": os.path.isdir(root), "drafts": []}
    if not rep["root_exists"]:
        return rep
    for entry in sorted(os.listdir(root)):
        folder = os.path.join(root, entry)
        if entry.startswith(".") or not os.path.isdir(folder):
            continue
        row = {"name": entry, "ours": os.path.isfile(os.path.join(folder, MARKER))}
        try:
            d = load_draft(folder)
            plat = d.get("last_modified_platform") or {}
            row.update(plain=True, version=d.get("version"), new_version=d.get("new_version"),
                       app_version=plat.get("app_version"), fps=d.get("fps"))
        except CapCutError as e:
            row.update(plain=False, why=str(e).split(". ")[0])
        rep["drafts"].append(row)
    return rep


def print_probe(rep: dict) -> int:
    print("CapCut app      %s" % (("%s (%s)" % (rep["app"], rep["version"])) if rep["app"]
                                  else "NOT FOUND"))
    print("drafts folder   %s%s" % (rep["drafts_root"], "" if rep["root_exists"] else "  (MISSING)"))
    theirs = [r for r in rep["drafts"] if not r["ours"]]
    for r in rep["drafts"]:
        tag = "ours " if r["ours"] else "capcut"
        if r.get("plain"):
            print("  %-6s %-32s plain JSON  version %s / %s  app %s  fps %s"
                  % (tag, r["name"][:32], r["version"], r["new_version"], r["app_version"], r["fps"]))
        else:
            print("  %-6s %-32s %s" % (tag, r["name"][:32], r.get("why")))
    if not rep["app"] or not rep["root_exists"]:
        return 1
    if not theirs:
        print("\nINCOMPLETE: no draft that CapCut itself saved. Make one empty project in CapCut, "
              "add any clip, close it, and probe again — the format this version writes has to be "
              "measured, not assumed.")
        return 1
    if any(not r.get("plain") for r in theirs):
        print("\n🔴 This CapCut saves drafts that are not plain JSON. A build may still open, but "
              "nothing CapCut saves can be read back, so no build here can be verified.")
        return 1
    newest = theirs[-1]
    if (newest["version"], newest["new_version"]) != (DRAFT_VERSION, DRAFT_NEW_VERSION):
        print("\n⚠️ CapCut writes version %s / %s; this tool writes %s / %s. If a build will not "
              "open, those are the two constants to update in capcut_draft.py."
              % (newest["version"], newest["new_version"], DRAFT_VERSION, DRAFT_NEW_VERSION))
    print("\nOK: plain-JSON drafts, so builds can be read back.")
    return 0


# --------------------------------------------------------------------------------------------
# the export — the one step that is manual, so it is the one that gets measured
# --------------------------------------------------------------------------------------------

def check_export(path: str, plan: dict) -> list[str]:
    import assemble_ffmpeg
    try:
        got = assemble_ffmpeg.probe(path)
        assemble_ffmpeg.Assembler(plan, path, tempfile.gettempdir(), verbose=False).verify(got)
    except assemble_ffmpeg.AssembleError as e:
        return [str(e)]
    if plan.get("_capcut_probe"):
        return check_probe_content(path, plan)
    return []


# --------------------------------------------------------------------------------------------
# the capability probe — a plan whose media says, in every frame, what it is
# --------------------------------------------------------------------------------------------
#
# A draft that CapCut keeps proves the numbers survived. It does not prove CapCut DRAWS them the way
# the IR means: whether a fade's keyframe offset is from the segment start, whether `visible` hides
# a clip, whether a crop's transform moves the picture the right way. Only the export can say, so
# the probe's media encodes identity as LUMA and the export is measured frame by frame.
#
#   V1  p0[10:50] at 0     uniform luma 24 + 1.5*N            placement, in-point
#       band[5:45] at 40   4 vertical bands; the plan crops    crop + transform (centre offset of
#                          band 1, luma 120 + 1.5*N             one band = transform.x of +1.0)
#       p1[0:20] at 80     DISABLED                            must be black
#       p2[0:20] at 100    uniform luma 60 + 1.5*N             placement after a disabled clip
#   V2  card at 10, 60 frames, opaque white on the bottom quarter, fade in 12 / out 18
#
# The top of the frame measures V1 alone; the bottom measures V1 under the card, so the card's
# opacity comes out as (bottom - top) / (white - top) on every frame of the fade.

PROBE_FPS = [30, 1]
PROBE_W, PROBE_H = 1080, 1920
PROBE_LEN = 120
CARD_TOP = 1440            # the card covers y >= this
STRIP = (1370, 1430)       # an opaque PNG strip, in the gap no other region measures
BANDS = (30, None, 200, 235)   # band 1 carries the frame identity


def _probe_base(t: int) -> float | None:
    """What V1 must read at timeline frame t. None means black."""
    if t < 40:
        return 24 + 1.5 * (10 + t)
    if t < 80:
        return 120 + 1.5 * (5 + t - 40)
    if t < 100:
        return None
    return 60 + 1.5 * (t - 100)


def _probe_alpha(t: int) -> float:
    """The card's opacity at timeline frame t, linear between [(0,0),(12,1),(41,1),(59,0)]."""
    f = t - 10
    if f < 0 or f > 59:
        return 0.0
    for (f0, a0), (f1, a1) in zip(((0, 0.0), (12, 1.0), (41, 1.0)), ((12, 1.0), (41, 1.0), (59, 0.0))):
        if f0 <= f <= f1:
            return a0 + (a1 - a0) * (f - f0) / float(f1 - f0)
    return 0.0


def make_probe(dest: str) -> str:
    """Write the probe's media and plan into `dest`. Returns the plan's path."""
    ff = shutil.which("ffmpeg")
    if not ff:
        raise CapCutError("ffmpeg is not on PATH; the probe media is generated with it")
    os.makedirs(dest, exist_ok=True)
    rate = "%d/%d" % tuple(PROBE_FPS)
    # 8-bit H.264 at near-lossless quality: an ordinary file CapCut imports without complaint, and
    # an 8-bit luma is directly the number the expression wrote.
    #
    # 🔴 Every file is TAGGED bt709 / tv range. The first probe's ProRes 4444 card carried no tags
    # and CapCut drew its white at luma 191 instead of 235 — an application left to guess a file's
    # colour will guess, and a probe must not leave it room to.
    tags = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-color_range", "tv"]
    h264 = ["-c:v", "libx264", "-crf", "1", "-preset", "veryfast", "-pix_fmt", "yuv420p", *tags]
    # ⚠️ The output flags alone did NOT tag the ProRes card — ffprobe read every field as unknown,
    # because a filtered frame carries its own (empty) colour properties and they win. `setparams`
    # stamps them on the frames themselves, which is what the muxer writes.
    stamp = ",setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709"

    def gen(name, w, lum, frames):
        out = os.path.join(dest, name)
        r = subprocess.run([ff, "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                            "color=c=black:s=%dx%d:r=%s" % (w, PROBE_H, rate),
                            "-vf", "geq=lum='%s':cb=128:cr=128,format=yuv420p" % lum + stamp,
                            *h264, "-frames:v", str(frames), out], capture_output=True, text=True)
        if r.returncode != 0:
            raise CapCutError("could not generate %s: %s" % (name, r.stderr.strip()[-300:]))
        return out

    band = "if(lt(X,1080),%d,if(lt(X,2160),120+1.5*N,if(lt(X,3240),%d,%d)))" % (
        BANDS[0], BANDS[2], BANDS[3])
    media = {
        "p0": gen("probe_p0.mp4", PROBE_W, "24+1.5*N", 60),
        "band": gen("probe_band.mp4", 4 * PROBE_W, band, 60),
        "p1": gen("probe_p1.mp4", PROBE_W, "180", 30),
        "p2": gen("probe_p2.mp4", PROBE_W, "60+1.5*N", 30),
    }
    card = os.path.join(dest, "probe_card.mov")
    r = subprocess.run([ff, "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=white:s=%dx%d:r=%s" % (PROBE_W, PROBE_H, rate),
                        "-vf", "format=yuva444p10le,geq=lum='p(X,Y)':cb=128:cr=128:"
                               "a='if(gte(Y,%d),1023,0)'" % CARD_TOP + stamp,
                        "-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuva444p10le",
                        *tags, "-frames:v", "60", card], capture_output=True, text=True)
    if r.returncode != 0:
        raise CapCutError("could not generate the card: %s" % r.stderr.strip()[-300:])
    media["card"] = card
    # A second KIND of graphic: an RGBA PNG still, the most common title format there is. If it
    # draws at its level while the ProRes card does not, the fault is the codec, not the tags.
    strip = os.path.join(dest, "probe_strip.png")
    r = subprocess.run([ff, "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=white:s=%dx%d" % (PROBE_W, PROBE_H),
                        "-vf", "format=rgba,geq=r=255:g=255:b=255:"
                               "a='if(between(Y,%d,%d),255,0)'" % (STRIP[0], STRIP[1] - 1),
                        "-frames:v", "1", strip], capture_output=True, text=True)
    if r.returncode != 0:
        raise CapCutError("could not generate the strip: %s" % r.stderr.strip()[-300:])
    media["strip"] = strip
    tone = os.path.join(dest, "probe_tone.wav")
    subprocess.run([ff, "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=48000:duration=5", "-c:a", "pcm_s24le",
                    tone], check=True)
    media["tone"] = tone

    plan = {
        "name": "firsttake-capcut-probe-30p-v2",
        "_capcut_probe": True,
        "_comment": "Generated by capcut_draft.py --make-probe. Export it from CapCut at "
                    "1080x1920, 30 fps, then: capcut_draft.py --check-export FILE --plan this.",
        "fps": PROBE_FPS, "timeline_raster": [PROBE_W, PROBE_H],
        "media": media,
        "baked": [{"what": "nothing graded", "why": "synthetic probe; it measures placement"}],
        "tracks": [
            {"id": "V1", "kind": "video", "index": 1, "nodes": [
                {"kind": "clip", "media": "p0", "rec": 0, "src_in": 10, "len": 40},
                {"kind": "clip", "media": "band", "rec": 40, "src_in": 5, "len": 40,
                 "src_raster": [4 * PROBE_W, PROBE_H], "crop": [PROBE_W, 0, PROBE_W, PROBE_H]},
                {"kind": "clip", "media": "p1", "rec": 80, "src_in": 0, "len": 20,
                 "disabled": True},
                {"kind": "clip", "media": "p2", "rec": 100, "src_in": 0, "len": 20},
            ]},
            {"id": "V2", "kind": "video", "index": 2, "nodes": [
                {"kind": "graphic", "media": "card", "rec": 10, "src_in": 0, "len": 60,
                 "fade": {"in": {"frames": 12}, "out": {"frames": 18}}},
            ]},
            {"id": "V3", "kind": "video", "index": 3, "nodes": [
                {"kind": "graphic", "media": "strip", "rec": 0, "src_in": 0, "len": PROBE_LEN},
            ]},
            {"id": "A1", "kind": "audio", "index": 1, "nodes": [
                {"kind": "clip", "media": "tone", "rec": 0, "src_in": 0, "len": PROBE_LEN},
            ]},
        ],
        "render": {"output": os.path.join(dest, "export.mp4"), "frames": PROBE_LEN},
    }
    path = os.path.join(dest, "probe_plan.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, indent=2)
    return path


def _region_luma(path: str, x: int, y: int, w: int, h: int) -> list[float]:
    r = subprocess.run([shutil.which("ffmpeg") or "ffmpeg", "-nostdin", "-hide_banner", "-i", path,
                        # to 8-bit FIRST: signalstats reports in the file's own bit depth, so a
                        # 10-bit ProRes export measures 4x and reads as the wrong picture.
                        "-vf", "crop=%d:%d:%d:%d,format=yuv420p,signalstats,metadata=print:"
                               "key=lavfi.signalstats.YAVG:file=-" % (w, h, x, y),
                        "-vsync", "0", "-an", "-f", "null", os.devnull],
                       capture_output=True, text=True)
    return [float(ln.split("YAVG=")[1]) for ln in r.stdout.splitlines() if "YAVG=" in ln]


def _fit(xs: list[float], ys: list[float]) -> tuple[float, float]:
    n = float(len(xs))
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    gain = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return gain, my - gain * mx


def check_probe_content(path: str, plan: dict, top=None, bottom=None, strip=None) -> list[str]:
    """Is source frame S at timeline frame T, on every frame — and do crop, disable and fade draw?

    🔴 A GAIN AND OFFSET ARE FITTED FIRST, from the identity frames alone. CapCut may treat the
    range or the colour space differently from the encoder, which scales every luma by the same
    affine map. Asserting raw values would fail a correct export for its colour handling; NOT
    fitting would pass a wrong one whose error happened to look like a range change. The fit is
    reported, and what is asserted is the residual after it — less than a third of the 1.5-code
    step between adjacent source frames, so a one-frame slip cannot hide inside it.
    """
    out = []
    if top is None:
        top = _region_luma(path, 40, 40, PROBE_W - 80, CARD_TOP - 120)
        bottom = _region_luma(path, 40, CARD_TOP + 60, PROBE_W - 80, PROBE_H - CARD_TOP - 120)
        if any(n.get("media") == "strip" for t in plan.get("tracks", []) for n in t["nodes"]):
            strip = _region_luma(path, 40, STRIP[0] + 15, PROBE_W - 80, STRIP[1] - STRIP[0] - 30)
    if len(top) != PROBE_LEN or len(bottom) != PROBE_LEN:
        return ["measured %d/%d frames of luma, expected %d" % (len(top), len(bottom), PROBE_LEN)]

    ident = [t for t in range(PROBE_LEN) if _probe_base(t) is not None]
    gain, off = _fit([_probe_base(t) for t in ident], [top[t] for t in ident])
    if not 0.8 < gain < 1.25:
        out.append("the export's luma scales by %.3f against the source's. That is not a range "
                   "conversion; the picture is not the probe's." % gain)
        return out

    def pred(v):
        return gain * v + off

    tol = 0.5
    worst = max(abs(top[t] - pred(_probe_base(t))) for t in ident)
    slips = []
    for t in ident:
        want = pred(_probe_base(t))
        if abs(top[t] - want) > tol:
            # say which source frame it IS, which is the useful number
            seen = (top[t] - off) / gain
            slips.append("frame %d reads as %.1f, should be %.1f" % (t, seen, _probe_base(t)))
    if slips:
        out.append("placement: " + "; ".join(slips[:6]) +
                   (" (+%d more)" % (len(slips) - 6) if len(slips) > 6 else ""))
    black = pred(16)
    for t in range(80, 100):
        if top[t] > black + 3:
            out.append("frame %d of the DISABLED clip reads %.1f, not black (%.1f). `visible` "
                       "did not hide it." % (t, top[t], black))
            break
    # 🔴 The card's opaque level is MEASURED from the frames where it is fully on, not assumed to be
    # the white that was written. CapCut 9.6 drew an untagged ProRes 4444 card at 191, not 235, and
    # assuming 235 turned a correct fade into fifty "wrong opacity" frames — a colour fault reported
    # as a timing fault. So the two are separate findings: the level, then the ramp against it.
    white = pred(235)
    plateau = [t for t in range(PROBE_LEN) if _probe_alpha(t) == 1.0]
    card = sum(bottom[t] for t in plateau) / len(plateau)
    if abs(card - white) > 3:
        out.append("the ProRes card draws at luma %.1f when opaque, not %.1f: a COLOUR shift on "
                   "the alpha graphic, separate from its timing. CapCut 9.6 did this tagged and "
                   "untagged alike." % (card, white))
    if strip is not None:
        level = sum(strip) / float(len(strip))
        if abs(level - white) > 3 or max(strip) - min(strip) > 2:
            out.append("the PNG strip draws at luma %.1f (%.1f..%.1f), not %.1f: a COLOUR shift on "
                       "a still graphic" % (level, min(strip), max(strip), white))
    fade_bad = []
    for t in range(PROBE_LEN):
        base = top[t]
        want = _probe_alpha(t)
        got = (bottom[t] - base) / (card - base) if abs(card - base) > 20 else want
        if abs(got - want) > 0.04:
            fade_bad.append("frame %d opacity %.2f, should be %.2f" % (t, got, want))
    if fade_bad:
        out.append("card fade: " + "; ".join(fade_bad[:6]) +
                   (" (+%d more)" % (len(fade_bad) - 6) if len(fade_bad) > 6 else ""))
    out.insert(0, "NOTE: luma fit gain %.4f offset %+.2f, worst identity residual %.2f codes; "
                  "card opaque at %.1f%s" % (gain, off, worst, card, "" if strip is None else
                                             ", PNG strip at %.1f" % (sum(strip) / len(strip))))
    return out


# --------------------------------------------------------------------------------------------
# selftest — no CapCut, no media: plant each fault in a written draft, assert it is caught
# --------------------------------------------------------------------------------------------

def _selftest_plan() -> dict:
    return {
        "name": "firsttake-selftest",
        "fps": [30, 1],
        "timeline_raster": [1080, 1920],
        "media": {"a": "/tmp/ft-a.mov", "b": "/tmp/ft-b.mov", "card": "/tmp/ft-card.mov",
                  "mix": "/tmp/ft-mix.wav"},
        "baked": [{"what": "grade", "why": "selftest"}],
        "tracks": [
            {"id": "V1", "kind": "video", "index": 1, "nodes": [
                {"kind": "clip", "media": "a", "rec": 0, "src_in": 4783, "len": 210,
                 "src_raster": [3840, 2160], "crop": [1312, 0, 1215, 2160]},
                {"kind": "clip", "media": "b", "rec": 210, "src_in": 5004, "len": 181,
                 "src_raster": [3840, 2160], "crop": [1430, 0, 1215, 2160]},
            ]},
            {"id": "V2", "kind": "video", "index": 2, "nodes": [
                {"kind": "graphic", "media": "card", "rec": 30, "src_in": 0, "len": 96,
                 "fade": {"in": {"frames": 12}, "out": {"frames": 18}}},
                {"kind": "graphic", "media": "card", "rec": 200, "src_in": 0, "len": 50,
                 "disabled": True},
            ]},
            {"id": "A1", "kind": "audio", "index": 1, "nodes": [
                {"kind": "clip", "media": "mix", "rec": 0, "src_in": 0, "len": 391}]},
        ],
        "render": {"output": "/tmp/ft-out.mov", "frames": 391},
    }


def _fake_probe(path: str) -> dict:
    if path.endswith(".png"):
        return {"kind": "photo", "width": 1080, "height": 1920, "duration_us": PHOTO_DURATION_US,
                "has_audio": False, "codec": "png"}
    if path.endswith(".wav"):
        return {"kind": "audio", "width": 0, "height": 0, "duration_us": 60 * SEC,
                "has_audio": True}
    if "card" in path:
        return {"kind": "video", "width": 1080, "height": 1920, "duration_us": 10 * SEC,
                "has_audio": False}
    return {"kind": "video", "width": 3840, "height": 2160, "duration_us": 600 * SEC,
            "has_audio": True}


class _Abort(Exception):
    pass


def selftest() -> int:
    failures = []

    def expect(name, problems, needle):
        real = [p for p in problems if not p.startswith("NOTE")]
        if not any(needle.lower() in p.lower() for p in real):
            failures.append("%s: expected a problem mentioning %r, got %r" % (name, needle, real))

    # --- microseconds: the round trip is exact on every frame, at every rate in use, for 2 hours
    for fps in ([24000, 1001], [24, 1], [25, 1], [30000, 1001], [30, 1], [60000, 1001]):
        n_max = 2 * 3600 * fps[0] // fps[1]
        bad = next((n for n in range(n_max) if us_to_frames(frames_to_us(n, fps), fps) != n), None)
        if bad is not None:
            failures.append("%r: frame %d does not survive frames->us->frames" % (fps, bad))
    # and the units: one 29.97 frame is 33366.67 us, so frame 3 is 100100 us exactly
    if frames_to_us(3, [30000, 1001]) != 100100 or frames_to_us(1, [30000, 1001]) != 33366:
        failures.append("frames_to_us is not in microseconds: %r, %r"
                        % (frames_to_us(3, [30000, 1001]), frames_to_us(1, [30000, 1001])))

    # --- adjacent clips meet EXACTLY, and source spans do not imply a retime. At 29.97, where the
    # rounding bites hardest, even though the gate keeps 29.97 out of CapCut today.
    fps = [30000, 1001]
    a = node_times({"rec": 0, "len": 210, "src_in": 4783}, fps)
    b = node_times({"rec": 210, "len": 181, "src_in": 5004}, fps)
    if a["target"]["start"] + a["target"]["duration"] != b["target"]["start"]:
        failures.append("two clips that meet in frames do not meet in microseconds")
    for n in range(0, 3000, 7):
        t = node_times({"rec": n, "len": 97, "src_in": 11 * n + 5}, fps)
        if abs(t["source"]["duration"] - t["target"]["duration"]) > 1:
            failures.append("rec %d: source and target durations differ by more than truncation"
                            % n)
            break
        if us_to_frames(t["source"]["start"] + t["source"]["duration"], fps) != 11 * n + 5 + 97:
            failures.append("rec %d: the source end lands on the wrong frame" % n)
            break

    # --- the gate
    plan = _selftest_plan()
    # ⚠️ Against an INJECTED unverified capability, not the real table: this asserted the real one
    # until the last CapCut entry was promoted, and then failed with the mechanism fine — the
    # trap nle_ir's own selftest already records.
    saved = nle_ir.CAPABILITIES[BACKEND]["clip_disable"]
    nle_ir.CAPABILITIES[BACKEND]["clip_disable"] = "UNVERIFIED"
    try:
        expect("UNVERIFIED blocks by default", gate(plan)[0], "UNVERIFIED")
        if gate(plan, allow_unverified=True)[0]:
            failures.append("--allow-unverified did not let an UNVERIFIED capability through")
    finally:
        nle_ir.CAPABILITIES[BACKEND]["clip_disable"] = saved
    errs, notes = gate(plan)
    if errs:
        failures.append("with every capability proven, a buildable plan still needs "
                        "--allow-unverified: %r" % errs)
    errs, notes = gate(plan, allow_unverified=True)
    if errs:
        failures.append("a buildable plan was refused: %r" % errs)
    if not any("--check-export" in n for n in notes):
        failures.append("the render block must turn into a note about the manual export")
    pl = _selftest_plan(); pl["tracks"][0]["nodes"][1]["rec"] = 230
    for tr in pl["tracks"][1:]:
        tr["nodes"][-1]["len"] = min(tr["nodes"][-1]["len"], 391 - tr["nodes"][-1]["rec"])
    expect("a main-track gap", gate(pl, True)[0], "magnetic")
    pl = _selftest_plan(); pl["tracks"][0]["nodes"][0]["grade"] = {"lut": "/tmp/x.cube"}
    expect("a LUT", gate(pl, True)[0], "baked")
    pl = _selftest_plan(); pl["tracks"][1]["nodes"][0]["fade"]["in"]["shape"] = "ease"
    expect("a shaped fade", gate(pl, True)[0], "fade_shaped")
    pl = _selftest_plan(); pl["tracks"][0]["nodes"][0]["transition"] = {"out": {"frames": 12}}
    expect("a transition", gate(pl, True)[0], "transition")

    # --- media the plan cannot use
    info = {k: _fake_probe(v) for k, v in plan["media"].items()}
    pl = _selftest_plan(); pl["tracks"][0]["nodes"][0]["src_in"] = 600 * 30
    expect("a clip past the end of its source", media_checks(pl, info), "end the clip early")
    pl = _selftest_plan(); del pl["tracks"][0]["nodes"][0]["crop"]
    expect("16:9 into 9:16 with no crop", media_checks(pl, info), "letterboxes")
    pl = _selftest_plan(); pl["tracks"][0]["nodes"][0]["src_raster"] = [1920, 1080]
    pl["tracks"][0]["nodes"][0]["crop"] = [0, 0, 607.5, 1080]
    expect("src_raster from another file", media_checks(pl, info), "different file")
    pr = dict(info, card=dict(info["card"], codec="prores"))
    expect("a ProRes 4444 graphic", media_checks(_selftest_plan(), pr), "too dark")
    probe_pl = dict(_selftest_plan(), _capcut_probe=True)
    if media_checks(probe_pl, pr):
        failures.append("the probe must still build with its ProRes card, to re-measure it")
    png = dict(info, card=_fake_probe("/tmp/ft-card.png"))
    if media_checks(_selftest_plan(), png):
        failures.append("a PNG graphic was refused: %r" % media_checks(_selftest_plan(), png))

    # --- the probe checker, on luma it is handed rather than measured: what a correct export
    # reads, the same under a range conversion, and each fault it exists to catch
    def ideal(gain=1.0, off=0.0, slip_at=None, show_disabled=False, fade_shift=0, card=235.0):
        top, bot = [], []
        for t in range(PROBE_LEN):
            src_t = t + 1 if slip_at is not None and t >= slip_at else t
            b = _probe_base(src_t if _probe_base(src_t) is not None else t)
            b = 180.0 if (show_disabled and 80 <= t < 100) else (16.0 if b is None else b)
            a = _probe_alpha(t - fade_shift)
            top.append(gain * b + off)
            bot.append(gain * (b + a * (card - b)) + off)
        return top, bot

    def probe_says(**kw):
        top, bot = ideal(**kw)
        return check_probe_content("", {}, top=top, bottom=bot)

    if [p for p in probe_says() if not p.startswith("NOTE")]:
        failures.append("a perfect probe export fails: %r" % probe_says())
    if [p for p in probe_says(gain=219 / 255.0, off=16) if not p.startswith("NOTE")]:
        failures.append("a full-to-limited range conversion was read as a timing fault")
    expect("a one-frame slip from frame 50", probe_says(slip_at=50), "placement")
    expect("the disabled clip drawn", probe_says(show_disabled=True), "DISABLED")
    expect("a fade one frame late", probe_says(fade_shift=1), "card fade")
    # what CapCut 9.6 actually drew: a correct fade on a card at 191. A colour finding, and ONLY
    # that — the ramp must still pass against the measured level.
    dim = [p for p in probe_says(card=191.0) if not p.startswith("NOTE")]
    if not any("COLOUR shift" in p for p in dim):
        failures.append("a card drawn at 191 must be reported as a colour shift: %r" % dim)
    if any("card fade" in p for p in dim):
        failures.append("a dim card with a correct ramp was reported as a fade fault: %r" % dim)
    expect("a dim card AND a late fade", probe_says(card=191.0, fade_shift=1), "card fade")
    top, bot = ideal()
    if [p for p in check_probe_content("", {}, top=top, bottom=bot, strip=[235.0] * PROBE_LEN)
            if not p.startswith("NOTE")]:
        failures.append("a PNG strip at its level was reported")
    expect("a dim PNG strip", check_probe_content("", {}, top=top, bottom=bot,
                                                  strip=[191.0] * PROBE_LEN), "still graphic")

    fps = plan["fps"]
    root = tempfile.mkdtemp(prefix="firsttake-capcut-")
    try:
        try:
            built = build(plan, root, allow_unverified=True, probe_media=_fake_probe,
                          verbose=False)
        except CapCutError as e:
            failures.append("the selftest plan did not build: %s" % e)
            raise _Abort()
        folder = built["folder"]
        probs = readback(folder, plan)
        if [p for p in probs if not p.startswith("NOTE")]:
            failures.append("a fresh build does not read back clean: %r" % probs)
        if not any("has not saved this draft" in p for p in probs):
            failures.append("an unsaved draft must say it proves only the writer")

        # the written numbers, against the IR rather than against themselves
        d = load_draft(folder)
        v1 = [t for t in d["tracks"] if t["name"] == "V1"][0]["segments"]
        g = nle_ir.crop_to_premiere([1312, 0, 1215, 2160], [3840, 2160], [1080, 1920])
        if abs(v1[0]["clip"]["transform"]["x"] / 2.0 - (g["position"][0] - 0.5)) > 1e-12:
            failures.append("the draft's transform does not agree with Premiere's Position")
        if d["tracks"][0]["name"] != "V1" or d["tracks"][0]["type"] != "video":
            failures.append("V1 must be the first track: CapCut's main track")
        if d["fps"] != 30.0:
            failures.append("the draft's rate is %r, not 30.0" % d["fps"])
        if d["config"].get("maintrack_adsorb") is not False:
            failures.append("main-track magnetism must be written OFF")
        v2 = [t for t in d["tracks"] if t["name"] == "V2"][0]["segments"]
        offs = [k["time_offset"] for k in v2[0]["common_keyframes"][0]["keyframe_list"]]
        if offs[1] != frames_to_us(42, fps) - frames_to_us(30, fps) or offs[-1] > v2[0][
                "target_timerange"]["duration"]:
            failures.append("fade keyframe offsets are not clip-relative microseconds: %r" % offs)

        # --- plant each fault in the draft, assert readback names it
        def planted(name, mutate, needle):
            dd = copy.deepcopy(d)
            mutate(dd)
            with open(os.path.join(folder, DRAFT_FILE), "w", encoding="utf-8") as fh:
                json.dump(dd, fh)
            expect(name, readback(folder, plan), needle)

        def seg(dd, track, i):
            return [t for t in dd["tracks"] if t["name"] == track][0]["segments"][i]

        def slide(dd):
            s = seg(dd, "V1", 1)
            s["target_timerange"]["start"] += frames_to_us(1, fps)
        planted("a clip slid one frame", slide, "starts at frame 211")
        planted("in-point moved 2 frames",
                lambda dd: seg(dd, "V1", 1)["source_timerange"].__setitem__(
                    "start", frames_to_us(5006, fps)), "same length, different picture")
        planted("camera audio left up",
                lambda dd: seg(dd, "V1", 0).__setitem__("volume", 1.0), "under the mix")
        planted("a reframe nudged",
                lambda dd: seg(dd, "V1", 0)["clip"]["transform"].__setitem__("x", 0.01),
                "transform.x")
        planted("a one-frame retime",
                lambda dd: seg(dd, "V1", 0)["source_timerange"].__setitem__(
                    "duration", seg(dd, "V1", 0)["target_timerange"]["duration"]
                    + frames_to_us(1, fps)), "RETIME")
        planted("a speed that is not 1",
                lambda dd: seg(dd, "V1", 0).__setitem__("speed", 1.01), "RETIME")
        planted("a dropped segment",
                lambda dd: [t for t in dd["tracks"] if t["name"] == "V2"][0]["segments"].pop(),
                "missing")
        # what CapCut 9.6 did to a 29.97 draft, the other way round
        planted("the timeline re-timed", lambda dd: dd.__setitem__("fps", 30000 / 1001),
                "re-timed")
        planted("media swapped",
                lambda dd: dd["materials"]["videos"][0].__setitem__("path", "/tmp/other.mov"),
                "plays")
        planted("the disabled clip shown",
                lambda dd: seg(dd, "V2", 1).__setitem__("visible", True), "disabled")
        planted("a fade keyframe moved",
                lambda dd: seg(dd, "V2", 0)["common_keyframes"][0]["keyframe_list"][1].__setitem__(
                    "time_offset", frames_to_us(43, fps) - frames_to_us(30, fps)),
                "opacity keyframes")

        def reorder(dd):
            dd["tracks"][0], dd["tracks"][1] = dd["tracks"][1], dd["tracks"][0]
        planted("main track demoted", reorder, "main track")

        with open(os.path.join(folder, DRAFT_FILE), "wb") as fh:
            fh.write(b"\x8f\x02encrypted-looking bytes")
        try:
            readback(folder, plan)
            failures.append("an encrypted draft must refuse to read back")
        except CapCutError as e:
            if "ENCRYPTED" not in str(e):
                failures.append("the encrypted-draft error does not say so: %s" % e)

        # --- never overwrite a draft somebody made by hand
        theirs = os.path.join(root, "made-by-hand")
        os.makedirs(theirs)
        with open(os.path.join(theirs, DRAFT_FILE), "w", encoding="utf-8") as fh:
            fh.write("{}")
        for rep in (False, True):
            try:
                build(plan, root, name="made-by-hand", replace=rep, allow_unverified=True,
                      probe_media=_fake_probe, verbose=False)
                failures.append("overwrote a draft without our marker (replace=%r)" % rep)
            except CapCutError as e:
                if "not written by this tool" not in str(e):
                    failures.append("wrong refusal for a hand-made draft: %s" % e)
        if not os.path.isfile(os.path.join(theirs, DRAFT_FILE)):
            failures.append("the hand-made draft was touched")
        try:
            build(plan, root, allow_unverified=True, probe_media=_fake_probe, verbose=False)
            failures.append("rebuilt over an earlier build without --replace")
        except CapCutError as e:
            if "--replace" not in str(e):
                failures.append("the earlier-build refusal does not say how: %s" % e)
        build(plan, root, replace=True, allow_unverified=True, probe_media=_fake_probe,
              verbose=False)

        # --- the probe reads both kinds of draft
        rep = probe(root)
        kinds = {r["name"]: (r["ours"], r.get("plain")) for r in rep["drafts"]}
        if kinds.get(plan["name"]) != (True, True) or kinds.get("made-by-hand") != (False, True):
            failures.append("probe misread the drafts folder: %r" % kinds)
    except _Abort:
        pass
    finally:
        shutil.rmtree(root, ignore_errors=True)

    if failures:
        print("SELFTEST FAILED (%d)" % len(failures))
        for f in failures:
            print("  - " + f)
        return 1
    print("capcut_draft selftest: all checks passed")
    return 0


# --------------------------------------------------------------------------------------------

def _resolve_folder(arg: str, root: str) -> str:
    return arg if os.path.isdir(arg) else os.path.join(root, arg)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--probe", action="store_true", help="what CapCut is here, and what it writes")
    ap.add_argument("--build", metavar="PLAN.JSON")
    ap.add_argument("--readback", metavar="DRAFT", help="a draft folder, or a name in the root")
    ap.add_argument("--check-export", metavar="FILE", help="measure a CapCut export")
    ap.add_argument("--make-probe", metavar="DIR",
                    help="write a 30 fps capability probe (media + plan) to build and export")
    ap.add_argument("--plan", metavar="PLAN.JSON", help="for --readback and --check-export")
    ap.add_argument("--drafts-root", default=default_drafts_root())
    ap.add_argument("--name", help="draft folder name (default: the plan's name)")
    ap.add_argument("--replace", action="store_true", help="rebuild a draft this tool wrote")
    ap.add_argument("--allow-unverified", action="store_true")
    ap.add_argument("--out", metavar="JSON", help="write the probe or readback report here")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if a.make_probe:
        try:
            path = make_probe(os.path.abspath(a.make_probe))
        except CapCutError as e:
            print("REFUSED: %s" % e)
            return 1
        print("probe plan: %s\nthen: --build %s, open + save + close in CapCut, "
              "--readback, export at 1080x1920 30 fps, --check-export" % (path, path))
        return 0
    if a.probe:
        rep = probe(os.path.expanduser(a.drafts_root))
        if a.out:
            with open(a.out, "w", encoding="utf-8") as fh:
                json.dump(rep, fh, indent=2)
        return print_probe(rep)

    def load(p):
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)

    try:
        if a.build:
            build(load(a.build), a.drafts_root, name=a.name, replace=a.replace,
                  allow_unverified=a.allow_unverified)
            print("Open it in CapCut (restart CapCut if it is not listed), let it save, close it, "
                  "then:\n  python3 capcut_draft.py --readback %s --plan %s"
                  % (a.name or load(a.build)["name"], a.build))
            return 0
        if a.readback or a.check_export:
            if not a.plan:
                ap.error("--readback and --check-export need --plan")
            plan = load(a.plan)
            if a.readback:
                probs = readback(_resolve_folder(a.readback, os.path.expanduser(a.drafts_root)),
                                 plan)
            else:
                probs = check_export(a.check_export, plan)
            if a.out:
                with open(a.out, "w", encoding="utf-8") as fh:
                    json.dump(probs, fh, indent=2)
            real = [p for p in probs if not p.startswith("NOTE")]
            for p in probs:
                print(("  " if p.startswith("NOTE") else "  - ") + p)
            print("MISMATCH (%d)" % len(real) if real else "matches the plan")
            return 1 if real else 0
    except CapCutError as e:
        print("REFUSED: %s" % e)
        return 1
    ap.error("give one of --probe --build --readback --check-export --selftest")
    return 2


if __name__ == "__main__":
    sys.exit(main())

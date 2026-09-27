/*
 * film-shoot timeline — Premiere Pro UXP backend.
 *
 * Premiere has no external scripting entry point. Resolve ships a Python module that talks to the
 * running application, so a build script can be driven from a shell; UXP is a JavaScript runtime
 * INSIDE Premiere with no CLI. So this panel is the backend, and it takes its orders from a job
 * folder on disk: Python writes `job-<id>.json`, this reads it, does the work, and writes
 * `job-<id>.result.json`. One manual step — open the panel and press Start listening — and then
 * everything after it is scripted, which is the same bargain Resolve makes by requiring the app to
 * be open on the right window.
 *
 * TWO STRUCTURAL FACTS ABOUT THIS API THAT SHAPE EVERY OP BELOW
 *
 * 1. Edits are Actions committed inside `project.lockedAccess(() => project.executeTransaction(cb))`,
 *    and BOTH callbacks are SYNCHRONOUS. Awaiting inside either commits an empty transaction.
 *
 *    🔴 And a handle is invalidated by a PROJECT MUTATION, not merely by an await. That is why
 *    `applyCrop` can await a component chain and use it in the very next lock — nothing changes in
 *    between — while the audio sweep reusing an editor acquired before 2228 frames were placed
 *    throws "The script object is no longer valid". Acquire handles after the last mutation, and do
 *    the casts and create*Action calls inside the lock.
 *
 * 2. Source in/out lives on the ProjectItem, not on the placement call.
 *    `createOverwriteItemAction(item, time, vTrack, aTrack)` has no in/out arguments, so two clips
 *    cut from one file cannot be batched: set in/out, place, set in/out, place. Batching them all
 *    would place every clip with the LAST in/out applied, and the timeline would still tile
 *    perfectly.
 *
 * Nothing here is trusted until `probe` has run against this build of Premiere. Every op reports
 * what it actually read back, never what it intended.
 */

const ppro = require("premierepro");
const uxp = require("uxp");

const POLL_MS = 600;
const PANEL_VERSION = "1.9.0-program-build";   // bump on every edit, so a stale panel is visible in --ping

/* Premiere's internal time unit. Asserted against the running application in `probe` rather than
 * trusted: if this number is ever different, every frame position computed from it is wrong and
 * the failure would show up as drift, not as an error. */
const TICKS_PER_SECOND = 254016000000;

let jobFolder = null;      // uxp storage Folder entry, or a native path string when fs works
let dataFolder = null;     // the plugin's own sandbox — always writable, needs no permission
let useNativeFs = false;
let nativeFs = null;
let listening = false;
let timer = null;
let seen = Object.create(null);

// -------------------------------------------------------------------------------------------
// logging
// -------------------------------------------------------------------------------------------

function log(msg) {
  const el = document.getElementById("log");
  const stamp = new Date().toISOString().substr(11, 8);
  el.textContent += "[" + stamp + "] " + msg + "\n";
  el.scrollTop = el.scrollHeight;
}

function setState(s) {
  document.getElementById("state").textContent = s;
}

function errString(e) {
  if (!e) return "unknown error";
  if (typeof e === "string") return e;
  return (e.message || String(e)) + (e.stack ? "\n" + e.stack : "");
}

// -------------------------------------------------------------------------------------------
// filesystem — prefer a native-path fs, fall back to a picked folder
// -------------------------------------------------------------------------------------------

async function initFs() {
  // The plugin's own data folder needs no permission and no picker, so there is ALWAYS somewhere
  // to write a report to. A probe that runs and has nowhere to put its findings is a probe that
  // did not run, and the first version of this panel had exactly that hole.
  try {
    dataFolder = await uxp.storage.localFileSystem.getDataFolder();
    log("fallback write folder: " + (dataFolder.nativePath || dataFolder.name));
  } catch (e) {
    log("no data folder available: " + errString(e));
  }

  try {
    nativeFs = require("fs");
    if (nativeFs && typeof nativeFs.readdir === "function") {
      useNativeFs = true;
      log("filesystem: native fs module available");
      await adoptDefaultJobFolder();
      return;
    }
    log("filesystem: fs module loaded but has no readdir — treating as unavailable");
  } catch (e) {
    log("filesystem: no native fs (" + errString(e).split("\n")[0] + ")");
  }
  log("filesystem: pick a job folder to use the Python bridge; reports will still be written "
      + "to the fallback folder above.");
}

/* Find the home directory so the job folder can default to the same path the Python side uses,
 * and the panel needs no click at all.
 *
 * ⚠️ A picker was the first design and it was wrong: "native fs is available" and "we know where
 * to write" are different facts, and conflating them meant a probe ran successfully and threw its
 * findings away. Whatever this resolves to is LOGGED, not assumed — if it is wrong you can see
 * that it is wrong instead of wondering where the file went.
 */
async function homeDir() {
  try {
    const os = require("os");
    if (os && typeof os.homedir === "function") {
      const h = os.homedir();
      if (h) return h;
    }
  } catch (e) { /* os may not be exposed */ }

  // Derive it from the plugin's own sandbox path, which is always under the user's home.
  const p = dataFolder && dataFolder.nativePath;
  if (p) {
    for (const marker of ["/Library/", "\\AppData\\"]) {
      const i = p.indexOf(marker);
      if (i > 0) return p.slice(0, i);
    }
  }
  return null;
}

async function adoptDefaultJobFolder() {
  const home = await homeDir();
  if (!home) {
    log("could not work out the home directory — press 'Job folder…' to choose one");
    return;
  }
  const path = home + "/.film-shoot/premiere-jobs";
  try {
    await nativeFs.readdir(path);          // exists and is listable
  } catch (e) {
    try {
      try { await nativeFs.mkdir(home + "/.film-shoot"); } catch (e0) { /* may exist */ }
      await nativeFs.mkdir(path);
    } catch (e2) {
      log("default job folder " + path + " is not usable (" + errString(e2).split("\n")[0]
          + ") — press 'Job folder…'");
      return;
    }
  }
  jobFolder = path;
  useNativeFs = true;
  document.getElementById("folder").textContent = path;
  log("job folder defaulted to: " + path);
}

/* Write somewhere, and say where. Prefers the job folder so Python can see it. */
async function writeReport(name, obj) {
  const txt = JSON.stringify(obj, null, 2);
  if (jobFolder) {
    try {
      await writeJson(name, obj);
      return useNativeFs ? jobFolder + "/" + name : (jobFolder.nativePath || "") + "/" + name;
    } catch (e) {
      log("could not write to the job folder (" + errString(e).split("\n")[0]
          + ") — falling back");
    }
  }
  if (!dataFolder) throw new Error("nowhere to write " + name);
  const entry = await dataFolder.createFile(name, { overwrite: true });
  await entry.write(txt);
  return (dataFolder.nativePath || dataFolder.name) + "/" + name;
}

async function pickFolder() {
  const fsl = uxp.storage.localFileSystem;
  const folder = await fsl.getFolder();
  if (!folder) return;
  jobFolder = folder;
  useNativeFs = false;
  document.getElementById("folder").textContent = folder.nativePath || folder.name;
  log("job folder: " + (folder.nativePath || folder.name));
}

async function setFolderPath(p) {
  // Used when Python hands us a path and native fs works.
  jobFolder = p;
  useNativeFs = true;
  document.getElementById("folder").textContent = p;
}

/* Pending jobs only — a job that already has a result is DONE and must never run twice.
 *
 * 🔴 The in-memory `seen` map is not enough, because a UDT reload builds a fresh panel with an
 * empty one. Without this filter, pressing Reload re-executes every job ever written to the
 * folder — including a `build`, which would lay a second copy of the whole programme into the
 * project. The durable record of "already done" is the result file on disk, not a variable.
 */
async function listJobs() {
  if (!jobFolder) return [];
  let names;
  if (useNativeFs) {
    names = await nativeFs.readdir(jobFolder);
  } else {
    names = (await jobFolder.getEntries()).filter((e) => e.isFile).map((e) => e.name);
  }
  const done = new Set(names.filter((n) => /\.result\.json$/.test(n)));
  return names.filter((n) => /^job-.*\.json$/.test(n)
                         && !/\.result\.json$/.test(n)
                         && !done.has(n.replace(/\.json$/, ".result.json")));
}

async function readJson(name) {
  if (useNativeFs) {
    const txt = await nativeFs.readFile(jobFolder + "/" + name, { encoding: "utf-8" });
    return JSON.parse(txt);
  }
  const entry = await jobFolder.getEntry(name);
  return JSON.parse(await entry.read());
}

async function writeJson(name, obj) {
  const txt = JSON.stringify(obj, null, 2);
  if (useNativeFs) {
    await nativeFs.writeFile(jobFolder + "/" + name, txt, { encoding: "utf-8" });
    return;
  }
  const entry = await jobFolder.createFile(name, { overwrite: true });
  await entry.write(txt);
}

// -------------------------------------------------------------------------------------------
// time
// -------------------------------------------------------------------------------------------

/* An EXACT frame rate from a rational, not from a float.
 *
 * `FrameRate.createWithValue()` takes a number, so 30000/1001 arrives as 29.97002997002997 and
 * whatever the application rounds that to is what the programme is built on. But `ticksPerFrame`
 * is read/WRITE, and ticks are integers — so the rational can be restored exactly:
 *
 *     ticksPerFrame = TICKS_PER_SECOND * den / num        (29.97 DF -> 8475667200)
 *
 * This is the same class of problem as Resolve's playback frame rate, with a better answer:
 * Resolve refuses every format offered and needs a committed settings preset, Premiere lets the
 * integer be written directly. Verified by reading `value` and `ticksPerFrame` back below.
 */
function exactFrameRate(fps) {
  const num = fps[0], den = fps[1];
  const fr = ppro.FrameRate.createWithValue(num / den);
  const want = (TICKS_PER_SECOND * den) / num;
  if (!Number.isInteger(want)) {
    throw new Error("fps " + num + "/" + den + " does not land on a whole number of ticks ("
      + want + "). Every frame position would carry a rounding error.");
  }
  if (fr.ticksPerFrame !== want) {
    fr.ticksPerFrame = want;
    if (fr.ticksPerFrame !== want) {
      throw new Error("could not set ticksPerFrame to " + want + "; it read back as "
        + fr.ticksPerFrame + ". Do not build on this rate.");
    }
  }
  return fr;
}

function frameRateOf(plan) {
  return exactFrameRate(plan.fps || [30000, 1001]);
}

function tickAt(frames, frameRate) {
  return ppro.TickTime.createWithFrameAndFrameRate(frames, frameRate);
}

// -------------------------------------------------------------------------------------------
// project helpers
// -------------------------------------------------------------------------------------------

/* Mutate inside a LOCKED transaction, and derive short-lived handles inside the lock.
 *
 * 🔴 Measured 26.5.1: a `ClipProjectItem` obtained from `ClipProjectItem.cast()` and then used in
 * `executeTransaction` threw **"The script object is no longer valid."** UXP's handles to Premiere
 * objects do not survive the async boundaries between acquiring them and using them — the project
 * may have changed underneath, so the bridge invalidates them.
 *
 * `project.lockedAccess(cb)` is the documented answer: nothing changes during `cb`, and a
 * transaction may be opened inside it. Both callbacks are SYNCHRONOUS, so the split is:
 *
 *     await everything you need (items, chains, frame rate)   <- outside
 *     lockedTransaction(project, label, (compound) => {       <- inside: cast, create, add
 *         const clip = castClip(item);
 *         compound.addAction(clip.createSetInOutPointsAction(a, b));
 *     });
 *
 * i.e. do the CASTS and the create*Action calls inside the lock, not before it.
 */
function lockedTransaction(project, label, fn) {
  let thrown = null;
  let ran = false;
  project.lockedAccess(() => {
    try {
      const ok = project.executeTransaction((compound) => {
        ran = true;
        fn(compound);
      }, label);
      if (ok === false) thrown = new Error("executeTransaction returned false for " + label);
    } catch (e) {
      thrown = e;
    }
  });
  if (thrown) throw thrown;
  if (!ran) {
    // An empty transaction reports nothing and changes nothing. Never let that pass as success.
    throw new Error("transaction " + JSON.stringify(label) + " never invoked its callback, so "
      + "nothing was committed.");
  }
}

async function activeProject() {
  const p = await ppro.Project.getActiveProject();
  if (!p) throw new Error("no active project — open or create one first");
  return p;
}

/* Map media path -> ProjectItem by walking the project tree.
 *
 * 🔴 `ProjectItem` is a BASE type with almost nothing on it: `name` and `type` are PROPERTIES (not
 * `getName()`), plus `getId()` and `getParentBin()`. Everything useful lives on a subtype reached
 * through a cast:
 *
 *     ppro.FolderItem.cast(item)       -> getItems()
 *     ppro.ClipProjectItem.cast(item)  -> getMediaFilePath(), getInPoint(mediaType),
 *                                         createSetInOutPointsAction(), createSetInputLUTIDAction()
 *
 * The first version of this called `it.getName()` and died with "is not a function" — which is the
 * good failure. The bad one would have been a cast that quietly returned something falsy and left
 * the index empty, so the build then reported "imported media not found" on media that was there.
 *
 * Duck-type the cast RESULT rather than switching on `type`: the enum's runtime representation has
 * not been measured here, and a wrong comparison would silently classify every clip as a bin.
 */
function castFolder(item) {
  try {
    const f = ppro.FolderItem.cast(item);
    return f && typeof f.getItems === "function" ? f : null;
  } catch (e) {
    return null;
  }
}

function castClip(item) {
  try {
    const c = ppro.ClipProjectItem.cast(item);
    return c && typeof c.getMediaFilePath === "function" ? c : null;
  } catch (e) {
    return null;
  }
}

async function indexProjectItems(project) {
  const byPath = Object.create(null);
  const byName = Object.create(null);
  const observed = [];
  const root = await project.getRootItem();

  async function walk(folder, depth) {
    if (depth > 12) return;                       // a bin cycle would otherwise hang the host
    const items = await folder.getItems();
    for (const it of items) {
      const name = it.name;                       // PROPERTY
      if (name && !(name in byName)) byName[name] = it;
      observed.push({ name: name, type: it.type, typeOf: typeof it.type });

      const clip = castClip(it);
      if (clip) {
        try {
          const p = await clip.getMediaFilePath();
          if (p) byPath[p] = it;
        } catch (e) { /* offline or not file-backed */ }
      }

      const sub = castFolder(it);
      if (sub) await walk(sub, depth + 1);
    }
  }
  await walk(root, 0);
  return { byPath: byPath, byName: byName, observed: observed };
}

/* Look a parameter up by display name — and REFUSE an ambiguous name rather than taking the first.
 *
 * 🔴 Measured on 26.5.1: `AE.ADBE Opacity` exposes THREE params, of which index 1 and index 2 are
 * BOTH called "Blend Mode". So display names are not unique, and "take the first match" is a coin
 * toss that happens to land right for Opacity/Position/Scale. Also `AE.ADBE Motion` param 3 is
 * named " " — a single space — so some params cannot be addressed by name at all.
 *
 * Failing loudly here is the whole point: writing to the wrong one of two identically named params
 * produces a timeline that renders, with the effect set on something nobody looked at.
 */
async function findParamByName(component, wanted) {
  const n = component.getParamCount();
  const lowered = wanted.toLowerCase();
  const hits = [];
  for (let i = 0; i < n; i++) {
    const param = component.getParam(i);
    const dn = (param.displayName || "").toLowerCase();
    if (dn === lowered) hits.push({ index: i, param: param });
  }
  if (hits.length > 1) {
    const mn = await component.getMatchName();
    throw new Error("parameter name " + JSON.stringify(wanted) + " is ambiguous on " + mn
      + " — it matches indices " + hits.map((h) => h.index).join(", ")
      + ". Address it by index instead, and record which one in the probe report; picking the "
      + "first would set the effect on a parameter nobody chose.");
  }
  return hits.length ? hits[0] : null;
}

async function describeChain(trackItem, withKeyframes) {
  const chain = await trackItem.getComponentChain();
  const out = [];
  const n = chain.getComponentCount();
  for (let i = 0; i < n; i++) {
    const c = chain.getComponentAtIndex(i);
    const params = [];
    const pc = c.getParamCount();
    for (let j = 0; j < pc; j++) {
      const p = c.getParam(j);
      let kf = null;
      try {
        kf = await p.areKeyframesSupported();
      } catch (e) {
        kf = "error: " + errString(e);
      }
      const row = { index: j, displayName: p.displayName, keyframable: kf };

      /* What a fade or an animated push-in actually BECAME on the timeline.
       *
       * Without this a readback can only say a parameter exists, which cannot tell a fade that
       * took from one that silently did not — the failure mode this whole backend is written
       * against. Times come back as ticks so they can be compared to an exact frame. */
      if (withKeyframes) {
        try {
          row.timeVarying = p.isTimeVarying();
          const times = p.getKeyframeListAsTickTimes() || [];
          row.keyframeCount = times.length;
          if (times.length) {
            row.keyframes = [];
            for (const t of times.slice(0, 24)) {
              let v = null;
              try {
                const got = await p.getValueAtTime(t);
                v = got && got.value;
              } catch (e) { v = "unreadable"; }
              // Interpolation IS readable, via the keyframe rather than the param. An earlier note
              // in this repo claimed it was not, which understated what can be verified: a shaped
              // fade can be proven, not merely assumed from a setter not complaining.
              let interp = null;
              try {
                const k = p.getKeyframePtr(t);
                interp = k ? await k.getTemporalInterpolationMode() : null;
              } catch (e) { interp = "unreadable"; }
              row.keyframes.push({ ticks: t.ticksNumber, seconds: t.seconds, value: v,
                                   interpolation: interp });
            }
          }
        } catch (e) {
          row.keyframeError = errString(e).split("\n")[0];
        }
      }
      params.push(row);
    }
    out.push({
      index: i,
      matchName: await c.getMatchName(),
      displayName: await c.getDisplayName(),
      paramCount: pc,
      params: params,
    });
  }
  return out;
}

// -------------------------------------------------------------------------------------------
// op: ping
// -------------------------------------------------------------------------------------------

async function opPing() {
  return {
    ok: true,
    panelVersion: PANEL_VERSION,
    host: uxp.host ? { name: uxp.host.name, version: uxp.host.version } : null,
    filesystem: useNativeFs ? "native-fs" : "picked-folder",
  };
}

// -------------------------------------------------------------------------------------------
// op: probe — read-only. Turns every UNVERIFIED capability into a measured fact.
// -------------------------------------------------------------------------------------------

async function opProbe(job) {
  const rep = { ok: true, panelVersion: PANEL_VERSION, checks: {}, notes: [] };

  rep.host = uxp.host ? { name: uxp.host.name, version: uxp.host.version } : null;

  // --- ticks. Premiere's internal time unit; everything else is derived from it.
  try {
    const one = ppro.TickTime.TIME_ONE_SECOND;
    rep.checks.ticksPerSecond = {
      ticksNumber: one.ticksNumber,
      ticks: one.ticks,
      assumed: TICKS_PER_SECOND,
      matches: one.ticksNumber === TICKS_PER_SECOND,
    };
    if (one.ticksNumber !== TICKS_PER_SECOND) {
      rep.notes.push("🔴 ticks per second is " + one.ticksNumber + ", not the assumed "
        + TICKS_PER_SECOND + ". Every exact frame rate computed in this panel is wrong. Fix the "
        + "constant before building anything.");
    }
  } catch (e) {
    rep.checks.ticksPerSecond = { error: errString(e) };
  }

  /* --- the 29.97 round trip. FrameRate.createWithValue takes a FLOAT, so a drop-frame rate has
   * to survive being expressed as one. If 29.97 does not come back as exactly 30000/1001 then
   * every frame position in a long programme drifts, which reads as a lip-sync fault ten minutes
   * in rather than as an error here. */
  const rates = [
    { label: "29.97 drop-frame", fps: [30000, 1001] },
    { label: "30.000", fps: [30, 1] },
    { label: "23.976", fps: [24000, 1001] },
    { label: "25", fps: [25, 1] },
  ];
  rep.checks.frameRateRoundTrip = [];
  for (const r of rates) {
    const num = r.fps[0], den = r.fps[1];
    const wantTpf = (TICKS_PER_SECOND * den) / num;
    const row = { label: r.label, fps: r.fps, wantTicksPerFrame: wantTpf,
                  wantIsInteger: Number.isInteger(wantTpf) };
    try {
      const naive = ppro.FrameRate.createWithValue(num / den);
      row.fromFloat = { value: naive.value, ticksPerFrame: naive.ticksPerFrame,
                        exactFromFloat: naive.ticksPerFrame === wantTpf };
      // can the integer be written?
      naive.ticksPerFrame = wantTpf;
      row.afterWrite = { ticksPerFrame: naive.ticksPerFrame, value: naive.value,
                         writable: naive.ticksPerFrame === wantTpf };

      const fr = naive;
      row.frames = {};
      for (const f of [1, 1000, 26688]) {
        const tt = ppro.TickTime.createWithFrameAndFrameRate(f, fr);
        const exact = (f * den) / num;
        row.frames["f" + f] = {
          seconds: tt.seconds,
          ticksNumber: tt.ticksNumber,
          exactSeconds: exact,
          deltaSeconds: tt.seconds - exact,
          ticksExact: tt.ticksNumber === f * wantTpf,
        };
      }
    } catch (e) {
      row.error = errString(e);
    }
    rep.checks.frameRateRoundTrip.push(row);
  }

  // --- video filters available by match name. This is the list that stops effect names
  //     from being guesses; the Motion/Opacity intrinsics are what the IR needs.
  try {
    const names = await ppro.VideoFilterFactory.getMatchNames();
    rep.checks.videoFilterCount = names.length;
    rep.checks.videoFilterMatchNames = names;
    rep.checks.lutLookingFilters = names.filter((n) => /lut|lumetri|color/i.test(n));
  } catch (e) {
    rep.checks.videoFilterMatchNames = { error: errString(e) };
  }

  // --- the active project and sequence, if any
  let project = null;
  try {
    project = await ppro.Project.getActiveProject();
    rep.checks.project = project ? { name: project.name, path: project.path } : null;
  } catch (e) {
    rep.checks.project = { error: errString(e) };
  }

  if (project) {
    // What is actually in the project, by path — so a later job can address media exactly rather
    // than by a name two bins can share.
    try {
      const idx = await indexProjectItems(project);
      rep.checks.projectItems = Object.keys(idx.byPath).map((p) => ({ path: p }));
      rep.checks.projectItemNames = Object.keys(idx.byName);
      // what a ProjectItem's `type` actually looks like at runtime, since the enum's
      // representation decides whether switching on it would work at all
      rep.checks.projectItemTypes = idx.observed;

      /* Which Constants actually exist at runtime, and what their members are.
       *
       * 🔴 Measured 26.5.1: `Constants.ProjectItemType` does NOT survive JSON — and separately,
       * a SEQUENCE project item reports `type === 1`, the same value a clip reports. So `type`
       * cannot tell a sequence from a clip, and code switching on that enum would mis-sort every
       * item while looking perfectly reasonable. The backend duck-types the cast result instead.
       * This dump is here so the next person does not have to rediscover it. */
      rep.checks.constants = {};
      for (const k of ["ProjectItemType", "MediaType", "TrackItemType", "InterpolationMode",
                       "ExportType", "ContentType"]) {
        const v = ppro.Constants ? ppro.Constants[k] : undefined;
        rep.checks.constants[k] = (v === undefined || v === null)
          ? String(v)
          : { typeOf: typeof v, members: Object.keys(v), values: Object.assign({}, v) };
      }
      if (Object.keys(idx.byPath).length === 0 && idx.observed.length > 0) {
        rep.notes.push("🔴 " + idx.observed.length + " project item(s) but NOT ONE resolved to a "
          + "media path. ClipProjectItem.cast is not working as assumed — a build would report "
          + "'imported media not found' for media that is present.");
      }
    } catch (e) {
      rep.checks.projectItems = { error: errString(e) };
    }

    try {
      const seq = await project.getActiveSequence();
      if (!seq) {
        rep.notes.push("No active sequence. Open one and re-probe to learn the intrinsic "
                     + "component and parameter names, and which index is V1.");
      } else {
        const settings = await seq.getSettings();
        rep.checks.sequence = {
          name: seq.name,
          videoTrackCount: await seq.getVideoTrackCount(),
          audioTrackCount: await seq.getAudioTrackCount(),
          timebase: await seq.getTimebase(),
          endTimeSeconds: (await seq.getEndTime()).seconds,
        };
        try {
          const vfr = settings.getVideoFrameRate();
          const rect = await settings.getVideoFrameRect();
          const asr = await settings.getAudioSampleRate();
          rep.checks.sequenceSettings = {
            videoFrameRate: vfr ? { value: vfr.value, ticksPerFrame: vfr.ticksPerFrame } : null,
            videoFrameRect: rect ? { width: rect.width, height: rect.height } : null,
            audioChannelCount: await settings.getAudioChannelCount(),
            audioChannelType: await settings.getAudioChannelType(),
            audioSampleRate: asr ? asr.value : null,
            editingMode: await settings.getEditingMode(),
          };
        } catch (e) {
          rep.checks.sequenceSettings = { error: errString(e) };
        }

        /* --- is getVideoTrack 0-based or 1-based? Ask both and compare what each reports its
         * own index to be. Getting this wrong puts every graphic on the wrong lane, and a
         * layered timeline still renders — just with the wrong thing on top. */
        rep.checks.trackIndexBase = [];
        for (const idx of [0, 1]) {
          try {
            const tr = await seq.getVideoTrack(idx);
            rep.checks.trackIndexBase.push({
              askedFor: idx,
              ok: !!tr,
              reportedIndex: tr ? await tr.getIndex() : null,
              name: tr ? tr.name : null,
              id: tr ? tr.id : null,
            });
          } catch (e) {
            rep.checks.trackIndexBase.push({ askedFor: idx, error: errString(e) });
          }
        }

        // --- the component chain of the first clip found. The point of the whole probe.
        rep.checks.firstClipChain = null;
        for (let idx = 0; idx < (await seq.getVideoTrackCount()); idx++) {
          const tr = await seq.getVideoTrack(idx);
          if (!tr) continue;
          const items = tr.getTrackItems(ppro.Constants.TrackItemType.CLIP, false);
          if (items && items.length) {
            const ti = items[0];
            rep.checks.firstClipChain = {
              trackIndexAsked: idx,
              clipName: await ti.getName(),
              startSeconds: (await ti.getStartTime()).seconds,
              durationSeconds: (await ti.getDuration()).seconds,
              inPointSeconds: (await ti.getInPoint()).seconds,
              outPointSeconds: (await ti.getOutPoint()).seconds,
              components: await describeChain(ti, false),
            };
            break;
          }
        }
        if (!rep.checks.firstClipChain) {
          rep.notes.push("No clips on any video track, so the intrinsic parameter names are "
                       + "still unknown. Drop one clip on V1 and re-probe.");
        }
      }
    } catch (e) {
      rep.checks.sequence = { error: errString(e) };
    }
  }

  // --- AME, which is how a render leaves the building
  try {
    const enc = ppro.EncoderManager.getManager();   // synchronous, unlike most of this API
    rep.checks.encoder = { isAMEInstalled: enc.isAMEInstalled };
  } catch (e) {
    rep.checks.encoder = { error: errString(e) };
  }

  /* --- which statics actually exist on this build.
   *
   * Some names in the type declarations are INSTANCE types, not factories — `Component`,
   * `ComponentParam`, `CompoundAction`, `PointKeyframe`, `Action` are typed without a `…Static`
   * companion, and read as `undefined` on the module. That is correct and not a missing feature:
   * a ComponentParam is only ever obtained from `component.getParam(i)`. Listing the expectation
   * beside the reading keeps a normal absence from looking like a broken install. */
  const MUST_EXIST = ["Project", "Sequence", "SequenceEditor", "SequenceUtils", "TickTime",
                      "FrameRate", "Keyframe", "PointF", "VideoFilterFactory", "TransitionFactory",
                      "EncoderManager", "ProjectItem", "VideoComponentChain", "VideoClipTrackItem",
                      "Utils", "Constants"];
  const INSTANCE_ONLY = ["Component", "ComponentParam", "CompoundAction", "Action",
                         "PointKeyframe"];
  rep.checks.statics = {};
  for (const w of MUST_EXIST) {
    const t = typeof ppro[w];
    rep.checks.statics[w] = t;
    if (t === "undefined") {
      rep.notes.push("🔴 ppro." + w + " is missing on this build. The backend calls it directly.");
    }
  }
  rep.checks.instanceOnlyTypes = {};
  for (const w of INSTANCE_ONLY) {
    rep.checks.instanceOnlyTypes[w] = typeof ppro[w];   // 'undefined' here is EXPECTED
  }

  return rep;
}

// -------------------------------------------------------------------------------------------
// op: probe_placement — WRITES. Settles the questions a read-only probe cannot.
// -------------------------------------------------------------------------------------------

async function opProbePlacement(job) {
  const rep = { ok: true, checks: {}, notes: [] };
  const project = await activeProject();
  const seq = await project.getActiveSequence();
  if (!seq) throw new Error("probe_placement needs an active sequence to write into");

  /* Use the SEQUENCE's own rate, not the job's. This op measures placement in a sequence that
   * already exists, and measuring frames against a rate the sequence does not use would make a
   * correct placement read as wrong. */
  const fr = (await seq.getSettings()).getVideoFrameRate();
  if (!fr) throw new Error("could not read the sequence frame rate");

  const items = await indexProjectItems(project);
  const item = (job.mediaPath && items.byPath[job.mediaPath])
            || (job.mediaName && items.byName[job.mediaName]);
  if (!item) {
    throw new Error("media not found in project. Give 'mediaPath' or 'mediaName'. Known names: "
      + Object.keys(items.byName).slice(0, 20).join(", "));
  }

  /* Is the out point INCLUSIVE or EXCLUSIVE? Resolve's endFrame is exclusive, and getting it
   * wrong makes every clip exactly one frame short — which survives casual review and destroys
   * frame-accurate graphics. Ask for a known length and measure what arrives. */
  const askFrames = job.askFrames || 100;
  const at = tickAt(job.recFrame || 0, fr);
  const editor = ppro.SequenceEditor.getEditor(seq);

  const inTick = tickAt(job.srcInFrame || 0, fr);
  const outTick = tickAt((job.srcInFrame || 0) + askFrames, fr);

  // The cast happens INSIDE the lock; a handle cast out here is dead by the time it is used.
  lockedTransaction(project, "film-shoot probe: set in/out", (c) => {
    const clip = castClip(item);
    if (!clip) throw new Error("project item " + item.name + " is not a clip project item");
    c.addAction(clip.createSetInOutPointsAction(inTick, outTick));
  });

  // read the in/out back before placing: if they did not take, the length below means nothing
  const clipForRead = castClip(item);
  const gotIn = await clipForRead.getInPoint(ppro.Constants.MediaType.VIDEO);
  const gotOut = await clipForRead.getOutPoint(ppro.Constants.MediaType.VIDEO);
  rep.checks.inOutAsSet = {
    askedInFrames: job.srcInFrame || 0,
    askedOutFrames: (job.srcInFrame || 0) + askFrames,
    readBackInTicks: gotIn ? gotIn.ticksNumber : null,
    readBackOutTicks: gotOut ? gotOut.ticksNumber : null,
    readBackInFrames: gotIn ? gotIn.ticksNumber / fr.ticksPerFrame : null,
    readBackOutFrames: gotOut ? gotOut.ticksNumber / fr.ticksPerFrame : null,
  };

  lockedTransaction(project, "film-shoot probe: overwrite", (c) => {
    c.addAction(editor.createOverwriteItemAction(item, at, job.videoTrack || 0,
                                                 job.audioTrack || 0));
  });

  const tr = await seq.getVideoTrack(job.videoTrack || 0);
  const placed = tr.getTrackItems(ppro.Constants.TrackItemType.CLIP, false);
  const mine = placed[placed.length - 1];

  /* Count frames in TICKS, not by multiplying seconds by a rate. Ticks are integers and the
   * division is exact; seconds x fps reintroduces the float rounding this whole design avoids. */
  const dur = await mine.getDuration();
  const gotFrames = dur.ticksNumber / fr.ticksPerFrame;
  rep.checks.outPointSemantics = {
    sequenceFps: fr.value,
    ticksPerFrame: fr.ticksPerFrame,
    askedFrames: askFrames,
    inOutSpanFrames: askFrames,
    deliveredDurationSeconds: dur.seconds,
    deliveredTicks: dur.ticksNumber,
    deliveredFrames: gotFrames,
    landsOnAWholeFrame: Number.isInteger(gotFrames),
    verdict: gotFrames === askFrames ? "EXCLUSIVE (in..out span == length)"
           : gotFrames === askFrames + 1 ? "INCLUSIVE (span + 1 == length)"
           : "NEITHER — got " + gotFrames + " frames for a span of " + askFrames,
  };
  const start = await mine.getStartTime();
  rep.checks.placedAt = {
    askedRecFrame: job.recFrame || 0,
    startFrames: start.ticksNumber / fr.ticksPerFrame,
    startSeconds: start.seconds,
    landedWhereAsked: start.ticksNumber / fr.ticksPerFrame === (job.recFrame || 0),
  };
  rep.notes.push("This op wrote into the active sequence. Undo it, or throw the sequence away.");
  return rep;
}


// -------------------------------------------------------------------------------------------
// op: call — a reflective RPC, so exploring the API needs no panel edit and no reload
//
// Every probe written today (the filter parameters, the runtime surface, the type probing) needed a
// new op, which meant editing this file and a manual Reload + Start listening in the UXP Developer
// Tool. That is a slow loop and it is the operator who pays for it.
//
// This op walks a declarative chain instead: each step either reads a property or calls a method,
// promises are awaited, and the result is described rather than returned raw. No `eval`, so
// `allowCodeGenerationFromStrings` stays false.
//
//   {"op":"call","chain":[{"get":"Project"},
//                         {"call":"getActiveProject"},
//                         {"call":"getActiveSequence"},
//                         {"call":"getVideoTrackCount"}]}
//
// `{"describe": true}` on a step returns that value's shape — typeof, keys, method names — which is
// how you explore an object you have never seen without guessing its members.
// -------------------------------------------------------------------------------------------

function describeValue(v, deep) {
  if (v === null) return { type: "null" };
  const t = typeof v;
  if (t !== "object" && t !== "function") return { type: t, value: v };
  const out = { type: t };
  if (t === "function") out.arity = v.length;
  if (Array.isArray(v)) {
    out.type = "array";
    out.length = v.length;
    out.items = v.slice(0, 40).map((x) => describeValue(x, false));
    return out;
  }
  const keys = [];
  try { for (const k of Object.keys(v)) keys.push(k); } catch (e) { /* proxy */ }
  try {
    const proto = Object.getPrototypeOf(v);
    if (proto && proto !== Object.prototype) {
      for (const k of Object.getOwnPropertyNames(proto)) {
        if (k !== "constructor" && keys.indexOf(k) === -1) keys.push(k);
      }
    }
  } catch (e) { /* */ }
  out.keys = keys.sort();
  if (deep) {
    out.members = {};
    for (const k of out.keys.slice(0, 200)) {
      let kt;
      try { kt = typeof v[k]; } catch (e) { kt = "threw"; }
      out.members[k] = kt;
      if (kt !== "object" && kt !== "function") {
        try { out.members[k] = kt + " = " + JSON.stringify(v[k]); } catch (e) { /* */ }
      }
    }
  }
  return out;
}

function resolveArg(a, results) {
  if (a && typeof a === "object" && !Array.isArray(a)) {
    if ("$ref" in a) return results[a.$ref];
    if ("$prev" in a) return results[results.length - 1];
    if ("$const" in a) {
      // a dotted path into the module, e.g. Constants.TrackItemType.CLIP
      let cur = ppro;
      for (const part of String(a.$const).split(".")) cur = cur[part];
      return cur;
    }
    if ("$tick" in a) {
      const fps = a.fps || [30000, 1001];
      return tickAt(a.$tick, exactFrameRate(fps));
    }
  }
  return a;
}

async function opCall(job) {
  const chain = job.chain;
  if (!Array.isArray(chain) || !chain.length) {
    throw new Error("call needs a non-empty 'chain' array");
  }
  const results = [];
  const steps = [];
  let cur = ppro;

  for (let i = 0; i < chain.length; i++) {
    const step = chain[i];
    const rec = { i: i };
    try {
      if (step.get !== undefined) {
        rec.get = step.get;
        cur = cur[step.get];
      } else if (step.call !== undefined) {
        rec.call = step.call;
        const fn = cur[step.call];
        if (typeof fn !== "function") {
          throw new Error(JSON.stringify(step.call) + " is " + typeof fn + ", not a function");
        }
        const args = (step.args || []).map((a) => resolveArg(a, results));
        rec.args = (step.args || []).length;
        cur = fn.apply(cur, args);
      } else if (step.index !== undefined) {
        rec.index = step.index;
        cur = cur[step.index];
      } else {
        throw new Error("step " + i + " has none of get / call / index");
      }

      if (cur && typeof cur.then === "function") {
        cur = await cur;
        rec.awaited = true;
      }
      results.push(cur);
      rec.result = describeValue(cur, !!step.describe);
      steps.push(rec);
    } catch (e) {
      rec.error = errString(e).split("\n")[0];
      steps.push(rec);
      return { ok: false, error: "step " + i + " failed: " + rec.error, steps: steps };
    }
  }
  return { ok: true, steps: steps, final: describeValue(cur, !!job.describe) };
}


// -------------------------------------------------------------------------------------------
// op: program — a tiny interpreter, so BUILD LOGIC LIVES IN PYTHON AND THIS FILE STOPS CHANGING
//
// Why this exists: every change to the build had to be made in here, and a change in here needs a
// manual reload in the UXP Developer Tool. That cost the operator a click for every iteration of
// mine, which is the wrong way round. The `call` op already removed that for *exploring* the API;
// this removes it for *building*, which was the remaining two thirds of the reloads.
//
// A program is a list of steps sharing a named environment. Each step is one of:
//
//   {"get": "Project", "as": "P"}                 read a property (of `env.on` or the module)
//   {"call": "getActiveProject", "on": "P", "as": "proj", "args": [...]}     call a method
//   {"set": "position", "on": "kf", "value": {...}}                          assign a property
//   {"cast": "ClipProjectItem", "on": "item", "as": "clip"}                  a narrowing cast
//   {"transaction": "label", "steps": [...]}      lockedAccess + executeTransaction
//   {"addAction": "act"}                          only valid inside a transaction
//   {"describe": "proj", "as": "info"}            shape of a value, for the report
//   {"require": "clip", "message": "..."}         fail loudly if a name is falsy
//
// Arguments and `value` resolve through `resolveArg`, so {"$": "name"} is an environment lookup,
// {"$tick": 12, "fps": [30000,1001]} is a TickTime, {"$const": "Constants.MediaType.AUDIO"} is a
// module constant.
//
// 🔴 The two rules from the header still bind, and the interpreter enforces what it can:
// a transaction's steps must be SYNCHRONOUS (no `call` inside it may return a promise — that is
// checked and thrown, because awaiting inside a transaction commits an empty one), and handles go
// stale on a project mutation, so acquire inside the transaction that uses them.
// -------------------------------------------------------------------------------------------

function progResolve(a, env) {
  if (a && typeof a === "object" && !Array.isArray(a)) {
    if ("$" in a) {
      if (!(a.$ in env)) throw new Error("no such name in the program: " + a.$);
      return env[a.$];
    }
    if ("$tick" in a) return tickAt(a.$tick, exactFrameRate(a.fps || [30000, 1001]));
    if ("$fps" in a) return exactFrameRate(a.$fps);
    if ("$const" in a) {
      let cur = ppro;
      for (const part of String(a.$const).split(".")) cur = cur[part];
      if (cur === undefined) throw new Error("no such constant: " + a.$const);
      return cur;
    }
    if ("$pointf" in a) return new ppro.PointF(a.$pointf[0], a.$pointf[1]);
    if ("$rectf" in a) {
      const r = new ppro.RectF();
      r.width = a.$rectf[0];
      r.height = a.$rectf[1];
      return r;
    }
    const out = {};
    for (const k of Object.keys(a)) out[k] = progResolve(a[k], env);
    return out;
  }
  if (Array.isArray(a)) return a.map((x) => progResolve(x, env));
  return a;
}

function progTarget(step, env) {
  if (step.on === undefined) return ppro;
  if (typeof step.on === "string") {
    if (!(step.on in env)) throw new Error("step targets unknown name " + JSON.stringify(step.on));
    return env[step.on];
  }
  return progResolve(step.on, env);
}

/* One step. `sync` is true inside a transaction, where a promise is a bug rather than a wait. */
function progStep(step, env, sync, trace) {
  const rec = { op: Object.keys(step)[0], as: step.as };

  if ("transaction" in step) {
    if (sync) throw new Error("a transaction cannot be nested inside a transaction");
    let ran = false;
    lockedTransaction(env.__project, step.transaction, (compound) => {
      ran = true;
      env.__compound = compound;
      for (const sub of step.steps || []) {
        trace.push(progStep(sub, env, true, trace));
      }
      delete env.__compound;
    });
    rec.ranSteps = (step.steps || []).length;
    rec.committed = ran;
    return rec;
  }

  if ("addAction" in step) {
    if (!env.__compound) throw new Error("addAction outside a transaction");
    const act = progResolve({ $: step.addAction }, env);
    if (!act) throw new Error("addAction on a falsy value: " + step.addAction);
    env.__compound.addAction(act);
    rec.added = true;
    return rec;
  }

  if ("require" in step) {
    const v = env[step.require];
    if (!v) throw new Error(step.message || ("required value is falsy: " + step.require));
    rec.ok = true;
    return rec;
  }

  if ("describe" in step) {
    const v = progResolve({ $: step.describe }, env);
    rec.value = describeValue(v, !!step.deep);
    if (step.as) env[step.as] = v;
    return rec;
  }

  let value;
  if ("get" in step) {
    value = progTarget(step, env)[step.get];
  } else if ("set" in step) {
    const t = progTarget(step, env);
    t[step.set] = progResolve(step.value, env);
    rec.set = step.set;
    return rec;
  } else if ("cast" in step) {
    const t = progTarget(step, env);
    value = ppro[step.cast].cast(t);
    if (!value) throw new Error("cast to " + step.cast + " returned nothing");
  } else if ("new" in step) {
    // Some of this API is constructed rather than fetched — `AddTransitionOptions` is a bare
    // constructor, and without this the interpreter could not express a transition at all.
    const ctor = ppro[step["new"]];
    if (typeof ctor !== "function") {
      throw new Error(step["new"] + " is " + typeof ctor + ", not a constructor");
    }
    value = new ctor(...(step.args || []).map((x) => progResolve(x, env)));
  } else if ("call" in step) {
    const t = progTarget(step, env);
    const fn = t[step.call];
    if (typeof fn !== "function") {
      throw new Error(step.call + " is " + typeof fn + " on this target, not a function");
    }
    value = fn.apply(t, (step.args || []).map((x) => progResolve(x, env)));
  } else {
    throw new Error("unknown step: " + JSON.stringify(step).slice(0, 120));
  }

  if (value && typeof value.then === "function") {
    if (sync) {
      throw new Error(step.call + " returned a promise inside a transaction. Awaiting there "
        + "commits an EMPTY transaction — hoist it out.");
    }
    return { rec: rec, promise: value };
  }
  if (step.as) env[step.as] = value;
  rec.type = typeof value;
  return rec;
}

async function opProgram(job) {
  const env = Object.create(null);
  const trace = [];
  const project = await activeProject();
  env.__project = project;
  env.project = project;

  const steps = job.steps;
  if (!Array.isArray(steps)) throw new Error("program needs a 'steps' array");

  for (let i = 0; i < steps.length; i++) {
    const step = steps[i];
    try {
      const r = progStep(step, env, false, trace);
      if (r && r.promise) {
        const v = await r.promise;
        if (step.as) env[step.as] = v;
        r.rec.awaited = true;
        r.rec.type = typeof v;
        trace.push(r.rec);
      } else {
        trace.push(r);
      }
    } catch (e) {
      return { ok: false, error: "step " + i + " (" + JSON.stringify(step).slice(0, 160) + "): "
               + errString(e).split("\n")[0], step: i, trace: trace };
    }
  }

  const named = {};
  for (const k of Object.keys(env)) {
    if (k.startsWith("__")) continue;
    named[k] = describeValue(env[k], false);
  }
  return { ok: true, steps: steps.length, trace: trace, env: named };
}


// -------------------------------------------------------------------------------------------
// op: probe_surface — the WHOLE runtime surface, including anything undeclared.
//
// The published TypeScript declarations are not the same thing as what the module exposes. QE
// (the old undocumented ExtendScript "quality engineering" DOM) never appeared in any declaration
// file, so its absence there says nothing. Dump every key the module actually has and let the
// caller diff it against the declared list; a hidden API shows up as the difference.
// -------------------------------------------------------------------------------------------

async function opProbeSurface(job) {
  const out = { ok: true, moduleKeys: [], candidates: {}, globals: {}, otherModules: {} };

  const keys = new Set();
  for (const k of Object.keys(ppro)) keys.add(k);
  try {
    for (const k of Object.getOwnPropertyNames(ppro)) keys.add(k);
  } catch (e) { /* proxy may refuse */ }
  let proto = null;
  try { proto = Object.getPrototypeOf(ppro); } catch (e) { proto = null; }
  if (proto && proto !== Object.prototype) {
    try { for (const k of Object.getOwnPropertyNames(proto)) keys.add(k); } catch (e) { /* */ }
  }
  out.moduleKeys = Array.from(keys).sort().map((k) => {
    let t;
    try { t = typeof ppro[k]; } catch (e) { t = "threw"; }
    return { name: k, type: t };
  });

  // QE and friends, by every name it has gone by
  const names = ["QE", "qe", "enableQE", "getQE", "QEProject", "QEApplication", "Advanced",
                 "AdvancedAPI", "AdvancedQE", "Internal", "Debug", "app", "Application",
                 "ColorSettings", "LUT", "Lut", "LutUtils", "ColorUtils"];
  for (const n of names) {
    let t;
    try { t = typeof ppro[n]; } catch (e) { t = "threw"; }
    out.candidates[n] = t;
    if (t === "function") {
      // a factory or an enabler: call it and report what comes back, without assuming success
      try {
        const r = ppro[n]();
        out.candidates[n + "()"] = r === undefined ? "undefined"
          : (r && typeof r === "object" ? Object.keys(r).slice(0, 40) : typeof r);
      } catch (e) {
        out.candidates[n + "()"] = "threw: " + errString(e).split("\n")[0];
      }
    }
  }

  for (const g of ["qe", "app", "QE", "premiere"]) {
    try {
      const v = (typeof globalThis !== "undefined") ? globalThis[g] : undefined;
      out.globals[g] = v === undefined ? "undefined"
        : (v && typeof v === "object" ? Object.keys(v).slice(0, 40) : typeof v);
    } catch (e) {
      out.globals[g] = "threw";
    }
  }

  for (const m of ["premierepro", "uxp", "premierepro-advanced", "premierepro-internal",
                   "premierepro-qe", "ppro", "os", "fs", "path"]) {
    try {
      const mod = require(m);
      out.otherModules[m] = mod ? Object.keys(mod).slice(0, 40) : "falsy";
    } catch (e) {
      out.otherModules[m] = "not available";
    }
  }
  return out;
}


// -------------------------------------------------------------------------------------------
// op: probe_filter — what a filter's parameters are, and what TYPE each one accepts
//
// `ComponentParam` has no type accessor. But `createKeyframe()` "throws if the passed in value is
// not compatible with the component param type", so offering it one of each kind and recording
// which are refused IS the type probe. A display name is not a type: matching "Input LUT" by name
// and handing it a string produced "Illegal Parameter type" — the name was right and the type was
// not, which is unknowable without asking.
// -------------------------------------------------------------------------------------------

async function opProbeFilter(job) {
  const mn = job.matchName;
  if (!mn) throw new Error("probe_filter needs 'matchName'");

  /* 🔴 `VideoFilterFactory.createComponent()` returns a `VideoFilterComponent`, which the
   * declarations define as `{}` — an OPAQUE HANDLE with no members. It has no getParam, no
   * getParamCount, not even getDisplayName. Parameters only exist once the filter is attached to a
   * chain and read back as a `Component` via `getComponentAtIndex()`.
   *
   * So this op has to WRITE: append the filter to a real clip, enumerate it, then remove it again.
   * It cleans up after itself, and reports whether the cleanup worked rather than assuming.
   */
  const project = await activeProject();
  const seq = await project.getActiveSequence();
  if (!seq) throw new Error("probe_filter needs an active sequence with at least one clip");

  let trackItem = null;
  const nTracks = await seq.getVideoTrackCount();
  for (let t = 0; t < nTracks && !trackItem; t++) {
    const tr = await seq.getVideoTrack(t);
    if (!tr) continue;
    const items = tr.getTrackItems(ppro.Constants.TrackItemType.CLIP, false);
    if (items && items.length) trackItem = items[0];
  }
  if (!trackItem) throw new Error("no clip on any video track to attach the filter to");

  const filter = await ppro.VideoFilterFactory.createComponent(mn);
  if (!filter) throw new Error("could not create " + mn);

  const chain = await trackItem.getComponentChain();
  const before = chain.getComponentCount();
  lockedTransaction(project, "film-shoot probe: append " + mn, (c) => {
    c.addAction(chain.createAppendComponentAction(filter));
  });

  const chain2 = await trackItem.getComponentChain();
  const after = chain2.getComponentCount();
  let comp = null;
  for (let i = 0; i < after; i++) {
    const cc = chain2.getComponentAtIndex(i);
    if ((await cc.getMatchName()) === mn) comp = cc;       // last match = the one just appended
  }
  if (!comp) {
    throw new Error("appended " + mn + " but it is not on the chain (count " + before + " -> "
      + after + ")");
  }

  const out = { ok: true, matchName: mn, displayName: await comp.getDisplayName(),
                clipName: await trackItem.getName(),
                chainCountBefore: before, chainCountAfter: after,
                paramCount: comp.getParamCount(), params: [] };

  for (let i = 0; i < out.paramCount; i++) {
    const p = comp.getParam(i);
    const row = { index: i, displayName: p.displayName };
    try { row.keyframable = await p.areKeyframesSupported(); } catch (e) { row.keyframable = null; }

    try {
      const sv = await p.getStartValue();
      if (sv && typeof sv === "object") {
        row.startValueKeys = Object.keys(sv).slice(0, 10);
        if ("value" in sv) {
          row.startValue = sv.value;
          row.startValueType = typeof sv.value;
        }
      } else {
        row.startValue = sv;
        row.startValueType = typeof sv;
      }
    } catch (e) {
      row.startValueError = errString(e).split("\n")[0];
    }

    // The actual type probe: which of these does createKeyframe accept?
    row.accepts = [];
    const candidates = [["number", 0.5], ["string", "probe"], ["boolean", true]];
    for (const pair of candidates) {
      try {
        p.createKeyframe(pair[1]);
        row.accepts.push(pair[0]);
      } catch (e) { /* refused: not this type */ }
    }
    out.params.push(row);
  }

  // put the clip back as it was, and say whether that worked
  if (!job.keep) {
    try {
      const chain3 = await trackItem.getComponentChain();
      let mine = null;
      for (let i = 0; i < chain3.getComponentCount(); i++) {
        const cc = chain3.getComponentAtIndex(i);
        if ((await cc.getMatchName()) === mn) mine = cc;
      }
      if (mine) {
        lockedTransaction(project, "film-shoot probe: remove " + mn, (c) => {
          c.addAction(chain3.createRemoveComponentAction(mine));
        });
      }
      const chain4 = await trackItem.getComponentChain();
      out.chainCountAfterCleanup = chain4.getComponentCount();
      out.cleanedUp = out.chainCountAfterCleanup === before;
      if (!out.cleanedUp) {
        out.cleanupNote = "the filter was NOT removed — the clip still carries it. Undo in "
                        + "Premiere, or the next render includes an effect nobody asked for.";
      }
    } catch (e) {
      out.cleanupError = errString(e).split("\n")[0];
      out.cleanedUp = false;
    }
  }
  return out;
}


// -------------------------------------------------------------------------------------------
// op: build — the IR goes in, a timeline comes out
// -------------------------------------------------------------------------------------------

async function opBuild(job) {
  const plan = job.plan;
  if (!plan) throw new Error("build needs a 'plan' (the NLE-neutral IR)");
  const report = { ok: true, placed: [], warnings: [] };

  const project = await activeProject();
  const fr = frameRateOf(plan);

  /* --- the sequence.
   *
   * A committed preset is still preferred, because it pins the raster, codec and audio layout
   * too. But `SequenceSettings.setVideoFrameRate()` exists, so unlike Resolve the rate is
   * writable and a missing preset is recoverable rather than fatal.
   *
   * Either way the rate is READ BACK and compared. Resolve's version of this bug shipped a
   * project that played at 0.8x with a contented green number in the toolbar: the build had asked
   * for 29.97, been refused, and not looked. */
  const seq = plan.sequence_preset
    ? await project.createSequenceWithPresetPath(plan.name, plan.sequence_preset)
    : await project.createSequence(plan.name);
  if (!seq) throw new Error("sequence creation returned nothing");
  await project.setActiveSequence(seq);

  const settings = await seq.getSettings();
  const wantW = plan.timeline_raster[0];
  const wantH = plan.timeline_raster[1];

  /* Set the RASTER as well as the rate. A default sequence is 1920x1080, and a vertical programme
   * built into it is letterboxed with no warning — the frame counts and the audio are all correct,
   * so nothing downstream notices. */
  const haveRate = settings.getVideoFrameRate();
  const haveRect = await settings.getVideoFrameRect();
  const rateWrong = !haveRate || haveRate.ticksPerFrame !== fr.ticksPerFrame;
  const rectWrong = !haveRect || haveRect.width !== wantW || haveRect.height !== wantH;

  if (rateWrong || rectWrong) {
    if (rateWrong) settings.setVideoFrameRate(fr);
    if (rectWrong) {
      const rect = new ppro.RectF();
      rect.width = wantW;
      rect.height = wantH;
      await settings.setVideoFrameRect(rect);
    }
    lockedTransaction(project, "film-shoot: sequence rate and raster", (c) => {
      c.addAction(seq.createSetSettingsAction(settings));
    });
  }

  const back = await seq.getSettings();
  const readBack = back.getVideoFrameRate();
  const readRect = await back.getVideoFrameRect();
  report.sequence = {
    name: seq.name,
    timebase: await seq.getTimebase(),
    videoTrackCount: await seq.getVideoTrackCount(),
    askedFps: plan.fps,
    askedTicksPerFrame: fr.ticksPerFrame,
    readBackFpsValue: readBack ? readBack.value : null,
    readBackTicksPerFrame: readBack ? readBack.ticksPerFrame : null,
    askedRaster: [wantW, wantH],
    readBackRaster: readRect ? [readRect.width, readRect.height] : null,
    fromPreset: !!plan.sequence_preset,
  };
  if (!readBack || readBack.ticksPerFrame !== fr.ticksPerFrame) {
    throw new Error("sequence frame rate is " + (readBack ? readBack.ticksPerFrame : "unreadable")
      + " ticks/frame, not the " + fr.ticksPerFrame + " asked for. Refusing to build on it — a "
      + "29.97 programme on a 30.000 timeline drifts a frame every 33 s and reads as a lip-sync "
      + "fault ten minutes in, not as a build error.");
  }
  if (!readRect || readRect.width !== wantW || readRect.height !== wantH) {
    throw new Error("sequence raster is "
      + (readRect ? readRect.width + "x" + readRect.height : "unreadable") + ", not the "
      + wantW + "x" + wantH + " asked for. Refusing to build — a vertical programme in a "
      + "landscape sequence is letterboxed silently, with every frame count still correct.");
  }

  // --- import. asNumberedStills=false matters: numbered files in one folder otherwise collapse
  //     into a single image-sequence clip and every caption but the first ceases to exist.
  const paths = Object.keys(plan.media).map((k) => plan.media[k]);
  const okImport = await project.importFiles(paths, true, await project.getRootItem(), false);
  if (!okImport) throw new Error("importFiles returned false");

  const index = await indexProjectItems(project);
  const editor = ppro.SequenceEditor.getEditor(seq);

  for (const track of plan.tracks) {
    if (track.kind !== "video") continue;
    const vIdx = trackIndexFor(track, plan);
    for (let n = 0; n < track.nodes.length; n++) {
      const node = track.nodes[n];
      const where = track.id + "#" + n;

      /* 🔴 Refuse an unimplemented node kind HERE, by name.
       *
       * `transition` is a node kind the IR defines and this backend has no code for. Without this
       * check it failed anyway — `plan.media[undefined]` is undefined, so it died with "imported
       * media not found", which sends whoever reads it looking for a missing file that was never
       * the problem. A capability table that says UNVERIFIED while the builder has nothing at all
       * is the same lie the grade gap was: checked against what the API can do, not what this file
       * does. */
      if (node.kind !== "clip" && node.kind !== "graphic") {
        throw new Error(where + ": node kind " + JSON.stringify(node.kind) + " is defined in the "
          + "IR but NOT IMPLEMENTED in this backend. Refusing rather than failing obscurely. "
          + "Implement it, or set it False in nle_ir.CAPABILITIES so the gate stops the plan "
          + "before it reaches here.");
      }

      const mediaPath = plan.media[node.media];
      const item = index.byPath[mediaPath];
      if (!item) {
        throw new Error(where + ": imported media not found in the project tree: " + mediaPath);
      }

      // in/out first, on the item, in its own transaction — see the header note.
      // The in/out setters live on ClipProjectItem, and the cast must happen inside the lock.
      const inTick = tickAt(node.src_in, fr);
      const outTick = tickAt(node.src_in + node.len, fr);   // exclusive until the probe says otherwise
      lockedTransaction(project, "film-shoot: in/out " + where, (c) => {
        const clip = castClip(item);
        if (!clip) {
          throw new Error(where + ": " + item.name + " is not a clip project item, so it has no "
            + "in/out points. Check what was imported.");
        }
        c.addAction(clip.createSetInOutPointsAction(inTick, outTick));
      });

      lockedTransaction(project, "film-shoot: place " + where, (c) => {
        c.addAction(editor.createOverwriteItemAction(item, tickAt(node.rec, fr), vIdx, vIdx));
      });

      // find what we just placed and assert it landed where it was asked to
      const tr = await seq.getVideoTrack(vIdx);
      const items = tr.getTrackItems(ppro.Constants.TrackItemType.CLIP, false);
      const placed = items[items.length - 1];
      const startSec = (await placed.getStartTime()).seconds;
      const wantSec = tickAt(node.rec, fr).seconds;
      if (Math.abs(startSec - wantSec) > 1e-6) {
        report.warnings.push(where + ": asked for " + wantSec + "s, landed at " + startSec
          + "s. A clip that SLID still tiles, so a no-gaps check cannot see this.");
      }

      if (node.disabled) {
        lockedTransaction(project, "film-shoot: disable " + where, (c) => {
          c.addAction(placed.createSetDisabledAction(true));
        });
      }

      if (node.crop) await applyCrop(project, placed, node, plan, report, where);
      if (node.fade) await applyFade(project, placed, node, plan, fr, report, where);
      if (node.keyframes) await applyKeyframes(project, placed, node, plan, fr, report, where);

      /* 🔴 A capability gate upstream is not the same as an implementation down here.
       *
       * The gate said Premiere could do `lut`, and this builder had NO grading code at all — so it
       * would have laid the whole programme ungraded and reported success. That is the exact
       * failure the gate exists to prevent, arriving from the other side: the plan was checked
       * against what the API *can* do, not against what this file *does*.
       *
       * So anything in a node that is not handled must stop the build. Refusing is cheap; a
       * plausible ungraded master is not. */
      if (node.grade) await applyGrade(project, placed, node, report, where);
      if (node.transition) await applyTransition(project, tr, placed, node, fr, report, where);
      for (const key of Object.keys(node)) {
        if (HANDLED_NODE_KEYS.indexOf(key) === -1 && key.charAt(0) !== "_") {
          throw new Error(where + ": the plan carries " + JSON.stringify(key) + ", which this "
            + "backend does not implement. Refusing rather than building without it. Implement it "
            + "or mark the feature unsupported in nle_ir.CAPABILITIES so the gate refuses the "
            + "plan before it reaches here.");
        }
      }

      report.placed.push({
        where: where,
        track: vIdx,
        startSeconds: startSec,
        durationSeconds: (await placed.getDuration()).seconds,
      });
    }
  }

  /* --- AUDIO: place what the plan asks for, and remove what it did not.
   *
   * 🔴 `createOverwriteItemAction(item, time, videoTrackIndex, audioTrackIndex)` places BOTH
   * streams. There is no video-only form. So a picture clip drags the camera's embedded audio onto
   * the timeline, and the render comes out with it: measured, a short whose mastered mix is
   * -14.0 LUFS delivered at -28.7 because raw camera audio arrived uninvited and nothing looked.
   * The Resolve builder has always asserted "no camera audio came in"; this one did not.
   */
  for (const track of plan.tracks) {
    if (track.kind !== "audio") continue;
    const aIdx = trackIndexFor(track, plan);
    for (let n = 0; n < track.nodes.length; n++) {
      const node = track.nodes[n];
      const where = track.id + "#" + n;
      const mediaPath = plan.media[node.media];
      const item = index.byPath[mediaPath];
      if (!item) throw new Error(where + ": audio media not found: " + mediaPath);
      const inTick = tickAt(node.src_in, fr);
      const outTick = tickAt(node.src_in + node.len, fr);
      lockedTransaction(project, "film-shoot: audio in/out " + where, (c) => {
        const clip = castClip(item);
        if (!clip) throw new Error(where + ": not a clip project item");
        c.addAction(clip.createSetInOutPointsAction(inTick, outTick));
      });
      lockedTransaction(project, "film-shoot: audio place " + where, (c) => {
        c.addAction(editor.createOverwriteItemAction(item, tickAt(node.rec, fr), aIdx, aIdx));
      });
      report.placed.push({ where: where, audioTrack: aIdx });
    }
  }

  const wantedAudio = new Set();
  for (const track of plan.tracks) {
    if (track.kind === "audio") wantedAudio.add(trackIndexFor(track, plan));
  }

  /* 🔴 A HANDLE IS INVALIDATED BY A PROJECT MUTATION, not merely by an await.
   *
   * The first version of this sweep reused the `editor` acquired at the top of the build and threw
   * "The script object is no longer valid" — because every clip placed since then changed the
   * project. That is the sharper form of the earlier lesson: `applyCrop` gets away with awaiting a
   * component chain and using it in the very next lock only because nothing mutates in between.
   *
   * So: collect the track handles AFTER the last placement (read-only, no mutation), then build the
   * selection and get a FRESH editor inside the lock.
   */
  const audioTracks = [];
  const nAudio = await seq.getAudioTrackCount();
  for (let i = 0; i < nAudio; i++) {
    if (wantedAudio.has(i)) continue;
    const tr = await seq.getAudioTrack(i);
    if (tr) audioTracks.push({ index: i, track: tr });
  }

  let strayCount = 0;
  for (const at of audioTracks) {
    strayCount += (at.track.getTrackItems(ppro.Constants.TrackItemType.CLIP, false) || []).length;
  }

  if (strayCount) {
    lockedTransaction(project, "film-shoot: remove camera audio", (c) => {
      const ed = ppro.SequenceEditor.getEditor(seq);            // fresh, inside the lock
      let sel = null;
      ppro.TrackItemSelection.createEmptySelection((s) => { sel = s; });
      if (!sel) throw new Error("could not create a selection to remove stray audio");
      let added = 0;
      for (const at of audioTracks) {
        const items = at.track.getTrackItems(ppro.Constants.TrackItemType.CLIP, false) || [];
        for (const it of items) {
          sel.addItem(it, true);
          added++;
        }
      }
      if (!added) throw new Error("counted " + strayCount + " stray audio item(s) but added none "
        + "to the selection");
      c.addAction(ed.createRemoveItemsAction(sel, false, ppro.Constants.MediaType.AUDIO, false));
    });
  }
  const strays = { count: strayCount };

  // assert it worked, rather than trusting the removal
  let left = 0;
  for (let i = 0; i < (await seq.getAudioTrackCount()); i++) {
    if (wantedAudio.has(i)) continue;
    const tr = await seq.getAudioTrack(i);
    if (!tr) continue;
    left += (tr.getTrackItems(ppro.Constants.TrackItemType.CLIP, false) || []).length;
  }
  report.audio = { requestedTracks: Array.from(wantedAudio), strays: strays.count,
                   straysRemaining: left };
  if (left) {
    throw new Error(left + " audio item(s) the plan never asked for are still on the timeline "
      + "after trying to remove " + strays.count + ". Refusing to call this build done — a render "
      + "would carry camera audio, which measures as a programme roughly 15 LU quieter than its "
      + "own mix and is easy to miss.");
  }

  await project.save();
  return report;
}

function trackIndexFor(track, plan) {
  // The probe settles whether getVideoTrack is 0- or 1-based; the plan carries 1-based indices
  // because that is what an editor sees on screen.
  const base = plan.track_index_base === undefined ? 0 : plan.track_index_base;
  return track.index - 1 + base;
}

/* Every node key this backend actually acts on. Anything else stops the build — see the note at
 * the call site. Keys beginning with "_" are commentary and are ignored by design. */
const HANDLED_NODE_KEYS = ["kind", "media", "rec", "len", "src_in", "src_raster",
                           "crop", "fade", "keyframes", "grade", "disabled", "speed",
                           "transition"];


/* Apply a LUT-only grade through Lumetri.
 *
 * `ClipProjectItem.createSetInputLUTIDAction(stringLUTID)` wants an opaque ID, not a path, and how
 * to obtain one for a cube you generated is unknown. `AE.ADBE Lumetri` is in this build's filter
 * list, so the route is a Lumetri component with its input-LUT parameter set.
 *
 * ⚠️ UNVERIFIED: whether that parameter takes a file path at all. If it does not, this throws and
 * says so rather than attaching an inert effect — an ungraded clip with a Lumetri badge on it looks
 * exactly like a graded one in a bin view.
 */
async function applyGrade(project, trackItem, node, report, where) {
  const grade = node.grade;
  if (grade.cdl) {
    throw new Error(where + ": the plan carries an ASC CDL. Premiere cannot set one from a script. "
      + "Bake it into the LUT (see bake_cdl_into_lut.py) and emit the plan with the baked cube.");
  }
  if (!grade.lut) {
    throw new Error(where + ": a grade with neither a cdl nor a lut — nothing to apply.");
  }

  const comp = await ppro.VideoFilterFactory.createComponent("AE.ADBE Lumetri");
  if (!comp) throw new Error(where + ": could not create AE.ADBE Lumetri");

  const chain = await trackItem.getComponentChain();
  lockedTransaction(project, "film-shoot: lumetri " + where, (c) => {
    c.addAction(chain.createAppendComponentAction(comp));
  });

  // find the component we just appended, and the parameter that takes a LUT
  const chain2 = await trackItem.getComponentChain();
  let lumetri = null;
  for (let i = 0; i < chain2.getComponentCount(); i++) {
    const cc = chain2.getComponentAtIndex(i);
    if ((await cc.getMatchName()) === "AE.ADBE Lumetri") lumetri = cc;
  }
  if (!lumetri) {
    throw new Error(where + ": Lumetri was appended but is not on the clip's component chain.");
  }

  const names = [];
  let target = null;
  for (let j = 0; j < lumetri.getParamCount(); j++) {
    const p = lumetri.getParam(j);
    const dn = p.displayName || "";
    names.push(dn);
    if (/input\s*lut|^lut$/i.test(dn)) target = { index: j, param: p };
  }
  if (!target) {
    throw new Error(where + ": Lumetri exposes no input-LUT parameter on this build. Its "
      + "parameters are: " + JSON.stringify(names) + ". The grade cannot be applied this way — "
      + "pre-grade the media instead, and declare it baked.");
  }

  lockedTransaction(project, "film-shoot: lut " + where, (c) => {
    c.addAction(target.param.createSetValueAction(
      target.param.createKeyframe(grade.lut), true));
  });

  const got = await target.param.getValueAtTime(ppro.TickTime.TIME_ZERO);
  report.placed.push({ where: where, grade: {
    lut: grade.lut, paramIndex: target.index, paramName: names[target.index],
    readBack: got && got.value,
  } });
  if (!got || String(got.value || "").indexOf(".cube") === -1) {
    report.warnings.push(where + ": set the LUT to " + grade.lut + " but it read back as "
      + JSON.stringify(got && got.value) + " — the grade may not have taken.");
  }
}


/* Transitions, attached to a clip's start or end.
 *
 * The IR names them by intent (`cross_dissolve`) rather than by vendor string, so the plan is not
 * carrying `AE.ADBE ...` around. This is the only place that mapping exists for Premiere.
 *
 * ⚠️ TWO WRONG TURNS HERE, both from reading the declarations instead of the runtime:
 *
 *   1. `hasTransition()` DOES NOT EXIST. The declarations carry a docstring saying "Returns true if
 *      trackItem has transition" — attached to `createRemoveVideoTransitionAction`, a mismatched
 *      comment in Adobe's own file. Calling it threw "is not a function".
 *   2. The position constants are NOT `Constants.TransitionPosition` (absent at runtime). They are
 *      `VideoTransition.TRANSITIONPOSITION_START` / `_END`.
 *
 * So verification counts TRANSITION track items — `getTrackItems(TrackItemType.TRANSITION, false)`.
 *
 * 🔴 That count is ALL it gives. Every TRANSITION item marshals as `null`, so its start and
 * duration are unreadable and getStartTime() on one throws. A control track reports length 0, so
 * the count is real evidence that a transition exists — and no evidence at all about where it sits
 * or how long it is. Do not claim otherwise in a report.
 *
 * ⚠️ A transition consumes handles from BOTH clips it spans, and a centred dissolve takes HALF its
 * duration from each side of the cut. `nle_ir` refuses one longer than its own clip, which is the
 * case it can see. A clip with no handle beyond the cut silently truncates the transition instead:
 * asking for 12 frames against an outgoing clip with zero tail handle rendered SIX, all on one
 * side, with no warning anywhere and the clip length unchanged. Nothing in this API reports that.
 * The only way to know a transition's real geometry is to RENDER and measure.
 */
const TRANSITION_MATCH = {
  cross_dissolve: "AE.ADBE Cross Dissolve New",
  dip_to_black: "AE.ADBE Dip To Black",
  dip_to_white: "AE.ADBE Dip To White",
};

async function applyTransition(project, track, trackItem, node, fr, report, where) {
  const durBefore = (await trackItem.getDuration()).ticksNumber;
  const before = (track.getTrackItems(ppro.Constants.TrackItemType.TRANSITION, false) || []).length;

  for (const side of ["in", "out"]) {
    const t = node.transition[side];
    if (!t) continue;
    const kind = t.type || "cross_dissolve";
    const match = TRANSITION_MATCH[kind];
    if (!match) {
      throw new Error(where + ": no Premiere match name for transition type "
        + JSON.stringify(kind) + ". Known: " + Object.keys(TRANSITION_MATCH).join(", "));
    }

    const applyToStart = side === "in";
    lockedTransaction(project, "film-shoot: transition " + side + " " + where, (c) => {
      const tr = ppro.TransitionFactory.createVideoTransition(match);
      if (!tr) throw new Error(where + ": createVideoTransition(" + match + ") returned nothing");
      const opts = new ppro.AddTransitionOptions();
      opts.setApplyToStart(applyToStart);
      opts.setDuration(tickAt(t.frames, fr));
      c.addAction(trackItem.createAddVideoTransitionAction(tr, opts));
    });
  }

  // Read back rather than trust: count the TRANSITION items on this track.
  const items = track.getTrackItems(ppro.Constants.TrackItemType.TRANSITION, false) || [];
  const asked = Object.keys(node.transition).filter((k) => node.transition[k]);
  const durAfter = (await trackItem.getDuration()).ticksNumber;

  // Null items are EXPECTED here (see the note above), so they are counted, not filed as errors.
  const found = [];
  let unreadable = 0;
  for (const it of items) {
    if (!it) { unreadable++; continue; }
    try {
      found.push({ startTicks: (await it.getStartTime()).ticksNumber,
                   durationTicks: (await it.getDuration()).ticksNumber });
    } catch (e) { found.push({ error: errString(e).split("\n")[0] }); }
  }

  report.placed.push({ where: where, transition: {
    asked: asked,
    transitionsOnTrackBefore: before, transitionsOnTrackAfter: items.length,
    added: items.length - before,
    found: found, unreadable: unreadable,
    durationTicksBefore: durBefore, durationTicksAfter: durAfter,
    clipLengthChanged: durBefore !== durAfter,
  } });

  if (items.length - before !== asked.length) {
    report.warnings.push(where + ": asked for " + asked.length + " transition(s) and the track "
      + "gained " + (items.length - before) + ". createAddVideoTransitionAction returned without "
      + "complaint, so the count is the only thing that knows.");
  }
  if (durBefore !== durAfter) {
    report.warnings.push(where + ": the clip's duration CHANGED when the transition was added ("
      + durBefore + " -> " + durAfter + " ticks). A transition eats handles; the cut is no longer "
      + "where the plan put it.");
  }
}


async function applyCrop(project, trackItem, node, plan, report, where) {
  const src = node.src_raster;
  const tl = plan.timeline_raster;
  const c = node.crop;
  const px = tl[0] / c[2];
  const scalePercent = 100 * px;
  const pos = [
    0.5 + (src[0] / 2 - (c[0] + c[2] / 2)) * px / tl[0],
    0.5 + (src[1] / 2 - (c[1] + c[3] / 2)) * px / tl[1],
  ];

  const chain = await trackItem.getComponentChain();
  let motion = null;
  for (let i = 0; i < chain.getComponentCount(); i++) {
    const comp = chain.getComponentAtIndex(i);
    const mn = await comp.getMatchName();
    if (/motion/i.test(mn) || /motion/i.test(await comp.getDisplayName())) { motion = comp; break; }
  }
  if (!motion) {
    throw new Error(where + ": no Motion component on the clip, so a crop cannot be applied. "
      + "Run probe to get this build's intrinsic component match names.");
  }

  const scaleP = await findParamByName(motion, "Scale");
  const posP = await findParamByName(motion, "Position");
  if (!scaleP || !posP) {
    throw new Error(where + ": Motion has no parameter named Scale and/or Position on this "
      + "build. The probe report lists the real names — do not guess an index.");
  }

  const kfScale = scaleP.param.createKeyframe(scalePercent);
  const kfPos = posP.param.createKeyframe(new ppro.PointF(pos[0], pos[1]));

  lockedTransaction(project, "film-shoot: crop " + where, (cc) => {
    cc.addAction(scaleP.param.createSetValueAction(kfScale, true));
    cc.addAction(posP.param.createSetValueAction(kfPos, true));
  });

  // read it back — a set that returned nothing looks exactly like a set that worked
  const gotScale = await scaleP.param.getValueAtTime(ppro.TickTime.TIME_ZERO);
  report.placed.push({ where: where, cropApplied: { askedScale: scalePercent,
    readBackScale: gotScale && gotScale.value, askedPosition: pos } });
}

async function applyFade(project, trackItem, node, plan, fr, report, where) {
  const chain = await trackItem.getComponentChain();
  let op = null;
  for (let i = 0; i < chain.getComponentCount(); i++) {
    const comp = chain.getComponentAtIndex(i);
    if (/opacity/i.test(await comp.getMatchName())
        || /opacity/i.test(await comp.getDisplayName())) { op = comp; break; }
  }
  if (!op) throw new Error(where + ": no Opacity component found; cannot fade.");
  const p = await findParamByName(op, "Opacity");
  if (!p) throw new Error(where + ": Opacity component has no 'Opacity' parameter on this build.");

  const supported = await p.param.areKeyframesSupported();
  if (!supported) throw new Error(where + ": Opacity reports keyframes unsupported.");

  /* 🔴 THE SHAPE GOES ON BOTH ENDS OF A RAMP, NOT JUST THE FAR ONE.
   *
   * A segment takes its interpolation from the keyframe it LEAVES FROM. The first version put the
   * mode only on the second point of each pair, so a read-back showed [0, 5, 0, 5]: every ramp
   * still departed from a LINEAR keyframe and the ease did nothing to the visible curve. It passed
   * a test asserting only that the modes "differ from the linear clip", which is how a weak
   * assertion launders a real defect.
   */
  const pts = [];
  const last = node.len - 1;
  if (node.fade.in) {
    pts.push({ f: 0, v: 0, shape: node.fade.in.shape },
             { f: node.fade.in.frames, v: 100, shape: node.fade.in.shape });
  }
  if (node.fade.out) {
    pts.push({ f: last - node.fade.out.frames, v: 100, shape: node.fade.out.shape },
             { f: last, v: 0, shape: node.fade.out.shape });
  }

  /* Keyframe times on a clip's effect: clip-relative or sequence-absolute? The plan says which,
   * because guessing puts a fade thousands of frames outside the clip where nothing moves and
   * nothing errors — the same failure shape as Fusion's COMPN_GlobalStart. */
  const base = plan.keyframe_time_base === "sequence" ? node.rec : 0;

  /* 🔴 A KEYFRAME CARRIES ITS OWN POSITION, AND `createKeyframe` DOES NOT SET IT.
   *
   * This asked for four opacity keyframes and got ONE. `createKeyframe(value)` returns a keyframe
   * at the default position, so every one landed at the same instant and each replaced the last —
   * a "fade" that is a single static value. `Keyframe.position` is a read/write TickTime; set it
   * before adding.
   *
   * And interpolation belongs on the keyframe too: `setTemporalInterpolationMode` with
   * `Keyframe.INTERPOLATION_MODE_*`. It is ASYNC, so it has to happen out here, before the lock —
   * which is why the keyframes are fully built first and only added inside it.
   */
  const built = [];
  for (const pt of pts) {
    const kf = p.param.createKeyframe(pt.v);
    kf.position = tickAt(base + pt.f, fr);
    const shaped = pt.shape && pt.shape !== "linear";
    try {
      await kf.setTemporalInterpolationMode(shaped
        ? ppro.Keyframe.INTERPOLATION_MODE_BEZIER
        : ppro.Keyframe.INTERPOLATION_MODE_LINEAR);
    } catch (e) {
      report.warnings.push(where + ": could not set interpolation on the keyframe at frame "
        + pt.f + " (" + errString(e).split("\n")[0] + ")");
    }
    built.push({ kf: kf, f: pt.f, shaped: !!shaped });
  }

  lockedTransaction(project, "film-shoot: fade " + where, (cc) => {
    cc.addAction(p.param.createSetTimeVaryingAction(true));
    for (const b of built) {
      cc.addAction(p.param.createAddKeyframeAction(b.kf));
    }
  });

  const times = p.param.getKeyframeListAsTickTimes();
  report.placed.push({ where: where, fade: {
    asked: pts.map((x) => x.f),
    readBackKeyframeCount: times ? times.length : 0,
    timeVarying: p.param.isTimeVarying(),
  } });
  if (!times || times.length !== pts.length) {
    report.warnings.push(where + ": asked for " + pts.length + " opacity keyframes, read back "
      + (times ? times.length : 0) + ". A fade with one keyframe does not fade.");
  }
}

async function applyKeyframes(project, trackItem, node, plan, fr, report, where) {
  const chain = await trackItem.getComponentChain();
  const base = plan.keyframe_time_base === "sequence" ? node.rec : 0;

  for (const set of node.keyframes) {
    let target = null;
    for (let i = 0; i < chain.getComponentCount() && !target; i++) {
      const comp = chain.getComponentAtIndex(i);
      const hit = await findParamByName(comp, set.param);
      if (hit) target = hit;
    }
    if (!target) {
      throw new Error(where + ": no component exposes a parameter named " + set.param
        + ". The probe report lists every parameter on this build by display name.");
    }
    if (!(await target.param.areKeyframesSupported())) {
      throw new Error(where + ": " + set.param + " does not support keyframes.");
    }

    // Position each keyframe before adding it — see the note in applyFade. Without this every
    // point lands at the same instant and the last one wins, so an animation becomes a constant.
    const pts = [];
    for (const pt of set.points) {
      const kf = target.param.createKeyframe(pt.value);
      kf.position = tickAt(base + pt.frame, fr);
      if (pt.interpolation && pt.interpolation !== "linear") {
        try {
          await kf.setTemporalInterpolationMode(ppro.Keyframe.INTERPOLATION_MODE_BEZIER);
        } catch (e) {
          report.warnings.push(where + ": interpolation on " + set.param + " frame " + pt.frame
            + " not set (" + errString(e).split("\n")[0] + ")");
        }
      }
      pts.push(kf);
    }

    lockedTransaction(project, "film-shoot: keyframes " + set.param + " " + where, (cc) => {
      cc.addAction(target.param.createSetTimeVaryingAction(true));
      for (const kf of pts) {
        cc.addAction(target.param.createAddKeyframeAction(kf));
      }
    });

    const times = target.param.getKeyframeListAsTickTimes();
    report.placed.push({ where: where, keyframes: {
      param: set.param, asked: set.points.length,
      readBack: times ? times.length : 0,
      timeVarying: target.param.isTimeVarying(),
    } });
    if (!times || times.length !== set.points.length) {
      report.warnings.push(where + ": " + set.param + " asked " + set.points.length
        + " keyframes, read back " + (times ? times.length : 0));
    }
  }
}

// -------------------------------------------------------------------------------------------
// op: readback — dump the active sequence so Python can diff it against the plan
// -------------------------------------------------------------------------------------------

async function opReadback(job) {
  const project = await activeProject();
  const seq = await project.getActiveSequence();
  if (!seq) throw new Error("no active sequence", (cc) => {
      cc.addAction(target.param.createSetTimeVaryingAction(true));
      for (const pt of set.points) {
        cc.addAction(target.param.createAddKeyframeAction(
          target.param.createKeyframe(pt.value)));
      }
    });

  const out = {
    ok: true,
    project: project.name,
    sequence: seq.name,
    timebase: await seq.getTimebase(),
    videoTrackCount: await seq.getVideoTrackCount(),
    audioTrackCount: await seq.getAudioTrackCount(),
    endTimeSeconds: (await seq.getEndTime()).seconds,
    tracks: [],
  };

  for (let i = 0; i < out.videoTrackCount; i++) {
    const tr = await seq.getVideoTrack(i);
    if (!tr) continue;
    const items = tr.getTrackItems(ppro.Constants.TrackItemType.CLIP, false);
    const nodes = [];
    for (const ti of items) {
      let mediaPath = null;
      try {
        const pi = await ti.getProjectItem();
        const clip = castClip(pi);
        mediaPath = clip ? await clip.getMediaFilePath() : null;
        if (!mediaPath) {
          // Do NOT swallow this. A readback that reports every media path as null makes a diff
          // against the plan pass without comparing anything.
          out.unresolvedMedia = (out.unresolvedMedia || 0) + 1;
        }
      } catch (e) {
        mediaPath = null;
        out.mediaPathErrors = (out.mediaPathErrors || []);
        out.mediaPathErrors.push(errString(e).split("\n")[0]);
      }
      nodes.push({
        name: await ti.getName(),
        mediaPath: mediaPath,
        startSeconds: (await ti.getStartTime()).seconds,
        endSeconds: (await ti.getEndTime()).seconds,
        durationSeconds: (await ti.getDuration()).seconds,
        inPointSeconds: (await ti.getInPoint()).seconds,
        outPointSeconds: (await ti.getOutPoint()).seconds,
        disabled: await ti.isDisabled(),
        speed: await ti.getSpeed(),

        components: job.with_components ? await describeChain(ti, !!job.with_keyframes)
                                       : undefined,
      });
    }
    // Transitions are their OWN track items, not a property of a clip: there is no
    // hasTransition() anywhere in this API (the declarations' docstring saying so is attached to
    // createRemoveVideoTransitionAction by mistake).
    //
    // 🔴 And listing them gives a COUNT AND NOTHING ELSE. Every TRANSITION track item marshals
    // across the UXP boundary as `null` — the array has the right length and null in every slot,
    // confirmed against a control track that reports length 0. So start and duration are not
    // knowable from here, and calling getStartTime() on one throws a TypeError. Report the count
    // and say plainly that the rest is unreadable, rather than filing the TypeError as a finding:
    // an error string sitting in a results list reads like evidence and is not.
    const transItems = tr.getTrackItems(ppro.Constants.TrackItemType.TRANSITION, false) || [];
    const trans = { count: transItems.length, unreadable: 0, items: [] };
    for (const it of transItems) {
      if (!it) { trans.unreadable++; continue; }
      try {
        trans.items.push({ startTicks: (await it.getStartTime()).ticksNumber,
                           durationTicks: (await it.getDuration()).ticksNumber });
      } catch (e) { trans.items.push({ error: errString(e).split("\n")[0] }); }
    }
    out.tracks.push({ askedIndex: i, reportedIndex: await tr.getIndex(),
                      name: tr.name, nodes: nodes, transitions: trans });
  }
  return out;
}

// -------------------------------------------------------------------------------------------
// op: render
// -------------------------------------------------------------------------------------------

async function opRender(job) {
  const project = await activeProject();
  const seq = await project.getActiveSequence();
  if (!seq) throw new Error("no active sequence");
  if (!job.output) throw new Error("render needs 'output'");
  if (!job.preset) throw new Error("render needs 'preset' (an .epr path)");

  const enc = ppro.EncoderManager.getManager();   // synchronous, unlike most of this API
  const type = job.queue_to_ame
    ? ppro.Constants.ExportType.QUEUE_TO_AME
    : ppro.Constants.ExportType.IMMEDIATELY;

  /* exportFull = true, deliberately. Sequence in/out points PERSIST, and anything that rendered
   * a short probe window earlier leaves them set — on the Resolve side that shipped a review
   * render of 8 frames out of 26688, reported Complete. */
  const ok = await enc.exportSequence(seq, type, job.output, job.preset, true);
  return {
    ok: !!ok,
    output: job.output,
    exportType: job.queue_to_ame ? "QUEUE_TO_AME" : "IMMEDIATELY",
    note: "\"Complete\" is not \"correct\" — measure the file's frame count, rate and loudness "
        + "against the programme before shipping it.",
  };
}

/* op: strip_audio — remove audio items from tracks the plan never asked for.
 *
 * 🔴 THE ONE PIECE OF THE BUILD THE `program` INTERPRETER CANNOT EXPRESS.
 *
 * `TrackItemSelection.createEmptySelection(cb)` hands the selection to a CALLBACK. A declarative
 * step list has no way to say "and then, inside that callback, do these things", so this stays in
 * the panel. It is a fair place to draw the line: this is a fixed primitive, not build logic — it
 * takes the set of audio tracks the plan wants and removes everything on the others, and it does
 * not change when a cut changes.
 *
 * It REPORTS the counts and refuses nothing. The decision to fail belongs to the caller, which is
 * the whole point of moving the build out here. See `premiere_program.py`.
 *
 * Why it must exist at all: `createOverwriteItemAction(item, time, vTrack, aTrack)` places BOTH
 * streams, with no video-only form, so every picture clip drags the camera's embedded audio in.
 * Measured once at -28.7 LUFS for a programme mixed to -14.0.
 */
async function opStripAudio(job) {
  const project = await activeProject();
  const seq = await project.getActiveSequence();
  if (!seq) throw new Error("strip_audio: no active sequence");

  const keep = {};
  for (const i of (job.keep || [])) keep[i] = true;

  // Collect the track handles AFTER the last placement — read-only, so nothing is invalidated —
  // and get a FRESH editor inside the lock, because every clip placed since mutated the project.
  const tracks = [];
  const n = await seq.getAudioTrackCount();
  for (let i = 0; i < n; i++) {
    if (keep[i]) continue;
    const tr = await seq.getAudioTrack(i);
    if (tr) tracks.push({ index: i, track: tr });
  }

  let strays = 0;
  for (const at of tracks) {
    strays += (at.track.getTrackItems(ppro.Constants.TrackItemType.CLIP, false) || []).length;
  }

  if (strays) {
    lockedTransaction(project, "film-shoot: remove camera audio", (c) => {
      const ed = ppro.SequenceEditor.getEditor(seq);
      let sel = null;
      ppro.TrackItemSelection.createEmptySelection((s) => { sel = s; });
      if (!sel) throw new Error("could not create a selection to remove stray audio");
      let added = 0;
      for (const at of tracks) {
        for (const it of (at.track.getTrackItems(ppro.Constants.TrackItemType.CLIP, false) || [])) {
          sel.addItem(it, true);
          added++;
        }
      }
      if (!added) {
        throw new Error("counted " + strays + " stray audio item(s) but added none to the selection");
      }
      c.addAction(ed.createRemoveItemsAction(sel, false, ppro.Constants.MediaType.AUDIO, false));
    });
  }

  // Read back rather than trust the removal.
  let remaining = 0;
  const after = await seq.getAudioTrackCount();
  for (let i = 0; i < after; i++) {
    if (keep[i]) continue;
    const tr = await seq.getAudioTrack(i);
    if (!tr) continue;
    remaining += (tr.getTrackItems(ppro.Constants.TrackItemType.CLIP, false) || []).length;
  }

  return { ok: true, keep: job.keep || [], strays: strays, straysRemaining: remaining,
           audioTrackCount: after };
}

// -------------------------------------------------------------------------------------------
// dispatch
// -------------------------------------------------------------------------------------------

const OPS = {
  ping: opPing,
  probe: opProbe,
  probe_placement: opProbePlacement,
  probe_filter: opProbeFilter,
  probe_surface: opProbeSurface,
  call: opCall,
  program: opProgram,
  build: opBuild,
  strip_audio: opStripAudio,
  readback: opReadback,
  render: opRender,
};

async function runJob(name) {
  let job;
  try {
    job = await readJson(name);
  } catch (e) {
    log("could not read " + name + ": " + errString(e));
    return;
  }
  const op = job.op;
  log("job " + name + " op=" + op);
  setState("running " + op);

  let result;
  const t0 = Date.now();
  try {
    if (!OPS[op]) throw new Error("unknown op " + op + " (known: " + Object.keys(OPS).join(", ") + ")");
    result = await OPS[op](job);
  } catch (e) {
    result = { ok: false, error: errString(e) };
    log("FAILED: " + errString(e));
  }
  result.op = op;
  result.elapsedMs = Date.now() - t0;
  result.panelVersion = PANEL_VERSION;

  const resultName = name.replace(/\.json$/, "") + ".result.json";
  try {
    await writeJson(resultName, result);
    log((result.ok ? "ok" : "error") + " -> " + resultName);
  } catch (e) {
    log("could not write result: " + errString(e));
  }
  setState(listening ? "listening" : "idle");
}

async function poll() {
  if (!listening || !jobFolder) return;
  try {
    const names = await listJobs();
    for (const n of names) {
      if (seen[n]) continue;
      seen[n] = true;
      await runJob(n);
    }
  } catch (e) {
    log("poll error: " + errString(e));
  }
}

// -------------------------------------------------------------------------------------------
// wiring
// -------------------------------------------------------------------------------------------

document.getElementById("pick").addEventListener("click", async () => {
  try { await pickFolder(); } catch (e) { log(errString(e)); }
});

document.getElementById("listen").addEventListener("click", async () => {
  if (!jobFolder) { log("pick a job folder first"); return; }
  listening = !listening;
  document.getElementById("listen").textContent = listening ? "Stop listening" : "Start listening";
  setState(listening ? "listening" : "idle");
  if (listening && !timer) timer = setInterval(poll, POLL_MS);
});

document.getElementById("probe").addEventListener("click", async () => {
  setState("probing");
  try {
    const rep = await opProbe({});
    let where = null;
    try {
      where = await writeReport("probe-report.json", rep);
      log("probe written to: " + where);
    } catch (e) {
      log("probe ran but could not be written: " + errString(e).split("\n")[0]);
    }
    // Always summarise into the log too, so a probe is never lost to a filesystem problem.
    const tps = (rep.checks.ticksPerSecond || {}).ticksNumber;
    const chain = rep.checks.firstClipChain;
    log("host " + ((rep.host || {}).version || "?") + " | ticks/s " + tps
        + " | filters " + (rep.checks.videoFilterCount || "?")
        + " | clip inspected: " + (chain ? "yes" : "NO"));
    if (chain) {
      for (const c of chain.components || []) {
        log("  " + c.matchName + " [" + (c.params || []).map((p) => p.displayName).join(", ") + "]");
      }
    }
    for (const n of rep.notes || []) log("  note: " + n);
  } catch (e) {
    log("probe failed: " + errString(e));
  }
  setState(listening ? "listening" : "idle");
});

document.getElementById("clear").addEventListener("click", () => {
  document.getElementById("log").textContent = "";
});

initFs().then(() => {
  log("film-shoot timeline panel " + PANEL_VERSION);
  log("host: " + (uxp.host ? uxp.host.name + " " + uxp.host.version : "unknown"));

  /* Start listening automatically.
   *
   * A UDT reload builds a fresh panel with `listening = false`, so every code change used to cost
   * the operator TWO clicks and cost me a round trip when I forgot to wait for the second one. The
   * job guard means a pending job runs at most once, and polling a folder changes nothing on its
   * own, so there is no reason to make a human ask for it.
   */
  if (jobFolder) {
    listening = true;
    document.getElementById("listen").textContent = "Stop listening";
    setState("listening");
    if (!timer) timer = setInterval(poll, POLL_MS);
    log("listening automatically — reload is now the only manual step");
  } else {
    log("no job folder, so not listening. Press 'Job folder…'.");
  }
  log("Run Probe API first on a new Premiere build. Nothing here is trusted until it has.");
});

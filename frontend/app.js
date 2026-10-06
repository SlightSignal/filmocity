/* Filmocity v0.2 — core. State = the project JSON on the server; every edit is a list of ops with actor/tool/reason (training data).
   panels.js adds Effect Controls, Color, Captions, Proposals, Audio, dialogs. Shared namespace: window.CR */
(() => {
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const CLIENT = "ui-" + Math.random().toString(36).slice(2, 8);
const PREFS = { vt: 1.0, at: 1.0, still: 5, title: 3, autosave: 25, thumbs: true };
const S = { prefs: PREFS, multiView: false, binSel: new Set(), proj: null, seq: null, pps: 60, t: 0, playing: false, rate: 1, tool: "select", snap: true, sel: new Set(), src: null, srcIn: null, srcOut: null,
  target: { video: "V1", audio: "A1" }, hist: [], hist_i: -1, lastRaf: 0, focus: "program", agentEvents: 0, useProxy: true, tall: {}, tlopt: { thumbs: true, waves: true, linked: true, captions: true, ghosts: true, opacity: true, through: true }, agentHot: {}, editPoint: null };
const timing = window.FilmocityTime;
const fmtTC = (t, fps = S.seq?.fps || 30, mode = S.seq?.timecode_format || 'ndf') => {
  try { return timing.formatTimecode(Math.max(0, t ?? 0), fps, mode); } catch { return "—"; }
};
const parseTC = (v, fps = S.seq?.fps || 30) => {
  try { const value = timing.parseTimecode(v, fps); if (value < 0) throw Error('Time must be nonnegative; use +/- in Go to Timecode for offsets'); return value; }
  catch (error) { status(error.message, 'err'); return NaN; }
};
function mediaRate(media, interpreted = false) {
  for (const value of [interpreted && media?.interpret_fps, media?.frame_rate, media?.fps, S.seq?.fps, 30]) {
    if (!value) continue; try { return timing.frameRate(value); } catch {}
  }
}
function sourceMonitorTime(media, logical) { return window.FilmocitySourceClock.nativeTime(media, logical); }
function sourceLogicalTime(media, native) { return window.FilmocitySourceClock.logicalTime(media, native); }
function sourceAddressKey(media, video) {
  return JSON.stringify([media.id, media.path, media.sub_in, media.interpret_fps, media.frame_rate, media.native_fps, media.fps, video.getAttribute?.("src")]);
}
function retainSourceAddress(video, native) {
  video._sourceFrameAddress = { media: S.src, key: sourceAddressKey(S.src, video),
    logical: sourceLogicalTime(S.src, native), decoder: video.currentTime };
}
function sourcePlayheadTime(video = $("#srcVideo")) {
  const retained = video._sourceFrameAddress;
  if (video.paused && retained?.media === S.src && retained.key === sourceAddressKey(S.src, video) &&
      Math.abs(video.currentTime - retained.decoder) <= 1e-6) return retained.logical;
  return sourceLogicalTime(S.src, video.currentTime);
}
function sourceDuration(media = S.src, video = $("#srcVideo")) {
  if (!media) return null;
  if (Number.isFinite(media.duration) && media.duration > 0) return media.duration;
  return Number.isFinite(video?.duration) && video.duration > 0 ? Math.max(0, sourceLogicalTime(media, video.duration)) : null;
}
function sourceLastFrame(video, fps) {
  if (!Number.isFinite(video.duration) || video.duration <= 0) return null;
  const count = video.duration * fps, epsilon = Math.max(1e-7, Math.abs(count) * Number.EPSILON * 4);
  return Math.max(0, Math.ceil(count - epsilon) - 1);
}
function sourceFrameBounds(video = $("#srcVideo"), media = S.src) {
  const fps = mediaRate(media), begin = sourceMonitorTime(media, 0), duration = sourceDuration(media, video);
  const end = duration == null ? null : Math.min(sourceMonitorTime(media, duration), Number.isFinite(video.duration) ? video.duration : Infinity);
  return { fps, begin, end, first: timing.displayFrame(begin, fps), last: end == null ? null : sourceLastFrame({ duration: end }, fps) };
}
function seekSourceTime(logical, pause = true) {
  const video = $("#srcVideo"); if (!S.src || !video || !Number.isFinite(logical)) return false;
  try {
    const bounds = sourceFrameBounds(video), duration = sourceDuration();
    let native = sourceMonitorTime(S.src, Math.max(0, logical));
    if (duration != null && logical >= duration && bounds.last != null) native = timing.fromFrames(bounds.last, bounds.fps);
    if (pause) video.pause?.();
    if (seekPreviewPicture(video, Math.max(bounds.begin, native), S.src, { playing: !video.paused, strictPlayback: true, begin: bounds.begin, end: bounds.end }) === null) throw Error("Source preview could not seek to this frame.");
    retainSourceAddress(video, Math.max(bounds.begin, native));
    return true;
  } catch (error) { status(error.message, "err"); return false; }
}
function stepSourceFrame(n) {
  const video = $("#srcVideo");
  if (!S.src || !video || !Number.isSafeInteger(n) || !Number.isFinite(video.currentTime)) return false;
  const { fps, begin, first, last } = sourceFrameBounds(video);
  let target = Math.max(first, timing.displayFrame(Math.max(begin, video.currentTime), fps) + n);
  if (last != null) target = Math.min(last, target);
  if (!Number.isSafeInteger(target)) return false;
  video.pause?.();
  const native = Math.max(begin, timing.fromFrames(target, fps));
  if (seekPreviewPicture(video, native, S.src, { playing: false, strictPlayback: true, begin, end: sourceFrameBounds(video).end }) === null) return false;
  retainSourceAddress(video, native);
  return true;
}
function remapSegments(c) { const k = [...(c.time_remap || [])].sort((a, b) => a.t - b.t); const segs = []; let sigma = 0; if (!k.length) return [[0, null, 1, 1, 0]];
  if (k[0].t > 0) { segs.push([0, k[0].t, k[0].v, k[0].v, 0]); sigma += k[0].v * k[0].t; }
  k.forEach((kf, i) => { if (i === k.length - 1) { segs.push([kf.t, null, kf.v, kf.v, sigma]); return; } const nx = k[i + 1], a = kf.v, b = kf.e === "hold" ? kf.v : nx.v; segs.push([kf.t, nx.t, a, b, sigma]); sigma += (nx.t - kf.t) * (a + b) / 2; }); return segs; }
function remapDuration(c) { const need = c.out - c.in_; for (const [t0, t1, a, b, sig] of remapSegments(c)) { if (t1 == null) return t0 + (need - sig) / Math.max(a, 1e-6); const gain = (t1 - t0) * (a + b) / 2; if (sig + gain >= need) { const d = t1 - t0, rem = need - sig; if (Math.abs(b - a) < 1e-9) return t0 + rem / Math.max(a, 1e-6); const k = (b - a) / d; return t0 + (-a + Math.sqrt(Math.max(a * a + 2 * k * rem, 0))) / k; } } return need; }
function sourceOffset(c, t) { if (c.hold) return 0; if (!c.time_remap?.length) return t * (c.speed || 1); for (const [t0, t1, a, b, sig] of remapSegments(c)) { if (t1 == null || t < t1) { const x = t - t0; const k = t1 == null ? 0 : (b - a) / (t1 - t0); return sig + a * x + k * x * x / 2; } } return c.out - c.in_; }
function speedAt(c, t) { if (c.hold) return 0; if (!c.time_remap?.length) return c.speed || 1; for (const [t0, t1, a, b] of remapSegments(c)) if (t1 == null || t < t1) return t1 == null ? a : a + (b - a) * (t - t0) / (t1 - t0); return 1; }
const clipDur = c => c.hold ? Math.max(c.out - c.in_, 0) : c.time_remap?.length ? remapDuration(c) : (c.out - c.in_) / Math.max(c.speed || 1, 1e-6), clipEnd = c => c.start + clipDur(c);
function bezierY(k0, k1, t) { const t0 = k0.t, v0 = k0.v, t1 = k1.t, v1 = k1.v, d = Math.max(t1 - t0, 1e-6); const [ox, oy] = k0.o || [0.33, 0], [ix, iy] = k1.i || [0.33, 0]; const p1x = t0 + Math.min(1, Math.max(0, ox)) * d, p1y = v0 + oy, p2x = t1 - Math.min(1, Math.max(0, ix)) * d, p2y = v1 + iy; let lo = 0, hi = 1; for (let n = 0; n < 30; n++) { const u = (lo + hi) / 2, x = (1 - u) ** 3 * t0 + 3 * (1 - u) ** 2 * u * p1x + 3 * (1 - u) * u * u * p2x + u ** 3 * t1; if (x < t) lo = u; else hi = u; } const u = (lo + hi) / 2; return (1 - u) ** 3 * v0 + 3 * (1 - u) ** 2 * u * p1y + 3 * (1 - u) * u * u * p2y + u ** 3 * v1; }
const seqDurOf = sq => { let end = 0; for (const tr of sq.tracks) for (const c of tr.clips) end = Math.max(end, clipEnd(c)); return end; }; const seqDur = () => seqDurOf(S.seq);
const frame = () => 1 / timing.frameRate(S.seq.fps || 30), uid = () => Math.random().toString(36).slice(2, 10);
function adoptTracks(applied) { if (!applied) return false; let changed = false; for (const [sid, tracks] of Object.entries(applied)) { const sq = S.proj.sequences.find(x => x.id === sid); if (!sq) continue; for (const [tid, clips] of Object.entries(tracks)) { const tr = sq.tracks.find(t => t.id === tid); if (!tr || !Array.isArray(clips)) continue; if (JSON.stringify(tr.clips) !== JSON.stringify(clips)) { tr.clips = clips; changed = true; } } } return changed; }
function toast(msg) { let box = $("#toasts"); if (!box) { box = document.createElement("div"); box.id = "toasts"; box.style.cssText = "position:fixed;right:14px;bottom:30px;display:flex;flex-direction:column;gap:6px;z-index:70;max-width:420px"; document.body.appendChild(box); } const t = document.createElement("div"); t.style.cssText = "background:#3a1f1f;border:1px solid var(--danger);color:#ffd7d7;padding:8px 10px;border-radius:4px;font-size:12px;display:flex;gap:8px;align-items:flex-start;box-shadow:0 8px 24px rgba(0,0,0,.5)"; t.innerHTML = `<span style="flex:1;user-select:text">${String(msg).replace(/</g, "&lt;")}</span><button style="padding:0 6px" title="Events panel">≡</button><button style="padding:0 6px">×</button>`; t.children[1].onclick = () => { window.CR && CR.showTab && CR.showTab("events"); }; t.children[2].onclick = () => t.remove(); box.appendChild(t); setTimeout(() => t.remove(), 15000); }
const status = (msg, cls) => { const el = $("#stMsg"); el.textContent = msg; el.className = cls || ""; if (cls === "err") toast(msg); };
const deep = o => JSON.parse(JSON.stringify(o));
function apiErrorMessage(body) {
  const detail = body && (body.errors || body.detail || body.error);
  const describe = value => typeof value === "string" ? value : String(value && (value.msg || value.message) || "");
  return (Array.isArray(detail) ? detail.map(describe).filter(Boolean).join("; ") : describe(detail)).slice(0, 500);
}
async function readApiResponse(response) {
  let body;
  try { body = await response.json(); }
  catch (error) { throw new Error(`${response.ok ? "Invalid server response" : "Request failed"} (HTTP ${response.status})`); }
  if (!response.ok) {
    const error = new Error(apiErrorMessage(body) || `Request failed (HTTP ${response.status})`);
    error.status = response.status; error.code = body && body.detail && body.detail.code;
    throw error;
  }
  return body;
}
async function uploadMediaFiles(files) {
  const selected = [...(files || [])]; let uploaded = 0;
  if (!selected.length) return { ok: true, uploaded: 0, total: 0 };
  for (const file of selected) {
    try {
      const data = new FormData(); data.append("file", file);
      await readApiResponse(await fetch("/api/media/upload", { method: "POST", body: data }));
      uploaded++;
    } catch (error) {
      const completed = uploaded ? `Uploaded ${uploaded} of ${selected.length} files. ` : "";
      const uncertain = Number.isInteger(error.status) ? "" : " Upload outcome is unconfirmed; check the bin before trying again.";
      status(`${completed}Upload stopped at ${file.name || "file"}: ${error.message || "Request failed"}.${uncertain}`, "err");
      return { ok: false, uploaded, total: selected.length };
    }
  }
  status(`Uploaded ${uploaded} file${uploaded === 1 ? "" : "s"}.`);
  return { ok: true, uploaded, total: selected.length };
}
const requestJson = (m, p, b) => fetch(p, { method: m, headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) }).then(readApiResponse);
const PROJECT_SWITCH_PATHS = new Set(["/api/projects/open", "/api/projects/new", "/api/projects/save_as", "/api/projects/duplicate", "/api/projects/sample"]);
const api = { get: p => fetch(p).then(readApiResponse), json: (m, p, b) => PROJECT_SWITCH_PATHS.has(p) ? switchProjectRequest(m, p, b) : requestJson(m, p, b) };
const trackOf = id => S.seq.tracks.find(t => t.id === id), clipById = id => { for (const tr of S.seq.tracks) { const c = tr.clips.find(x => x.id === id); if (c) return { c, tr }; } return null; };
const linkedAudioTrack = vtr => S.seq.tracks.filter(t => t.kind === "audio").sort((a, b) => a.index - b.index).find(t => t.index === vtr.index) || S.seq.tracks.filter(t => t.kind === "audio")[0];
const kfVal = (kfs, t, dflt) => { if (!kfs || !kfs.length) return dflt; const k = [...kfs].sort((a, b) => a.t - b.t); if (t <= k[0].t) return k[0].v; for (let i = 0; i < k.length - 1; i++) if (t < k[i + 1].t) { let p = (t - k[i].t) / Math.max(k[i + 1].t - k[i].t, 1e-6); const e = k[i].e || "linear"; if (e === "hold") return k[i].v; if (e === "bezier") return bezierY(k[i], k[i + 1], t); if (e === "ease") p = p * p * (3 - 2 * p); else if (e === "ease_in") p = p * p; else if (e === "ease_out") p = 1 - (1 - p) * (1 - p); return k[i].v + (k[i + 1].v - k[i].v) * p; } return k[k.length - 1].v; };

// ---------- sync ----------
const SAVE_STATES = new Map();
const PROJECT_LOADS = new Set();
const drafts = window.FilmocitySync.createDraftStore(() => window.localStorage, CLIENT);
let connectionStatus = "connecting…", loadGeneration = 0;
const contextKey = context => context ? JSON.stringify([context.workspace, context.project]) : "unloaded";
function projectSaveState() {
  const key = contextKey(S.context);
  if (!SAVE_STATES.has(key)) {
    let state;
    if (S.context) state = window.FilmocitySync.createSaveQueue(S.context, {
      send: (method, body) => api.json(method, "/api/project", body),
      changed: () => { if (state === projectSaveState()) renderSaveStatus(); },
      committed: async (result, item, last) => {
        if (state === projectSaveState()) S.context = { ...state.context };
        state.normalized ||= !!result.warnings?.length;
        if (last && state === projectSaveState() && state.normalized) {
          if (result.warnings?.length) status("Resolved overlap: " + result.warnings[0]);
          const refreshed = await loadProject(true, { acknowledged: state, revision: item.revision });
          if (refreshed) state.normalized = false;
        }
        if (state.pending === 1 && state.revision === item.revision && !state.normalized && !state.error) clearDraft(state);
        if (result.warning && state === projectSaveState()) status("Saved. " + result.warning, "err");
      },
    });
    else state = { pending: 0, revision: 0, error: "", saved: false };
    SAVE_STATES.set(key, state);
  }
  return SAVE_STATES.get(key);
}
function clearDraft(state) {
  if (state.draftKey) {
    try { drafts.remove(state.draftKey); state.draftKey = null; state.draftError = ""; }
    catch (error) { state.draftError = "Saved, but the browser draft could not be cleared: " + error.message; }
  }
}
function preserveDraft(state) {
  try { state.draftKey = drafts.save(state.context, S.proj, state.draftKey); state.draftError = ""; state.draftFresh = true; return true; }
  catch (error) {
    state.draftFresh = false;
    state.draftError = "Browser draft unavailable: " + (error.message || error);
    status(state.draftError + ". Keep this window open until saving succeeds, or download your editor copy from Recovery.", "err");
    return false;
  }
}
function renderSaveStatus() {
  const state = projectSaveState(), el = $("#stConn");
  el.textContent = state.error ? "save paused" : state.pending ? "saving…" : state.draftError ? "draft unavailable" : state.saved && connectionStatus === "connected" ? "saved" : connectionStatus;
  el.className = state.error || state.draftError ? "err" : "";
  el.title = state.error ? `${state.error} Open Recovery to review your editor copy.` : state.draftError || "";
}
async function loadProject(keepT, options = {}) {
  if (S.switching && !options.projectSwitch && !options.acknowledged) return false;
  if (S.gesture) { S.gesture.refreshNeeded = true; return false; }
  if (S.versionAction && !options.savedVersion) return false;
  const first = !S.proj, t = S.t, state = projectSaveState(), revision = state.revision, pending = state.pending, request = ++loadGeneration;
  let result;
  PROJECT_LOADS.add(request);
  try { result = await api.get("/api/project/state"); }
  catch (error) {
    if (request === loadGeneration && error.code === "project_recovery_required") {
      S.recoveryRequired = true; status("The project needs recovery: " + error.message, "err");
      window.FilmocityRecovery.open(CR);
    }
    throw error;
  } finally { PROJECT_LOADS.delete(request); }
  if (request !== loadGeneration || S.switching && !options.projectSwitch && !options.acknowledged) return false;
  if (result?.project_action_busy && (S.projectSwitchUncertain || options.projectRefresh)) throw new Error("A project copy is still running. Wait for it to finish, then refresh the editor.");
  const { project, context } = result || {};
  if (!project || !Array.isArray(project.sequences) || !project.sequences.length || !window.FilmocitySync.validContext(context)) throw new Error("Invalid project response");
  if (options.expectedContext && (!window.FilmocitySync.sameProject(options.expectedContext, context) || options.expectedContext.revision !== context.revision)) throw new Error("The project changed again during restore. Review it in Recovery.");
  if (options.expectedProject && !window.FilmocitySync.sameProject(options.expectedProject, context)) throw new Error("The active project changed during the workflow action");
  const same = window.FilmocitySync.sameProject(S.context, context);
  // A refresh is not consent to discard a pending or uncertain editor copy.
  const acknowledged = options.acknowledged === state && options.revision === state.revision && state.pending === 1;
  if (!options.recovery && !acknowledged && (state.pending || (same && (state.error || pending || revision !== state.revision)))) return false;
  if (!same && state.error && !state.draftFresh && !options.recovery) {
    status("Resolve your unsaved editor copy in Recovery before opening another project. You can download a copy there.", "err"); return false;
  }
  if (!same || options.recovery) { S.hist = []; S.histLabels = []; S.hist_i = -1; S.sel?.clear(); S.previewProposal = null; }
  if (options.recovery && same) clearDraft(state);
  if (!same || options.recovery) SAVE_STATES.delete(contextKey(context));
  if (!same || S.context?.revision !== context.revision || options.recovery || window.FilmocityProxyPreview?.sameOriginals?.(S.mediaAvailability, result.media_availability) === false) window.CR?.invalidateRenderedPreview?.();
  S.mediaAvailability = result.media_availability || {}; S.mediaStatus = Object.fromEntries(Object.entries(S.mediaAvailability).map(([id, value]) => [id, value.original_online]));
  if (!same && S.src) { S.src = null; S.srcIn = S.srcOut = null; FilmocityProxyPreview.replaceSource($("#srcVideo"), "", {retain: false}); $("#srcName").textContent = "Source"; $("#srcPreviewState").textContent = ""; window.CR?.updateSourcePresentation?.(); }
  if (!same) window.CR?.resetPreviewVoices?.();
  if (!same || S.context?.revision !== context.revision || options.recovery) window.FilmocityTasks?.invalidateReviews?.();
  const relink = window.CR?.currentSourceRelink?.(); if (relink && !relink.submitted && (S.proj !== project || !same || options.recovery)) window.CR.cancelSourceRelink(relink, "");
  S.proj = project; S.context = context; S.recoveryRequired = false; S.projectSwitchUncertain = false;
  if (S.previewProposal && !(project.proposals || []).some(p => p.id === S.previewProposal && p.items.some(it => it.status === "pending"))) S.previewProposal = null;
  const next = projectSaveState(); next.context = { ...context };
  renderSaveStatus();
  S.seq = S.proj.sequences.find(x => x.id === S.seqId) || S.proj.sequences[0]; S.seqId = S.seq.id;
  if (S.src) { S.src = S.proj.media[S.src.id] || null; const v = $("#srcVideo"); FilmocityProxyPreview.replaceSource(v, S.src ? sourceMediaUrl(S.src) : "", {report: sourcePreviewError}); if (!S.src) $("#srcName").textContent = "Source"; updateSourcePreviewState(); }
  if (keepT && same) S.t = t; else if (!same) S.t = 0;
  pushHist(); renderAll();
  if ($("#dlgExport").classList.contains("open")) CR.panels?.refreshExportPreflight();
  if (first && S.pendingWorkspace && CR.panels) { const w = S.pendingWorkspace; S.pendingWorkspace = null; try { CR.panels.setWorkspace(w, false); } catch (e) { } }
  if (first || !same) {
    try { if (drafts.list(context.workspace).drafts.some(d => d.context.project === context.project)) status("An unsaved browser draft is available. Open Recovery to review it."); }
    catch (error) { /* The first edit reports unavailable draft storage. */ }
  }
  return true;
}
async function flushSaves() {
  const state = projectSaveState();
  while (state.pending && state.lastSave) await state.lastSave;
  return state;
}
async function switchProjectRequest(method, path, body = {}) {
  if (S.projectSwitchUncertain) throw new Error("Refresh the editor from the project dialog before starting another project action");
  if (S.switching || S.commandPending || S.gesture) throw new Error("Wait for the current edit to finish, or press Escape, before switching projects");
  const origin = { ...S.context }, recoveryDeparture = !!S.recoveryRequired && path === "/api/projects/open";
  if (body._origin && !window.FilmocitySync.sameProject(body._origin, origin)) throw new Error("The active project changed. Reopen this dialog.");
  if (!recoveryDeparture && !window.FilmocitySync.validContext(origin)) throw new Error("Wait for the project to load before opening another project");
  window.CR?.cancelSourceRelink?.(undefined, "");
  window.CR?.invalidateRenderedPreview?.();
  S.switching = true;
  let result = null, submitted = false;
  try {
    const state = await flushSaves();
    if (state !== projectSaveState() || !recoveryDeparture && !window.FilmocitySync.sameProject(origin, state.context)) throw new Error("The active project changed while saving; reopen the project list");
    if (state.error && (!state.draftFresh || ['/api/projects/save_as','/api/projects/duplicate'].includes(path) || body.copy_media)) throw new Error("Resolve your unsaved editor copy in Recovery before copying this project; you can download a copy there");
    const payload = { ...body, _context: { ...state.context } }; delete payload._origin;
    const recovery = recoveryDeparture ? await api.get('/api/projects/recovery') : null;
    if (recovery) {
      if (recovery.current_valid || typeof recovery.project !== 'string' || typeof recovery.workspace !== 'string' || typeof recovery.current_sha256 !== 'string') throw new Error('Recovery project changed. Refresh before opening another project.');
      payload._recovery_origin = {project:recovery.project,workspace:recovery.workspace,current_sha256:recovery.current_sha256};
    }
    submitted = true; result = await requestJson(method, path, payload);
    if (!result || typeof result.id !== 'string' || !result.id) throw new Error('Invalid project action response');
    const target = { workspace: recovery?.workspace || state.context.workspace, project: result.id };
    if (result.context && !window.FilmocitySync.sameProject(result.context,target)) throw new Error('Project action returned a different owner');
    if (!await loadProject(false, { projectSwitch: true, expectedProject: target })) throw new Error('The editor could not adopt the opened project');
    if (result.warning) status(result.warning, 'err');
    return result;
  } catch (error) {
    const prefix = result?.id ? 'The project action completed, but the editor could not confirm the opened project. Use Refresh editor in the project dialog before repeating it. ' : submitted && ![400,403,404,409,422].includes(error.status) ? 'Project action was not confirmed. Use Refresh editor in the project dialog before repeating it. ' : 'Could not open project: ';
    if (submitted && (result?.id || ![400,403,404,409,422].includes(error.status) || error.code === 'project_changed')) S.projectSwitchUncertain = true;
    error.message = prefix + (error.message || error);
    status(error.message, 'err'); throw error;
  } finally { S.switching = false; }
}
async function refreshProjectSelection() {
  if (S.switching || S.commandPending || S.gesture) throw new Error('Wait for the current edit before refreshing the editor');
  S.commandPending = true;
  try {
    const state = await flushSaves();
    if (state.error && !state.draftFresh) throw new Error('Resolve or download the unsaved editor copy in Recovery first');
    if (!await loadProject(false, { projectRefresh: true })) throw new Error('The editor could not refresh; keep your current copy open');
    return S.context;
  } finally { S.commandPending = false; }
}
function switchSeq(id) { if (S.workflowBusy) { status("Wait for the workflow action to finish"); return; } if (S.gesture) { status("Finish the drag or press Escape before changing sequences."); return; } const sq = S.proj.sequences.find(x => x.id === id); if (!sq) return; if (sq !== S.seq) { window.CR?.cancelSourceRelink?.(undefined, ""); window.FilmocityTasks?.invalidateReviews?.(); } window.CR?.invalidateRenderedPreview?.(); S.seqId = id; S.seq = sq; S.sel.clear(); S.t = 0; renderAll(); }
function pushHist(label) { S.hist = S.hist.slice(0, S.hist_i + 1); S.histLabels = (S.histLabels || []).slice(0, S.hist_i + 1); S.hist.push(JSON.stringify(S.proj)); S.histLabels.push(label || "edit"); if (S.hist.length > 200) { S.hist.shift(); S.histLabels.shift(); } S.hist_i = S.hist.length - 1; }
function gotoHist(i) {
  if (i < 0 || i >= S.hist.length || !canEdit()) return;
  S.hist_i = i; S.proj = JSON.parse(S.hist[i]); S.seq = S.proj.sequences.find(x => x.id === S.seqId) || S.proj.sequences[0]; renderAll();
  return queueProjectSave("PUT", { ...S.proj, _actor: "human", _source: "history", _client: CLIENT });
}
function invalidateSourceEditHistory(old, patch) {
  const window = old.source_edit_window;
  if (Object.hasOwn(patch, "source_edit_window") || window?.version !== 1) return;
  const equal = (a, b) => a === b || !!a && !!b && typeof a === "object" && typeof b === "object" && Array.isArray(a) === Array.isArray(b) && Object.keys(a).length === Object.keys(b).length && Object.keys(a).every(k => Object.hasOwn(b, k) && equal(a[k], b[k]));
  const next = deep(window);
  if (Object.hasOwn(patch, "time_remap") && !equal(old.time_remap, patch.time_remap)) delete next.ramp;
  if (Object.hasOwn(patch, "keyframes") && !equal(old.keyframes?.["audio.duck_db"], patch.keyframes?.["audio.duck_db"])) delete next.duck;
  old.source_edit_window = next;
}
function applyLocal(ops) {
  for (const o of ops) {
    if (o.op === "set_mix") {
      const sq = S.proj.sequences.find(x => x.id === o.sequence);
      if (!sq) throw new Error("Mixer sequence no longer exists");
      const bus = o.track === null ? (sq.master ||= {}) : sq.tracks.find(x => x.id === o.track);
      if (!bus) throw new Error("Mixer track no longer exists");
      Object.assign(bus, deep(o.changes));
    }
    else if (o.op === "set_clip" || o.op === "remove_clip") { const sq = S.proj.sequences.find(x => x.id === o.sequence); const tr = sq?.tracks.find(x => x.id === o.track); if (!tr) continue;
      if (o.op === "set_clip") { const old = tr.clips.find(c => c.id === o.clip.id); if (old) { invalidateSourceEditHistory(old, o.clip); Object.assign(old, o.clip); } else tr.clips.push(o.clip); } else tr.clips = tr.clips.filter(c => c.id !== o.clip_id); }
    else { const parts = o.path.split("/").filter(Boolean).map(p => p.replace(/~1/g, "/").replace(/~0/g, "~")); let obj = S.proj; for (const p of parts.slice(0, -1)) obj = Array.isArray(obj) ? obj[+p] : obj[p]; const k = Array.isArray(obj) ? +parts.at(-1) : parts.at(-1);
      if (o.op === "set") obj[k] = o.value; else if (o.op === "insert") obj.splice(k, 0, o.value); else if (o.op === "remove") { Array.isArray(obj) ? obj.splice(k, 1) : delete obj[k]; } }
  }
  S.seq = S.proj.sequences.find(x => x.id === S.seqId) || S.proj.sequences[0]; S.seqId = S.seq.id;
}
function canEdit() {
  if (S.gesture) { status("Finish the drag or press Escape before editing."); return false; }
  if (!S.context || S.switching || S.commandPending || S.recoveryRequired || S.projectSwitchUncertain) { status("Wait for the project to finish loading before editing."); return false; }
  return true;
}
function captureGestureFields(object, fields) {
  const values = fields.map(key => ({ key, exists: Object.hasOwn(object, key), value: object[key] === undefined ? undefined : deep(object[key]) }));
  return () => { for (const item of values) { if (item.exists) object[item.key] = item.value; else delete object[item.key]; } };
}
function watchEditGesture(ev, move, up, rollback = () => {}, options = {}) {
  const state = projectSaveState();
  if (!canEdit() || state.error || (ev.button !== undefined && ev.button !== 0)) {
    rollback(); renderAll(); if (state.error) status("Resolve unsaved edits in Recovery before dragging.", "err"); return null;
  }
  const project = S.proj, sequence = S.seq, context = { ...S.context };
  const gesture = { refreshNeeded: PROJECT_LOADS.size > 0 };
  window.CR?.invalidateRenderedPreview?.();
  S.gesture = gesture; ++loadGeneration;
  if (S.playing && !options.keepPlaying) togglePlay(false, { commitTrim: false });
  const valid = () => S.proj === project && S.seq === sequence && state === projectSaveState() &&
    window.FilmocitySync.sameProject(context, S.context) && !state.error && !S.switching && !S.commandPending && !S.recoveryRequired && (!options.valid || options.valid());
  const clearPreview = () => { hideSnap(); trimReadout(null); S.previewT = null; S.slipTwoUp = null; S.lineDraft = null; };
  const attach = options.control ? window.FilmocityGestures.attachControlGesture : window.FilmocityGestures.attachMouseGesture;
  const controller = attach({ target: window, document, valid, move,
    commit(event) { S.gesture = null; up(event); },
    cancel(reason) { S.gesture = null; rollback(); clearPreview(); renderAll(); renderProgram(); status(reason); },
    failed(error, phase) {
      S.gesture = null;
      if (phase === "commit" && state === projectSaveState()) { preserveDraft(state); state.error = "Drag could not be completed: " + (error.message || error); }
      status("Drag failed: " + (error.message || error) + ". Review your edit in Recovery.", "err");
    },
    settled() {
      clearPreview(); renderSaveStatus();
      if (gesture.panelsNeeded) CR.panels?.render();
      options.settled?.();
      if (!gesture.refreshNeeded) return;
      Promise.resolve().then(async () => {
        await flushSaves();
        if (state !== projectSaveState() || state.error) return;
        if (S.gesture) { S.gesture.refreshNeeded = true; return; }
        if (await loadProject(true)) { state.normalized = false; if (!state.pending) clearDraft(state); }
      }).catch(error => status("Project refresh failed: " + error.message, "err"));
    },
  });
  gesture.cancel = controller.cancel;
  return controller;
}
async function queueProjectSave(method, body) {
  window.CR?.invalidateRenderedPreview?.();
  const state = projectSaveState(); preserveDraft(state);
  const promise = state.enqueue(method, body); state.lastSave = promise;
  const result = await promise;
  if (!result.ok && state === projectSaveState()) status("Save paused: " + result.error + ". Open Recovery to review your editor copy.", "err");
  return result.ok;
}
async function applyOps(ops, tool, reason, options = {}) {
  if (!ops.length) { renderAll(); return true; }
  if (!canEdit()) return false;
  // Capture before optimistic application: inserts and clips may otherwise share
  // mutable objects with later local edits while they wait in the queue.
  window.CR?.invalidateRenderedPreview?.();
  const captured = deep(ops);
  if (!options.alreadyApplied) applyLocal(ops);
  pushHist(reason || tool); renderAll();
  return queueProjectSave("PATCH", { ops: captured, actor: "human", tool, reason, client: CLIENT });
}
// Undo/redo use the same saved-project precondition and wait for queued edits.
async function historyAction(action) {
  if (!canEdit()) return;
  S.commandPending = true;
  try {
    const state = await flushSaves();
    if (state.error) throw new Error("Resolve unsaved edits in Recovery first");
    const result = await api.json("POST", "/api/" + action, { actor: "human", client: CLIENT, _context: { ...state.context } });
    if (!result.ok) { status(result.reason || "Nothing to " + action); return; }
    await loadProject(true);
    status(`${action === "undo" ? "Undo" : "Redo"}: ${result.undone || result.redone || ""}${result.warning ? ". " + result.warning : ""}`, result.warning ? "err" : "");
  } catch (error) { status(action + " failed: " + (error.message || error), "err"); }
  finally { S.commandPending = false; }
}
async function withSavedProject(action) {
  if (!canEdit()) throw new Error("Wait for the current project action to finish");
  const origin = { ...S.context };
  S.commandPending = true;
  try {
    const state = await flushSaves();
    if (state.error) throw new Error("Resolve unsaved edits in Recovery first");
    if (state !== projectSaveState() || !window.FilmocitySync.sameProject(origin, state.context)) throw new Error("The active project changed. Refresh versions.");
    return await action(state);
  } finally { S.commandPending = false; }
}
function workflowRequest(request, basis) {
  return window.FilmocityWorkflowTransaction.run({
    withSavedProject, sync: window.FilmocitySync, sequence: () => S.seq?.id,
    preserve: preserveDraft, clear: clearDraft, changed: renderSaveStatus,
    busy: value => { S.versionAction = value; S.workflowBusy = value; if (value && S.playing) togglePlay(false); },
    reload: context => loadProject(false, { savedVersion: true, expectedProject: context }),
  }, request, basis).then(result => {
    if (result.sequence) switchSeq(result.sequence);
    return result;
  });
}
function previewAudioDucking(body, basis) {
  return withSavedProject(async state => {
    if (!window.FilmocitySync.sameProject(basis, state.context) || basis.sequence !== S.seq?.id) throw new Error('The sequence changed. Reopen Auto-duck music.');
    const result = await api.json('POST', '/api/audio/duck_all', { ...body, sequence: basis.sequence, preview: true, _context: { ...state.context } });
    if (result?.ok !== true || result.preview !== true || !result.plan || result.sequence !== basis.sequence || result.context?.revision !== state.context.revision || !window.FilmocitySync.sameProject(state.context, result.context)) throw new Error('The ducking review was not confirmed');
    return result;
  });
}
function applyAudioDucking(body, reviewed) {
  return workflowRequest(context => api.json('POST', '/api/audio/duck_all', { ...body, sequence: reviewed.sequence, preview: false,
    preview_plan: reviewed.plan, _context: context, actor: 'human' }), { ...reviewed.context, sequence: reviewed.sequence });
}
function workflowAction(action, body, basis) {
  return workflowRequest(context => api.json('POST', action === 'transcribe' ? '/api/workflow/transcribe' : '/api/workflow/action',
    { ...body, action, sequence: basis.sequence, _context: context, actor: 'human', client: CLIENT }), basis);
}
async function startTranscription(body = {}, basis = { ...S.context, sequence: S.seq?.id }) {
  const requestId = crypto.randomUUID().replaceAll('-', '');
  return withSavedProject(async state => {
    if (!window.FilmocitySync.sameProject(basis, state.context) || basis.revision !== state.context.revision || basis.sequence !== S.seq?.id) throw new Error('The edit changed. Refresh this step before starting transcription.');
    let result;
    try {
      result = await api.json('POST', '/api/tasks/transcribe', { model: body.model || 'base', sequence: basis.sequence, _context: { ...state.context }, request_id: requestId });
      if (result?.ok !== true || !result.task?.id || !window.FilmocitySync.sameProject(state.context, result.context)) throw new Error('Invalid task submission reply');
    } catch (error) {
      if (![400,401,403,404,409,422,501].includes(error.status)) throw new Error('Task submission was not confirmed. Open Tasks before starting again. ' + error.message);
      throw error;
    }
    window.FilmocityTasks?.open(); return result;
  });
}
async function startProjectPackage(mode, path, basis = { ...S.context }) {
  if (!['package','package_import'].includes(mode)) throw new Error('Unknown package action');
  const requestId = crypto.randomUUID().replaceAll('-', '');
  return withSavedProject(async state => {
    if (!window.FilmocitySync.sameProject(basis, state.context)) throw new Error('The project changed. Reopen the package dialog.');
    let result;
    try {
      result = await api.json('POST', '/api/tasks/' + mode, { path, _context: { ...state.context }, request_id: requestId });
      if (result?.ok !== true || !result.task?.id || !window.FilmocitySync.sameProject(state.context, result.context)) throw new Error('Invalid package submission reply');
    } catch (error) {
      if (![400,401,403,404,409,422].includes(error.status)) throw new Error('Package submission was not confirmed. Open Tasks before starting again. ' + error.message);
      throw error;
    }
    if (window.FilmocitySync.sameProject(state.context,S.context)) window.FilmocityTasks?.open();
    return result;
  });
}
async function startMediaCollection(basis = { ...S.context }) {
  const requestId = crypto.randomUUID().replaceAll('-', '');
  return withSavedProject(async state => {
    if (!window.FilmocitySync.sameProject(basis, state.context)) throw new Error('The project changed. Reopen Collect original media.');
    let result;
    try {
      result = await api.json('POST', '/api/tasks/collect', { _context: { ...state.context }, request_id: requestId });
      if (result?.ok !== true || !result.task?.id || !window.FilmocitySync.sameProject(state.context, result.context)) throw new Error('Invalid collection submission reply');
    } catch (error) {
      if (![400,401,403,404,409,422].includes(error.status)) throw new Error('Collection submission was not confirmed. Open Tasks before starting again. ' + error.message);
      throw error;
    }
    if (window.FilmocitySync.sameProject(state.context,S.context)) window.FilmocityTasks?.open();
    return result;
  });
}
function applyMediaCollection(identity, basis) {
  return workflowRequest(context => api.json('POST', '/api/tasks/' + encodeURIComponent(identity) + '/apply', { _context: context, actor: 'human' }), basis);
}
function applyBackgroundTranscript(identity, basis) {
  return workflowRequest(context => api.json('POST', '/api/tasks/' + encodeURIComponent(identity) + '/apply', { _context: context, actor: 'human' }), basis);
}
function retryBackgroundTask(identity) {
  return withSavedProject(state => api.json('POST', '/api/tasks/' + encodeURIComponent(identity) + '/retry', { _context: { ...state.context }, request_id: crypto.randomUUID().replaceAll('-', '') }));
}

async function prepareMedia(identity) {
  const basis={...S.context};
  const result=await withSavedProject(state=>{
    if(!window.FilmocitySync.sameProject(basis,state.context))throw new Error('The active project changed.');
    return api.json('POST','/api/tasks/media/'+encodeURIComponent(identity)+'/prepare',{_context:{...state.context}});
  });
  window.FilmocityTasks?.open();return result;
}

function importXml(xml, basis) {
  return workflowRequest(context => api.json('POST', '/api/import/fcpxml', {xml, _context:context, actor:'human'}), basis);
}

function workflowUpload(files, basis) {
  const chosen = Array.from(files);
  return workflowRequest(async context => {
    const data = new FormData(); chosen.forEach(file => data.append('files', file));
    return readApiResponse(await fetch('/api/workflow/import', { method: 'POST', headers: { 'X-Filmocity-Context': JSON.stringify(context) }, body: data }));
  }, basis);
}

async function savedVersionCatalog(kind) {
  return withSavedProject(async state => {
    const result = await api.get("/api/projects/versions?kind=" + encodeURIComponent(kind));
    if (result?.kind !== kind || !Array.isArray(result.versions) || !Array.isArray(result.unavailable) ||
        !window.FilmocitySync.sameProject(state.context, result.context) || result.context.revision !== state.context.revision || state !== projectSaveState()) {
      throw new Error("The saved project changed. Reload the editor, then refresh versions.");
    }
    return result;
  });
}
async function saveSnapshot(label) {
  return withSavedProject(async state => {
    const result = await api.json("POST", "/api/snapshot", { label, actor: "human", sequence: S.seq.id, client: CLIENT, _context: { ...state.context } });
    if (result?.ok !== true || !result.snapshot || !window.FilmocitySync.sameProject(result.context, state.context) || result.context.revision !== state.context.revision) {
      throw new Error("The server did not confirm this snapshot. Refresh versions before trying again.");
    }
    return result;
  });
}
async function restoreSavedVersion(kind, selected, context) {
  const choice = deep(selected), expected = deep(context);
  return withSavedProject(async state => {
    if (!window.FilmocitySync.sameProject(expected, state.context) || expected.revision !== state.context.revision) throw new Error("The project changed. Refresh versions before restoring.");
    if (!["snapshots", "backups"].includes(kind) || !choice?.file || !choice.sha256) throw new Error("Select a saved version first");
    let result;
    preserveDraft(state); S.versionAction = true;
    if (S.playing) togglePlay(false);
    try {
      result = await api.json("POST", `/api/${kind}/restore`, { [kind === "snapshots" ? "name" : "file"]: choice.file, sha256: choice.sha256, _context: expected, actor: "human", client: CLIENT });
      if (result?.ok !== true || !window.FilmocitySync.validContext(result.context) || !window.FilmocitySync.sameProject(expected, result.context) || result.sha256 !== choice.sha256 || result.file !== choice.file || !result.preserved) throw new Error("The server did not confirm the selected restore");
      S.hist = []; S.histLabels = []; S.hist_i = -1; S.sel?.clear(); S.previewProposal = null; S.t = 0;
      if (!await loadProject(false, { savedVersion: true, expectedContext: result.context })) throw new Error("The editor could not refresh");
      clearDraft(state); renderSaveStatus();
      return result;
    } catch (error) {
      if (!result && [400, 401, 403, 404, 409, 422].includes(error.status)) clearDraft(state);
      else {
        state.error = (result?.ok ? "Restored, but the editor could not refresh: " : "Restore outcome not confirmed: ") + (error.message || error);
        renderSaveStatus();
        throw new Error(state.error + ". Open Recovery before continuing; do not repeat the restore.");
      }
      throw error;
    } finally { S.versionAction = false; }
  });
}
async function prepareProposalPreview(proposal, items) {
  if (!canEdit()) throw new Error("Wait for the current project action to finish");
  const origin = { ...S.context }, ids = items.map(item => item.id);
  S.commandPending = true;
  let view;
  try {
    const state = await flushSaves();
    if (state.error) throw new Error("Resolve unsaved edits in Recovery first");
    if (!window.FilmocitySync.sameProject(origin, state.context) || state !== projectSaveState()) throw new Error("The active project changed. Review proposals again.");
    view = await api.json("POST", `/api/proposals/${encodeURIComponent(proposal)}/preview`, { items: ids, _context: { ...state.context } });
    if (view?.ok !== true || !view.id || !view.plan || !Array.isArray(view.sequences) || !view.sequences.length ||
        !window.FilmocitySync.validContext(view.context) || !window.FilmocitySync.sameProject(origin, view.context) ||
        view.context.revision !== state.context.revision || state !== projectSaveState() || state.pending || state.error ||
        view.proposal !== proposal || JSON.stringify(view.items) !== JSON.stringify(ids)) throw new Error("The project changed or the preview could not be verified. Preview again.");
    return view;
  } catch (error) {
    if (view?.id) api.json("DELETE", `/api/proposals/preview/${encodeURIComponent(view.id)}`).catch(() => {});
    throw error;
  } finally { S.commandPending = false; }
}
async function proposalDecision(proposal, items, decision, preview = null) {
  if (!canEdit()) return false;
  const captured = deep(items), origin = { ...S.context };
  S.commandPending = true;
  try {
    const state = await flushSaves();
    if (state.error) throw new Error("Resolve unsaved edits in Recovery first");
    if (!window.FilmocitySync.sameProject(origin, state.context) || state !== projectSaveState()) throw new Error("The active project changed. Review proposals again.");
    if (preview && (preview.context.revision !== state.context.revision || !window.FilmocitySync.sameProject(preview.context, state.context) ||
        preview.proposal !== proposal || JSON.stringify(preview.items) !== JSON.stringify(captured.map(item => item.id)))) throw new Error("The reviewed project changed. Preview again before accepting.");
    const result = await api.json("POST", `/api/proposals/${encodeURIComponent(proposal)}/decide`, { items: captured, decision, client: CLIENT, _context: { ...state.context },
      ...(preview ? { _preview: { id: preview.id, plan: preview.plan } } : {}) });
    if (result?.ok !== true || !window.FilmocitySync.validContext(result.context) || !window.FilmocitySync.sameProject(origin, result.context)) throw new Error("The server did not confirm this proposal decision");
    S.previewProposal = null;
    if (!await loadProject(true)) throw new Error("Decision saved, but the editor could not refresh. Reload before continuing.");
    status(result.warning || `${decision === "accept" ? "Accepted" : "Rejected"} ${captured.length} proposal item${captured.length === 1 ? "" : "s"}${result.warnings?.length ? ". " + result.warnings.join("; ") : ""}`, result.warning ? "err" : "");
    return true;
  } catch (error) {
    status("Proposal decision not confirmed: " + (error.message || error), "err");
    return false;
  } finally { S.commandPending = false; }
}
function undo() { return historyAction("undo"); }
function redo() { return historyAction("redo"); }
function connectWS() {
  const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
  ws.onopen = () => { connectionStatus = "connected"; renderSaveStatus(); }; ws.onclose = () => { connectionStatus = "reconnecting…"; renderSaveStatus(); setTimeout(connectWS, 1500); };
  ws.onmessage = async ev => { const e = JSON.parse(ev.data); if (e.client === CLIENT) return;
    if (e.project && S.context && e.project !== S.context.project && e.type !== "project_replaced") return;
    if (e.type === "focus" && e.actor !== "human") { S.agentFocus = { t: +e.t, name: e.client || e.actor, ts: Date.now() }; positionAgentCursor(); }
    if (e.actor === "agent" && e.type === "ops") { const now = Date.now(); for (const o of (e.ops || [])) { const id = o.op === "set_clip" ? o.clip && o.clip.id : o.clip_id; if (id) S.agentHot[id] = now; } setTimeout(renderTimeline, 4200); }
    if (["ops", "project_replaced", "media_added", "media_ready", "snapshot", "proxy_ready", "proposal", "proposal_decision"].includes(e.type)) { try { await loadProject(true); } catch (error) { status("Project refresh failed: " + error.message, "err"); } }
    if (e.actor === "agent") { const dot = $("#agentDot"); if (dot) { dot.style.background = "#4fa36b"; dot.style.boxShadow = "0 0 8px #4fa36b"; clearTimeout(S.agentDotT); S.agentDotT = setTimeout(() => { dot.style.background = "#333"; dot.style.boxShadow = ""; }, 3000); } S.agentEvents++; const message = $("#stEvents"); message.className = "agent"; message.textContent = `agent: ${e.type}${e.reason ? " — " + e.reason : e.title ? " — " + e.title : ""} (${S.agentEvents})`; if (e.type === "proposal") CR.showTab("props"); }
    else if (e.type === "render") { $("#stEvents").className = ""; $("#stEvents").textContent = `render ${e.job.status}: ${e.job.out}`; } };
}

// ---------- render ----------
function renderAll() { window.FilmocityProjectResources?.sync(CR); renderHeader(); renderBin(); renderTimeline(); CR.panels && CR.panels.render(); updatePlayhead(); }
function renderSeqTabs() { const el = $("#seqTabs"); el.innerHTML = S.proj.sequences.map(sq => `<button class="${sq.id === S.seq.id ? "on" : ""}" data-seq="${sq.id}" title="double-click to rename">${sq.name}</button>`).join("");
  $$("button", el).forEach(b => { b.onclick = () => switchSeq(b.dataset.seq); b.ondblclick = () => { const n = prompt("Sequence name", S.proj.sequences.find(x => x.id === b.dataset.seq).name); if (n) applyOps([{ op: "set", path: `/sequences/${S.proj.sequences.findIndex(x => x.id === b.dataset.seq)}/name`, value: n }], "sequence", "rename"); }; }); }
function renderHeader() { renderSeqTabs(); $("#projName").textContent = S.proj.name || "Untitled"; $("#projTab").textContent = S.proj.name || "Untitled"; $("#seqName").textContent = `${S.seq.name} · ${S.seq.width}×${S.seq.height} · ${Number(S.seq.fps).toFixed(3).replace(/\.?0+$/, "")} fps · ${(S.seq.timecode_format || "ndf").toUpperCase()}`; $("#prgName").textContent = S.seq.name; $("#prgDur").textContent = fmtTC(seqDur(), S.seq.fps);
  const i = S.seq.in_point, o = S.seq.out_point; $("#prgIO").textContent = (i != null || o != null) ? `in ${i != null ? fmtTC(i, S.seq.fps) : "—"} · out ${o != null ? fmtTC(o, S.seq.fps) : "—"}` : "";
  ["lnkXml", "lnkOtio", "lnkSrt"].forEach(id => { const a = $("#" + id); if (a) a.href = a.href.replace(/sequence=[^&]*/, "sequence=" + S.seq.id); }); }
function renderBin() {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const bin = $("#bin"), q = (S.binQuery || "").toLowerCase(), bins = S.proj.bins || [], off = S.mediaStatus || {}, icons = S.binView === "icons";
  const hay = m => (m.name + " " + Object.values(m.meta || {}).join(" ")).toLowerCase();
  let items = Object.values(S.proj.media).filter(m => (!q || hay(m).includes(q)) && (!S.binFilter || m.bin === S.binFilter));
  if (S.binSort) { const k = S.binSort.replace("-", ""), dir = S.binSort.startsWith("-") ? -1 : 1; items = items.sort((a, b) => (k === "name" ? a.name.localeCompare(b.name) : (a[k] || 0) - (b[k] || 0)) * dir); }
  const cur = bins.find(b => b.id === S.binFilter); const crumbs = []; let walk = cur; while (walk) { crumbs.unshift(walk); walk = bins.find(b => b.id === walk.parent); } const children = bins.filter(b => (b.parent || null) === (S.binFilter || null));
  const bar = `<div class="binbar"><button id="binRefreshMedia" title="Check originals and proxies on this device">Refresh media</button><input id="binSearch" aria-label="Search project media" placeholder="Search media" value="${esc(S.binQuery || "")}"><button class="chip ${!S.binFilter ? "on" : ""}" data-bin="">${crumbs.length ? "⌂" : "all"}</button>${crumbs.map(b => `<span style="color:var(--dim)">›</span><button class="chip ${S.binFilter === b.id ? "on" : ""}" data-bin="${esc(b.id)}">${esc(b.name)}</button>`).join("")}${children.length ? `<span style="color:var(--dim)">│</span>` : ""}${children.map(b => `<button class="chip" data-bin="${esc(b.id)}" title="drop media here to file it">📁 ${esc(b.name)}</button>`).join("")}</div>`;
  const head = icons ? "" : `<div class="listhead"><span style="grid-column:1/3;cursor:pointer" data-sort="name">Name ${S.binSort && S.binSort.endsWith("name") ? (S.binSort.startsWith("-") ? "▼" : "▲") : ""}</span><span data-sort="fps" style="cursor:pointer">Frame Rate</span><span data-sort="duration" style="cursor:pointer">Media Duration ${S.binSort && S.binSort.endsWith("duration") ? (S.binSort.startsWith("-") ? "▼" : "▲") : ""}</span><span>Video Info</span><span>Audio Info</span></div>`;
  const seqRows = S.proj.sequences.map(sq => `<div class="media" draggable="true" data-seq="${esc(sq.id)}" title="sequence — drag to a track to nest, double-click to open"><div class="ph">⧉</div><div class="nm">${esc(sq.name)}</div><span class="meta">${sq.fps}</span><span class="meta">${fmtTC(seqDurOf(sq), sq.fps, sq.timecode_format || 'ndf')}</span><span class="meta">${sq.width}×${sq.height}</span><span class="meta">sequence</span></div>`).join("");
  const rows = items.map(m => off[m.id] === false && !S.mediaAvailability?.[m.id]?.proxy_available
    ? `<div class="media offline" data-id="${esc(m.id)}" title="${esc(m.path)} — offline"><div class="ph" style="color:var(--danger)">⚠</div><div class="nm">${esc(m.name)}</div><span class="meta">—</span><span class="meta">—</span><span class="meta">offline</span><span class="meta"><a href="#" data-relink="${esc(m.audio_alias?.physical_media_id || m.id)}" title="${m.audio_alias ? "Relink the shared original source for this audio alias and its sibling clips" : "Relink the original source"}">relink…</a></span></div>`
    : `<div class="media ${S.src && S.src.id === m.id ? "sel" : ""}" draggable="true" data-id="${esc(m.id)}" title="${esc(m.path)}">${m.thumb ? `<img src="${esc(m.thumb)}" alt="">` : `<div class="ph">${m.status === "ingesting" ? "⏳" : "♫"}</div>`}<div class="nm">${esc(m.name)}${off[m.id] === false || S.mediaAvailability?.[m.id]?.proxy_state === "stale_source" ? ` <a href="#" data-relink="${esc(m.audio_alias?.physical_media_id || m.id)}" title="${m.audio_alias ? "Relink the shared original source for this audio alias and its sibling clips" : "Relink the original source"}">${off[m.id] === false ? "Original offline" : "Source changed"} · relink…</a>` : ""}${m.status === "ingesting" ? ' <span style="color:var(--dim)">·analyzing…</span>' : ""}${FilmocityProxyPreview.label(m, S.mediaAvailability?.[m.id]) ? ` <span style="color:var(--dim)" title="${esc(m.proxy_error || "Proxy size and quality are set in Preferences")}">·${esc(FilmocityProxyPreview.label(m, S.mediaAvailability?.[m.id]))}</span>` : ""}${((m.audio_alias && S.mediaAvailability?.[m.id]?.proxy_state === "stale_source") || (S.mediaAvailability?.[m.id]?.proxy_state !== "stale_source" && (m.ingest_error || m.proxy_error || m.status === "cancelled" || (m.status === "unprepared" && (m.audio_alias || !m.subclip_of && !m.synthetic)) || (m.has_video && !m.is_image && !m.synthetic)))) ? ` <button data-prepare="${esc(m.id)}" title="${esc(m.audio_alias && S.mediaAvailability?.[m.id]?.proxy_state === "stale_source" ? "Prepare this audio preview from the accepted shared source. Unaccepted file changes or changed interpretation will refuse; relink the parent or recreate the alias as instructed." : m.ingest_error || m.proxy_error || "Prepare with the size and quality selected in Preferences; follow progress in Tasks")}">${m.proxy ? "Rebuild proxy" : "Prepare media"}</button>` : ""}${FilmocityMediaColor.badge(FilmocityMediaColor.source(S.proj,m)) ? `<span style="color:#f6c14a" title="${esc(FilmocityMediaColor.description(FilmocityMediaColor.source(S.proj,m)))}"> ·${esc(FilmocityMediaColor.badge(FilmocityMediaColor.source(S.proj,m)))}</span>` : ""}</div><span class="meta">${m.fps ? m.fps.toFixed(2) : m.is_image ? "still" : "—"}</span><span class="meta">${fmtTC(m.duration, mediaRate(m), 'ndf')}</span><span class="meta">${m.width ? m.width + "×" + m.height : "—"}</span><span class="meta">${esc(window.FilmocityMediaInfo.audioLabel(m))}</span></div>`).join("");
  bin.innerHTML = bar + `<div class="bin ${icons ? "icons" : ""}">${!items.length ? `<div id="projectWelcome" style="grid-column:1/-1"></div>` : ""}${head}${seqRows}${rows}</div>`;
  const welcome = $("#projectWelcome");
  if (welcome) window.FilmocityWelcome.renderWelcome({ document, host: welcome, hasMedia: Object.keys(S.proj.media).length > 0, filtered: !!q,
    importFiles: () => $("#fileInput").click(), browse: () => CR.showTab("browser"), findCommands: () => window.FilmocityCommands.open(CR),
    clearFilters: () => { S.binQuery = ""; S.binFilter = null; renderBin(); $("#binSearch").focus(); },
  });
  wireBin(bin);
}
function wireBin(bin) {
  const refresh = $("#binRefreshMedia"); if (refresh) refresh.onclick = async () => { refresh.disabled = true; try { await refreshMediaStatus(); } finally { refresh.disabled = false; } };
  $$('[data-prepare]',bin).forEach(button=>{button.onclick=async event=>{
    event.preventDefault();event.stopPropagation();if(button.disabled)return;button.disabled=true;
    try{await prepareMedia(button.dataset.prepare);}catch(error){status('Preparation could not start: '+error.message,'err');}
    finally{button.disabled=false;}
  };button.ondblclick=event=>event.stopPropagation();});
  $$(".media[data-id] .nm", bin).forEach(nm => nm.ondblclick = ev => { ev.stopPropagation(); const id = nm.closest(".media").dataset.id; const n = prompt("Rename", S.proj.media[id].name); if (n) applyOps([{ op: "set", path: `/media/${id}/name`, value: n }], "rename_media", "rename media"); });
  if (S.binView === "icons") $$(".media[data-id] img", bin).forEach(img => { const m = S.proj.media[img.closest(".media").dataset.id]; if (!m || !m.strip || m.is_image) return; img.onmousemove = ev => { const r = img.getBoundingClientRect(); const f = Math.max(0, Math.min(0.999, (ev.clientX - r.left) / r.width)); img.style.objectFit = "none"; img.style.objectPosition = `${-Math.floor(f * 10) * 160}px 0`; img.src = m.strip; img.style.width = "100%"; img.style.height = "62px"; }; img.onmouseleave = () => { img.style.objectFit = "cover"; img.style.objectPosition = ""; img.src = m.thumb; }; });
  $$(".media[data-id]", bin).forEach(el => {
    const mid = el.dataset.id, owner = { project: S.proj, context: { ...S.context }, source: S.proj.media[mid] };
    el.onclick = ev => { if (ev.ctrlKey || ev.metaKey || ev.shiftKey) { S.binSel.has(mid) ? S.binSel.delete(mid) : S.binSel.add(mid); $$(".media[data-id]", bin).forEach(x => x.classList.toggle("msel", S.binSel.has(x.dataset.id))); return; } S.binSel = new Set([mid]); loadSource(mid); CR.panels && CR.panels.renderMeta && CR.panels.renderMeta(); };
    el.ondragstart = ev => beginMediaDrag(ev, mid, "", false, owner); el.ondragend = () => { S.mediaDrag = null; }; el.ondblclick = () => { loadSource(mid); };
  });
  $$(".media[data-seq]", bin).forEach(el => {
    const sid = el.dataset.seq, owner = { project: S.proj, context: { ...S.context }, source: S.proj.sequences.find(sq => sq.id === sid) };
    el.ondragstart = ev => beginMediaDrag(ev, "seq:" + sid, "", false, owner); el.ondragend = () => { S.mediaDrag = null; }; el.ondblclick = () => switchSeq(sid);
  });
  const si = $("#binSearch"); if (si) { si.oninput = () => { S.binQuery = si.value; renderBin(); $("#binSearch").focus(); $("#binSearch").setSelectionRange(si.value.length, si.value.length); }; }
  $$("[data-bin]", bin).forEach(b => {
    const owner = { project: S.proj, context: { ...S.context } }, binId = b.dataset.bin || null;
    b.onclick = () => { S.binFilter = binId; renderBin(); }; b.ondragover = e => e.preventDefault();
    b.ondrop = e => { e.preventDefault(); if (!placementReady()) return; try {
      if (owner.project !== S.proj || !window.FilmocitySync.sameProject(owner.context, S.context) || binId && !(S.proj.bins || []).some(item => item.id === binId)) throw Error("Choose a bin in the current project.");
      const payload = readMediaDrag(e); if (!payload.mid.startsWith("seq:")) applyOps([{ op: "set", path: `/media/${payload.mid}/bin`, value: binId }], "bin", "file media");
    } catch (error) { status(error.message, "err"); } finally { S.mediaDrag = null; } };
  });
  $$("[data-relink]", bin).forEach(a => { const project = S.proj, context = { ...S.context }, media = S.proj.media[a.dataset.relink]; a.onclick = e => { e.preventDefault(); e.stopPropagation(); return beginSourceRelink(a.dataset.relink, { current: () => a.isConnected && S.proj === project && S.proj.media[a.dataset.relink] === media && window.FilmocitySync.sameProject(context, S.context) }); }; });
}
function trackRows() { return [...S.seq.tracks].sort((a, b) => (a.kind === b.kind ? (a.kind === "video" ? b.index - a.index : a.index - b.index) : (a.kind === "video" ? -1 : 1))); }
function renderTimeline() {
  const inner = $("#tlInner"), heads = $("#heads"), ruler = $("#ruler"); const dur = Math.max(seqDur() + 10, 30), width = dur * S.pps;
  const body0 = $("#tlBody"); const sl0 = body0.scrollLeft, cw0 = body0.clientWidth || 1200;  // read layout before mutating (no forced reflow)
  inner.style.width = width + "px"; inner.querySelectorAll(".track").forEach(e => e.remove()); heads.innerHTML = "";
  const si = S.proj.sequences.indexOf(S.seq), geometry = window.FilmocityTimeline, view = geometry.viewport(sl0, cw0, S.pps);
  const inView = (start, end = start) => geometry.intersects(view, start, end);
  const visible = c => inView(c.start, clipEnd(c)) || S.sel.has(c.id); S.tlVis = [view.start, view.end];
  for (const tr of trackRows()) {
    const tall = !!S.tall[tr.id], ti = S.seq.tracks.indexOf(tr);
    const h = document.createElement("div"); h.className = `head ${tr.kind}${tall ? " tall" : ""}`;
    const isV = tr.kind === "video";
    h.innerHTML = `<span class="patch ${S.target[tr.kind] === tr.id ? "on" : ""}" title="Source patching / target track">${isV ? "V" : "A"}${tr.index}</span><button class="tb ${tr.locked ? "on" : ""}" title="Toggle Track Lock">${window.trackIcon ? trackIcon(tr.locked ? "lock" : "unlock") : (tr.locked ? "🔒" : "🔓")}</button><button class="tb ${tr.sync_lock !== false ? "on" : ""}" title="Toggle Sync Lock">${window.trackIcon ? trackIcon("sync") : "⇅"}</button>${isV ? `<button class="tb ${tr.muted ? "" : "on"}" title="Toggle Track Output">${window.trackIcon ? trackIcon(tr.muted ? "eyeoff" : "eye") : "◉"}</button>` : `<button class="tb ${tr.muted ? "on" : ""}" title="Mute Track">${window.trackIcon ? trackIcon("mute") : "M"}</button><button class="tb ${tr.solo ? "on" : ""}" title="Solo Track">${window.trackIcon ? trackIcon("solo") : "S"}</button>`}<span class="nm" title="click: toggle height · double-click: rename · right-click: track menu">${tr.name || tr.id}</span>${isV ? "" : `<span class="tm" title="track level"><i></i></span>`}`; h.dataset.track = tr.id;
    h.querySelector(".patch").onclick = () => { S.target[tr.kind] = tr.id; renderTimeline(); }; h.querySelector(".nm").onclick = () => { S.tall[tr.id] = !tall; renderTimeline(); };
    h.querySelector(".nm").ondblclick = () => { const n = prompt("Track name", tr.name || tr.id); if (n != null) applyOps([{ op: "set", path: `/sequences/${si}/tracks/${ti}/name`, value: n }], "track", "rename track"); };
    h.oncontextmenu = ev => { ev.preventDefault(); CR.extras && CR.extras.trackMenu(ev, tr); };
    const tbs = h.querySelectorAll(".tb"); const [lb, yb, mb, sb] = [tbs[0], tbs[1], tbs[2], tbs[3]];
    lb.onclick = () => applyOps([{ op: "set", path: `/sequences/${si}/tracks/${ti}/locked`, value: !tr.locked }], "track", "lock toggle");
    yb.onclick = () => applyOps([{ op: "set", path: `/sequences/${si}/tracks/${ti}/sync_lock`, value: tr.sync_lock === false }], "track", "sync lock toggle");
    mb.onclick = () => applyOps([{ op: "set", path: `/sequences/${si}/tracks/${ti}/muted`, value: !tr.muted }], "track", isV ? "track output toggle" : "mute toggle");
    if (sb) sb.onclick = () => applyOps([{ op: "set", path: `/sequences/${si}/tracks/${ti}/solo`, value: !tr.solo }], "track", "solo toggle");
    heads.appendChild(h);
    const rowProject = S.proj, rowSequence = S.seq, rowContext = { ...S.context }; const ownsRow = () => S.proj === rowProject && S.seq === rowSequence && trackOf(tr.id) === tr && window.FilmocitySync.sameProject(rowContext, S.context);
    const row = document.createElement("div"); row.className = `track ${tr.kind}${S.target[tr.kind] === tr.id ? " target" : ""}${tall ? " tall" : ""}`; row.dataset.track = tr.id; row.style.width = width + "px";
    row.ondragover = ev => ev.preventDefault(); row.ondrop = ev => { ev.preventDefault(); if (!ownsRow()) return; return dropMedia(ev, tr.id, snapT(xToT(ev.clientX)), ev.shiftKey ? "insert" : "overwrite", "drag onto timeline"); };
    row.oncontextmenu = ev => { if (!ownsRow()) return; if (ev.target === row && CR.extras && CR.extras.timelineMenu) { ev.preventDefault(); CR.extras.timelineMenu(ev, tr, xToT(ev.clientX)); } };
    row.onmousedown = ev => { if (!ownsRow() || ev.target !== row) return; if (S.tool === "hand") { panDrag(ev); return; } if (S.tool === "trackback") { selectBackward(xToT(ev.clientX)); return; } if (S.tool === "trackfwd") { selectForward(xToT(ev.clientX)); return; } if (S.tool === "trackback") { selectBackward(xToT(ev.clientX)); return; } if (S.tool === "zoom") { zoomAt(ev); return; } if (S.tool === "razor") return;
      if (!ev.shiftKey) { S.sel.clear(); refreshSel(); } const gt = xToT(ev.clientX), before = tr.clips.filter(x => clipEnd(x) <= gt + 1e-6).sort((a, b) => clipEnd(b) - clipEnd(a))[0], after = tr.clips.filter(x => x.start >= gt - 1e-6).sort((a, b) => a.start - b.start)[0];
      S.gap = (before && after) ? { track: tr.id, start: clipEnd(before), end: after.start, project: S.proj, sequence: S.seq, before: before.id, after: after.id } : null; marqueeDrag(ev); };
    for (const c of tr.clips) if (visible(c)) row.appendChild(clipEl(c, tr));
    if (tr.kind === "audio" && S.tlopt.linked) for (const vtr of S.seq.tracks.filter(t => t.kind === "video")) { if (linkedAudioTrack(vtr) !== tr) continue; for (const c of vtr.clips) { if (!visible(c)) continue; const m = c.media_id && S.proj.media[c.media_id]; if (m && m.has_audio && (c.audio || {}).linked !== false) row.appendChild(clipEl(c, vtr, true)); } }
    inner.appendChild(row);
  }
  if (S.tlopt.captions) { const ch = document.createElement("div"); ch.className = "head caption"; ch.innerHTML = `<span class="patch" title="caption track">C1</span><span class="nm">Captions</span>`; heads.insertBefore(ch, heads.firstChild);
    const crow = document.createElement("div"); crow.className = "track caption"; crow.style.width = width + "px"; for (const cp of (S.seq.captions || [])) { if (!inView(cp.start, cp.end)) continue; const el = document.createElement("div"); el.className = "capclip"; el.style.left = cp.start * S.pps + "px"; el.style.width = Math.max(6, (cp.end - cp.start) * S.pps) + "px"; el.title = cp.text; el.textContent = cp.text.split("\n")[0]; for (const side of ["l", "r"]) { const handle = document.createElement("div"); handle.className = "h " + side; el.appendChild(handle); } el.onmousedown = ev => captionDrag(ev, cp, el); el.ondblclick = ev => { ev.stopPropagation(); const t = prompt("Caption text", cp.text); if (t == null) return; const si = S.proj.sequences.indexOf(S.seq); applyOps([{ op: "set", path: `/sequences/${si}/captions`, value: (S.seq.captions || []).map(x => x.id === cp.id ? { ...x, text: t } : x) }], "captions", "edit caption"); }; el.oncontextmenu = ev => { ev.preventDefault(); ev.stopPropagation(); if (confirm("Upgrade this caption to a text layer (graphic on the top video track)?")) upgradeCaption(cp); }; crow.appendChild(el); } inner.insertBefore(crow, inner.querySelector(".track")); }
  if (S.tlopt.ghosts) for (const pr of (S.proj.proposals || [])) for (const it of pr.items) { if (it.status !== "pending") continue; for (const o of it.ops) { if (o.op !== "set_clip" || !o.track || (o.sequence && o.sequence !== S.seq.id)) continue; const row = inner.querySelector(`.track[data-track="${o.track}"]`); if (!row) continue; const cur = clipById(o.clip.id); const merged = { ...(cur ? cur.c : {}), ...o.clip }; if (merged.start == null || merged.out == null || merged.in_ == null || !inView(merged.start, clipEnd(merged))) continue;
      const g = document.createElement("div"); g.className = "ghost"; g.style.left = merged.start * S.pps + "px"; g.style.width = Math.max(6, clipDur(merged) * S.pps) + "px"; const label = document.createElement("span"); label.textContent = "agent: " + (it.reason || pr.title); g.appendChild(label); g.title = "pending proposal — click to review"; g.onmousedown = ev => { ev.stopPropagation(); CR.showTab("props"); }; row.appendChild(g); } }
  if (S.editPoint) { const r = clipById(S.editPoint.clipId); if (r) { const row = inner.querySelector(`.track[data-track="${r.tr.id}"]`); const ep = document.createElement("div"); ep.className = "editpt"; ep.style.left = ((S.editPoint.side === "l" ? r.c.start : clipEnd(r.c)) * S.pps - 1) + "px"; const col = { regular: "#ff5a5a", ripple: "#f6c14a", roll: "#9fe0b7" }[S.trimType || "ripple"]; ep.style.background = col; ep.style.setProperty("--epc", col); row && row.appendChild(ep); } }
  if (S.tlopt.through) for (const tr of S.seq.tracks) { const row = inner.querySelector(`.track[data-track="${tr.id}"]`); if (!row) continue; const sorted = [...tr.clips].sort((a, b) => a.start - b.start); for (let i = 1; i < sorted.length; i++) { const a = sorted[i - 1], b = sorted[i]; if (inView(b.start) && a.media_id && a.media_id === b.media_id && Math.abs(clipEnd(a) - b.start) < frame() / 2 && Math.abs(a.out - b.in_) < frame() / 2 && (a.speed || 1) === (b.speed || 1)) { const d = document.createElement("div"); d.className = "through"; d.style.left = b.start * S.pps + "px"; d.title = "through edit — continuous source across this cut"; row.appendChild(d); } } }
  const foot = document.createElement("div"); foot.className = "headfoot"; foot.innerHTML = `<button title="add video track">+V</button><button title="add audio track">+A</button>`; const [bv, ba] = foot.querySelectorAll("button"); bv.onclick = () => addTrack("video"); ba.onclick = () => addTrack("audio"); heads.appendChild(foot);
  ruler.innerHTML = ""; const step = S.pps >= 120 ? 0.5 : S.pps >= 60 ? 1 : S.pps >= 30 ? 2 : S.pps >= 15 ? 5 : 10;
  const rin = document.createElement("div"); rin.style.cssText = `position:relative;width:${width}px;height:100%`;
  if (S.seq.in_point != null && S.seq.out_point != null && S.seq.out_point > S.seq.in_point) { const io = document.createElement("div"); io.className = "ruler-io"; io.style.left = S.seq.in_point * S.pps + "px"; io.style.width = (S.seq.out_point - S.seq.in_point) * S.pps + "px"; rin.appendChild(io); }
  for (const t of geometry.ticks(view, dur, step)) { const tick = document.createElement("div"); tick.className = "ruler-tick"; tick.style.left = t * S.pps + "px"; rin.appendChild(tick); const l = document.createElement("div"); l.className = "ruler-lbl"; l.style.left = t * S.pps + "px"; l.textContent = fmtTC(t, S.seq.fps).slice(3, 8); rin.appendChild(l); }
  for (const m of S.seq.markers || []) { if (!inView(m.time, m.time + (m.duration || 0))) continue; const mk = document.createElement("div"); mk.className = "marker"; mk.style.left = m.time * S.pps + "px"; mk.style.background = m.color || "#4fa36b"; if (m.duration) { mk.style.width = Math.max(8, m.duration * S.pps) + "px"; mk.style.transform = "none"; mk.style.opacity = .8; } mk.title = (m.name || "marker") + " — double-click to edit"; mk.ondblclick = ev => { ev.stopPropagation(); CR.panels.editMarker(m); }; mk.onmousedown = ev => ev.stopPropagation(); rin.appendChild(mk); }
  const rp = document.createElement("div"); rp.className = "ruler-play"; rp.id = "rulerPlay"; rin.appendChild(rp); ruler.appendChild(rin);
  ruler.onmousedown = ev => { const move = e => seekTo(xToT(e.clientX)); move(ev); const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); }; window.addEventListener("mousemove", move); window.addEventListener("mouseup", up); };
  $("#tlBody").onscroll = () => { const b = $("#tlBody"); ruler.scrollLeft = b.scrollLeft; heads.scrollTop = b.scrollTop; if (S.tlVis) { const v0 = b.scrollLeft / S.pps, v1 = (b.scrollLeft + b.clientWidth) / S.pps, m = Math.max(5, v1 - v0) * 0.6; if ((v0 < S.tlVis[0] - m || v1 > S.tlVis[1] + m) && !S.tlRerender) { S.tlRerender = true; requestAnimationFrame(() => { S.tlRerender = false; renderTimeline(); }); } } };
  $("#tl").className = "tl " + S.tool; positionPlayhead(); refreshRenderBar();
}
function positionAgentCursor() { let el = $("#agentCursor"); if (!S.agentFocus || Date.now() - S.agentFocus.ts > 120000) { if (el) el.remove(); return; } if (!el) { el = document.createElement("div"); el.id = "agentCursor"; el.className = "playhead"; el.style.background = "#4fa36b"; el.style.opacity = ".8"; el.innerHTML = `<span style="position:absolute;top:2px;left:4px;font-size:10px;color:#9fe0b7;white-space:nowrap"></span>`; $("#tlInner").appendChild(el); } el.style.left = S.agentFocus.t * S.pps + "px"; el.firstElementChild.textContent = S.agentFocus.name; }
let idleTimer = null;
function scheduleIdleRender() {
  clearTimeout(idleTimer);
  if (!S.prefs.bg_render) return;
  idleTimer = setTimeout(async () => {
    const controller = getRenderedPreview(), view = previewView();
    if (S.playing || controller.busy || controller.current || view.pending || view.error || view.gesture || view.switching) return;
    try {
      const jobs = await api.get("/api/jobs");
      if (!window.FilmocityRenderedPreview.sameView(view, previewView()) || jobs.some(j => ["running", "queued", "cancelling"].includes(j.status))) return;
      controller.start({ background: true });
    } catch (error) { /* A later idle edit can retry; no render was submitted. */ }
  }, 8000);
}
let renderStatus = null;
function renderStatusView() {
  const body = $("#tlBody");
  return { ...previewView(), viewport: window.FilmocityTimeline.viewport(body.scrollLeft, body.clientWidth, S.pps) };
}
function drawRenderBar(segments) {
  let bar = $("#renderBar"); const rin = $("#ruler").firstElementChild; if (!rin) return;
  if (!bar || bar.parentElement !== rin) { bar = document.createElement("div"); bar.id = "renderBar"; bar.className = "renderbar"; rin.appendChild(bar); }
  bar.innerHTML = segments.map(sg => `<i class="${sg.rendered || sg.cached ? "ok" : "no"}" style="left:${sg.t0 * S.pps}px;width:${Math.max(1, (sg.t1 - sg.t0) * S.pps)}px" title="${sg.rendered ? "preview available" : sg.cached ? "cached draft segment" : "not rendered"} ${fmtTC(sg.t0, S.seq.fps)}–${fmtTC(sg.t1, S.seq.fps)}"></i>`).join("");
}
function refreshRenderBar(force = false) {
  scheduleIdleRender();
  if (!renderStatus) renderStatus = window.FilmocityRenderStatus.create({
    capture: renderStatusView,
    sameView: window.FilmocityRenderedPreview.sameView, sameContext: window.FilmocityRenderedPreview.sameContext,
    request: (view, range, signal) => fetch("/api/render/segments?sequence=" + encodeURIComponent(view.sequence) +
      "&start=" + range[0] + "&end=" + range[1], { signal }).then(readApiResponse),
    draw: drawRenderBar,
    accept(r, view) {
      const controller = getRenderedPreview();
      if (r.ready_preview && !controller.busy) controller.adopt(r.ready_preview, view);
      else if (controller.current && !controller.busy) controller.invalidate("Preview outdated — render again");
    },
  });
  renderStatus.refresh({ force });
}
function positionPlayhead() { positionAgentCursor(); $("#playhead").style.left = S.t * S.pps + "px"; const rp = $("#rulerPlay"); if (rp) rp.style.left = S.t * S.pps + "px"; $("#tlTC").textContent = fmtTC(S.t, S.seq.fps); }
function clipEl(c, tr, linked) {
  const ownerProject = S.proj, ownerSequence = S.seq, ownerContext = S.context && { ...S.context };
  const m = c.media_id ? S.proj.media[c.media_id] : null, el = document.createElement("div"); const H = [];
  el.className = "clip " + (linked ? "audio linked" : c.media_id ? (tr.kind === "audio" ? "audio" : "") : c.sequence_id ? "nested" : c.adjustment ? "adjust" : "title") + (S.sel.has(c.id) ? " sel" : ""); el.dataset.id = c.id; el.dataset.track = tr.id;
  el.title = `${m ? m.name : c.title ? "Text: " + (c.title.text || "") : c.graphic ? c.graphic.name : c.sequence_id ? "Nested sequence" : "Adjustment layer"}\nStart ${fmtTC(c.start, S.seq.fps)}  End ${fmtTC(clipEnd(c), S.seq.fps)}  Duration ${fmtTC(clipDur(c), S.seq.fps)}${m ? `\nIn ${fmtTC(c.in_, mediaRate(m, true), 'ndf')}  Out ${fmtTC(c.out, mediaRate(m, true), 'ndf')}${c.speed && c.speed !== 1 ? "  Speed " + Math.round(c.speed * 100) + "%" : ""}` : ""}${c.label ? "\nLabel " + c.label : ""}${c.note ? "\nNote: " + c.note : ""}`;
  el.style.left = c.start * S.pps + "px"; el.style.width = Math.max(4, clipDur(c) * S.pps) + "px";
  const mdur = m ? Math.max(m.duration || 1, 0.01) : 1, sp0 = c.speed || 1, fullW = (mdur / sp0) * S.pps, offX = -((c.in_ || 0) / sp0) * S.pps + (m && m.sub_in ? 0 : 0);
  if (m && !linked && tr.kind === "video" && m.strip && S.tlopt.thumbs) H.push(`<div class="strip" style="background-image:url(${m.strip});background-size:${Math.max(fullW, 40)}px 100%;background-position:${offX}px 0;background-repeat:no-repeat"></div>`);
  if (m && (linked || tr.kind === "audio") && m.wave && S.tlopt.waves) {
    const geometry = m.audio_alias ? FilmocityProxyPreview.aliasWaveGeometry(m, c, S.pps, S.mediaAvailability?.[m.id]) : { width: Math.max(fullW, 40), offset: offX };
    if (geometry) H.push(`<div class="wave" style="background-image:url(${m.wave});background-size:${geometry.width}px 100%;background-position:${geometry.offset}px 0;background-repeat:no-repeat"></div>`);
    else if (m.audio_alias) el.title += "\nAudio alias waveform is hidden until its preview is verified; reverse, speed-ramp and held clips require rendered audio review.";
  }
  if (!linked) { if (c.transition_in && c.transition_in.duration > 0) H.push(`<div class="trans" data-tr="in" style="left:0;width:${c.transition_in.duration * S.pps}px" title="${c.transition_in.type} · ${c.transition_in.duration.toFixed(2)}s — drag the right edge to change · double-click to type"><div class="th"></div></div>`);
    if (c.transition_out && c.transition_out.duration > 0) H.push(`<div class="trans out" data-tr="out" style="right:0;width:${c.transition_out.duration * S.pps}px" title="${c.transition_out.type} · ${c.transition_out.duration.toFixed(2)}s — drag the left edge to change · double-click to type"><div class="th l"></div></div>`);
    for (const k of Object.keys(c.keyframes || {})) for (const kf of c.keyframes[k]) if (kf.t >= 0 && kf.t <= clipDur(c)) H.push(`<div class="kf" style="left:${kf.t * S.pps}px" title="${k} @ ${fmtTC(kf.t, S.seq.fps)}"></div>`); }
  const nestedName = c.sequence_id ? "⧉ " + ((S.proj.sequences.find(x => x.id === c.sequence_id) || {}).name || "sequence") : null; if (c.enabled === false) el.style.opacity = .45; if (c.label) el.style.borderLeft = `5px solid ${c.label}`;
  if (S.agentHot[c.id] && Date.now() - S.agentHot[c.id] < 4000) el.classList.add("agent"); for (const mk of (c.markers || []).filter(m => Number.isFinite(m.t) && m.t >= 0 && m.t <= clipDur(c))) H.push(`<div class="cm" style="left:${mk.t * S.pps}px;background:${mk.color || "#4fa36b"}" title="${mk.name || "clip marker"}"></div>`);
  if (m && m.has_audio && (linked || tr.kind === "audio") && (c.keyframes || {})["audio.gain_db"]) for (const k of c.keyframes["audio.gain_db"]) if (k.t >= 0 && k.t <= clipDur(c)) H.push(`<div class="gkf" data-gt="${k.t}" style="left:${k.t * S.pps}px;top:${Math.max(4, Math.min(92, 50 - k.v * 2.2))}%" title="${k.v} dB @ ${fmtTC(k.t, S.seq.fps).slice(3)} — drag (Pen tool)"></div>`);
  if (m && m.has_audio && (linked || tr.kind === "audio")) { const g = (c.audio || {}).gain_db || 0; H.push(`<div class="gain" style="top:${Math.max(4, Math.min(92, 50 - g * 2.2))}%" title="gain ${g} dB — drag"></div>`); }
  if (!linked && tr.kind === "video" && S.tlopt.opacity && (c.media_id || c.title || c.graphic || c.sequence_id)) { const op = (c.transform || {}).opacity == null ? 1 : c.transform.opacity; H.push(`<div class="opline" style="top:${Math.max(4, Math.min(92, 92 - op * 88))}%" title="opacity ${Math.round(op * 100)}% — drag · Pen: keyframes"></div>`); for (const k of ((c.keyframes || {})["transform.opacity"] || [])) if (k.t >= 0 && k.t <= clipDur(c)) H.push(`<div class="okf" data-ot="${k.t}" style="left:${k.t * S.pps}px;top:${Math.max(4, Math.min(92, 92 - k.v * 88))}%" title="${Math.round(k.v * 100)}% @ ${fmtTC(k.t, S.seq.fps).slice(3)}"></div>`); }
  const modified = !linked && ((c.fx_stack || []).some(f => f.enabled !== false) || (c.color && Object.keys(c.color).some(k => c.color[k] !== null && c.color[k] !== undefined && c.color[k] !== 0 && c.color[k] !== "" && !(Array.isArray(c.color[k]) && !c.color[k].length))) || (c.effects && Object.keys(c.effects).length) || (c.transform && ((c.transform.scale != null && c.transform.scale !== 1) || c.transform.x || c.transform.y || c.transform.rotation || (c.transform.opacity != null && c.transform.opacity !== 1))) || (c.mask && c.mask.type) || (c.blend && c.blend !== "normal")); if (modified) H.push(`<div class="fxbadge" title="effects / motion / colour applied">fx</div>`);
  H.push(`<div class="lbl">${linked ? "" : c.title ? "T: " + (c.title.text || "") : c.graphic ? "G: " + (c.graphic.name || "graphic") : c.adjustment ? "Adjustment layer" : nestedName ? nestedName : (m ? m.name : "?")}${c.mask && c.mask.type ? " ◫" : ""}${c.hold ? " ❚ hold" : c.time_remap ? " ⏱ remap" : ""}${c.blend && c.blend !== "normal" ? " ⊕" + c.blend : ""}${c.enabled === false ? " (disabled)" : ""}${!linked && c.speed && c.speed !== 1 ? ` (${Math.round(c.speed * 100)}%)` : ""}${!linked && c.color && Object.values(c.color).some(v => v) ? " ◐" : ""}</div><div class="h l"></div><div class="h r"></div>`);
  if (H.length) el.innerHTML = (el.innerHTML || "") + H.join("");
  el.onmousedown = ev => { ev.stopPropagation();
    if (S.proj !== ownerProject || S.seq !== ownerSequence || !window.FilmocitySync.sameProject(ownerContext, S.context)) return;
    const current = clipById(c.id); if (current?.c !== c || current?.tr !== tr || tr.locked || !canEdit()) return;
    const t = xToT(ev.clientX);
    if (ev.target.classList.contains("gkf")) { gainKfDrag(ev, c, tr, el); return; }
    if (ev.target.classList.contains("okf")) { opKfDrag(ev, c, tr, el); return; }
    if (ev.target.classList.contains("th")) { transitionDrag(ev, c, tr, ev.target.parentElement.dataset.tr); return; }
    if (ev.target.classList.contains("opline")) { if (S.tool === "pen") { opPenAdd(ev, c, tr, el); return; } opDrag(ev, c, tr, el); return; }
    if (S.tool === "pen" && (linked || tr.kind === "audio")) { penAdd(ev, c, tr, el); return; }
    if (ev.target.classList.contains("gain")) { gainDrag(ev, c, tr, el); return; }
    if (S.tool === "ratestretch") { const handle = ev.target.classList.contains("h") ? (ev.target.classList.contains("l") ? "l" : "r") : "r"; rateStretch(ev, c, tr, el, handle); return; }
    if (S.tool === "razor") { razorAtCmd(t, tr.id); return; } if (S.tool === "hand") { panDrag(ev); return; } if (S.tool === "trackback") { selectBackward(t); return; } if (S.tool === "trackfwd") { selectForward(t); return; } if (S.tool === "trackback") { selectBackward(t); return; } if (S.tool === "zoom") { zoomAt(ev); return; }
    if (S.tool === "select" && ev.target.classList.contains("h") && !ev.shiftKey) { S.editPoint = { clipId: c.id, side: ev.target.classList.contains("l") ? "l" : "r" }; } else if (!ev.target.classList.contains("h")) S.editPoint = null;
    if (ev.shiftKey) { S.sel.has(c.id) ? S.sel.delete(c.id) : S.sel.add(c.id); } else if (!S.sel.has(c.id)) { S.sel.clear(); S.sel.add(c.id); }
    if (c.group) for (const t2 of S.seq.tracks) for (const x of t2.clips) if (x.group === c.group) S.sel.add(x.id);
    refreshSel(); CR.panels.render(); if (S.editPoint) renderTimeline();
    const handle = ev.target.classList.contains("h") ? (ev.target.classList.contains("l") ? "l" : "r") : null;
    if (ev.altKey && !handle && S.tool === "select" && !c.hold) { const selection = [...S.sel], dup = { ...deep(c), id: uid() }; applyLocal([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: dup }]); S.sel = new Set([dup.id]); renderTimeline(); const nel = document.querySelector(`.clip[data-id="${dup.id}"]`); S.dupPending = { clip: dup, track: tr.id, selection }; startDrag(ev, dup, tr, null, nel || el); return; }
    startDrag(ev, c, tr, handle, el); };
  el.ondragover = ev => { if ([...ev.dataTransfer.types].includes("text/fx")) ev.preventDefault(); }; el.ondrop = ev => { const d = ev.dataTransfer.getData("text/fx"); if (!d) return; ev.preventDefault(); ev.stopPropagation(); const [k, t] = d.split("|"); S.sel = new Set([c.id]); refreshSel(); CR.addStackFx(k, t); };
  el.oncontextmenu = ev => { ev.preventDefault(); ev.stopPropagation(); if (!S.sel.has(c.id)) { S.sel = new Set([c.id]); refreshSel(); CR.panels.render(); } CR.extras && CR.extras.clipMenu(ev, c, tr); };
  el.ondblclick = ev => { const t = ev.target.closest(".trans"); if (t) { ev.stopPropagation(); const key = t.dataset.tr === "in" ? "transition_in" : "transition_out"; const v = prompt(`${c[key].type} duration (seconds)`, String(c[key].duration)); const d = parseFloat(v); if (d > 0) applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, [key]: { ...c[key], duration: Math.min(clipDur(c), d) } } }], "transition", `transition ${d}s`); return; } if (c.sequence_id) { switchSeq(c.sequence_id); return; } if (m) { loadSource(m.id); seekSourceTime(c.in_); S.srcIn = c.in_; S.srcOut = c.out; updateSrcIO(); } };
  return el;
}
function refreshSel() { $$(".clip").forEach(el => el.classList.toggle("sel", S.sel.has(el.dataset.id))); const n = S.sel.size; const st = $("#stSel"); if (st) st.textContent = n ? `${n} selected` : ""; }
function xToT(clientX) { const body = $("#tlBody"), r = body.getBoundingClientRect(); return Math.max(0, (clientX - r.left + body.scrollLeft) / S.pps); }
function snapT(t, exclude, frameOrigin = null) {
  const align = value => frameOrigin == null ? value : frameOrigin + timing.fromFrames(timing.toFrames(value - frameOrigin, S.seq.fps || 30), S.seq.fps || 30);
  const target = align(t); if (!S.snap) return target;
  const pts = [0, S.t];
  for (const tr of S.seq.tracks) for (const c of tr.clips) { if (exclude && exclude.has ? exclude.has(c.id) : c.id === exclude) continue; pts.push(c.start, clipEnd(c)); }
  for (const m of S.seq.markers || []) pts.push(m.time);
  if (S.seq.in_point != null) pts.push(S.seq.in_point); if (S.seq.out_point != null) pts.push(S.seq.out_point);
  let best = target, distance = 8 / S.pps;
  for (const point of pts) { const candidate = align(point); if (Number.isFinite(candidate) && Math.abs(candidate - t) < distance) { distance = Math.abs(candidate - t); best = candidate; } }
  return best;
}
function showSnap(t) { const sl = $("#snapline"); sl.style.display = "block"; sl.style.left = t * S.pps + "px"; }
function hideSnap() { $("#snapline").style.display = "none"; }

// ---------- drags: move (group, cross-track), trim, ripple, roll, slip, slide ----------
function trimReadout(text, x, y) { let el = $("#trimReadout"); if (!text) { if (el) el.remove(); return; } if (!el) { el = document.createElement("div"); el.id = "trimReadout"; el.style.cssText = "position:fixed;z-index:80;background:#111;border:1px solid #f6c14a;color:#f6c14a;font:11px var(--mono);padding:2px 6px;border-radius:3px;pointer-events:none"; document.body.appendChild(el); } el.textContent = text; el.style.left = (x + 14) + "px"; el.style.top = (y - 26) + "px"; }
function autoScroll(e) { const body = $("#tlBody"), r = body.getBoundingClientRect(); if (e.clientX > r.right - 30) body.scrollLeft += 18; else if (e.clientX < r.left + 30) body.scrollLeft -= 18; }
function startDrag(ev, c, tr, handle, el) {
  const current = S.seq && clipById(c.id);
  if (current?.c !== c || current?.tr !== tr || !S.proj?.sequences.includes(S.seq) || !canEdit() || tr.locked) return;
  const x0 = ev.clientX, y0 = ev.clientY, tool = S.tool; let moved = false, warned = false, lastError = null;
  const selIds = (S.sel.has(c.id) ? [...S.sel] : [c.id]).filter(id => clipById(id));
  const orig = Object.fromEntries(selIds.map(id => { const r = clipById(id); return [id, { c: deep(r.c), tr: r.tr.id }]; }));
  const sorted = [...tr.clips].sort((a, b) => a.start - b.start), idx = sorted.indexOf(c), prev = sorted[idx - 1], next = sorted[idx + 1];
  const oPrev = prev && deep(prev), oNext = next && deep(next), o = orig[c.id].c;
  const fps = S.seq.fps || 30, rate = timing.frameRate(fps), minimum = tr.kind === "audio" ? 1 / 48000 : timing.fromFrames(1, fps);
  const picture = selIds.map(id => ({ clip: orig[id].c, track: trackOf(orig[id].tr) })).find(x => x.track.kind === "video");
  // One video anchor determines the common move delta; intentional offsets in a group remain intact.
  const moveOrigin = picture ? o.start - picture.clip.start : null, trimOrigin = tr.kind === "video" ? 0 : null;
  const originals = new Map([...tr.clips, ...selIds.map(id => clipById(id).c)].map(clip => [clip, deep(clip)]));
  let targetTrack = tr.id, lastMode = null;
  const sourceLimit = clip => {
    if (clip.media_id) { const media = S.proj.media[clip.media_id]; if (media?.is_image) return Infinity; const duration = media?.duration; return Number.isFinite(duration) && duration >= 0 ? duration : clip.out; }
    if (clip.sequence_id) { const sequence = S.proj.sequences.find(sq => sq.id === clip.sequence_id); return sequence ? Math.max(0, ...sequence.tracks.flatMap(t => t.clips.map(clipEnd))) : clip.out; }
    return Infinity;
  };
  // Project onto the permitted frame lattice after clamping. A source handle between frames must never be exceeded.
  const bounded = (value, lo, hi, origin = trimOrigin) => {
    if (lo > hi) return null;
    if (origin == null) return Math.max(lo, Math.min(hi, value));
    const first = Math.ceil((lo - origin) * rate - 1e-7), last = Math.floor((hi - origin) * rate + 1e-7);
    if (first > last) return null;
    return origin + timing.fromFrames(Math.max(first, Math.min(last, timing.toFrames(value - origin, fps))), fps);
  };
  const edits = window.FilmocityClipSplit;
  const clock = clip => ({ duration: clipDur(clip), sourceOffset: at => sourceOffset(clip, at), speedAt: at => speedAt(clip, at) });
  const options = clip => ({ sourceLimit: sourceLimit(clip), still: !!S.proj.media[clip.media_id]?.is_image, sourceFrame: 1 / mediaRate(S.proj.media[clip.media_id], true) });
  const limits = clip => edits.bounds(clip, clock(clip), options(clip));
  const trim = (clip, begin, end, start = clip.start + begin) => edits.trim(clip, begin, end, clock(clip), { ...options(clip), start });
  const adjacent = (left, right) => left && right && Math.abs(clipEnd(left) - right.start) <= 1e-7;
  const previewed = new Set();
  const replace = (clip, value) => { for (const key of Object.keys(clip)) if (!Object.hasOwn(value, key)) delete clip[key]; Object.assign(clip, deep(value)); };
  const reset = () => { for (const clip of previewed) replace(clip, originals.get(clip)); previewed.clear(); };
  const install = plan => { for (const [clip, value] of plan) if (JSON.stringify(value) !== JSON.stringify(originals.get(clip))) { replace(clip, value); previewed.add(clip); } };
  const duplicate = S.dupPending?.clip === c ? S.dupPending : null;
  const rollback = () => { reset(); if (duplicate) { tr.clips = tr.clips.filter(x => x !== c); S.sel = new Set(duplicate.selection); S.dupPending = null; } };
  const move = e => {
    if (Math.abs(e.clientX - x0) > 2 || Math.abs(e.clientY - y0) > 6) moved = true; if (!moved) return;
    reset(); autoScroll(e); const dx = (e.clientX - x0) / S.pps; hideSnap();
    const slipping = tool === "slip" || (tool === "select" && e.altKey && !handle && !duplicate);
    const sliding = tool === "slide" && !handle;
    const trimming = handle && ["ripple", "select", "roll"].includes(tool);
    const sourceEdit = slipping || sliding || trimming; lastMode = sourceEdit ? "source" : "move";
    if (sourceEdit) targetTrack = tr.id;
    if (sourceEdit && dx === 0) { renderTimeline(); refreshSel(); renderProgram(); return; }
    let actualDelta = 0; const plan = new Map();
    try {
    if (slipping) {
      const range = edits.slipBounds(o, clock(o), options(o));
      const d = bounded(dx, range.begin, range.end, tr.kind === "video" ? 0 : null);
      if (d != null && Math.abs(d) > 1e-10) { plan.set(c, edits.slip(o, d, clock(o), options(o))); actualDelta = d; }
    } else if (sliding) {
      if ((prev && !adjacent(oPrev, o)) || (next && !adjacent(o, oNext))) throw Error("Slide needs adjoining clips; move the clip to change a gap.");
      let lo = -o.start, hi = Infinity;
      if (oPrev) { lo = Math.max(lo, minimum - clipDur(oPrev)); hi = Math.min(hi, limits(oPrev).end - clipDur(oPrev)); }
      if (oNext) { lo = Math.max(lo, limits(oNext).begin); hi = Math.min(hi, clipDur(oNext) - minimum); }
      const position = bounded(snapT(o.start + dx, new Set([c.id, prev?.id, next?.id]), trimOrigin), o.start + lo, o.start + hi);
      if (position != null && Math.abs(position - o.start) > 1e-10) { const d = position - o.start; actualDelta = d;
        plan.set(c, { ...deep(o), start: position });
        if (prev) plan.set(prev, trim(oPrev, 0, clipDur(oPrev) + d));
        if (next) plan.set(next, trim(oNext, d, clipDur(oNext)));
      }
    } else if (trimming) {
      if (tool === "roll" && !(handle === "l" ? adjacent(oPrev, o) : adjacent(o, oNext))) throw Error("Roll needs two clips sharing the selected edit point.");
      if (handle === "l") {
        let lo = tool === "ripple" ? limits(o).begin : Math.max(-o.start, limits(o).begin), hi = clipDur(o) - minimum;
        if (tool === "select" && prev) lo = Math.max(lo, clipEnd(oPrev) - o.start);
        if (tool === "roll") { lo = Math.max(lo, minimum - clipDur(oPrev)); hi = Math.min(hi, limits(oPrev).end - clipDur(oPrev)); }
        const position = bounded(snapT(o.start + dx, new Set([c.id, tool === "roll" ? prev?.id : null]), trimOrigin), o.start + lo, o.start + hi);
        if (position != null && Math.abs(position - o.start) > 1e-10) { const d = position - o.start; actualDelta = d;
          plan.set(c, trim(o, d, clipDur(o), tool === "ripple" ? o.start : position));
          if (tool === "ripple") for (const x of tr.clips) { const original = originals.get(x); if (x !== c && original.start >= clipEnd(o) - 1e-6) plan.set(x, { ...deep(original), start: original.start - d }); }
          if (tool === "roll") plan.set(prev, trim(oPrev, 0, clipDur(oPrev) + d));
          if (position !== o.start + dx) showSnap(position);
        }
      } else {
        let lo = minimum - clipDur(o), hi = limits(o).end - clipDur(o);
        if (tool === "select" && next) hi = Math.min(hi, oNext.start - clipEnd(o));
        if (tool === "roll") { lo = Math.max(lo, limits(oNext).begin); hi = Math.min(hi, clipDur(oNext) - minimum); }
        const end = bounded(snapT(clipEnd(o) + dx, new Set([c.id, tool === "roll" ? next?.id : null]), trimOrigin), clipEnd(o) + lo, clipEnd(o) + hi);
        if (end != null && Math.abs(end - clipEnd(o)) > 1e-10) { const d = end - clipEnd(o); actualDelta = d;
          plan.set(c, trim(o, 0, end - o.start));
          if (tool === "ripple") for (const x of tr.clips) { const original = originals.get(x); if (x !== c && original.start >= clipEnd(o) - 1e-6) plan.set(x, { ...deep(original), start: original.start + d }); }
          if (tool === "roll") plan.set(next, trim(oNext, d, clipDur(oNext)));
          if (end !== clipEnd(o) + dx) showSnap(end);
        }
      }
    } else if (!handle) {
      if (selIds.some(id => trackOf(orig[id].tr).locked)) { if (!warned) { status("Unlock every selected track before moving this group.", "err"); warned = true; } return; }
      const earliest = Math.min(...selIds.map(id => orig[id].c.start));
      const base = o.start + dx, position = dx === 0 ? o.start : bounded(snapT(base, new Set(selIds), moveOrigin), o.start - earliest, Infinity, moveOrigin);
      if (position != null) { const d = position - o.start; actualDelta = d;
        for (const id of selIds) plan.set(clipById(id).c, { ...deep(orig[id].c), start: orig[id].c.start + d });
        if (position !== base) showSnap(position);
      }
      if (selIds.length === 1) { const row = document.elementsFromPoint(e.clientX, e.clientY).find(x => x.classList && x.classList.contains("track")); if (row && row.dataset.track !== targetTrack) { const nt = trackOf(row.dataset.track); if (nt && nt.kind === tr.kind && !nt.locked && (nt.kind === "video" || !c.title)) targetTrack = nt.id; } }
    }
    install(plan); lastError = null;
    } catch (error) {
      // Nothing is installed until every affected clip has a complete valid plan.
      hideSnap(); trimReadout(null); S.previewT = null; S.slipTwoUp = null;
      const message = error.message || String(error); if (message !== lastError) status(message, "err"); lastError = message;
      renderTimeline(); refreshSel(); renderProgram(); return;
    }
    if (sourceEdit) { const fr = timing.toFrames(actualDelta, fps), amount = tr.kind === "audio" ? `${actualDelta >= 0 ? "+" : ""}${(actualDelta * 1000).toFixed(3)} ms` : `${fr >= 0 ? "+" : ""}${fr} f`; trimReadout(`${amount}  ${slipping ? "slip" : sliding ? "slide" : tool === "roll" ? "roll" : tool === "ripple" ? "ripple" : "trim"}`, e.clientX, e.clientY); S.previewT = handle === "l" || slipping ? c.start : clipEnd(c); if (slipping) S.slipTwoUp = c; }
    else if (e.ctrlKey && selIds.length === 1) trimReadout("insert", e.clientX, e.clientY); else trimReadout(null);
    renderTimeline(); refreshSel(); if (sourceEdit) renderProgram();
  };
  const changed = () => targetTrack !== tr.id || previewed.size > 0;
  const up = e => {
    window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); hideSnap(); trimReadout(null); S.previewT = null; S.slipTwoUp = null;
    if (!moved || !changed()) { rollback(); renderTimeline(); renderProgram(); return; }
    if (e && e.ctrlKey && !handle && tool === "select" && lastMode === "move" && !duplicate && selIds.length === 1) {
      const placement = deep(c), destination = targetTrack; reset();
      try {
        const tracks = timelineRangeTrackIds([destination], { sync: true }), picture = tracks.some(id => trackOf(id).kind === "video"), fps = S.seq.fps || 30;
        const at = picture ? timing.fromFrames(timing.toFrames(placement.start, fps), fps) : placement.start;
        const duration = clipDur(placement), gap = picture ? timing.fromFrames(Math.ceil(duration * timing.frameRate(fps) - 1e-7), fps) : duration;
        if (!(gap > 0)) throw Error("The moved clip needs a positive insertion duration.");
        const detached = deep(S.seq); detached.tracks.find(track => track.id === tr.id).clips = detached.tracks.find(track => track.id === tr.id).clips.filter(clip => clip.id !== c.id);
        const hooks = timelineRangeHooks(detached); hooks.reuseIds = [c.id]; placement.start = at;
        const inserted = window.FilmocityTimelineRange.planInsert(detached, tracks, at, gap, hooks);
        const plan = window.FilmocityTimelineRange.planOverwrite(inserted.sequence, [{ trackId: destination, clip: placement }], hooks);
        const operations = timelineRangeOps(plan, S.seq, { type: "insert", at, duration: gap });
        if (operations.length) applyOps(operations, "insert_drag", "Ctrl-drag insert");
      } catch (error) { rollback(); renderTimeline(); renderProgram(); status(error.message, "err"); }
      return;
    }
    const ops = [], push = (trackId, clip) => ops.push({ op: "set_clip", sequence: S.seq.id, track: trackId, clip });
    if (duplicate) { S.dupPending = null; push(targetTrack, deep(c)); if (targetTrack !== tr.id) tr.clips = tr.clips.filter(x => x !== c); applyOps(ops, "duplicate", "Alt-drag duplicate"); return; }
    if (targetTrack === tr.id && lastMode === "move" && !handle) {
      // Later list entries own an overlap. Reinsert moved clips last, preserving their original project order.
      const movedClips = S.seq.tracks.flatMap(track => track.clips.filter(clip => selIds.includes(clip.id)).map(clip => ({ track, clip })));
      for (const item of movedClips) ops.push({ op: "remove_clip", sequence: S.seq.id, track: item.track.id, clip_id: item.clip.id });
      for (const item of movedClips) push(item.track.id, deep(item.clip));
    } else {
      if (targetTrack !== tr.id) { ops.push({ op: "remove_clip", sequence: S.seq.id, track: tr.id, clip_id: c.id }); push(targetTrack, deep(c)); }
      for (const clip of [...selIds.map(id => clipById(id).c), ...tr.clips.filter(x => !selIds.includes(x.id))]) { if (targetTrack !== tr.id && clip === c) continue; if (previewed.has(clip)) { const owner = clipById(clip.id); push(owner.tr.id, deep(clip)); } }
    }
    applyOps(ops, tool + (handle ? "_trim_" + handle : ""), `${tool}${handle ? " trim " + (handle === "l" ? "head" : "tail") : selIds.length > 1 ? " move " + selIds.length + " clips" : targetTrack !== tr.id ? " move to " + targetTrack : " move"}`);
  };
  watchEditGesture(ev, move, up, rollback);
}
function captionDrag(ev, cp, el) { const rollback = captureGestureFields(cp, ["start", "end"]); if (!canEdit()) return; ev.stopPropagation(); const x0 = ev.clientX, o = { ...cp }; const handle = ev.target.classList.contains("h") ? (ev.target.classList.contains("l") ? "l" : "r") : null; let moved = false;
  const move = e => { const dx = (e.clientX - x0) / S.pps; if (Math.abs(e.clientX - x0) > 2) moved = true; if (!moved) return; if (!handle) { cp.start = Math.max(0, o.start + dx); cp.end = cp.start + (o.end - o.start); } else if (handle === "l") cp.start = Math.min(o.end - 0.2, Math.max(0, o.start + dx)); else cp.end = Math.max(o.start + 0.2, o.end + dx); el.style.left = cp.start * S.pps + "px"; el.style.width = Math.max(6, (cp.end - cp.start) * S.pps) + "px"; };
  const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); if (!moved) return; const si = S.proj.sequences.indexOf(S.seq); const list = (S.seq.captions || []).map(x => x.id === cp.id ? { ...x, start: +cp.start.toFixed(3), end: +cp.end.toFixed(3) } : x); applyOps([{ op: "set", path: `/sequences/${si}/captions`, value: list }], "captions", handle ? "trim caption" : "move caption"); }; watchEditGesture(ev, move, up, rollback); }
function transitionDrag(ev, c, tr, side) { if (!canEdit()) return; ev.stopPropagation(); const key = side === "in" ? "transition_in" : "transition_out"; const d0 = c[key].duration, x0 = ev.clientX; let last = d0; const move = e => { const dx = (e.clientX - x0) / S.pps * (side === "in" ? 1 : -1); last = Math.max(frame(), Math.min(clipDur(c), +(d0 + dx).toFixed(3))); trimReadout(`${key === "transition_in" ? c.transition_in.type : c.transition_out.type} ${last.toFixed(2)}s`, e.clientX, e.clientY); const el = document.querySelector(`.clip[data-id="${c.id}"] .trans${side === "out" ? ".out" : ":not(.out)"}`); if (el) el.style.width = last * S.pps + "px"; };
  const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); trimReadout(null); if (last !== d0) applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, [key]: { ...c[key], duration: last } } }], "transition", `${key.replace("transition_", "transition ")} ${last.toFixed(2)}s`); }; watchEditGesture(ev, move, up); }
function opDrag(ev, c, tr, el) { const rollback = captureGestureFields(c, ["transform"]); if (!canEdit()) return; const y0 = ev.clientY, o0 = (c.transform || {}).opacity == null ? 1 : c.transform.opacity, line = ev.target; let moved = false; const move = e => { moved = true; const v = Math.max(0, Math.min(1, o0 - (e.clientY - y0) / 88)); c.transform = { ...(c.transform || {}), opacity: Math.round(v * 100) / 100 }; line.style.top = Math.max(4, Math.min(92, 92 - c.transform.opacity * 88)) + "%"; status(`opacity ${Math.round(c.transform.opacity * 100)}%`); renderProgram(); };
  const up = () => { if (!moved) { rollback(); return; } window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, transform: c.transform } }], "opacity_line", `opacity ${Math.round(c.transform.opacity * 100)}%`); }; watchEditGesture(ev, move, up, rollback); }
function opPenAdd(ev, c, tr, el) { const r = el.getBoundingClientRect(); const t = Math.max(0, Math.min(clipDur(c), (ev.clientX - r.left) / S.pps)); const v = Math.max(0, Math.min(1, (92 - (ev.clientY - r.top) / r.height * 100) / 88)); const list = [...((c.keyframes || {})["transform.opacity"] || [])]; if (!list.length && t > frame()) list.push({ t: 0, v: (c.transform || {}).opacity == null ? 1 : c.transform.opacity }); list.push({ t: +t.toFixed(4), v: +v.toFixed(2) }); applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, keyframes: { ...(c.keyframes || {}), "transform.opacity": list.sort((a, b) => a.t - b.t) } } }], "pen", `opacity keyframe ${Math.round(v * 100)}%`); }
function opKfDrag(ev, c, tr, el) { if (!canEdit()) return; const t0 = +ev.target.dataset.ot, list = deep(c.keyframes["transform.opacity"]).sort((a, b) => a.t - b.t), i = list.findIndex(k => Math.abs(k.t - t0) < 1e-6); if (i < 0) return; const y0 = ev.clientY, x0 = ev.clientX, v0 = list[i].v, dot = ev.target;
  const move = e => { list[i].v = Math.max(0, Math.min(1, +(v0 - (e.clientY - y0) / 88).toFixed(2))); list[i].t = Math.max(0, Math.min(clipDur(c), t0 + (e.clientX - x0) / S.pps)); dot.style.top = Math.max(4, Math.min(92, 92 - list[i].v * 88)) + "%"; dot.style.left = list[i].t * S.pps + "px"; };
  const up = e => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); if (e.altKey) list.splice(i, 1); const kf = { ...(c.keyframes || {}), "transform.opacity": list.sort((a, b) => a.t - b.t) }; if (!list.length) delete kf["transform.opacity"]; applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, keyframes: kf } }], "pen", "opacity keyframe"); }; watchEditGesture(ev, move, up); }
function gainKfDrag(ev, c, tr, el) { if (!canEdit()) return; const t0 = +ev.target.dataset.gt, list = deep(c.keyframes["audio.gain_db"]).sort((a, b) => a.t - b.t), i = list.findIndex(k => Math.abs(k.t - t0) < 1e-6); if (i < 0) return; const y0 = ev.clientY, x0 = ev.clientX, g0 = list[i].v, dot = ev.target;
  const move = e => { list[i].v = Math.round(Math.max(-40, Math.min(12, g0 - (e.clientY - y0) / 2.2)) * 2) / 2; list[i].t = Math.max(0, Math.min(clipDur(c), t0 + (e.clientX - x0) / S.pps)); dot.style.top = Math.max(4, Math.min(92, 50 - list[i].v * 2.2)) + "%"; dot.style.left = list[i].t * S.pps + "px"; status(`${list[i].v} dB @ ${fmtTC(list[i].t, S.seq.fps)}`); };
  const up = e => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); if (e.altKey) list.splice(i, 1); const kf = { ...(c.keyframes || {}), "audio.gain_db": list.sort((a, b) => a.t - b.t) }; if (!list.length) delete kf["audio.gain_db"]; applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, keyframes: kf } }], "pen", "gain keyframe"); }; watchEditGesture(ev, move, up); }
function penAdd(ev, c, tr, el) { const r = el.getBoundingClientRect(); const t = Math.max(0, Math.min(clipDur(c), (ev.clientX - r.left) / S.pps)); const v = Math.round(Math.max(-40, Math.min(12, (50 - (ev.clientY - r.top) / r.height * 100) / 2.2)) * 2) / 2; const list = [...((c.keyframes || {})["audio.gain_db"] || [])];
  if (!list.length) { const base = (c.audio || {}).gain_db || 0; if (t > frame()) list.push({ t: 0, v: base }); } list.push({ t: +t.toFixed(4), v }); applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, keyframes: { ...(c.keyframes || {}), "audio.gain_db": list.sort((a, b) => a.t - b.t) } } }], "pen", `gain keyframe ${v} dB`); }
function rateStretch(ev, c, tr, el, handle) { const rollback = captureGestureFields(c, ["speed", "start", "time_remap"]); if (!canEdit()) return; if (!c.media_id) return; const x0 = ev.clientX, o = deep(c), src = o.out - o.in_; let moved = false; const move = e => { moved = true; const dx = (e.clientX - x0) / S.pps; let nd = handle === "r" ? Math.max(frame() * 2, clipDur(o) + dx) : Math.max(frame() * 2, clipDur(o) - dx); c.speed = +(src / nd).toFixed(4); c.time_remap = null; if (handle === "l") c.start = clipEnd(o) - nd; el.style.left = c.start * S.pps + "px"; el.style.width = clipDur(c) * S.pps + "px"; status(`speed ${Math.round(c.speed * 100)}%`); };
  const up = () => { if (!moved) { rollback(); return; } window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, speed: c.speed, start: c.start, time_remap: null } }], "rate_stretch", `rate stretch ${Math.round(c.speed * 100)}%`); }; watchEditGesture(ev, move, up, rollback); }
function gainDrag(ev, c, tr, el) { const rollback = captureGestureFields(c, ["audio"]); if (!canEdit()) return; const y0 = ev.clientY, g0 = (c.audio || {}).gain_db || 0; const line = ev.target; let moved = false; const move = e => { moved = true; const g = Math.max(-40, Math.min(12, g0 - (e.clientY - y0) / 2.2)); c.audio = { ...(c.audio || {}), gain_db: Math.round(g * 2) / 2 }; line.style.top = Math.max(4, Math.min(92, 50 - c.audio.gain_db * 2.2)) + "%"; line.title = `gain ${c.audio.gain_db} dB`; status(`gain ${c.audio.gain_db} dB`); };
  const up = () => { if (!moved) { rollback(); return; } window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, audio: c.audio } }], "gain_line", `gain ${c.audio.gain_db} dB`); }; watchEditGesture(ev, move, up, rollback); }
function rippleFrom(tr, c, o, d) { for (const x of tr.clips) if (x.id !== c.id && x.start >= clipEnd(o) - 1e-6) x.start = Math.max(0, x.start + d); }
function marqueeDrag(ev) { const mq = $("#marquee"), body = $("#tlBody"), r0 = body.getBoundingClientRect(); const sx = ev.clientX - r0.left + body.scrollLeft, sy = ev.clientY - r0.top + body.scrollTop; let did = false;
  const move = e => { const x = e.clientX - r0.left + body.scrollLeft, y = e.clientY - r0.top + body.scrollTop; did = true; mq.style.display = "block"; mq.style.left = Math.min(sx, x) + "px"; mq.style.top = Math.min(sy, y) + "px"; mq.style.width = Math.abs(x - sx) + "px"; mq.style.height = Math.abs(y - sy) + "px";
    const box = mq.getBoundingClientRect(); S.sel.clear(); $$(".clip:not(.linked)").forEach(el => { const b = el.getBoundingClientRect(); if (b.right > box.left && b.left < box.right && b.bottom > box.top && b.top < box.bottom) S.sel.add(el.dataset.id); }); refreshSel(); };
  const up = e => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); mq.style.display = "none"; if (!did) seekTo(xToT(e.clientX)); CR.panels.render(); }; window.addEventListener("mousemove", move); window.addEventListener("mouseup", up); }
function panDrag(ev) { const body = $("#tlBody"), x0 = ev.clientX, s0 = body.scrollLeft; const move = e => { body.scrollLeft = s0 - (e.clientX - x0); }; const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); }; window.addEventListener("mousemove", move); window.addEventListener("mouseup", up); }
function selectBackward(t) { S.sel.clear(); for (const tr of S.seq.tracks) for (const c of tr.clips) if (c.start < t) S.sel.add(c.id); refreshSel(); CR.panels.render(); }
function zoomAt(ev) { const body = $("#tlBody"), tAt = xToT(ev.clientX), f = ev.altKey ? 1 / 1.6 : 1.6; S.pps = Math.min(600, Math.max(6, S.pps * f)); renderTimeline(); body.scrollLeft = tAt * S.pps - (ev.clientX - body.getBoundingClientRect().left); }
function selectForward(t) { S.sel.clear(); for (const tr of S.seq.tracks) for (const c of tr.clips) if (clipEnd(c) > t) S.sel.add(c.id); refreshSel(); CR.panels.render(); }
function updatePlayhead() { const sb = $("#prgScrub"); if (sb) sb.firstElementChild.style.width = Math.min(100, S.t / Math.max(seqDur(), 0.01) * 100) + "%"; $("#playhead").style.left = S.t * S.pps + "px"; const rp = $("#rulerPlay"); if (rp) rp.style.left = S.t * S.pps + "px"; $("#prgTC").textContent = $("#tlTC").textContent = fmtTC(S.t, S.seq.fps); renderProgram(); if (!S.playing && CR.panels && S.sel.size === 1 && !document.activeElement.matches("input,textarea,select")) CR.panels.renderEC(); }

// ---------- program monitor: canvas compositor ----------
const pool = {}; // clip occurrence -> <video>; repeated media has independent playheads
function pictureDisplaySize(media, decoded, fallbackWidth, fallbackHeight) {
  // Browser intrinsic dimensions describe the actual decoded picture, including
  // a proxy's scale. Never apply the original SAR a second time to those pixels.
  if (decoded?.readyState >= 1 && Number.isFinite(decoded.videoWidth) && decoded.videoWidth > 0 && Number.isFinite(decoded.videoHeight) && decoded.videoHeight > 0) return [decoded.videoWidth, decoded.videoHeight];
  let width = Number.isFinite(media?.width) && media.width > 0 ? media.width : fallbackWidth;
  let height = Number.isFinite(media?.height) && media.height > 0 ? media.height : fallbackHeight;
  const match = typeof media?.sample_aspect_ratio === "string" && /^([0-9]+)[:/]([0-9]+)$/.exec(media.sample_aspect_ratio);
  const ratio = match && +match[2] > 0 ? +match[1] / +match[2] : 1;
  if (Number.isFinite(ratio) && ratio > 0 && ratio !== 1) {
    // Probe dimensions already include rotation. Quarter-turn rotation moves
    // the decoder's horizontal sample aspect correction to display height.
    if (Number.isFinite(media.rotation) && media.rotation % 180 !== 0 && media.rotation % 90 === 0) height = Math.max(1, Math.round(height * ratio));
    else width = Math.max(1, Math.round(width * ratio));
  }
  return [width, height];
}
function programPictureSize(c, tr, width, height) {
  const track = tr || S.seq.tracks.find(item => item.clips.includes(c));
  const key = track && FilmocityAudioPreview.voiceKey(S.context, S.seq.id, ["program"], track.id, c.id);
  return pictureDisplaySize(S.proj.media[c.media_id], key ? pool[key] : null, width, height);
}
function sourceMediaUrl(m) { return FilmocityProxyPreview.playbackUrl(m, S.mediaAvailability?.[m.id], S.context, S.useProxy, nativePlayable(m)); }
function updateSourcePresentation() {
  const video = $("#srcVideo"), panel = $("#srcAudioPreview"), wave = $("#srcAudioWave"), image = $("#srcAudioWaveImage"), label = $("#srcAudioLabel");
  const media = S.src, audioOnly = !!media?.has_audio && !media.has_video;
  if (video) { video.style.visibility = audioOnly ? "hidden" : ""; if (audioOnly) video.setAttribute("aria-hidden", "true"); else video.removeAttribute("aria-hidden"); }
  if (!panel) return;
  const token = {}; panel.sourceOwner = token; panel.style.display = audioOnly ? "flex" : "none";
  if (image) { image.onerror = null; image.removeAttribute("src"); }
  if (wave) wave.style.display = "none";
  if (label) label.textContent = audioOnly ? "Audio-only source — " + (media.name || "Selected audio") : "";
  if (!audioOnly || !image || !wave || !FilmocityProxyPreview.aliasWaveReady(media, S.mediaAvailability?.[media.id])) return;
  // Alias waveform/proxy preparation spans the complete native source clock.
  // Show only this alias's logical range; interpretation affects both ends.
  try {
    const full = Number(media.proxy_info?.native_duration), begin = sourceMonitorTime(media, 0), end = sourceMonitorTime(media, media.duration), span = end - begin;
    if (!Number.isFinite(full) || full <= 0 || !Number.isFinite(span) || span <= 0 || begin < 0 || end > full + 1e-7) return;
    image.style.width = full / span * 100 + "%"; image.style.left = -begin / span * 100 + "%";
    image.onerror = () => { if (panel.sourceOwner === token && S.src === media) wave.style.display = "none"; };
    image.src = media.wave; wave.style.display = "block";
  } catch { /* The audio placeholder remains usable without a waveform. */ }
}
function updateSourcePreviewState() { updateSourcePresentation(); const el = $("#srcPreviewState"); if (el) el.textContent = S.src ? FilmocityProxyPreview.availabilityMessage(S.src, S.mediaAvailability?.[S.src.id], nativePlayable(S.src), S.useProxy) : ""; }
function sourcePreviewError(message) { status(message + " Refresh media status in Project to check the original and proxy.", "err"); updateSourcePreviewState(); }
const AG = { ctx: null, nodes: {}, buses: {}, groups: {}, master: null, limiter: null, analyser: null, splitter: null, an: null };
function audioCtx() {
  if (!AG.ctx) {
    let ctx, strip; const owned = [];
    try {
      ctx = new (window.AudioContext || window.webkitAudioContext)();
      const make = (method, ...args) => { const node = ctx[method](...args); owned.push(node); return node; };
      strip = FilmocityAudioPreview.busNodes(ctx);
      const splitter = make("createChannelSplitter", 2), an = [make("createAnalyser"), make("createAnalyser")], mix = make("createGain");
      an.forEach((node, i) => { node.fftSize = 512; splitter.connect(node, i); });
      strip.gain.connect(mix); mix.connect(ctx.destination); mix.connect(splitter);
      Object.assign(AG, { ctx, rootStrip: strip, rootInput: strip.input, master: strip.gain, limiter: strip.limit, splitter, an, mix });
    } catch (error) { strip?.dispose(); FilmocityAudioPreview.disposeNodes(owned); ctx?.close?.().catch(() => {}); }
  }
  if (AG.ctx?.state === "suspended") AG.ctx.resume().catch(() => {}); return AG.ctx;
}
function routeRenderedAudio(element) {
  if (AG.rendered?.element === element) return;
  if (AG.rendered) { AG.rendered.src.disconnect(); AG.rendered = null; }
  if (!element) return;
  const ctx = audioCtx(); if (!ctx) return;
  AG.renderSources ||= new WeakMap(); let src = AG.renderSources.get(element);
  try { if (!src) { src = ctx.createMediaElementSource(element); AG.renderSources.set(element, src); } src.connect(AG.mix); AG.rendered = { element, src }; }
  catch (error) { status("Rendered audio meters are unavailable: " + error.message, "err"); }
}
function routeAudioStage(...args) { FilmocityAudioPreview.routeStage(...args); }
function busFor(trackId, target) {
  const ctx = audioCtx(); if (!ctx) return null; target ||= AG.rootInput;
  let b = AG.buses[trackId];
  if (!b) {
    try { b = FilmocityAudioPreview.busNodes(ctx); b.an = ctx.createAnalyser(); b.an.fftSize = 256; b.target = null; AG.buses[trackId] = b; }
    catch (error) { b?.dispose(); b?.an?.disconnect(); return null; }
  }
  if (b.target !== target) { b.gain.disconnect(); b.gain.connect(target); b.gain.connect(b.an); b.target = target; }
  return b;
}
function disposeAudioBus(bus) { bus.dispose(); bus.an.disconnect(); }
function sequenceMasterNodes(key, owner) {
  let n = AG.nodes[key]; if (n?.element === owner) return n;
  if (n) FilmocityAudioPreview.disposeNodes(n);
  try { n = FilmocityAudioPreview.busNodes(audioCtx()); Object.assign(n, { element: owner, src: n.input, out: n.gain }); AG.nodes[key] = n; owner._audioError = false; return n; }
  catch (error) { owner._audioError = true; return null; }
}
function audioNodesFor(mid, v) { const ctx = audioCtx(); if (!ctx) return null; let n = AG.nodes[mid]; if (n?.element === v) return n; if (n) { FilmocityAudioPreview.disposeNodes(n); delete AG.nodes[mid]; }
  if (v._audioError) return null;
  const owned = []; const make = (method, ...args) => { const node = ctx[method](...args); owned.push(node); return node; };
  try { const src = v._mixOwner ? make("createGain") : make("createMediaElementSource", v); const gain = make("createGain"), pan = make("createGain"), low = make("createBiquadFilter"), mid_ = make("createBiquadFilter"), high = make("createBiquadFilter"), comp = make("createDynamicsCompressor"), limit = make("createDynamicsCompressor"), makeup = make("createGain"), out = make("createGain");
    low.type = "lowshelf"; low.frequency.value = 120; mid_.type = "peaking"; mid_.frequency.value = 1000; mid_.Q.value = 1; high.type = "highshelf"; high.frequency.value = 6000; comp.threshold.value = 0; comp.ratio.value = 1; FilmocityAudioPreview.configureLimiter(limit);
    const channels = FilmocityAudioPreview.matrixNodes(ctx, make); src.connect(channels.input); channels.output.connect(low); low.connect(mid_); mid_.connect(high); high.connect(makeup); pan.connect(gain); gain.connect(out);
    const timeFx = FilmocityAudioTimeFX.create(ctx, makeup, pan); let disposed = false;
    n = { element: v, channels, src, gain, pan, low, mid: mid_, high, comp, limit, makeup, compressed: false, limited: false, out, bus: null, timeFx,
      dispose() { if (disposed) return; disposed = true; timeFx.dispose(); FilmocityAudioPreview.disposeNodes(owned); } };
    AG.nodes[mid] = n; v._audioError = false; return n;
  } catch (e) { FilmocityAudioPreview.disposeNodes(owned); v._audioError = true; return null; }
}
function configureAudioGraph(n, c, rel, duration) { const ctx = AG.ctx; const au = c.audio || {}, fx = c.audio_fx || {}, kf = c.keyframes || {}; const t = ctx.currentTime;
  const gdb = kfVal(kf["audio.gain_db"], rel, au.gain_db || 0) + FilmocityAudioPreview.duckDb(c, rel);
  if (!Number.isFinite(gdb)) { n.out.gain.setValueAtTime(0, t); return false; } n.channels.update(au.channels || S.proj.media[c.media_id]?.channel_mode, au.pan, t);
  const eq = fx.eq || {}; FilmocityAudioPreview.configureEQ(n, eq, t);
  const cp = fx.comp || {}; FilmocityAudioPreview.configureCompressor(n.comp, cp);
  if (!n.timeFx.update(S.playing ? c.afx_stack : [])) { n.out.gain.setValueAtTime(0, t); return false; }
  for (const f of (c.afx_stack || [])) { if (f.enabled === false) continue; const p = f.params || {};
    if (f.type === "bass") n.low.gain.setTargetAtTime((eq.low_db || 0) + (p.gain_db == null ? 3 : p.gain_db), t, 0.02);
    else if (f.type === "treble") n.high.gain.setTargetAtTime((eq.high_db || 0) + (p.gain_db == null ? 3 : p.gain_db), t, 0.02);
    else if (f.type === "highpass") { n.low.type = "highpass"; n.low.frequency.value = p.frequency || 80; }
    else if (f.type === "lowpass") { n.high.type = "lowpass"; n.high.frequency.value = p.frequency || 8000; }
    else if (f.type === "compressor") { n.comp.threshold.value = p.threshold_db == null ? -18 : p.threshold_db; n.comp.ratio.value = Math.max(1, Math.min(20, p.ratio || 3)); }
  }
  routeAudioStage(n.high, n.comp, n.makeup, cp.enabled || (c.afx_stack || []).some(f => f.enabled !== false && f.type === "compressor"), n, "compressed");
  routeAudioStage(n.pan, n.limit, n.gain, fx.limiter, n, "limited");
  n.makeup.gain.setValueAtTime(Math.pow(10, (cp.enabled ? Number(cp.makeup_db || 0) : 0) / 20), t);
  const stackGain = (c.afx_stack || []).filter(f => f.enabled !== false && f.type === "amplify").reduce((sum, f) => sum + Number(f.params?.gain_db || 0), 0);
  const fade = duration == null ? 1 : FilmocityAudioPreview.fadeGain(c, duration, rel);
  n.gain.gain.setValueAtTime(Math.pow(10, (gdb + stackGain) / 20) * fade, t); n.out.gain.setValueAtTime(1, t); return true;
}
function applyAudioParams(mid, v, c, tr, rel, sq = S.seq, voicePath = ["program"], target = null) {
  const n = audioNodesFor(mid, v); if (!n) return false;
  if (!configureAudioGraph(n, c, rel, clipDur(c))) { v._audioError = true; return false; } const t = AG.ctx.currentTime;
  const btr = FilmocityAudioPreview.destination(sq, tr), busId = JSON.stringify([sq.id, voicePath, btr.id]), bus = busFor(busId, target);
  if (!bus) { n.out.gain.setValueAtTime(0, t); n.timeFx.clear(); v._audioError = true; return false; }
  bus.trackId = btr.id; bus.root = sq === S.seq && voicePath[0] === "program";
  if (n.bus !== bus) { n.out.disconnect(); n.out.connect(bus.input); n.bus = bus; }
  bus.update(btr.audio_fx, btr.gain_db, t);
  const master = S.seq.master || {}; AG.rootStrip.update(master.audio_fx, master.gain_db, t);
  v._audioError = false; return true;
}
function nestedAudioTarget(sq, sub, tr, c, rel, path, used, target) {
  const key = "nested:" + FilmocityAudioPreview.voiceKey(S.context, sq.id, path, tr.id, c.id);
  const owner = AG.groups[key] || (AG.groups[key] = { _mixOwner: true }); used.add(key);
  if (!applyAudioParams(key, owner, c, tr, rel, sq, path, target)) return null;
  const parent = AG.nodes[key], masterKey = key + ":master";
  const masterOwner = AG.groups[masterKey] || (AG.groups[masterKey] = { _mixOwner: true }); used.add(masterKey);
  const master = sequenceMasterNodes(masterKey, masterOwner); if (!master) { parent.out.gain.setValueAtTime(0, AG.ctx.currentTime); parent.timeFx.clear(); return null; }
  master.update(sub.master?.audio_fx, sub.master?.gain_db);
  if (master.parent !== parent) { master.out.disconnect(); master.out.connect(parent.src); master.parent = parent; }
  return master.src;
}

function retireVoice(key) {
  const v = pool[key], n = AG.nodes[key];
  if (v) { v.pause(); v.muted = true; } if (n && AG.ctx) n.out.gain.setValueAtTime(0, AG.ctx.currentTime);
  FilmocityAudioPreview.disposeNodes(n); delete AG.nodes[key];
  if (v) { v.removeAttribute("src"); v.load(); v.remove(); delete pool[key]; }
}
function resetPreviewVoices() {
  for (const key of Object.keys(pool)) retireVoice(key);
  for (const bus of Object.values(AG.buses)) disposeAudioBus(bus);
  AG.buses = {};
  for (const key of Object.keys(AG.groups)) { FilmocityAudioPreview.disposeNodes(AG.nodes[key]); delete AG.nodes[key]; } AG.groups = {};
}
function sweepVoices(used) {
  const idle = [];
  for (const [key, v] of Object.entries(pool)) v._activeVoice = used.has(key);
  for (const [key, v] of Object.entries(pool)) if (!used.has(key)) {
    if (!v.paused) v.pause(); const n = AG.nodes[key]; if (n && AG.ctx) { n.out.gain.setValueAtTime(0, AG.ctx.currentTime); n.timeFx?.clear(); } idle.push(key);
  }
  // Keep a small seek cache; old oscillator/decoder graphs cannot grow forever.
  idle.sort((a,b) => pool[b]._lastUse - pool[a]._lastUse);
  for (const key of idle.slice(16)) retireVoice(key);
  for (const key of Object.keys(AG.groups)) if (!used.has(key)) { FilmocityAudioPreview.disposeNodes(AG.nodes[key]); delete AG.nodes[key]; delete AG.groups[key]; }
  const liveBuses = new Set(Object.values(AG.nodes).map(n => n.bus));
  for (const [key,bus] of Object.entries(AG.buses)) if (!liveBuses.has(bus)) { disposeAudioBus(bus); delete AG.buses[key]; }
}
let audioStatusCache = null;
function updateAudioPreviewStatus(rendered) {
  const el = $("#audioPreviewStatus"); if (!el || !S.seq) return;
  let message = "Rendered picture and audio";
  if (!rendered) {
    const now = performance.now();
    // Legacy edits can mutate a sequence in place. Refresh at most twice a
    // second while playing, and immediately when its owner changes.
    if (!audioStatusCache || audioStatusCache.seq !== S.seq || audioStatusCache.project !== S.proj || now - audioStatusCache.at >= 500 || !S.playing) {
      audioStatusCache = { seq: S.seq, project: S.proj, at: now, reasons: FilmocityAudioPreview.limitations(S.seq, S.proj) };
    }
    const reasons = audioStatusCache.reasons;
    const waitingForChannel = Object.values(pool).some(v => v._activeVoice && v._channelPreviewUnavailable);
    const failed = Object.values(pool).some(v => v._activeVoice && v._audioError) || Object.values(AG.groups).some(v => v._audioError);
    const reverseAudio = Object.values(pool).some(v => v._activeVoice && v._reverseAudioUnavailable);
    message = waitingForChannel ? "Selected-channel live audio unavailable · Prepare media in Project, or use a rendered preview" : reverseAudio ? "Reverse audio is muted · render preview to hear the mix" : failed ? "Live audio unavailable · use a rendered preview to hear the mix" : reasons.length ? "Live audio estimate · render preview to check " + reasons.join(", ") : "Live stereo mix · verify final delivery with a rendered preview";
  }
  if (el.textContent !== message) el.textContent = message;
}

const NATIVE_OK = new Set(["mp4", "m4v", "webm", "mov", "ogv", "mp3", "m4a", "aac", "wav", "ogg", "oga", "opus", "flac", "png", "jpg", "jpeg", "webp", "gif", "bmp", "avif", "mkv"]);
function nativePlayable(m) { const ext = (m.path || "").split(".").pop().toLowerCase(); if (m.synthetic || m.is_image) return true; if (m.sequence_frames) return false; if (!NATIVE_OK.has(ext)) return false; if ((m.pix_fmt || "").includes("10") || (m.pix_fmt || "").includes("422") || (m.pix_fmt || "").includes("444")) return false; return true; }
function vidFor(mid, key = JSON.stringify([S.context?.workspace, S.context?.project, "external", mid])) {
  let v = pool[key]; const m = S.proj.media[mid], src = sourceMediaUrl(m);
  if (!v) { v = document.createElement("video"); v.preload = "auto"; v.crossOrigin = "anonymous"; v.style.display = "none"; document.body.appendChild(v); pool[key] = v;
    v.addEventListener("loadeddata", () => { if (!S.playing) renderProgram(); }); v.addEventListener("seeked", () => { if (!S.playing) renderProgram(); }); }
  if (src && v.getAttribute("src") !== src) v.src = src; else if (!src && v.getAttribute("src")) { v.pause(); v.removeAttribute("src"); v.load(); }
  v._channelPreviewUnavailable = FilmocityProxyPreview.channelAlias(m) && !src;
  v._lastUse = performance.now(); return v;
}
function pictureSourcePosition(clip, local, media) {
  const clock = { duration: clipDur(clip), sourceOffset: t => sourceOffset(clip, t) };
  const source = window.FilmocitySourceClock.sourceTime(clip, local, clock);
  if (source == null) throw Error("Preview time is outside the clip.");
  const native = window.FilmocitySourceClock.nativeTime(media, source);
  if (!clip.reverse || clip.hold || media.is_image || media.has_video === false) return native;
  // Reversed ranges are half-open: Out itself is never a retained picture.
  const timing = window.FilmocityTime, rate = timing.frameRate(media.frame_rate ?? media.fps);
  const count = native * rate, epsilon = value => Math.max(1e-7, Math.abs(value) * Number.EPSILON * 8);
  const firstCount = window.FilmocitySourceClock.nativeTime(media, clip.in_) * rate;
  const endCount = window.FilmocitySourceClock.nativeTime(media, clip.out) * rate;
  const first = Math.ceil(firstCount - epsilon(firstCount)), last = Math.ceil(endCount - epsilon(endCount)) - 1;
  const frame = Math.max(first, Math.min(last, Math.ceil(count - epsilon(count)) - 1));
  if (!Number.isSafeInteger(frame) || frame < 0 || last < first) throw Error("The reversed preview range contains no complete source frame.");
  return timing.fromFrames(frame, rate);
}

function seekPreviewPicture(video, position, media, { playing = S.playing, strictPlayback = false, begin = 0, end: limit = video.duration } = {}) {
  const current = Number.isFinite(video.currentTime) ? video.currentTime : 0;
  let target = position;
  if (!playing && media?.has_video && !media.is_image && !media.vfr) {
    // A paused CFR picture owns its native frame, not the sequence's rate.
    // Seek inside that frame: browsers can round its leading timestamp down
    // and present the preceding picture. Repeated seeked redraws must not seek
    // again once currentTime is safely inside the same retained frame.
    try {
      const timing = window.FilmocityTime, rate = timing.frameRate(media.frame_rate ?? media.native_fps ?? media.fps);
      let index = timing.displayFrame(Math.max(0, position), rate);
      const duration = Math.min(Number.isFinite(video.duration) && video.duration > 0 ? video.duration : Infinity,
        Number.isFinite(limit) && limit > begin ? limit : Infinity);
      if (Number.isFinite(duration) && duration > 0) {
        const count = duration * rate, epsilon = Math.max(1e-7, Math.abs(count) * Number.EPSILON * 8);
        index = Math.min(index, Math.max(0, Math.ceil(count - epsilon) - 1));
      }
      const frameStart = timing.fromFrames(index, rate), start = Math.max(begin, frameStart);
      const end = Math.min(frameStart + 1 / rate, duration);
      if (end <= start) return null;
      const margin = Math.min(1e-6, (end - start) / 4);
      if (current > start + margin && current < end - margin) return false;
      target = start + (end - start) / 2;
    } catch {} // Missing native rate retains the existing approximate live path.
  }
  const tolerance = playing ? (strictPlayback ? 1e-9 : 0.15) : media?.has_video && !media.is_image ? 1e-6 : strictPlayback ? 1e-9 : 0.03;
  if (!Number.isFinite(target)) return null;
  if (Math.abs(current - target) <= tolerance) return false;
  try { video.currentTime = target; return true; } catch { return null; }
}

function activeClipsOf(sq, t) { return sq.tracks.filter(x => !x.muted).sort((a, b) => (a.kind === b.kind ? a.index - b.index : a.kind === "video" ? 1 : -1)).flatMap(tr => { const out = []; for (const c of tr.clips) { if (c.enabled === false || t < c.start || t >= clipEnd(c)) continue;
      const D = ((c.transition_in || {}).duration) || 0; if (tr.kind === "video" && D > 0 && t - c.start < D) { const prev = tr.clips.find(p => p !== c && p.enabled !== false && Math.abs(clipEnd(p) - c.start) < 1 / (sq.fps || 30)); if (prev) out.push({ c: { ...prev, out: prev.out + D * (prev.speed || 1), transition_out: null, _extended: true }, tr }); }  // the outgoing clip keeps playing under the transition, as in the export
      out.push({ c, tr }); } return out; }); }
const imageCache = {}; function imageFor(path) { let im = imageCache[path]; if (!im) { im = new Image(); im.src = path.startsWith("/") && !path.startsWith("/api/") && !path.startsWith("/assets/") ? "/api/media/path?p=" + encodeURIComponent(path) : path; im.onload = () => renderProgram(); imageCache[path] = im; } return im; }
function easeVal(kind, p) { p = Math.max(0, Math.min(1, p)); switch (kind) { case "linear": return p; case "ease_in": return p ** 3; case "ease_in_out": return p < 0.5 ? 4 * p ** 3 : 1 - (-2 * p + 2) ** 3 / 2; case "back_out": return 1 + 2.70158 * (p - 1) ** 3 + 1.70158 * (p - 1) ** 2; case "bounce": return 1 - Math.abs(Math.cos(p * Math.PI * 1.5)) * (1 - p); default: return 1 - (1 - p) ** 3; } }
function layerState(L, rel, cd, kf, li) { const W = S.seq.width, H = S.seq.height; const st = { alpha: L.opacity == null ? 1 : L.opacity, scale: L.scale || 1, rot: (L.rotation || 0) * Math.PI / 180, dx: 0, dy: 0, clip: null, text: null, hidden: false };
  if (kf && li != null) { const g = k => kf[`g${li}.${k}`]; if (g("x")) st.dx += kfVal(g("x"), rel, 0); if (g("y")) st.dy += kfVal(g("y"), rel, 0); if (g("opacity")) st.alpha *= kfVal(g("opacity"), rel, 1); if (g("scale")) st.scale *= kfVal(g("scale"), rel, 1); if (g("rotation")) st.rot += kfVal(g("rotation"), rel, 0) * Math.PI / 180; }
  for (const [an, isOut] of [[L.anim_in || {}, false], [L.anim_out || {}, true]]) { const ty = an.type; if (!ty || ty === "none") continue; const d = Math.max(0.05, an.duration || 0.6), dl = an.delay || 0; const p = isOut ? (rel - (cd - d - dl)) / d : (rel - dl) / d; if (isOut ? p <= 0 : p >= 1) continue; const q = isOut ? 1 - easeVal(an.ease, p) : easeVal(an.ease, p); const dist = an.distance || 0.25;
    if (ty === "typewriter" && !isOut) { const n = (L.text || "").length; st.text = (L.text || "").slice(0, Math.max(0, Math.ceil(n * Math.max(0, Math.min(1, (rel - dl) / d))))); if (!st.text.length) st.hidden = true; continue; }
    if (["fade", "rise", "drop", "blur_in", "blur_out", "rotate_in", "zoom", "pop"].includes(ty)) st.alpha *= q;
    if (ty === "slide_left") st.dx -= W * dist * (1 - q); if (ty === "slide_right") st.dx += W * dist * (1 - q); if (ty === "slide_up") st.dy += H * dist * (1 - q); if (ty === "slide_down") st.dy -= H * dist * (1 - q); if (ty === "rise") st.dy += H * 0.06 * (1 - q); if (ty === "drop") st.dy -= H * 0.06 * (1 - q);
    if (ty === "pop") st.scale *= 0.6 + 0.4 * q; if (ty === "zoom") st.scale *= 1.25 - 0.25 * q; if (ty === "rotate_in") st.rot -= 0.35 * (1 - q);
    if (ty === "wipe_left") st.clip = [0, 0, W * q, H]; if (ty === "wipe_right") st.clip = [W * (1 - q), 0, W * q, H]; if (ty === "wipe_up") st.clip = [0, H * (1 - q), W, H * q]; if (ty === "wipe_down") st.clip = [0, 0, W, H * q]; } return st; }
function drawText(ctx, t, W, H, dflt, px, alpha) { if (t.uppercase) t = { ...t, text: String(t.text || "").toUpperCase() }; const size = t.size || dflt; const fam = window.FilmocityProjectResources.family(t); const weight = (t.weight || "bold") === "regular" ? 400 : 700; ctx.save(); ctx.globalAlpha = alpha == null ? 1 : alpha; ctx.font = `${weight} ${size}px ${fam}, sans-serif`; ctx.textBaseline = "top";
  const lines = String(t.text || "").split("\n"), lh = size * 1.0 + (t.line_spacing == null ? size * 0.15 : t.line_spacing), th = lines.length * lh - (t.line_spacing == null ? size * 0.15 : t.line_spacing); const tw = Math.max(...lines.map(l => ctx.measureText(l).width));
  const align = t.align || "center", valign = t.valign || "center"; const x0 = align === "left" ? W * 0.06 : align === "right" ? W - W * 0.06 - tw : (W - tw) / 2; const y0 = valign === "top" ? H * 0.08 : valign === "bottom" ? H - th - H * 0.12 : (H - th) / 2; const X = x0 + (t.x || 0) + (px || 0), Y = y0 + (t.y || 0);
  if (t.box) { const pad = t.boxpad == null ? size * 0.35 : t.boxpad; ctx.fillStyle = cssColor(t.boxcolor || "black@0.6"); ctx.fillRect(X - pad, Y - pad, tw + pad * 2, th + pad * 2); }
  lines.forEach((ln, i) => { const lx = align === "center" ? X + (tw - ctx.measureText(ln).width) / 2 : align === "right" ? X + tw - ctx.measureText(ln).width : X; const ly = Y + i * lh;
    if (t.shadow) { ctx.fillStyle = cssColor(t.shadowcolor || "black@0.6"); ctx.fillText(ln, lx + (t.shadowx == null ? size * 0.04 : t.shadowx), ly + (t.shadowy == null ? size * 0.04 : t.shadowy)); }
    if (t.borderw) { ctx.lineWidth = t.borderw * 2; ctx.strokeStyle = cssColor(t.bordercolor || "black"); ctx.strokeText(ln, lx, ly); } ctx.fillStyle = cssColor(t.color || "white"); ctx.fillText(ln, lx, ly); }); ctx.restore(); }
function cssColor(c) { c = String(c || "white"); let a = 1; if (c.includes("@")) { const [col, al] = c.split("@"); c = col; a = parseFloat(al); } if (c.startsWith("0x")) c = "#" + c.slice(2); if (a < 1) { const tmp = document.createElement("canvas").getContext("2d"); tmp.fillStyle = c; const hex = tmp.fillStyle; if (hex.startsWith("#") && hex.length === 7) return `rgba(${parseInt(hex.slice(1, 3), 16)},${parseInt(hex.slice(3, 5), 16)},${parseInt(hex.slice(5, 7), 16)},${a})`; } return c; }
function activeClips(t) { return S.seq.tracks.filter(x => !x.muted).sort((a, b) => (a.kind === b.kind ? a.index - b.index : a.kind === "video" ? 1 : -1)).flatMap(tr => tr.clips.filter(c => t >= c.start && t < clipEnd(c)).map(c => ({ c, tr }))); }
function stackCss(c) { const out = []; for (const fx of (c.fx_stack || [])) { if (fx.enabled === false) continue; const p = fx.params || {}; const d = k => (p[k] ?? ((CR.catalog || { video: [] }).video.find(e => e.type === fx.type) || { params: {} }).params[k]?.default ?? 0);
    switch (fx.type) { case "procamp": out.push(`brightness(${1 + d("brightness") / 200}) contrast(${d("contrast") / 100}) saturate(${d("saturation") / 100}) hue-rotate(${d("hue")}deg)`); break; case "extract": case "black_white": out.push("grayscale(1)"); break; case "invert": out.push("invert(1)"); break; case "gaussian_blur": out.push(`blur(${d("blurriness") / 4}px)`); break; case "camera_blur": out.push(`blur(${d("camera_blur") / 6 || d("percent") / 6}px)`); break; case "tint": out.push(`sepia(${d("amount") / 100})`); break; case "vibrance_fx": out.push(`saturate(${1 + d("amount") * 0.5})`); break; case "levels": out.push(`contrast(${(255 / Math.max(1, d("input_white") - d("input_black"))).toFixed(2)})`); break; } } return out.join(" "); }
function stackCssNonColor(c) { const out = []; for (const fx of (c.fx_stack || [])) { if (fx.enabled === false) continue; const p = fx.params || {}; if (fx.type === "gaussian_blur") out.push(`blur(${(p.blurriness ?? 10) / 4}px)`); else if (fx.type === "camera_blur") out.push(`blur(${(p.percent ?? 10) / 6}px)`); } return out.join(" ") || "none"; }
function cssFilter(col, c) { const st = c ? stackCss(c) : ""; if (!col && !st) return "none"; const f = st ? [st] : []; if (col.exposure) f.push(`brightness(${1 + col.exposure * 0.5})`); if (col.contrast) f.push(`contrast(${1 + col.contrast})`); if (col.saturation) f.push(`saturate(${Math.max(0, 1 + col.saturation)})`); if (col.temperature) f.push(`sepia(${Math.min(Math.abs(col.temperature) * 0.35, 0.6)})`, `hue-rotate(${col.temperature > 0 ? 0 : 180}deg)`); return f.length ? f.join(" ") : "none"; }
const offscreen = {}; const BLEND_CANVAS = { multiply: "multiply", screen: "screen", overlay: "overlay", darken: "darken", lighten: "lighten", difference: "difference", add: "lighter", softlight: "soft-light", hardlight: "hard-light", exclusion: "exclusion", subtract: "difference" };
const pv = { el: null, file: null, range: null };
let renderedPreview = null;
function previewView() {
  const state = projectSaveState();
  return { context: S.context, sequence: S.seq?.id, revision: state.revision, pending: state.pending, error: state.error,
    gesture: !!S.gesture, switching: !!(S.switching || S.commandPending || S.versionAction || S.recoveryRequired) };
}
function releasePreviewVideo() {
  routeRenderedAudio(null);
  if (pv.el) { pv.el.onloadeddata = pv.el.onseeked = pv.el.onerror = null; pv.el.pause(); pv.el.removeAttribute("src"); pv.el.load(); pv.el.remove(); pv.el = null; }
  pv.file = pv.range = null; S.previewMode = false;
}
function getRenderedPreview() {
  if (!renderedPreview) renderedPreview = window.FilmocityRenderedPreview.create({
    capture: previewView, flush: flushSaves, api: window.FilmocityRenderedPreview.transport(fetch, readApiResponse),
    clear: releasePreviewVideo,
    ready(result, enabled) { pv.file = result.out; pv.range = result.preview.range.slice(); S.previewMode = enabled; },
    changed(state) {
      const label = $("#previewMessage"), cancel = $("#previewCancel");
      if (label) label.textContent = state.message;
      if (cancel) { cancel.hidden = ["idle", "ready", "error"].includes(state.phase) || (state.phase === "checking" && !state.job); cancel.disabled = state.phase === "cancelling"; cancel.onclick = () => renderedPreview.cancel(); }
    },
    finished() { refreshRenderBar(true); renderProgram(); },
  });
  return renderedPreview;
}
function invalidateRenderedPreview() { if (exactFrames) exactFrames.invalidate(); if (renderedPreview) renderedPreview.invalidate(); if (renderStatus) renderStatus.invalidate(); }
function fitCanvas() { const cv = $("#prgCanvas"), scr = $("#prgScreen"); if (!cv || !scr) return; const zoom = ($("#prgFit") || {}).value || "Fit"; if (zoom !== "Fit") return; const w = scr.clientWidth - 8, h = scr.clientHeight - 8; if (w <= 0 || h <= 0) return; const k = Math.min(w / S.seq.width, h / S.seq.height); cv.style.width = Math.floor(S.seq.width * k) + "px"; cv.style.height = Math.floor(S.seq.height * k) + "px"; cv.style.maxWidth = "none"; cv.style.maxHeight = "none"; }
function renderProgram() {
  S.mvGrid = null; S.mvGridMessage = ""; if (!S.seq) { updateMulticamControls(null); return; } const cv = $("#prgCanvas"); if (cv.width !== S.seq.width || cv.height !== S.seq.height) { cv.width = S.seq.width; cv.height = S.seq.height; } fitCanvas();
  const used = new Set(); let usingRenderedAudio = false; try {
  const rendered = getRenderedPreview().playable(S.t, S.playing ? (S.rate || 1) : 1); usingRenderedAudio = !!rendered;
  if (rendered) {
    if (!pv.el) {
      pv.el = document.createElement("video"); pv.el.preload = "auto"; pv.el.muted = false; pv.el.style.display = "none";
      const element = pv.el;
      pv.el.onloadeddata = () => { if (pv.el === element) renderProgram(); };
      pv.el.onseeked = () => { if (pv.el === element && !S.playing) renderProgram(); };
      pv.el.onerror = () => { getRenderedPreview().invalidate("Preview playback failed — render again"); renderProgram(); };
      document.body.appendChild(pv.el);
    }
    if (!pv.el.src.endsWith(rendered.out)) pv.el.src = rendered.out;
    const local = S.t - rendered.preview.range[0];
    seekPreviewPicture(pv.el, local, { has_video: true, frame_rate: S.seq.fps });
    routeRenderedAudio(pv.el);
    pv.el.playbackRate = Math.max(.0625, Math.min(16, S.rate || 1));
    if (S.playing && pv.el.paused) {
      const element = pv.el;
      element.play().catch(() => {
        if (pv.el !== element) return;
        getRenderedPreview().invalidate("Preview playback could not start — press Play or render again");
        if (S.playing) togglePlay(false);
        renderProgram();
      });
    }
    if (!S.playing && !pv.el.paused) pv.el.pause();
    const ctx = cv.getContext("2d"); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.filter = "none"; ctx.globalAlpha = 1; ctx.fillStyle = "#000"; ctx.fillRect(0, 0, cv.width, cv.height);
    if (pv.el.readyState >= 2) ctx.drawImage(pv.el, 0, 0, cv.width, cv.height);
    for (const v of Object.values(pool)) if (!v.paused) v.pause();
    return;
  } else { routeRenderedAudio(null); if (pv.el && !pv.el.paused) pv.el.pause(); }
  const drawT = S.previewT != null ? S.previewT : S.t; if (S.slipTwoUp && !S.playing) { const c = S.slipTwoUp; const ctx = cv.getContext("2d"), W = cv.width, H = cv.height; const oc = offscreen["slipA"] || (offscreen["slipA"] = document.createElement("canvas")); oc.width = W; oc.height = H; ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.filter = "none"; ctx.globalAlpha = 1; ctx.fillStyle = "#111"; ctx.fillRect(0, 0, W, H); [[c.start + frame() / 2, 0, "in"], [clipEnd(c) - frame() / 2, 1, "out"], [Math.max(0, c.start - frame()), 2, "prev out"], [clipEnd(c) + frame() / 2, 3, "next in"]].forEach(([t, i, lbl]) => { drawSequence(oc, S.seq, t, used, 1, ["slip", String(i)]); const x = (i % 2) * W / 2 + 4, y = i < 2 ? H / 4 - H / 8 : H / 2 + 12; ctx.drawImage(oc, x, y, W / 2 - 8, H / 4); ctx.fillStyle = "#fff"; ctx.font = `${Math.round(W / 36)}px sans-serif`; ctx.fillText(lbl + " " + fmtTC(t, S.seq.fps), x + 8, y - 8); }); return; }
  const seqToDraw = S.seq;
  drawSequence(cv, seqToDraw, drawT, used, 0, ["program"]);

  drawUiOverlay(cv);
  if (S.alphaMode) { const ctx = cv.getContext("2d"); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.filter = "none"; ctx.globalCompositeOperation = "source-in"; ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, cv.width, cv.height); ctx.globalCompositeOperation = "destination-over"; ctx.fillStyle = "#000"; ctx.fillRect(0, 0, cv.width, cv.height); ctx.globalCompositeOperation = "source-over"; }
  if (S.compareMode && !S.playing) { const ctx = cv.getContext("2d"), W = cv.width, H = cv.height; const oc = offscreen["cmpA"] || (offscreen["cmpA"] = document.createElement("canvas")); oc.width = W; oc.height = H; const refT = S.refT == null ? Math.max(0, S.t - 1 / 30) : S.refT; drawSequence(oc, S.seq, refT, used, 1, ["compare"]); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.filter = "none"; ctx.globalAlpha = 1;
    if (S.compareMode === "side") { const cur = offscreen["cmpB"] || (offscreen["cmpB"] = document.createElement("canvas")); cur.width = W; cur.height = H; cur.getContext("2d").drawImage(cv, 0, 0); ctx.fillStyle = "#111"; ctx.fillRect(0, 0, W, H); ctx.drawImage(cur, 4, H / 4, W / 2 - 8, H / 2); ctx.drawImage(oc, W / 2 + 4, H / 4, W / 2 - 8, H / 2); ctx.fillStyle = "#fff"; ctx.font = `${Math.round(W / 34)}px sans-serif`; ctx.fillText("current " + fmtTC(S.t, S.seq.fps), 12, H / 4 - 12); ctx.fillText("reference " + fmtTC(refT, S.seq.fps), W / 2 + 12, H / 4 - 12); }
    else { ctx.save(); ctx.beginPath(); ctx.rect(W / 2, 0, W / 2, H); ctx.clip(); ctx.drawImage(oc, 0, 0); ctx.restore(); ctx.fillStyle = "#f6c14a"; ctx.fillRect(W / 2 - 2, 0, 4, H); ctx.fillStyle = "#fff"; ctx.font = `${Math.round(W / 34)}px sans-serif`; ctx.fillText("current", 12, H * 0.06); ctx.fillText("reference " + fmtTC(refT, S.seq.fps), W / 2 + 12, H * 0.06); } return; }
  if (S.trimMode && S.editPoint && !S.playing) { const r = clipById(S.editPoint.clipId); if (r) { const { c, tr } = r; const sorted = [...tr.clips].sort((a, b) => a.start - b.start), i = sorted.findIndex(x => x.id === c.id); const left = S.editPoint.side === "r" ? c : sorted[i - 1], right = S.editPoint.side === "r" ? sorted[i + 1] : c; const cut = S.editPoint.side === "r" ? clipEnd(c) : c.start;
      const ctx = cv.getContext("2d"), W = cv.width, H = cv.height; ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.filter = "none"; ctx.globalAlpha = 1; ctx.fillStyle = "#111"; ctx.fillRect(0, 0, W, H); const oc = offscreen["trim"] || (offscreen["trim"] = document.createElement("canvas")); oc.width = W; oc.height = H;
      const fw = W / 2 - 12, fh = fw * H / W, fy = Math.max(H * 0.06, H / 2 - fh / 2 - H * 0.06);
      [[left, cut - frame() / 2, 0], [right, cut + frame() / 2, 1]].forEach(([cl, t, side]) => { if (!cl) return; drawSequence(oc, S.seq, t, used, 1, ["trim", String(side)]); ctx.drawImage(oc, side * W / 2 + 6, fy, fw, fh); ctx.fillStyle = "#ddd"; ctx.font = `${Math.round(W / 34)}px sans-serif`; ctx.textAlign = "left"; ctx.fillText(side ? "incoming  " + fmtTC(cl.start, S.seq.fps) : "outgoing  " + fmtTC(clipEnd(cl), S.seq.fps), side * W / 2 + 10, fy - W / 60); });
      ctx.strokeStyle = "#f6c14a"; ctx.lineWidth = Math.max(3, W / 300); ctx.strokeRect((S.editPoint.side === "r" ? 0 : W / 2) + 4, fy - 4, fw + 4, fh + 8); ctx.fillStyle = "#f6c14a"; ctx.font = `700 ${Math.round(W / 26)}px sans-serif`; ctx.textAlign = "center"; ctx.fillText(`${(S.trimMode.type || "ripple").toUpperCase()} TRIM · ${fmtTC(cut, S.seq.fps)}${S.t !== cut ? `  →  ${(S.t - cut) >= 0 ? "+" : ""}${Math.round((S.t - cut) / frame())} f` : ""}`, W / 2, fy + fh + W / 18); ctx.textAlign = "left"; return; } }
  if (S.multiView) drawMulticamGrid(cv, drawT, used);
  if (!S.playing) renderGuides(); CR.scopes && !S.playing && CR.scopes(cv); if (CR.renderRef && !S.playing && document.querySelector("#pane-ref.on")) CR.renderRef(); const btc = document.getElementById("bigTC"); if (btc) btc.textContent = fmtTC(S.t, S.seq.fps);
  if (S.lineDraft) { const ctx = cv.getContext("2d"), W = cv.width, H = cv.height; ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.strokeStyle = "#f6c14a"; ctx.lineWidth = Math.max(3, W / 200); ctx.setLineDash([W / 80, W / 120]); ctx.beginPath(); ctx.moveTo(S.lineDraft[0][0] * W, S.lineDraft[0][1] * H); ctx.lineTo(S.lineDraft[1][0] * W, S.lineDraft[1][1] * H); ctx.stroke(); ctx.restore(); }
  if (S.polyDraft && S.polyDraft.length) { const ctx = cv.getContext("2d"), W = cv.width, H = cv.height; ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.strokeStyle = "#f6c14a"; ctx.lineWidth = Math.max(2, W / 400); ctx.setLineDash([W / 80, W / 120]); ctx.beginPath(); S.polyDraft.forEach(([x, y], i) => i ? ctx.lineTo(x * W, y * H) : ctx.moveTo(x * W, y * H)); ctx.stroke(); ctx.setLineDash([]); ctx.fillStyle = "#f6c14a"; for (const [x, y] of S.polyDraft) ctx.fillRect(x * W - W / 200, y * H - W / 200, W / 100, W / 100); ctx.restore(); }
  const sel = selectedClips().filter(x => x.tr.kind === "video" && x.c.media_id && S.t >= x.c.start && S.t < clipEnd(x.c)); if (sel.length === 1 && !S.playing) { const { c, tr } = sel[0], m = S.proj.media[c.media_id]; const ctx = cv.getContext("2d"), W = cv.width, H = cv.height, tf = c.transform || {}, kf = c.keyframes || {}, rel = S.t - c.start; const sc = kfVal(kf["transform.scale"], rel, tf.scale == null ? 1 : tf.scale), ox = kfVal(kf["transform.x"], rel, tf.x || 0), oy = kfVal(kf["transform.y"], rel, tf.y || 0); const [fw, fh] = programPictureSize(c, tr, W, H), fit = c.fit === "cover" ? Math.max(W / fw, H / fh) : Math.min(W / fw, H / fh), dw = fw * fit * sc, dh = fh * fit * sc;
    ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.filter = "none"; ctx.globalAlpha = 1; ctx.translate(W / 2 + ox, H / 2 + oy); if (tf.rotation) ctx.rotate(tf.rotation * Math.PI / 180); ctx.strokeStyle = "rgba(159,195,255,.9)"; ctx.lineWidth = Math.max(2, W / 400); ctx.setLineDash([W / 60, W / 90]); ctx.strokeRect(-dw / 2, -dh / 2, dw, dh); ctx.setLineDash([]); ctx.fillStyle = "#9fc3ff"; for (const [px, py] of [[-dw / 2, -dh / 2], [dw / 2, -dh / 2], [-dw / 2, dh / 2], [dw / 2, dh / 2]]) ctx.fillRect(px - W / 120, py - W / 120, W / 60, W / 60);
    ctx.beginPath(); ctx.moveTo(0, -dh / 2); ctx.lineTo(0, -dh / 2 - W / 30); ctx.stroke(); ctx.beginPath(); ctx.arc(0, -dh / 2 - W / 30, W / 110, 0, Math.PI * 2); ctx.fill();  // rotation handle
    const ax = tf.anchor_x || 0, ay = tf.anchor_y || 0; ctx.strokeStyle = "#fff"; ctx.beginPath(); ctx.moveTo(ax - W / 80, ay); ctx.lineTo(ax + W / 80, ay); ctx.moveTo(ax, ay - W / 80); ctx.lineTo(ax, ay + W / 80); ctx.stroke(); ctx.beginPath(); ctx.arc(ax, ay, W / 160, 0, Math.PI * 2); ctx.stroke(); ctx.restore(); }
  for (const [mid, v] of Object.entries(pool)) if (!used.has(mid) && !v.paused) v.pause();
  if (S.exact && !S.playing) exactFrame(); else if (exactFrames) exactFrames.invalidate();
} finally { if (!S.mvGrid) updateMulticamControls(null, S.mvGridMessage); sweepVoices(used); updateAudioPreviewStatus(usingRenderedAudio); CR.metersTick?.(); CR.panels?.meter?.(); }
}
let exactFrames = null;
function getExactFrames() {
  if (!exactFrames) {
    const client = window.FilmocityFramePreview;
    exactFrames = client.create({
      capture: () => ({ ...previewView(), time: S.t, fps: S.seq?.fps, duration: S.seq ? seqDur() : 0, enabled: S.exact && !S.previewMode && !S.multiView && !S.trimMode && !S.compareMode && !S.slipTwoUp && S.previewT == null && !S.previewProposal, playing: S.playing }),
      flush: flushSaves, load: client.load, decode: client.decode, save: client.save,
      report: (message, error) => status(message, error ? "err" : ""),
      draw(image) { const cv = $("#prgCanvas"), ctx = cv.getContext("2d"); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.filter = "none"; ctx.globalAlpha = 1; ctx.drawImage(image, 0, 0, cv.width, cv.height); }
    });
  }
  return exactFrames;
}
function exactFrame() { getExactFrames().request(); }
function exportProgramFrame() { return getExactFrames().exportFrame(); }
let sourceFrames = null;
function exportSourceFrame() {
  const video = $("#srcVideo"); video.pause();
  if (!sourceFrames) {
    const client = window.FilmocityFramePreview;
    sourceFrames = client.create({
      capture: () => {
        const m = S.src; if (!m?.has_video) throw Error("Load a source with a video picture first");
        return { ...previewView(), sequence: "source:" + m.id, media: m.id, time: video.currentTime, fps: mediaRate(m),
          duration: Number.isFinite(video.duration) ? video.duration : m.native_duration || m.duration, enabled: false, playing: false };
      },
      flush: flushSaves, load: client.load, save: client.save, report: (message, error) => status(message, error ? "err" : "")
    });
  }
  return sourceFrames.exportFrame();
}
function drawSequence(cv, sq, T, used, depth, voicePath = ["reference"], audioScope = null) {

  const ctx = cv.getContext("2d"); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.filter = "none"; ctx.globalAlpha = 1; ctx.globalCompositeOperation = "source-over"; ctx.fillStyle = "#000"; if (depth || (S.alphaMode && depth === 0)) ctx.clearRect(0, 0, cv.width, cv.height); else ctx.fillRect(0, 0, cv.width, cv.height);
  const act = activeClipsOf(sq, T); const W = cv.width, H = cv.height; const S_t = T;
  for (const { c, tr } of act) {
    if (tr._mc_picture_hidden && (c.adjustment || c.title || c.graphic || S.proj.media[c.media_id]?.synthetic)) continue;
    if (c.adjustment && tr.kind === "video") { const tmp = offscreen["adj"] || (offscreen["adj"] = document.createElement("canvas")); tmp.width = W; tmp.height = H; const tc = tmp.getContext("2d"); tc.clearRect(0, 0, W, H); tc.drawImage(cv, 0, 0); ctx.save(); ctx.filter = cssFilter(c.color); ctx.clearRect(0, 0, W, H); ctx.drawImage(tmp, 0, 0); ctx.restore(); continue; }
    if (c.sequence_id && depth < 4) { let sub = S.proj.sequences.find(x => x.id === c.sequence_id); if (!sub) continue;
      if (sub.multicam) { try { sub = FilmocityAudioPreview.multicam(sub, c.multicam_angle === undefined ? 0 : c.multicam_angle); } catch (error) { status(error.message, "err"); continue; } } const key = "n_" + c.sequence_id + "_" + depth; const oc = offscreen[key] || (offscreen[key] = document.createElement("canvas")); oc.width = sub.width; oc.height = sub.height;
      const rel = T - c.start, local = pictureSourcePosition(c, rel, { fps: sub.fps, has_video: true });
      const admitted = voicePath[0] === "program" && audioScope?.audible !== false && !!FilmocityAudioPreview.route(sq, tr, c);
      const target = admitted ? nestedAudioTarget(sq, sub, tr, c, rel, voicePath, used, audioScope?.target) : null;
      const scope = { target, audible: admitted && !!target, rate: (audioScope?.rate ?? 1) * speedAt(c, rel), held: !!(audioScope?.held || c.hold), reversed: !!(audioScope?.reversed || c.reverse) };
      drawSequence(oc, sub, local, used, depth + 1, [...voicePath, sq.id, tr.id, c.id], scope);
      if (tr.kind !== "video" || tr._mc_picture_hidden) continue; const tf = c.transform || {}, kf = c.keyframes || {}; const sc = kfVal(kf["transform.scale"], rel, tf.scale == null ? 1 : tf.scale), ox = kfVal(kf["transform.x"], rel, tf.x || 0), oy = kfVal(kf["transform.y"], rel, tf.y || 0); let alpha = kfVal(kf["transform.opacity"], rel, tf.opacity == null ? 1 : tf.opacity);
      const fit = Math.min(W / sub.width, H / sub.height), dw = sub.width * fit * sc, dh = sub.height * fit * sc; ctx.save(); ctx.globalAlpha = Math.max(0, Math.min(1, alpha)); ctx.translate(W / 2 + ox, H / 2 + oy); if (tf.rotation) ctx.rotate(tf.rotation * Math.PI / 180); ctx.drawImage(oc, -dw / 2, -dh / 2, dw, dh); ctx.restore(); continue; }
    const m = c.media_id && S.proj.media[c.media_id]; const local = c.in_ + (T - c.start) * (c.speed || 1), rel = T - c.start, cd = clipDur(c);
    if (m && m.synthetic && tr.kind === "video") { const kind = m.synthetic.kind, tf = c.transform || {}, kf = c.keyframes || {}; const sc = kfVal(kf["transform.scale"], rel, tf.scale == null ? 1 : tf.scale), ox = kfVal(kf["transform.x"], rel, tf.x || 0), oy = kfVal(kf["transform.y"], rel, tf.y || 0); let alpha = kfVal(kf["transform.opacity"], rel, tf.opacity == null ? 1 : tf.opacity);
      ctx.save(); ctx.globalAlpha = Math.max(0, Math.min(1, alpha)); ctx.translate(W / 2 + ox, H / 2 + oy); ctx.scale(sc, sc);
      if (kind === "bars") { const cols = ["#c0c0c0", "#c0c000", "#00c0c0", "#00c000", "#c000c0", "#c00000", "#0000c0"]; cols.forEach((col, i) => { ctx.fillStyle = col; ctx.fillRect(-W / 2 + i * W / 7, -H / 2, W / 7 + 1, H * 0.62); }); ctx.fillStyle = "#222"; ctx.fillRect(-W / 2, -H / 2 + H * 0.62, W, H * 0.38); ctx.fillStyle = "#fff"; ctx.fillRect(-W / 2 + W * 0.08, -H / 2 + H * 0.66, W * 0.15, H * 0.3); }
      else if (kind === "gradient") { const sy = m.synthetic, ph = rel * (sy.speed || 0.05) * 6; const g = ctx.createLinearGradient(-W / 2 + Math.sin(ph) * W * 0.3, -H / 2, W / 2 + Math.cos(ph) * W * 0.3, H / 2); g.addColorStop(0, cssColor(sy.color || "#E8631C")); g.addColorStop(0.5, cssColor(sy.color2 || "#7A2E9E")); g.addColorStop(1, cssColor(sy.color3 || sy.color || "#E8631C")); ctx.fillStyle = g; ctx.fillRect(-W / 2, -H / 2, W, H); }
      else if (kind === "light_leak") { const inten = m.synthetic.intensity == null ? 0.5 : m.synthetic.intensity; const ph = rel * (m.synthetic.speed || 0.06) * 8; const g = ctx.createRadialGradient(Math.sin(ph) * W * 0.4, -H * 0.25 + Math.cos(ph * 0.7) * H * 0.2, 0, 0, 0, W * 0.9); g.addColorStop(0, `rgba(255,140,40,${0.55 * inten})`); g.addColorStop(0.45, `rgba(255,60,120,${0.25 * inten})`); g.addColorStop(1, "rgba(0,0,0,0)"); ctx.fillStyle = "#000"; ctx.fillRect(-W / 2, -H / 2, W, H); ctx.fillStyle = g; ctx.fillRect(-W / 2, -H / 2, W, H); }
      else if (kind === "grain") { const inten = m.synthetic.intensity == null ? 0.5 : m.synthetic.intensity; const oc = offscreen["grain"] || (offscreen["grain"] = document.createElement("canvas")); if (oc.width !== 160) { oc.width = 160; oc.height = 284; } const g2 = oc.getContext("2d"); const id = g2.createImageData(160, 284); const d = id.data; for (let i = 0; i < d.length; i += 4) { const v = 128 + (Math.random() - 0.5) * 255 * inten; d[i] = d[i + 1] = d[i + 2] = v; d[i + 3] = 255; } g2.putImageData(id, 0, 0); ctx.imageSmoothingEnabled = false; ctx.drawImage(oc, -W / 2, -H / 2, W, H); ctx.imageSmoothingEnabled = true; }
      else if (kind !== "transparent") { ctx.fillStyle = cssColor(kind === "black" ? "#000000" : (m.synthetic.color || "#000")); ctx.fillRect(-W / 2, -H / 2, W, H); if (kind === "counting_leader") { ctx.fillStyle = "#fff"; ctx.font = `700 ${Math.round(H * 0.3)}px sans-serif`; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(String(Math.max(0, Math.ceil(cd - rel))), 0, 0); } }
      ctx.restore(); continue; }
    if (m) { const voice = FilmocityAudioPreview.voiceKey(S.context, sq.id, voicePath, tr.id, c.id); const v = vidFor(m.id, voice); used.add(voice); const ifac = window.FilmocitySourceClock.interpretationFactor(m); const localT = pictureSourcePosition(c, rel, m), reversed = !!(c.reverse || audioScope?.reversed); seekPreviewPicture(v, localT, m, { playing: S.playing && !(m.has_video && (c.hold || audioScope?.held || reversed)), strictPlayback: reversed });
      v.playbackRate = Math.min(Math.max((speedAt(c, rel) || 0.25) / ifac * (audioScope?.rate ?? 1), 0.25), 4); if ((c.hold || audioScope?.held || reversed) && !v.paused) v.pause(); v._reverseAudioUnavailable = reversed && !!m.has_audio && voicePath[0] === "program"; const audible = m.has_audio && !reversed && voicePath[0] === "program" && audioScope?.audible !== false && !!FilmocityAudioPreview.route(sq, tr, c); const routed = audible && applyAudioParams(voice, v, c, tr, rel, sq, voicePath, audioScope?.target); v._audioError = !!audible && !routed; v.muted = !routed; v.volume = routed ? 1 : 0; if (!audible && AG.nodes[voice] && AG.ctx) { AG.nodes[voice].out.gain.setValueAtTime(0, AG.ctx.currentTime); AG.nodes[voice].timeFx?.clear(); } v.preservesPitch = (c.audio || {}).maintain_pitch !== false;
      if (S.playing && v.paused && !c.hold && !audioScope?.held && !reversed) v.play().catch(() => { }); if (!S.playing && !v.paused) v.pause();
      if (tr.kind !== "video" || !m.has_video || tr._mc_picture_hidden) continue;
      const tf = c.transform || {}, kf = c.keyframes || {}; const sc = kfVal(kf["transform.scale"], rel, tf.scale == null ? 1 : tf.scale), ox = kfVal(kf["transform.x"], rel, tf.x || 0), oy = kfVal(kf["transform.y"], rel, tf.y || 0); let alpha = kfVal(kf["transform.opacity"], rel, tf.opacity == null ? 1 : tf.opacity);
      const ti = c.transition_in || {}, to = c.transition_out || {}; let dip = null, wipe = null, push = 0;
      if (ti.duration > 0 && rel < ti.duration) { const p = rel / ti.duration; if (["dissolve", "fade"].includes(ti.type)) alpha *= p; else if (ti.type === "dip_black" || ti.type === "dip_white") dip = [ti.type === "dip_black" ? "#000" : "#fff", 1 - p]; else if (ti.type === "wipe_left") wipe = [0, p]; else if (ti.type === "wipe_right") wipe = [1 - p, 1]; else if (ti.type === "push_left") push = -W * (1 - p); else if (ti.type === "push_right") push = W * (1 - p); }
      if (to.duration > 0 && cd - rel < to.duration) { const p = (cd - rel) / to.duration; if (["dissolve", "fade"].includes(to.type)) alpha *= p; else if (to.type === "dip_black" || to.type === "dip_white") dip = [to.type === "dip_black" ? "#000" : "#fff", 1 - p]; }
      const [fw, fh] = pictureDisplaySize(m, v, W, H), fit = (c.fit === "cover" ? Math.max(W / fw, H / fh) : Math.min(W / fw, H / fh)), dw = fw * fit * sc, dh = fh * fit * sc;
      if (c.fit === "blur_fill" && v.readyState >= 2) { const bf = Math.max(W / fw, H / fh); ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.globalAlpha = Math.max(0, Math.min(1, alpha)); ctx.filter = `blur(${Math.round(W / 40)}px) brightness(0.85)`; ctx.drawImage(v, (W - fw * bf) / 2, (H - fh * bf) / 2, fw * bf, fh * bf); ctx.restore(); } const fx = c.effects || {}, cr = fx.crop || {}; const ax = tf.anchor_x || 0, ay = tf.anchor_y || 0, rr = (tf.rotation || 0) * Math.PI / 180; const aox = ax - (ax * Math.cos(rr) - ay * Math.sin(rr)) * sc, aoy = ay - (ax * Math.sin(rr) + ay * Math.cos(rr)) * sc;
      ctx.save(); const tiT = c.transition_in || {}, tD = tiT.duration || 0; if (tD > 0 && rel < tD && !["dissolve", "fade", "dip_black", "dip_white", "push_left", "push_right", "slide_left", "slide_right", "slide_up", "slide_down", "cross_zoom", "wipe_left", "wipe_right"].includes(tiT.type)) { const p = Math.min(1, rel / tD); ctx.beginPath(); const ty = tiT.type;
        if (ty === "wipe_up") ctx.rect(0, H * (1 - p), W, H * p); else if (ty === "wipe_down") ctx.rect(0, 0, W, H * p); else if (ty === "iris") ctx.arc(W / 2, H / 2, Math.hypot(W / 2, H / 2) * p, 0, Math.PI * 2); else if (ty === "iris_close") { ctx.rect(0, 0, W, H); ctx.arc(W / 2, H / 2, Math.hypot(W / 2, H / 2) * (1 - p), 0, Math.PI * 2, true); } else if (ty === "barn_h") ctx.rect(W / 2 * (1 - p), 0, W * p, H); else if (ty === "barn_v") ctx.rect(0, H / 2 * (1 - p), W, H * p); else if (ty === "clock") { ctx.moveTo(W / 2, H / 2); ctx.arc(W / 2, H / 2, Math.hypot(W, H), -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * p); ctx.closePath(); } else if (ty.startsWith("diagonal")) { const q = p * 2; const pts = ty === "diagonal_tl" ? [[0, 0], [W * q, 0], [0, H * q]] : ty === "diagonal_tr" ? [[W, 0], [W - W * q, 0], [W, H * q]] : ty === "diagonal_bl" ? [[0, H], [W * q, H], [0, H - H * q]] : [[W, H], [W - W * q, H], [W, H - H * q]]; ctx.moveTo(pts[0][0], pts[0][1]); ctx.lineTo(pts[1][0], pts[1][1]); ctx.lineTo(pts[2][0], pts[2][1]); ctx.closePath(); } else if (ty === "checker") { const cols = 8, rows = 14; for (let i = 0; i < cols; i++) for (let j = 0; j < rows; j++) { const order = ((i + j) % 2) * 0.5 + (((i * 7 + j * 3) % 16) / 32); if (order < p) ctx.rect(i * W / cols, j * H / rows, W / cols + 1, H / rows + 1); } } else ctx.rect(0, 0, W, H); ctx.clip(); }
      if (wipe) { ctx.beginPath(); ctx.rect(W * wipe[0], 0, W * (wipe[1] - wipe[0]), H); ctx.clip(); }
      if (c.mask && c.mask.type) { const mk = c.mask, mx = kfVal(kf["mask.x"], rel, mk.x), my = kfVal(kf["mask.y"], rel, mk.y), mw = kfVal(kf["mask.w"], rel, mk.w), mh = kfVal(kf["mask.h"], rel, mk.h); ctx.beginPath(); if (mk.invert) { ctx.rect(0, 0, W, H); } if (mk.type === "ellipse") ctx.ellipse((mx + mw / 2) * W, (my + mh / 2) * H, mw * W / 2, mh * H / 2, 0, 0, Math.PI * 2); else ctx.rect(mx * W, my * H, mw * W, mh * H); ctx.clip(mk.invert ? "evenodd" : "nonzero"); }
      const colorMedia = FilmocityMediaColor.source(S.proj,m); const previewClip = { ...c, _inputTransformPath: colorMedia.input_transform_resource?.name === colorMedia.input_transform ? colorMedia.input_transform_resource?.path : null, _inputTransform: colorMedia.input_transform && colorMedia.input_transform !== "none" ? colorMedia.input_transform : null };
      const gpuSrc = (window.CR_GPU && S.prefs.gpu !== false && !(S.playing && S.prefs.draft) && v.readyState >= 2 && CR_GPU.needs(previewClip)) ? CR_GPU.process(v, previewClip, fw, fh) : null;
      ctx.globalAlpha = Math.max(0, Math.min(1, alpha)); ctx.filter = (S.playing && S.prefs.draft) ? "none" : ((gpuSrc ? stackCssNonColor(c) : cssFilter(c.color, c)) + (fx.blur ? ` blur(${fx.blur * fit}px)` : "")); if (ctx.filter.startsWith("none ")) ctx.filter = ctx.filter.slice(5); if (!ctx.filter.trim()) ctx.filter = "none"; if (c.blend && c.blend !== "normal") ctx.globalCompositeOperation = BLEND_CANVAS[c.blend] || "source-over";
      ctx.translate(W / 2 + ox + push + aox, H / 2 + oy + aoy); if (tf.rotation) ctx.rotate(rr); const cl = (cr.l || 0), ct = (cr.t || 0), crr = (cr.r || 0), cb = (cr.b || 0); const sx = fw * cl, sy = fh * ct, sw = fw * Math.max(0.02, 1 - cl - crr), sh = fh * Math.max(0.02, 1 - ct - cb); const ddw = dw * (sw / fw), ddh = dh * (sh / fh);
      if (gpuSrc) ctx.drawImage(gpuSrc, sx, sy, sw, sh, -ddw / 2, -ddh / 2, ddw, ddh); else if (v.readyState >= 2) ctx.drawImage(v, sx, sy, sw, sh, -ddw / 2, -ddh / 2, ddw, ddh);
      if ((c.transition_in || {}).type === "glitch" && rel < (c.transition_in.duration || 0) && v.readyState >= 2) { const k = 1 - rel / c.transition_in.duration; ctx.save(); ctx.globalCompositeOperation = "lighter"; ctx.globalAlpha = 0.35 * k; for (let q = 0; q < 3; q++) { const dx = (Math.random() - 0.5) * 60 * k; ctx.drawImage(gpuSrc || v, sx, sy, sw, sh, -ddw / 2 + dx, -ddh / 2 + (Math.random() - 0.5) * 8 * k, ddw, ddh); } ctx.restore(); }
      if (!gpuSrc && v.readyState < 2) { ctx.fillStyle = "#222"; ctx.fillRect(-ddw / 2, -ddh / 2, ddw, ddh); if (v.error || !S.mediaAvailability?.[m.id]?.proxy_available && !nativePlayable(m) || !S.mediaAvailability?.[m.id]?.original_online) { ctx.fillStyle = "#aaa"; ctx.font = `${Math.round(W / 30)}px sans-serif`; ctx.textAlign = "center"; ctx.fillText(v.error ? "Preview unavailable — refresh media in Project" : FilmocityProxyPreview.availabilityMessage(m, S.mediaAvailability?.[m.id], nativePlayable(m), S.useProxy), 0, 0); ctx.textAlign = "left"; } }
      if (fx.vignette) { const g = ctx.createRadialGradient(0, 0, Math.min(ddw, ddh) * 0.35, 0, 0, Math.max(ddw, ddh) * 0.75); g.addColorStop(0, "rgba(0,0,0,0)"); g.addColorStop(1, `rgba(0,0,0,${Math.min(0.95, fx.vignette)})`); ctx.filter = "none"; ctx.fillStyle = g; ctx.fillRect(-ddw / 2, -ddh / 2, ddw, ddh); }
      ctx.restore(); if (dip) { ctx.globalAlpha = dip[1]; ctx.fillStyle = dip[0]; ctx.fillRect(0, 0, W, H); ctx.globalAlpha = 1; } }
    else if ((c.title || c.graphic) && tr.kind === "video") { const ti = c.transition_in || {}, to = c.transition_out || {}; let a = (c.transform || {}).opacity == null ? 1 : c.transform.opacity;
      if (ti.duration > 0 && rel < ti.duration) a *= (["dissolve", "fade"].includes(ti.type) ? rel / ti.duration : 1); if (to.duration > 0 && cd - rel < to.duration) a *= (["dissolve", "fade"].includes(to.type) ? (cd - rel) / to.duration : 1); let px = 0; if (ti.duration > 0 && rel < ti.duration && ti.type === "push_left") px = -W * (1 - rel / ti.duration); if (ti.duration > 0 && rel < ti.duration && ti.type === "push_right") px = W * (1 - rel / ti.duration);
      if (c.title) drawText(ctx, c.title, W, H, Math.round(H * 0.07), px, a);
      else for (const [li, L] of (c.graphic.layers || []).entries()) { const st = layerState(L, rel, cd, c.keyframes, li); if (st.hidden) continue; ctx.save(); ctx.globalAlpha = a * st.alpha; ctx.translate(W / 2, H / 2); ctx.scale(st.scale, st.scale); ctx.rotate(st.rot); ctx.translate(-W / 2 + st.dx, -H / 2 + st.dy); if (st.clip) { ctx.beginPath(); ctx.rect(...st.clip); ctx.clip(); } if (L.blur) ctx.filter = `blur(${L.blur}px)`;
        if (L.kind === "image" && L.path) { const im = imageFor(L.path); if (im && im.complete && im.naturalWidth) { const bw = L.w * W, bh = L.h * H, bx = L.x * W + px, by = L.y * H; const k = L.fit === "cover" ? Math.max(bw / im.naturalWidth, bh / im.naturalHeight) : Math.min(bw / im.naturalWidth, bh / im.naturalHeight); const iw = im.naturalWidth * k, ih = im.naturalHeight * k; ctx.globalAlpha *= (L.opacity == null ? 1 : L.opacity); if (L.fit === "cover") { ctx.beginPath(); ctx.rect(bx, by, bw, bh); ctx.clip(); } ctx.drawImage(im, bx + (bw - iw) / 2, by + (bh - ih) / 2, iw, ih); } }
        else if (L.kind === "box") { ctx.fillStyle = cssColor(L.color || "white@0.9"); ctx.fillRect(L.x * W + px, L.y * H, L.w * W, L.h * H); }
        else if (L.kind === "shape" && (L.shape === "line" || L.shape === "arrow")) { ctx.globalAlpha *= (L.opacity == null ? 1 : L.opacity); ctx.strokeStyle = cssColor(L.color || "#fff"); ctx.fillStyle = ctx.strokeStyle; ctx.lineWidth = L.stroke || 8; ctx.lineCap = "round"; const x1 = L.x1 * W + px, y1 = L.y1 * H, x2 = L.x2 * W + px, y2 = L.y2 * H; const ang = Math.atan2(y2 - y1, x2 - x1), hl = L.head || 40; ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(L.shape === "arrow" ? x2 - Math.cos(ang) * hl * 0.6 : x2, L.shape === "arrow" ? y2 - Math.sin(ang) * hl * 0.6 : y2); ctx.stroke(); if (L.shape === "arrow") { ctx.beginPath(); ctx.moveTo(x2, y2); ctx.lineTo(x2 - Math.cos(ang - 0.45) * hl, y2 - Math.sin(ang - 0.45) * hl); ctx.lineTo(x2 - Math.cos(ang + 0.45) * hl, y2 - Math.sin(ang + 0.45) * hl); ctx.closePath(); ctx.fill(); } }
        else if (L.kind === "shape") { ctx.globalAlpha *= (L.opacity == null ? 1 : L.opacity); if (L.shadow) { ctx.shadowColor = cssColor(L.shadow_color || "#000") ; ctx.shadowBlur = L.shadow_blur || 14; ctx.shadowOffsetX = L.shadow_x == null ? 8 : L.shadow_x; ctx.shadowOffsetY = L.shadow_y == null ? 12 : L.shadow_y; } const g = L.gradient; if (g && g.to) { const ang = (g.angle || 0) * Math.PI / 180, x0 = L.x * W + px, y0 = L.y * H, bw = L.w * W, bh = L.h * H; const gr = ctx.createLinearGradient(x0 + bw / 2 - Math.cos(ang) * bw / 2, y0 + bh / 2 - Math.sin(ang) * bh / 2, x0 + bw / 2 + Math.cos(ang) * bw / 2, y0 + bh / 2 + Math.sin(ang) * bh / 2); gr.addColorStop(0, cssColor(L.color || g.from || "#fff")); gr.addColorStop(1, cssColor(g.to)); ctx.fillStyle = gr; } else ctx.fillStyle = cssColor(L.color || "#fff"); ctx.beginPath(); if (L.shape === "polygon" && L.points) { L.points.forEach(([x, y], i) => i ? ctx.lineTo(x * W + px, y * H) : ctx.moveTo(x * W + px, y * H)); ctx.closePath(); } else if (L.shape === "ellipse") ctx.ellipse((L.x + L.w / 2) * W + px, (L.y + L.h / 2) * H, L.w * W / 2, L.h * H / 2, 0, 0, Math.PI * 2); else if (L.radius) ctx.roundRect(L.x * W + px, L.y * H, L.w * W, L.h * H, L.radius); else ctx.rect(L.x * W + px, L.y * H, L.w * W, L.h * H); ctx.fill(); if (L.stroke) { ctx.lineWidth = L.stroke; ctx.strokeStyle = cssColor(L.stroke_color || "#000"); ctx.stroke(); } }
        else if (L.kind === "text") { if (L.glow) { ctx.shadowColor = cssColor(L.color || "#fff"); ctx.shadowBlur = L.glow_size || 18; } drawText(ctx, st.text != null ? { ...L, text: st.text } : L, W, H, Math.round(H * 0.05), px, 1); }
        ctx.restore(); } }
  }
  const cs = sq.caption_style || {}; for (const cp of (sq.captions || [])) if (T >= cp.start && T < cp.end) { ctx.save(); const size = cs.size || Math.round(H * 0.032); ctx.font = `${cs.weight === "regular" ? 400 : 700} ${size}px ${window.FilmocityProjectResources.family(cs)}`; ctx.textAlign = "center"; ctx.textBaseline = "middle"; const lines = wrapCaption(cp.text, W, size), y0 = H * (cs.y == null ? 0.74 : cs.y) - (lines.length - 1) * size * 0.65;
    if (cs.animate === "highlight" || cs.animate === "pop") { const words = lines.flatMap(l => l.split(" ")); const n = words.length; const tw = (sq.transcript || []).filter(w => w.s >= cp.start - 0.05 && w.e <= cp.end + 0.05); const times = tw.length === n ? tw.map(w => [w.s, w.e]) : words.map((_, i) => [cp.start + (cp.end - cp.start) * i / n, cp.start + (cp.end - cp.start) * (i + 1) / n]); let k = 0; ctx.textAlign = "left"; const space = ctx.measureText(" ").width;
      lines.forEach((ln, i) => { const y = y0 + i * size * 1.3; const ws = ln.split(" "); const lw = ws.reduce((a, w) => a + ctx.measureText(w).width, 0) + space * (ws.length - 1); let x = W / 2 - lw / 2; for (const w of ws) { const [s0, e0] = times[k++] || [0, 0]; const hot = T >= s0 && T < e0; ctx.save(); if (hot && cs.animate === "pop") { ctx.translate(x + ctx.measureText(w).width / 2, y); ctx.scale(1.07, 1.07); ctx.translate(-(x + ctx.measureText(w).width / 2), -y); } ctx.lineWidth = (cs.borderw == null ? 3 : cs.borderw) * 2; ctx.strokeStyle = "black"; if (ctx.lineWidth) ctx.strokeText(w, x, y); ctx.fillStyle = hot ? cssColor(cs.highlight_color || "#F6C14A") : (cs.color || "white"); ctx.fillText(w, x, y); ctx.restore(); x += ctx.measureText(w).width + space; } }); ctx.restore(); continue; }
    lines.forEach((ln, i) => { const y = y0 + i * size * 1.3; if (cs.box) { const w = ctx.measureText(ln).width + 28; ctx.fillStyle = "rgba(0,0,0,.55)"; ctx.fillRect(W / 2 - w / 2, y - size * 0.7, w, size * 1.4); } ctx.lineWidth = (cs.borderw == null ? 3 : cs.borderw) * 2; ctx.strokeStyle = "black"; if (ctx.lineWidth) ctx.strokeText(ln, W / 2, y); ctx.fillStyle = cs.color || "white"; ctx.fillText(ln, W / 2, y); }); ctx.restore(); }
}
function wrapCaption(text, W, size) { if (text.includes("\n")) return text.split("\n"); const max = Math.max(12, Math.floor((W * 0.88) / (size * 0.56))); const lines = []; let cur = ""; for (const w of text.split(/\s+/)) { if (cur && cur.length + 1 + w.length > max) { lines.push(cur); cur = w; } else cur = (cur + " " + w).trim(); } if (cur) lines.push(cur); return lines.slice(0, 3); }
function seekTo(t) { if (!Number.isFinite(t)) return false; S.t = Math.max(0, t); if (S.selFollow && !S.playing) { const tr = trackOf(S.target.video); const c = tr && tr.clips.find(x => S.t >= x.start && S.t < clipEnd(x)); S.sel = new Set(c ? [c.id] : []); refreshSel(); } updatePlayhead(); return true; }
function trackMetersTick() { if (!AG.ctx) return; const buf = AG._buf || (AG._buf = new Float32Array(256)); for (const b of Object.values(AG.buses)) { if (!b.root) continue; const el = document.querySelector(`.head[data-track="${b.trackId}"] .tm i`); if (!el) continue; b.an.getFloatTimeDomainData(buf); let pk = 0; for (let i = 0; i < buf.length; i++) pk = Math.max(pk, Math.abs(buf[i])); const db = 20 * Math.log10(pk || 1e-6); el.style.width = Math.max(0, Math.min(100, (db + 48) / 48 * 100)) + "%"; el.style.background = db > -3 ? "#d94f4f" : db > -12 ? "#e0c341" : "#3fa36b"; } }
let playbackFrame = null;
function schedulePlayback() {
  if (!S.playing || playbackFrame !== null) return;
  const id = requestAnimationFrame(ts => {
    if (playbackFrame !== id) return;
    playbackFrame = null; tick(ts);
  });
  playbackFrame = id;
}
function tick(ts) { if (!S.playing) return; trackMetersTick(); const dt = Math.max(0, (ts - S.lastRaf) / 1000); S.lastRaf = ts; S.t += dt * (S.rate || 1);
  if (S.stopAt != null && S.t >= S.stopAt) { S.t = S.stopAt; S.stopAt = null; togglePlay(false); updatePlayhead(); return; }
  const end = (S.seq.in_point != null && S.seq.out_point != null && S.loop && S.rate > 0) ? S.seq.out_point : seqDur();
  if (S.t > end + 0.02 || S.t < 0) { if (S.loop && S.rate > 0) { S.t = (S.seq.in_point != null && S.seq.out_point != null) ? S.seq.in_point : 0; } else { S.t = Math.max(0, Math.min(S.t, seqDur())); togglePlay(false); } }
  updatePlayhead(); if (S.rate !== 1) status(`shuttle ${S.rate > 0 ? "▶" : "◀"} ${Math.abs(S.rate)}×`); schedulePlayback(); }
function playAround(pre = 2, post = 2) { const t = S.t; seekTo(Math.max(0, t - pre)); S.stopAt = Math.min(seqDur(), t + post); togglePlay(true); }
function playInOut() { const a = S.seq.in_point, b = S.seq.out_point; if (a == null || b == null) { status("Set sequence In and Out first."); return; } seekTo(a); S.stopAt = b; togglePlay(true); }
function togglePlay(on, options = {}) { const was = S.playing; S.playing = on == null ? !S.playing : on; if (!S.playing) S.stopAt = null; if (S.playing) audioCtx();
  if (was && !S.playing && S.trimMode && options.commitTrim !== false) setTimeout(trimToPlayhead, 0);
  if (!was && S.playing) S.playStart = S.t; else if (was && !S.playing && S.playStart != null) { const a = Math.min(S.playStart, S.t), b = Math.max(S.playStart, S.t); if (b - a >= 0.3) api.json("POST", "/api/events", { type: "playback", sequence: S.seq.id, start: +a.toFixed(3), end: +b.toFixed(3), rate: S.rate || 1 }).catch(() => { }); S.playStart = null; } $("#prgPlay").textContent = S.playing ? "❚❚" : "▶"; if (S.playing) { S.rate = S.rate || 1; if (!was) S.lastRaf = performance.now(); schedulePlayback(); } else { if (playbackFrame !== null) cancelAnimationFrame(playbackFrame); playbackFrame = null; S.rate = 1; renderProgram(); } }

// ---------- source monitor ----------
function loadSource(mid) {
  const media = S.proj.media[mid]; if (!media) { status("Choose a source in the current project.", "err"); return false; }
  if (S.src !== media) window.CR?.cancelSourceRelink?.(undefined, "");
  S.monitorScrub?.cancel("Source changed."); S.src = media; S.srcIn = null; S.srcOut = null; const v = $("#srcVideo");
  FilmocityProxyPreview.replaceSource(v, sourceMediaUrl(media), { retain: false, report: sourcePreviewError });
  try { const bounds = sourceFrameBounds(v, media); if (seekPreviewPicture(v, bounds.begin, media, { playing: !v.paused, strictPlayback: true, begin: bounds.begin, end: bounds.end }) !== null) retainSourceAddress(v, bounds.begin); } catch {}
  updateSourcePreviewState(); $("#srcName").textContent = media.name; updateSrcIO(); renderBin(); setFocus("source"); return true;
}
function updateSrcIO() { $("#srcIO").textContent = `in ${S.srcIn == null ? "—" : fmtTC(S.srcIn, mediaRate(S.src, true), 'ndf')} · out ${S.srcOut == null ? "—" : fmtTC(S.srcOut, mediaRate(S.src, true), 'ndf')}`; }
function markIO(which) {
  if (S.focus === "source") {
    if (!S.src || S.proj.media[S.src.id] !== S.src) return false;
    const time = sourcePlayheadTime(), duration = sourceDuration();
    if (!Number.isFinite(time)) return false;
    const value = Math.max(0, duration == null ? time : Math.min(duration, time));
    if (which === "in") S.srcIn = value; else S.srcOut = value; updateSrcIO();
  } else { const si = S.proj.sequences.indexOf(S.seq); applyOps([{ op: "set", path: `/sequences/${si}/${which === "in" ? "in_point" : "out_point"}`, value: S.t }], "sequence_io", `mark sequence ${which}`); }
}
function updateSourceTime() {
  const video = $("#srcVideo"); if (!S.src) return;
  const duration = sourceDuration();
  let local = sourcePlayheadTime(video);
  if (local < 0) { seekSourceTime(0, false); local = sourcePlayheadTime(video); }
  else if (duration != null && local >= duration) { video.pause?.(); seekSourceTime(duration); local = sourcePlayheadTime(video); }
  $("#srcTC").textContent = fmtTC(local, mediaRate(S.src, true), 'ndf');
  $("#srcDur").textContent = duration == null ? "—" : fmtTC(duration, mediaRate(S.src, true), 'ndf');
  const bar = $("#srcScrub"); if (bar?.firstElementChild && duration) bar.firstElementChild.style.width = (Math.max(0, Math.min(1, local / duration)) * 100) + "%";
}
function bindMonitorScrub(bar, source) {
  bar.onmousedown = event => {
    if (event.button !== undefined && event.button !== 0 || S.gesture) return;
    const project = S.proj, sequence = S.seq, media = S.src, context = { ...S.context };
    if (!project || !sequence || source && !media) return;
    const duration = source ? sourceDuration() : seqDur(), rect = bar.getBoundingClientRect();
    if (!Number.isFinite(duration) || duration <= 0 || !Number.isFinite(rect.width) || rect.width <= 0) return;
    S.monitorScrub?.cancel("Scrub replaced.");
    const identity = source ? placementMediaIdentity(media) : null;
    const valid = () => S.proj === project && S.seq === sequence && window.FilmocitySync.sameProject(context, S.context) && !S.switching &&
      (!source || S.src === media && S.proj.media[media.id] === media && placementMediaIdentity(media) === identity);
    if (source) $("#srcVideo").pause?.(); else if (S.playing) togglePlay(false, { commitTrim: false });
    const move = ev => { if (!Number.isFinite(ev.clientX)) return; const at = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width)) * duration; source ? seekSourceTime(at, false) : seekTo(at); };
    const controller = window.FilmocityGestures.attachMouseGesture({ target: window, document, valid, move, commit: () => {}, cancel: () => {}, failed: error => status(error.message, "err"), settled: () => { if (S.monitorScrub === controller) S.monitorScrub = null; } });
    S.monitorScrub = controller; controller.update(event); event.preventDefault();
  };
}
function clearIO() { const si = S.proj.sequences.indexOf(S.seq); applyOps([{ op: "set", path: `/sequences/${si}/in_point`, value: null }, { op: "set", path: `/sequences/${si}/out_point`, value: null }], "sequence_io", "clear in/out"); }
function timelineRangeHooks(sequence = S.seq) {
  const reservedIds = S.proj.sequences.flatMap(sq => [...sq.tracks.flatMap(tr => tr.clips.map(c => c.id)), ...(sq.markers || []).map(m => m.id), ...(sq.captions || []).map(c => c.id)]).concat(sequence.tracks.flatMap(tr => tr.clips.map(c => c.id))).filter(id => id != null);
  return { clock: c => ({ duration: clipDur(c), sourceOffset: at => sourceOffset(c, at), speedAt: at => speedAt(c, at) }), id: uid, reservedIds };
}
function timelineRangeTrackIds(ids, { sync = false } = {}) {
  const tracks = new Map(S.seq.tracks.map(tr => [tr.id, tr])), selected = new Set(ids);
  for (const id of selected) { const tr = tracks.get(id); if (!tr) throw Error("The edit track no longer exists."); if (tr.locked) throw Error("Unlock every affected track before editing."); }
  if (sync) for (const tr of S.seq.tracks) if (!tr.locked && tr.sync_lock !== false) selected.add(tr.id);
  return [...selected];
}
function timelineRangeOps(plan, original = S.seq, annotationChange = null) {
  const candidate = plan.sequence, si = S.proj.sequences.indexOf(original), ops = [];
  if (si < 0 || original !== S.seq || !candidate || candidate.id !== original.id || candidate.tracks.length !== original.tracks.length) throw Error("The timeline changed before this range edit could be planned.");
  for (let ti = 0; ti < original.tracks.length; ti++) { const before = original.tracks[ti], after = candidate.tracks[ti];
    if (!after || after.id !== before.id) throw Error("The timeline tracks changed before this edit.");
    if (JSON.stringify(before.clips) !== JSON.stringify(after.clips)) { if (before.locked) throw Error("Unlock every affected track before editing."); ops.push({ op: "set", path: `/sequences/${si}/tracks/${ti}/clips`, value: deep(after.clips) }); }
  }
  const annotations = window.FilmocityTimelineAnnotations.edit(original, annotationChange, timelineRangeHooks(candidate));
  for (const [key, value] of Object.entries(annotations)) ops.push({ op: "set", path: `/sequences/${si}/${key}`, value });
  return ops;
}
function placementReady() {
  if (!canEdit()) return false;
  if (!S.proj?.sequences.includes(S.seq) || projectSaveState().error) { status("Resolve project loading or unsaved edits in Recovery before placing clips.", "err"); return false; }
  return true;
}
function placementId(used) {
  for (let n = 0; n < 100; n++) { const id = uid(); if (id && !used.has(id)) { used.add(id); return id; } }
  throw Error("Could not allocate a unique clip ID. Try again.");
}
function placementIds() { return new Set(S.proj.sequences.flatMap(sq => sq.tracks.flatMap(tr => tr.clips.flatMap(c => [c.id, c.group].filter(Boolean))))); }
function placementMediaIdentity(media) {
  const fields = ["path", "duration", "has_video", "has_audio", "is_image", "width", "height", "frame_rate", "fps", "interpret_fps", "sample_rate", "channels", "subclip_of", "sub_in", "sub_out", "synthetic", "sequence_frames", "size", "mtime", "sha256"];
  return JSON.stringify(Object.fromEntries(fields.map(key => [key, media[key] ?? null])));
}
function placementNested(id) {
  const seen = new Set(), visiting = new Set();
  const visit = sid => {
    if (sid === S.seq.id) throw Error("This placement would nest the active sequence inside itself.");
    if (visiting.has(sid)) throw Error("The nested source contains a sequence cycle.");
    if (seen.has(sid)) return; visiting.add(sid);
    const sq = S.proj.sequences.find(x => x.id === sid); if (!sq) throw Error("The nested sequence is missing from this project.");
    for (const tr of sq.tracks) for (const c of tr.clips) { if (c.media_id && !S.proj.media[c.media_id]) throw Error("The nested sequence references missing media."); if (c.sequence_id) visit(c.sequence_id); }
    visiting.delete(sid); seen.add(sid);
  };
  visit(id); return S.proj.sequences.find(sq => sq.id === id);
}
function validatePlacementClip(clip, track) {
  if (!track || track.locked) throw Error("Every destination track must exist and be unlocked before placing clips.");
  const duration = clipDur(clip), minimum = track.kind === "video" ? timing.fromFrames(1, S.seq.fps || 30) : 1 / 48000;
  if (![clip.start, clip.in_, clip.out, duration].every(Number.isFinite) || clip.start < 0 || clip.in_ < 0 || clip.out <= clip.in_ || duration < minimum - 1e-10) throw Error("Choose a positive source range of at least one video frame or one audio sample.");
  if (clip.media_id && clip.sequence_id) throw Error("A clip cannot reference both media and a nested sequence.");
  let limit = Infinity, still = false;
  if (clip.media_id) {
    const media = S.proj.media[clip.media_id]; if (!media) throw Error("The source media is missing from this project.");
    if (track.kind === "audio" ? !media.has_audio : !(media.has_video || media.is_image)) throw Error("The source does not match the destination track kind.");
    still = !!media.is_image; limit = still ? Infinity : media.duration;
  } else if (clip.sequence_id) {
    if (track.kind !== "video") throw Error("Place nested sequences on a video track.");
    limit = seqDurOf(placementNested(clip.sequence_id));
  } else if (track.kind !== "video" || !(clip.title || clip.graphic || clip.adjustment)) throw Error("The clip has no supported source for this track.");
  if (limit !== Infinity && (!Number.isFinite(limit) || limit <= 0 || (clip.hold ? clip.in_ >= limit : clip.out > limit + 1e-9))) throw Error("The selected source range exceeds the available media.");
  window.FilmocityClipSplit.bounds(clip, { duration, sourceOffset: t => sourceOffset(clip, t), speedAt: t => speedAt(clip, t) }, { sourceLimit: limit, still });
  return duration;
}
function planPlacement(placements, mode, at) {
  if (!["insert", "overwrite"].includes(mode)) throw Error("Choose Insert or Overwrite placement.");
  if (!Number.isFinite(at) || at < 0) throw Error("Choose a valid timeline position.");
  const targetIds = [...new Set(placements.map(item => item.trackId))], participants = timelineRangeTrackIds(targetIds, { sync: mode === "insert" });
  const picture = placements.find(item => trackOf(item.trackId)?.kind === "video");
  const pictureParticipant = participants.some(id => trackOf(id).kind === "video");
  // Preserve every group offset; align its first picture anchor, or the insertion point when picture tracks participate.
  const anchor = picture ? picture.clip.start - at : 0, fps = S.seq.fps || 30;
  const firstFrame = Math.ceil(anchor * timing.frameRate(fps) - 1e-7);
  const start = Math.max(0, picture || (mode === "insert" && pictureParticipant) ? timing.fromFrames(Math.max(firstFrame, timing.toFrames(at + anchor, fps)), fps) - anchor : at);
  const shifted = placements.map(item => ({ trackId: item.trackId, clip: { ...deep(item.clip), start: item.clip.start + start - at } }));
  for (const item of shifted) validatePlacementClip(item.clip, trackOf(item.trackId));
  const span = Math.max(...shifted.map(item => clipEnd(item.clip))) - start, hooks = timelineRangeHooks(S.seq);
  let candidate = S.seq, annotationChange = null, gap = span;
  if (mode === "insert") {
    if (pictureParticipant) gap = timing.fromFrames(Math.ceil(span * timing.frameRate(S.seq.fps || 30) - 1e-7), S.seq.fps || 30);
    const insertHooks = { ...hooks, reservedIds: [...hooks.reservedIds, ...shifted.map(item => item.clip.id)] };
    const inserted = window.FilmocityTimelineRange.planInsert(candidate, participants, start, gap, insertHooks); candidate = inserted.sequence;
    annotationChange = { type: "insert", at: start, duration: gap };
  }
  const plan = window.FilmocityTimelineRange.planOverwrite(candidate, shifted, hooks);
  return { ops: timelineRangeOps(plan, S.seq, annotationChange), ids: shifted.map(item => item.clip.id), start, end: start + gap, padding: mode === "insert" ? gap - span : 0 };
}
function commitPlacement(plan, tool, reason, advance = false) {
  const pending = applyOps(plan.ops, tool, reason); S.sel = new Set(plan.ids);
  if (advance) { S.t = plan.end; updatePlayhead(); } refreshSel(); CR.panels.render();
  if (plan.padding > 1e-9) status(`Inserted with ${(plan.padding * 1000).toFixed(3)} ms of trailing space to keep picture tracks on frame boundaries.`);
  return pending;
}
function beginMediaDrag(event, mid, only = "", monitor = false, owner = null) {
  S.mediaDrag = null; event.dataTransfer?.clearData?.();
  if (!placementReady()) { event.preventDefault(); return false; }
  try {
    const nested = typeof mid === "string" && mid.startsWith("seq:"), source = nested ? S.proj.sequences.find(sq => sq.id === mid.slice(4)) : S.proj.media[mid];
    if (!source || owner && (owner.project !== S.proj || owner.source !== source || !window.FilmocitySync.sameProject(owner.context, S.context))) throw Error("Drag the source again from the current project's bin.");
    if (monitor && (nested || source !== S.src || S.proj.media[S.src.id] !== S.src)) throw Error("Reload this project's clip in the Source monitor before dragging.");
    if (!["", "video", "audio"].includes(only)) throw Error("Choose a supported media drag mode.");
    const kind = only || (nested || source.has_video || source.is_image ? "video" : "audio");
    const inn = monitor ? S.srcIn ?? 0 : 0, out = monitor ? S.srcOut ?? (source.is_image ? S.prefs.still || 5 : source.duration) : nested ? seqDurOf(source) : source.is_image ? S.prefs.still || 5 : source.duration;
    if (monitor) validatePlacementClip({ start: 0, in_: inn, out, speed: 1, ...(nested ? { sequence_id: source.id } : { media_id: mid }) }, { kind });
    const payload = { version: 1, token: uid(), context: { workspace: S.context.workspace, project: S.context.project }, mid, only, in_: inn, out };
    const serialized = JSON.stringify(payload), identity = nested ? JSON.stringify(source) : placementMediaIdentity(source);
    S.mediaDrag = { project: S.proj, source, identity, serialized, context: { ...S.context } };
    event.dataTransfer.setData("application/x-filmocity-media", serialized); event.dataTransfer.setData("text/media", mid); event.dataTransfer.setData("text/only", only); event.dataTransfer.effectAllowed = "copy";
    return true;
  } catch (error) { S.mediaDrag = null; event.dataTransfer?.clearData?.(); event.preventDefault(); status(error.message || String(error), "err"); return false; }
}
function readMediaDrag(event) {
  const serialized = event.dataTransfer?.getData("application/x-filmocity-media"), drag = S.mediaDrag;
  if (!serialized || !drag || serialized !== drag.serialized || drag.project !== S.proj || !window.FilmocitySync.sameProject(drag.context, S.context)) throw Error("Drag the source again from the current project; this drag is no longer valid.");
  const payload = JSON.parse(serialized), nested = payload.mid.startsWith("seq:"), source = nested ? S.proj.sequences.find(sq => sq.id === payload.mid.slice(4)) : S.proj.media[payload.mid];
  if (payload.version !== 1 || !window.FilmocitySync.sameProject(payload.context, S.context) || source !== drag.source || (nested ? JSON.stringify(source) : placementMediaIdentity(source)) !== drag.identity) throw Error("The dragged source changed. Drag it again after reloading the source.");
  return payload;
}
function dropMedia(event, trackId, at, mode, reason) {
  if (!placementReady()) return false;
  try {
    const payload = readMediaDrag(event), nested = payload.mid.startsWith("seq:"), source = nested ? null : S.proj.media[payload.mid];
    const kind = payload.only || (nested || source.has_video || source.is_image ? "video" : "audio"), destination = trackId || S.target[kind], track = trackOf(destination);
    if (!track || payload.only && track.kind !== payload.only) throw Error(`Drop ${payload.only || kind}-only material on a ${payload.only || kind} track.`);
    return placeMedia(payload.mid, destination, at, payload.in_, payload.out, mode, reason, { audioLinked: payload.only !== "video" });
  } catch (error) { status(error.message || String(error), "err"); return false; } finally { S.mediaDrag = null; }
}
function insertFromSource(mode) {
  if (!placementReady()) return false;
  if (!S.src || S.proj.media[S.src.id] !== S.src) { status("Reload a clip from this project in the Source monitor before placing it.", "err"); return false; }
  return placeMedia(S.src.id, S.src.has_video || S.src.is_image ? S.target.video : S.target.audio, S.t, S.srcIn ?? 0, S.srcOut ?? (S.src.is_image ? S.prefs.still || 5 : S.src.duration), mode, `${mode} from source`);
}
function placeMedia(mid, trackId, t, inn, out, mode, reason, options = {}) {
  if (!placementReady()) return false;
  try {
    const tr = trackOf(trackId); if (!tr || tr.locked) throw Error("Choose an existing unlocked destination track.");
    const nestedId = typeof mid === "string" && mid.startsWith("seq:") ? mid.slice(4) : null;
    const media = nestedId ? null : S.proj.media[mid]; if (!nestedId && !media) throw Error("The source media is missing from this project.");
    const duration = nestedId ? seqDurOf(placementNested(nestedId)) : media.is_image ? S.prefs.still || 5 : media.duration;
    const clip = { id: placementId(placementIds()), media_id: nestedId ? null : mid, ...(nestedId ? { sequence_id: nestedId } : {}), start: t, in_: inn ?? 0, out: out ?? duration, speed: 1,
      transform: { x: 0, y: 0, scale: 1, rotation: 0, opacity: 1 }, audio: { gain_db: 0, fade_in: 0, fade_out: 0, linked: options.audioLinked !== false }, transition_in: null, transition_out: null, keyframes: {}, color: {} };
    const plan = planPlacement([{ trackId, clip }], mode, t); return commitPlacement(plan, mode, reason || `${mode} source`, true);
  } catch (error) { status(error.message || String(error), "err"); return false; }
}

// ---------- editing commands ----------
function razorAt(t, trackId, strict = false) {
  const tr = trackOf(trackId); if (!tr || tr.locked || !Number.isFinite(t)) return null;
  t = timing.fromFrames(timing.toFrames(t, S.seq.fps), S.seq.fps);
  const c = tr.clips.find(x => t > x.start + 1e-9 && t < clipEnd(x) - 1e-9); if (!c) return null;
  try {
    const [left, right] = window.FilmocityClipSplit.split(c, t - c.start, uid(), { duration: clipDur(c), sourceOffset: at => sourceOffset(c, at), speedAt: at => speedAt(c, at) });
    right.start = t;
    return [left, right].map(clip => ({ op: "set_clip", sequence: S.seq.id, track: tr.id, clip }));
  } catch (error) { if (strict) throw error; status(error.message, "err"); return null; }
}
function shiftKf(kfs, dt, keepAfter, cutAt) { if (!kfs) return {}; const o = {}; for (const k of Object.keys(kfs)) { o[k] = kfs[k].filter(x => keepAfter ? x.t >= -dt : x.t <= cutAt).map(x => ({ t: x.t + (keepAfter ? dt : 0), v: x.v })); if (!o[k].length) delete o[k]; } return o; }
function razorAtCmd(t, trackId) { if (!canEdit()) return; const ops = razorAt(t, trackId); if (ops) applyOps(ops, "razor", "split at " + fmtTC(ops[1].clip.start, S.seq.fps)); }
function addEdits(trackIds, tool, reason) {
  if (!canEdit()) return;
  try { const ops = []; for (const id of new Set(trackIds)) { const cut = razorAt(S.t, id, true); if (cut) ops.push(...cut); } if (ops.length) return applyOps(ops, tool, reason); }
  catch (error) { status(error.message, "err"); }
}
function addEditAtPlayhead() { return addEdits([S.target.video, S.target.audio], "add_edit", "add edit at playhead"); }
function selectedClips() { return [...S.sel].map(clipById).filter(Boolean); }
function rippleMarkers(t, d) {
  if (!Number.isFinite(t) || !Number.isFinite(d)) throw Error("Marker ripple requires finite times.");
  const si = S.proj.sequences.indexOf(S.seq), markers = S.seq.markers || [];
  if (si < 0) throw Error("The sequence changed before its markers could be shifted.");
  if (!d || !markers.some(m => m.time >= t - 1e-6)) return [];
  return [{ op: "set", path: `/sequences/${si}/markers`, value: markers.map(m => m.time >= t - 1e-6 ? { ...deep(m), time: Math.max(0, m.time + d) } : deep(m)) }];
}
function removalReady() {
  if (!canEdit()) return false;
  if (!S.proj?.sequences.includes(S.seq)) { status("Wait for the current sequence to finish loading before removing material.", "err"); return false; }
  if (projectSaveState().error) { status("Resolve unsaved edits in Recovery before removing timeline material.", "err"); return false; }
  return true;
}
function removalIntersects(c, ranges) {
  const end = clipEnd(c); let lo = 0, hi = ranges.length;
  // Normalized disjoint ranges are sorted. Only the first range ending after
  // this clip starts can be its first intersection; avoid clips × ranges work.
  while (lo < hi) { const mid = (lo + hi) >>> 1, boundary = ranges[mid][1], epsilon = Math.max(1e-12, Math.abs(boundary) * Number.EPSILON * 8); if (c.start >= boundary - epsilon) lo = mid + 1; else hi = mid; }
  if (lo === ranges.length) return false;
  const [a, b] = ranges[lo], epsilon = Math.max(1e-12, Math.abs(a) * Number.EPSILON * 8, Math.abs(b) * Number.EPSILON * 8);
  return c.start < b - epsilon && end > a + epsilon;
}
function rippleRemovalPlan(trackIds, intervals, selectedIds = new Set()) {
  const engine = window.FilmocityTimelineRange, ranges = engine.normalizeRanges(intervals), tracks = timelineRangeTrackIds(trackIds, { sync: true }), owners = new Set(tracks);
  for (const tr of S.seq.tracks) if (owners.has(tr.id)) for (const c of tr.clips) {
    if (!selectedIds.has(c.id) && removalIntersects(c, ranges)) throw Error("Ripple removal would cut unselected material. Use Extract to cut through it, or adjust track sync locks.");
  }
  const plan = engine.planRemove(S.seq, tracks, ranges, { close: true }, timelineRangeHooks());
  const ops = timelineRangeOps(plan, S.seq, { type: "remove", ranges: plan.removedRanges, close: true });
  return { ops, ranges: plan.removedRanges };
}
function applyRemoval(ops, tool, reason, target) {
  if (!ops.length) return false;
  S.sel.clear(); S.gap = null;
  const pending = applyOps(ops, tool, reason);
  if (Number.isFinite(target)) seekTo(target);
  return pending;
}
function deleteSel(ripple) {
  if (!removalReady()) return false;
  const sel = selectedClips();
  if (S.focus === "project" && S.binSel.size && !sel.length) return ACTIONS.bin_delete[2]();
  try {
    if (!sel.length && S.gap) {
      const gap = S.gap, tr = trackOf(gap.track);
      if (gap.project !== S.proj || gap.sequence !== S.seq || !tr) throw Error("Select the gap again in the current sequence.");
      if (tr.locked) throw Error("Unlock the gap's track before closing it.");
      const before = tr.clips.find(c => c.id === gap.before), after = tr.clips.find(c => c.id === gap.after);
      const epsilon = Math.max(1e-10, Math.abs(gap.end || 0) * Number.EPSILON * 8);
      if (!before || !after || !Number.isFinite(gap.start) || !Number.isFinite(gap.end) || gap.start < 0 || gap.end <= gap.start || Math.abs(clipEnd(before) - gap.start) > epsilon || Math.abs(after.start - gap.end) > epsilon || tr.clips.some(c => removalIntersects(c, [[gap.start, gap.end]]))) throw Error("The selected gap has changed. Select the gap again.");
      const plan = rippleRemovalPlan([tr.id], [[gap.start, gap.end]]);
      return applyRemoval(plan.ops, "close_gap", `close gap on ${tr.id}`, gap.start);
    }
    if (!sel.length) return false;
    if (sel.some(({ tr }) => tr.locked)) throw Error("Unlock every selected track before deleting these clips.");
    if (!ripple) return applyRemoval(sel.map(({ c, tr }) => ({ op: "remove_clip", sequence: S.seq.id, track: tr.id, clip_id: c.id })), "delete", `delete ${sel.length} clip(s)`);
    const plan = rippleRemovalPlan([...new Set(sel.map(({ tr }) => tr.id))], sel.map(({ c }) => [c.start, clipEnd(c)]), new Set(sel.map(({ c }) => c.id)));
    const target = window.FilmocityTimelineRange.mapTime(S.t, plan.ranges);
    return applyRemoval(plan.ops, "ripple_delete", `ripple delete ${sel.length} clip(s)`, target);
  } catch (error) { status(error.message, "err"); return false; }
}
function applyTransition(type = "dissolve", dur = 1.0) {
  let sel = selectedClips(), c, tr; if (sel.length) ({ c, tr } = sel[0]); else { tr = trackOf(S.target.video); c = tr.clips.find(x => Math.abs(x.start - S.t) < frame() * 2) || tr.clips.filter(x => S.t >= x.start && S.t < clipEnd(x))[0]; }
  if (!c) { status("Put the playhead at a cut or select a clip, then apply the transition."); return; }
  const ops = [{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, transition_in: { type, duration: Math.min(dur, clipDur(c) / 2) } } }];
  if (S.prefs.trans_sfx && tr.kind === "video") { const kind = { glitch: "impact", cross_zoom: "swoosh_reverse", dip_black: "impact", dip_white: "impact" }[type] || "whoosh"; api.json("POST", "/api/media/sfx", { kind, actor: "human" }).then(m => { const atr = S.seq.tracks.filter(t => t.kind === "audio").sort((a, b) => b.index - a.index)[0]; if (!atr) return; const st = Math.max(0, c.start - Math.min(0.25, m.duration * 0.4)); applyOps([{ op: "set_clip", sequence: S.seq.id, track: atr.id, clip: { id: uid(), media_id: m.id, start: +st.toFixed(3), in_: 0, out: m.duration, speed: 1, audio: { gain_db: -6, linked: true }, note: `${kind} under ${type}` } }], "sfx", `${kind} on ${type}`); }).catch(() => { }); }
  const prev = tr.clips.filter(x => x.id !== c.id && Math.abs(clipEnd(x) - c.start) < frame()).sort((a, b) => b.start - a.start)[0];
  if (prev) { if (type === "dip_black" || type === "dip_white") ops.push({ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: prev.id, transition_out: { type, duration: Math.min(dur, clipDur(prev) / 2) } } });
    else if (prev.media_id) { const m = S.proj.media[prev.media_id], ext = Math.min(dur, Math.max(0, (m.duration - prev.out) / (prev.speed || 1))); if (ext > 0) ops.push({ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: prev.id, out: prev.out + ext * (prev.speed || 1) } }); else status("Outgoing clip has no handle; transition plays over black."); } }
  applyOps(ops, "transition", `${type} ${dur}s`); }
function addTitle() { const tr = S.seq.tracks.filter(x => x.kind === "video").sort((a, b) => b.index - a.index)[0]; const c = { id: uid(), media_id: null, start: S.t, in_: 0, out: S.prefs.title || 3.0, speed: 1, title: { text: "Title", size: Math.round(S.seq.height * 0.07), color: "white", x: 0, y: 0, borderw: 0 }, transform: { opacity: 1 }, transition_in: { type: "fade", duration: 0.3 }, transition_out: { type: "fade", duration: 0.3 }, keyframes: {} };
  applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: c }], "title", "add title"); S.sel = new Set([c.id]); refreshSel(); CR.panels.render(); }
function addMarker() { const si = S.proj.sequences.indexOf(S.seq); applyOps([{ op: "set", path: `/sequences/${si}/markers`, value: [...(S.seq.markers || []), { id: uid(), time: S.t, name: "", author: "human" }] }], "marker", "add marker"); }
function moveTrack(trackId, dir) { const tr = trackOf(trackId); if (!tr) return; const same = S.seq.tracks.filter(t => t.kind === tr.kind).sort((a, b) => a.index - b.index); const i = same.indexOf(tr), j = i + dir; if (j < 0 || j >= same.length) return; const other = same[j]; const si = S.proj.sequences.indexOf(S.seq); const ti = S.seq.tracks.indexOf(tr), oi = S.seq.tracks.indexOf(other); applyOps([{ op: "set", path: `/sequences/${si}/tracks/${ti}/index`, value: other.index }, { op: "set", path: `/sequences/${si}/tracks/${oi}/index`, value: tr.index }], "track", `move ${tr.id} ${dir > 0 ? "up" : "down"}`); }
function deleteTrack(trackId, force) { const tr = trackOf(trackId); if (!tr) return; if (tr.clips.length && !force && !confirm(`Track ${tr.id} has ${tr.clips.length} clip(s). Delete anyway?`)) return; const si = S.proj.sequences.indexOf(S.seq), ti = S.seq.tracks.indexOf(tr); if (S.target[tr.kind] === tr.id) { const other = S.seq.tracks.find(t => t.kind === tr.kind && t.id !== tr.id); if (other) S.target[tr.kind] = other.id; } applyOps([{ op: "remove", path: `/sequences/${si}/tracks/${ti}` }], "track", `delete track ${tr.id}`); }
function deleteEmptyTracks() { const si = S.proj.sequences.indexOf(S.seq); const ops = []; [...S.seq.tracks].map((t, i) => [t, i]).filter(([t]) => !t.clips.length && S.seq.tracks.filter(x => x.kind === t.kind).length > 1).reverse().forEach(([t, i]) => ops.push({ op: "remove", path: `/sequences/${si}/tracks/${i}` })); if (ops.length) applyOps(ops, "track", `delete ${ops.length} empty track(s)`); }
function addTrack(kind) { const si = S.proj.sequences.indexOf(S.seq), idx = Math.max(0, ...S.seq.tracks.filter(t => t.kind === kind).map(t => t.index)) + 1; applyOps([{ op: "insert", path: `/sequences/${si}/tracks/${S.seq.tracks.length}`, value: { id: (kind === "video" ? "V" : "A") + idx, kind, index: idx, muted: false, locked: false, clips: [] } }], "track", `add ${kind} track`); }
function sequenceOrganizationOwner() {
  const owner = editorialCommandOwner();
  owner.selection = JSON.stringify([...(S.sel || [])]); owner.binSelection = JSON.stringify([...(S.binSel || [])]);
  owner.sourceId = S.src?.id || null; owner.sourceMarks = JSON.stringify([S.srcIn, S.srcOut]);
  owner.snapshot = JSON.stringify([owner.project.sequences, owner.project.media]);
  owner.existingSequences = new Set(owner.project.sequences.map(sq => sq.id));
  owner.existingClips = new Set(owner.project.sequences.flatMap(sq => sq.tracks.flatMap(tr => tr.clips.map(c => c.id))));
  return owner;
}
function sequenceOrganizationCurrent(owner) {
  return editorialOwnerCurrent(owner) && !owner.cancelled && owner.selection === JSON.stringify([...(S.sel || [])]) &&
    owner.binSelection === JSON.stringify([...(S.binSel || [])]) && owner.sourceId === (S.src?.id || null) &&
    owner.sourceMarks === JSON.stringify([S.srcIn, S.srcOut]) && owner.snapshot === JSON.stringify([S.proj.sequences, S.proj.media]);
}
function sequenceOrganizationIntent(owner) {
  return !owner.cancelled && window.FilmocitySync.sameProject(owner.context, S.context) && S.seq?.id === owner.sequence.id &&
    owner.selection === JSON.stringify([...(S.sel || [])]) && owner.binSelection === JSON.stringify([...(S.binSel || [])]) &&
    owner.sourceId === (S.src?.id || null) && owner.sourceMarks === JSON.stringify([S.srcIn, S.srcOut]);
}
function sequenceOrganizationName(name) {
  if (typeof name !== "string" || !name.trim() || [...name.trim()].length > 120 || /[\u0000-\u001f]/.test(name)) throw Error("Enter a sequence name from 1 to 120 characters without control characters.");
  return name.trim();
}
function captureNestSelection() {
  const owner = sequenceOrganizationOwner(), selected = selectedClips();
  if (!selected.length || selected.length !== S.sel.size) throw Error("Select current clips to nest.");
  if (selected.some(({ tr }) => tr.locked)) throw Error("Unlock every selected track before nesting.");
  owner.clipIds = [...S.sel]; owner.pending = false; owner.submitted = false; owner.cancelled = false; owner.generation = 0; owner.review = null;
  owner.name = `Nested ${String(S.proj.sequences.length).padStart(2, "0")}`;
  return owner;
}
async function reviewNestSelection(owner, name, current = () => true) {
  if (!sequenceOrganizationCurrent(owner) || owner.pending || owner.submitted || !canEdit() || !current()) throw Error("The selected clips or project changed. Reopen Nest.");
  name = sequenceOrganizationName(name);
  const generation = ++owner.generation, valid = () => sequenceOrganizationCurrent(owner) && owner.generation === generation && current();
  owner.pending = true; owner.review = null;
  try {
    const state = await flushSaves();
    if (state !== projectSaveState() || state.error || !valid() || !canEdit()) throw Error("The selection changed or Recovery needs attention. Reopen Nest after saving.");
    const context = { ...state.context }, report = await api.json("POST", "/api/sequence/nest/review", { sequence: owner.sequence.id, clip_ids: owner.clipIds, name, _context: context, actor: "human", client: CLIENT });
    if (!valid() || state !== projectSaveState() || state.pending || state.error || S.context.revision !== context.revision) throw Error("The saved sequence changed while Nest was reviewed. Review again.");
    const sum = report?.summary, strings = values => Array.isArray(values) && values.every(value => typeof value === "string");
    if (typeof report?.ok !== "boolean" || report.kind !== "sequence_nesting" || report.sequence !== owner.sequence.id || JSON.stringify(report.clip_ids) !== JSON.stringify(owner.clipIds) ||
      !window.FilmocitySync.validContext(report.context) || !window.FilmocitySync.sameProject(context, report.context) || report.context.revision !== context.revision ||
      report.settings?.name !== name || !/^[a-f0-9]{64}$/.test(report.fingerprint || "") || !Array.isArray(report.issues) || report.issues.some(issue => !issue || typeof issue.message !== "string" || !["error", "warning", "info"].includes(issue.severity)) ||
      !sum || typeof sum.message !== "string" || sum.name !== name || sum.selected_count !== owner.clipIds.length || !sum.range || ![sum.range.start, sum.range.end, sum.range.duration].every(Number.isFinite) || sum.range.start < 0 || sum.range.end <= sum.range.start || Math.abs(sum.range.duration - (sum.range.end - sum.range.start)) > 1e-8 ||
      !Array.isArray(sum.source_tracks) || sum.source_tracks.some(tr => !tr || typeof tr.id !== "string" || !["video", "audio"].includes(tr.kind) || !strings(tr.selected_clip_ids)) ||
      !Array.isArray(sum.added_tracks) || sum.added_tracks.some(tr => !tr || typeof tr.id !== "string" || !["video", "audio"].includes(tr.kind)) || !strings(sum.processing) || !strings(sum.warnings) || !strings(sum.wrapper_clip_ids) || !strings(sum.affected_fields) ||
      sum.picture_ranges !== undefined && (!Array.isArray(sum.picture_ranges) || sum.picture_ranges.some(range => !Array.isArray(range) || range.length !== 2 || !range.every(Number.isFinite) || range[0] < 0 || range[1] <= range[0])) ||
      report.ok && (typeof sum.child_sequence !== "string" || !sum.child_sequence || owner.existingSequences.has(sum.child_sequence) || !sum.wrapper_clip_ids.length || new Set(sum.wrapper_clip_ids).size !== sum.wrapper_clip_ids.length || sum.wrapper_clip_ids.some(id => !id || owner.existingClips.has(id)))) throw Error("Nest review did not confirm this saved selection. Review again before applying.");
    owner.review = { report, snapshot: JSON.stringify(report), generation }; return report;
  } finally { owner.pending = false; }
}
function nestReviewCurrent(owner, report) {
  const state = projectSaveState();
  return sequenceOrganizationCurrent(owner) && owner.review?.report === report && owner.review.snapshot === JSON.stringify(report) && owner.review.generation === owner.generation &&
    report.ok === true && !report.issues.some(issue => issue.severity === "error") && !state.pending && !state.error && S.context.revision === report.context.revision && state.context.revision === report.context.revision;
}
async function applyNestSelection(owner, report, current = () => true) {
  if (!nestReviewCurrent(owner, report) || owner.pending || owner.submitted || !current() || !canEdit()) throw Error("The reviewed selection changed. Review Nest again before Apply.");
  owner.pending = true; let savedReply;
  try {
    if (S.playing) togglePlay(false, { commitTrim: false });
    await workflowRequest(async context => {
      if (!nestReviewCurrent(owner, report) || !current()) { const error = Error("Nest was canceled or its selection changed before submission."); error.status = 409; throw error; }
      owner.submitted = true;
      const reply = await api.json("POST", "/api/sequence/nest", { sequence: owner.sequence.id, clip_ids: owner.clipIds, name: report.settings.name, fingerprint: report.fingerprint, _context: context, actor: "human", client: CLIENT });
      if (S.proj !== owner.project || !window.FilmocitySync.sameProject(owner.context, S.context)) throw Error("The original project changed while Nest was submitted. Inspect its saved outcome before continuing.");
      if (reply?.ok !== true || reply.kind !== "sequence_nesting" || reply.changed !== true || reply.project !== owner.context.project || reply.sequence !== owner.sequence.id ||
        reply.child_sequence !== report.summary.child_sequence || JSON.stringify(reply.source_clip_ids) !== JSON.stringify(owner.clipIds) || JSON.stringify(reply.wrapper_clip_ids) !== JSON.stringify(report.summary.wrapper_clip_ids) ||
        typeof reply.summary?.message !== "string" || !Array.isArray(reply.warnings) || reply.warnings.some(value => typeof value !== "string")) throw Error("Nest did not confirm the reviewed change. Inspect the saved project in Recovery before repeating it.");
      savedReply = reply;
      // The transaction reloads the owned project; navigation belongs to the caller's still-current intent.
      return { ...reply, sequence: null };
    }, { ...report.context, sequence: owner.sequence.id });
    if (sequenceOrganizationIntent(owner)) { S.sel = new Set(savedReply.wrapper_clip_ids); refreshSel(); CR.panels?.render(); }
    if (window.FilmocitySync.sameProject(owner.context, S.context)) status([savedReply.summary.message, ...savedReply.warnings].join(" "));
    owner.cancelled = true; owner.review = null; return savedReply;
  } catch (error) { if ([400, 401, 403, 404, 409, 422, 501].includes(error.status)) owner.submitted = false; throw error; }
  finally { owner.pending = false; }
}
function nestSelected() {
  try { return CR.panels.nestDialog(captureNestSelection()); }
  catch (error) { status(error.message || String(error), "err"); return false; }
}
let sequenceCreationPending = null;
async function createSequence(mode, name) {
  let owner, ticket;
  try {
    if (sequenceCreationPending) throw Error("Wait for the current sequence creation to finish.");
    owner = sequenceOrganizationOwner();
    const body = { mode, sequence: owner.sequence.id };
    if (name !== undefined) {
      if (typeof name !== "string" || !name.trim() || [...name.trim()].length > 256 || /[\u0000-\u001f]/.test(name)) throw Error("Enter a sequence name from 1 to 256 characters without control characters.");
      body.name = name.trim();
    }
    if (mode === "source") {
      const media = S.src || owner.project.media[[...(S.binSel || [])][0]];
      if (!media?.id || owner.project.media[media.id] !== media) throw Error("Load or select a current source first.");
      if (!media.has_video && !media.has_audio && !media.is_image) throw Error("This source has no supported picture or audio stream. Finish preparation before creating a sequence.");
      if (media.is_image) {
        const duration = S.prefs?.still ?? 5;
        if (!Number.isFinite(duration) || duration <= 0) throw Error("Set a positive finite still-image duration in Preferences first.");
        body.still_duration = duration;
      } else if (!Number.isFinite(media.duration) || media.duration <= 0) throw Error("The source duration is not ready. Finish preparation before creating a sequence.");
      body.media_id = media.id;
    } else if (mode !== "empty") throw Error("Unknown sequence creation command.");
    ticket = { cancelled: false, submitted: false }; sequenceCreationPending = ticket;
    const escape = event => { if (event.key === "Escape" && !ticket.submitted) { event.preventDefault(); ticket.cancelled = true; } };
    ticket.escape = escape; window.addEventListener("keydown", escape, true);
    const valid = () => !ticket.cancelled && sequenceOrganizationCurrent(owner);
    status("Saving the new sequence… Escape cancels before submission.");
    const state = await flushSaves();
    if (state !== projectSaveState() || state.error || !valid() || !canEdit()) throw Error("Sequence creation was canceled, the source changed, or Recovery needs attention. Start again from the current selection.");
    if (S.playing) togglePlay(false, { commitTrim: false });
    let savedReply;
    await workflowRequest(async context => {
      if (!valid()) { const error = Error("Sequence creation was canceled or its original selection changed before submission."); error.status = 409; throw error; }
      ticket.submitted = true;
      const reply = await api.json("POST", "/api/sequence/create", { ...body, _context: context, actor: "human", client: CLIENT });
      if (S.proj !== owner.project || !window.FilmocitySync.sameProject(owner.context, S.context)) throw Error("The original project changed while sequence creation was submitted. Inspect its saved outcome before repeating it.");
      if (reply?.ok !== true || reply.kind !== "sequence_creation" || reply.changed !== true || reply.mode !== mode || reply.project !== owner.context.project ||
        reply.source_sequence !== owner.sequence.id || reply.media_id !== (body.media_id || null) || typeof reply.sequence !== "string" || !reply.sequence || owner.existingSequences.has(reply.sequence) ||
        (mode === "empty" ? reply.clip_id !== null : typeof reply.clip_id !== "string" || !reply.clip_id || owner.existingClips.has(reply.clip_id)) ||
        typeof reply.summary?.message !== "string" || !Array.isArray(reply.warnings) || reply.warnings.some(value => typeof value !== "string")) throw Error("Sequence creation did not confirm the saved sequence. Review Recovery before repeating this command.");
      savedReply = reply; return { ...reply, sequence: null };
    }, { ...state.context, sequence: owner.sequence.id });
    if (sequenceOrganizationIntent(owner)) {
      switchSeq(savedReply.sequence); S.sel = new Set(savedReply.clip_id ? [savedReply.clip_id] : []); refreshSel(); CR.panels?.render();
    }
    if (window.FilmocitySync.sameProject(owner.context, S.context)) {
      const format = savedReply.summary.format;
      status([savedReply.summary.message, format ? `${format.width}×${format.height} · ${format.frame_rate} fps · ${(format.timecode_format || "ndf").toUpperCase()}.` : "", ...savedReply.warnings].filter(Boolean).join(" "));
    }
    return savedReply;
  } catch (error) { if (!owner || window.FilmocitySync.sameProject(owner.context, S.context)) status(error.message || String(error), "err"); return false; }
  finally { if (ticket?.escape) window.removeEventListener("keydown", ticket.escape, true); if (sequenceCreationPending === ticket) sequenceCreationPending = null; }
}
function newSequence(name) { return createSequence("empty", name); }
function addAdjustmentLayer() { const ops = []; let tr = S.seq.tracks.filter(x => x.kind === "video").sort((a, b) => b.index - a.index)[0]; const c = { id: uid(), media_id: null, adjustment: true, start: S.t, in_: 0, out: 5.0, speed: 1, color: {}, keyframes: {} };
  if (tr.clips.some(x => x.start < S.t + 5.0 && clipEnd(x) > S.t)) { const idx = tr.index + 1, si = S.proj.sequences.indexOf(S.seq); tr = { id: "V" + idx, kind: "video", index: idx, muted: false, locked: false, clips: [] }; ops.push({ op: "insert", path: `/sequences/${si}/tracks/${S.seq.tracks.length}`, value: tr }); }
  ops.push({ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: c }); applyOps(ops, "adjustment", "add adjustment layer"); S.sel = new Set([c.id]); refreshSel(); CR.panels.render(); CR.showTab("color"); }
function setUiOverlay(kind) { S.uiOverlay = kind; status(kind ? `Platform UI overlay: ${kind} — keep text and faces out of the shaded areas` : "Platform UI overlay off"); renderProgram(); }
function drawUiOverlay(cv) { if (!S.uiOverlay) return; const ctx = cv.getContext("2d"), W = cv.width, H = cv.height; ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.filter = "none"; ctx.globalAlpha = 1; ctx.fillStyle = "rgba(0,0,0,.42)"; ctx.strokeStyle = "rgba(255,255,255,.35)"; ctx.lineWidth = 2;
  const box = (x, y, w, h, label) => { ctx.fillRect(x, y, w, h); ctx.strokeRect(x, y, w, h); if (label) { ctx.fillStyle = "rgba(255,255,255,.7)"; ctx.font = `${Math.round(W / 40)}px sans-serif`; ctx.fillText(label, x + 8, y + W / 32); ctx.fillStyle = "rgba(0,0,0,.42)"; } };
  if (S.uiOverlay === "reels") { box(0, 0, W, H * 0.11, "status / header"); box(W * 0.86, H * 0.52, W * 0.14, H * 0.30, ""); box(0, H * 0.79, W, H * 0.21, "caption · profile · audio"); }
  else if (S.uiOverlay === "tiktok") { box(0, 0, W, H * 0.09, "status / search"); box(W * 0.84, H * 0.48, W * 0.16, H * 0.34, ""); box(0, H * 0.76, W * 0.84, H * 0.24, "caption · sound"); }
  else if (S.uiOverlay === "shorts") { box(0, 0, W, H * 0.10, "status / title"); box(W * 0.86, H * 0.50, W * 0.14, H * 0.32, ""); box(0, H * 0.80, W, H * 0.20, "title · channel"); }
  ctx.restore(); }
function renderGuides() { const wrap = $("#uguides"); if (!wrap) return; const g = S.seq.guides || { h: [], v: [] }; const scr = $("#prgScreen"), cv = $("#prgCanvas"); const r = cv.getBoundingClientRect(), sr = scr.getBoundingClientRect();
  wrap.innerHTML = g.h.map((f, i) => `<div class="uguide h" data-gi="h|${i}" style="top:${r.top - sr.top + f * r.height}px" title="guide ${(f * 100).toFixed(1)}% — drag · double-click removes"></div>`).join("") + g.v.map((f, i) => `<div class="uguide v" data-gi="v|${i}" style="left:${r.left - sr.left + f * r.width}px" title="guide ${(f * 100).toFixed(1)}%"></div>`).join("");
  const si = S.proj.sequences.indexOf(S.seq); $$(".uguide", wrap).forEach(el => { el.onmousedown = ev => { if (!canEdit()) return; ev.stopPropagation(); const [ax, i] = el.dataset.gi.split("|"); const move = e => { const f = ax === "h" ? (e.clientY - r.top) / r.height : (e.clientX - r.left) / r.width; el.style[ax === "h" ? "top" : "left"] = (ax === "h" ? r.top - sr.top + f * r.height : r.left - sr.left + f * r.width) + "px"; el.dataset.f = f; }; const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); const ng = deep(g); ng[ax][+i] = Math.max(0, Math.min(1, +(el.dataset.f === undefined ? g[ax][+i] : +el.dataset.f).toFixed(4))); applyOps([{ op: "set", path: `/sequences/${si}/guides`, value: ng }], "guides", "move guide"); }; watchEditGesture(ev, move, up); }; el.ondblclick = ev => { ev.stopPropagation(); const [ax, i] = el.dataset.gi.split("|"); const ng = deep(g); ng[ax].splice(+i, 1); applyOps([{ op: "set", path: `/sequences/${si}/guides`, value: ng }], "guides", "remove guide"); }; });
  const rh = $("#rulerH"), rv = $("#rulerV"); if (rh && scr.classList.contains("showrulers")) { rh.innerHTML = ""; rv.innerHTML = ""; for (let x = 0; x <= S.seq.width; x += 100) { const px = r.left - sr.left + x / S.seq.width * r.width; rh.innerHTML += `<span style="position:absolute;left:${px}px;top:0;border-left:1px solid #666;height:${x % 500 ? 5 : 10}px"></span>${x % 500 ? "" : `<span style="position:absolute;left:${px + 2}px;top:2px">${x}</span>`}`; } for (let y = 0; y <= S.seq.height; y += 100) { const py = r.top - sr.top + y / S.seq.height * r.height; rv.innerHTML += `<span style="position:absolute;top:${py}px;left:0;border-top:1px solid #666;width:${y % 500 ? 5 : 10}px"></span>`; } } }
function addGuide(axis, f) { const si = S.proj.sequences.indexOf(S.seq); const g = deep(S.seq.guides || { h: [], v: [] }); g[axis].push(f == null ? 0.5 : f); applyOps([{ op: "set", path: `/sequences/${si}/guides`, value: g }], "guides", "add guide"); }
function snapToGuides(px, py) { if (!S.snapGuides) return [px, py]; const g = S.seq.guides || { h: [], v: [] }; const W = S.seq.width, H = S.seq.height; const tol = W / 120; let nx = px, ny = py; for (const f of g.v) { const gx = f * W - W / 2; if (Math.abs(px - gx) < tol) nx = gx; } for (const f of g.h) { const gy = f * H - H / 2; if (Math.abs(py - gy) < tol) ny = gy; } return [nx, ny]; }
function keyframesWithValue(keyframes, key, t, v) {
  const list = [...(keyframes[key] || [])], i = list.findIndex(k => Math.abs(k.t - t) < frame() / 2);
  if (i >= 0) list[i] = { ...list[i], v }; else list.push({ t: +t.toFixed(4), v });
  return { ...keyframes, [key]: list.sort((a, b) => a.t - b.t) };
}
function setKeyframeValue(c, tr, key, t, v) { applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, keyframes: keyframesWithValue(c.keyframes || {}, key, t, v) } }], "keyframe", `${key} keyframe`); }
function monitorHit(ev) { const sel = selectedClips().filter(x => x.tr.kind === "video" && x.c.media_id && S.t >= x.c.start && S.t < clipEnd(x.c)); if (sel.length !== 1) return null; const { c, tr } = sel[0], m = S.proj.media[c.media_id]; const cv = $("#prgCanvas"), r = cv.getBoundingClientRect(), W = cv.width, H = cv.height, k = W / r.width; const tf = c.transform || {}, kf = c.keyframes || {}, rel = S.t - c.start; const sc = kfVal(kf["transform.scale"], rel, tf.scale == null ? 1 : tf.scale), ox = kfVal(kf["transform.x"], rel, tf.x || 0), oy = kfVal(kf["transform.y"], rel, tf.y || 0); const [fw, fh] = programPictureSize(c, tr, W, H), fit = c.fit === "cover" ? Math.max(W / fw, H / fh) : Math.min(W / fw, H / fh), dw = fw * fit * sc, dh = fh * fit * sc;
  const px = (ev.clientX - r.left) * k - (W / 2 + ox), py = (ev.clientY - r.top) * k - (H / 2 + oy); const rot = -(tf.rotation || 0) * Math.PI / 180; const lx = px * Math.cos(rot) - py * Math.sin(rot), ly = px * Math.sin(rot) + py * Math.cos(rot); const tol = W / 40;
  for (const [hx, hy] of [[-dw / 2, -dh / 2], [dw / 2, -dh / 2], [-dw / 2, dh / 2], [dw / 2, dh / 2]]) if (Math.hypot(lx - hx, ly - hy) < tol) return { kind: "scale", c, tr, sc, dw, dh, ox, oy, k, W, H, cx: r.left + (W / 2 + ox) / k, cy: r.top + (H / 2 + oy) / k, fit, m };
  if (Math.hypot(lx, ly - (-dh / 2 - W / 30)) < tol) return { kind: "rotate", c, tr, cx: r.left + (W / 2 + ox) / k, cy: r.top + (H / 2 + oy) / k, rot0: tf.rotation || 0 };
  return null; }
function revealInProject(c) { if (!c.media_id) return; S.binSel = new Set([c.media_id]); S.binQuery = ""; CR.showTab("project"); renderBin(); const el = document.querySelector(`.media[data-id="${c.media_id}"]`); if (el) el.scrollIntoView({ block: "center" }); api.get("/api/media/usage?media_id=" + c.media_id).then(u => status(`${S.proj.media[c.media_id].name}: used ${u.length} time(s) across ${new Set(u.map(x => x.sequence)).size} sequence(s)`)); }
function canvasDrag(ev) { if (!canEdit() || ev.button != null && ev.button !== 0) return;
  if (S.multiView && S.mvGrid) { const grid = S.mvGrid, cv = $("#prgCanvas"), r = cv.getBoundingClientRect(); const col = Math.floor((ev.clientX - r.left) / r.width * grid.cols), row = Math.floor((ev.clientY - r.top) / r.height * grid.rows); const i = row * grid.cols + col; if (col >= 0 && col < grid.cols && row >= 0 && row < grid.rows && i < grid.n) switchAngle(i, grid); return; }
  const hit = S.tool === "select" ? monitorHit(ev) : null;
  if (S.tool === "line") { const cv = $("#prgCanvas"), r = cv.getBoundingClientRect(); const p0 = [(ev.clientX - r.left) / r.width, (ev.clientY - r.top) / r.height]; let last = p0; const move = e => { last = [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height]; S.lineDraft = [p0, last]; renderProgram(); }; const up = e => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); S.lineDraft = null; if (Math.hypot(last[0] - p0[0], last[1] - p0[1]) < 0.01) { renderProgram(); return; } const tr = S.seq.tracks.filter(x => x.kind === "video").sort((a, b) => b.index - a.index)[0]; const c = { id: uid(), media_id: null, start: S.t, in_: 0, out: 3.0, speed: 1, graphic: { name: e.shiftKey ? "Arrow" : "Line", layers: [{ kind: "shape", shape: e.shiftKey ? "arrow" : "line", x1: +p0[0].toFixed(4), y1: +p0[1].toFixed(4), x2: +last[0].toFixed(4), y2: +last[1].toFixed(4), x: Math.min(p0[0], last[0]), y: Math.min(p0[1], last[1]), w: Math.abs(last[0] - p0[0]) || 0.01, h: Math.abs(last[1] - p0[1]) || 0.01, stroke: 10, head: 44, color: "#E8631C", anim_in: { type: "wipe_left", duration: 0.4 } }] }, transform: { opacity: 1 }, keyframes: {} }; applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: c }], "shape", e.shiftKey ? "arrow" : "line"); S.sel = new Set([c.id]); refreshSel(); CR.panels.render(); }; watchEditGesture(ev, move, up); return; }
  if (hit && hit.kind === "scale") { const { c, tr, sc, cx, cy } = hit; const rollback = captureGestureFields(c, ["transform", "keyframes"]); const d0 = Math.max(4, Math.hypot(ev.clientX - cx, ev.clientY - cy)); const kf = c.keyframes || {}; let moved = false; const move = e => { moved = true; const ns = Math.max(0.02, +(sc * Math.hypot(e.clientX - cx, e.clientY - cy) / d0).toFixed(3)); c.transform = { ...(c.transform || {}), scale: ns }; if (kf["transform.scale"]?.length) c.keyframes = keyframesWithValue(kf, "transform.scale", S.t - c.start, ns); status(`scale ${Math.round(ns * 100)}%`); renderProgram(); }; const up = () => { if (!moved) { rollback(); return; } window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); if (kf["transform.scale"] && kf["transform.scale"].length) { const value = c.transform.scale; rollback(); setKeyframeValue(c, tr, "transform.scale", S.t - c.start, value); return; } applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, transform: c.transform } }], "monitor_scale", `scale ${Math.round(c.transform.scale * 100)}%`); }; watchEditGesture(ev, move, up, rollback); return; }
  if (hit && hit.kind === "rotate") { const { c, tr, cx, cy, rot0 } = hit; const rollback = captureGestureFields(c, ["transform"]); const a0 = Math.atan2(ev.clientY - cy, ev.clientX - cx); let moved = false; const move = e => { moved = true; let deg = rot0 + (Math.atan2(e.clientY - cy, e.clientX - cx) - a0) * 180 / Math.PI; if (e.shiftKey) deg = Math.round(deg / 15) * 15; c.transform = { ...(c.transform || {}), rotation: +deg.toFixed(1) }; status(`rotation ${c.transform.rotation}°`); renderProgram(); }; const up = () => { if (!moved) { rollback(); return; } window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, transform: c.transform } }], "monitor_rotate", `rotate ${c.transform.rotation}°`); }; watchEditGesture(ev, move, up, rollback); return; }
  if (S.tool === "polygon") { const cv = $("#prgCanvas"), r = cv.getBoundingClientRect(); const fx = (ev.clientX - r.left) / r.width, fy = (ev.clientY - r.top) / r.height; if (!S.polyDraft) S.polyDraft = []; S.polyDraft.push([+fx.toFixed(4), +fy.toFixed(4)]); if (S.polyDraft.length >= 3 && ev.detail >= 2) finishPolygon(); renderProgram(); return; }
  if (S.tool === "rect" || S.tool === "ellipse") { const cv = $("#prgCanvas"), r = cv.getBoundingClientRect(); const fx0 = (ev.clientX - r.left) / r.width, fy0 = (ev.clientY - r.top) / r.height; const tr = S.seq.tracks.filter(t => t.kind === "video").sort((a, b) => b.index - a.index)[0]; const L = { kind: "shape", shape: S.tool === "rect" ? "rect" : "ellipse", x: fx0, y: fy0, w: 0.01, h: 0.01, color: "#E8631C", opacity: 0.9, radius: 0 };
    const selection = [...S.sel]; const c = { id: uid(), media_id: null, start: S.t, in_: 0, out: 4.0, speed: 1, graphic: { name: S.tool === "rect" ? "Rectangle" : "Ellipse", layers: [L] }, transform: { opacity: 1 }, keyframes: {} }; applyLocal([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: c }]);
    const rollback = () => { tr.clips = tr.clips.filter(x => x !== c); S.sel = new Set(selection); };
    const move = e => { const fx = (e.clientX - r.left) / r.width, fy = (e.clientY - r.top) / r.height; L.x = Math.min(fx0, fx); L.y = Math.min(fy0, fy); L.w = Math.max(0.01, Math.abs(fx - fx0)); L.h = Math.max(0.01, Math.abs(fy - fy0)); renderProgram(); };
    const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); S.sel = new Set([c.id]); applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: c }], "shape", `${L.shape} shape`); CR.showTab("ec"); }; watchEditGesture(ev, move, up, rollback); return; }
  if (S.tool === "type") { const hitText = textClipAt(); if (hitText) { editTextInline(hitText.c, hitText.tr, ev); return; } const cv = $("#prgCanvas"), r = cv.getBoundingClientRect(), k = cv.width / r.width; const x = Math.round((ev.clientX - r.left) * k - cv.width / 2), y = Math.round((ev.clientY - r.top) * k - cv.height / 2); const tr = S.seq.tracks.filter(t => t.kind === "video").sort((a, b) => b.index - a.index)[0];
    const c = { id: uid(), media_id: null, start: S.t, in_: 0, out: 3.0, speed: 1, title: { text: "Type here", size: Math.round(S.seq.height * 0.05), color: "white", x, y, align: "center", valign: "center" }, transform: { opacity: 1 }, transition_in: null, transition_out: null, keyframes: {} }; applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: c }], "type", "type tool text"); S.sel = new Set([c.id]); refreshSel(); CR.panels.render(); CR.showTab("ec"); setTimeout(() => { const ta = document.querySelector('[data-k="title.text"]'); if (ta) { ta.focus(); ta.select(); } }, 50); return; }
  const sel = selectedClips().filter(x => x.tr.kind === "video" && S.t >= x.c.start && S.t < clipEnd(x.c)); if (sel.length !== 1) return; const { c, tr } = sel[0]; const cv = $("#prgCanvas"), r = cv.getBoundingClientRect(), k = cv.width / r.width; const rollback = captureGestureFields(c, ["transform", "keyframes"]); const tf0 = deep(c.transform || {}); const kf = c.keyframes || {}; const rel = S.t - c.start; const x0 = ev.clientX, y0 = ev.clientY;
  let moved = false; const move = e => { moved = true; const dx = (e.clientX - x0) * k, dy = (e.clientY - y0) * k; let nx = (kf["transform.x"] && kf["transform.x"].length ? kfVal(kf["transform.x"], rel, 0) : (tf0.x || 0)) + dx, ny = (kf["transform.y"] && kf["transform.y"].length ? kfVal(kf["transform.y"], rel, 0) : (tf0.y || 0)) + dy; [nx, ny] = snapToGuides(nx, ny); c.transform = { ...(c.transform || {}), x: nx, y: ny }; if (c.keyframes) c.keyframes = kf; for (const [key, value] of [["transform.x", nx], ["transform.y", ny]]) if (kf[key]?.length) c.keyframes = keyframesWithValue(c.keyframes, key, rel, value); renderProgram(); };
  const up = () => { if (!moved) { rollback(); return; } window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); const nx = c.transform.x, ny = c.transform.y; const patch = { id: c.id };
    if ((kf["transform.x"] && kf["transform.x"].length) || (kf["transform.y"] && kf["transform.y"].length)) { let nk = kf; for (const [key, v] of [["transform.x", nx], ["transform.y", ny]]) nk = keyframesWithValue(nk, key, rel, v); patch.keyframes = nk; rollback(); }
    else patch.transform = { ...(c.transform || {}), x: nx, y: ny }; applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: patch }], "monitor_drag", "move clip in monitor"); };
  watchEditGesture(ev, move, up, rollback); }
function canvasWheel(e) { if (!e.altKey) return; e.preventDefault(); const sel = selectedClips().filter(x => x.tr.kind === "video" && S.t >= x.c.start && S.t < clipEnd(x.c)); if (sel.length !== 1) return; const { c, tr } = sel[0]; const sc = Math.max(0.05, ((c.transform || {}).scale == null ? 1 : c.transform.scale) * (e.deltaY < 0 ? 1.05 : 0.95)); applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, transform: { ...(c.transform || {}), scale: +sc.toFixed(3) } } }], "monitor_wheel", "scale clip in monitor"); }
function resizers() { const app = $("#app"), g = $(".gutter"); g.onmousedown = ev => { const y0 = ev.clientY, rows = getComputedStyle(app).gridTemplateRows.split(" "), h0 = parseFloat(rows[3]); const move = e => { app.style.gridTemplateRows = `34px 1fr 6px ${Math.max(160, h0 - (e.clientY - y0))}px 22px`; renderProgram(); }; const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); }; window.addEventListener("mousemove", move); window.addEventListener("mouseup", up); };
  const rp = $("#rightTop"); if (rp) { const vs = document.createElement("div"); vs.className = "vsplit"; rp.appendChild(vs); vs.onmousedown = ev => { const x0 = ev.clientX, w0 = rp.getBoundingClientRect().width; const move = e => { const w = Math.max(260, w0 - (e.clientX - x0)); $("#rowTop").style.gridTemplateColumns = `1fr 1fr ${w}px`; $("#rowBottom").style.gridTemplateColumns = `${$("#projectPanel").getBoundingClientRect().width}px 62px 1fr ${w}px`; renderProgram(); }; const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); CR.saveLayout && CR.saveLayout(); }; window.addEventListener("mousemove", move); window.addEventListener("mouseup", up); }; }
  const pp = $("#projectPanel"); const ps = document.createElement("div"); ps.className = "vsplit"; ps.style.left = ""; ps.style.right = "-3px"; pp.appendChild(ps); ps.onmousedown = ev => { const x0 = ev.clientX, w0 = pp.getBoundingClientRect().width; const move = e => { const cols = getComputedStyle($("#rowBottom")).gridTemplateColumns.split(" "); cols[0] = `${Math.max(240, w0 + (e.clientX - x0))}px`; $("#rowBottom").style.gridTemplateColumns = cols.join(" "); }; const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); CR.saveLayout && CR.saveLayout(); }; window.addEventListener("mousemove", move); window.addEventListener("mouseup", up); };
  const sp = $("#srcPanel"); const ss = document.createElement("div"); ss.className = "vsplit"; ss.style.left = ""; ss.style.right = "-3px"; sp.appendChild(ss); ss.onmousedown = ev => { const x0 = ev.clientX, w0 = sp.getBoundingClientRect().width, total = $("#rowTop").getBoundingClientRect().width; const move = e => { const cols = getComputedStyle($("#rowTop")).gridTemplateColumns.split(" "); const w = Math.max(280, w0 + (e.clientX - x0)); cols[0] = `${w}px`; cols[1] = "1fr"; $("#rowTop").style.gridTemplateColumns = cols.join(" "); renderProgram(); }; const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); CR.saveLayout && CR.saveLayout(); }; window.addEventListener("mousemove", move); window.addEventListener("mouseup", up); }; }
function addFrameHold() { const sel = selectedClips().filter(x => x.c.media_id && !x.c.hold); if (sel.length !== 1) { status("Select one clip to hold."); return; } const { c, tr } = sel[0]; const d = clipDur(c); const src = S.t > c.start && S.t < clipEnd(c) ? c.in_ + sourceOffset(c, S.t - c.start) : c.in_;
  applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, hold: true, in_: +src.toFixed(4), out: +(src + d).toFixed(4), speed: 1, time_remap: null } }], "frame_hold", "add frame hold"); }
function keyboardEditReady() {
  if (!canEdit()) return false;
  if (projectSaveState().error) { status("Resolve unsaved edits in Recovery before trimming or nudging.", "err"); return false; }
  return true;
}
function keyboardSourceOptions(c) {
  if (c.media_id) { const media = S.proj.media[c.media_id]; return { still: !!media?.is_image, sourceLimit: media?.is_image ? Infinity : Number.isFinite(media?.duration) && media.duration >= 0 ? media.duration : c.out }; }
  if (c.sequence_id) { const sequence = S.proj.sequences.find(sq => sq.id === c.sequence_id); return { sourceLimit: sequence ? seqDurOf(sequence) : c.out }; }
  return { sourceLimit: Infinity };
}
function keyboardTrimClip(c, tr, begin, end, start = c.start + begin) {
  const minimum = tr.kind === "audio" ? 1 / 48000 : timing.fromFrames(1, S.seq.fps || 30);
  if (!Number.isFinite(start) || start < 0 || end - begin < minimum - 1e-10) throw Error("Keep at least one video frame or one audio sample, and do not trim before sequence start.");
  return window.FilmocityClipSplit.trim(c, begin, end, { duration: clipDur(c), sourceOffset: t => sourceOffset(c, t), speedAt: t => speedAt(c, t) }, { ...keyboardSourceOptions(c), start });
}
function keyboardEditTarget(c, tr, side, frames) {
  if (!Number.isSafeInteger(frames) || !frames) return null;
  const fps = S.seq.fps || 30, edge = side === "l" ? c.start : clipEnd(c);
  return tr.kind === "video" ? timing.fromFrames(timing.toFrames(edge, fps) + frames, fps) : edge + timing.fromFrames(frames, fps);
}
function keyboardTrimPlan(c, tr, side, target, mode) {
  if (tr.locked) throw Error("Unlock the track before trimming.");
  if (!["l", "r"].includes(side) || !Number.isFinite(target)) throw Error("Choose a valid edit point.");
  const duration = clipDur(c), edge = side === "l" ? c.start : clipEnd(c), delta = target - edge;
  if (Math.abs(delta) <= Math.max(1e-10, Math.abs(edge) * Number.EPSILON * 8)) return null;
  const sorted = [...tr.clips].sort((a, b) => a.start - b.start), index = sorted.indexOf(c), previous = sorted[index - 1], next = sorted[index + 1];
  const ops = [], push = clip => ops.push({ op: "set_clip", sequence: S.seq.id, track: tr.id, clip });
  if (mode === "roll") {
    const neighbor = side === "l" ? previous : next, boundary = side === "l" ? neighbor && clipEnd(neighbor) : neighbor?.start;
    if (!neighbor || Math.abs(boundary - edge) > 1e-7) throw Error("Rolling requires two adjoining clips. Use a regular trim at a gap or sequence edge.");
    if (side === "l") { push(keyboardTrimClip(c, tr, target - c.start, duration, target)); push(keyboardTrimClip(neighbor, tr, 0, target - neighbor.start, neighbor.start)); }
    else { push(keyboardTrimClip(c, tr, 0, target - c.start, c.start)); push(keyboardTrimClip(neighbor, tr, target - neighbor.start, clipDur(neighbor), target)); }
    return { ops, edge: target };
  }
  if (mode !== "ripple") {
    if (side === "l" && previous && target < clipEnd(previous) - 1e-10 || side === "r" && next && target > next.start + 1e-10) throw Error("The neighboring clip limits this trim. Use a rolling or ripple trim to move that edit.");
    push(side === "l" ? keyboardTrimClip(c, tr, target - c.start, duration, target) : keyboardTrimClip(c, tr, 0, target - c.start, c.start));
    return { ops, edge: target };
  }
  push(side === "l" ? keyboardTrimClip(c, tr, target - c.start, duration, c.start) : keyboardTrimClip(c, tr, 0, target - c.start, c.start));
  const shift = side === "l" ? -delta : delta;
  for (const other of tr.clips) if (other !== c && other.start >= clipEnd(c) - 1e-7) {
    const start = other.start + shift; if (!Number.isFinite(start) || start < 0) throw Error("This ripple trim would move a clip before sequence start.");
    push({ id: other.id, start });
  }
  return { ops, edge: side === "l" ? c.start : target };
}
function applyKeyboardTrim(c, tr, side, target, mode, tool, reason) {
  try { const plan = keyboardTrimPlan(c, tr, side, target, mode); if (!plan) return false; const pending = applyOps(plan.ops, tool, reason); seekTo(plan.edge); return pending; }
  catch (error) { status(error.message, "err"); return false; }
}
function extendEdit() {
  if (!keyboardEditReady()) return false;
  const sel = selectedClips(); if (sel.length !== 1) { status("Select one clip to extend or trim to the playhead."); return false; }
  const { c, tr } = sel[0], ep = S.editPoint?.clipId === c.id ? S.editPoint : null;
  const target = tr.kind === "video" ? timing.fromFrames(timing.toFrames(S.t, S.seq.fps || 30), S.seq.fps || 30) : S.t;
  const side = ep?.side || (target < c.start ? "l" : target > clipEnd(c) ? "r" : target - c.start < clipEnd(c) - target ? "l" : "r");
  return applyKeyboardTrim(c, tr, side, target, "regular", "extend", "extend edit to playhead");
}
function zoomToFit() { const body = $("#tlBody"); S.pps = Math.max(6, Math.min(600, (body.clientWidth - 40) / Math.max(seqDur(), 1))); renderTimeline(); body.scrollLeft = 0; }
let sourceRelinkOwner = null;
function sourceRelinkSelection() {
  return JSON.stringify({ bin: [...(S.binSel || [])].sort(), clips: [...(S.sel || [])].sort(), source: S.src?.id || null });
}
function sourceRelinkSignature(owner) {
  const media = owner.project.media, ids = new Set([owner.mediaId, owner.physicalId]);
  for (const [id, value] of Object.entries(media)) if (value.subclip_of === owner.physicalId || value.audio_alias?.physical_media_id === owner.physicalId) ids.add(id);
  return JSON.stringify([owner.project.sequences, [...ids].sort().map(id => [id, media[id]])]);
}
function sourceRelinkCurrent(owner, inactive = false) {
  return !!owner && (inactive || sourceRelinkOwner === owner && !owner.cancelled) && S.proj === owner.project && S.seq === owner.sequence &&
    window.FilmocitySync.sameProject(owner.context, S.context) && owner.selection === sourceRelinkSelection() && owner.signature === sourceRelinkSignature(owner);
}
function currentSourceRelink() { return sourceRelinkOwner; }
function cancelSourceRelink(owner = sourceRelinkOwner, message = "Relink canceled. Any already submitted change still requires its saved outcome to be checked.") {
  if (!owner || sourceRelinkOwner !== owner) return false;
  owner.cancelled = true; owner.generation++; sourceRelinkOwner = null; S.relinkTarget = null;
  window.removeEventListener("keydown", owner.escape, true);
  CR.panels?.retireRelink?.(owner);
  if (message && window.FilmocitySync.sameProject(owner.context, S.context)) status(message);
  return true;
}
function beginSourceRelink(mediaId, options = {}) {
  try {
    if (!canEdit() || projectSaveState().error || !window.FilmocitySync.validContext(S.context) || !S.proj?.sequences?.includes(S.seq)) throw Error("Wait for saved project controls, or resolve Recovery, before relinking.");
    if (options.current && !options.current()) throw Error("This source control is no longer current. Choose its current Relink control.");
    const media = S.proj.media[mediaId];
    if (!media || media.synthetic || media.sequence_frames) throw Error("Choose one original file source to relink.");
    const physicalId = media.audio_alias?.physical_media_id || media.subclip_of || mediaId, physical = S.proj.media[physicalId];
    if (!physical || physical.subclip_of || physical.synthetic || physical.sequence_frames) throw Error("The source parent is missing or unsupported. Repair the source reference before relinking.");
    cancelSourceRelink(sourceRelinkOwner, "");
    const owner = { project: S.proj, sequence: S.seq, context: { ...S.context }, mediaId, physicalId,
      name: media.name || mediaId, physicalName: physical.name || physicalId, originalPath: physical.path || "", selection: sourceRelinkSelection(),
      cancelled: false, pending: false, submitted: false, generation: 0, review: null };
    owner.signature = sourceRelinkSignature(owner);
    owner.escape = event => { if (event.key === "Escape") { event.preventDefault(); cancelSourceRelink(owner); } };
    sourceRelinkOwner = owner; S.relinkTarget = mediaId; window.addEventListener("keydown", owner.escape, true);
    status(`Choose a replacement original for "${owner.name}" in the Media Browser. Review is required before Relink; Escape cancels.`);
    CR.panels?.browseRelink?.(owner);
    return owner;
  } catch (error) { status(error.message || String(error), "err"); return null; }
}
async function inspectSourceRelink(owner, path, options = {}) {
  if (!sourceRelinkCurrent(owner) || owner.pending || !canEdit()) throw Error("The relink target changed or another action is pending. Start Relink again.");
  if (typeof path !== "string" || !path.trim() || path.length > 4096 || /[\u0000-\u001f]/.test(path)) throw Error("Choose a replacement file path of at most 4096 characters without control characters.");
  const generation = ++owner.generation, current = () => generation === owner.generation && sourceRelinkCurrent(owner) && (!options.current || options.current());
  owner.pending = true; owner.review = null;
  try {
    const state = await flushSaves();
    if (state !== projectSaveState() || state.error || !current() || !canEdit()) throw Error("The project, source, selection or controls changed before inspection. Start Relink again.");
    const context = { ...state.context };
    const report = await api.json("POST", "/api/media/relink/inspect", { media_id: owner.mediaId, path, _context: context, actor: "human", client: CLIENT });
    if (!current() || state !== projectSaveState() || state.pending || state.error || S.context.revision !== context.revision) throw Error("The project or source changed while inspection ran. Inspect the current selection again.");
    if (typeof report?.ok !== "boolean" || report.requested_media_id !== owner.mediaId || report.media_id !== owner.physicalId ||
      !window.FilmocitySync.validContext(report.context) || !window.FilmocitySync.sameProject(context, report.context) || report.context.revision !== context.revision ||
      typeof report.path !== "string" || !report.path || !/^[a-f0-9]{64}$/.test(report.fingerprint || "") || !report.info || typeof report.info !== "object" ||
      !report.summary || typeof report.summary !== "object" || !Array.isArray(report.issues) || report.issues.some(issue => !issue || typeof issue.message !== "string" || !["error", "warning", "info"].includes(issue.severity))) {
      throw Error("Inspection did not confirm this saved source and replacement. Inspect again before applying.");
    }
    owner.review = { report, snapshot: JSON.stringify(report), generation };
    return report;
  } finally { owner.pending = false; }
}
function sourceRelinkReviewCurrent(owner, report) {
  const state = projectSaveState();
  return sourceRelinkCurrent(owner) && owner.review?.report === report && owner.review.snapshot === JSON.stringify(report) &&
    owner.review.generation === owner.generation && report.ok === true && !report.issues.some(issue => issue.severity === "error") &&
    !state.pending && !state.error && S.context.revision === report.context.revision && state.context.revision === report.context.revision;
}
async function applySourceRelink(owner, report, options = {}) {
  if (!sourceRelinkReviewCurrent(owner, report) || owner.pending || !canEdit() || options.current && !options.current()) throw Error("The inspected source, file or saved revision changed. Inspect again before relinking.");
  const generation = owner.generation; owner.pending = true;
  try {
    if (S.playing) togglePlay(false, { commitTrim: false });
    const result = await workflowRequest(async context => {
      if (!sourceRelinkReviewCurrent(owner, report) || owner.generation !== generation || options.current && !options.current()) {
        const error = Error("Relink was canceled or its inspected source changed before submission."); error.status = 409; throw error;
      }
      owner.submitted = true;
      const result = await api.json("POST", "/api/media/relink", { media_id: owner.mediaId, path: report.path, _context: context, fingerprint: report.fingerprint, actor: "human", client: CLIENT });
      if (S.proj !== owner.project || S.seq !== owner.sequence || !window.FilmocitySync.sameProject(owner.context, S.context)) throw Error("The original project or sequence changed while Relink was submitted. Review its saved outcome before continuing.");
      if (result?.ok !== true || result.media_id !== owner.physicalId || typeof result.changed !== "boolean" || !result.summary || !Array.isArray(result.warnings)) throw Error("Relink did not confirm the inspected source change.");
      return result;
    }, { ...report.context, sequence: owner.sequence.id });
    cancelSourceRelink(owner, "");
    if (window.FilmocitySync.sameProject(owner.context, S.context) && S.seq?.id === owner.sequence.id) status([result.summary?.message || "Source relink saved.", ...result.warnings,
      ...(result.preparation?.warning ? [result.preparation.warning] : [])].join(" "));
    return result;
  } catch (error) { if ([400, 401, 403, 404, 409, 422, 501].includes(error.status)) owner.submitted = false; throw error; }
  finally { owner.pending = false; }
}
let editorialCommandPending = null;
function editorialCommandOwner() {
  if (!canEdit() || !window.FilmocitySync.validContext(S.context)) throw Error("Wait for the current project action to finish.");
  if (projectSaveState().error) throw Error("Resolve unsaved edits in Recovery before continuing.");
  if (!S.proj?.sequences?.includes(S.seq)) throw Error("Choose a current sequence first.");
  return { project: S.proj, sequence: S.seq, context: { ...S.context } };
}
function editorialOwnerCurrent(owner) {
  return S.proj === owner.project && S.seq === owner.sequence && window.FilmocitySync.sameProject(owner.context, S.context);
}
function editorialSourceChain(media) {
  const seen = new Set(), entries = [];
  while (media) {
    if (!media.id || S.proj.media[media.id] !== media || seen.has(media.id) || entries.length >= 32) throw Error("The selected source or its subclip parent is missing or stale.");
    seen.add(media.id); entries.push({ id: media.id, media, snapshot: JSON.stringify(media) });
    if (!media.subclip_of) return entries;
    media = S.proj.media[media.subclip_of];
  }
  throw Error("The selected source or its subclip parent is missing.");
}
async function runEditorialCommand(owner, path, body, current, options = {}) {
  if (editorialCommandPending) throw Error("Wait for the current editorial command to finish.");
  const ticket = { submitted: false, cancelled: false }; editorialCommandPending = ticket;
  const escape = event => { if (event.key === "Escape" && !ticket.submitted) ticket.cancelled = true; };
  window.addEventListener("keydown", escape, true);
  const valid = () => !ticket.cancelled && editorialOwnerCurrent(owner) && current();
  try {
    const state = await flushSaves();
    if (ticket.cancelled) throw Error("Command canceled before submission.");
    if (state !== projectSaveState() || state.error || !valid() || !canEdit()) throw Error("The selected item, edit or project changed. Select it again before continuing.");
    if (S.playing) togglePlay(false, { commitTrim: false });
    const result = await workflowRequest(async context => {
      if (!valid()) { const error = Error("The command was canceled or its selected item changed before submission."); error.status = 409; throw error; }
      ticket.submitted = true;
      const reply = await api.json("POST", path, { ...body, _context: context, actor: "human", client: CLIENT });
      if (!editorialOwnerCurrent(owner)) throw Error("The original project or sequence changed while the command was submitted. Review its saved outcome before continuing.");
      if (options.validateReply) options.validateReply(reply);
      return reply;
    }, { ...state.context, sequence: owner.sequence.id });
    if (window.FilmocitySync.sameProject(owner.context, S.context) && S.seq?.id === owner.sequence.id) {
      const warnings = Array.isArray(result.warnings) ? result.warnings.filter(value => typeof value === "string") : [];
      status([result.summary?.message || (result.changed === false ? "No change was needed." : "Editorial change saved."), ...warnings].join(" "));
    }
    return result;
  } finally {
    window.removeEventListener("keydown", escape, true);
    if (editorialCommandPending === ticket) editorialCommandPending = null;
  }
}
async function splitGraphicWords(c, tr, layer, options = {}) {
  let owner;
  try {
    owner = editorialCommandOwner();
    if (!owner.sequence.tracks.includes(tr) || !tr.clips.includes(c) || tr.locked || !S.sel.has(c.id)) throw Error("Select a graphic on an unlocked current track before splitting its words.");
    if (!Number.isSafeInteger(layer) || layer < 0 || c.graphic?.layers?.[layer]?.kind !== "text") throw Error("Choose an existing graphic text layer.");
    const selection = JSON.stringify([...S.sel].sort()), snapshot = JSON.stringify(owner.sequence);
    const current = () => (!options.current || options.current()) && !tr.locked && owner.sequence.tracks.includes(tr) && tr.clips.includes(c) &&
      JSON.stringify([...S.sel].sort()) === selection && JSON.stringify(owner.sequence) === snapshot;
    if (!current()) throw Error("The graphic controls changed. Reopen the selected graphic.");
    return await runEditorialCommand(owner, "/api/graphics/split_words", { sequence: owner.sequence.id, clip_id: c.id, layer,
      anim: { type: "pop", duration: .35, ease: "back_out" }, stagger: .12 }, current);
  } catch (error) { if (!owner || window.FilmocitySync.sameProject(owner.context, S.context)) status(error.message || String(error), "err"); return false; }
}
async function extractAudioSource() {
  let owner;
  try {
    owner = editorialCommandOwner();
    const ids = [...(S.binSel || [])], media = ids.length === 1 ? owner.project.media[ids[0]] : null;
    if (!media?.has_audio) throw Error("Select exactly one source with audio in the Project panel.");
    const chain = editorialSourceChain(media), selected = JSON.stringify(ids);
    const current = () => JSON.stringify([...(S.binSel || [])]) === selected && chain.every(item => owner.project.media[item.id] === item.media && JSON.stringify(item.media) === item.snapshot);
    return await runEditorialCommand(owner, "/api/media/extract_audio", { media_id: media.id }, current);
  } catch (error) { if (!owner || window.FilmocitySync.sameProject(owner.context, S.context)) status(error.message || String(error), "err"); return false; }
}
function sourceCreationReply(reply, mode, count, existingIds, owner, sourceIds) {
  const bad = () => { throw Error("The server did not confirm the created source items. Review the saved project in Recovery before repeating this command."); };
  if (reply?.ok !== true || reply.kind !== "source_creation" || reply.mode !== mode || typeof reply.changed !== "boolean" ||
      !window.FilmocitySync.validContext(reply.context) || !window.FilmocitySync.sameProject(owner, reply.context) || reply.project !== owner.project ||
      !Array.isArray(reply.media_ids) || !Array.isArray(reply.media) || reply.media.length !== reply.media_ids.length ||
      !reply.summary || reply.summary.kind !== "source_creation" || reply.summary.mode !== mode || reply.summary.changed !== reply.changed ||
      JSON.stringify(reply.summary.source_media_ids) !== JSON.stringify(sourceIds) || JSON.stringify(reply.summary.created_media_ids) !== JSON.stringify(reply.media_ids) || reply.summary.count !== reply.media_ids.length ||
      typeof reply.summary.message !== "string" || !Array.isArray(reply.warnings) || reply.warnings.some(value => typeof value !== "string") ||
      !reply.preparation || !Array.isArray(reply.preparation.tasks) || !Array.isArray(reply.preparation.warnings) || reply.preparation.warnings.some(value => typeof value !== "string")) bad();
  const ids = reply.media_ids;
  if (ids.length !== (reply.changed ? count : 0) || new Set(ids).size !== ids.length ||
      ids.some((id, i) => typeof id !== "string" || !id || existingIds.has(id) || reply.media[i]?.id !== id)) bad();
  return reply;
}
async function createSourceItems(mode, options = {}) {
  let owner;
  try {
    owner = editorialCommandOwner();
    if (editorialCommandPending) throw Error("Wait for the current editorial command to finish.");
    const selected = JSON.stringify([...(S.binSel || [])]), src = S.src, marks = JSON.stringify([S.srcIn, S.srcOut]);
    const ids = mode === "subclip" ? [src?.id] : [...(S.binSel || [])];
    if (mode === "duplicate" ? !ids.length || ids.length > 50 : ids.length !== 1) throw Error(mode === "duplicate" ? "Select 1–50 source items in the Project panel." : "Select exactly one source in the Project panel.");
    const media = ids.map(id => owner.project.media[id]);
    if (media.some(value => !value)) throw Error(mode === "subclip" ? "Load a current source and mark its In/Out first." : "Select current source items in the Project panel.");
    if (mode === "subclip" && media[0] !== src) throw Error("The Source monitor changed. Load the source again.");
    const chain = [...new Map(media.flatMap(editorialSourceChain).map(item => [item.id, item])).values()];
    const current = () => editorialOwnerCurrent(owner) && (!options.current || options.current()) && JSON.stringify([...(S.binSel || [])]) === selected &&
      (mode !== "subclip" || S.src === src && JSON.stringify([S.srcIn, S.srcOut]) === marks) &&
      chain.every(item => owner.project.media[item.id] === item.media && JSON.stringify(item.media) === item.snapshot);
    let body, count;
    if (mode === "subclip") {
      const begin = S.srcIn ?? 0, end = S.srcOut ?? src.duration;
      if (!Number.isFinite(begin) || !Number.isFinite(end) || !Number.isFinite(src.duration) || begin < 0 || end <= begin || end > src.duration) throw Error("Mark a finite In/Out range inside the selected source; Out must follow In.");
      const name = prompt("Subclip name — marked range within " + (src.name || "selected source"), String(src.name || "Source").replace(/\.[^.]+$/, "") + ".sub");
      if (name === null) return false;
      if (typeof name !== "string" || !name.trim() || [...name.trim()].length > 256 || /[\u0000-\u001f]/.test(name)) throw Error("Enter a subclip name from 1 to 256 characters without control characters.");
      body = { media_id: src.id, in: begin, out: end, name: name.trim() }; count = 1;
    } else if (mode === "breakout") {
      const item = media[0], physical = chain.at(-1).media;
      if (FilmocityProxyPreview.channelAlias(item)) throw Error("This item already selects one channel. Choose its original source for Breakout.");
      if (!physical.has_audio || !Number.isSafeInteger(physical.channels) || physical.channels < 1 || physical.channels > 32) throw Error("Select one source with 1–32 measured audio channels. Refresh its media metadata if channels are unknown.");
      body = { media_id: item.id }; count = physical.channels;
    } else if (mode === "duplicate") { body = { media_ids: ids }; count = ids.length; }
    else throw Error("Unknown source creation command.");
    if (!current()) throw Error("The selected source, marks or controls changed. Start this command again.");
    const existingIds = new Set(Object.keys(owner.project.media));
    status(mode === "subclip" ? "Saving the marked subclip… Escape cancels before submission." : mode === "breakout" ? "Saving selected-channel source items… Escape cancels before submission." : "Saving duplicate source items… Escape cancels before submission.");
    const result = await runEditorialCommand(owner, "/api/media/" + mode, body, current, { validateReply: reply => sourceCreationReply(reply, mode, count, existingIds, owner.context, ids) });
    // Acknowledgement replaces project objects. Keep newer user selection and the loaded Source/marks.
    if (mode === "duplicate" && result.changed && window.FilmocitySync.sameProject(owner.context, S.context) && S.seq?.id === owner.sequence.id &&
        JSON.stringify([...(S.binSel || [])]) === selected && result.media_ids.every(id => S.proj.media[id])) {
      S.binSel = new Set(result.media_ids); renderBin(); CR.panels?.renderMeta?.();
    }
    return result;
  } catch (error) { if (!owner || window.FilmocitySync.sameProject(owner.context, S.context)) status(error.message || String(error), "err"); return false; }
}
function duplicateBinSources() { return createSourceItems("duplicate"); }
function breakoutAudioSource() { return createSourceItems("breakout"); }
async function requestSequenceDescription(options = {}) {
  const project = S.proj, sequence = S.seq, origin = { ...S.context };
  if (!project?.sequences?.includes(sequence) || !window.FilmocitySync.validContext(origin)) throw Error("Wait for the sequence to load before reading its summary.");
  const snapshot = JSON.stringify([sequence, project.media]);
  const current = () => (!options.current || options.current()) && S.proj === project && S.seq === sequence &&
    window.FilmocitySync.sameProject(origin, S.context) && JSON.stringify([sequence, project.media]) === snapshot;
  const state = await flushSaves();
  if (state !== projectSaveState() || state.error || !current()) throw Error("The sequence changed or has unsaved edits. Refresh its summary after saving.");
  const context = { ...state.context };
  const query = Object.entries({ sequence: sequence.id, ...context }).map(([key, value]) => encodeURIComponent(key) + "=" + encodeURIComponent(value)).join("&");
  const result = await api.get("/api/sequence/describe?" + query);
  if (!current() || S.context.revision !== context.revision || state !== projectSaveState() || state.pending || state.error) throw Error("The sequence changed while its summary loaded. Refresh the summary.");
  if (result?.sequence !== sequence.id || typeof result.text !== "string" || !window.FilmocitySync.validContext(result.context) ||
      !window.FilmocitySync.sameProject(context, result.context) || result.context.revision !== context.revision) throw Error("The summary did not confirm the selected saved sequence.");
  return result;
}
function sourceReplacementMediaChain(media) {
  const entries = [], seen = new Set();
  while (media) {
    if (!media.id || S.proj.media[media.id] !== media || seen.has(media.id) || entries.length >= 32) throw Error("The replacement media source is missing, stale or cyclic.");
    seen.add(media.id); entries.push({ id: media.id, media, snapshot: JSON.stringify(media) });
    if (!media.subclip_of) return entries;
    media = S.proj.media[media.subclip_of]; if (!media) throw Error("The source subclip parent is missing.");
  }
  throw Error("Choose an existing replacement media source.");
}
async function replaceMediaSource(origin) {
  try {
    if (!placementReady()) return false;
    const selected = selectedClips();
    if (selected.length !== 1 || S.sel.size !== 1 || !selected[0].c.media_id) throw Error("Select exactly one existing media clip to replace.");
    const { c, tr } = selected[0], project = S.proj, sequence = S.seq, context = { ...S.context };
    if (!sequence.tracks.includes(tr) || !tr.clips.includes(c) || tr.locked) throw Error("The target clip must belong to an unlocked current track.");
    if (c.audio_detached_id || c.unlinked_from || project.sequences.some(sq => sq.tracks.some(track => track.clips.some(other => other !== c && (other.unlinked_from === c.id || other.audio_detached_id === c.id))))) throw Error("Relink detached audio before replacing its source.");
    const binIds = [...(S.binSel || [])], media = origin === "source" ? S.src : binIds.length === 1 ? project.media[binIds[0]] : null;
    if (!media || project.media[media.id] !== media) throw Error(origin === "source" ? "Load the replacement from the current project in the Source monitor." : "Select exactly one existing replacement item in the Project panel.");
    const chains = [...sourceReplacementMediaChain(project.media[c.media_id]), ...sourceReplacementMediaChain(media)];
    const snapshot = JSON.stringify(sequence), sourceInMark = S.srcIn, sourceOutMark = S.srcOut;
    const sourceIn = origin === "source" ? S.srcIn ?? sourcePlayheadTime() : 0;
    const sourceOut = origin === "source" ? S.srcOut ?? null : null;
    const duration = validatePlacementClip(c, tr);
    const planned = window.FilmocitySourceReplacement.replace(c, media, { duration, sourceOffset: t => sourceOffset(c, t), speedAt: t => speedAt(c, t) }, { sourceIn, sourceOut, trackKind: tr.kind });
    validatePlacementClip(planned.clip, tr);
    if (!planned.changed) { status("This clip already uses the selected source range."); return false; }
    const valid = () => S.proj === project && S.seq === sequence && window.FilmocitySync.sameProject(context, S.context) && JSON.stringify(sequence) === snapshot &&
      sequence.tracks.includes(tr) && tr.clips.includes(c) && !tr.locked && S.sel.size === 1 && S.sel.has(c.id) &&
      chains.every(item => project.media[item.id] === item.media && JSON.stringify(item.media) === item.snapshot) &&
      (origin === "source" ? S.src === media && S.srcIn === sourceInMark && S.srcOut === sourceOutMark : JSON.stringify([...(S.binSel || [])]) === JSON.stringify(binIds));
    // Let earlier saves finish, then bind one non-optimistic transaction to
    // their acknowledged revision. A concurrent selection/edit invalidates it.
    const state = await flushSaves();
    if (state !== projectSaveState() || state.error || !valid() || !canEdit()) throw Error("The source, target or project changed before replacement. Select it again.");
    if (S.playing) togglePlay(false, { commitTrim: false });
    const result = await workflowRequest(current => {
      if (!valid()) { const error = Error("The source or edit changed before replacement was sent."); error.status = 409; throw error; }
      return api.json("POST", "/api/clip/replace-source", { _context: current, actor: "human", sequence: sequence.id, clip_id: c.id, media_id: media.id, in: sourceIn, ...(sourceOut == null ? {} : { out: sourceOut }) });
    }, { ...state.context, sequence: sequence.id });
    status(`Source replaced; duration, effects and active clip markers kept. Review markers against the new source.${planned.discardedMarkers ? ` ${planned.discardedMarkers} hidden source marker(s) discarded.` : ""}`);
    return result;
  } catch (error) { status(error.message || String(error), "err"); return false; }
}
function replaceWithSource() { return replaceMediaSource("source"); }
function replaceFromBinSource() { return replaceMediaSource("bin"); }
function setLabel(color) { const sel = selectedClips(); if (!sel.length) return; applyOps(sel.map(({ c, tr }) => ({ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, label: color } })), "label", `label ${color || "none"}`); }
function enterTrimMode() { if (!S.editPoint) { status("Select an edit point (click a cut with the Selection tool) first."); return; } S.trimMode = { type: S.trimType || "ripple" }; $("#trimBar").style.display = ""; $("#trimType").textContent = S.trimMode.type; const r = clipById(S.editPoint.clipId); if (r) seekTo(S.editPoint.side === "l" ? r.c.start : clipEnd(r.c)); status("Trim mode: J/K/L then K to trim the edit to the playhead · −5/−1/+1/+5 · Ctrl+T cycles trim type · Esc exits"); renderProgram(); }
function exitTrimMode() { S.trimMode = null; $("#trimBar").style.display = "none"; renderProgram(); }
function cycleTrimType() { const order = ["regular", "ripple", "roll"]; S.trimType = order[(order.indexOf(S.trimType || "ripple") + 1) % order.length]; if (S.trimMode) S.trimMode.type = S.trimType; $("#trimType").textContent = S.trimType; status(`Trim type: ${S.trimType}`); renderTimeline(); }
function trimByType(frames) {
  if (!keyboardEditReady()) return false;
  const ep = S.editPoint, r = ep && clipById(ep.clipId);
  if (!r) { status("Select an edit point before trimming."); return false; }
  const target = keyboardEditTarget(r.c, r.tr, ep.side, frames); if (target == null) return false;
  const mode = S.trimMode?.type || S.trimType || "ripple";
  return applyKeyboardTrim(r.c, r.tr, ep.side, target, mode, mode === "roll" ? "roll_kb" : mode === "ripple" ? "ripple_trim_kb" : "trim_kb", `${mode} trim ${frames} frame(s)`);
}
function trimToPlayhead() {
  if (!keyboardEditReady()) return false;
  const ep = S.editPoint, r = ep && clipById(ep.clipId); if (!r) return false;
  const target = r.tr.kind === "video" ? timing.fromFrames(timing.toFrames(S.t, S.seq.fps || 30), S.seq.fps || 30) : S.t;
  const mode = S.trimMode?.type || S.trimType || "ripple";
  return applyKeyboardTrim(r.c, r.tr, ep.side, target, mode, mode === "roll" ? "roll_kb" : mode === "ripple" ? "ripple_trim_kb" : "trim_kb", `${mode} trim to playhead`);
}
function trimEditPoint(frames, ripple) {
  if (!keyboardEditReady()) return false;
  const ep = S.editPoint, r = ep && clipById(ep.clipId);
  if (!r) { status("Click a cut with the Selection tool to select an edit point, then Ctrl+←/→ (Alt for ripple)."); return false; }
  const target = keyboardEditTarget(r.c, r.tr, ep.side, frames); if (target == null) return false;
  return applyKeyboardTrim(r.c, r.tr, ep.side, target, ripple ? "ripple" : "regular", ripple ? "ripple_trim_kb" : "trim_kb", `${ripple ? "ripple " : ""}trim ${frames > 0 ? "forward" : "backward"} ${Math.abs(frames)} frame(s)`);
}
function addClipMarker() { const sel = selectedClips(); if (sel.length !== 1) return false; const { c, tr } = sel[0]; if (S.t < c.start || S.t > clipEnd(c)) return false; applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, markers: [...(c.markers || []), { id: uid(), t: +(S.t - c.start).toFixed(4), name: "", color: "#4fa36b" }] } }], "marker", "add clip marker"); return true; }
function makeSubclip() { return createSourceItems("subclip"); }
function groupSel(on) {
  if (!canEdit()) return false;
  if (!S.proj?.sequences.includes(S.seq) || projectSaveState().error) { status("Resolve project loading or unsaved edits in Recovery before grouping clips.", "err"); return false; }
  try {
    const sel = selectedClips(); if (sel.length !== S.sel.size) throw Error("The selection changed. Select the clips again before grouping.");
    if (sel.length < (on ? 2 : 1)) return false;
    if (sel.some(({ tr }) => tr.locked)) throw Error("Unlock every selected track before grouping or ungrouping clips.");
    if (on && sel[0].c.group && sel.every(({ c }) => c.group === sel[0].c.group)) return false;
    const affected = on ? sel : sel.filter(({ c }) => c.group != null); if (!affected.length) return false;
    let group = null;
    if (on) {
      const reserved = new Set(S.proj.sequences.flatMap(sq => sq.tracks.flatMap(tr => tr.clips.flatMap(c => [c.id, c.group].filter(Boolean)))));
      for (let tries = 0; tries < 100; tries++) { const candidate = uid(); if (typeof candidate === "string" && candidate && !reserved.has(candidate)) { group = candidate; break; } }
      if (!group) throw Error("Could not allocate a unique group ID. Try again.");
    }
    return applyOps(affected.map(({ c, tr }) => ({ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, group } })), on ? "group" : "ungroup", on ? `group ${affected.length} clips` : "ungroup");
  } catch (error) { status(error.message || String(error), "err"); return false; }
}
function setFit(mode) { /* contain | cover | blur_fill */ const sel = selectedClips().filter(x => x.c.media_id); if (!sel.length) return; applyOps(sel.map(({ c, tr }) => ({ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, fit: mode, transform: { ...(c.transform || {}), scale: 1, x: 0, y: 0 } } })), "fit", mode === "cover" ? "scale to frame size" : "set to frame size"); }
function setAllTall(on) { for (const t of S.seq.tracks) S.tall[t.id] = on; renderTimeline(); }
const ANALYSIS_REVIEWS = new WeakMap();
function analysisTarget(kind) {
  if (!["scenes", "silences", "remix"].includes(kind)) throw Error("Choose a supported source analysis.");
  if (!canEdit() || !S.proj?.sequences.includes(S.seq) || projectSaveState().error) throw Error("Resolve project loading or unsaved edits in Recovery before analyzing clips.");
  const selected = selectedClips(); if (selected.length !== 1) throw Error("Select one timeline clip to analyze.");
  const { c, tr } = selected[0], media = c.media_id && S.proj.media[c.media_id];
  if (tr.locked) throw Error("Unlock the selected clip’s track before analyzing it.");
  if (!media || !(kind === "scenes" ? media.has_video && tr.kind === "video" : media.has_audio)) throw Error(kind === "scenes" ? "Select a video-track clip with a video source." : "Select a clip with an audio source.");
  if (kind === "remix" && tr.kind !== "audio") throw Error("Select one music clip on an audio track to remix.");
  if (c.hold) throw Error("Source analysis cannot establish timeline edits for a held frame. Release the frame hold first.");
  validatePlacementClip(c, tr);
  return { c, tr, media };
}
async function startClipAnalysis(kind, options) {
  const target = analysisTarget(kind), original = { ...S.context, sequence: S.seq.id }, clip = deep(target.c), source = placementMediaIdentity(target.media), requestId = crypto.randomUUID().replaceAll('-', '');
  return withSavedProject(async state => {
    const current = clipById(clip.id);
    if (!window.FilmocitySync.sameProject(original, state.context) || original.sequence !== S.seq.id || !current || current.tr.id !== target.tr.id || current.tr.locked || JSON.stringify(current.c) !== JSON.stringify(clip) || placementMediaIdentity(S.proj.media[clip.media_id] || {}) !== source) throw Error("The source clip changed before analysis started. Select it again.");
    const context = { ...state.context }, project = S.proj, sequence = S.seq;
    let response;
    try {
      response = await api.json("POST", ({ scenes: "/api/media/scenes", silences: "/api/audio/silences", remix: "/api/audio/remix" })[kind], { ...options, media_id: clip.media_id, sequence: sequence.id, clip_id: clip.id, in: clip.in_, out: clip.out, _context: context, request_id: requestId });
    } catch (error) {
      if (![400, 401, 403, 404, 409, 422, 501].includes(error.status)) throw Error("Analysis submission was not confirmed. Check Tasks before starting again. " + error.message);
      throw error;
    }
    if (response?.ok !== true || !response.task?.id || !window.FilmocitySync.sameProject(context, response.context)) throw Error("Analysis submission was not confirmed. Check Tasks before starting again.");
    if (S.proj === project && S.seq === sequence && window.FilmocitySync.sameProject(context, S.context)) { window.FilmocityTasks?.open(); status("Analysis queued. Review the result in Tasks before applying edits."); }
    return response;
  });
}
async function reviewAnalysisTask(identity) {
  const basis = { ...S.context, sequence: S.seq?.id };
  return withSavedProject(async state => {
    if (!window.FilmocitySync.sameProject(basis, state.context) || basis.sequence !== S.seq?.id) throw Error("The sequence changed. Open the analyzed sequence before reviewing.");
    const project = S.proj, sequence = S.seq, context = { ...state.context }, revision = state.revision, snapshot = JSON.stringify(S.seq);
    const reviewed = await api.json("POST", "/api/tasks/" + encodeURIComponent(identity) + "/analysis", { _context: context });
    if (state !== projectSaveState() || state.revision !== revision || S.proj !== project || S.seq !== sequence || JSON.stringify(S.seq) !== snapshot || !window.FilmocitySync.sameProject(context, S.context) || state.error) throw Error("The edit changed while loading analysis. Review it again.");
    if (reviewed?.ok !== true || reviewed.task?.id !== identity || reviewed.context?.revision !== context.revision || !window.FilmocitySync.sameProject(context, reviewed.context)) throw Error("The analysis review was not confirmed for this project.");
    const raw = reviewed.result?.sequence == null && reviewed.result?.clip_id == null;
    if (reviewed.result?.version !== 1 || reviewed.result?.clock !== "media" || !["scenes", "silences", "remix"].includes(reviewed.result?.kind)) throw Error("The analysis review was not confirmed for this sequence.");
    if (raw ? reviewed.plan != null : reviewed.result?.sequence !== sequence?.id || !reviewed.plan || typeof reviewed.plan.fingerprint !== "string" || !Array.isArray(reviewed.plan.ops) || !reviewed.plan.summary) throw Error("The analysis review was not confirmed for this sequence.");
    const media = S.proj.media[reviewed.result.media_id]; if (!media) throw Error("The analyzed source is missing. Run analysis again.");
    if (!raw) ANALYSIS_REVIEWS.set(reviewed, { project, sequence, snapshot, media, identity: placementMediaIdentity(media), review: JSON.stringify(reviewed) });
    return reviewed;
  });
}
function applyAnalysisTask(identity, reviewed) {
  if (reviewed?.task?.id !== identity || !reviewed.plan?.fingerprint || !reviewed.result?.sequence) return Promise.reject(Error("Review this analysis result before applying it."));
  if (!reviewed.plan.ops?.length) return Promise.reject(Error("This result contains no timeline edits to apply."));
  const owner = ANALYSIS_REVIEWS.get(reviewed), media = S.proj?.media[reviewed.result.media_id];
  if (!owner || owner.project !== S.proj || owner.sequence !== S.seq || JSON.stringify(S.seq) !== owner.snapshot || media !== owner.media || placementMediaIdentity(media) !== owner.identity || JSON.stringify(reviewed) !== owner.review) return Promise.reject(Error("The edit or source changed after review. Refresh this analysis review before applying."));
  if (S.playing) togglePlay(false, { commitTrim: false });
  return workflowRequest(context => {
    const currentMedia = S.proj?.media[reviewed.result.media_id];
    if (owner.project !== S.proj || owner.sequence !== S.seq || JSON.stringify(S.seq) !== owner.snapshot || currentMedia !== owner.media || placementMediaIdentity(currentMedia) !== owner.identity || JSON.stringify(reviewed) !== owner.review) { const error = Error("The edit or source changed before analysis Apply. Refresh the review."); error.status = 409; throw error; }
    return api.json("POST", "/api/tasks/" + encodeURIComponent(identity) + "/apply", { _context: context, fingerprint: reviewed.plan.fingerprint, actor: "human", client: CLIENT });
  }, { ...reviewed.context, sequence: reviewed.result.sequence });
}
async function sceneDetect() {
  try {
    analysisTarget("scenes"); const text = prompt("Scene threshold (0.2 = sensitive … 0.6 = only hard cuts)", "0.35"); if (text == null || !text.trim()) return false;
    const threshold = Number(text); if (!Number.isFinite(threshold) || threshold <= 0 || threshold >= 1) throw Error("Choose a scene threshold greater than 0 and less than 1.");
    return await startClipAnalysis("scenes", { threshold });
  } catch (error) { status(error.message || String(error), "err"); return false; }
}

const RENDER_REPLACE_REVIEWS = new WeakMap();
function renderReplaceSnapshot() {
  return JSON.stringify({ sequences: S.proj.sequences, media: S.proj.media, brand: S.proj.brand, resources: S.proj.resources });
}
function renderReplaceTarget(identity) {
  if (!S.proj?.sequences.includes(S.seq) || projectSaveState().error) throw Error("Resolve project loading or unsaved edits in Recovery before rendering a replacement.");
  if (identity == null) {
    const selected = selectedClips();
    if (selected.length !== 1 || S.sel.size !== 1) throw Error("Select one current timeline clip to render and replace.");
    identity = selected[0].c.id;
  }
  const matches = S.seq.tracks.flatMap(tr => tr.clips.filter(c => c.id === identity).map(c => ({ c, tr })));
  if (matches.length !== 1) throw Error("The replacement target is missing or ambiguous. Select it again.");
  const { c, tr } = matches[0];
  if (tr.locked) throw Error("Unlock the selected clip’s track before rendering a replacement.");
  if (c.enabled === false) throw Error("Enable the selected clip before rendering a replacement.");
  if (c.audio_detached_id || c.unlinked_from || S.proj.sequences.some(sq => sq.tracks.some(track => track.clips.some(clip => clip.unlinked_from === c.id)))) throw Error("Keep detached audio pairs separate. Render and Replace does not support detached associations yet.");
  if (c.adjustment || c.blend && c.blend !== "normal" || (c.fx_stack || []).some(fx => fx.enabled !== false && fx.type === "track_matte")) throw Error("This clip depends on other layers. Render and Replace needs a self-contained clip.");
  validatePlacementClip(c, tr);
  return { c, tr };
}
async function startRenderReplace() {
  if (!canEdit()) throw Error("Wait for the current project action to finish.");
  const target = renderReplaceTarget(), project = S.proj, sequence = S.seq, snapshot = renderReplaceSnapshot();
  const basis = { ...S.context, sequence: sequence.id }, clipId = target.c.id, trackId = target.tr.id, requestId = crypto.randomUUID().replaceAll('-', '');
  return withSavedProject(async state => {
    if (S.proj !== project || S.seq !== sequence || !window.FilmocitySync.sameProject(basis, state.context) || basis.sequence !== S.seq.id || renderReplaceSnapshot() !== snapshot) throw Error("The source or project changed before rendering started. Select the clip again.");
    const current = renderReplaceTarget(clipId);
    if (current.tr.id !== trackId || S.sel.size !== 1 || !S.sel.has(clipId)) throw Error("The replacement selection changed before rendering started. Select the clip again.");
    const context = { ...state.context };
    let response;
    try {
      response = await api.json("POST", "/api/render_replace", { sequence: sequence.id, clip_id: clipId, _context: context, request_id: requestId, actor: "human", client: CLIENT });
    } catch (error) {
      if (![400, 401, 403, 404, 409, 422, 501].includes(error.status)) throw Error("Render submission was not confirmed. Check Tasks before starting again. " + error.message);
      throw error;
    }
    if (response?.ok !== true || !response.task?.id || response.task.kind !== "render_replace" || !window.FilmocitySync.sameProject(context, response.context)) throw Error("Render submission was not confirmed. Check Tasks before starting again.");
    if (S.proj === project && S.seq === sequence && window.FilmocitySync.sameProject(context, S.context)) { window.FilmocityTasks?.open(); status("Render queued. Review the baked picture, retained audio settings and warnings in Tasks before applying the replacement."); }
    return response;
  });
}
async function reviewRenderReplaceTask(identity) {
  const basis = { ...S.context, sequence: S.seq?.id };
  return withSavedProject(async state => {
    if (!S.proj?.sequences.includes(S.seq) || !window.FilmocitySync.sameProject(basis, state.context) || basis.sequence !== S.seq?.id) throw Error("The sequence changed. Open the rendered sequence before reviewing.");
    const project = S.proj, sequence = S.seq, context = { ...state.context }, revision = state.revision, snapshot = renderReplaceSnapshot();
    const reviewed = await api.json("POST", "/api/tasks/" + encodeURIComponent(identity) + "/render-replace", { _context: context });
    if (state !== projectSaveState() || state.revision !== revision || S.proj !== project || S.seq !== sequence || renderReplaceSnapshot() !== snapshot || !window.FilmocitySync.sameProject(context, S.context) || state.error) throw Error("The edit or source changed while loading the replacement review. Review it again.");
    const result = reviewed?.result, plan = reviewed?.plan;
    if (reviewed?.ok !== true || reviewed.task?.id !== identity || reviewed.task.kind !== "render_replace" || reviewed.task.status !== "ready" || reviewed.context?.revision !== context.revision || !window.FilmocitySync.sameProject(context, reviewed.context) || result?.version !== 1 || result.kind !== "render_replace" || result.sequence !== sequence.id || !result.clip_id || !result.track_id || !result.media?.id || !Number.isFinite(result.duration) || result.duration <= 0 || !plan || typeof plan.fingerprint !== "string" || !plan.fingerprint || !Array.isArray(plan.ops) || !plan.ops.length || !plan.summary) throw Error("The rendered replacement review was not confirmed for this edit.");
    const target = renderReplaceTarget(result.clip_id);
    if (target.tr.id !== result.track_id) throw Error("The rendered replacement target changed. Review it again.");
    RENDER_REPLACE_REVIEWS.set(reviewed, { project, sequence, snapshot, envelope: JSON.stringify(reviewed) });
    return reviewed;
  });
}
function applyRenderReplaceTask(identity, reviewed) {
  const owner = reviewed && RENDER_REPLACE_REVIEWS.get(reviewed);
  if (!owner || reviewed.task?.id !== identity || owner.project !== S.proj || owner.sequence !== S.seq || renderReplaceSnapshot() !== owner.snapshot || JSON.stringify(reviewed) !== owner.envelope) return Promise.reject(Error("The edit, source or review changed. Refresh the replacement review before applying."));
  try { const target = renderReplaceTarget(reviewed.result.clip_id); if (target.tr.id !== reviewed.result.track_id) throw Error("The replacement target changed."); }
  catch (error) { return Promise.reject(error); }
  if (S.playing) togglePlay(false, { commitTrim: false });
  return workflowRequest(context => {
    if (S.proj !== owner.project || S.seq !== owner.sequence || renderReplaceSnapshot() !== owner.snapshot || JSON.stringify(reviewed) !== owner.envelope) { const error = Error("The edit or source changed before replacement. Refresh the review."); error.status = 409; throw error; }
    return api.json("POST", "/api/tasks/" + encodeURIComponent(identity) + "/apply", { _context: context, fingerprint: reviewed.plan.fingerprint, actor: "human", client: CLIENT });
  }, { ...reviewed.context, sequence: reviewed.result.sequence });
}

function seqFromClip() { return createSequence("source"); }
// ---- multicam ----
async function createMulticam(name, sync, audioMode, ids, reviewed = null) {
  return createSyncSequence(reviewed, { kind: "multicam", name, sync, audioMode, media_ids: [...ids] });
}
function multicamAt(t, strict = false) {
  try {
    if (!S.seq || !Number.isFinite(t)) return null;
    const candidates = S.seq.tracks.filter(tr => tr.kind === "video").flatMap(tr => tr.clips.flatMap(c => {
      if (!c.sequence_id || t < c.start || t >= clipEnd(c)) return [];
      const sub = S.proj.sequences.find(sq => sq.id === c.sequence_id);
      return sub?.multicam ? [{ c, tr, sub }] : [];
    }));
    const selected = candidates.filter(({ c }) => S.sel?.has(c.id));
    if (selected.length > 1) throw Error("Select only one active multicamera clip, or deselect to use the targeted video track.");
    if (selected.length === 1) {
      if (selected[0].tr.muted || selected[0].c.enabled === false) throw Error("The selected multicamera picture is muted or disabled. Enable it before switching angles.");
      return selected[0];
    }
    const visible = candidates.filter(({ c, tr }) => !tr.muted && c.enabled !== false);
    const targeted = visible.filter(({ tr }) => tr.id === S.target?.video);
    if (targeted.length > 1) throw Error("The targeted track contains overlapping multicamera clips. Select the intended clip.");
    if (targeted.length === 1) return targeted[0];
    visible.sort((a, b) => b.tr.index - a.tr.index);
    if (visible.length > 1 && visible[0].tr.index === visible[1].tr.index) throw Error("Overlapping multicamera clips need an explicit selection.");
    return visible[0] || null;
  } catch (error) { if (strict) throw error; return null; }
}
function multicamGridOwner(mc) {
  return { project: S.proj, sequence: S.seq, context: { ...S.context }, c: mc.c, tr: mc.tr, sub: mc.sub,
    selection: JSON.stringify([...(S.sel || [])]), target: S.target?.video,
    timing: JSON.stringify([mc.c.start, mc.c.in_, mc.c.out, mc.c.speed, mc.c.reverse, mc.c.hold, mc.c.time_remap]),
    angles: mc.sub.tracks.filter(tr => tr.kind === "video").sort((a, b) => a.index - b.index) };
}
function multicamGridCurrent(grid, mc) {
  return !!grid && grid.project === S.proj && grid.sequence === S.seq && window.FilmocitySync.sameProject(grid.context, S.context) &&
    grid.c === mc.c && grid.tr === mc.tr && grid.sub === mc.sub && grid.selection === JSON.stringify([...(S.sel || [])]) && grid.target === S.target?.video &&
    grid.timing === JSON.stringify([mc.c.start, mc.c.in_, mc.c.out, mc.c.speed, mc.c.reverse, mc.c.hold, mc.c.time_remap]) &&
    grid.angles.length === mc.sub.tracks.filter(tr => tr.kind === "video").length && grid.angles.every((tr, i) => tr === mc.sub.tracks.filter(t => t.kind === "video").sort((a, b) => a.index - b.index)[i]);
}
function switchAngle(n, grid = null) {
  if (!canEdit() || projectSaveState().error) { if (projectSaveState().error) status("Resolve unsaved edits in Recovery before switching angles.", "err"); return false; }
  try {
    const mc = multicamAt(S.t, true); if (!mc) throw Error("No visible multicamera clip under the playhead.");
    if (grid && (!S.multiView || !multicamGridCurrent(grid, mc))) throw Error("The camera grid changed. Choose an angle from the current grid.");
    const { c, tr, sub } = mc, angles = sub.tracks.filter(t => t.kind === "video").sort((a, b) => a.index - b.index);
    if (!Number.isInteger(n) || n < 0 || n >= angles.length) throw Error("Choose an available camera angle.");
    const bus = window.FilmocityAudioPreview.destination(S.seq, tr);
    if (tr.locked || c.audio?.linked !== false && bus?.locked) throw Error("Unlock the multicamera track and its routed sound track before switching angles.");
    if ((c.multicam_angle ?? 0) === n) return false;
    const t = timing.fromFrames(timing.toFrames(S.t, S.seq.fps), S.seq.fps);
    if (t >= clipEnd(c) - 1e-9) throw Error("Move the playhead inside the multicamera clip before switching.");
    let ops, right;
    if (t <= c.start + 1e-9) ops = [{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, multicam_angle: n } }];
    else {
      if (c.audio_detached_id || c.unlinked_from || S.proj.sequences.some(sq => sq.tracks.some(track => track.clips.some(other => other !== c && (other.unlinked_from === c.id || other.audio_detached_id === c.id))))) throw Error("Relink the detached sound pair before cutting a multicamera clip. Its partner cannot be duplicated by an angle cut.");
      const parts = window.FilmocityClipSplit.split(c, t - c.start, uid(), { duration: clipDur(c), sourceOffset: at => sourceOffset(c, at), speedAt: at => speedAt(c, at) });
      right = parts[1]; right.start = t; right.multicam_angle = n;
      ops = parts.map(clip => ({ op: "set_clip", sequence: S.seq.id, track: tr.id, clip }));
    }
    const result = applyOps(ops, right ? "multicam_cut" : "multicam_switch", `${right ? "cut & switch" : "switch"} to angle ${n + 1}`);
    if (right && S.sel.has(c.id)) { S.sel = new Set([...S.sel].map(id => id === c.id ? right.id : id)); refreshSel(); if (S.multiView) renderProgram(); }
    return result;
  } catch (error) { status(error.message || String(error), "err"); return false; }
}
function updateMulticamControls(grid, message = "") {
  const bar = $("#multicamAngles"), label = $("#multicamTarget"), buttons = $("#multicamAngleButtons");
  if (!bar || !label || !buttons) return;
  bar.hidden = !S.multiView;
  if (!grid) { label.textContent = message || (S.multiView ? "No editable camera grid at this position." : ""); for (const button of buttons.children) button.disabled = true; return; }
  label.textContent = `${grid.c.name || grid.sub.name || "Multicamera"} · ${grid.tr.name || grid.tr.id} · Audio: ${grid.sub.multicam_audio === "follow" ? "follows camera" : "fixed track"}. Choose an angle to cut at the playhead.`;
  while (buttons.children.length > grid.angles.length) buttons.lastElementChild.remove();
  grid.angles.forEach((tr, index) => {
    let button = buttons.children[index];
    if (!button) { button = document.createElement("button"); button.type = "button"; buttons.appendChild(button); }
    button.textContent = `${index + 1}: ${tr.name || tr.id}`; button.setAttribute("aria-pressed", String(index === (grid.c.multicam_angle ?? 0)));
    button.disabled = !!grid.tr.locked || grid.c.audio?.linked !== false && !!window.FilmocityAudioPreview.destination(S.seq, grid.tr)?.locked || S.recoveryRequired || !!projectSaveState().error;
    button.onclick = () => switchAngle(index, grid);
  });
}
function drawMulticamGrid(cv, time, used) {
  try {
    const mc = multicamAt(time, true); if (!mc) { updateMulticamControls(null); return; }
    const grid = multicamGridOwner(mc), { c, sub } = mc, vts = grid.angles, n = vts.length;
    if (!n) throw Error("This multicamera sequence has no camera tracks.");
    const cols = Math.ceil(Math.sqrt(n)), rows = Math.ceil(n / cols), ctx = cv.getContext("2d"), W = cv.width, H = cv.height;
    const local = pictureSourcePosition(c, time - c.start, { fps: sub.fps, has_video: true });
    ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.filter = "none"; ctx.globalAlpha = 1; ctx.fillStyle = "#000"; ctx.fillRect(0, 0, W, H);
    vts.forEach((vt, i) => {
      const oc = offscreen["mv" + i] || (offscreen["mv" + i] = document.createElement("canvas")); oc.width = sub.width; oc.height = sub.height;
      drawSequence(oc, { ...sub, tracks: sub.tracks.map(tr => ({ ...tr, muted: tr.kind !== "video" || tr !== vt })) }, local, used, 1, ["multiview", String(i)], { audible: false, held: !!c.hold, reversed: !!c.reverse, rate: speedAt(c, time - c.start) });
      const cw = W / cols, ch = H / rows, x = i % cols * cw, y = Math.floor(i / cols) * ch, fit = Math.min(cw / sub.width, ch / sub.height) * .96;
      ctx.drawImage(oc, x + (cw - sub.width * fit) / 2, y + (ch - sub.height * fit) / 2, sub.width * fit, sub.height * fit);
      ctx.strokeStyle = i === (c.multicam_angle ?? 0) ? "#f6c14a" : "#444"; ctx.lineWidth = i === (c.multicam_angle ?? 0) ? Math.max(4, W / 180) : 2; ctx.strokeRect(x + 4, y + 4, cw - 8, ch - 8);
      ctx.fillStyle = "#fff"; ctx.font = `700 ${Math.round(W / 24)}px sans-serif`; ctx.fillText(`${i + 1}`, x + W / 40, y + W / 20);
    });
    S.mvGrid = { ...grid, cols, rows, n }; updateMulticamControls(S.mvGrid);
  } catch (error) { S.mvGrid = null; S.mvGridMessage = error.message || String(error); updateMulticamControls(null, S.mvGridMessage); }
}
function cutSel() {
  if (!placementReady()) return false;
  try {
    const captured = captureClipboard(); if (!captured.items.length) return false;
    if (captured.items.some(item => trackOf(item.track)?.locked)) throw Error("Unlock every selected track before cutting clips.");
    const ops = captured.items.map(item => ({ op: "remove_clip", sequence: S.seq.id, track: item.track, clip_id: item.clip.id }));
    clipboard = captured.items; clipboardOwner = captured.owner;
    const pending = applyOps(ops, "cut", `cut ${clipboard.length} clip(s)`); S.sel.clear(); refreshSel(); return pending;
  } catch (error) { status(error.message || String(error), "err"); return false; }
}
function pasteInsert() { return pasteClipboard("insert"); }
function selectMatchingLabel() { const sel = selectedClips(); if (!sel.length) return; const labs = new Set(sel.map(x => x.c.label || "")); for (const t2 of S.seq.tracks) for (const c of t2.clips) if (labs.has(c.label || "")) S.sel.add(c.id); refreshSel(); CR.panels.render(); }
function findInTimeline() { const q = prompt("Find clip (name or text)"); if (!q) return; const ql = q.toLowerCase(); const hits = []; for (const t2 of S.seq.tracks) for (const c of t2.clips) { const nm = (c.name || (c.media_id && S.proj.media[c.media_id] ? S.proj.media[c.media_id].name : "") || (c.title && c.title.text) || (c.graphic && c.graphic.name) || "").toLowerCase(); if (nm.includes(ql)) hits.push(c); } if (!hits.length) { status(`No clip matching "${q}"`); return; } hits.sort((a, b) => a.start - b.start); const nx = hits.find(c => c.start > S.t + 1e-4) || hits[0]; S.sel = new Set([nx.id]); refreshSel(); seekTo(nx.start); CR.panels.render(); status(`${hits.length} match(es) — ${fmtTC(nx.start, S.seq.fps)}`); }
function markClip() { const sel = selectedClips(); if (!sel.length) return; const a = Math.min(...sel.map(x => x.c.start)), b = Math.max(...sel.map(x => clipEnd(x.c))); const si = S.proj.sequences.indexOf(S.seq); applyOps([{ op: "set", path: `/sequences/${si}/in_point`, value: a }, { op: "set", path: `/sequences/${si}/out_point`, value: b }], "sequence_io", "mark clip/selection"); }
function gotoIn() { if (S.seq.in_point != null) seekTo(S.seq.in_point); } function gotoOut() { if (S.seq.out_point != null) seekTo(S.seq.out_point); }
function addEditAllTracks() { return addEdits(S.seq.tracks.map(tr => tr.id), "add_edit_all", "add edit to all tracks"); }
function defaultTransitionsToSelection() { const sel = selectedClips(); if (sel.length < 1) return; const ops = []; const byTrack = {}; for (const { c, tr } of sel) (byTrack[tr.id] = byTrack[tr.id] || []).push(c);
  for (const [tid, cs] of Object.entries(byTrack)) { const tr = trackOf(tid); const sorted = cs.sort((a, b) => a.start - b.start); const d = tr.kind === "video" ? (S.prefs.vt || 1) : (S.prefs.at || 1); for (let i = 0; i < sorted.length; i++) { const c = sorted[i]; const isVideo = tr.kind === "video"; const key = isVideo ? "transition_in" : "audio_transition_in"; const keyOut = isVideo ? "transition_out" : "audio_transition_out"; const prev = sorted[i - 1]; if (prev && Math.abs(clipEnd(prev) - c.start) < frame()) { ops.push({ op: "set_clip", sequence: S.seq.id, track: tid, clip: { id: c.id, [key]: { type: isVideo ? "dissolve" : "constant_power", duration: Math.min(d, clipDur(c) / 2) } } }); if (!isVideo) ops.push({ op: "set_clip", sequence: S.seq.id, track: tid, clip: { id: prev.id, [keyOut]: { type: "constant_power", duration: Math.min(d, clipDur(prev) / 2) } } }); } } }
  if (ops.length) applyOps(ops, "transition", `default transitions to ${sel.length} clip(s)`); else status("No adjacent cuts among the selected clips."); }
function alignSel(mode) { const sel = selectedClips().filter(x => x.c.title || x.c.graphic || x.c.media_id); if (!sel.length) return; const ops = []; for (const { c, tr } of sel) { if (c.title) { const t = { ...c.title }; if (mode === "hcenter") { t.align = "center"; t.x = 0; } if (mode === "vcenter") { t.valign = "center"; t.y = 0; } if (mode === "left") { t.align = "left"; t.x = 0; } if (mode === "right") { t.align = "right"; t.x = 0; } if (mode === "top") { t.valign = "top"; t.y = 0; } if (mode === "bottom") { t.valign = "bottom"; t.y = 0; } ops.push({ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, title: t } }); }
    else if (c.graphic) { const g = deep(c.graphic); for (const L of g.layers) { if (L.kind === "text") { if (mode === "hcenter") { L.align = "center"; L.x = 0; } if (mode === "vcenter") { L.valign = "center"; L.y = 0; } if (mode === "left") L.align = "left"; if (mode === "right") L.align = "right"; if (mode === "top") L.valign = "top"; if (mode === "bottom") L.valign = "bottom"; } else if (L.w != null) { if (mode === "hcenter") L.x = (1 - L.w) / 2; if (mode === "vcenter") L.y = (1 - L.h) / 2; if (mode === "left") L.x = 0.05; if (mode === "right") L.x = 0.95 - L.w; if (mode === "top") L.y = 0.05; if (mode === "bottom") L.y = 0.95 - L.h; } } ops.push({ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, graphic: g } }); }
    else { const t = { ...(c.transform || {}) }; if (mode === "hcenter") t.x = 0; if (mode === "vcenter") t.y = 0; ops.push({ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, transform: t } }); } } applyOps(ops, "align", `align ${mode}`); }
function captureFlattenSelection() {
  const owner = sequenceOrganizationOwner(), selected = selectedClips();
  if (!selected.length || selected.length > 100 || selected.length !== S.sel.size || selected.some(({ c }) => !S.proj.sequences.find(sq => sq.id === c.sequence_id)?.multicam)) throw Error("Select 1–100 current multicamera clips to flatten.");
  if (selected.some(({ tr }) => tr.locked)) throw Error("Unlock every selected track before flattening.");
  Object.assign(owner, { clipIds: [...S.sel], pending: false, submitted: false, cancelled: false, generation: 0, review: null }); return owner;
}
async function reviewFlattenSelection(owner, current = () => true) {
  if (!sequenceOrganizationCurrent(owner) || owner.pending || owner.submitted || !canEdit() || !current()) throw Error("The selected multicamera clips changed. Reopen Flatten.");
  const generation = ++owner.generation, valid = () => sequenceOrganizationCurrent(owner) && owner.generation === generation && current();
  owner.pending = true; owner.review = null;
  try {
    const state = await flushSaves();
    if (state !== projectSaveState() || state.error || !valid() || !canEdit()) throw Error("The selection changed or Recovery needs attention. Reopen Flatten after saving.");
    const context = { ...state.context }, report = await api.json("POST", "/api/multicam/flatten/review", { sequence: owner.sequence.id, clip_ids: owner.clipIds, _context: context, actor: "human", client: CLIENT });
    if (!valid() || state !== projectSaveState() || state.pending || state.error || S.context.revision !== context.revision) throw Error("The saved multicamera edit changed during review. Review again.");
    const sum = report?.summary, strings = list => Array.isArray(list) && list.every(value => typeof value === "string");
    const ranges = list => Array.isArray(list) && list.every(value => value && [value.start, value.end, value.source_in, value.source_out, value.speed].every(Number.isFinite) && value.start >= 0 && value.end > value.start && value.source_in >= 0 && value.source_out > value.source_in && value.speed > 0 && typeof value.media_id === "string");
    if (typeof report?.ok !== "boolean" || report.kind !== "multicam_flatten" || report.sequence !== owner.sequence.id || JSON.stringify(report.clip_ids) !== JSON.stringify(owner.clipIds) ||
      !window.FilmocitySync.validContext(report.context) || !window.FilmocitySync.sameProject(context, report.context) || report.context.revision !== context.revision ||
      !report.settings || JSON.stringify(report.settings) !== "{}" || !/^[a-f0-9]{64}$/.test(report.fingerprint || "") || !Array.isArray(report.issues) || report.issues.some(issue => !issue || typeof issue.message !== "string" || !["error", "warning", "info"].includes(issue.severity)) ||
      !sum || sum.kind !== "multicam_flatten" || sum.sequence !== owner.sequence.id || sum.selected_count !== owner.clipIds.length || typeof sum.message !== "string" || !strings(sum.replacement_clip_ids) || new Set(sum.replacement_clip_ids).size !== sum.replacement_clip_ids.length ||
      !Array.isArray(sum.source_tracks) || !Array.isArray(sum.added_tracks) || [...sum.source_tracks, ...sum.added_tracks].some(tr => !tr || typeof tr.id !== "string" || !["video", "audio"].includes(tr.kind)) ||
      !Array.isArray(sum.resolved) || sum.resolved.some(value => !value || !owner.clipIds.includes(value.clip_id) || typeof value.source_sequence !== "string" || !Number.isInteger(value.angle) || value.angle < 0 || typeof value.audio_mode !== "string" || !ranges(value.picture_ranges) || !ranges(value.audio_ranges)) ||
      !strings(sum.processing) || !strings(sum.warnings) || !strings(sum.affected_fields) || report.ok && (!sum.replacement_clip_ids.length || sum.resolved.length !== owner.clipIds.length || new Set(sum.resolved.map(value => value.clip_id)).size !== owner.clipIds.length)) throw Error("Flatten review did not confirm this saved selection. Review again before applying.");
    owner.review = { report, snapshot: JSON.stringify(report), generation }; return report;
  } finally { owner.pending = false; }
}
function flattenReviewCurrent(owner, report) {
  const state = projectSaveState();
  return sequenceOrganizationCurrent(owner) && owner.review?.report === report && owner.review.snapshot === JSON.stringify(report) && owner.review.generation === owner.generation &&
    report.ok === true && !report.issues.some(issue => issue.severity === "error") && !state.pending && !state.error && S.context.revision === report.context.revision && state.context.revision === report.context.revision;
}
async function applyFlattenSelection(owner, report, current = () => true) {
  if (!flattenReviewCurrent(owner, report) || owner.pending || owner.submitted || !current() || !canEdit()) throw Error("The reviewed multicamera edit changed. Review Flatten again before Apply.");
  const same = (a, b) => {
    if (a === b) return true;
    if (!a || !b || typeof a !== "object" || typeof b !== "object" || Array.isArray(a) !== Array.isArray(b)) return false;
    const keys = Object.keys(a); return keys.length === Object.keys(b).length && keys.every(key => Object.hasOwn(b, key) && same(a[key], b[key]));
  };
  owner.pending = true; let savedReply;
  try {
    if (S.playing) togglePlay(false, { commitTrim: false });
    await workflowRequest(async context => {
      if (!flattenReviewCurrent(owner, report) || !current()) { const error = Error("Flatten was canceled or its selection changed before submission."); error.status = 409; throw error; }
      owner.submitted = true;
      const reply = await api.json("POST", "/api/multicam/flatten", { sequence: owner.sequence.id, clip_ids: owner.clipIds, fingerprint: report.fingerprint, _context: context, actor: "human", client: CLIENT });
      if (S.proj !== owner.project || !window.FilmocitySync.sameProject(owner.context, S.context)) throw Error("The original project changed while Flatten was submitted. Inspect its saved outcome before continuing.");
      if (reply?.ok !== true || reply.kind !== "multicam_flatten" || reply.changed !== true || reply.project !== owner.context.project || reply.sequence !== owner.sequence.id ||
        JSON.stringify(reply.source_clip_ids) !== JSON.stringify(owner.clipIds) || JSON.stringify(reply.replacement_clip_ids) !== JSON.stringify(report.summary.replacement_clip_ids) ||
        !same(reply.summary, report.summary) || !same(reply.warnings, report.summary.warnings)) throw Error("Flatten did not confirm the reviewed change. Inspect the saved project in Recovery before repeating it.");
      savedReply = reply; return { ...reply, sequence: null };
    }, { ...report.context, sequence: owner.sequence.id });
    if (sequenceOrganizationIntent(owner)) { S.sel = new Set(savedReply.replacement_clip_ids); refreshSel(); CR.panels?.render(); }
    if (window.FilmocitySync.sameProject(owner.context, S.context)) status([savedReply.summary.message, ...savedReply.warnings].join(" "));
    owner.cancelled = true; owner.review = null; return savedReply;
  } catch (error) { if ([400, 401, 403, 404, 409, 422, 501].includes(error.status)) owner.submitted = false; throw error; }
  finally { owner.pending = false; }
}
function flattenMulticam() {
  try { return CR.panels.flattenDialog(captureFlattenSelection()); }
  catch (error) { status(error.message || String(error), "err"); return false; }
}
const SYNC_REVIEWS = new WeakMap(), SYNC_QUEUES = new WeakMap();
function syncMediaSnapshot(ids, requireAudio = true) {
  if (!Array.isArray(ids) || ids.length < 2 || ids.length > 8 || ids.some(id => typeof id !== "string" || !id) || new Set(ids).size !== ids.length) throw Error("Choose two to eight distinct sources for synchronization.");
  const sources = {}, visiting = new Set();
  const visit = id => {
    if (visiting.has(id)) throw Error("A synchronized source has a circular subclip reference.");
    if (sources[id]) return;
    const media = S.proj?.media[id]; if (!media) throw Error("A synchronized source is missing from this project.");
    visiting.add(id); sources[id] = placementMediaIdentity(media);
    if (media.subclip_of) visit(media.subclip_of);
    visiting.delete(id);
  };
  for (const id of ids) {
    const media = S.proj?.media[id];
    if (!media || !Number.isFinite(media.duration) || media.duration <= 0 || requireAudio && !media.has_audio) throw Error("Synchronization needs available sources with audio and a known positive duration.");
    visit(id);
  }
  return JSON.stringify(sources);
}
function syncTimelineTargets(ids) {
  if (!S.proj?.sequences.includes(S.seq) || projectSaveState().error) throw Error("Resolve project loading or unsaved edits in Recovery before synchronizing.");
  if (!Array.isArray(ids) || ids.length < 2 || ids.length > 8 || new Set(ids).size !== ids.length) throw Error("Select two to eight clips on different tracks to synchronize.");
  const targets = ids.map(id => {
    const matches = S.seq.tracks.flatMap(tr => tr.clips.filter(c => c.id === id).map(c => ({ c, tr })));
    if (matches.length !== 1) throw Error("A synchronization target is missing or ambiguous. Select the clips again.");
    const { c, tr } = matches[0], media = S.proj.media[c.media_id];
    if (tr.locked || c.enabled === false) throw Error("Enable the selected clips and unlock all their tracks before synchronizing.");
    if (!media?.has_audio || c.sequence_id) throw Error("Every selected clip needs its own audio source for synchronization.");
    if (c.reverse || c.hold || c.time_remap?.length) throw Error("Synchronize supports forward clips at a constant rate. Reverse, ramped and held clips need a different alignment method.");
    validatePlacementClip(c, tr); return { c, tr };
  });
  if (new Set(targets.map(x => x.tr.id)).size !== targets.length) throw Error("Select synchronized clips on different tracks.");
  const mediaIds = [...new Set(targets.map(x => x.c.media_id))];
  // Repeated uses of one source are allowed for timeline clips; the raw-source
  // dialog requires distinct media. Include each source's parent chain here.
  const media = mediaIds.length === 1 ? JSON.stringify(S.proj.media) : syncMediaSnapshot(mediaIds);
  return { targets, mediaIds, snapshot: JSON.stringify({ sequence: S.seq, media }) };
}
function syncOwnerCurrent(owner) {
  if (!owner || owner.project !== S.proj || owner.sequence !== S.seq || !window.FilmocitySync.sameProject(owner.context, S.context) || projectSaveState().error) return false;
  try { return owner.clipIds ? syncTimelineTargets(owner.clipIds).snapshot === owner.snapshot : syncMediaSnapshot(owner.mediaIds, owner.requireAudio) === owner.snapshot; }
  catch { return false; }
}
async function startSyncTask(options = {}) {
  if (!canEdit() || !S.proj?.sequences.includes(S.seq)) throw Error("Wait for the current project action to finish before synchronizing.");
  const timeline = Array.isArray(options.clip_ids), ids = timeline ? [...options.clip_ids] : [...(options.media_ids || [])];
  if (timeline && options.sequence !== S.seq.id) throw Error("Open the selected clips’ sequence before synchronizing.");
  const selected = timeline ? syncTimelineTargets(ids) : null;
  const owner = { project: S.proj, sequence: S.seq, context: { ...S.context }, clipIds: timeline ? ids : null,
    mediaIds: selected ? selected.mediaIds : ids, requireAudio: true, snapshot: selected ? selected.snapshot : syncMediaSnapshot(ids) };
  const requestId = crypto.randomUUID().replaceAll('-', '');
  return withSavedProject(async state => {
    if (!syncOwnerCurrent(owner) || !window.FilmocitySync.sameProject(owner.context, state.context)) throw Error("The project, selected clips or sources changed before synchronization started. Select them again.");
    if (timeline && (S.sel.size !== ids.length || ids.some(id => !S.sel.has(id)))) throw Error("The synchronization selection changed. Select the clips again.");
    const context = { ...state.context }; let queued;
    try { queued = await api.json("POST", "/api/audio/sync", { ...(timeline ? { sequence: owner.sequence.id, clip_ids: ids } : { media_ids: ids }), _context: context, request_id: requestId, actor: "human", client: CLIENT }); }
    catch (error) {
      if (![400, 401, 403, 404, 409, 422, 501].includes(error.status)) throw Error("Synchronization submission was not confirmed. Check Tasks before starting again. " + error.message);
      throw error;
    }
    if (queued?.ok !== true || queued.task?.kind !== "sync" || !queued.task.id || !window.FilmocitySync.sameProject(context, queued.context)) throw Error("Synchronization submission was not confirmed. Check Tasks before starting again.");
    SYNC_QUEUES.set(queued, { ...owner, context });
    if (syncOwnerCurrent(owner)) { if (timeline) window.FilmocityTasks?.open(); status("Synchronization queued. Review measured offsets and confidence before applying any edit."); }
    return queued;
  });
}
async function waitSyncTask(queued, options = {}) {
  const owner = SYNC_QUEUES.get(queued), current = () => syncOwnerCurrent(owner) && (!options.isCurrent || options.isCurrent());
  if (!current()) throw Error("The synchronized sources or dialog changed. Open a fresh review in the original project.");
  const deadline = Date.now() + 900000;
  while (Date.now() < deadline) {
    if (!current()) throw Error("The synchronized sources or dialog changed. Analysis remains available in Tasks.");
    const catalog = await api.get("/api/tasks");
    if (!current()) throw Error("The synchronized sources or dialog changed. Analysis remains available in Tasks.");
    if (!window.FilmocitySync.sameProject(owner.context, catalog.context) || !Array.isArray(catalog.tasks)) throw Error("Synchronization status was not confirmed for this project. Check Tasks before trying again.");
    const task = catalog.tasks.find(value => value.id === queued.task.id);
    if (!task || task.kind !== "sync" || !window.FilmocitySync.sameProject(owner.context, task.context)) throw Error("The synchronization task is unavailable. Check Tasks before trying again.");
    if (task.status === "ready") {
      const reviewed = await reviewSyncTask(task.id);
      if (!current()) throw Error("The synchronized sources or dialog changed while loading review.");
      const expected = owner.clipIds || owner.mediaIds, actual = owner.clipIds ? reviewed.result.clip_ids : reviewed.result.media_ids;
      if (JSON.stringify(expected) !== JSON.stringify(actual)) throw Error("The synchronization result belongs to different sources.");
      return reviewed;
    }
    if (!["queued", "running", "cancelling", "publishing"].includes(task.status)) throw Error("Synchronization " + task.status + ": " + (task.message || "Inspect the task before starting again."));
    options.onProgress?.(task);
    await new Promise(resolve => setTimeout(resolve, 750));
  }
  throw Error("Synchronization is still pending. Inspect Tasks; no edit was created or retried.");
}
async function reviewSyncTask(identity) {
  const basis = { ...S.context, sequence: S.seq?.id };
  return withSavedProject(async state => {
    if (!S.proj?.sequences.includes(S.seq) || !window.FilmocitySync.sameProject(basis, state.context) || basis.sequence !== S.seq.id) throw Error("The sequence changed. Review synchronization in the original project and sequence.");
    const project = S.proj, sequence = S.seq, revision = state.revision, context = { ...state.context }, snapshot = JSON.stringify({ sequence, media: project.media });
    const reviewed = await api.json("POST", "/api/tasks/" + encodeURIComponent(identity) + "/sync", { _context: context });
    if (state !== projectSaveState() || state.revision !== revision || S.proj !== project || S.seq !== sequence || JSON.stringify({ sequence: S.seq, media: S.proj.media }) !== snapshot || state.error || !window.FilmocitySync.sameProject(context, S.context)) throw Error("The edit or sources changed while synchronization review loaded. Review it again.");
    const result = reviewed?.result, plan = reviewed?.plan;
    if (reviewed?.ok !== true || reviewed.task?.id !== identity || reviewed.task.kind !== "sync" || reviewed.task.status !== "ready" || reviewed.context?.revision !== context.revision || !window.FilmocitySync.sameProject(context, reviewed.context) || result?.version !== 1 || result.kind !== "sync" || result.clock !== "timeline-local" || !["timeline", "media"].includes(result.mode) || !plan || !plan.summary || !Array.isArray(plan.ops) || typeof plan.fingerprint !== "string" || !plan.fingerprint) throw Error("The synchronization review was not confirmed for this edit.");
    const timeline = result.mode === "timeline", ids = timeline ? result.clip_ids : result.media_ids;
    if (!Array.isArray(ids) || ids.length < 2 || ids.length > 8 || new Set(ids).size !== ids.length || ids.some(id => typeof id !== "string" || !id) || result.reference !== ids[0] || !result.offsets || ids.some(id => !Number.isFinite(result.offsets[id])) || result.offsets[ids[0]] !== 0 || !Array.isArray(result.matches) || !Array.isArray(result.media_ids) || result.media_ids.some(id => !project.media[id])) throw Error("Synchronization returned missing or invalid source offsets. No zero-offset fallback is allowed.");
    if (timeline ? result.sequence !== sequence.id : result.sequence != null || plan.ops.length) throw Error("The synchronization result belongs to a different edit.");
    if (result.media_ids.length !== ids.length || result.matches.length !== ids.length || result.matches.some((match, i) => !match || match.id !== ids[i] || match.media_id !== result.media_ids[i] || match.offset !== result.offsets[ids[i]] || !Number.isFinite(match.correlation) || Math.abs(match.correlation) > 1 || !Number.isFinite(match.confidence) || match.confidence < 0 || match.confidence > 1 || !Number.isFinite(match.overlap_seconds) || match.overlap_seconds <= 0 || !Number.isFinite(match.resolution) || match.resolution < 0 || !["reference", "envelope", "pcm"].includes(match.method))) throw Error("Synchronization returned incomplete measurement quality. Review a complete result before applying it.");
    const targets = timeline ? syncTimelineTargets(ids) : null;
    if (targets && targets.targets.some((target, i) => target.c.media_id !== result.media_ids[i])) throw Error("Synchronization returned sources that do not match the selected clips.");
    const owner = { project, sequence, context, clipIds: timeline ? [...ids] : null, mediaIds: timeline ? targets.mediaIds : [...ids], requireAudio: true,
      snapshot: timeline ? targets.snapshot : syncMediaSnapshot(ids), envelope: JSON.stringify(reviewed) };
    SYNC_REVIEWS.set(reviewed, owner); return reviewed;
  });
}
function syncReviewedOwner(identity, reviewed, mode) {
  const owner = reviewed && SYNC_REVIEWS.get(reviewed);
  if (!owner || reviewed.task?.id !== identity || reviewed.result?.mode !== mode || !syncOwnerCurrent(owner) || JSON.stringify(reviewed) !== owner.envelope) throw Error("The sources, edit or synchronization review changed. Refresh the review before applying it.");
  return owner;
}
function applySyncTask(identity, reviewed) {
  let owner;
  try { owner = syncReviewedOwner(identity, reviewed, "timeline"); if (!reviewed.plan.ops.length) throw Error("This synchronization result contains no timeline moves to apply."); }
  catch (error) { return Promise.reject(error); }
  if (S.playing) togglePlay(false, { commitTrim: false });
  return workflowRequest(context => {
    try { syncReviewedOwner(identity, reviewed, "timeline"); } catch (error) { error.status = 409; throw error; }
    return api.json("POST", "/api/tasks/" + encodeURIComponent(identity) + "/apply", { _context: context, fingerprint: reviewed.plan.fingerprint, actor: "human", client: CLIENT });
  }, { ...reviewed.context, sequence: owner.sequence.id });
}
function syncSequenceAlignment(options, offsets) {
  const ids = options.media_ids, picture = ids.map(id => S.proj.media[id]).find(m => m?.has_video), fps = mediaRate(picture, true);
  if (!picture || ids.some(id => !Number.isFinite(offsets[id]))) throw Error("Review a valid offset for each source before planning sequence alignment.");
  const shift = -Math.min(0, ...ids.map(id => offsets[id]));
  return { fps, shift, placements: ids.map(id => {
    const media = S.proj.media[id], exact = offsets[id] + shift;
    const pictureStart = media.has_video ? timing.fromFrames(timing.toFrames(exact, fps), fps) : null;
    const audioStart = media.has_audio ? Math.round(exact * 48000) / 48000 : null;
    return { id, exact_start: exact, picture_start: pictureStart, picture_residual: pictureStart == null ? null : pictureStart - exact,
      audio_start: audioStart, audio_residual: audioStart == null ? null : audioStart - exact };
  }) };
}
function previewSyncSequence(reviewed, options) {
  syncReviewedOwner(reviewed?.task?.id, reviewed, "media");
  if (JSON.stringify(options.media_ids) !== JSON.stringify(reviewed.result.media_ids)) throw Error("Review these sources before previewing sequence alignment.");
  return syncSequenceAlignment(options, reviewed.result.offsets);
}
function buildSyncSequence(options, offsets) {
  const ids = [...options.media_ids], media = ids.map(id => S.proj.media[id]), kind = options.kind;
  syncMediaSnapshot(ids, kind === "merge" || options.sync === "audio");
  if (kind === "multicam" ? media.some(m => !m.has_video || m.is_image) : kind !== "merge" || media.length !== 2 || !media[0].has_video || !media[1].has_audio || media[1].has_video) throw Error("Choose two to eight video sources for multicam, or one video and one audio-only source to merge.");
  if (ids.some(id => !Number.isFinite(offsets[id]))) throw Error("Review a measured offset for every source before creating the sequence.");
  const alignment = syncSequenceAlignment(options, offsets), used = placementIds();
  for (const sq of S.proj.sequences) used.add(sq.id);
  const next = () => placementId(used), picture = media.filter(m => m.has_video), fps = alignment.fps;
  const sq = { id: next(), name: String(options.name || (kind === "merge" ? "Merged Clip" : "Multicam 01")),
    width: Math.max(...picture.map(m => m.width || 1920)), height: Math.max(...picture.map(m => m.height || 1080)), fps,
    timecode_format: "ndf", duration: null, markers: [], captions: [], tracks: [] };
  if (kind === "multicam") Object.assign(sq, { multicam: true, multicam_audio: options.audioMode === "follow" ? "follow" : "track" }); else sq.merged = true;
  media.forEach((m, i) => {
    const placement = alignment.placements[i], common = { media_id: ids[i], in_: 0, out: m.duration, speed: 1, keyframes: {} };
    if (m.has_video) sq.tracks.push({ id: "V" + (i + 1), kind: "video", index: i + 1, muted: false, locked: false, clips: [{ ...deep(common), id: next(), start: placement.picture_start, transform: { x: 0, y: 0, scale: 1, rotation: 0, opacity: 1 }, audio: { linked: false }, color: {} }] });
    if (m.has_audio && (kind === "multicam" || i === 1)) sq.tracks.push({ id: "A" + (kind === "merge" ? 1 : i + 1), kind: "audio", index: kind === "merge" ? 1 : i + 1, muted: kind === "multicam" && i !== media.findIndex(x => x.has_audio), locked: false, clips: [{ ...deep(common), id: next(), start: placement.audio_start, audio: { gain_db: 0 } }] });
  });
  if (kind === "multicam") sq.multicam_audio_track = sq.tracks.find(tr => tr.kind === "audio")?.id || null;
  return sq;
}
async function createSyncSequence(reviewed, options) {
  if (!canEdit()) throw Error("Wait for the current edit to finish before creating a sequence.");
  const settings = { kind: options.kind, name: options.name, sync: options.sync, audioMode: options.audioMode, media_ids: [...options.media_ids] }, current = options.isCurrent || (() => true);
  const identity = reviewed?.task?.id, owner = reviewed ? syncReviewedOwner(identity, reviewed, "media") :
    { project: S.proj, sequence: S.seq, context: { ...S.context }, mediaIds: settings.media_ids, requireAudio: false, snapshot: syncMediaSnapshot(settings.media_ids, false) };
  if (reviewed ? JSON.stringify(reviewed.result.media_ids) !== JSON.stringify(settings.media_ids) : settings.kind !== "multicam" || settings.sync !== "in") throw Error("Review the selected sources’ synchronization before creating this sequence.");
  if (!current() || !syncOwnerCurrent(owner)) throw Error("The source selection or dialog changed. Reopen sequence creation.");
  const sequence = buildSyncSequence(settings, reviewed ? reviewed.result.offsets : Object.fromEntries(settings.media_ids.map(id => [id, 0])));
  if (S.playing) togglePlay(false, { commitTrim: false });
  const basis = reviewed ? { ...reviewed.context, sequence: owner.sequence.id } : await withSavedProject(state => {
    if (!current() || !syncOwnerCurrent(owner)) throw Error("The source selection or project changed before sequence creation.");
    return { ...state.context, sequence: owner.sequence.id };
  });
  await workflowRequest(async context => {
    const check = () => { if (!current() || !syncOwnerCurrent(owner) || reviewed && JSON.stringify(reviewed) !== owner.envelope) { const error = Error("The source selection, review or dialog changed before sequence creation."); error.status = 409; throw error; } };
    check();
    if (reviewed) {
      // Recheck captured source files immediately before the guarded insertion.
      // The raw task is read-only; its Apply endpoint does not create sequences.
      let fresh;
      try { fresh = await api.json("POST", "/api/tasks/" + encodeURIComponent(identity) + "/sync", { _context: context }); }
      catch (error) { error.status = 409; throw error; } // Read-only validation failed; no insertion was dispatched.
      check();
      if (fresh?.ok !== true || fresh.task?.id !== identity || fresh.task.kind !== "sync" || fresh.task.status !== "ready" || !window.FilmocitySync.sameProject(context, fresh.context) || fresh.context?.revision !== context.revision || fresh.plan?.fingerprint !== reviewed.plan.fingerprint || JSON.stringify(fresh.result) !== JSON.stringify(reviewed.result)) { const error = Error("Synchronization changed before creation. Refresh and review the offsets again."); error.status = 409; throw error; }
    }
    check();
    return api.json("PATCH", "/api/project", { ops: [{ op: "insert", path: "/sequences/" + S.proj.sequences.length, value: sequence }], _context: context, actor: "human", client: CLIENT, tool: settings.kind === "merge" ? "merge_clips" : "multicam", reason: "create " + settings.kind + " from " + settings.media_ids.length + " reviewed sources" });
  }, basis);
  return sequence;
}
async function synchronizeSel() {
  try {
    const selected = selectedClips(); if (selected.length !== S.sel.size) throw Error("The synchronization selection contains missing clips. Select the clips again.");
    return await startSyncTask({ sequence: S.seq?.id, clip_ids: selected.map(x => x.c.id) });
  } catch (error) { status(error.message || String(error), "err"); return false; }
}
function finishPolygon() { const pts = (S.polyDraft || []).filter((p, i, a) => i === 0 || Math.hypot(p[0] - a[i - 1][0], p[1] - a[i - 1][1]) > 0.002); S.polyDraft = null; if (pts.length < 3) return; const tr = S.seq.tracks.filter(x => x.kind === "video").sort((a, b) => b.index - a.index)[0]; const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]); const c = { id: uid(), media_id: null, start: S.t, in_: 0, out: 4.0, speed: 1, graphic: { name: "Polygon", layers: [{ kind: "shape", shape: "polygon", points: pts, x: Math.min(...xs), y: Math.min(...ys), w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys), color: "#E8631C", opacity: 0.9 }] }, transform: { opacity: 1 }, keyframes: {} }; applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: c }], "shape", "polygon shape"); S.sel = new Set([c.id]); refreshSel(); CR.panels.render(); }
function reverseMatchFrame() {
  if (!canEdit() || !S.proj?.sequences.includes(S.seq)) return false;
  if (!S.src || S.proj.media[S.src.id] !== S.src) { status("Reload this project's source before matching a timeline frame.", "err"); return false; }
  try {
    const media = S.src, video = $("#srcVideo"), rate = mediaRate(media), frame = timing.displayFrame(video.currentTime, rate);
    if (!Number.isSafeInteger(frame) || frame < 0) throw Error("Choose a valid source frame before matching.");
    const begin = sourceLogicalTime(media, timing.fromFrames(frame, rate)), end = sourceLogicalTime(media, timing.fromFrames(frame + 1, rate));
    for (const tr of S.seq.tracks) for (const c of tr.clips) {
      if (c.media_id !== media.id) continue;
      const clock = { duration: clipDur(c), sourceOffset: t => sourceOffset(c, t), speedAt: t => speedAt(c, t) };
      let local;
      if (c.hold) {
        if (timing.displayFrame(Math.max(0, sourceMonitorTime(media, c.in_)), rate) !== frame) continue;
        local = window.FilmocitySourceClock.timelineTime(c, c.in_, clock);
      } else {
        const epsilon = Math.max(1e-10, Math.abs(c.out) * Number.EPSILON * 8);
        if (end <= c.in_ + epsilon || begin >= c.out - epsilon) continue;
        local = window.FilmocitySourceClock.timelineTime(c, c.reverse ? Math.min(c.out, end) : Math.max(c.in_, begin), clock);
      }
      if (local == null || local < 0 || local >= clock.duration) continue;
      if (navigateTo(c.start + local) === false) return false;
      video.pause?.(); S.sel = new Set([c.id]); refreshSel(); setFocus("timeline"); status(`Found on ${tr.id}`); return true;
    }
    status("This source frame is not used in the sequence."); return false;
  } catch (error) { status(error.message || String(error), "err"); return false; }
}
function distributeSel(axis) { const sel = selectedClips().filter(x => x.c.graphic && x.c.graphic.layers.length >= 3); if (sel.length !== 1) { status("Select one graphic with three or more layers."); return; } const { c, tr } = sel[0]; const g = deep(c.graphic); const L = g.layers.filter(l => l.w != null); if (L.length < 3) return; const k = axis === "h" ? "x" : "y", sz = axis === "h" ? "w" : "h"; L.sort((a, b) => a[k] - b[k]); const first = L[0][k], last = L[L.length - 1][k] + L[L.length - 1][sz]; const total = L.reduce((n, l) => n + l[sz], 0); const gap = (last - first - total) / (L.length - 1); let pos = first; for (const l of L) { l[k] = +pos.toFixed(4); pos += l[sz] + gap; } applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, graphic: g } }], "distribute", `distribute ${axis}`); }
function upgradeCaption(cp) { const tr = S.seq.tracks.filter(x => x.kind === "video").sort((a, b) => b.index - a.index)[0]; const cs = S.seq.caption_style || {}; const c = { id: uid(), media_id: null, start: cp.start, in_: 0, out: cp.end - cp.start, speed: 1, title: { text: cp.text, size: cs.size || Math.round(S.seq.height * 0.032), color: cs.color || "white", box: !!cs.box, borderw: cs.borderw == null ? 3 : cs.borderw, align: "center", valign: "bottom", y: -Math.round(S.seq.height * (0.88 - (cs.y == null ? 0.74 : cs.y))) }, transform: { opacity: 1 }, keyframes: {} }; const si = S.proj.sequences.indexOf(S.seq); applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: c }, { op: "set", path: `/sequences/${si}/captions`, value: (S.seq.captions || []).filter(x => x.id !== cp.id) }], "captions", "upgrade caption to text"); }
function renderEntireSequence() { return getRenderedPreview().start(); }
// inline text editing on the monitor (Type tool click on existing text, or double-click a text clip's frame)
function editTextInline(c, tr, ev) { const cv = $("#prgCanvas"), r = cv.getBoundingClientRect(); const box = document.createElement("div"); box.id = "textEdit"; box.contentEditable = "true"; const t = c.title; box.textContent = t.text || ""; const size = Math.max(14, (t.size || 60) * r.width / cv.width); box.style.font = `${t.weight === "regular" ? 400 : 700} ${size}px ${window.FilmocityProjectResources.family(t)}`; box.style.left = (ev ? ev.clientX : r.left + r.width / 2) + "px"; box.style.top = (ev ? ev.clientY - size : r.top + r.height / 2) + "px"; box.style.transform = "translate(-50%,0)"; document.body.appendChild(box); box.focus(); document.execCommand && document.execCommand("selectAll", false, null);
  const commit = () => { const txt = box.innerText.replace(/\n$/, ""); box.remove(); if (txt !== (t.text || "")) applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, title: { ...t, text: txt } } }], "type", `text: ${txt.slice(0, 40)}`); }; box.onblur = commit; box.onkeydown = e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); box.blur(); } if (e.key === "Escape") { box.textContent = t.text || ""; box.blur(); } e.stopPropagation(); }; }
function textClipAt() { for (const tr of [...S.seq.tracks].filter(t => t.kind === "video").sort((a, b) => b.index - a.index)) for (const c of tr.clips) if (c.title && S.t >= c.start && S.t < clipEnd(c)) return { c, tr }; return null; }
const AUDIO_CAPTURES = new WeakMap(), AUDIO_QUEUES = new WeakMap(), AUDIO_REVIEWS = new WeakMap();
function audioSourceSnapshot(ids) {
  const sources = {}, visiting = new Set();
  const visit = id => {
    if (visiting.has(id)) throw Error("An audio source has a circular subclip reference.");
    if (sources[id]) return;
    const media = S.proj?.media[id]; if (!media) throw Error("An audio source is missing from this project.");
    visiting.add(id); sources[id] = placementMediaIdentity(media);
    if (media.subclip_of) visit(media.subclip_of);
    visiting.delete(id);
  };
  ids.forEach(visit); return JSON.stringify(sources);
}
function audioNestedSnapshot(ids) {
  const sequences = {}, mediaIds = new Set(), visiting = new Set();
  const visit = id => {
    if (visiting.has(id)) throw Error("Nested audio contains a circular sequence reference.");
    if (sequences[id]) return;
    const matches = S.proj.sequences.filter(sequence => sequence.id === id);
    if (matches.length !== 1) throw Error("A nested audio sequence is missing or ambiguous.");
    const sequence = matches[0]; visiting.add(id); sequences[id] = sequence;
    for (const track of sequence.tracks) for (const clip of track.clips) { if (clip.media_id) mediaIds.add(clip.media_id); if (clip.sequence_id) visit(clip.sequence_id); }
    visiting.delete(id);
  };
  ids.forEach(visit); return { sequences, sources: audioSourceSnapshot([...mediaIds]) };
}
function audioTimelineTargets(ids, audible = false) {
  if (!S.proj?.sequences.includes(S.seq) || projectSaveState().error) throw Error("Resolve project loading or unsaved edits in Recovery before editing clip audio.");
  if (!Array.isArray(ids) || !ids.length || ids.length > 50 || ids.some(id => typeof id !== "string" || !id) || new Set(ids).size !== ids.length) throw Error("Select one to fifty clips with audio.");
  const targets = ids.map(id => {
    const matches = S.seq.tracks.flatMap(tr => tr.clips.filter(c => c.id === id).map(c => ({ c, tr })));
    if (matches.length !== 1) throw Error("An audio target is missing or ambiguous. Select the clips again.");
    const { c, tr } = matches[0], media = S.proj.media[c.media_id];
    if (tr.locked) throw Error("Unlock every selected clip track before editing audio.");
    if (!c.sequence_id && !media?.has_audio) throw Error("Every selected clip needs an available audio source.");
    if (media?.synthetic) throw Error("Render generated audio to media before using these gain controls.");
    if (audible && (c.enabled === false || c.hold || tr.kind === "video" && c.audio?.linked === false)) throw Error("Enable clip audio before analysis; held or unlinked picture audio cannot be measured.");
    validatePlacementClip(c, tr); return { c, tr };
  });
  return { targets, snapshot: JSON.stringify({ sequence: S.seq.id, fps: S.seq.fps,
    clips: targets.map(({ c, tr }) => ({ clip: c, track: tr.id, kind: tr.kind, locked: !!tr.locked })),
    media: audioSourceSnapshot([...new Set(targets.map(x => x.c.media_id).filter(Boolean))]),
    nested: audioNestedSnapshot([...new Set(targets.map(x => x.c.sequence_id).filter(Boolean))]) }) };
}
function audioOwnerCurrent(owner) {
  if (!owner || owner.project !== S.proj || owner.sequence !== S.seq || !window.FilmocitySync.sameProject(owner.context, S.context) || projectSaveState().error) return false;
  if (owner.requireSelection && (S.sel.size !== owner.ids.length || owner.ids.some(id => !S.sel.has(id)))) return false;
  try { return (owner.ids ? audioTimelineTargets(owner.ids).snapshot : audioSourceSnapshot(owner.mediaIds)) === owner.snapshot; } catch { return false; }
}
function captureAudioTargets(ids = [...S.sel]) {
  if (!canEdit()) throw Error("Wait for the current project action to finish before editing audio.");
  const chosen = [...ids], selected = audioTimelineTargets(chosen);
  if (S.sel.size !== chosen.length || chosen.some(id => !S.sel.has(id))) throw Error("Select these audio clips again before opening their gain controls.");
  const capture = { clip_ids: chosen, sequence: S.seq.id, names: selected.targets.map(({ c }) => c.name || S.proj.media[c.media_id]?.name || S.proj.sequences.find(sequence => sequence.id === c.sequence_id)?.name || c.id), gains: selected.targets.map(({ c }) => c.audio?.gain_db || 0) };
  AUDIO_CAPTURES.set(capture, { project: S.proj, sequence: S.seq, context: { ...S.context }, ids: chosen, requireSelection: true, snapshot: selected.snapshot, envelope: JSON.stringify(capture) });
  return capture;
}
function audioTargetsCurrent(capture) {
  const owner = capture && AUDIO_CAPTURES.get(capture);
  return !!owner && JSON.stringify(capture) === owner.envelope && audioOwnerCurrent(owner);
}
function audioCaptureOwner(capture, guard) {
  if (!audioTargetsCurrent(capture) || guard && !guard()) throw Error("The selected clips, sources or gain choices changed. Reopen the audio controls in the original project.");
  return AUDIO_CAPTURES.get(capture);
}
async function startAudioTask(mode, options = {}, capture = captureAudioTargets()) {
  const owner = audioCaptureOwner(capture, options.isCurrent); audioTimelineTargets(owner.ids, true);
  if (!["peak", "loudness", "beats"].includes(mode)) throw Error("Choose peak, loudness or estimated beat analysis.");
  const settings = mode === "beats" ? { every: options.every } : { target: options.target };
  if (mode === "beats" ? owner.ids.length !== 1 || !Number.isInteger(settings.every) || settings.every < 1 || settings.every > 128 : !Number.isFinite(settings.target) || settings.target < -40 || settings.target > 0) throw Error(mode === "beats" ? "Select one clip and an integer from 1 to 128 estimated beat candidates." : "Choose a normalization target from −40 to 0 dB or LUFS.");
  const requestId = crypto.randomUUID().replaceAll('-', ''), endpoint = { peak: "peak", loudness: "measure", beats: "beats" }[mode];
  return withSavedProject(async state => {
    audioCaptureOwner(capture, options.isCurrent); audioTimelineTargets(owner.ids, true);
    const context = { ...state.context }; let queued;
    try { queued = await api.json("POST", "/api/audio/" + endpoint, { _context: context, request_id: requestId, sequence: owner.sequence.id, clip_ids: [...owner.ids], ...settings, actor: "human", client: CLIENT }); }
    catch (error) {
      if (![400, 401, 403, 404, 409, 422, 501].includes(error.status)) throw Error("Audio analysis submission was not confirmed. Check Tasks before starting again. " + error.message);
      throw error;
    }
    if (queued?.ok !== true || queued.task?.kind !== "audio_analysis" || !queued.task.id || !window.FilmocitySync.sameProject(context, queued.context)) throw Error("Audio analysis submission was not confirmed. Check Tasks before starting again.");
    AUDIO_QUEUES.set(queued, { ...owner, mode, context });
    if (audioOwnerCurrent(owner) && (!options.isCurrent || options.isCurrent())) { if (options.openTasks !== false) window.FilmocityTasks?.open(); status("Clip audio analysis queued. Review measurements before applying any edit."); }
    return queued;
  });
}
async function waitAudioTask(queued, options = {}) {
  const owner = AUDIO_QUEUES.get(queued), current = () => audioOwnerCurrent(owner) && (!options.isCurrent || options.isCurrent());
  const deadline = Date.now() + 900000;
  while (Date.now() < deadline) {
    if (!current()) throw Error("The audio targets or dialog changed. Analysis remains available in Tasks.");
    const catalog = await api.get("/api/tasks");
    if (!current()) throw Error("The audio targets or dialog changed. Analysis remains available in Tasks.");
    if (!window.FilmocitySync.sameProject(owner.context, catalog.context) || !Array.isArray(catalog.tasks)) throw Error("Audio analysis status was not confirmed. Check Tasks before trying again.");
    const task = catalog.tasks.find(value => value.id === queued.task.id);
    if (!task || task.kind !== "audio_analysis" || !window.FilmocitySync.sameProject(owner.context, task.context)) throw Error("The audio analysis task is unavailable. Check Tasks before trying again.");
    if (task.status === "ready") {
      const reviewed = await reviewAudioTask(task.id);
      if (!current() || reviewed.result.mode !== owner.mode || JSON.stringify(reviewed.result.clip_ids) !== JSON.stringify(owner.ids)) throw Error("The audio targets or result changed while review loaded.");
      return reviewed;
    }
    if (!["queued", "running", "cancelling", "publishing"].includes(task.status)) throw Error("Audio analysis " + task.status + ": " + (task.message || "Inspect Tasks before starting again."));
    options.onProgress?.(task); await new Promise(resolve => setTimeout(resolve, 750));
  }
  throw Error("Audio analysis is still pending. Inspect Tasks; no edit was created or retried.");
}
async function reviewAudioTask(identity) {
  const basis = { ...S.context, sequence: S.seq?.id };
  return withSavedProject(async state => {
    if (!S.proj?.sequences.includes(S.seq) || !window.FilmocitySync.sameProject(basis, state.context) || basis.sequence !== S.seq.id) throw Error("The sequence changed. Review clip audio in the original project and sequence.");
    const project = S.proj, sequence = S.seq, revision = state.revision, context = { ...state.context }, snapshot = JSON.stringify({ sequence, media: project.media });
    const reviewed = await api.json("POST", "/api/tasks/" + encodeURIComponent(identity) + "/audio", { _context: context });
    if (state !== projectSaveState() || state.revision !== revision || S.proj !== project || S.seq !== sequence || JSON.stringify({ sequence: S.seq, media: S.proj.media }) !== snapshot || state.error || !window.FilmocitySync.sameProject(context, S.context)) throw Error("The edit or sources changed while audio review loaded. Review it again.");
    const result = reviewed?.result, plan = reviewed?.plan;
    if (reviewed?.ok !== true || reviewed.task?.id !== identity || reviewed.task.kind !== "audio_analysis" || reviewed.task.status !== "ready" || reviewed.context?.revision !== context.revision || !window.FilmocitySync.sameProject(context, reviewed.context) || result?.version !== 1 || result.kind !== "audio_analysis" || result.clock !== "clip-local" || !["timeline", "media"].includes(result.scope) || !["peak", "loudness", "beats"].includes(result.mode) || !plan?.summary || !Array.isArray(plan.ops) || typeof plan.fingerprint !== "string" || !plan.fingerprint) throw Error("The clip audio review was not confirmed for this edit.");
    const timeline = result.scope === "timeline", ids = result.clip_ids, values = result.measurements;
    if (!Array.isArray(ids) || (timeline ? !ids.length || ids.length > 50 || result.sequence !== sequence.id : ids.length || result.sequence != null || plan.ops.length) || !Array.isArray(values) || values.length !== (timeline ? ids.length : 1) || result.mode === "beats" && values.length !== 1) throw Error("Audio analysis returned invalid or different targets.");
    const targets = timeline ? audioTimelineTargets(ids, true) : null;
    for (let i = 0; i < values.length; i++) {
      const value = values[i];
      if (!value || value.id !== (timeline ? ids[i] : value.media_id) || (timeline ? (targets.targets[i].c.media_id || null) !== value.media_id : !project.media[value.media_id]) || !Number.isFinite(value.duration) || value.duration <= 0 || value.duration > 600 + 1e-6 || value.sample_rate !== 48000 || value.channels !== 2 || typeof value.silent !== "boolean" || !Array.isArray(value.beats) || value.beats.length > 120000 || value.beats.some(t => !Number.isFinite(t) || t < 0 || t >= value.duration) || !Number.isFinite(value.resolution) || value.resolution <= 0 || !Number.isFinite(value.tempo_confidence) || value.tempo_confidence < 0 || value.tempo_confidence > 1 || !Array.isArray(value.warnings) || typeof value.method !== "string" || ["peak_db", "rms_db", "integrated_lufs", "true_peak_dbtp", "loudness_range_lu", "bpm"].some(key => value[key] !== null && !Number.isFinite(value[key]))) throw Error("Audio analysis returned incomplete measurements. No silent or failed result can become a zero measurement.");
    }
    const gains = plan.summary.gains, markers = plan.summary.markers;
    if (!Array.isArray(gains) || !Array.isArray(markers) || !Array.isArray(plan.summary.warnings) || gains.length > 50 || markers.length > 10000 || gains.some(gain => !gain || !ids.includes(gain.clip_id) || [gain.measured, gain.target, gain.delta_db, gain.from_db, gain.to_db].some(value => !Number.isFinite(value)) || !Number.isInteger(gain.automation_points) || gain.automation_points < 0) || markers.some(marker => !marker || typeof marker.id !== "string" || !Number.isFinite(marker.time) || marker.time < 0)) throw Error("The audio review contains incomplete proposed changes.");
    AUDIO_REVIEWS.set(reviewed, { project, sequence, context, ids: timeline ? [...ids] : null, mediaIds: values.map(value => value.media_id).filter(Boolean), requireSelection: false,
      snapshot: timeline ? targets.snapshot : audioSourceSnapshot(values.map(value => value.media_id).filter(Boolean)), envelope: JSON.stringify(reviewed) });
    return reviewed;
  });
}
function audioReviewedOwner(identity, reviewed, guard) {
  const owner = reviewed && AUDIO_REVIEWS.get(reviewed);
  if (!owner?.ids || reviewed.task?.id !== identity || !audioOwnerCurrent(owner) || JSON.stringify(reviewed) !== owner.envelope || guard && !guard()) throw Error("The clips, sources or audio review changed. Refresh the review before applying it.");
  if (!reviewed.plan.ops.length) throw Error("This audio review has no changes to apply.");
  return owner;
}
function applyAudioTask(identity, reviewed, options = {}) {
  let owner; try { owner = audioReviewedOwner(identity, reviewed, options.isCurrent); } catch (error) { return Promise.reject(error); }
  if (S.playing) togglePlay(false, { commitTrim: false });
  return workflowRequest(context => {
    try { audioReviewedOwner(identity, reviewed, options.isCurrent); } catch (error) { error.status = 409; throw error; }
    return api.json("POST", "/api/tasks/" + encodeURIComponent(identity) + "/apply", { _context: context, fingerprint: reviewed.plan.fingerprint, actor: "human", client: CLIENT });
  }, { ...reviewed.context, sequence: owner.sequence.id });
}
async function applyManualGain(capture, mode, value, options = {}) {
  const owner = audioCaptureOwner(capture, options.isCurrent);
  if (!["set", "adjust"].includes(mode) || !Number.isFinite(value) || mode === "set" && (value < -40 || value > 24)) throw Error("Choose a finite gain adjustment or a base gain from −40 to +24 dB.");
  const basis = await withSavedProject(state => { audioCaptureOwner(capture, options.isCurrent); return { ...state.context, sequence: owner.sequence.id }; });
  if (S.playing) togglePlay(false, { commitTrim: false });
  return workflowRequest(context => {
    try { audioCaptureOwner(capture, options.isCurrent); } catch (error) { error.status = 409; throw error; }
    return api.json("POST", "/api/audio/gain", { _context: context, sequence: owner.sequence.id, clip_ids: [...owner.ids], mode, value, actor: "human", client: CLIENT });
  }, basis);
}
async function beatMarkers() {
  try {
    const capture = captureAudioTargets(); if (capture.clip_ids.length !== 1) throw Error("Select one music clip on the timeline.");
    const answer = prompt("Add a marker every N estimated beat candidates (1 = every candidate). These are measured onsets, not guaranteed beats or bars:", "1");
    if (answer === null || !String(answer).trim()) return false;
    await startAudioTask("beats", { every: Number(answer) }, capture); return true;
  } catch (error) { status(error.message || String(error), "err"); return false; }
}
const RECIPE_CAPTURES = new WeakMap(), RECIPE_QUEUES = new WeakMap(), RECIPE_REVIEWS = new WeakMap();
function recipeSnapshot(mediaIds) {
  if (!S.proj?.sequences.includes(S.seq) || projectSaveState().error) throw Error("Resolve project loading or unsaved edits in Recovery before preparing a recipe.");
  const nested = [...new Set(S.seq.tracks.flatMap(track => track.clips.map(clip => clip.sequence_id).filter(Boolean)))];
  return JSON.stringify({ sequence: S.seq, brand: S.proj.brand || {}, media: audioSourceSnapshot(mediaIds), nested: audioNestedSnapshot(nested) });
}
function recipeOwnerCurrent(owner) {
  if (!owner || owner.project !== S.proj || owner.sequence !== S.seq || !window.FilmocitySync.sameProject(owner.context, S.context) || projectSaveState().error) return false;
  if (owner.selection && JSON.stringify([...S.sel]) !== owner.selection || owner.binSelection && JSON.stringify([...S.binSel]) !== owner.binSelection) return false;
  try { return recipeSnapshot(owner.mediaIds) === owner.snapshot; } catch { return false; }
}
function captureRecipeTargets(mode) {
  if (!canEdit() || !["talking_head", "reel", "explainer", "variants", "cover"].includes(mode)) throw Error("Wait for the current action to finish before preparing a recipe.");
  const selected = selectedClips(), bin = [...S.binSel], pictures = bin.filter(id => S.proj.media[id]?.has_video || S.proj.media[id]?.is_image);
  let clip = null;
  if (mode === "talking_head") {
    if (S.sel.size !== 1 || selected.length !== 1) throw Error("Select exactly one source clip with audio for Talking Head.");
    const target = selected[0]; clip = target.c;
    if (target.tr.locked || clip.enabled === false) throw Error("Enable the selected clip and unlock its track before preparing Talking Head.");
    if (clip.hold || clip.sequence_id || !S.proj.media[clip.media_id]?.has_audio) throw Error("Talking Head needs an ordinary audio source clip; held frames and nested sequences are not supported.");
    validatePlacementClip(clip, target.tr);
  } else if (mode === "reel" && (!pictures.length || pictures.length > 100)) throw Error("Select one to one hundred picture sources in the bin, in the desired shot order.");
  const broll = mode === "talking_head" ? pictures.filter(id => id !== clip.media_id) : [], music = Object.values(S.proj.media).filter(media => media.has_audio && !media.has_video && !media.sfx);
  if (broll.length > 100) throw Error("Choose no more than one hundred B-roll sources.");
  const mediaIds = [...new Set([...pictures, ...music.map(media => media.id), ...S.seq.tracks.flatMap(track => track.clips.map(value => value.media_id).filter(Boolean))])];
  let textTargets = mode === "variants" ? S.seq.tracks.flatMap(track => track.clips.flatMap(value => {
    const label = value.name || value.graphic?.name || value.id, choices = [];
    if (value.title && typeof value.title.text === "string") choices.push({ target: { clip_id: value.id, title: true }, label: `${track.id} · ${label} · Title: ${value.title.text}`, locked: !!track.locked });
    for (const [layer, item] of (value.graphic?.layers || []).entries()) if (item.kind === "text" && typeof item.text === "string") choices.push({ target: { clip_id: value.id, layer }, label: `${track.id} · ${label} · Layer ${layer + 1}: ${item.text}`, locked: !!track.locked });
    return choices;
  })) : [];
  if (mode === "variants" && textTargets.length > 500) {
    textTargets = textTargets.filter(choice => S.sel.has(choice.target.clip_id));
    if (!textTargets.length || textTargets.length > 500) throw Error("Select the text clips to narrow this catalog to at most 500 text layers, then reopen Hook Variants. No targets were truncated.");
  }
  if (mode === "variants" && !textTargets.length) throw Error("Add a title or graphic text layer before creating Hook Variants.");
  const capture = { mode, sequence: S.seq.id, sequence_name: S.seq.name || S.seq.id, duration: seqDur(), time: S.t, text_targets: textTargets, clip_id: clip?.id || null, shots: mode === "reel" ? pictures : [], broll,
    names: Object.fromEntries(mediaIds.map(id => [id, S.proj.media[id]?.name || id])), music: music.map(media => media.id),
    clip_name: clip && (clip.name || S.proj.media[clip.media_id]?.name || clip.id), fps: S.seq.fps, width: S.seq.width, height: S.seq.height };
  RECIPE_CAPTURES.set(capture, { project: S.proj, sequence: S.seq, context: { ...S.context }, mode, mediaIds,
    selection: ["talking_head", "variants"].includes(mode) ? JSON.stringify([...S.sel]) : null, binSelection: ["talking_head", "reel"].includes(mode) ? JSON.stringify(bin) : null, snapshot: recipeSnapshot(mediaIds), envelope: JSON.stringify(capture) });
  return capture;
}
function recipeTargetsCurrent(capture) {
  const owner = capture && RECIPE_CAPTURES.get(capture);
  return !!owner && JSON.stringify(capture) === owner.envelope && recipeOwnerCurrent(owner);
}
function recipeCaptureOwner(capture, guard) {
  if (!recipeTargetsCurrent(capture) || guard && !guard()) throw Error("The recipe targets, project or choices changed. Reopen the recipe controls in the original project.");
  return RECIPE_CAPTURES.get(capture);
}
function recipeSettings(mode, options, capture) {
  const bool = (value, label) => { if (typeof value !== "boolean") throw Error("Choose whether to include " + label + "."); return value; };
  if (mode === "talking_head") {
    const interval = options.punch_every;
    if (!Number.isFinite(interval) || interval < 0 || interval > 600 || interval > 0 && interval < timing.fromFrames(1, capture.fps) - 1e-10) throw Error("Punch-ins must be disabled (0) or spaced between one sequence frame and 600 seconds apart.");
    const target = selectedClips()[0];
    if (interval > 0 && (target.tr.kind !== "video" || !S.proj.media[target.c.media_id]?.has_video)) throw Error("Punch-ins need a picture clip on a video track; set the interval to 0 for audio-only material.");
    if (target.tr.kind === "video" && target.c.audio?.linked === false && (options.silences || options.voice_preset)) throw Error("Relink picture audio or disable silence removal and Voice Clean-up before preparing this recipe.");
    const broll = options.broll || [];
    if (!Array.isArray(broll) || broll.length > 100 || new Set(broll).size !== broll.length || broll.some(id => !capture.broll.includes(id))) throw Error("Choose B-roll from the originally selected bin pictures.");
    const threshold_db = options.threshold_db ?? -38, min_gap = options.min_gap ?? .45, pad = options.pad ?? .08;
    if (!Number.isFinite(threshold_db) || threshold_db < -120 || threshold_db > 0 || !Number.isFinite(min_gap) || min_gap < 0 || min_gap > 60 || !Number.isFinite(pad) || pad < 0 || pad > 10) throw Error("Choose a silence threshold from −120 to 0 dB, minimum gap from 0 to 60 seconds and padding from 0 to 10 seconds.");
    return { clip_id: capture.clip_id, broll: [...broll], punch_every: interval, threshold_db, min_gap, pad, silences: bool(options.silences, "silence removal"), voice_preset: bool(options.voice_preset, "Voice Clean-up"), captions: bool(options.captions, "transcript captions") };
  }
  if (mode === "explainer" || mode === "variants") {
    const text = (value, max, label, required = false) => { if (typeof value !== "string" || value.length > max || required && !value.trim()) throw Error(`Choose ${label} within ${max} characters.`); return value; };
    if (mode === "explainer") {
      const seconds = (value, label, duration = false) => { if (!Number.isFinite(value) || value < 0 || duration && (value <= 0 || value > 60)) throw Error(`${label} must be ${duration ? "greater than 0 and at most 60" : "a nonnegative finite number of"} seconds.`); return value; };
      let lower_third = null;
      if (options.lower_third != null) { const value = options.lower_third; lower_third = { name: text(value.name, 200, "the lower-third name", true), role: text(value.role, 200, "the lower-third role"), at: seconds(value.at, "Lower-third start"), duration: seconds(value.duration, "Lower-third duration", true) }; }
      return { lower_third, chapters: bool(options.chapters, "chapter cards"), chapter_duration: seconds(options.chapter_duration, "Chapter duration", true), end_card: text(options.end_card, 500, "end-card text"), end_duration: seconds(options.end_duration, "End-card duration", true) };
    }
    const hooks = options.hooks, targets = options.targets, name_prefix = text(options.name_prefix, 120, "the sequence-name prefix", true);
    if (!Array.isArray(hooks) || !hooks.length || hooks.length > 20) throw Error("Enter one to twenty hook headlines.");
    for (const hook of hooks) text(hook, 500, "each hook", true);
    if (!Array.isArray(targets) || !targets.length || targets.length > 100 || new Set(targets.map(value => JSON.stringify(value))).size !== targets.length) throw Error("Explicitly select one to one hundred distinct text targets.");
    const canonical = targets.map(target => {
      if (!target || typeof target.clip_id !== "string" || target.title !== true && (!Number.isInteger(target.layer) || target.layer < 0) || target.title === true && target.layer !== undefined) throw Error("Choose an explicit title or graphic layer target.");
      const value = target.title === true ? { clip_id: target.clip_id, title: true } : { clip_id: target.clip_id, layer: target.layer };
      const match = capture.text_targets.find(choice => JSON.stringify(choice.target) === JSON.stringify(value));
      if (!match || match.locked) throw Error("Choose an unlocked captured title or graphic text layer. Other text is preserved."); return value;
    });
    if (new Set(canonical.map(value => JSON.stringify(value))).size !== canonical.length) throw Error("Choose distinct text targets.");
    return { hooks: [...hooks], targets: canonical, name_prefix };
  }
  const name = String(options.name || "").trim(), target = options.target;
  if (!name || name.length > 120 || !Number.isFinite(target) || target < 1 || target > 600) throw Error("Name the new sequence (1–120 characters) and choose a finite duration from 1 to 600 seconds.");
  if (!["portrait", "landscape", "current"].includes(options.canvas) || !["even", "onsets"].includes(options.rhythm) || !["auto", "cover", "contain", "blur_fill"].includes(options.framing || "auto")) throw Error("Choose the new sequence canvas and cut timing.");
  const music = options.music || null;
  if (music && !capture.music.includes(music) || options.rhythm === "onsets" && !music) throw Error("Choose a captured music source for measured onset timing, or choose evenly spaced cuts.");
  const text = (value, limit) => { if (typeof value !== "string" || value.length > limit) throw Error("Recipe text exceeds its allowed length."); return value; };
  if (options.look != null && typeof options.look !== "string" || options.caption_style != null && (typeof options.caption_style !== "object" || Array.isArray(options.caption_style))) throw Error("Choose a valid look and caption style.");
  return { shots: [...capture.shots], name, canvas: options.canvas, framing: options.framing || "auto", rhythm: options.rhythm, music, target,
    hook: text(options.hook, 500), hook_sub: text(options.hook_sub, 1000), cta: text(options.cta, 500), look: options.look || null,
    captions: bool(options.captions, "hook captions"), caption_style: options.caption_style ? deep(options.caption_style) : null, sfx: bool(options.sfx, "cut sound effects") };
}
async function startRecipeTask(mode, options = {}, capture = captureRecipeTargets(mode)) {
  const owner = recipeCaptureOwner(capture, options.isCurrent);
  if (capture.mode !== mode) throw Error("The recipe mode changed. Reopen its controls.");
  const choices = recipeSettings(mode, options, capture), requestId = crypto.randomUUID().replaceAll('-', '');
  return withSavedProject(async state => {
    recipeCaptureOwner(capture, options.isCurrent);
    const context = { ...state.context }; let queued;
    try { queued = await api.json("POST", (mode === "variants" ? "/api/sequences/variants" : "/api/recipes/" + mode), { ...choices, sequence: owner.sequence.id, _context: context, request_id: requestId, actor: "human", client: CLIENT }); }
    catch (error) { if (![400, 401, 403, 404, 409, 422, 501].includes(error.status)) throw Error("Recipe submission was not confirmed. Check Tasks before starting again. " + error.message); throw error; }
    if (queued?.ok !== true || queued.task?.kind !== "recipe" || !queued.task.id || !window.FilmocitySync.sameProject(context, queued.context)) throw Error("Recipe submission was not confirmed. Check Tasks before starting again.");
    RECIPE_QUEUES.set(queued, { ...owner, context, mode, taskId: queued.task.id });
    if (recipeOwnerCurrent(owner) && (!options.isCurrent || options.isCurrent())) { if (options.openTasks !== false) window.FilmocityTasks?.open(); status("Recipe queued. Review the proposed edit before applying it."); }
    return queued;
  });
}
async function waitRecipeTask(queued, options = {}) {
  const owner = RECIPE_QUEUES.get(queued), current = () => recipeOwnerCurrent(owner) && (!options.isCurrent || options.isCurrent());
  const deadline = Date.now() + 900000;
  while (Date.now() < deadline) {
    if (!current()) throw Error("The recipe targets or dialog changed. The queued recipe remains in Tasks.");
    const catalog = await api.get("/api/tasks");
    if (!current()) throw Error("The recipe targets or dialog changed. The queued recipe remains in Tasks.");
    if (!window.FilmocitySync.sameProject(owner.context, catalog.context) || !Array.isArray(catalog.tasks)) throw Error("Recipe status was not confirmed. Check Tasks before trying again.");
    const task = catalog.tasks.find(value => value.id === owner.taskId);
    if (!task || task.kind !== "recipe" || !window.FilmocitySync.sameProject(owner.context, task.context)) throw Error("The recipe task is unavailable. Check Tasks before trying again.");
    if (task.status === "ready") {
      const reviewed = await reviewRecipeTask(task.id);
      if (!current() || reviewed.result.mode !== owner.mode) throw Error("The recipe or its targets changed while review loaded.");
      return reviewed;
    }
    if (!["queued", "running", "cancelling", "publishing"].includes(task.status)) throw Error("Recipe " + task.status + ": " + (task.message || "Inspect Tasks before starting again."));
    options.onProgress?.(task); await new Promise(resolve => setTimeout(resolve, 750));
  }
  throw Error("The recipe is still pending. Inspect Tasks; no edit was applied or retried.");
}
async function reviewRecipeTask(identity) {
  const basis = { ...S.context, sequence: S.seq?.id };
  return withSavedProject(async state => {
    if (!S.proj?.sequences.includes(S.seq) || !window.FilmocitySync.sameProject(basis, state.context) || basis.sequence !== S.seq.id) throw Error("The sequence changed. Review the recipe in its original project and sequence.");
    const owner = { project: S.proj, sequence: S.seq, context: { ...state.context }, mediaIds: Object.keys(S.proj.media) }, revision = state.revision;
    owner.snapshot = recipeSnapshot(owner.mediaIds);
    const reviewed = await api.json("POST", "/api/tasks/" + encodeURIComponent(identity) + "/recipe", { _context: owner.context });
    if (state !== projectSaveState() || state.revision !== revision || !recipeOwnerCurrent(owner)) throw Error("The edit or sources changed while recipe review loaded. Review it again.");
    const result = reviewed?.result, plan = reviewed?.plan;
    if (reviewed?.ok !== true || reviewed.task?.id !== identity || reviewed.task.kind !== "recipe" || reviewed.task.status !== "ready" || reviewed.context?.revision !== owner.context.revision || !window.FilmocitySync.sameProject(owner.context, reviewed.context) || result?.version !== 1 || result.kind !== "recipe" || !["talking_head", "reel", "explainer", "variants"].includes(result.mode) || result.sequence !== owner.sequence.id || !plan?.summary || !Array.isArray(plan.ops) || typeof plan.fingerprint !== "string" || !plan.fingerprint) throw Error("The recipe review was not confirmed for this edit.");
    const summary = plan.summary;
    if (summary.kind !== "recipe" || summary.mode !== result.mode || !Array.isArray(summary.warnings) || !Array.isArray(summary.tracks) || !Array.isArray(summary.cuts) || !Array.isArray(summary.ranges) || !Array.isArray(summary.affected_fields) || !Number.isFinite(summary.achieved) || summary.achieved < 0 || summary.cuts.some(value => !Number.isFinite(value) || value < 0) || summary.ranges.some(range => !Array.isArray(range) || range.length !== 2 || !range.every(Number.isFinite) || range[0] < 0 || range[1] < range[0])) throw Error("The recipe review contains incomplete positions or edit scope.");
    if (["explainer", "variants"].includes(result.mode) && (!Array.isArray(summary.placements) || !Array.isArray(summary.variants) || summary.placements.length > 258 || summary.variants.length > 20 || summary.placements.some(value => !value || !Number.isFinite(value.start) || !Number.isFinite(value.end) || value.start < 0 || value.end <= value.start || typeof value.track !== "string") || summary.variants.some(value => !value || typeof value.id !== "string" || typeof value.name !== "string" || typeof value.hook !== "string" || !Number.isInteger(value.replaced) || value.replaced < 1))) throw Error("The recipe review contains incomplete cards or variant targets.");
  RECIPE_REVIEWS.set(reviewed, { ...owner, envelope: JSON.stringify(reviewed) }); return reviewed;
  });
}
function recipeReviewLines(reviewed) {
  const summary = reviewed.plan?.summary || {}, lines = [summary.message || "Review the proposed recipe edit."];
  if (summary.sequence_name || summary.name) lines.push(`${["reel", "variants"].includes(summary.mode) ? "New sequence" : "Sequence"}: ${summary.sequence_name || summary.name}`);
  if (summary.canvas && Number.isFinite(summary.canvas.width) && Number.isFinite(summary.canvas.height)) lines.push(`Canvas ${summary.canvas.width} × ${summary.canvas.height} · ${summary.canvas.fps} fps`);
  if (Number.isFinite(summary.requested)) lines.push(`Requested ${summary.requested.toFixed(6)} s`);
  if (Number.isFinite(summary.achieved)) lines.push(`Planned duration ${summary.achieved.toFixed(6)} s`);
  if (Number.isFinite(summary.removed_duration)) lines.push(`Silence removed ${summary.removed_duration.toFixed(6)} s`);
  for (const [field, label] of [["pieces", "Speech pieces"], ["shots", "Shots"], ["broll", "B-roll clips"], ["captions", "Caption cues"], ["music_segments", "Music pieces"], ["onset_count", "Cuts placed on measured onsets"]]) if (Number.isInteger(summary[field])) lines.push(`${label}: ${summary[field]}`);
  if (Number.isInteger(summary.cards)) lines.push(`New cards: ${summary.cards}`);
  for (const placement of (summary.placements || []).slice(0,50)) lines.push(`Card: ${placement.kind} · ${placement.start.toFixed(6)} – ${placement.end.toFixed(6)} s · ${placement.track}${typeof placement.text === "string" ? " · " + placement.text : ""}${placement.sub ? " · " + placement.sub : ""}`);
  if ((summary.placements?.length || 0) > 50) lines.push(`${summary.placements.length - 50} more card positions in the recipe review API response.`);
  for (const target of (summary.targets || []).slice(0,100)) lines.push(`Text target: ${target.clip_id} · ${target.title === true ? "Title" : "Layer " + (target.layer + 1)}`);
  for (const variant of (summary.variants || []).slice(0,20)) lines.push(`Variant: ${variant.name} · ${variant.hook} · ${variant.replaced} text target(s)`);
  if (Number.isFinite(summary.music_coverage)) lines.push(`Music coverage ${summary.music_coverage.toFixed(6)} s`);
  if (Number.isFinite(summary.tempo_confidence)) lines.push(`Onset regularity score: ${summary.tempo_confidence.toFixed(3)} / 1. This score does not establish musical structure or guarantee edit quality.`);
  for (const transition of (summary.transitions || []).slice(0,50)) if (Number.isFinite(transition.at) && Number.isFinite(transition.duration)) lines.push(`Transition: ${transition.at.toFixed(6)} s · ${transition.type} · ${transition.align} · ${transition.duration.toFixed(6)} s`);
  if ((summary.transitions?.length || 0) > 50) lines.push(`${summary.transitions.length - 50} more transitions in the recipe review API response.`);
  if (Array.isArray(summary.tracks) && summary.tracks.length) lines.push("Affected tracks: " + summary.tracks.slice(0,50).join(", "));
  if (Array.isArray(summary.affected_fields) && summary.affected_fields.length) lines.push("Changed fields: " + summary.affected_fields.slice(0,50).join(", "));
  for (const at of (summary.cuts || []).slice(0,50)) if (Number.isFinite(at)) lines.push(`Cut: ${at.toFixed(6)} s`);
  for (const range of (summary.ranges || []).slice(0,50)) if (Array.isArray(range) && range.length === 2 && range.every(Number.isFinite)) lines.push(`Range: ${range[0].toFixed(6)} – ${range[1].toFixed(6)} s`);
  const remaining = Math.max(0, (summary.cuts?.length || 0) - 50) + Math.max(0, (summary.ranges?.length || 0) - 50);
  if (remaining) lines.push(`${remaining} more positions in the recipe review API response.`);
  const warnings = [...new Set(summary.warnings || [])], visibleWarnings = warnings.length > 50 ? [...warnings.slice(0,40), ...warnings.slice(-10)] : warnings;
  for (const warning of visibleWarnings) lines.push(String(warning));
  if (warnings.length > visibleWarnings.length) lines.push(`${warnings.length - visibleWarnings.length} more warnings in the recipe review API response. Review the full plan before applying.`);
  lines.push("This is a planned edit. Verify the resulting playback after applying.");
  if (!reviewed.plan?.ops?.length) lines.push("No changes are needed.");
  return lines;
}
function recipeReviewedOwner(identity, reviewed, guard) {
  const owner = reviewed && RECIPE_REVIEWS.get(reviewed);
  if (!owner || reviewed.task?.id !== identity || !recipeOwnerCurrent(owner) || JSON.stringify(reviewed) !== owner.envelope || guard && !guard()) throw Error("The recipe review, sources or choices changed. Refresh the review before applying it.");
  if (!reviewed.plan.ops.length) throw Error("This recipe contains no changes to apply.");
  return owner;
}
function applyRecipeTask(identity, reviewed, options = {}) {
  let owner; try { owner = recipeReviewedOwner(identity, reviewed, options.isCurrent); } catch (error) { return Promise.reject(error); }
  if (S.playing) togglePlay(false, { commitTrim: false });
  return workflowRequest(context => {
    try { recipeReviewedOwner(identity, reviewed, options.isCurrent); } catch (error) { error.status = 409; throw error; }
    return api.json("POST", "/api/tasks/" + encodeURIComponent(identity) + "/apply", { _context: context, fingerprint: reviewed.plan.fingerprint, actor: "human", client: CLIENT });
  }, { ...reviewed.context, sequence: owner.sequence.id });
}
const COVER_QUEUES = new WeakMap(), COVER_REVIEWS = new WeakMap();
function coverArtifactUrl(identity, index, context) {
  const encode = value => encodeURIComponent(value).replace(/[!'()*]/g, character => "%" + character.charCodeAt(0).toString(16).toUpperCase()).replace(/%20/g, "+");
  return "/api/tasks/" + encodeURIComponent(identity) + "/cover/" + index + "?workspace=" + encode(context.workspace) + "&project=" + encode(context.project);
}
function coverSettings(options, capture) {
  const { time, headline, sub, sizes, framing, template } = options;
  if (!Number.isFinite(time) || time < 0 || time >= capture.duration) throw Error("Choose a finite frame time inside the captured sequence.");
  if (typeof headline !== "string" || headline.length > 500 || typeof sub !== "string" || sub.length > 1000) throw Error("Use up to 500 headline and 1000 sub-line characters; leave both empty for a frame-only cover.");
  if (!["cover", "contain", "blur_fill"].includes(framing) || typeof template !== "string" || !template || template.length > 120) throw Error("Choose a framing mode and captured cover template.");
  if (!Array.isArray(sizes) || !sizes.length || sizes.length > 4 || sizes.some(size => !Array.isArray(size) || size.length !== 2 || size.some(value => !Number.isInteger(value) || value < 16 || value > 4096)) || new Set(sizes.map(size => size.join("x"))).size !== sizes.length || sizes.reduce((sum, size) => sum + size[0] * size[1], 0) > 32 * 1024 * 1024) throw Error("Choose one to four different output sizes, 16–4096 pixels per dimension, within 32 megapixels total.");
  return { time, headline, sub, sizes: deep(sizes), framing, template };
}
async function startCoverTask(options = {}, capture = captureRecipeTargets("cover")) {
  const owner = recipeCaptureOwner(capture, options.isCurrent);
  if (capture.mode !== "cover") throw Error("The cover capture changed. Reopen Cover.");
  const choices = coverSettings(options, capture), requestId = crypto.randomUUID().replaceAll('-', '');
  return withSavedProject(async state => {
    recipeCaptureOwner(capture, options.isCurrent); const context = { ...state.context }; let queued;
    try { queued = await api.json("POST", "/api/recipes/cover", { ...choices, sequence: owner.sequence.id, _context: context, request_id: requestId, actor: "human", client: CLIENT }); }
    catch (error) { if (![400,401,403,404,409,422,501].includes(error.status)) throw Error("Cover submission was not confirmed. Check Tasks before starting again. " + error.message); throw error; }
    if (queued?.ok !== true || queued.task?.kind !== "cover" || !queued.task.id || !window.FilmocitySync.sameProject(context, queued.context)) throw Error("Cover submission was not confirmed. Check Tasks before starting again.");
    COVER_QUEUES.set(queued, { ...owner, context, taskId: queued.task.id });
    if (recipeOwnerCurrent(owner) && (!options.isCurrent || options.isCurrent()) && options.openTasks !== false) window.FilmocityTasks?.open();
    return queued;
  });
}
async function waitCoverTask(queued, options = {}) {
  const owner = COVER_QUEUES.get(queued), current = () => recipeOwnerCurrent(owner) && (!options.isCurrent || options.isCurrent()), deadline = Date.now() + 900000;
  while (Date.now() < deadline) {
    if (!current()) throw Error("The cover targets or dialog changed. The queued task remains in Tasks.");
    const catalog = await api.get("/api/tasks");
    if (!current()) throw Error("The cover targets or dialog changed. The queued task remains in Tasks.");
    if (!window.FilmocitySync.sameProject(owner.context, catalog.context) || !Array.isArray(catalog.tasks)) throw Error("Cover status was not confirmed. Check Tasks before trying again.");
    const task = catalog.tasks.find(value => value.id === owner.taskId);
    if (!task || task.kind !== "cover" || !window.FilmocitySync.sameProject(owner.context, task.context)) throw Error("The cover task is unavailable. Check Tasks before trying again.");
    if (task.status === "ready") { const reviewed = await reviewCoverTask(task.id); if (!current()) throw Error("The cover targets changed while review loaded."); return reviewed; }
    if (!["queued","running","cancelling","publishing"].includes(task.status)) throw Error("Cover " + task.status + ": " + (task.message || "Inspect Tasks before starting again."));
    options.onProgress?.(task); await new Promise(resolve => setTimeout(resolve, 750));
  }
  throw Error("The cover is still pending. Inspect Tasks; no request was retried.");
}
async function reviewCoverTask(identity) {
  const basis = { ...S.context, sequence: S.seq?.id };
  return withSavedProject(async state => {
    if (!S.proj?.sequences.includes(S.seq) || !window.FilmocitySync.sameProject(basis, state.context) || basis.sequence !== S.seq.id) throw Error("Review the cover in its original project and sequence.");
    const owner = { project: S.proj, sequence: S.seq, context: { ...state.context }, mediaIds: Object.keys(S.proj.media) }, revision = state.revision;
    owner.snapshot = recipeSnapshot(owner.mediaIds);
    const reviewed = await api.json("POST", "/api/tasks/" + encodeURIComponent(identity) + "/cover", { _context: owner.context });
    if (state !== projectSaveState() || state.revision !== revision || !recipeOwnerCurrent(owner)) throw Error("The sequence or its sources changed while cover review loaded.");
    const result = reviewed?.result, plan = reviewed?.plan;
    if (reviewed?.ok !== true || reviewed.task?.id !== identity || reviewed.task.kind !== "cover" || reviewed.task.status !== "ready" || reviewed.context?.revision !== owner.context.revision || !window.FilmocitySync.sameProject(owner.context, reviewed.context) || result?.version !== 1 || result.kind !== "cover" || result.sequence !== owner.sequence.id || !window.FilmocitySync.sameProject(owner.context, result.context) || !Number.isInteger(result.frame) || result.frame < 0 || plan?.summary?.kind !== "cover" || !Number.isFinite(result.time) || result.time < 0 || !plan?.summary || !Array.isArray(plan.ops) || plan.ops.length || typeof plan.fingerprint !== "string" || !plan.fingerprint || !Array.isArray(result.covers) || !result.covers.length || result.covers.length > 4 || !Array.isArray(plan.summary.warnings)) throw Error("The cover review was not confirmed for this sequence.");
    for (const [index, item] of result.covers.entries()) if (!item || item.index !== index || item.url !== coverArtifactUrl(identity, index, owner.context) || !Number.isInteger(item.width) || !Number.isInteger(item.height) || item.width < 16 || item.width > 4096 || item.height < 16 || item.height > 4096 || !Number.isInteger(item.size) || item.size <= 0 || typeof item.sha256 !== "string" || !/^[a-f0-9]{64}$/.test(item.sha256) || typeof item.filename !== "string") throw Error("The cover review contains invalid output files.");
    COVER_REVIEWS.set(reviewed, { ...owner, envelope: JSON.stringify(reviewed) }); return reviewed;
  });
}
function coverReviewCurrent(reviewed) {
  const owner = reviewed && COVER_REVIEWS.get(reviewed);
  return !!owner && recipeOwnerCurrent(owner) && JSON.stringify(reviewed) === owner.envelope && S.context.revision === owner.context.revision;
}
const MOTION_PRESETS = { push_in: { name: "Push in (slow zoom)", kf: (c, d) => ({ "transform.scale": [{ t: 0, v: 1.0 }, { t: d, v: 1.12, e: "ease" }] }) }, pull_out: { name: "Pull out", kf: (c, d) => ({ "transform.scale": [{ t: 0, v: 1.12 }, { t: d, v: 1.0, e: "ease" }] }) }, pan_lr: { name: "Pan left → right", kf: (c, d) => ({ "transform.scale": [{ t: 0, v: 1.1 }], "transform.x": [{ t: 0, v: -S.seq.width * 0.05 }, { t: d, v: S.seq.width * 0.05, e: "linear" }] }) }, pan_rl: { name: "Pan right → left", kf: (c, d) => ({ "transform.scale": [{ t: 0, v: 1.1 }], "transform.x": [{ t: 0, v: S.seq.width * 0.05 }, { t: d, v: -S.seq.width * 0.05, e: "linear" }] }) }, whip_in: { name: "Whip in", kf: (c, d) => ({ "transform.x": [{ t: 0, v: -S.seq.width }, { t: Math.min(0.35, d), v: 0, e: "ease" }] }) }, drop_in: { name: "Drop in", kf: (c, d) => ({ "transform.y": [{ t: 0, v: -S.seq.height }, { t: Math.min(0.4, d), v: 0, e: "ease" }], "transform.scale": [{ t: 0, v: 1.15 }, { t: Math.min(0.4, d), v: 1.0, e: "ease" }] }) }, handheld: { name: "Handheld shake", kf: (c, d) => { const n = Math.max(4, Math.round(d * 4)); const rnd = i => Math.sin(i * 12.9898) * 43758.5453 % 1; return { "transform.scale": [{ t: 0, v: 1.06 }], "transform.x": Array.from({ length: n + 1 }, (_, i) => ({ t: +(d * i / n).toFixed(3), v: +(((rnd(i) + 1) % 1 - 0.5) * S.seq.width * 0.02).toFixed(1), e: "ease" })), "transform.y": Array.from({ length: n + 1 }, (_, i) => ({ t: +(d * i / n).toFixed(3), v: +(((rnd(i + 7) + 1) % 1 - 0.5) * S.seq.height * 0.012).toFixed(1), e: "ease" })) }; } }, zoom_pulse: { name: "Zoom pulse (beat)", kf: (c, d) => ({ "transform.scale": [{ t: 0, v: 1.0 }, { t: 0.12, v: 1.08, e: "ease" }, { t: 0.4, v: 1.0, e: "ease" }] }) }, punch_ins: { name: "Auto punch-ins (every 3 s)", kf: (c, d) => { const kf = []; let t = 0, k = 0; while (t < d) { kf.push({ t: +t.toFixed(3), v: k % 2 ? 1.12 : 1.0, e: "hold" }); t += 3; k++; } return { "transform.scale": kf }; } } };
function applyMotionPreset(key) { const sel = selectedClips().filter(x => x.tr.kind === "video"); if (!sel.length) { status("Select a video clip."); return; } const ops = sel.map(({ c, tr }) => ({ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, keyframes: { ...(c.keyframes || {}), ...MOTION_PRESETS[key].kf(c, clipDur(c)) } } })); applyOps(ops, "motion_preset", MOTION_PRESETS[key].name); }
function clipRect(c) { const m = S.proj.media[c.media_id]; if (!m) return null; const W = S.seq.width, H = S.seq.height, tf = c.transform || {}, kf = c.keyframes || {}, rel = S.t - c.start; const sc = kfVal(kf["transform.scale"], rel, tf.scale == null ? 1 : tf.scale), ox = kfVal(kf["transform.x"], rel, tf.x || 0), oy = kfVal(kf["transform.y"], rel, tf.y || 0); const [fw, fh] = programPictureSize(c, null, W, H), fit = c.fit === "cover" ? Math.max(W / fw, H / fh) : Math.min(W / fw, H / fh); const dw = fw * fit * sc, dh = fh * fit * sc; return [Math.max(0, W / 2 + ox - dw / 2), Math.max(0, H / 2 + oy - dh / 2), Math.min(W, W / 2 + ox + dw / 2), Math.min(H, H / 2 + oy + dh / 2)]; }
function frameStats(cv, rect) { const rr = rect || [0, 0, cv.width, cv.height]; const rw = Math.max(1, rr[2] - rr[0]), rh = Math.max(1, rr[3] - rr[1]); const w = 96, h = Math.max(8, Math.round(96 * rh / rw)); const oc = offscreen["stats"] || (offscreen["stats"] = document.createElement("canvas")); oc.width = w; oc.height = h; const c2 = oc.getContext("2d"); c2.drawImage(cv, rr[0], rr[1], rw, rh, 0, 0, w, h); const d = c2.getImageData(0, 0, w, h).data; const L = []; let r = 0, g = 0, b = 0, n = 0; for (let i = 0; i < d.length; i += 4) { if (d[i + 3] < 10) continue; r += d[i]; g += d[i + 1]; b += d[i + 2]; n++; L.push(0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2]); } L.sort((a, b) => a - b); const q = p => L[Math.min(L.length - 1, Math.floor(p * L.length))] / 255; return { r: r / n / 255, g: g / n / 255, b: b / n / 255, p1: q(0.01), p50: q(0.5), p99: q(0.99) }; }
function autoColor() { const sel = selectedClips().filter(x => x.tr.kind === "video" && x.c.media_id && S.t >= x.c.start && S.t < clipEnd(x.c)); if (sel.length !== 1) { status("Park on the clip and select it."); return; } const { c, tr } = sel[0]; const saved = deep(c.color || {}); c.color = {}; renderProgram(); const st = frameStats($("#prgCanvas"), clipRect(c)); c.color = saved; const col = { ...(c.color || {}) }; col.blacks = +Math.max(-1, Math.min(1, st.p1 * 6)).toFixed(2); col.whites = +Math.max(-1, Math.min(1, (1 - st.p99) * 4)).toFixed(2); col.exposure = +Math.max(-1, Math.min(1, (0.45 - st.p50) * 1.6)).toFixed(2); const grey = (st.r + st.g + st.b) / 3; col.temperature = +Math.max(-1, Math.min(1, (st.b - st.r) / Math.max(grey, 0.05) * 1.2)).toFixed(2); col.tint = +Math.max(-1, Math.min(1, (st.g - grey) / Math.max(grey, 0.05) * -1.5)).toFixed(2); applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, color: col } }], "auto_color", "auto colour"); status(`Auto colour: blacks ${col.blacks} whites ${col.whites} exposure ${col.exposure} temp ${col.temperature} tint ${col.tint}`); }
function colorMatch() { const at = selectedClips().find(x => x.tr.kind === "video" && x.c.media_id && S.t >= x.c.start && S.t < clipEnd(x.c)); if (!S.refStats) { const st = frameStats($("#prgCanvas"), at ? clipRect(at.c) : null); S.refStats = st; status("Reference frame captured — park on the shot to match and run Colour Match again."); return; } const sel = selectedClips().filter(x => x.tr.kind === "video" && x.c.media_id && S.t >= x.c.start && S.t < clipEnd(x.c)); if (sel.length !== 1) { status("Select the clip to match at the playhead."); return; } const { c, tr } = sel[0]; const cur = frameStats($("#prgCanvas"), clipRect(c)); const ref = S.refStats; S.refStats = null; const col = { ...(c.color || {}) }; col.exposure = +Math.max(-1, Math.min(1, (col.exposure || 0) + (ref.p50 - cur.p50) * 2.2)).toFixed(2); col.contrast = +Math.max(-1, Math.min(1, (col.contrast || 0) + ((ref.p99 - ref.p1) - (cur.p99 - cur.p1)) * 1.5)).toFixed(2); const rg = (ref.r + ref.g + ref.b) / 3, cg = (cur.r + cur.g + cur.b) / 3; col.temperature = +Math.max(-1, Math.min(1, (col.temperature || 0) + ((ref.r - ref.b) / Math.max(rg, 0.05) - (cur.r - cur.b) / Math.max(cg, 0.05)) * 1.2)).toFixed(2); col.tint = +Math.max(-1, Math.min(1, (col.tint || 0) + ((ref.g - rg) / Math.max(rg, 0.05) - (cur.g - cg) / Math.max(cg, 0.05)) * 1.5)).toFixed(2); applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, color: col } }], "color_match", "colour match"); status(`Matched to reference: exposure ${col.exposure} contrast ${col.contrast} temp ${col.temperature} tint ${col.tint}`); }
async function removeSilences() {
  try {
    analysisTarget("silences"); const text = prompt("Remove silences — threshold below the loudest level (dB) and minimum gap (s):", "-38, 0.45"); if (text == null || !text.trim()) return false;
    const fields = text.split(","); if (fields.length !== 2 || fields.some(value => !value.trim())) throw Error("Enter a negative dB threshold and a positive minimum gap, separated by a comma.");
    const [threshold_db, min_gap] = fields.map(Number);
    if (!Number.isFinite(threshold_db) || threshold_db >= 0 || threshold_db < -120 || !Number.isFinite(min_gap) || min_gap <= 0 || min_gap > 3600) throw Error("Choose a threshold from −120 to below 0 dB and a minimum gap greater than 0, up to 3600 seconds.");
    return await startClipAnalysis("silences", { threshold_db, min_gap });
  } catch (error) { status(error.message || String(error), "err"); return false; }
}
function autoPunchIns() { const sel = selectedClips().filter(x => x.tr.kind === "video"); if (!sel.length) { status("Select a talking-head clip."); return; } const every = parseFloat(prompt("Punch in every N seconds (alternating 100% / 112%):", "3")) || 3; const ops = sel.map(({ c, tr }) => { const d = clipDur(c); const kf = []; let t = 0, k = 0; while (t < d) { const v = k % 2 ? 1.12 : 1.0; kf.push({ t: +t.toFixed(3), v, e: "hold" }); t += every; k++; } return { op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, keyframes: { ...(c.keyframes || {}), "transform.scale": kf } } }; }); applyOps(ops, "punch_ins", `punch-ins every ${every}s`); }
function toggleEnabled() { const sel = selectedClips(); if (!sel.length) return; applyOps(sel.map(({ c, tr }) => ({ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, enabled: c.enabled === false } })), "enable", "toggle clip enable"); }
function liftExtract(extract) {
  if (!removalReady()) return false;
  let a = S.seq.in_point, b = S.seq.out_point;
  if (!Number.isFinite(a) || !Number.isFinite(b) || a < 0 || b <= a) { status("Set sequence In and Out first (I / O with the Program monitor focused).", "err"); return false; }
  try {
    // Preserve the established all-unlocked-track scope of Lift and Extract.
    const tracks = S.seq.tracks.filter(tr => !tr.locked), ids = tracks.map(tr => tr.id);
    if (!ids.length) throw Error("Unlock a track before lifting or extracting this range.");
    if (tracks.some(tr => tr.kind === "video")) { const fps = S.seq.fps || 30; a = timing.fromFrames(timing.toFrames(a, fps), fps); b = timing.fromFrames(timing.toFrames(b, fps), fps); }
    if (b <= a) throw Error("The marked range must contain at least one sequence frame.");
    const plan = window.FilmocityTimelineRange.planRemove(S.seq, ids, [[a, b]], { close: !!extract }, timelineRangeHooks());
    const ops = timelineRangeOps(plan, S.seq, extract ? { type: "remove", ranges: plan.removedRanges, close: true } : null);
    return applyRemoval(ops, extract ? "extract" : "lift", `${extract ? "extract" : "lift"} ${fmtTC(a, S.seq.fps)}–${fmtTC(b, S.seq.fps)}`, extract ? a : undefined);
  } catch (error) { status(error.message, "err"); return false; }
}
let clipboard = [], clipboardOwner = null;
function captureClipboard() {
  const selected = S.seq.tracks.flatMap(tr => tr.clips.filter(c => S.sel.has(c.id)).map(c => ({ c, tr })));
  if (selected.length !== S.sel.size) throw Error("The selection changed. Select the clips again before copying.");
  if (!selected.length) return { items: [], owner: null };
  const first = Math.min(...selected.map(({ c }) => c.start));
  const items = selected.map(({ c, tr }) => {
    const media = c.media_id && S.proj.media[c.media_id]; if (c.media_id && !media) throw Error("A selected clip references missing media.");
    return { clip: deep(c), track: tr.id, kind: tr.kind, offset: c.start - first, source: media ? placementMediaIdentity(media) : null,
      format: deep({ id: S.seq.id, fps: S.seq.fps, width: S.seq.width, height: S.seq.height }), media: media ? deep(Object.fromEntries(["id", "path", "subclip_of", "sub_in", "duration", "frame_rate", "fps", "interpret_fps", "ingest_token", "source_relink_basis", "stab_trf", "sequence_frames", "input_opts"].filter(key => Object.hasOwn(media, key)).map(key => [key, media[key]]))) : null,
      name: c.name || media?.name || S.proj.sequences.find(sq => sq.id === c.sequence_id)?.name || c.title?.text || c.id };
  });
  return { items, owner: { ...S.context } };
}
function copySel() {
  if (!canEdit()) return false;
  try { const captured = captureClipboard(); if (!captured.items.length) return false; clipboard = captured.items; clipboardOwner = captured.owner; status(`Copied ${clipboard.length} clip(s)`); return true; }
  catch (error) { status(error.message || String(error), "err"); return false; }
}
function pasteClipboard(mode) {
  if (!placementReady() || !clipboard.length) return false;
  try {
    const sameProject = window.FilmocitySync.sameProject(clipboardOwner, S.context), used = placementIds(), ids = new Map(), groups = new Map();
    for (const item of clipboard) ids.set(item.clip.id, placementId(used));
    const placements = clipboard.map(item => {
      const tr = trackOf(item.track); if (!tr || tr.locked || tr.kind !== item.kind) throw Error("Every copied destination track must exist, match its original kind, and be unlocked.");
      const clip = deep(item.clip); clip.id = ids.get(item.clip.id); clip.start = S.t + item.offset;
      if (clip.group) { if (!groups.has(clip.group)) groups.set(clip.group, placementId(used)); clip.group = groups.get(clip.group); }
      if (clip.audio_detached_id) { if (ids.has(clip.audio_detached_id)) clip.audio_detached_id = ids.get(clip.audio_detached_id); else delete clip.audio_detached_id; }
      if (clip.unlinked_from) { if (ids.has(clip.unlinked_from)) clip.unlinked_from = ids.get(clip.unlinked_from); else delete clip.unlinked_from; }
      if (clip.media_id) {
        if (sameProject) {
          const media = S.proj.media[clip.media_id]; if (!media || placementMediaIdentity(media) !== item.source) throw Error("Copied media changed or is missing. Copy the source clips again.");
        } else {
          const copied = JSON.parse(item.source), identifiable = (typeof copied.path === "string" && copied.path.length > 0) || copied.synthetic;
          if (clipboardOwner?.workspace !== S.context.workspace || !identifiable) throw Error("The copied media cannot be identified in this project. Import it and copy the clips again.");
          const matches = Object.entries(S.proj.media).filter(([, media]) => placementMediaIdentity(media) === item.source);
          if (matches.length !== 1) throw Error("Copied media has no unique matching source in this project. Import or relink it before pasting.");
          clip.media_id = matches[0][0];
        }
      }
      if (clip.sequence_id && !sameProject) throw Error("Nested sequence clips must be copied within their original project.");
      return { trackId: tr.id, clip };
    });
    const plan = planPlacement(placements, mode, S.t); return commitPlacement(plan, mode === "insert" ? "paste_insert" : "paste", `paste ${mode === "insert" ? "insert " : ""}${placements.length} clip(s)`);
  } catch (error) { status(error.message || String(error), "err"); return false; }
}
function pasteClips() { return pasteClipboard("overwrite"); }
const ATTRIBUTE_GROUPS = [
  { id: "motion", label: "Motion, opacity and fit", selected: true },
  { id: "color", label: "Color correction", selected: true },
  { id: "video_effects", label: "Video effects and blend", selected: true },
  { id: "audio_effects", label: "Audio effects", selected: true },
  { id: "mask", label: "Mask", selected: false },
  { id: "audio_gain", label: "Audio gain and ducking", selected: true },
  { id: "audio_controls", label: "Audio pan, channels and pitch", selected: false },
  { id: "audio_fades", label: "Audio fades and transitions", selected: true },
  { id: "picture_transitions", label: "Picture transitions", selected: false },
];
function attributeEqual(a, b) {
  return a === b || !!a && !!b && typeof a === "object" && typeof b === "object" && Array.isArray(a) === Array.isArray(b) &&
    Object.keys(a).length === Object.keys(b).length && Object.keys(a).every(key => Object.hasOwn(b, key) && attributeEqual(a[key], b[key]));
}
function captureAttributePaste() {
  const owner = editorialCommandOwner(), selected = selectedClips();
  if (!clipboard.length) throw Error("Copy a clip before pasting its attributes.");
  if (clipboard.length > 100) throw Error("Copy at most 100 clips to choose an attribute donor.");
  if (!selected.length || selected.length > 100 || selected.length !== S.sel.size) throw Error("Select 1–100 current clips to receive attributes.");
  if (selected.some(({ tr }) => tr.locked)) throw Error("Unlock every selected track before pasting attributes.");
  owner.clipIds = [...S.sel]; owner.selection = JSON.stringify(owner.clipIds);
  owner.clipboard = clipboard; owner.clipboardSnapshot = JSON.stringify([clipboard, clipboardOwner]);
  owner.snapshot = JSON.stringify([owner.project.sequences, owner.project.media, owner.project.resources]);
  owner.donors = clipboard.map(item => {
    if (!item.format || !Number.isFinite(clipDur(item.clip)) || clipDur(item.clip) <= 0) throw Error("Copy the donor clips again to capture their format and duration.");
    return { name: item.name || item.clip.name || item.clip.id, duration: clipDur(item.clip),
      bundle: deep({ version: 1, clip: item.clip, sequence: item.format, context: clipboardOwner, media: item.media || null }) };
  });
  owner.donorSnapshot = JSON.stringify(owner.donors); owner.ranges = selected.map(({ c }) => ({ id: c.id, start: c.start, end: clipEnd(c), in_: c.in_, out: c.out, hold: !!c.hold, reverse: !!c.reverse, ramp: !!c.time_remap?.length }));
  owner.pending = false; owner.submitted = false; owner.cancelled = false; owner.generation = 0; owner.review = null;
  return owner;
}
function attributeOwnerCurrent(owner) {
  return !!owner && editorialOwnerCurrent(owner) && !owner.cancelled && owner.selection === JSON.stringify([...(S.sel || [])]) &&
    owner.clipboard === clipboard && owner.clipboardSnapshot === JSON.stringify([clipboard, clipboardOwner]) && owner.donorSnapshot === JSON.stringify(owner.donors) &&
    owner.snapshot === JSON.stringify([S.proj.sequences, S.proj.media, S.proj.resources]);
}
function attributeChoices(owner, choices) {
  if (!Number.isSafeInteger(choices?.donor) || choices.donor < 0 || choices.donor >= owner.donors.length) throw Error("Choose a copied donor clip.");
  const allowed = ATTRIBUTE_GROUPS.map(group => group.id);
  if (!Array.isArray(choices.groups) || !choices.groups.length || new Set(choices.groups).size !== choices.groups.length || choices.groups.some(group => !allowed.includes(group))) throw Error("Choose at least one attribute category.");
  if (typeof choices.include_animation !== "boolean" || !["seconds", "scale"].includes(choices.timing)) throw Error("Choose whether to copy animation and how its timing should fit.");
  return { donor: deep(owner.donors[choices.donor].bundle), groups: allowed.filter(group => choices.groups.includes(group)), include_animation: choices.include_animation, timing: choices.timing };
}
async function reviewAttributePaste(owner, choices, current = () => true) {
  if (!attributeOwnerCurrent(owner) || owner.pending || owner.submitted || !canEdit() || !current()) throw Error("The copied donor, selected clips or project changed. Reopen Paste Attributes.");
  const request = attributeChoices(owner, choices), generation = ++owner.generation;
  const valid = () => attributeOwnerCurrent(owner) && owner.generation === generation && current();
  owner.pending = true; owner.review = null;
  try {
    const state = await flushSaves();
    if (state !== projectSaveState() || state.error || !valid() || !canEdit()) throw Error("The selection changed or Recovery needs attention. Reopen Paste Attributes after saving.");
    const context = { ...state.context }, report = await api.json("POST", "/api/clip/attributes/review", { ...request, sequence: owner.sequence.id, clip_ids: owner.clipIds, _context: context, actor: "human", client: CLIENT });
    if (!valid() || state !== projectSaveState() || state.pending || state.error || S.context.revision !== context.revision) throw Error("The saved clips or choices changed during review. Review attributes again.");
    const sum = report?.summary, strings = values => Array.isArray(values) && values.every(value => typeof value === "string"), settings = { groups: request.groups, include_animation: request.include_animation, timing: request.timing };
    if (typeof report?.ok !== "boolean" || report.kind !== "clip_attributes" || report.sequence !== owner.sequence.id || !attributeEqual(report.clip_ids, owner.clipIds) ||
      !window.FilmocitySync.validContext(report.context) || !window.FilmocitySync.sameProject(context, report.context) || report.context.revision !== context.revision ||
      !attributeEqual(report.settings, settings) || !attributeEqual(report.donor, request.donor) || !/^[a-f0-9]{64}$/.test(report.fingerprint || "") ||
      !Array.isArray(report.issues) || report.issues.some(issue => !issue || typeof issue.message !== "string" || !["error", "warning", "info"].includes(issue.severity)) ||
      sum?.kind !== "clip_attributes" || sum.sequence !== owner.sequence.id || typeof sum.message !== "string" || sum.selected_count !== owner.clipIds.length ||
      !strings(sum.changed_clip_ids) || sum.changed_clip_ids.length > owner.clipIds.length || new Set(sum.changed_clip_ids).size !== sum.changed_clip_ids.length || sum.changed_clip_ids.some(id => !owner.clipIds.includes(id)) ||
      (sum.changed_count !== undefined && sum.changed_count !== sum.changed_clip_ids.length) ||
      !attributeEqual(sum.groups, settings.groups) || sum.include_animation !== settings.include_animation || sum.timing !== settings.timing ||
      sum.donor?.clip_id !== request.donor.clip.id || typeof sum.donor.name !== "string" || !Number.isFinite(sum.donor.duration) || sum.donor.duration <= 0 ||
      !strings(sum.warnings) || !strings(sum.affected_fields) || !Array.isArray(sum.targets) || sum.targets.length !== owner.clipIds.length ||
      sum.targets.some((target, index) => !target || target.clip_id !== owner.clipIds[index] || typeof target.track !== "string" || !Number.isFinite(target.duration) || target.duration <= 0 || !Number.isFinite(target.ratio) || target.ratio <= 0 || typeof target.changed !== "boolean" || !strings(target.fields) || !strings(target.curves) || !strings(target.warnings))) throw Error("Paste Attributes review did not confirm this donor and saved selection. Review again before applying.");
    const close = (a, b) => Math.abs(a - b) <= Math.max(1e-8, Math.abs(b) * 1e-9), donorDuration = clipDur(request.donor.clip);
    if (!close(sum.donor.duration, donorDuration) || !attributeEqual(sum.changed_clip_ids, sum.targets.filter(target => target.changed).map(target => target.clip_id)) ||
      sum.targets.some(target => { const range = owner.ranges.find(item => item.id === target.clip_id), duration = range.end - range.start; return !close(target.duration, duration) || !close(target.ratio, settings.timing === "scale" ? duration / donorDuration : 1); })) throw Error("Paste Attributes review contains inconsistent durations or changed targets. Review again.");
    owner.review = { report, snapshot: JSON.stringify(report), request, generation }; return report;
  } finally { owner.pending = false; }
}
function attributeReviewCurrent(owner, report) {
  const state = projectSaveState();
  return attributeOwnerCurrent(owner) && owner.review?.report === report && owner.review.snapshot === JSON.stringify(report) && owner.review.generation === owner.generation &&
    report.ok === true && !report.issues.some(issue => issue.severity === "error") && !state.pending && !state.error && S.context.revision === report.context.revision && state.context.revision === report.context.revision;
}
async function applyAttributePaste(owner, report, current = () => true) {
  if (!attributeReviewCurrent(owner, report) || owner.pending || owner.submitted || !current() || !canEdit()) throw Error("The reviewed donor, choices or targets changed. Review Paste Attributes again before Apply.");
  const reviewedRequest = deep(owner.review.request), expected = deep(report);
  owner.pending = true; let savedReply;
  try {
    if (S.playing) togglePlay(false, { commitTrim: false });
    await workflowRequest(async context => {
      if (!attributeReviewCurrent(owner, report) || !current()) { const error = Error("Paste Attributes was canceled or changed before submission."); error.status = 409; throw error; }
      owner.submitted = true;
      const reply = await api.json("POST", "/api/clip/attributes", { ...reviewedRequest, sequence: owner.sequence.id, clip_ids: owner.clipIds, fingerprint: expected.fingerprint, _context: context, actor: "human", client: CLIENT });
      if (S.proj !== owner.project || !window.FilmocitySync.sameProject(owner.context, S.context)) throw Error("The original project changed while attributes were submitted. Inspect its saved outcome before continuing.");
      if (reply?.ok !== true || reply.kind !== "clip_attributes" || reply.project !== owner.context.project || reply.sequence !== owner.sequence.id ||
        reply.changed !== (expected.summary.changed_clip_ids.length > 0) || reply.source_clip_id !== reviewedRequest.donor.clip.id || !attributeEqual(reply.target_clip_ids, owner.clipIds) ||
        !attributeEqual(reply.changed_clip_ids, expected.summary.changed_clip_ids) || !attributeEqual(reply.summary, expected.summary) || !attributeEqual(reply.warnings, expected.summary.warnings)) throw Error("The server did not confirm the reviewed attributes. Inspect the saved project in Recovery before repeating this command.");
      savedReply = reply; return { ...reply, sequence: null };
    }, { ...expected.context, sequence: owner.sequence.id });
    if (window.FilmocitySync.sameProject(owner.context, S.context)) status([savedReply.summary.message, ...savedReply.warnings].join(" "));
    owner.cancelled = true; owner.review = null; return savedReply;
  } catch (error) { if ([400, 401, 403, 404, 409, 422, 501].includes(error.status)) owner.submitted = false; throw error; }
  finally { owner.pending = false; }
}
function pasteAttributes() {
  try { return CR.panels.pasteAttributesDialog(captureAttributePaste()); }
  catch (error) { status(error.message || String(error), "err"); return false; }
}
function matchFrame() {
  if (!canEdit() || !S.proj?.sequences.includes(S.seq)) return false;
  try {
    const tr = trackOf(S.target.video), c = tr && [...tr.clips].reverse().find(x => x.media_id && S.t >= x.start && S.t < clipEnd(x));
    if (!c) return false;
    const media = S.proj.media[c.media_id]; if (!media) throw Error("The clip's source is missing. Restore it before matching a frame.");
    const clock = { duration: clipDur(c), sourceOffset: t => sourceOffset(c, t), speedAt: t => speedAt(c, t) };
    const source = window.FilmocitySourceClock.sourceTime(c, S.t - c.start, clock); if (source == null) return false;
    const rate = mediaRate(media), native = sourceMonitorTime(media, source), count = native * rate;
    const epsilon = Math.max(1e-7, Math.abs(count) * Number.EPSILON * 8);
    const first = timing.displayFrame(Math.max(0, sourceMonitorTime(media, c.in_)), rate);
    const end = sourceMonitorTime(media, c.hold ? c.in_ : c.out) * rate;
    const last = c.hold ? first : Math.ceil(end - Math.max(1e-7, Math.abs(end) * Number.EPSILON * 8)) - 1;
    const frame = Math.max(first, Math.min(last, c.reverse && !c.hold ? Math.ceil(count - epsilon) - 1 : timing.displayFrame(Math.max(0, native), rate)));
    if (!Number.isSafeInteger(frame) || frame < 0 || last < first) throw Error("The matched frame is outside the supported source range.");
    togglePlay(false, { commitTrim: false }); loadSource(c.media_id);
    if (!seekSourceTime(sourceLogicalTime(media, timing.fromFrames(frame, rate)))) return false;
    S.srcIn = c.in_; S.srcOut = c.hold ? Math.min(media.duration ?? Infinity, sourceLogicalTime(media, timing.fromFrames(frame + 1, rate))) : c.out; updateSrcIO();
    return true;
  } catch (error) { status(error.message || String(error), "err"); return false; }
}
function nudge(frames) {
  if (!keyboardEditReady() || !Number.isSafeInteger(frames) || !frames) return false;
  // Project-list order is the overlap priority, independent of selection click order.
  const selected = S.seq.tracks.flatMap(tr => tr.clips.filter(c => S.sel.has(c.id)).map(c => ({ c, tr })));
  if (!selected.length) return false;
  if (selected.some(({ tr }) => tr.locked)) { status("Unlock every selected track before nudging this group.", "err"); return false; }
  const fps = S.seq.fps || 30, rate = timing.frameRate(fps), earliest = Math.min(...selected.map(({ c }) => c.start)), picture = selected.find(({ tr }) => tr.kind === "video");
  let delta = Math.max(-earliest, timing.fromFrames(frames, fps));
  if (picture) {
    const first = Math.ceil((picture.c.start - earliest) * rate - 1e-7);
    const target = timing.fromFrames(Math.max(first, timing.toFrames(picture.c.start, fps) + frames), fps);
    delta = target - picture.c.start;
  }
  if (!Number.isFinite(delta) || Math.abs(delta) <= 1e-10 || frames < 0 && delta >= 0 || frames > 0 && delta <= 0) return false;
  const moves = selected.map(({ c, tr }) => ({ tr, clip: { ...deep(c), start: c.start + delta } }));
  if (moves.some(({ clip }) => !Number.isFinite(clip.start) || clip.start < -1e-10)) { status("This nudge would move a clip before sequence start.", "err"); return false; }
  for (const { clip } of moves) if (clip.start < 0) clip.start = 0;
  const ops = selected.map(({ c, tr }) => ({ op: "remove_clip", sequence: S.seq.id, track: tr.id, clip_id: c.id }));
  ops.push(...moves.map(({ tr, clip }) => ({ op: "set_clip", sequence: S.seq.id, track: tr.id, clip })));
  return applyOps(ops, "nudge", `nudge ${frames} frame(s)`);
}
function applyAudioTransition(dur = 1.0) { let sel = selectedClips(), c, tr; if (sel.length) ({ c, tr } = sel[0]); else { for (const tid of [S.target.audio, S.target.video]) { const t = trackOf(tid); const x = t && t.clips.find(q => Math.abs(q.start - S.t) < frame() * 2); if (x) { c = x; tr = t; break; } } }
  if (!c) { status("Put the playhead at a cut or select a clip, then apply the audio transition."); return; } const ops = [{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, audio_transition_in: { type: "constant_power", duration: Math.min(dur, clipDur(c) / 2) } } }];
  const prev = tr.clips.filter(x => x.id !== c.id && Math.abs(clipEnd(x) - c.start) < frame()).sort((a, b) => b.start - a.start)[0]; if (prev) ops.push({ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: prev.id, audio_transition_out: { type: "constant_power", duration: Math.min(dur, clipDur(prev) / 2) } } }); applyOps(ops, "audio_transition", `constant power ${dur}s`); }
function applyBrand(obj) { const b = { primary: "#E8631C", secondary: "#7A2E9E", text: "#FFFFFF", font: "", ...(S.proj.brand || {}) }; const walk = v => { if (typeof v === "string") return v.replace(/\{\{(primary|secondary|text)\}\}/g, (_, k) => b[k]).replace(/\{\{font\}\}/g, b.font || ""); if (Array.isArray(v)) return v.map(walk); if (v && typeof v === "object") { const o = {}; for (const [k, x] of Object.entries(v)) { const w = walk(x); if (!(k === "font" && w === "")) o[k] = w; } return o; } return v; }; return walk(obj); }
function addGraphic(kind) { const H = S.seq.height, tr = S.seq.tracks.filter(x => x.kind === "video").sort((a, b) => b.index - a.index)[0]; const T = { lower_third: { name: "Lower third", layers: [{ kind: "box", x: 0.06, y: 0.72, w: 0.55, h: 0.008, color: "0xE8631C@1.0" }, { kind: "text", text: "Name Surname", size: Math.round(H * 0.036), align: "left", valign: "bottom", y: -Math.round(H * 0.02), color: "white", shadow: true }, { kind: "text", text: "Role · @handle · Paid partnership", size: Math.round(H * 0.02), align: "left", valign: "bottom", y: Math.round(H * 0.02), color: "0xDDDDDD", weight: "regular" }] },
    card: { name: "Card", layers: [{ kind: "box", x: 0.08, y: 0.40, w: 0.84, h: 0.20, color: "black@0.55" }, { kind: "text", text: "Headline goes here", size: Math.round(H * 0.045), align: "center", valign: "center", y: -Math.round(H * 0.02), color: "white" }, { kind: "text", text: "Supporting line", size: Math.round(H * 0.022), align: "center", valign: "center", y: Math.round(H * 0.035), color: "0xCCCCCC", weight: "regular" }] },
    tag: { name: "Tag", layers: [{ kind: "box", x: 0.06, y: 0.16, w: 0.30, h: 0.045, color: "0xE8631C@1.0" }, { kind: "text", text: "NEW", size: Math.round(H * 0.026), align: "left", valign: "top", x: Math.round(S.seq.width * 0.03), y: Math.round(H * 0.088), color: "0x1E2024" }] } }[kind];
  const c = { id: uid(), media_id: null, start: S.t, in_: 0, out: 4.0, speed: 1, graphic: T, transform: { opacity: 1 }, transition_in: { type: kind === "card" ? "fade" : "push_left", duration: 0.4 }, transition_out: { type: "fade", duration: 0.3 }, keyframes: {} }; applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: c }], "graphic", `add ${kind}`); S.sel = new Set([c.id]); refreshSel(); CR.panels.render(); }
function navigateTo(t) {
  if (!Number.isFinite(t)) return false;
  if (S.playing) togglePlay(false, { commitTrim: false });
  if (seekTo(t) === false) return false;
  const body = $("#tlBody"), x = S.t * S.pps;
  if (body?.clientWidth > 0 && (x < body.scrollLeft || x >= body.scrollLeft + body.clientWidth)) body.scrollLeft = Math.max(0, x - body.clientWidth / 2);
  return true;
}
function stepFrame(n) {
  if (!S.seq || !Number.isSafeInteger(n) || !Number.isFinite(S.t)) return false;
  const fps = S.seq.fps || 30, target = Math.max(0, timing.displayFrame(Math.max(0, S.t), fps) + n);
  return Number.isSafeInteger(target) && navigateTo(timing.fromFrames(target, fps));
}
function stepFocusedFrame(n) { return S.focus === "source" ? stepSourceFrame(n) : stepFrame(n); }
function navigateEdge(end = false) {
  if (S.focus !== "source") return navigateTo(end ? seqDur() : 0);
  const video = $("#srcVideo"); if (!S.src || !video) return false;
  const bounds = sourceFrameBounds(video); if (end && bounds.last == null) return false;
  return seekSourceTime(end ? sourceLogicalTime(S.src, Math.max(bounds.begin, timing.fromFrames(bounds.last, bounds.fps))) : 0);
}
function editPoints(targeted = false) {
  const pts = new Set([0]), targets = new Set([S.target?.video, S.target?.audio]);
  for (const tr of S.seq?.tracks || []) {
    if (targeted && !targets.has(tr.id)) continue;
    // Locks and visibility prevent edits, not read-only navigation.
    for (const c of tr.clips || []) for (const t of [c.start, clipEnd(c)]) if (Number.isFinite(t) && t >= 0) pts.add(t);
  }
  return [...pts].sort((a, b) => a - b);
}
function nextNavigationPoint(points, t, dir) {
  if (!Number.isFinite(t) || !Number.isFinite(dir) || !dir) return null;
  // Ignore only arithmetic noise at a shared boundary; retain source/audio subframe points.
  const epsilon = Math.max(1e-10, Math.abs(t) * Number.EPSILON * 8);
  return dir > 0 ? points.find(p => p > t + epsilon) : [...points].reverse().find(p => p < t - epsilon);
}
function gotoEdit(dir, targeted = false) {
  const target = nextNavigationPoint(editPoints(targeted), S.t, dir);
  return target != null ? navigateTo(target) : false;
}
function goToTimecode(source = false) {
  const video = source && $("#srcVideo");
  if (source ? !S.src || !video : !S.seq) return false;
  const fps = source ? mediaRate(S.src, true) : S.seq.fps || 30, current = source ? Math.max(0, sourcePlayheadTime(video)) : S.t;
  const value = prompt("Go to timecode: HH:MM:SS:FF (NDF), HH:MM:SS;FF (DF), seconds, or +/- offset", fmtTC(current, fps, source ? "ndf" : S.seq.timecode_format || "ndf"));
  if (value == null) return false;
  const text = value.trim(), relative = /^[+-]/.test(text), amount = parseTC(relative ? text.slice(1) : text, fps);
  if (!Number.isFinite(amount)) return false;
  let target = amount;
  if (relative) {
    // Frame-label offsets start at the displayed frame; decimal-second offsets preserve subframes.
    const sign = text[0] === "-" ? -1 : 1;
    target = text.includes(":") ? timing.fromFrames(timing.displayFrame(current, fps) + timing.toFrames(amount, fps) * sign, fps) : current + amount * sign;
  }
  target = Math.max(0, target);
  if (!Number.isFinite(target) || target * timing.frameRate(fps) > Number.MAX_SAFE_INTEGER) { status("Time is outside the supported range", "err"); return false; }
  if (!source) return navigateTo(target);
  return seekSourceTime(target);
}
function rippleTrimToPlayhead(which) {
  if (!keyboardEditReady() || !["head", "tail"].includes(which)) return false;
  const tr = trackOf(S.target.video); if (!tr) return false;
  const c = tr.clips.find(x => S.t > x.start && S.t < clipEnd(x));
  if (!c) { status("Playhead must be inside a clip on the target track."); return false; }
  const target = timing.fromFrames(timing.toFrames(S.t, S.seq.fps || 30), S.seq.fps || 30);
  return applyKeyboardTrim(c, tr, which === "head" ? "l" : "r", target, "ripple", "ripple_trim", `ripple trim ${which} to playhead`);
}
function toggleLink() {
  if (!canEdit()) return false;
  if (!S.proj?.sequences.includes(S.seq) || projectSaveState().error) { status("Resolve project loading or unsaved edits in Recovery before linking audio.", "err"); return false; }
  try {
    const selected = selectedClips(); if (selected.length !== S.sel.size) throw Error("The selection changed. Select the clips again before linking.");
    if (!selected.length) return false;
    if (selected.some(({ tr }) => tr.locked)) throw Error("Unlock every selected track before linking or unlinking audio.");
    const all = S.seq.tracks.flatMap(tr => tr.clips.map(c => ({ c, tr }))), parents = new Map();
    for (const item of selected) {
      if (item.tr.kind === "video" && item.c.media_id) parents.set(item.c.id, item);
      else if (item.c.unlinked_from) {
        const matches = all.filter(({ c, tr }) => tr.kind === "video" && c.id === item.c.unlinked_from);
        if (matches.length !== 1) throw Error("The detached audio has no unique video partner. Restore or select its original pair.");
        parents.set(matches[0].c.id, matches[0]);
      }
    }
    if (!parents.size) { status("Select a video clip or its detached audio partner."); return false; }
    const reserved = new Set(S.proj.sequences.flatMap(sq => sq.tracks.flatMap(tr => tr.clips.flatMap(c => [c.id, c.group, c.audio_detached_id].filter(Boolean)))));
    const fresh = () => { for (let n = 0; n < 100; n++) { const id = uid(); if (typeof id === "string" && id && !reserved.has(id)) { reserved.add(id); return id; } } throw Error("Could not allocate a unique audio clip ID. Try again."); };
    const canonical = value => {
      if (Array.isArray(value)) return value.map(canonical);
      if (value && typeof value === "object") return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
      return value;
    };
    const signature = clip => {
      const value = deep(clip);
      for (const key of ["id", "group", "unlinked_from", "audio_detached_id", "transform", "color", "effects", "fx_stack", "time_interpolation", "mask", "fit", "transition_in", "transition_out"]) delete value[key];
      if (value.audio) { delete value.audio.linked; if (!Object.keys(value.audio).length) delete value.audio; }
      if (value.keyframes) { value.keyframes = Object.fromEntries(Object.entries(value.keyframes).filter(([key]) => key.startsWith("audio."))); if (!Object.keys(value.keyframes).length) delete value.keyframes; }
      return JSON.stringify(canonical(value));
    };
    const busSignature = track => JSON.stringify(canonical({ gain_db: track.gain_db ?? 0, muted: !!track.muted, solo: !!track.solo, audio_fx: track.audio_fx || {} }));
    const linked = c => ({ ...c, audio: { ...(c.audio || {}), linked: true } });
    const sameRouting = (parent, videoTrack, child, audioTrack, destination) => {
      if (busSignature(audioTrack) !== busSignature(destination)) return false;
      const route = window.FilmocityAudioPreview.route;
      return !!route(S.seq, videoTrack, linked(parent)) === !!route(S.seq, audioTrack, child);
    };
    const duration = c => {
      const d = clipDur(c), media = S.proj.media[c.media_id];
      if (!media || !media.has_audio || !Number.isFinite(c.start) || c.start < 0 || !Number.isFinite(d) || d <= 0 || !Number.isFinite(c.start + d)) throw Error("Repair the clip's source and timing before linking audio.");
      if (!media.is_image && (!Number.isFinite(media.duration) || media.duration <= 0)) throw Error("Wait for valid source duration before linking audio.");
      window.FilmocityClipSplit.bounds(c, { duration: d, sourceOffset: t => sourceOffset(c, t), speedAt: t => speedAt(c, t) }, { sourceLimit: media.is_image ? Infinity : media.duration, still: !!media.is_image });
      return d;
    };
    const ops = [], added = [], selection = new Set(S.sel);
    for (const { c, tr } of parents.values()) {
      const media = S.proj.media[c.media_id]; if (!media) throw Error("The selected source is missing. Restore it before linking audio.");
      if (!media.has_audio) continue;
      if (tr.locked) throw Error("Unlock the video partner's track before linking audio.");
      const destination = linkedAudioTrack(tr); if (!destination || destination.kind !== "audio") throw Error("Add an audio track before linking or unlinking this video.");
      if (destination.locked) throw Error("Unlock the routed audio track before linking or unlinking this video.");
      const d = duration(c), partners = all.filter(item => item.c.unlinked_from === c.id);
      if (partners.length > 1) throw Error("This video has multiple detached audio pieces. Keep them detached or restore the original pair before linking.");
      const parent = deep(c);
      if (c.audio?.linked !== false) {
        if (c.audio_detached_id || partners.length) throw Error("This video already has a detached audio association. Resolve the pair before unlinking again.");
        const child = deep(c); child.id = fresh(); child.unlinked_from = c.id; delete child.audio_detached_id; delete child.group;
        child.audio = { ...(child.audio || {}), linked: true };
        if (!sameRouting(c, tr, child, destination, destination)) throw Error("Track mute or solo would change the sound when detached. Align the video and audio routing first.");
        const overlaps = [...destination.clips, ...added.filter(item => item.tr === destination).map(item => item.c)].some(other => {
          const end = other.start + duration(other), epsilon = Math.max(1e-12, Math.abs(c.start + d) * Number.EPSILON * 8, Math.abs(end) * Number.EPSILON * 8);
          return c.start < end - epsilon && other.start < c.start + d - epsilon;
        });
        if (overlaps) throw Error("The routed audio track is occupied at this time. Choose an empty destination before unlinking.");
        parent.audio = { ...(parent.audio || {}), linked: false }; parent.audio_detached_id = child.id;
        ops.push({ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: parent }, { op: "set_clip", sequence: S.seq.id, track: destination.id, clip: child }); added.push({ c: child, tr: destination });
      } else {
        const partner = partners[0];
        if (!partner && c.audio_detached_id) throw Error("The detached audio partner is missing. Restore it before relinking.");
        if (partner) {
          if (partner.tr.kind !== "audio" || partner.tr.locked) throw Error("The detached audio partner needs an unlocked audio track.");
          if (c.audio_detached_id && partner.c.id !== c.audio_detached_id) throw Error("The detached audio association changed. Restore the original pair before linking.");
          duration(partner.c);
          if (signature(c) !== signature(partner.c)) throw Error("The detached audio has independent edits. Keep it detached or restore matching source and audio settings before linking.");
          if (!sameRouting(c, tr, partner.c, partner.tr, destination)) throw Error("The detached audio uses different bus settings or routing. Align those settings before relinking.");
          ops.push({ op: "remove_clip", sequence: S.seq.id, track: partner.tr.id, clip_id: partner.c.id });
          if (selection.delete(partner.c.id)) selection.add(c.id);
        }
        parent.audio = { ...(parent.audio || {}), linked: true }; parent.audio_detached_id = null;
        ops.push({ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: parent });
      }
    }
    if (!ops.length) return false;
    // Every source, lock, partner and destination is validated before the first mutation.
    const pending = applyOps(ops, "link", "link/unlink audio"); S.sel = selection; refreshSel(); CR.panels.render(); return pending;
  } catch (error) { status(error.message || String(error), "err"); return false; }
}
function setZoom(f) { const body = $("#tlBody"), tAtLeft = body.scrollLeft / S.pps; S.pps = Math.min(600, Math.max(6, S.pps * f)); renderTimeline(); body.scrollLeft = tAtLeft * S.pps; const z = $("#tlZoom"); if (z) z.value = Math.log10(S.pps); }
function setTool(t) { S.tool = t; $$("[data-tool]").forEach(b => b.classList.toggle("active", b.dataset.tool === t)); $("#tl").className = "tl " + t; }
let hoverPanel = null; document.addEventListener("mouseover", e => { const p = e.target.closest && e.target.closest(".panel"); if (p) hoverPanel = p; });
function toggleMaximize() { const p = hoverPanel; if (!p) return; const on = !p.classList.contains("maxi"); $$(".panel.maxi").forEach(x => x.classList.remove("maxi")); if (on) p.classList.add("maxi"); renderProgram(); }
const TIMELINE_ONLY = new Set(["del", "ripple_del", "cut", "copy", "paste", "paste_insert", "paste_attr", "add_edit", "add_edit_all", "ripple_trim_in", "ripple_trim_out", "extend", "nudge_left", "nudge_right", "trim_back", "trim_fwd", "trim_back5", "trim_fwd5", "rtrim_back", "rtrim_fwd", "lift", "extract", "razor_all", "enable", "link", "nest", "group", "ungroup", "select_all", "mark_clip", "mark_sel", "duplicate"]);
function setFocus(f) { S.focus = f; $("#srcPanel").classList.toggle("focus", f === "source"); $("#prgPanel").classList.toggle("focus", f === "program" || f === "timeline"); $$(".panel").forEach(p => { if (p.id !== "srcPanel" && p.id !== "prgPanel") p.classList.remove("focus"); }); if (f === "panel") { const el = document.activeElement && document.activeElement.closest && document.activeElement.closest(".panel"); if (el) el.classList.add("focus"); } }
function gotoMarker(dir) { const ms = (S.seq?.markers || []).map(m => m.time).filter(t => Number.isFinite(t) && t >= 0).sort((a, b) => a - b); const target = nextNavigationPoint(ms, S.t, dir); return target != null ? navigateTo(target) : false; }

// ---------- keyboard: actions registry + editable keymap (Premiere defaults) ----------
const ACTIONS = {
  commands: ["Find a command", "ctrl+shift+p", () => window.FilmocityCommands.open(CR)],
  maximize: ["Maximize / restore panel under the pointer", "`", toggleMaximize],
  trim_edit: ["Trim Edit (trim monitor)", "shift+t", () => S.trimMode ? exitTrimMode() : enterTrimMode()], trim_type: ["Cycle trim type", "ctrl+shift+t", cycleTrimType], rev_match: ["Reverse match frame", "shift+r", reverseMatchFrame], tool_polygon: ["Polygon tool", "", () => setTool("polygon")], tool_line: ["Line / arrow tool", "", () => setTool("line")], reveal: ["Reveal in Project", "shift+f", () => { const s = selectedClips()[0]; if (s) revealInProject(s.c); }],
  cut: ["Cut", "ctrl+x", cutSel], paste_insert: ["Paste Insert", "ctrl+shift+v", pasteInsert], select_label: ["Select all matching label", "", selectMatchingLabel], find: ["Find in timeline", "ctrl+f", findInTimeline], mark_clip: ["Mark Clip", "shift+/", markClip], mark_sel: ["Mark Selection", "/", markClip], goto_in: ["Go to In", "shift+i", gotoIn], goto_out: ["Go to Out", "shift+o", gotoOut], add_edit_all: ["Add edit to all tracks", "ctrl+shift+k", addEditAllTracks], def_trans_sel: ["Apply default transitions to selection", "shift+d", defaultTransitionsToSelection], sel_follow: ["Toggle selection follows playhead", "", () => { S.selFollow = !S.selFollow; status(S.selFollow ? "Selection follows playhead: on" : "Selection follows playhead: off"); }], audio_gain: ["Audio Gain…", "g", () => CR.panels.gainDialog()],
  duplicate_media: ["Duplicate selected bin items", "ctrl+shift+/", duplicateBinSources],
  bin_delete: ["Delete selected bin items", "", () => { const ids = [...S.binSel]; if (!ids.length) return; const used = ids.filter(id => S.proj.sequences.some(sq => sq.tracks.some(t => t.clips.some(c => c.media_id === id)))); if (used.length && !confirm(`${used.length} item(s) are used in a sequence. Remove anyway? (clips referencing them go offline)`)) return; applyOps(ids.map(id => ({ op: "remove", path: `/media/${id}` })), "bin", `remove ${ids.length} item(s)`); S.binSel.clear(); }],
  render_seq: ["Render entire sequence (preview)", "enter", renderEntireSequence],
  play: ["Play / pause", " ", () => togglePlay()], play_around: ["Play around", "shift+k", () => playAround(S.prefs.preroll || 2, S.prefs.postroll || 2)], play_inout: ["Play In to Out", "ctrl+shift+ ", playInOut], loop: ["Toggle loop playback", "", () => { S.loop = !S.loop; $("#prgLoop").classList.toggle("active", S.loop); status(S.loop ? "Loop on" : "Loop off"); }], shuttle_back: ["Shuttle backward (J)", "j", () => { S.rate = S.playing && S.rate < 0 ? Math.max(S.rate * 2, -8) : -1; if (!S.playing) togglePlay(true); }], shuttle_fwd: ["Shuttle forward (L)", "l", () => { S.rate = S.playing && S.rate > 0 ? Math.min(S.rate * 2, 8) : 1; if (!S.playing) togglePlay(true); }], stop: ["Stop (K)", "k", () => togglePlay(false)],
  frame_back: ["Step back one frame", "arrowleft", () => stepFocusedFrame(-1)], frame_fwd: ["Step forward one frame", "arrowright", () => stepFocusedFrame(1)], frames_back: ["Step back five frames", "shift+arrowleft", () => stepFocusedFrame(-5)], frames_fwd: ["Step forward five frames", "shift+arrowright", () => stepFocusedFrame(5)],
  prev_edit: ["Go to previous edit point", "arrowup", () => gotoEdit(-1)], next_edit: ["Go to next edit point", "arrowdown", () => gotoEdit(1)], home: ["Go to start", "home", () => navigateEdge(false)], end: ["Go to end", "end", () => navigateEdge(true)],
  prev_target_edit: ["Go to previous edit on targeted tracks", "", () => gotoEdit(-1, true)], next_target_edit: ["Go to next edit on targeted tracks", "", () => gotoEdit(1, true)], goto_timecode: ["Go to timecode in focused monitor", "", () => goToTimecode(S.focus === "source")],
  tool_select: ["Selection tool", "v", () => setTool("select")], tool_trackfwd: ["Track select forward", "a", () => setTool("trackfwd")], tool_ripple: ["Ripple edit tool", "b", () => setTool("ripple")], tool_roll: ["Rolling edit tool", "n", () => setTool("roll")], tool_slip: ["Slip tool", "y", () => setTool("slip")], tool_slide: ["Slide tool", "u", () => setTool("slide")], tool_razor: ["Razor tool", "c", () => setTool("razor")], tool_hand: ["Hand tool", "h", () => setTool("hand")], snap: ["Toggle snapping", "s", () => $("#toolSnap").click()],
  mark_in: ["Mark In", "i", () => markIO("in")], mark_out: ["Mark Out", "o", () => markIO("out")], insert: ["Insert", ",", () => insertFromSource("insert")], overwrite: ["Overwrite", ".", () => insertFromSource("overwrite")], marker: ["Add marker", "m", () => { if (!addClipMarker()) addMarker(); }], subclip: ["Make subclip", "ctrl+u", makeSubclip], trim_back: ["Trim edit backward 1 frame", "ctrl+arrowleft", () => trimEditPoint(-1, false)], trim_fwd: ["Trim edit forward 1 frame", "ctrl+arrowright", () => trimEditPoint(1, false)], trim_back5: ["Trim edit backward 5 frames", "ctrl+shift+arrowleft", () => trimEditPoint(-5, false)], trim_fwd5: ["Trim edit forward 5 frames", "ctrl+shift+arrowright", () => trimEditPoint(5, false)], rtrim_back: ["Ripple trim edit backward", "ctrl+alt+arrowleft", () => trimEditPoint(-1, true)], rtrim_fwd: ["Ripple trim edit forward", "ctrl+alt+arrowright", () => trimEditPoint(1, true)], tool_trackback: ["Track select backward", "shift+a", () => setTool("trackback")], tool_zoom: ["Zoom tool", "z", () => setTool("zoom")],
  dissolve: ["Apply default transition", "ctrl+d", () => applyTransition("dissolve", 1.0)], dissolve2: ["Apply default transition (alt)", "d", () => applyTransition("dissolve", 1.0)], ripple_head: ["Ripple trim head to playhead", "q", () => rippleTrimToPlayhead("head")], ripple_tail: ["Ripple trim tail to playhead", "w", () => rippleTrimToPlayhead("tail")],
  del: ["Delete", "delete", () => deleteSel(false)], del2: ["Delete (backspace)", "backspace", () => deleteSel(false)], ripple_del: ["Ripple delete", "shift+delete", () => deleteSel(true)], ripple_del2: ["Ripple delete (backspace)", "shift+backspace", () => deleteSel(true)],
  zoom_in: ["Zoom in", "=", () => setZoom(1.5)], zoom_out: ["Zoom out", "-", () => setZoom(1 / 1.5)], deselect: ["Deselect all", "escape", () => { if (S.trimMode) { exitTrimMode(); return; } if (S.polyDraft) { S.polyDraft = null; renderProgram(); return; } S.sel.clear(); S.editPoint = null; refreshSel(); renderTimeline(); CR.panels.render(); }],
  undo: ["Undo", "ctrl+z", undo], redo: ["Redo", "ctrl+shift+z", redo], title: ["New title", "ctrl+t", addTitle], add_edit: ["Add edit at playhead", "ctrl+k", addEditAtPlayhead], speed: ["Speed / duration", "ctrl+r", () => CR.panels.speedDialog()], link: ["Link / unlink audio", "ctrl+l", toggleLink],
  enable: ["Enable / disable clip", "shift+e", toggleEnabled], extend: ["Extend selected edit to playhead", "e", extendEdit], fit: ["Zoom to sequence", "\\", zoomToFit], hold: ["Add frame hold", "ctrl+shift+h", addFrameHold], replace: ["Replace with source clip", "ctrl+shift+r", replaceWithSource],
  tool_pen: ["Pen tool (audio keyframes)", "p", () => setTool("pen")], tool_rate: ["Rate stretch tool", "r", () => setTool("ratestretch")], tool_type: ["Type tool", "t", () => setTool("type")], group: ["Group", "ctrl+g", () => groupSel(true)], ungroup: ["Ungroup", "ctrl+shift+g", () => groupSel(false)], expand: ["Expand all tracks", "shift+=", () => setAllTall(true)], minimize: ["Minimize all tracks", "shift+-", () => setAllTall(false)], multiview: ["Toggle multi-camera view", "shift+0", () => { S.multiView = !S.multiView; $("#prgMulti").classList.toggle("active", S.multiView); renderProgram(); }],
  angle1: ["Multicam: angle 1", "1", () => switchAngle(0)], angle2: ["Multicam: angle 2", "2", () => switchAngle(1)], angle3: ["Multicam: angle 3", "3", () => switchAngle(2)], angle4: ["Multicam: angle 4", "4", () => switchAngle(3)], angle5: ["Multicam: angle 5", "5", () => switchAngle(4)], angle6: ["Multicam: angle 6", "6", () => switchAngle(5)], angle7: ["Multicam: angle 7", "7", () => switchAngle(6)], angle8: ["Multicam: angle 8", "8", () => switchAngle(7)], angle9: ["Multicam: angle 9", "9", () => switchAngle(8)], new_seq: ["New sequence", "ctrl+n", () => newSequence()], import: ["Import by path", "ctrl+i", () => CR.panels.openDlg("#dlgPath")],
  prev_marker: ["Go to previous marker", "shift+arrowup", () => gotoMarker(-1)], next_marker: ["Go to next marker", "shift+arrowdown", () => gotoMarker(1)], lift: ["Lift (In to Out)", ";", () => liftExtract(false)], extract: ["Extract (In to Out)", "'", () => liftExtract(true)], copy: ["Copy", "ctrl+c", copySel], paste: ["Paste at playhead", "ctrl+v", pasteClips], paste_attr: ["Paste attributes", "ctrl+alt+v", pasteAttributes], match: ["Match frame", "f", matchFrame],
  nudge_l: ["Nudge clip left one frame", "alt+arrowleft", () => nudge(-1)], nudge_r: ["Nudge clip right one frame", "alt+arrowright", () => nudge(1)], audio_trans: ["Apply audio transition", "ctrl+shift+d", () => applyAudioTransition(1.0)], lower_third: ["Graphic: lower third", "ctrl+shift+l", () => addGraphic("lower_third")],
  export: ["Export media", "ctrl+m", () => CR.panels.openDlg("#dlgExport")], save: ["Save snapshot", "ctrl+s", () => CR.panels.snapshot("manual_save")], frame_export: ["Export frame", "ctrl+shift+e", () => CR.panels.exportFrame()], nest: ["Nest selected", "ctrl+shift+n", nestSelected], adjust: ["Add adjustment layer", "ctrl+shift+a", addAdjustmentLayer], select_all: ["Select all", "ctrl+a", () => { S.sel = new Set(S.seq.tracks.flatMap(t => t.clips.map(c => c.id))); refreshSel(); CR.panels.render(); }] };
S.keymap = window.FilmocityKeyboard.readKeymap(ACTIONS);
function comboOf(e) { return window.FilmocityKeyboard.comboOf(e); }
function keys(e) {
  if (e.defaultPrevented || e.isComposing || S.gesture) return;
  const combo = comboOf(e); if (!combo) return;
  const hit = Object.entries(S.keymap).find(([id, key]) => ACTIONS[id] && key === combo);
  if (!hit) return;
  if (document.querySelector('dialog[open], .dialog.open')) return;
  if (hit[0] === "commands") { if (!e.repeat) { e.preventDefault(); ACTIONS.commands[2](); } return; }
  if (!S.proj || S.recoveryRequired || e.target.matches("input,textarea,select,[role='textbox'],[role='searchbox'],[role='combobox'],[role='spinbutton']") || e.target.isContentEditable) return;
  if (/^(Arrow|Home$|End$|PageUp$|PageDown$)/.test(e.key) && e.target.closest?.("[role='slider'],[role='listbox'],[role='tree'],[role='grid'],[role='tablist'],[role='menu']")) return;
  // Native activation belongs to the focused control, including text/icons inside it.
  if ((e.key === "Enter" || e.key === " ") && e.target.closest?.("button,a[href],[role='button'],[role='menuitem']")) return;
  if (S.focus === "panel" && TIMELINE_ONLY.has(hit[0])) return;
  if (/^angle[1-9]$/.test(hit[0]) && (e.repeat || !["timeline", "program"].includes(S.focus) && !e.target.closest?.("#multicamAngles"))) return;
  if (e.repeat && ["duplicate_media", "subclip"].includes(hit[0])) return;
  e.preventDefault(); ACTIONS[hit[0]][2]();
}
async function refreshMediaStatus() { try { const loaded = await loadProject(true); status(loaded ? "Media availability refreshed." : "Finish saving the current edit before refreshing media.", loaded ? "" : "err"); return loaded; } catch (error) { status("Could not check media: " + error.message, "err"); return false; } }
async function loadKeymap() { try { const st = await api.get("/api/settings"); S.keymap = window.FilmocityKeyboard.readKeymap(ACTIONS, st.keymap); window.FilmocityCommands.updateShortcutHints(CR); if (st.prefs) Object.assign(S.prefs, st.prefs); S.tlopt.thumbs = S.prefs.thumbs !== false; } catch (e) { } }

// ---------- wiring ----------
function bind() {
  $("#btnCommands").onclick = () => window.FilmocityCommands.open(CR);
  $("#btnRecovery").onclick = () => window.FilmocityRecovery.open(CR);
  $("#btnUndo").onclick = undo; $("#btnRedo").onclick = redo; $("#prgPlay").onclick = () => togglePlay(); $("#prgStep").onclick = () => stepFrame(1); $("#prgStepBack").onclick = () => stepFrame(-1); $("#prgToStart").onclick = () => seekTo(0); $("#prgToEnd").onclick = () => seekTo(seqDur());
  const toggleSafe = () => { const on = !$("#prgScreen").classList.contains("showsafe"); $("#prgScreen").classList.toggle("showsafe", on); $("#prgSafe").classList.toggle("active", on); }; $("#prgSafe").onclick = toggleSafe; CR.toggleSafe = toggleSafe;
  const setProxy = on => { S.useProxy = on; $("#prgRes").value = $("#srcRes").value = on ? "proxy" : "full"; if (S.src) FilmocityProxyPreview.replaceSource($("#srcVideo"), sourceMediaUrl(S.src), {report: sourcePreviewError}); updateSourcePreviewState(); resetPreviewVoices(); renderProgram(); }; $("#prgRes").onchange = $("#srcRes").onchange = e => setProxy(e.target.value === "proxy"); CR.setProxy = setProxy;
  const toggleExact = () => { S.exact = !S.exact; $("#prgExact").classList.toggle("active", S.exact); renderProgram(); }; $("#prgExact").onclick = toggleExact; CR.toggleExact = toggleExact;
  $("#prgIn").onclick = () => { setFocus("program"); markIO("in"); }; $("#prgOut").onclick = () => { setFocus("program"); markIO("out"); }; $("#prgLift").onclick = () => liftExtract(false); $("#prgExtract").onclick = () => liftExtract(true); $("#prgFrame").onclick = () => CR.panels.exportFrame(); $("#srcFrame").onclick = exportSourceFrame;
  $("#srcMarker").onclick = addMarker; $("#tlMarkerBtn").onclick = addMarker; $("#srcStepBack").onclick = () => stepSourceFrame(-1); $("#srcStep").onclick = () => stepSourceFrame(1);
  $("#binNew").onclick = () => { const n = prompt(S.binFilter ? `New bin inside "${(S.proj.bins.find(b => b.id === S.binFilter) || {}).name}"` : "Bin name"); if (n) applyOps([{ op: "set", path: "/bins", value: [...(S.proj.bins || []), { id: uid(), name: n, parent: S.binFilter || null }] }], "bin", "new bin"); }; $("#binNewSeq").onclick = () => newSequence();
  $("#binViewList").onclick = () => { S.binView = "list"; $("#binViewList").classList.add("active"); $("#binViewIcons").classList.remove("active"); renderBin(); }; $("#binViewIcons").onclick = () => { S.binView = "icons"; $("#binViewIcons").classList.add("active"); $("#binViewList").classList.remove("active"); renderBin(); };
  $("#projName").ondblclick = () => { const n = prompt("Project name", S.proj.name); if (n) applyOps([{ op: "set", path: "/name", value: n }], "project", "rename project"); };
  $("#srcDragV").ondragstart = ev => beginMediaDrag(ev, S.src?.id, "video", true); $("#srcDragA").ondragstart = ev => beginMediaDrag(ev, S.src?.id, "audio", true);
  $("#srcDragV").ondragend = $("#srcDragA").ondragend = () => { S.mediaDrag = null; };
  $$("[data-trim]").forEach(b => b.onclick = () => trimByType(+b.dataset.trim)); $("#trimApplyPH").onclick = trimToPlayhead; $("#trimExit").onclick = exitTrimMode;
  $("#prgCompare").onclick = () => { S.compareMode = S.compareMode === "side" ? "wipe" : S.compareMode === "wipe" ? null : "side"; $("#prgCompare").classList.toggle("active", !!S.compareMode); status(S.compareMode ? `Comparison view (${S.compareMode}) — scrub the Reference Monitor to choose the frame` : "Comparison view off"); renderProgram(); };
  $("#agentMode").onchange = async () => { await api.json("PUT", "/api/settings", { agent_mode: $("#agentMode").value }); status($("#agentMode").value === "proposals_only" ? "Agents must now propose; direct edits are refused." : "Agents may edit directly (changes glow green, undo works)."); }; api.get("/api/settings").then(st => { if (st.agent_mode) $("#agentMode").value = st.agent_mode; }).catch(() => { });
  $("#srcSubclip").onclick = makeSubclip; $("#prgMulti").onclick = () => ACTIONS.multiview[2](); $("#prgLoop").onclick = () => ACTIONS.loop[2]();
  $("#prgCanvas").ondblclick = ev => { const hitText = textClipAt(); if (hitText && S.tool === "select") editTextInline(hitText.c, hitText.tr, ev); };
  $("#prgCanvas").onmousemove = ev => { if (S.tool !== "select") return; const h = monitorHit(ev); $("#prgCanvas").style.cursor = h ? (h.kind === "scale" ? "nwse-resize" : "grab") : "move"; };
  $("#prgTC").onclick = () => goToTimecode(false); $("#prgTC").style.cursor = "text"; $("#prgTC").title = "Click to type a timecode; also available in Find a command";
  $("#srcTC").onclick = () => goToTimecode(true); $("#srcTC").style.cursor = "text"; $("#srcTC").title = "Source-relative time at interpreted frame rate (NDF). Click to type a timecode.";
  window.addEventListener("resize", () => { renderProgram(); if (S.seq) renderTimeline(); });
  window.FilmocityTimeline.observeWidth($("#tlBody"), () => { if (S.seq) renderTimeline(); }, window.ResizeObserver);
  $("#prgFit").onchange = e => { const v = e.target.value; const cv = $("#prgCanvas"); if (v === "Fit") { cv.style.transform = ""; fitCanvas(); } else { const z = parseInt(v) / 100; cv.style.maxWidth = "none"; cv.style.maxHeight = "none"; cv.style.width = (S.seq.width * z) + "px"; cv.style.height = (S.seq.height * z) + "px"; } $("#prgScreen").style.overflow = v === "Fit" ? "hidden" : "auto"; $("#prgScreen").style.display = v === "Fit" ? "grid" : "block"; };
  const scr = $("#prgScreen"); scr.ondragover = e => { if ([...e.dataTransfer.types].includes("application/x-filmocity-media")) { e.preventDefault(); scr.style.outline = "2px dashed var(--accent2)"; } }; scr.ondragleave = () => { scr.style.outline = ""; }; scr.ondrop = e => { scr.style.outline = ""; e.preventDefault(); return dropMedia(e, null, S.t, e.shiftKey ? "insert" : "overwrite", "drop on program monitor"); };
  $("#prgGrid").onclick = () => { $("#prgScreen").classList.toggle("checker"); $("#prgGrid").classList.toggle("active"); };
  // scrub bars under both monitors
  bindMonitorScrub($("#prgScrub"), false); bindMonitorScrub($("#srcScrub"), true); $$("[data-tlopt]").forEach(b => { const k = b.dataset.tlopt; const paint = () => b.textContent = (S.tlopt[k] ? "✓ " : "   ") + b.textContent.replace(/^[✓ ]+/, ""); paint(); b.onclick = () => { S.tlopt[k] = !S.tlopt[k]; paint(); renderTimeline(); }; });
  S.linkedSel = true; $("#tlLinked").classList.add("on"); $("#tlLinked").onclick = () => { S.linkedSel = !S.linkedSel; $("#tlLinked").classList.toggle("on", S.linkedSel); };
  $("#btnAddTitle").onclick = addTitle; $("#btnMarker").onclick = addMarker; $("#srcPlay").onclick = () => { const v = $("#srcVideo"); if (!S.src) return; if (!v.paused) { v.pause(); return; } try { v.playbackRate = sourceMonitorTime(S.src, 1) - sourceMonitorTime(S.src, 0); updateSourceTime(); const pending = v.play(); pending?.catch(error => status(error.message, "err")); } catch (error) { status("Source playback rate is unavailable: " + error.message, "err"); } }; $("#srcIn").onclick = () => { setFocus("source"); markIO("in"); }; $("#srcOut").onclick = () => { setFocus("source"); markIO("out"); };
  $("#srcVideo").ontimeupdate = updateSourceTime; $("#srcVideo").onloadedmetadata = updateSourceTime; $("#btnInsert").onclick = () => insertFromSource("insert"); $("#btnOverwrite").onclick = () => insertFromSource("overwrite");
  $("#srcPanel").onmousedown = () => { const ecOn = $("#pane-ec") && $("#pane-ec").classList.contains("on") && $("#srcPanel").contains($("#pane-ec")); setFocus(ecOn ? "panel" : "source"); }; $("#prgPanel").onmousedown = () => setFocus("program"); $("#timelinePanel").onmousedown = () => setFocus("program"); $("#projectPanel").onmousedown = () => { S.focus = "project"; }; const rt = $("#rightTop"); if (rt) rt.onmousedown = () => { S.focus = "panel"; }; $$(".tabs button[data-pane]").forEach(b => b.addEventListener("mousedown", () => { setTimeout(() => { if (["ec", "mixer", "color", "audio", "gfx", "caps", "props", "scopes", "meters", "tc", "ref", "queue", "hist", "events", "meta", "info", "fx", "markers", "browser"].includes(b.dataset.pane)) S.focus = "panel"; }, 0); }));
  $$("[data-tool]").forEach(b => b.onclick = () => setTool(b.dataset.tool)); $("#toolSnap").onclick = () => { S.snap = !S.snap; $("#toolSnap").classList.toggle("active", S.snap); }; $("#zoomIn").onclick = () => setZoom(1.5); $("#zoomOut").onclick = () => setZoom(1 / 1.5);
  $("#fileInput").onchange = async e => { const input = e.target; try { await uploadMediaFiles(input.files); } finally { input.value = ""; } };
  const pp = $("#projectPanel"); pp.ondragover = e => { e.preventDefault(); pp.classList.add("drop"); }; pp.ondragleave = () => pp.classList.remove("drop"); pp.ondrop = async e => { e.preventDefault(); pp.classList.remove("drop"); await uploadMediaFiles(e.dataTransfer.files); };
  $$(".menu").forEach(mn => { mn.querySelector(":scope > button").onclick = ev => { ev.stopPropagation(); const open = mn.classList.contains("open"); $$(".menu").forEach(x => x.classList.remove("open")); if (!open) mn.classList.add("open"); }; }); document.addEventListener("click", () => $$(".menu").forEach(x => x.classList.remove("open")));
  window.addEventListener("keydown", keys);
  $("#btnNewSeq").onclick = () => newSequence(); $("#btnNest").onclick = nestSelected; $("#btnAdjust").onclick = addAdjustmentLayer;
  $("#tlBody").addEventListener("wheel", e => { if (!e.altKey) return; e.preventDefault(); const body = $("#tlBody"), tAt = xToT(e.clientX), f = e.deltaY < 0 ? 1.25 : 0.8; S.pps = Math.min(600, Math.max(6, S.pps * f)); renderTimeline(); body.scrollLeft = tAt * S.pps - (e.clientX - body.getBoundingClientRect().left); }, { passive: false }); $("#prgCanvas").onmousedown = canvasDrag; $("#prgScreen").onmousedown = ev => { if (ev.target !== $("#prgCanvas") && S.tool === "select" && monitorHit(ev)) canvasDrag(ev); }; $("#prgScreen").onmousemove = ev => { if (ev.target === $("#prgCanvas")) return; const h = S.tool === "select" ? monitorHit(ev) : null; $("#prgScreen").style.cursor = h ? (h.kind === "scale" ? "nwse-resize" : "grab") : ""; }; $("#prgCanvas").addEventListener("wheel", canvasWheel, { passive: false }); resizers();
}
window.CR = { S, $, $$, api, CLIENT, projectSaveState, workflowRequest, workflowAction, workflowUpload, previewAudioDucking, applyAudioDucking, importXml, startTranscription, startProjectPackage, startMediaCollection, applyMediaCollection, applyBackgroundTranscript, retryBackgroundTask, startClipAnalysis, reviewAnalysisTask, applyAnalysisTask, startRenderReplace, reviewRenderReplaceTask, applyRenderReplaceTask, flushSaves, exportProgramFrame, fmtTC, parseTC, mediaRate, clipDur, clipEnd, seqDur, seqDurOf, frame, uid, status, deep, kfVal, trackOf, clipById, selectedClips, applyOps, watchEditGesture, captureGestureFields, keyframesWithValue, canEdit, proposalDecision, prepareProposalPreview, savedVersionCatalog, restoreSavedVersion, saveSnapshot, loadProject, refreshProjectSelection, placeMedia, switchSeq, newSequence, nestSelected, captureNestSelection, reviewNestSelection, nestReviewCurrent, applyNestSelection, addAdjustmentLayer, ACTIONS, addGraphic, applyAudioTransition, toggleEnabled, liftExtract, copySel, pasteClips, pasteAttributes, ATTRIBUTE_GROUPS, captureAttributePaste, attributeOwnerCurrent, reviewAttributePaste, attributeReviewCurrent, applyAttributePaste, matchFrame, cssColor, drawText, addFrameHold, extendEdit, zoomToFit, replaceWithSource, replaceFromBinSource, beginSourceRelink, currentSourceRelink, sourceRelinkCurrent, cancelSourceRelink, inspectSourceRelink, sourceRelinkReviewCurrent, applySourceRelink, splitGraphicWords, extractAudioSource, duplicateBinSources, breakoutAudioSource, requestSequenceDescription, updateSourcePresentation, setLabel, speedAt, sourceOffset, remapDuration, deleteSel, undo, redo, pushHist, gotoHist, bezierY, setTool, gotoMarker, setFocus, makeSubclip, trimEditPoint, groupSel, setFit, setAllTall, sceneDetect, seqFromClip, createMulticam, switchAngle, multicamAt, PREFS, deleteTrack, deleteEmptyTracks, addTrack, moveTrack, playAround, playInOut, toggleMaximize, setZoom, cutSel, pasteInsert, selectMatchingLabel, findInTimeline, markClip, beatMarkers, applyBrand, removeSilences, autoPunchIns, setUiOverlay, revealInProject, applyMotionPreset, MOTION_PRESETS, autoColor, colorMatch, gotoIn, gotoOut, addEditAllTracks, defaultTransitionsToSelection, alignSel, flattenMulticam, captureFlattenSelection, reviewFlattenSelection, flattenReviewCurrent, applyFlattenSelection, synchronizeSel, startSyncTask, waitSyncTask, reviewSyncTask, applySyncTask, captureAudioTargets, audioTargetsCurrent, startAudioTask, waitAudioTask, reviewAudioTask, applyAudioTask, applyManualGain, captureRecipeTargets, recipeTargetsCurrent, startRecipeTask, waitRecipeTask, reviewRecipeTask, applyRecipeTask, recipeReviewLines, startCoverTask, waitCoverTask, reviewCoverTask, coverReviewCurrent, coverArtifactUrl, createSyncSequence, previewSyncSequence, pv, getRenderedPreview, invalidateRenderedPreview, previewView, addGuide, renderGuides, upgradeCaption, renderBin, renderEntireSequence, editTextInline, refreshRenderBar, enterTrimMode, exitTrimMode, cycleTrimType, trimByType, trimToPlayhead, reverseMatchFrame, distributeSel, finishPolygon, drawSequence, offscreen, razorAt: razorAtCmd, deleteSel, applyTransition, addTitle, addMarker, addTrack, addEditAtPlayhead, seekTo, togglePlay, renderAll, renderTimeline, renderProgram, updatePlayhead, refreshSel, setTool, markIO, clearIO, toggleLink, rippleTrimToPlayhead, insertFromSource, loadSource, pool, resetPreviewVoices, refreshMediaStatus, AG, audioCtx, panels: null, showTab: null };
CR.propertyTarget = (c, tr, element) => window.FilmocityPropertyControls.target(CR, c, tr, element);
CR.bindPropertyControl = (input, options) => window.FilmocityPropertyControls.bind(CR, input, options);
CR.drafts = drafts;
CR.hasUnsavedEdits = () => { const state = projectSaveState(); return !!(S.gesture || state.pending || state.error); };
CR.reloadAfterRecovery = () => loadProject(false, { recovery: true });
CR.savePendingCount = () => [...SAVE_STATES.values()].reduce((total, state) => total + state.pending, S.gesture ? 1 : 0);
window.addEventListener("beforeunload", event => {
  const state = projectSaveState();
  if (S.gesture || state.pending || state.error || window.FilmocityWorkflow?.hasDrafts()) { event.preventDefault(); event.returnValue = ""; }
});
bind(); connectWS(); loadKeymap().then(loadProject).catch(error => {
  if (error.code !== "project_recovery_required") status("Could not open the project: " + error.message, "err");
});
})();

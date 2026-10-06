/* Filmocity v0.3 — extras: mask + audio FX sections in Effect Controls, keyboard shortcut editor, Premiere XML import. */
(() => {
const CR = window.CR; const { S, $, $$, api, fmtTC, clipDur, deep, status, applyOps } = CR;
const f = (label, html) => `<div class="fld"><label>${label}</label>${html}<span></span></div>`; const val = (v, d = 2) => `<span class="val">${Number(v).toFixed(d)}</span>`;
let stabilizationPending = false;
CR.analyzeStabilization = async (c, tr, button) => {
  const current = CR.propertyTarget(c, tr, button);
  if (stabilizationPending || !current() || !CR.canEdit()) return false;
  const project = S.proj, sequence = S.seq, mediaId = c.media_id;
  stabilizationPending = true; button.disabled = true;
  try {
    const state = await CR.flushSaves();
    if (!current() || project !== S.proj || sequence !== S.seq || state !== CR.projectSaveState() || state.error)
      throw new Error("The selected project changed or has unsaved edits. Select the source again.");
    const basis = { ...S.context, sequence: sequence.id };
    const settings = { shakiness: 5, force: !!project.media[mediaId]?.stab_trf };
    status("Inspecting the source for stabilization…");
    const review = await api.json("POST", "/api/stabilize/inspect", {
      media_id: mediaId, ...settings, actor: "human", client: CR.CLIENT, _context: { ...S.context }
    });
    if (!current() || project !== S.proj || sequence !== S.seq || c.media_id !== mediaId)
      throw new Error("The selected source changed. Select it again before analyzing.");
    if (!review || review.ok !== true || review.kind !== "stabilization" || review.requested_media_id !== mediaId
        || !/^[0-9a-f]{64}$/.test(review.fingerprint || "")
        || ["workspace", "project", "revision"].some(k => review.context?.[k] !== basis[k])
        || review.settings?.shakiness !== settings.shakiness || review.settings?.force !== settings.force)
      throw new Error("The source inspection does not match this selection. Inspect it again.");
    status("Analyzing motion for Warp Stabilizer…");
    const result = await CR.workflowRequest(async context => {
      const reply = await api.json("POST", "/api/stabilize", {
        media_id: mediaId, ...settings, fingerprint: review.fingerprint,
        actor: "human", client: CR.CLIENT, _context: context
      });
      if (!reply || reply.ok !== true || reply.kind !== "stabilization" || reply.requested_media_id !== mediaId
          || reply.media_id !== review.media_id || typeof reply.cached !== "boolean" || typeof reply.changed !== "boolean"
          || reply.analysis?.source?.sha256 !== review.source?.sha256
          || reply.analysis?.source?.path !== review.source?.path
          || JSON.stringify(reply.analysis?.source?.stamp) !== JSON.stringify(review.source?.stamp)
          || reply.analysis?.settings?.shakiness !== settings.shakiness
          || reply.analysis?.analyzer?.sha256 !== review.analyzer?.sha256
          || reply.analysis?.owner?.workspace !== basis.workspace || reply.analysis?.owner?.project !== basis.project)
        throw new Error("The server did not confirm this source analysis. Inspect saved state before another edit.");
      return reply;
    }, basis);
    status(result.warning || "Stabilization analysis saved. Review a rendered preview before export.");
    return result;
  } catch (error) {
    status(error.message || "Stabilization failed. Inspect saved state before trying again.", "err");
    return false;
  } finally {
    stabilizationPending = false;
    if (button.isConnected) button.disabled = false;
  }
};
function videoFx(pane, c, tr) {
  const fx = c.effects || {}, cr = fx.crop || {}, ky = fx.chromakey || {}; const wrap = document.createElement("div"); wrap.className = "grp";
  wrap.innerHTML = `<h4><span class="fx">fx</span>Video Effects</h4>
    ${f("Crop L / R", `<div class="pair"><input type="number" data-fxv="crop.l" step="0.01" min="0" max="0.9" value="${cr.l || 0}"><input type="number" data-fxv="crop.r" step="0.01" min="0" max="0.9" value="${cr.r || 0}"></div>`)}${f("Crop T / B", `<div class="pair"><input type="number" data-fxv="crop.t" step="0.01" min="0" max="0.9" value="${cr.t || 0}"><input type="number" data-fxv="crop.b" step="0.01" min="0" max="0.9" value="${cr.b || 0}"></div>`)}
    ${f("Gaussian blur", `<input type="range" data-fxv="blur" min="0" max="40" step="0.5" value="${fx.blur || 0}">${val(fx.blur || 0, 1)}`)}${f("Sharpen", `<input type="range" data-fxv="sharpen" min="0" max="3" step="0.1" value="${fx.sharpen || 0}">${val(fx.sharpen || 0, 1)}`)}${f("Vignette", `<input type="range" data-fxv="vignette" min="0" max="1" step="0.05" value="${fx.vignette || 0}">${val(fx.vignette || 0)}`)}
    ${f("Chroma key", `<div class="pair"><label><input type="checkbox" data-fxv="chromakey.enabled" ${ky.enabled ? "checked" : ""}> on</label><input type="text" data-fxv="chromakey.color" value="${ky.color || "#00ff00"}" title="key color"></div>`)}${ky.enabled ? f("Similarity / blend", `<div class="pair"><input type="number" data-fxv="chromakey.similarity" step="0.01" min="0.01" max="1" value="${ky.similarity == null ? 0.2 : ky.similarity}"><input type="number" data-fxv="chromakey.blend" step="0.01" min="0" max="1" value="${ky.blend == null ? 0.05 : ky.blend}"></div>`) : ""}
    <p style="color:var(--dim);margin:4px 0 0">Crop, blur and vignette preview live; chroma key and sharpen render only (FFmpeg chromakey / unsharp).</p>`;
  pane.insertBefore(wrap, pane.lastElementChild);
  $$("[data-fxv]", wrap).forEach(inp => { const commit = () => { const k = inp.dataset.fxv; const v = inp.type === "checkbox" ? inp.checked : inp.type === "text" ? inp.value : parseFloat(inp.value); const nf = deep(c.effects || {}); if (k.includes(".")) { const [a, b] = k.split("."); nf[a] = { ...(nf[a] || {}), [b]: v }; } else nf[k] = v; applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, effects: nf } }], "effects", `effect ${k}`); }; CR.bindPropertyControl(inp, { c, tr, fields: ["effects"], commit, preview: () => { if (inp.type !== "range") return; inp.nextElementSibling.textContent = inp.value; c.effects = { ...(c.effects || {}), [inp.dataset.fxv]: parseFloat(inp.value) }; CR.renderProgram(); } }); });
}
function stackUI(pane, c, tr, kind) { const key = kind === "video" ? "fx_stack" : "afx_stack"; const stack = c[key] || []; if (!stack.length) return; const cat = (CR.catalog || {})[kind] || []; const wrap = document.createElement("div"); wrap.className = "grp";
  wrap.innerHTML = `<h4><span class="fx">fx</span>${kind === "video" ? "Video Effects (stack)" : "Audio Effects (stack)"}</h4>` + stack.map((fx, i) => { const spec = cat.find(e => e.type === fx.type) || { name: fx.type, params: {} }; const rows = Object.entries(spec.params).map(([pn, sp]) => { const v = (fx.params || {})[pn] ?? sp.default; const id = `${fx.id}|${pn}`;
      let ctl; if (sp.kind === "color") ctl = `<input type="color" data-sp="${id}" value="${String(v).startsWith("#") ? v : "#ffffff"}">`; else if (sp.kind === "bool") ctl = `<label><input type="checkbox" data-sp="${id}" ${v ? "checked" : ""}></label>`; else if (String(sp.kind).startsWith("select:")) ctl = `<select data-sp="${id}">${sp.kind.slice(7).split(",").map(o => `<option ${o === v ? "selected" : ""}>${o}</option>`).join("")}</select>`; else if (sp.kind === "text") ctl = `<input type="text" data-sp="${id}" value="${v}">`; else ctl = `<input type="range" data-sp="${id}" min="${sp.min}" max="${sp.max}" step="${sp.step}" value="${v}"><span class="val">${Number(v).toFixed(sp.step < 1 ? 2 : 0)}</span>`;
      return `<div class="fld"><span></span><label title="${pn}">${pn.replace(/_/g, " ")}</label>${ctl}</div>`; }).join("");
    return `<div class="grp" style="margin-top:2px;padding:4px 6px;border:1px solid var(--line2);border-radius:4px;${fx.enabled === false ? "opacity:.55" : ""}"><h4 style="margin:0 0 2px"><button class="kfsw ${fx.enabled !== false ? "on" : ""}" data-fxen="${fx.id}" title="toggle effect">fx</button>${spec.name}<span class="sp"></span>${fx.type === "stabilize" ? `<button data-stab="${c.id}" title="Run the analysis pass (vidstabdetect) on this clip's media">${S.proj.media[c.media_id] && S.proj.media[c.media_id].stab_trf ? "Re-analyze" : "Analyze"}</button>` : ""}<button class="tb" data-fxmv="${fx.id}|-1" title="move up">↑</button><button class="tb" data-fxmv="${fx.id}|1" title="move down">↓</button><button class="tb" data-fxreset="${fx.id}" title="reset">↺</button><button class="tb danger" data-fxdel="${fx.id}" title="remove">×</button></h4>${rows}${spec.note ? `<p style="color:var(--dim);margin:2px 0 0">${spec.note}</p>` : ""}</div>`; }).join("");
  pane.insertBefore(wrap, pane.lastElementChild);
  const save = (list, reason) => applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, [key]: list } }], "effect", reason);
  $$("[data-sp]", wrap).forEach(inp => { const [fid, pn] = inp.dataset.sp.split("|"); const read = () => inp.type === "checkbox" ? (inp.checked ? 1 : 0) : (inp.type === "range" || inp.type === "number") ? parseFloat(inp.value) : inp.value; const changed = () => { const list = deep(c[key] || []); const fx = list.find(x => x.id === fid); fx.params = { ...(fx.params || {}), [pn]: read() }; return { list, fx }; };
    CR.bindPropertyControl(inp, { c, tr, fields: [key], commit: () => { const { list, fx } = changed(); save(list, `effect ${fx.type}.${pn}`); }, preview: () => { if (inp.type !== "range") return; inp.nextElementSibling.textContent = Number(inp.value).toFixed(parseFloat(inp.step) < 1 ? 2 : 0); c[key] = changed().list; CR.renderProgram(); } }); });
  $$("[data-fxen]", wrap).forEach(b => b.onclick = () => { const list = deep(stack); const fx = list.find(x => x.id === b.dataset.fxen); fx.enabled = fx.enabled === false; save(list, `effect ${fx.type} ${fx.enabled ? "on" : "off"}`); });
  $$("[data-fxdel]", wrap).forEach(b => b.onclick = () => save(stack.filter(x => x.id !== b.dataset.fxdel), "remove effect")); $$("[data-fxreset]", wrap).forEach(b => b.onclick = () => { const list = deep(stack); const fx = list.find(x => x.id === b.dataset.fxreset); fx.params = {}; save(list, `reset ${fx.type}`); });
  $$("[data-fxmv]", wrap).forEach(b => b.onclick = () => { const [fid, d] = b.dataset.fxmv.split("|"); const list = deep(stack); const i = list.findIndex(x => x.id === fid), j = i + (+d); if (j < 0 || j >= list.length) return; [list[i], list[j]] = [list[j], list[i]]; save(list, "reorder effects"); });
  $$("[data-stab]", wrap).forEach(b => b.onclick = () => CR.analyzeStabilization(c, tr, b)); }
CR.ecExtras = (pane, c, tr) => {
  if (!CR.catalog && CR.panels) fetch("/api/effects").then(r => r.json()).then(cat => { CR.catalog = cat; CR.panels.renderEC(); });
  if (tr.kind === "video") stackUI(pane, c, tr, "video");
  if (c.media_id && S.proj.media[c.media_id] && S.proj.media[c.media_id].has_audio) stackUI(pane, c, tr, "audio");
  if (tr.kind !== "video" || (!c.media_id && !c.sequence_id)) { audioFx(pane, c, tr); return; }
  if (c.media_id) videoFx(pane, c, tr);
  const mk = c.mask || {}; const wrap = document.createElement("div"); wrap.className = "grp";
  wrap.innerHTML = `<h4>Mask<span class="sp"></span><select data-mk="type"><option value="">none</option><option value="rect" ${mk.type === "rect" ? "selected" : ""}>rectangle</option><option value="ellipse" ${mk.type === "ellipse" ? "selected" : ""}>ellipse</option></select></h4>
    ${mk.type ? `${f("Left / top", `<div class="pair"><input type="number" data-mk="x" step="0.01" min="0" max="1" value="${mk.x == null ? 0.1 : mk.x}"><input type="number" data-mk="y" step="0.01" min="0" max="1" value="${mk.y == null ? 0.1 : mk.y}"></div>`)}
    ${f("Width / height", `<div class="pair"><input type="number" data-mk="w" step="0.01" min="0.01" max="1" value="${mk.w == null ? 0.8 : mk.w}"><input type="number" data-mk="h" step="0.01" min="0.01" max="1" value="${mk.h == null ? 0.8 : mk.h}"></div>`)}
    ${f("Feather (px)", `<input type="range" data-mk="feather" min="0" max="300" step="1" value="${mk.feather || 0}">${val(mk.feather || 0, 0)}`)}${f("Invert", `<label><input type="checkbox" data-mk="invert" ${mk.invert ? "checked" : ""}> inverted</label>`)}
    <p style="color:var(--dim);margin:4px 0 0">Values are fractions of the frame. The monitor shows a hard edge; the render feathers exactly.</p>` : ""}`;
  pane.insertBefore(wrap, pane.lastElementChild);
  $$("[data-mk]", wrap).forEach(inp => { const commit = () => { const k = inp.dataset.mk; let v = inp.type === "checkbox" ? inp.checked : inp.tagName === "SELECT" ? inp.value : parseFloat(inp.value); const nm = k === "type" ? (v ? { type: v, x: 0.1, y: 0.1, w: 0.8, h: 0.8, feather: 20, invert: false, ...(mk.type ? mk : {}), type: v } : {}) : { ...mk, [k]: v };
      applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, mask: nm } }], "mask", `mask ${k}`); }; CR.bindPropertyControl(inp, { c, tr, commit, preview: () => { if (inp.type === "range") inp.nextElementSibling.textContent = inp.value; } }); });
  audioFx(pane, c, tr);
};
function audioFx(pane, c, tr) {
  const m = c.media_id && S.proj.media[c.media_id]; if (!m || !m.has_audio) return; const fx = c.audio_fx || {}, eq = fx.eq || {}, co = fx.comp || {}, dn = fx.denoise || {};
  const wrap = document.createElement("div"); wrap.className = "grp";
  wrap.innerHTML = `<h4>Audio effects</h4>
    ${f("EQ low (dB)", `<input type="range" data-fx="eq.low_db" min="-12" max="12" step="0.5" value="${eq.low_db || 0}">${val(eq.low_db || 0, 1)}`)}${f("EQ mid (dB)", `<input type="range" data-fx="eq.mid_db" min="-12" max="12" step="0.5" value="${eq.mid_db || 0}">${val(eq.mid_db || 0, 1)}`)}${f("EQ high (dB)", `<input type="range" data-fx="eq.high_db" min="-12" max="12" step="0.5" value="${eq.high_db || 0}">${val(eq.high_db || 0, 1)}`)}
    ${f("Compressor", `<label><input type="checkbox" data-fx="comp.enabled" ${co.enabled ? "checked" : ""}> on</label>`)}${co.enabled ? `${f("Threshold (dB)", `<input type="number" data-fx="comp.threshold_db" value="${co.threshold_db == null ? -18 : co.threshold_db}">`)}${f("Ratio", `<input type="number" data-fx="comp.ratio" step="0.5" min="1" value="${co.ratio || 3}">`)}${f("Attack / release", `<div class="pair"><input type="number" data-fx="comp.attack_ms" value="${co.attack_ms || 20}"><input type="number" data-fx="comp.release_ms" value="${co.release_ms || 200}"></div>`)}${f("Makeup (dB)", `<input type="number" data-fx="comp.makeup_db" step="0.5" value="${co.makeup_db || 0}">`)}` : ""}
    ${f("De-noise", `<label><input type="checkbox" data-fx="denoise.enabled" ${dn.enabled ? "checked" : ""}> on</label>`)}${dn.enabled ? f("Reduction (dB)", `<input type="range" data-fx="denoise.db" min="1" max="40" step="1" value="${dn.db || 12}">${val(dn.db || 12, 0)}`) : ""}
    ${f("Limiter", `<label><input type="checkbox" data-fx="limiter" ${fx.limiter ? "checked" : ""}> −0.45 dBFS sample ceiling (export)</label>`)}
    <p style="color:var(--dim);margin:4px 0 0">EQ, compression and limiting affect live playback; dynamics are estimates. Use rendered preview for noise reduction and final mix checks. The limiter is not a true-peak limiter.</p>`;
  pane.insertBefore(wrap, pane.lastElementChild);
  $$("[data-fx]", wrap).forEach(inp => { const commit = () => { const k = inp.dataset.fx; const v = inp.type === "checkbox" ? inp.checked : parseFloat(inp.value); const nf = deep(c.audio_fx || {}); if (k.includes(".")) { const [a, b] = k.split("."); nf[a] = { ...(nf[a] || {}), [b]: v }; } else nf[k] = v;
    applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, audio_fx: nf } }], "audio_fx", `audio fx ${k}`); }; CR.bindPropertyControl(inp, { c, tr, commit, preview: () => { if (inp.type === "range") inp.nextElementSibling.textContent = inp.value; } }); });
}
// ---- value graph editor with bezier handles ----
function curveEditor(pane, c, tr, key) {
  const list = deep(c.keyframes[key] || []).sort((a, b) => a.t - b.t); if (list.length < 2) return; const cv = document.createElement("canvas"); cv.className = "curve"; cv.width = 560; cv.height = 192; cv.title = `${key} — drag points: value · Alt+drag a point: bezier out-handle · Shift+drag: in-handle of the next key`;
  const lane = pane.querySelector(`.kflane[data-lane="${key}"]`); if (!lane) return; lane.after(cv);
  const D = Math.max(clipDur(c), 1e-6); const vals = list.map(k => k.v).concat(list.flatMap(k => [k.v + ((k.o || [0, 0])[1]), k.v + ((k.i || [0, 0])[1])])); let vmin = Math.min(...vals), vmax = Math.max(...vals); if (vmax - vmin < 1e-6) { vmin -= 1; vmax += 1; } const pad = (vmax - vmin) * 0.15; vmin -= pad; vmax += pad;
  const X = t => 12 + (t / D) * (cv.width - 24), Y = v => cv.height - 12 - ((v - vmin) / (vmax - vmin)) * (cv.height - 24), invY = y => vmin + ((cv.height - 12 - y) / (cv.height - 24)) * (vmax - vmin), invX = x => Math.max(0, Math.min(D, ((x - 12) / (cv.width - 24)) * D));
  const draw = () => { const ctx = cv.getContext("2d"); ctx.clearRect(0, 0, cv.width, cv.height); ctx.strokeStyle = "#2a2a2a"; for (let i = 0; i <= 4; i++) { const y = 12 + i * (cv.height - 24) / 4; ctx.beginPath(); ctx.moveTo(12, y); ctx.lineTo(cv.width - 12, y); ctx.stroke(); }
    ctx.strokeStyle = "#9fc3ff"; ctx.lineWidth = 2; ctx.beginPath(); for (let px = 12; px <= cv.width - 12; px += 2) { const v = CR.kfVal(list, invX(px), list[0].v); px === 12 ? ctx.moveTo(px, Y(v)) : ctx.lineTo(px, Y(v)); } ctx.stroke(); ctx.lineWidth = 1;
    list.forEach((k, i) => { if (k.e === "bezier" && list[i + 1]) { const n = list[i + 1], d = n.t - k.t, [ox, oy] = k.o || [0.33, 0], [ix, iy] = n.i || [0.33, 0]; ctx.strokeStyle = "#f6c14a"; ctx.beginPath(); ctx.moveTo(X(k.t), Y(k.v)); ctx.lineTo(X(k.t + ox * d), Y(k.v + oy)); ctx.stroke(); ctx.beginPath(); ctx.moveTo(X(n.t), Y(n.v)); ctx.lineTo(X(n.t - ix * d), Y(n.v + iy)); ctx.stroke(); ctx.fillStyle = "#f6c14a"; ctx.fillRect(X(k.t + ox * d) - 3, Y(k.v + oy) - 3, 6, 6); ctx.fillRect(X(n.t - ix * d) - 3, Y(n.v + iy) - 3, 6, 6); }
      ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(X(k.t), Y(k.v), 4.5, 0, Math.PI * 2); ctx.fill(); });
    const rel = Math.max(0, Math.min(D, S.t - c.start)); ctx.strokeStyle = "#9fc3ff"; ctx.beginPath(); ctx.moveTo(X(rel), 0); ctx.lineTo(X(rel), cv.height); ctx.stroke(); ctx.fillStyle = "#8f8f8f"; ctx.font = "11px sans-serif"; ctx.fillText(vmax.toFixed(2), 14, 10); ctx.fillText(vmin.toFixed(2), 14, cv.height - 2); };
  draw();
  const current = CR.propertyTarget(c, tr, cv);
  cv.onmousedown = ev => { if (!current() || !CR.canEdit()) return; const before = deep(list), rollback = () => { list.splice(0, list.length, ...before); draw(); }; const r = cv.getBoundingClientRect(), sx = cv.width / r.width, sy = cv.height / r.height; const mx = (ev.clientX - r.left) * sx, my = (ev.clientY - r.top) * sy; let hit = -1, best = 12;
    list.forEach((k, i) => { const d = Math.hypot(X(k.t) - mx, Y(k.v) - my); if (d < best) { best = d; hit = i; } }); if (hit < 0) return; ev.preventDefault();
    const k = list[hit], mode = ev.altKey ? "out" : ev.shiftKey ? "in" : "value"; if (mode === "out" && list[hit + 1]) k.e = "bezier"; if (mode === "in" && hit > 0) list[hit - 1].e = "bezier";
    const move = e => { const x = (e.clientX - r.left) * sx, y = (e.clientY - r.top) * sy; if (mode === "value") k.v = +invY(y).toFixed(3); else if (mode === "out" && list[hit + 1]) { const d = list[hit + 1].t - k.t; k.o = [Math.max(0, Math.min(1, (invX(x) - k.t) / d)), +(invY(y) - k.v).toFixed(3)]; } else if (mode === "in" && hit > 0) { const p = list[hit - 1], d = k.t - p.t; k.i = [Math.max(0, Math.min(1, (k.t - invX(x)) / d)), +(invY(y) - k.v).toFixed(3)]; } draw(); };
    const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, keyframes: { ...(c.keyframes || {}), [key]: list } } }], "curve", `curve ${key}`); }; CR.watchEditGesture(ev, move, up, rollback, { valid: current }); };
}
// ---- clip context menu ----
function clipMenu(ev, c, tr) { document.querySelectorAll(".ctx").forEach(x => x.remove()); const m = document.createElement("div"); m.className = "ctx"; m.style.left = Math.min(ev.clientX, window.innerWidth - 240) + "px"; m.style.top = Math.min(ev.clientY, window.innerHeight - 380) + "px";
  const items = [["Enable / disable", "shift+e", CR.toggleEnabled], ["Speed / duration…", "ctrl+r", () => CR.panels.speedDialog()], ["Add frame hold", "ctrl+shift+h", CR.addFrameHold], ["Nest", "ctrl+shift+n", CR.nestSelected], ["Link / unlink audio", "ctrl+l", CR.toggleLink], ["Replace with source clip", "ctrl+shift+r", CR.replaceWithSource], ["Paste attributes", "ctrl+shift+v", CR.pasteAttributes], null, ["Delete", "del", () => CR.deleteSel(false)], ["Ripple delete", "shift+del", () => CR.deleteSel(true)]];
  m.innerHTML = items.map(it => it ? `<button data-i="${items.indexOf(it)}">${it[0]}<span class="k" style="float:right;color:var(--dim)">${it[1]}</span></button>` : "<hr>").join("") + `<hr><div style="padding:4px 10px;color:var(--dim)">Label</div><div style="display:flex;gap:6px;padding:2px 10px 6px">${["", "#e05b5b", "#e0a15b", "#e0d85b", "#7bd67b", "#5bb3e0", "#b07be0", "#e07bc7"].map(col => `<span class="sw" data-lab="${col}" style="background:${col || "#444"};cursor:pointer;border:1px solid #666" title="${col || "none"}"></span>`).join("")}</div>`;
  document.body.appendChild(m); m.querySelectorAll("[data-i]").forEach(b => b.onclick = () => { items[+b.dataset.i][2](); m.remove(); }); m.querySelectorAll("[data-lab]").forEach(sw => sw.onclick = () => { CR.setLabel(sw.dataset.lab || null); m.remove(); });
  setTimeout(() => document.addEventListener("mousedown", e => { if (!m.contains(e.target)) m.remove(); }, { once: true }), 0); }
// ---- RGB curves editor (master) for the Color panel ----
CR.curvesEditor = (pane, c, tr) => { const pts = (c.color || {}).curves || [[0, 0], [0.25, 0.25], [0.5, 0.5], [0.75, 0.75], [1, 1]]; const cv = document.createElement("canvas"); cv.className = "curvebox"; cv.width = 300; cv.height = 300; const wrap = document.createElement("div"); wrap.className = "grp"; wrap.innerHTML = `<h4>Curves (master)<span class="sp"></span><button id="curveValues" data-curve-edit="rgb:master">Edit points…</button><button id="curvesReset">Reset</button></h4>`; wrap.appendChild(cv); pane.appendChild(wrap);
  const list = pts.map(p => [...p]); const X = x => 8 + x * 284, Y = y => 292 - y * 284;
  const draw = () => { const ctx = cv.getContext("2d"); ctx.clearRect(0, 0, 300, 300); ctx.strokeStyle = "#2a2a2a"; for (let i = 0; i <= 4; i++) { ctx.beginPath(); ctx.moveTo(X(i / 4), Y(0)); ctx.lineTo(X(i / 4), Y(1)); ctx.stroke(); ctx.beginPath(); ctx.moveTo(X(0), Y(i / 4)); ctx.lineTo(X(1), Y(i / 4)); ctx.stroke(); } ctx.strokeStyle = "#555"; ctx.beginPath(); ctx.moveTo(X(0), Y(0)); ctx.lineTo(X(1), Y(1)); ctx.stroke();
    const srt = [...list].sort((a, b) => a[0] - b[0]); ctx.strokeStyle = "#9fc3ff"; ctx.lineWidth = 2; ctx.beginPath(); for (let i = 0; i <= 100; i++) { const x = i / 100; let y; if (x <= srt[0][0]) y = srt[0][1]; else if (x >= srt[srt.length - 1][0]) y = srt[srt.length - 1][1]; else { let j = 0; while (srt[j + 1][0] < x) j++; const [x0, y0] = srt[j], [x1, y1] = srt[j + 1]; const p = (x - x0) / Math.max(x1 - x0, 1e-6); y = y0 + (y1 - y0) * (p * p * (3 - 2 * p)); } i ? ctx.lineTo(X(x), Y(y)) : ctx.moveTo(X(x), Y(y)); } ctx.stroke(); ctx.lineWidth = 1; ctx.fillStyle = "#fff"; for (const [x, y] of list) { ctx.beginPath(); ctx.arc(X(x), Y(y), 4, 0, Math.PI * 2); ctx.fill(); } };
  draw(); const commit = () => applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, color: { ...(c.color || {}), curves: [...list].sort((a, b) => a[0] - b[0]) } } }], "curves", "curves");
  const current = CR.propertyTarget(c, tr, cv);
  cv.onmousedown = ev => { if (!current() || !CR.canEdit()) return; const before = deep(list), rollback = () => { list.splice(0, list.length, ...before); draw(); }; const r = cv.getBoundingClientRect(), sx = 300 / r.width, sy = 300 / r.height; const mx = (ev.clientX - r.left) * sx, my = (ev.clientY - r.top) * sy; let hit = -1, best = 10; list.forEach((p, i) => { const d = Math.hypot(X(p[0]) - mx, Y(p[1]) - my); if (d < best) { best = d; hit = i; } }); if (hit < 0) { list.push([(mx - 8) / 284, (292 - my) / 284]); hit = list.length - 1; } if (ev.altKey && list.length > 2) { list.splice(hit, 1); draw(); commit(); return; }
    const move = e => { const x = Math.max(0, Math.min(1, ((e.clientX - r.left) * sx - 8) / 284)), y = Math.max(0, Math.min(1, (292 - (e.clientY - r.top) * sy) / 284)); list[hit] = [hit === 0 ? 0 : hit === list.length - 1 && list.length === pts.length ? list[hit][0] : x, y]; draw(); }; const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); commit(); }; CR.watchEditGesture(ev, move, up, rollback, { valid: current }); };
  const valuesButton = wrap.querySelector("#curveValues"); valuesButton.onclick = () => window.FilmocityCurveValues.open(CR, { kind: "rgb", c, tr, origin: valuesButton });
  wrap.querySelector("#curvesReset").onclick = () => applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, color: { ...(c.color || {}), curves: null } } }], "curves", "reset curves"); };
function trackMenu(ev, tr) { document.querySelectorAll(".ctx").forEach(x => x.remove()); const m = document.createElement("div"); m.className = "ctx"; m.style.left = ev.clientX + "px"; m.style.top = ev.clientY + "px"; const si = S.proj.sequences.indexOf(S.seq), ti = S.seq.tracks.indexOf(tr);
  const items = [["Rename Track…", () => { const n = prompt("Track name", tr.name || tr.id); if (n != null) applyOps([{ op: "set", path: `/sequences/${si}/tracks/${ti}/name`, value: n }], "track", "rename track"); }], ["Add Track", () => CR.addTrack(tr.kind)], ["Move Track Up", () => CR.moveTrack(tr.id, 1)], ["Move Track Down", () => CR.moveTrack(tr.id, -1)], ["Delete Track", () => CR.deleteTrack(tr.id)], ["Delete All Empty Tracks", CR.deleteEmptyTracks], null, [tr.locked ? "Unlock Track" : "Lock Track", () => applyOps([{ op: "set", path: `/sequences/${si}/tracks/${ti}/locked`, value: !tr.locked }], "track", "lock")], [tr.muted ? (tr.kind === "video" ? "Enable Track Output" : "Unmute Track") : (tr.kind === "video" ? "Disable Track Output" : "Mute Track"), () => applyOps([{ op: "set", path: `/sequences/${si}/tracks/${ti}/muted`, value: !tr.muted }], "track", "mute")], [tr.sync_lock === false ? "Enable Sync Lock" : "Disable Sync Lock", () => applyOps([{ op: "set", path: `/sequences/${si}/tracks/${ti}/sync_lock`, value: tr.sync_lock === false }], "track", "sync lock")], null, [S.tall[tr.id] ? "Minimize Track Height" : "Expand Track Height", () => { S.tall[tr.id] = !S.tall[tr.id]; CR.renderTimeline(); }]];
  m.innerHTML = items.map((it, i) => it ? `<button data-i="${i}">${it[0]}</button>` : "<hr>").join(""); document.body.appendChild(m); m.querySelectorAll("[data-i]").forEach(b => b.onclick = () => { items[+b.dataset.i][1](); m.remove(); }); setTimeout(() => document.addEventListener("mousedown", e => { if (!m.contains(e.target)) m.remove(); }, { once: true }), 0); }
function timelineMenu(ev, tr, t) { document.querySelectorAll(".ctx").forEach(x => x.remove()); const m = document.createElement("div"); m.className = "ctx"; m.style.left = ev.clientX + "px"; m.style.top = ev.clientY + "px"; const items = [["Paste here", () => { CR.seekTo(t); CR.pasteClips(); }], ["Add Marker here", () => { CR.seekTo(t); CR.addMarker(); }], ["Add Edit here", () => { CR.seekTo(t); CR.addEditAtPlayhead(); }], null, ["Add Video Track", () => CR.addTrack("video")], ["Add Audio Track", () => CR.addTrack("audio")], ["Delete All Empty Tracks", CR.deleteEmptyTracks], null, ["Zoom to Sequence", CR.zoomToFit], ["Select All", () => CR.ACTIONS.select_all[2]()]]; m.innerHTML = items.map((it, i) => it ? `<button data-i="${i}">${it[0]}</button>` : "<hr>").join(""); document.body.appendChild(m); m.querySelectorAll("[data-i]").forEach(b => b.onclick = () => { items[+b.dataset.i][1](); m.remove(); }); setTimeout(() => document.addEventListener("mousedown", e => { if (!m.contains(e.target)) m.remove(); }, { once: true }), 0); }
let shortcutController;
function keysDialog() {
  shortcutController ||= window.FilmocityKeyboard.createShortcutController({ document, actions: CR.ACTIONS, getKeymap: () => S.keymap,
    save: async keymap => {
      const result = await api.json("PUT", "/api/settings", { keymap });
      if (!result?.keymap || !Object.entries(keymap).every(([id, key]) => result.keymap[id] === key)) throw new Error("The server did not confirm the shortcut settings");
      S.keymap = keymap;
    },
    changed: () => window.FilmocityCommands.updateShortcutHints(CR),
  });
  shortcutController.open();
}
$("#xmlInput").onchange = async e => {
  const input=e.target,file=input.files[0];if(!file||input.disabled)return;
  const basis={...S.context,sequence:S.seq?.id};input.disabled=true;
  try{const xml=await file.text();const result=await CR.importXml(xml,basis);status(result.message);}
  catch(error){status('XML import: '+error.message,'err');}
  finally{input.disabled=false;input.value='';}
};
// ---- docking-lite: drag a panel tab to another dock's tab bar ----
(function tabDocking() { let dragPane = null; $$(".tabs[data-dock] button[data-pane]").forEach(b => { b.draggable = true; b.ondragstart = e => { dragPane = b.dataset.pane; e.dataTransfer.setData("text/pane", dragPane); }; });
  $$(".tabs[data-dock]").forEach(bar => { bar.ondragover = e => { if (dragPane) { e.preventDefault(); bar.style.outline = "1px dashed var(--accent2)"; } }; bar.ondragleave = () => { bar.style.outline = ""; }; bar.ondrop = e => { e.preventDefault(); bar.style.outline = ""; const name = e.dataTransfer.getData("text/pane") || dragPane; dragPane = null; if (!name) return; const btn = document.querySelector(`.tabs button[data-pane="${name}"]`), pane = document.querySelector(`#pane-${name}`); if (!btn || !pane || bar.contains(btn)) return; const srcBar = btn.parentElement; bar.appendChild(btn); const dstBd = bar.closest(".panel").querySelector(":scope > .bd"); pane.classList.remove("on"); dstBd.appendChild(pane); if (!$$("button.on", srcBar).length && srcBar.firstElementChild) CR.showTab(srcBar.firstElementChild.dataset.pane); CR.showTab(name); api.json("PUT", "/api/settings", { panes: Object.fromEntries($$(".tabs[data-dock] button[data-pane]").map(x => [x.dataset.pane, x.closest(".tabs").dataset.dock])) }).catch(() => { }); }; }); })();
CR.extras = { keysDialog, curveEditor, clipMenu, trackMenu, timelineMenu };
})();

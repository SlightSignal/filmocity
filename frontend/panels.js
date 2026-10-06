/* Filmocity v0.2 — panels: Effect Controls (with keyframes), Color, Captions, Proposals, Audio; dialogs and menus. */
(() => {
const CR = window.CR; const { S, $, $$, api, fmtTC, parseTC, clipDur, clipEnd, seqDur, frame, uid, status, deep, kfVal, trackOf, applyOps } = CR;
const f = (label, html, kfKey) => `<div class="fld">${kfKey !== undefined ? `<button class="kfb" data-kf="${kfKey}" title="Toggle animation / add keyframe at playhead">◷</button>` : `<span></span>`}<label>${label}</label>${html}</div>`;
const val = (v, d = 2) => `<span class="val">${Number(v).toFixed(d)}</span>`;

// ---------- Effect Controls ----------
function renderEC() {
  if (S.gesture) { S.gesture.panelsNeeded = true; return; }
  const pane0 = $("#pane-ec"), sel = CR.selectedClips(); if (sel.length !== 1) { pane0.innerHTML = `<div class="empty">${sel.length ? sel.length + " clips selected" : "Select a clip in the timeline."}<br><span class="kbd">V</span> select · <span class="kbd">B</span> ripple · <span class="kbd">N</span> roll · <span class="kbd">Y</span> slip · <span class="kbd">U</span> slide · <span class="kbd">C</span> razor<br><span class="kbd">Q</span>/<span class="kbd">W</span> ripple trim to playhead · <span class="kbd">Ctrl+K</span> add edit · <span class="kbd">Ctrl+D</span> dissolve · <span class="kbd">Del</span> / <span class="kbd">Shift+Del</span></div>`; return; }
  const { c, tr } = sel[0], m = c.media_id ? S.proj.media[c.media_id] : null, tf = c.transform || {}, au = c.audio || {}, ti = c.transition_in || {}, to = c.transition_out || {}, kf = c.keyframes || {}, rel = Math.max(0, Math.min(clipDur(c), S.t - c.start));
  pane0.innerHTML = `<div class="ec"><div class="props"></div><div class="ktl"></div></div>`; const pane = pane0.querySelector(".props"); const ktl = pane0.querySelector(".ktl");

  const D = Math.max(clipDur(c), 1e-6); ktl.innerHTML = `<div style="position:absolute;inset:0;padding:6px 8px;font-family:var(--mono);font-size:10px;color:var(--dim)"><div style="display:flex;justify-content:space-between"><span>${fmtTC(c.start, S.seq.fps)}</span><span>${fmtTC(CR.clipEnd(c), S.seq.fps)}</span></div><div style="position:relative;height:calc(100% - 20px);margin-top:4px;background:#222;border:1px solid var(--line2);border-radius:3px"><div style="position:absolute;top:0;bottom:0;width:1px;background:var(--accent2);left:${Math.min(100, rel / D * 100)}%"></div>${Object.entries(kf).flatMap(([k, list], row) => list.map(x => `<div title="${k} @ ${fmtTC(x.t, S.seq.fps).slice(3)}" style="position:absolute;left:${Math.min(100, x.t / D * 100)}%;top:${8 + row * 14}px;width:7px;height:7px;background:#fff;transform:translateX(-4px) rotate(45deg)"></div>`)).join("")}${(c.time_remap || []).map(x => `<div title="speed ${Math.round(x.v * 100)}%" style="position:absolute;left:${Math.min(100, x.t / D * 100)}%;bottom:6px;width:7px;height:7px;background:#f6c14a;transform:translateX(-4px) rotate(45deg)"></div>`).join("")}</div></div>`;
  const cur = (key, dflt) => kfVal(kf[key], rel, dflt);
  const kfList = key => key === "speed" ? (c.time_remap || []) : (kf[key] || []);
  const kfnav = key => kfList(key).length ? `<div class="fld" style="grid-template-columns:22px 92px 1fr;margin:0"><span></span><span></span><span class="lbls" style="flex-wrap:wrap"><button class="tb" data-kfnav="${key}|-1" title="previous keyframe">◄</button><button class="tb" data-kfnav="${key}|0" title="add/remove keyframe at playhead">◆</button><button class="tb" data-kfnav="${key}|1" title="next keyframe">►</button><button class="tb" data-kfclear="${key}" title="remove all keyframes">✕</button><button class="curve-open" data-curve-edit="keyframes:${key}" data-kfedit="${key}" aria-label="Edit ${key.replace(/^transform\./, '').replace(/^audio\./, '').replace(/_/g, ' ')} keyframe values" title="Edit keyframe times, values and easing">Values…</button></span></div>` : "";
  const kfrow = key => kfnav(key) + (kfList(key).length ? `<div class="kflane" data-lane="${key}"><div class="ph" style="left:${Math.min(100, rel / Math.max(clipDur(c), 1e-6) * 100)}%"></div>${[...kfList(key)].sort((a, b) => a.t - b.t).map((k, i) => `<div class="d ${k.e || "linear"}" data-kfi="${key}|${i}" style="left:${Math.min(100, k.t / Math.max(clipDur(c), 1e-6) * 100)}%" title="${fmtTC(k.t, S.seq.fps).slice(3)} → ${Number(k.v).toFixed(2)} (${k.e || "linear"}) · drag to move · click: cycle easing · Alt+click: remove"></div>`).join("")}</div>` : "");
  pane.innerHTML = `
    <div class="grp"><h4><span class="fx">fx</span>${c.name || (c.title ? "Text" : c.graphic ? "Graphic" : c.adjustment ? "Adjustment Layer" : (m ? m.name : c.id))}<span class="sp"></span><span style="color:var(--dim);font-weight:400">${tr.id}</span></h4>
      ${m ? f("Motion preset", `<select id="ecMotionPreset"><option value="">Apply…</option>${Object.entries(CR.MOTION_PRESETS).map(([k, v]) => `<option value="${k}">${v.name}</option>`).join("")}</select>`) : ""}
      ${f("Name", `<input type="text" data-k="name" value="${(c.name || "").replace(/"/g, "&quot;")}" placeholder="${m ? m.name : ""}">`)}${f("Note", `<input type="text" data-k="note" value="${(c.note || "").replace(/"/g, "&quot;")}" placeholder="why this clip is here (the agent writes its rationale here too)">`)}
      ${f("Start", `<input type="text" class="tcin" data-k="start" value="${fmtTC(c.start, S.seq.fps)}">`)}${f("Duration", `<input class="tcin" data-dur="1" value="${fmtTC(clipDur(c), S.seq.fps)}" title="type a new duration and press Enter — trims the out point (Shift+Enter ripples the clips after it)">`)}
      ${m ? f("Source in / out", `<div class="pair"><input type="text" class="tcin" data-k="in_" value="${fmtTC(c.in_, CR.mediaRate(m, true), 'ndf')}"><input type="text" class="tcin" data-k="out" value="${fmtTC(c.out, CR.mediaRate(m, true), 'ndf')}"></div>`) : ""}
      ${m ? f("Speed %", `<input type="number" data-k="speed" step="5" min="10" max="800" value="${Math.round((c.speed || 1) * 100)}" ${c.time_remap ? "disabled title='time remapping is active'" : ""}>`) : ""}
      ${m && !c.hold ? f("Time remap", `<div class="pair"><input type="number" id="trSpeed" step="10" min="5" max="1000" value="${Math.round(CR.speedAt(c, rel) * 100)}" title="speed % at playhead"><button id="trClear" ${c.time_remap ? "" : "disabled"}>clear</button></div>`, "speed") + kfrow("speed") : ""}
      ${c.hold ? f("Frame hold", `<span>held at ${fmtTC(c.in_, CR.mediaRate(m, true), 'ndf')} — <button id="holdOff">release</button></span>`) : ""}</div>
    ${tr.kind === "video" && m ? f("Blend mode", `<select data-k="blend">${["normal", "multiply", "screen", "overlay", "darken", "lighten", "difference", "add", "softlight", "hardlight", "exclusion", "subtract"].map(b => `<option ${(c.blend || "normal") === b ? "selected" : ""}>${b}</option>`).join("")}</select>`) : ""}
    ${tr.kind === "video" ? `<div class="grp"><h4><span class="fx">fx</span>Motion</h4>
      ${f("Position x", `<input type="range" data-k="transform.x" min="${-S.seq.width}" max="${S.seq.width}" step="1" value="${cur("transform.x", tf.x || 0)}">${val(cur("transform.x", tf.x || 0), 0)}`, "transform.x")}${kfrow("transform.x")}
      ${f("Position y", `<input type="range" data-k="transform.y" min="${-S.seq.height}" max="${S.seq.height}" step="1" value="${cur("transform.y", tf.y || 0)}">${val(cur("transform.y", tf.y || 0), 0)}`, "transform.y")}${kfrow("transform.y")}
      ${f("Scale", `<input type="range" data-k="transform.scale" min="0.1" max="3" step="0.01" value="${cur("transform.scale", tf.scale == null ? 1 : tf.scale)}">${val(cur("transform.scale", tf.scale == null ? 1 : tf.scale))}`, "transform.scale")}${kfrow("transform.scale")}
      ${f("Rotation", `<input type="number" data-k="transform.rotation" step="1" value="${tf.rotation || 0}">`)}
      ${f("Anchor point", `<div class="pair"><input type="number" data-k="transform.anchor_x" step="1" value="${tf.anchor_x || 0}" title="x offset from clip center (px)"><input type="number" data-k="transform.anchor_y" step="1" value="${tf.anchor_y || 0}" title="y offset from clip center (px)"></div>`)}
      ${f("Opacity", `<input type="range" data-k="transform.opacity" min="0" max="1" step="0.01" value="${cur("transform.opacity", tf.opacity == null ? 1 : tf.opacity)}">${val(cur("transform.opacity", tf.opacity == null ? 1 : tf.opacity))}`, "transform.opacity")}${kfrow("transform.opacity")}</div>
    <div class="grp"><h4><span class="fx">fx</span>Transitions</h4>
      ${f("In", `<div class="pair"><select data-k="transition_in.type">${["dissolve", "dip_black", "dip_white", "wipe_left", "wipe_right", "wipe_up", "wipe_down", "push_left", "push_right", "slide_left", "slide_right", "slide_up", "slide_down", "iris", "iris_close", "diagonal_tl", "diagonal_tr", "diagonal_bl", "diagonal_br", "barn_h", "barn_v", "clock", "checker", "cross_zoom", "glitch", "fade"].map(t => `<option ${ti.type === t ? "selected" : ""}>${t}</option>`).join("")}</select><input type="number" data-k="transition_in.duration" step="0.1" min="0" value="${ti.duration || 0}"></div>`)}
      ${ti.duration ? f("Alignment", `<select data-k="transition_in.align">${[["start", "Start at Cut"], ["center", "Center at Cut"], ["end", "End at Cut"]].map(([v, n]) => `<option value="${v}" ${(ti.align || "start") === v ? "selected" : ""}>${n}</option>`).join("")}</select>`) : ""}
      ${f("Out", `<div class="pair"><select data-k="transition_out.type">${["dissolve", "dip_black", "dip_white", "fade"].map(t => `<option ${to.type === t ? "selected" : ""}>${t}</option>`).join("")}</select><input type="number" data-k="transition_out.duration" step="0.1" min="0" value="${to.duration || 0}"></div>`)}</div>` : ""}
    ${m && m.has_audio ? `<div class="grp"><h4><span class="fx">fx</span>Volume</h4>
      ${f("Gain (dB)", `<input type="range" data-k="audio.gain_db" min="-40" max="12" step="0.5" value="${cur("audio.gain_db", au.gain_db || 0)}">${val(cur("audio.gain_db", au.gain_db || 0), 1)}`, "audio.gain_db")}${kfrow("audio.gain_db")}
      ${f("Fade in / out", `<div class="pair"><input type="number" data-k="audio.fade_in" step="0.1" min="0" value="${au.fade_in || 0}"><input type="number" data-k="audio.fade_out" step="0.1" min="0" value="${au.fade_out || 0}"></div>`)}
      ${au.fade_window ? `<p class="hint">Fades retain their original timing across cuts and trims. Changing a fade setting anchors both fades to this clip’s edges.</p>` : ""}
      ${f("Balance (L / R)", `<input type="range" data-k="audio.pan" min="-1" max="1" step="0.05" value="${au.pan || 0}">${val(au.pan || 0)}`)}
      ${f("Channels", `<select data-k="audio.channels">${[["stereo", "Stereo"], ["left", "Left → both"], ["right", "Right → both"], ["mono", "Mono sum"], ["swap", "Swap L/R"]].map(([v, n]) => `<option value="${v}" ${(au.channels || "stereo") === v ? "selected" : ""}>${n}</option>`).join("")}</select>`)}
      ${f("Auto-match", `<div class="pair"><select id="amTarget"><option value="-18">Dialogue −18 LUFS</option><option value="-22">Music −22 LUFS</option><option value="-16">Voice-over −16 LUFS</option><option value="-14">Clip −14 LUFS</option></select><button id="amGo">Match</button></div>`)}
      ${tr.kind === "video" ? f("Linked audio", `<label><input type="checkbox" data-k="audio.linked" ${au.linked === false ? "" : "checked"}> use this clip's audio (Ctrl+L to split it onto ${tr.id.replace("V", "A")})</label>`) : ""}</div>` : ""}
    ${c.title ? textEditor("title", c.title, "Title") : ""}
    ${c.graphic ? `<div class="grp"><h4>${c.graphic.name || "Graphic"} layers</h4>${(c.graphic.layers || []).map((L, i) => `<div class="fld" style="grid-template-columns:22px 92px 1fr;margin:2px 0 0"><span></span><span style="color:var(--dim)">layer ${i + 1} · ${L.kind}</span><span class="lbls"><button class="tb" data-gmv="${i}|-1" title="send backward">↑</button><button class="tb" data-gmv="${i}|1" title="bring forward">↓</button><button class="tb danger" data-gdel="${i}" title="delete layer">×</button></span></div>` + (L.kind === "text" ? `${f("Text " + (i + 1), `<input type="text" data-g="${i}.text" value="${(L.text || "").replace(/"/g, "&quot;")}" style="width:100%">`)}${f("Size / color", `<div class="pair"><input type="number" data-g="${i}.size" value="${L.size || 60}"><input type="text" data-g="${i}.color" value="${L.color || "white"}"></div>`)}${f("Offset x / y", `<div class="pair"><input type="number" data-g="${i}.x" value="${L.x || 0}"><input type="number" data-g="${i}.y" value="${L.y || 0}"></div>`)}` : `${f("Box " + (i + 1), `<div class="pair"><input type="text" data-g="${i}.color" value="${L.color || "white"}" title="color (name, 0xRRGGBB, with @alpha)"><input type="number" data-g="${i}.h" step="0.005" min="0" max="1" value="${L.h}" title="height (fraction)"></div>`)}`)).join("")}</div>` : ""}
    <div class="grp"><h4>Annotate for training</h4><div class="lbls">${["keep", "too long", "too short", "wrong take", "bad cut", "great moment", "fix audio", "fix color"].map(l => `<button class="ann" data-l="${l}">${l}</button>`).join("")}</div>${reasonChips("ann")}<input type="text" id="annNote" placeholder="why (optional)" style="width:100%;margin-top:6px"></div>`;
  wireReasons(pane);
  const patchFor = (key, v) => { const p = { id: c.id }; if (key.includes(".")) { const [a, b] = key.split("."); p[a] = { ...(c[a] || {}), [b]: v }; if (a.startsWith("transition")) p[a].type = p[a].type || (c.title ? "fade" : "dissolve"); } else p[key] = v; return p; };
  $$("[data-gmv]", pane).forEach(b => b.onclick = () => { const [i, d] = b.dataset.gmv.split("|").map(Number); const g = deep(c.graphic); const j = i + d; if (j < 0 || j >= g.layers.length) return; [g.layers[i], g.layers[j]] = [g.layers[j], g.layers[i]]; applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, graphic: g } }], "graphic", "arrange layers"); }); $$("[data-gdel]", pane).forEach(b => b.onclick = () => { const g = deep(c.graphic); g.layers.splice(+b.dataset.gdel, 1); applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, graphic: g } }], "graphic", "delete layer"); });
  $$("[data-g]", pane).forEach(inp => CR.bindPropertyControl(inp, { c, tr, commit: () => { const [i, k] = inp.dataset.g.split("."); const g = deep(c.graphic); g.layers[+i][k] = inp.type === "number" ? parseFloat(inp.value) : inp.value; applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, graphic: g } }], "graphic", `graphic layer ${k}`); } }));
  if (!S.textStylesLoaded) { S.textStylesLoaded = true; api.get("/api/settings").then(st => { S.textStyles = (st && st.text_styles) || {}; renderEC(); }).catch(() => { }); }
  const STYLE_KEYS = ["font", "weight", "size", "color", "shadow", "borderw", "border_color", "box", "box_color", "line_spacing", "uppercase", "align", "valign", "glow"];
  $$("[data-savestyle]", pane).forEach(b => b.onclick = async () => { const src = b.dataset.savestyle.startsWith("title") ? c.title : (c.graphic && c.graphic.layers[+b.dataset.savestyle.split(".")[1]]); if (!src) return; const n = prompt("Style name", ""); if (!n) return; const style = {}; for (const k of STYLE_KEYS) if (src[k] !== undefined) style[k] = src[k]; S.textStyles = { ...(S.textStyles || {}), [n]: style }; await api.json("PUT", "/api/settings", { text_styles: S.textStyles }); status(`Text style "${n}" saved`); renderEC(); });
  $$("[data-textstyle]", pane).forEach(sel => sel.onchange = () => { const st = (S.textStyles || {})[sel.value]; if (!st) return; const prefix = sel.dataset.textstyle; if (prefix === "title") applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, title: { ...c.title, ...st } } }], "text_style", `style ${sel.value}`); else { const g = deep(c.graphic); const li = +prefix.split(".")[1]; g.layers[li] = { ...g.layers[li], ...st }; applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, graphic: g } }], "text_style", `style ${sel.value}`); } });
  $$("[data-fontadd]", pane).forEach(b => b.onclick = uploadFontDialog); $$("[data-k]", pane).forEach(inp => {
    const read = () => inp.type === "checkbox" ? inp.checked : inp.classList.contains("tcin") ? parseTC(inp.value, ["in_", "out"].includes(inp.dataset.k) ? CR.mediaRate(m, true) : S.seq.fps) : (inp.type === "number" || inp.type === "range") ? parseFloat(inp.value) : inp.value;
    const commit = () => { const key = inp.dataset.k; let v = read(); if (inp.classList.contains("tcin") && !Number.isFinite(v)) return; if (key === "speed") v = Math.max(0.1, v / 100);
      if (kf[key] && kf[key].length && inp.type === "range") { setKeyframe(c, tr, key, Math.max(0, Math.min(clipDur(c), S.t - c.start)), v); return; }
      applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: patchFor(key, v) }], "effect_controls", `set ${key}`); };
    const preview = () => { if (inp.type !== "range") return; const key = inp.dataset.k, v = parseFloat(inp.value); inp.nextElementSibling && (inp.nextElementSibling.textContent = v.toFixed(key.includes("gain") ? 1 : key.includes("x") || key.includes("y") ? 0 : 2));
      if (kf[key]?.length) c.keyframes = CR.keyframesWithValue(kf, key, rel, v);
      else Object.assign(c, patchFor(key, v)); CR.renderProgram(); };
    CR.bindPropertyControl(inp, { c, tr, fields: [inp.dataset.k.split(".")[0], "keyframes"], preview, commit });
  });
  const relNow = () => Math.max(0, Math.min(clipDur(c), S.t - c.start));
  $$(".kfb", pane).forEach(b => b.onclick = () => { const key = b.dataset.kf; if (key === "speed") { setKeyframe(c, tr, "speed", relNow(), Math.max(0.05, parseFloat($("#trSpeed").value) / 100)); return; } const inp = pane.querySelector(`[data-k="${key}"]`); setKeyframe(c, tr, key, relNow(), parseFloat(inp.value)); });
  const mp = $("#ecMotionPreset"); if (mp) mp.onchange = () => { if (mp.value) CR.applyMotionPreset(mp.value); };
  const di = pane.querySelector("[data-dur]"); if (di) di.onkeydown = e => { if (e.key !== "Enter") return; const d = parseTC(di.value); if (!(d > 0)) return; const sp = c.speed || 1; const maxOut = m && !m.is_image ? m.duration : 1e9; const nout = Math.min(maxOut, c.in_ + d * sp); const ops = [{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, out: nout } }]; if (e.shiftKey) { const delta = (nout - c.in_) / sp - clipDur(c); for (const x of tr.clips) if (x.id !== c.id && x.start >= clipEnd(c) - 1e-6) ops.push({ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: x.id, start: x.start + delta } }); } applyOps(ops, "duration", `duration ${di.value}${e.shiftKey ? " (ripple)" : ""}`); };
  wireAudioMatch(c, tr);
  $$("[data-kfnav]", pane).forEach(b => b.onclick = () => { const [key, d] = b.dataset.kfnav.split("|"); const list = [...kfList(key)].sort((a, b) => a.t - b.t); const relT = Math.max(0, Math.min(clipDur(c), S.t - c.start)); if (d === "0") { const at = list.find(k => Math.abs(k.t - relT) < frame() / 2); if (at) writeKf(key, list.filter(k => k !== at), `remove keyframe ${key}`); else { const inp = pane.querySelector(`[data-k="${key}"]`); setKeyframe(c, tr, key, relT, inp ? parseFloat(inp.value) : (key === "speed" ? CR.speedAt(c, relT) : kfVal(list, relT, 0))); } return; } const nx = d === "1" ? list.find(k => k.t > relT + 1e-4) : [...list].reverse().find(k => k.t < relT - 1e-4); if (nx) CR.seekTo(c.start + nx.t); });
  $$("[data-kfedit]", pane).forEach(button => button.onclick = () => window.FilmocityCurveValues.open(CR, { kind: "keyframes", key: button.dataset.kfedit, c, tr, origin: button }));
  $$("[data-kfclear]", pane).forEach(b => b.onclick = () => writeKf(b.dataset.kfclear, [], `clear keyframes ${b.dataset.kfclear}`));
  const trc = $("#trClear"); if (trc) trc.onclick = () => applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, time_remap: null } }], "time_remap", "clear time remap"); const ho = $("#holdOff"); if (ho) ho.onclick = () => applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, hold: false, speed: 1 } }], "frame_hold", "release frame hold");
  const writeKf = (key, list, reason) => { if (key === "speed") applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, time_remap: list.length ? list : null } }], "time_remap", reason); else { const nk = { ...(c.keyframes || {}), [key]: list }; if (!list.length) delete nk[key]; applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, keyframes: nk } }], "keyframe", reason); } };
  $$("[data-kfi]", pane).forEach(d => { const current = CR.propertyTarget(c, tr, d); d.onmousedown = ev => { if (!current() || !CR.canEdit()) return; ev.stopPropagation(); const [key, i] = d.dataset.kfi.split("|"); const list = [...kfList(key)].sort((a, b) => a.t - b.t); const lane = d.parentElement, r = lane.getBoundingClientRect(); let moved = false; const t0 = list[+i].t;
      const move = e => { moved = true; const t = Math.max(0, Math.min(clipDur(c), (e.clientX - r.left) / r.width * clipDur(c))); d.style.left = (t / clipDur(c) * 100) + "%"; d.dataset.t = t; };
      const up = e => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); const l2 = deep(kfList(key)).sort((a, b) => a.t - b.t);
        if (moved) { l2[+i].t = +(+d.dataset.t).toFixed(4); writeKf(key, l2.sort((a, b) => a.t - b.t), `move keyframe ${key}`); }
        else if (e.altKey) { l2.splice(+i, 1); writeKf(key, l2, `remove keyframe ${key}`); }
        else { const order = key === "speed" ? ["linear", "hold"] : ["linear", "ease", "ease_in", "ease_out", "bezier", "hold"]; l2[+i].e = order[(order.indexOf(l2[+i].e || "linear") + 1) % order.length]; writeKf(key, l2, `easing ${l2[+i].e}`); } };
      CR.watchEditGesture(ev, move, up, undefined, { valid: current }); }; });
  for (const key of Object.keys(kf)) if (kf[key].length > 1 && !key.startsWith("mask.")) CR.extras && CR.extras.curveEditor(pane, c, tr, key);
  CR.ecExtras && CR.ecExtras(pane, c, tr);
  $$(".fld > label", pane).forEach(label => { const input = label.parentElement.querySelector('input[type="number"], input[type="range"]'); if (!input?.filmocityScrub) return; label.style.cursor = "ew-resize"; label.title = "Drag to change the value · Shift ×10 · Escape cancels"; label.onmousedown = input.filmocityScrub; });
  $$(".ann", pane).forEach(b => b.onclick = async () => { await api.json("POST", "/api/annotate", { target: { sequence: S.seq.id, track: tr.id, clip_id: c.id }, label: b.dataset.l, reasons: pickedReasons(pane, "ann"), note: $("#annNote").value, actor: "human" }); status(`Annotated: ${b.dataset.l}`); });
}
function textEditor(prefix, t, label) {
  const fonts = (CR.fonts || []); const opt = (list, cur) => list.map(x => `<option ${x === cur ? "selected" : ""}>${x}</option>`).join("");
  return `<div class="grp"><h4>${label}</h4>
    ${f("Text", `<textarea data-k="${prefix}.text" rows="2" style="width:100%">${(t.text || "").replace(/</g, "&lt;")}</textarea>`)}
    ${f("Font", `<div class="pair"><select data-k="${prefix}.font"><option value="">default (DejaVu Sans)</option>${opt(fonts, t.font)}</select><button class="tb" data-fontadd="1" title="Add your own TTF/OTF fonts">+</button><select data-k="${prefix}.weight">${opt(["bold", "regular"], t.weight || "bold")}</select></div>`)}
    ${f("Size / color", `<div class="pair"><input type="number" data-k="${prefix}.size" value="${t.size || 100}"><input type="text" data-k="${prefix}.color" value="${t.color || "white"}" title="name, #hex or 0xRRGGBB, optional @alpha"></div>`)}
    ${f("Align", `<div class="pair"><select data-k="${prefix}.align">${opt(["left", "center", "right"], t.align || "center")}</select><select data-k="${prefix}.valign">${opt(["top", "center", "bottom"], t.valign || "center")}</select></div>`)}
    ${f("Offset x / y", `<div class="pair"><input type="number" data-k="${prefix}.x" value="${t.x || 0}"><input type="number" data-k="${prefix}.y" value="${t.y || 0}"></div>`)}
    ${f("Line spacing", `<input type="number" data-k="${prefix}.line_spacing" value="${t.line_spacing == null ? Math.round((t.size || 100) * 0.15) : t.line_spacing}">`)}
    ${f("Vertical text", `<label><input type="checkbox" data-k="${prefix}.vertical" ${t.vertical ? "checked" : ""}> stack characters vertically</label>`)}
    ${f("Uppercase", `<label><input type="checkbox" data-k="${prefix}.uppercase" ${t.uppercase ? "checked" : ""}> ALL CAPS</label>`)}
    ${f("Text style", `<div class="pair"><select data-textstyle="${prefix}"><option value="">apply saved style…</option>${Object.keys(S.textStyles || {}).map(n => `<option>${n}</option>`).join("")}</select><button class="tb" data-savestyle="${prefix}" title="Save this text's look (font, size, colour, shadow, border, spacing) as a named style">save</button></div>`)}
    ${f("Box", `<div class="pair"><label><input type="checkbox" data-k="${prefix}.box" ${t.box ? "checked" : ""}> on</label><input type="text" data-k="${prefix}.boxcolor" value="${t.boxcolor || "black@0.6"}"></div>`)}
    ${f("Shadow", `<div class="pair"><label><input type="checkbox" data-k="${prefix}.shadow" ${t.shadow ? "checked" : ""}> on</label><input type="text" data-k="${prefix}.shadowcolor" value="${t.shadowcolor || "black@0.6"}"></div>`)}
    ${f("Outline", `<div class="pair"><input type="number" data-k="${prefix}.borderw" min="0" value="${t.borderw || 0}"><input type="text" data-k="${prefix}.bordercolor" value="${t.bordercolor || "black"}"></div>`)}</div>`;
}
async function loadFonts() { try { const r = await api.get("/api/fonts"); CR.fonts = r.fonts || []; CR.userFonts = r.user || []; let st = $("#userFontFaces"); if (!st) { st = document.createElement("style"); st.id = "userFontFaces"; document.head.appendChild(st); } st.textContent = CR.userFonts.map(u => `@font-face{font-family:"${u.family}";src:url("${u.url}");font-weight:${/bold/i.test(u.style) ? "700" : "400"};font-style:${/italic|oblique/i.test(u.style) ? "italic" : "normal"}}`).join("\n") + `\n@font-face{font-family:"DejaVu Sans";src:url("/assets/fonts/DejaVuSans-Bold.ttf");font-weight:700}@font-face{font-family:"DejaVu Sans";src:url("/assets/fonts/DejaVuSans.ttf");font-weight:400}`; if (document.fonts && document.fonts.load) for (const u of CR.userFonts) document.fonts.load(`700 24px "${u.family}"`).catch(() => { }); } catch (e) { CR.fonts = []; } }
async function uploadFontDialog() { const inp = document.createElement("input"); inp.type = "file"; inp.accept = ".ttf,.otf,.ttc"; inp.multiple = true; inp.onchange = async () => { for (const f of inp.files) { const fd = new FormData(); fd.append("file", f); const r = await fetch("/api/fonts/upload", { method: "POST", body: fd }); if (!r.ok) { status(`Font ${f.name}: ${(await r.json()).detail || "rejected"}`, "err"); continue; } const j = await r.json(); status(`Font added: ${j.family} ${j.style}`); } await loadFonts(); CR.panels.render(); }; inp.click(); }
function setKeyframe(c, tr, key, t, v) { if (key === "speed") { const list = (c.time_remap || []).filter(k => Math.abs(k.t - t) > frame() / 2); if (!list.length && t > frame()) list.push({ t: 0, v: c.speed || 1 }); list.push({ t: +t.toFixed(4), v }); applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, time_remap: list.sort((a, b) => a.t - b.t), speed: 1 } }], "time_remap", `speed ${Math.round(v * 100)}% @ ${fmtTC(t, S.seq.fps)}`); return; }
  const kf = CR.keyframesWithValue(c.keyframes || {}, key, t, v), list = kf[key];
  if (list.length === 1) { const [a, b] = key.split("."); const base = (c[a] || {})[b]; if (base != null && Math.abs(base - v) > 1e-9 && Math.abs(t) > frame()) kf[key].unshift({ t: 0, v: base }); }
  applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, keyframes: kf } }], "keyframe", `keyframe ${key} @ ${fmtTC(t, S.seq.fps)}`); }

// ---------- Color ----------
function renderLookGrid(pane, c, tr) { const grid = $("#lookGrid", pane); if (!grid || !window.CR_GPU || !CR_GPU.available()) return; const src = document.getElementById("prgCanvas"); if (!src || !src.width) return; const base = document.createElement("canvas"); base.width = 108; base.height = 192; base.getContext("2d").drawImage(src, 0, 0, 108, 192); const col = c.color || {};
  grid.innerHTML = [{ name: "None", path: "" }, ...(S.luts || [])].map(l => `<div data-look="${l.path}" title="${l.name}" style="cursor:pointer;border:2px solid ${(col.lut || "") === l.path ? "var(--accent)" : "transparent"};border-radius:4px;overflow:hidden;background:#111"><canvas width="108" height="192" style="width:100%;display:block;aspect-ratio:9/16"></canvas><div style="font-size:10px;padding:2px 4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${l.name}</div></div>`).join("");
  const draw = () => { $$("[data-look]", grid).forEach(el => { const cv = el.querySelector("canvas"); const out = CR_GPU.process(base, { color: { ...col, lut: el.dataset.look || undefined } }, 108, 192); const g = cv.getContext("2d"); g.clearRect(0, 0, 108, 192); g.drawImage(out || base, 0, 0, 108, 192); }); }; draw(); setTimeout(draw, 700); setTimeout(draw, 1800);  // redraw as LUT files finish loading
  $$("[data-look]", grid).forEach(el => el.onclick = () => applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, color: { ...(c.color || {}), lut: el.dataset.look || null } } }], "look", `look ${el.title}`)); }
function renderColor() {
  if (S.gesture) { S.gesture.panelsNeeded = true; return; }
  const pane = $("#pane-color"), sel = CR.selectedClips().filter(x => x.tr.kind === "video" && (x.c.media_id || x.c.adjustment || x.c.sequence_id)); if (sel.length !== 1) { pane.innerHTML = `<div class="empty">Select one video clip (or an adjustment layer) to grade.</div>`; return; }
  const { c, tr } = sel[0], col = c.color || {}; const sl = (k, label, min = -1, max = 1) => f(label, `<input type="range" data-c="${k}" min="${min}" max="${max}" step="0.01" value="${col[k] || 0}">${val(col[k] || 0)}`);
  pane.innerHTML = `<div class="grp"><h4>Basic correction<span class="sp"></span><button id="colAuto" title="Set blacks, whites, exposure, temperature and tint from this frame">Auto</button><button id="colMatch" title="First click captures the reference frame; park on the shot to match and click again">Match</button><button id="colReset">Reset</button></h4>${sl("exposure", "Exposure")}${sl("contrast", "Contrast")}${sl("highlights", "Highlights")}${sl("shadows", "Shadows")}${sl("whites", "Whites")}${sl("blacks", "Blacks")}</div>
    <div class="grp"><h4>Color</h4>${sl("temperature", "Temperature")}${sl("tint", "Tint")}${sl("saturation", "Saturation")}${sl("vibrance", "Vibrance")}</div>
    <div class="grp"><h4>Color Wheels</h4><div class="wheels">${["shadows", "midtones", "highlights"].map(k => `<div class="wheel"><canvas data-wheel="${k}" width="120" height="120" role="img" aria-label="${k} color wheel; use Values to edit with the keyboard"></canvas><span style="color:var(--dim)">${k}</span><button class="curve-open" data-curve-edit="wheel:${k}" data-wheel-values="${k}" aria-label="Edit ${k} color balance values">Values…</button></div>`).join("")}</div></div>
    <div class="grp"><h4>Creative</h4>${f("Look (LUT)", `<select id="lutSel"><option value="">none</option>${(S.luts || []).map(l => `<option value="${l.path}" ${col.lut === l.path ? "selected" : ""}>${l.name}</option>`).join("")}<option value="__custom" ${col.lut && !(S.luts || []).some(l => l.path === col.lut) ? "selected" : ""}>custom path…</option></select>`)}${f("LUT (.cube path)", `<input type="text" data-c="lut" value="${col.lut || ""}" placeholder="/path/to/look.cube" style="width:100%">`)}
    <div id="lookGrid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(84px,1fr));gap:6px;margin-top:8px"></div>
    <p style="color:var(--dim);margin:6px 0 0">Looks preview on the current frame (GPU); the render applies the grade exactly (eq, colorbalance, curves, lut3d).</p></div>`;
  const commit = (k, v) => applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, color: { ...(c.color || {}), [k]: v } } }], "color", `color ${k}`);
  const ca = $("#colAuto"); if (ca) ca.onclick = CR.autoColor; const cm = $("#colMatch"); if (cm) cm.onclick = CR.colorMatch;
  $$("[data-c]", pane).forEach(inp => CR.bindPropertyControl(inp, { c, tr, fields: ["color"], commit: () => commit(inp.dataset.c, inp.type === "range" ? parseFloat(inp.value) : inp.value), preview: () => { if (inp.type !== "range") return; inp.nextElementSibling.textContent = parseFloat(inp.value).toFixed(2); c.color = { ...(c.color || {}), [inp.dataset.c]: parseFloat(inp.value) }; CR.renderProgram(); } }));
  $("#colReset").onclick = () => applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, color: {} } }], "color", "reset color"); if (c.media_id && CR.curvesEditor) CR.curvesEditor(pane, c, tr);
  renderLookGrid(pane, c, tr);
  const ls = $("#lutSel"); if (ls) ls.onchange = () => { if (ls.value === "__custom") { pane.querySelector('[data-c="lut"]').focus(); return; } commit("lut", ls.value); }; if (!S.luts) api.get("/api/luts").then(l => { S.luts = l; renderColor(); });
  $$("[data-wheel-values]", pane).forEach(button => button.onclick = () => window.FilmocityCurveValues.open(CR, { kind: "wheel", key: button.dataset.wheelValues, c, tr, origin: button }));
  $$("[data-wheel]", pane).forEach(cv => { const k = cv.dataset.wheel; const w = ((c.color || {}).wheels || {})[k] || { r: 0, g: 0, b: 0 }; const ctx = cv.getContext("2d"); const draw = (wv) => { const g = ctx.createConicGradient(0, 60, 60); for (let i = 0; i <= 12; i++) g.addColorStop(i / 12, `hsl(${i * 30},70%,50%)`); ctx.clearRect(0, 0, 120, 120); ctx.fillStyle = g; ctx.beginPath(); ctx.arc(60, 60, 58, 0, Math.PI * 2); ctx.fill(); const rg = ctx.createRadialGradient(60, 60, 0, 60, 60, 58); rg.addColorStop(0, "rgba(40,40,40,1)"); rg.addColorStop(1, "rgba(40,40,40,0)"); ctx.fillStyle = rg; ctx.fill(); const x = 60 + (wv.r - wv.b) * 45, y = 60 + (wv.b + wv.r - 2 * wv.g) * 26; ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(x, y, 5, 0, Math.PI * 2); ctx.fill(); ctx.strokeStyle = "#000"; ctx.stroke(); }; draw(w);
    const current = CR.propertyTarget(c, tr, cv); cv.onmousedown = ev => { if (!current() || !CR.canEdit()) return; const r = cv.getBoundingClientRect(); const set = e => { const dx = ((e.clientX - r.left) / r.width * 120 - 60) / 58, dy = ((e.clientY - r.top) / r.height * 120 - 60) / 58; const ang = Math.atan2(dy, dx), rad = Math.min(1, Math.hypot(dx, dy)) * 0.6; const rr = Math.cos(ang) * rad, gg = Math.cos(ang - 2 * Math.PI / 3) * rad, bb = Math.cos(ang + 2 * Math.PI / 3) * rad; return { r: +rr.toFixed(3), g: +gg.toFixed(3), b: +bb.toFixed(3) }; }; let cur = set(ev); draw(cur); const move = e => { cur = set(e); draw(cur); }; const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, color: { ...(c.color || {}), wheels: { ...((c.color || {}).wheels || {}), [k]: cur } } } }], "color", `wheel ${k}`); }; CR.watchEditGesture(ev, move, up, () => draw(w), { valid: current }); }; cv.ondblclick = () => applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, color: { ...(c.color || {}), wheels: { ...((c.color || {}).wheels || {}), [k]: { r: 0, g: 0, b: 0 } } } } }], "color", `reset wheel ${k}`); });
}

// ---------- Captions ----------
function renderCaps() {
  const pane = $("#pane-caps"), caps = [...(S.seq.captions || [])].sort((a, b) => a.start - b.start), cs = S.seq.caption_style || {}, si = S.proj.sequences.indexOf(S.seq);

  const scope = JSON.stringify([S.context?.workspace, S.context?.project, S.seq.id]);
  if (S.captionBrowse?.scope !== scope) S.captionBrowse = { scope, query: '', page: 0 };
  const browse = S.captionBrowse, page = window.FilmocityCaptionList.page(caps, browse);
  pane.innerHTML = `<div class="grp"><h4>Captions<span class="sp"></span><button id="capAdd">+ at playhead</button><button id="capImport">Import SRT…</button><button id="capAuto" title="Prepare a transcript in Tasks, then generate captions in Workflow">Transcribe…</button><a href="/api/captions/export?sequence=${encodeURIComponent(S.seq.id)}&amp;context=${encodeURIComponent(JSON.stringify(S.context))}" target="_blank"><button>Export SRT</button></a></h4>
    <label>Find captions <input id="capFind" type="search" value="${escapeExportText(browse.query)}"></label>
    <div class="caption-pages"><button id="capPrev" ${browse.page===0?'disabled':''}>Previous captions</button><span role="status">${page.total} matches · page ${browse.page+1} of ${page.pages}</span><button id="capNext" ${browse.page+1>=page.pages?'disabled':''}>Next captions</button></div>
    <div id="capList">${page.items.length ? page.items.map(({caption:cp,index:i}) => `<div class="caprow" data-i="${i}"><input class="tcin" data-f="start" value="${fmtTC(cp.start, S.seq.fps)}"><input class="tcin" data-f="end" value="${fmtTC(cp.end, S.seq.fps)}"><input class="txt" data-f="text" value="${escapeExportText(cp.text || "").replace(/\n/g, " ")}"><button class="danger" data-del="${i}" title="delete">×</button></div>`).join("") : `<div class="empty">No matching captions. Change the search, import SRT or add one at the playhead.</div>`}</div></div>
    <div class="grp"><h4>Style</h4>${f("Preset", `<select id="capPreset"><option value="">choose…</option>${Object.keys(S.capPresets || {}).map(n => `<option>${n}</option>`).join("")}</select>`)}${f("Size (px)", `<input type="number" data-s="size" value="${cs.size || Math.round(S.seq.height * 0.032)}">`)}${f("Vertical (0–1)", `<input type="number" data-s="y" step="0.01" min="0" max="1" value="${cs.y == null ? 0.74 : cs.y}">`)}${f("Box", `<label><input type="checkbox" data-s="box" ${cs.box ? "checked" : ""}> dark box behind text</label>`)}${f("Outline", `<input type="number" data-s="borderw" min="0" value="${cs.borderw == null ? 3 : cs.borderw}">`)}
    ${f("Word animation", `<select data-s="animate">${[["", "None"], ["highlight", "Highlight the spoken word"], ["pop", "Pop the spoken word"]].map(([v, n]) => `<option value="${v}" ${(cs.animate || "") === v ? "selected" : ""}>${n}</option>`).join("")}</select>`)}${f("Highlight colour", `<input type="color" data-s="highlight_color" value="${cs.highlight_color || "#F6C14A"}">`)}
    <p style="color:var(--dim);margin:6px 0 0">Captions are burned in on export. Keep ≤ 2 lines of 32–42 characters, ≥ 1 s each, inside the safe zone. Word animation follows the transcript's word timings when one exists, otherwise spreads evenly.</p></div>
    <textarea id="srtPaste" rows="4" placeholder="Paste SRT here, then Import" style="width:100%;display:none"></textarea>
    <div class="grp"><h4>Transcript</h4><p>${(S.seq.transcript||[]).length} words. Use Story for word selection, corrections and new cuts.</p><button id="trOpen">Open transcript editor</button><button id="capGenerate">Generate captions in Workflow</button></div>
    <div class="grp"><h4>Markers<span class="sp"></span><a href="/api/markers/export?sequence=${S.seq.id}&fmt=chapters" target="_blank"><button title="YouTube chapters text">Export chapters</button></a><a href="/api/markers/export?sequence=${S.seq.id}&fmt=csv" target="_blank"><button>CSV</button></a><button id="mkAdd">+ at playhead</button></h4>${(S.seq.markers || []).length ? [...S.seq.markers].sort((a, b) => a.time - b.time).map((m, i) => `<div class="caprow" data-mi="${i}"><input class="tcin" data-mf="time" value="${fmtTC(m.time, S.seq.fps)}"><input class="tcin" data-mf="duration" value="${fmtTC(m.duration || 0, S.seq.fps)}" title="duration (span marker)"><input class="txt" data-mf="name" value="${(m.name || "").replace(/"/g, "&quot;")}" placeholder="name / note"><select data-mf="type" title="marker type" style="width:auto">${["comment", "chapter", "segmentation", "web_link", "flash_cue"].map(ty => `<option ${(m.type || "comment") === ty ? "selected" : ""}>${ty}</option>`).join("")}</select><select data-mf="color" style="width:auto">${["green", "red", "orange", "yellow", "blue", "cyan", "magenta"].map(cl => `<option ${(m.color || "green") === cl ? "selected" : ""}>${cl}</option>`).join("")}</select><button class="danger" data-mdel="${i}">×</button></div>`).join("") : `<div class="empty">No markers. Press M at the playhead.</div>`}</div>`;
  const marks = [...(S.seq.markers || [])].sort((a, b) => a.time - b.time); const saveM = (list, reason) => applyOps([{ op: "set", path: `/sequences/${si}/markers`, value: list }], "marker", reason);
  $("#trOpen").onclick = () => { CR.showTab('workflow'); window.FilmocityWorkflow.go(1); };
  $("#capGenerate").onclick = () => { CR.showTab('workflow'); window.FilmocityWorkflow.go(3); };
  $("#capPrev").onclick = () => { browse.page--; renderCaps(); $("#capPrev").focus(); };
  $("#capNext").onclick = () => { browse.page++; renderCaps(); $("#capNext").focus(); };
  $("#capFind").oninput = event => { const input = event.target, cursor = input.selectionStart; browse.query = input.value; browse.page = 0; renderCaps(); const next = $("#capFind"); next.focus(); try { next.setSelectionRange(cursor,cursor); } catch {} };
  $("#mkAdd").onclick = CR.addMarker; $$("[data-mi]", pane).forEach(row => { const i = +row.dataset.mi; $$("[data-mf]", row).forEach(inp => inp.onchange = () => { const list = deep(marks); list[i][inp.dataset.mf] = (inp.dataset.mf === "time" || inp.dataset.mf === "duration") ? parseTC(inp.value) : inp.value; if (typeof list[i][inp.dataset.mf] === "number" && !Number.isFinite(list[i][inp.dataset.mf])) return; saveM(list, "edit marker"); }); row.onclick = e => { if (!e.target.matches("input,select,button")) CR.seekTo(marks[i].time); }; });
  $$("[data-mdel]", pane).forEach(b => b.onclick = () => { const list = deep(marks); list.splice(+b.dataset.mdel, 1); saveM(list, "delete marker"); });
  const save = (list, reason) => applyOps([{ op: "set", path: `/sequences/${si}/captions`, value: list }], "captions", reason);
  $("#capAdd").onclick = () => save([...caps, { id: uid(), start: S.t, end: S.t + 2, text: "Caption" }], "add caption");
  $("#capAuto").onclick = async () => { try { const result = await CR.startTranscription(); status(result.message); } catch(error) { status(error.message, 'err'); } };
  $("#capImport").onclick = async () => { const ta = $("#srtPaste"); if (ta.style.display === "none") { ta.style.display = "block"; ta.focus(); $("#capImport").textContent = "Import pasted SRT"; return; } await api.json("POST", "/api/captions/import", { srt: ta.value, sequence: S.seq.id, actor: "human" }); await CR.loadProject(true); };
  $$(".caprow[data-i]", pane).forEach(row => { const i = +row.dataset.i; $$("[data-f]", row).forEach(inp => inp.onchange = () => { const list = deep(caps); const v = inp.dataset.f === "text" ? inp.value : parseTC(inp.value); if (inp.dataset.f !== "text" && !Number.isFinite(v)) return; list[i][inp.dataset.f] = v; if (!(list[i].end > list[i].start)) { status("Caption end must be after its start.", "err"); return; } save(list, `edit caption ${inp.dataset.f}`); }); row.onclick = e => { if (!e.target.matches("input,button")) CR.seekTo(caps[i].start); }; });
  $$("[data-del]", pane).forEach(b => b.onclick = () => { const list = deep(caps); list.splice(+b.dataset.del, 1); save(list, "delete caption"); });
  $$("[data-s]", pane).forEach(inp => inp.onchange = () => applyOps([{ op: "set", path: `/sequences/${si}/caption_style`, value: { ...cs, [inp.dataset.s]: inp.type === "checkbox" ? inp.checked : (inp.tagName === "SELECT" || inp.type === "color") ? inp.value : parseFloat(inp.value) } }], "captions", "caption style"));
  const cp = $("#capPreset"); if (cp) cp.onchange = () => { const p = CR.applyBrand((S.capPresets || {})[cp.value]); if (p) applyOps([{ op: "set", path: `/sequences/${si}/caption_style`, value: { ...cs, ...p } }], "captions", `caption preset ${cp.value}`); }; if (!S.capPresets) api.get("/api/presets/caption_styles").then(p => { S.capPresets = p; renderCaps(); });
}

// ---------- Proposals ----------
function proposalClip(o, id) { const seq = S.proj.sequences.find(s => s.id === o.sequence), tr = seq?.tracks.find(t => t.id === o.track), c = tr?.clips.find(c => c.id === id); return c ? { c, tr } : null; }
function describeOps(ops) { return ops.map(o => { if (o.op === "set_clip") { const k = Object.keys(o.clip).filter(x => x !== "id"); const r = proposalClip(o, o.clip.id); const name = r ? (r.c.title ? "title" : (S.proj.media[r.c.media_id] || {}).name || o.clip.id) : (o.clip.title ? `new title "${o.clip.title.text}"` : "new clip");
  return `${r ? "change" : "add"} ${name} in ${o.sequence} / ${o.track}: ` + k.map(x => `${x}=${typeof o.clip[x] === "object" ? JSON.stringify(o.clip[x]) : o.clip[x]}${r && r.c[x] != null && typeof r.c[x] !== "object" ? ` (was ${r.c[x]})` : ""}`).join(", "); }
  if (o.op === "remove_clip") { const r = proposalClip(o, o.clip_id); return `remove ${r ? ((S.proj.media[r.c.media_id] || {}).name || (r.c.title && "title") || o.clip_id) : o.clip_id} from ${o.sequence} / ${o.track}`; } let before; try { before = o.path.split("/").filter(Boolean).reduce((value, key) => value[key], S.proj); } catch (_) { before = undefined; }
  const shown = value => value === undefined ? "not set" : JSON.stringify(value);
  return `${o.op} ${o.path}${o.op === "remove" ? " (was " + shown(before) + ")" : ": " + shown(o.value) + " (was " + shown(before) + ")"}`; }).join("\n"); }
function renderProps() {
  const pane = $("#pane-props"), props = [...(S.proj.proposals || [])].reverse(); const pending = props.reduce((n, p) => n + p.items.filter(i => i.status === "pending").length, 0);
  const badge = $("#propBadge"); badge.style.display = pending ? "" : "none"; badge.textContent = pending;
  if (!props.length) { pane.innerHTML = `<div class="empty">No suggested edits yet.<br>Agent proposals appear here for review. Preview the result, then accept or reject individual items. You can undo a decision.</div>`; return; }
  pane.innerHTML = props.map(p => `<div class="grp"><h4>${escapeExportText(p.title)}<span class="sp"></span><span style="color:var(--dim);font-weight:400">${new Date(p.ts * 1000).toLocaleTimeString()}</span>${p.items.some(i => i.status === "pending") ? `<button data-prev="${escapeExportText(p.id)}" title="Review rendered frames and normalized changes before accepting">Preview all</button><button data-all="accept|${escapeExportText(p.id)}">Accept all</button><button class="danger" data-all="reject|${escapeExportText(p.id)}">Reject all</button>` : ""}</h4>
    ${p.items.map(it => `<div class="prop ${it.status !== "pending" ? escapeExportText(it.status) : ""}"><div class="reason">${escapeExportText(it.reason || "(no reason given)")}</div><div class="ops">${escapeExportText(describeOps(it.ops))}</div>
      ${it.status === "pending" ? reasonChips(it.id) : ""}<div class="acts">${it.status === "pending" ? `<input type="text" aria-label="Review note: ${escapeExportText(it.reason || "Proposed edit")}" placeholder="note (optional)" data-note="${escapeExportText(it.id)}" style="flex:1"><button data-one-prev="${escapeExportText(p.id)}|${escapeExportText(it.id)}">Preview</button><button class="primary" data-dec="accept|${escapeExportText(p.id)}|${escapeExportText(it.id)}">Accept</button><button class="danger" data-dec="reject|${escapeExportText(p.id)}|${escapeExportText(it.id)}">Reject</button>` : `<span style="color:var(--dim)">${escapeExportText(it.status)}${it.reasons && it.reasons.length ? " · " + escapeExportText(it.reasons.join(", ")) : ""}${it.note ? " — " + escapeExportText(it.note) : ""}</span>`}</div></div>`).join("")}</div>`).join("");
  wireReasons(pane); (async () => { for (const p of props) for (const it of p.items) { if (it.status !== "pending") continue; try { const r = await api.json("POST", "/api/advisor/score", { sequence: S.seq.id, ops: it.ops, reason: it.reason }); const sc = r.scores[0]; const el = pane.querySelector(`[data-note="${CSS.escape(it.id)}"]`); if (el && sc && (sc.p_accept != null || sc.history)) { const tag = document.createElement("span"); tag.style.cssText = "font-family:var(--mono);font-size:10.5px;color:var(--dim);margin-right:6px"; tag.title = "advisor: predicted acceptance from your past decisions"; tag.textContent = (sc.p_accept != null ? `P(accept) ${Math.round(sc.p_accept * 100)}% · ` : "") + (sc.history ? `history ${sc.history.accepted}✓ ${sc.history.rejected}✗` : "") + ` (${sc.confidence})`; el.parentElement.insertBefore(tag, el); } } catch (e) { } } })();
  const decisionItem = iid => ({ id: iid, note: (pane.querySelector(`[data-note="${CSS.escape(iid)}"]`) || {}).value || "", reasons: pickedReasons(pane, iid) });
  const decide = async (decision, pid, items) => {
    if (S.commandPending) return;
    const buttons = $$("[data-dec], [data-all], [data-prev], [data-one-prev]", pane); buttons.forEach(b => b.disabled = true);
    try { await CR.proposalDecision(pid, items, decision); }
    finally { buttons.forEach(b => b.disabled = false); }
  };
  $$("[data-dec]", pane).forEach(b => b.onclick = () => { const [d, p, i] = b.dataset.dec.split("|"); return decide(d, p, [decisionItem(i)]); });
  const preview = (pid, ids) => {
    if (S.commandPending) return;
    const proposal = (S.proj.proposals || []).find(p => p.id === pid);
    return window.FilmocityProposalReview.open(CR, pid, ids.map(decisionItem), proposal?.title || "Proposed edits");
  };
  $$("[data-prev]", pane).forEach(b => b.onclick = () => {
    const p = (S.proj.proposals || []).find(x => x.id === b.dataset.prev);
    return preview(p.id, p.items.filter(it => it.status === "pending").map(it => it.id));
  });
  $$("[data-one-prev]", pane).forEach(b => b.onclick = () => { const [pid, iid] = b.dataset.onePrev.split("|"); return preview(pid, [iid]); });
  $$("[data-all]", pane).forEach(b => b.onclick = () => { const [d, pid] = b.dataset.all.split("|"); const p = (S.proj.proposals || []).find(x => x.id === pid); return decide(d, pid, p.items.filter(it => it.status === "pending").map(it => decisionItem(it.id))); });
}

// ---------- Audio mixer + meter ----------
let meterData;
function renderAudio() {
  if (S.gesture) { S.gesture.panelsNeeded = true; return; }
  const pane = $("#pane-audio"), doc = pane.ownerDocument || document;
  pane.replaceChildren();
  window.FilmocityMixerControls.mountMixer(CR, pane);
  const add = (parent, tag, text) => { const node = doc.createElement(tag); if (text !== undefined) node.textContent = text; parent.appendChild(node); return node; };
  const output = add(pane, "section"); output.className = "grp";
  add(output, "h4", "Preview output (stereo RMS estimate)");
  const display = add(output, "div"); display.id = "meter"; display.className = "meter"; display.setAttribute("aria-hidden", "true");
  const fill = add(display, "i"); fill.style.height = "0%";
  const level = add(output, "span", "−∞"); level.id = "meterDb"; level.className = "db";
  add(output, "p", "This meter is not a loudness, true-peak or clipping measurement. Check the rendered mix before delivery.");
  window.FilmocityMixerControls.mount(CR, pane);
  const duck = add(pane, "section"); duck.className = "grp"; add(duck, "h4", "Auto-duck");
  const button = add(duck, "button", "Review music ducking"); button.id = "duckGo"; button.type = "button";
  button.addEventListener("click", () => window.FilmocityDucking.open());
  add(duck, "p", "Choose music and dialogue tracks, then review separate ducking automation. Manual volume edits are preserved. Apply or remove with one Undo step.");
}
function meter() {
  const el = $("#meter"), channels = CR.AG?.an; if (!el || !S.playing || !channels) return;
  const samples = meterData || (meterData = new Float32Array(512)); let sum = 0, count = 0;
  for (const analyser of channels) { analyser.getFloatTimeDomainData(samples); for (const value of samples) { sum += value * value; count++; } }
  const db = 20 * Math.log10(Math.sqrt(sum / Math.max(1, count)) || 1e-6);
  el.firstChild.style.height = Math.max(0, Math.min(100, (db + 60) / 60 * 100)) + "%"; $("#meterDb").textContent = db < -59 ? "−∞" : db.toFixed(1) + " dB";
}

// ---------- dialogs / menu actions ----------
function openDlg(id) {
  const d = $(id); d._returnFocus = document.activeElement; d.classList.add("open");
  d.setAttribute("role", "dialog"); d.setAttribute("aria-modal", "true");
  const title = d.querySelector("h3"); if (title) { title.id ||= d.id + "Title"; d.setAttribute("aria-labelledby", title.id); }
  d.onkeydown = e => {
    e.stopPropagation();
    if (id === "#dlgExport" && e.altKey && e.key.toLowerCase() === "l") { e.preventDefault(); d.querySelector("[data-locate-original]")?.click(); }
    else if (e.key === "Escape") { e.preventDefault(); const c = d.querySelector(".actions button:not(.primary)"); if (c) c.click(); else closeDlg(id); }
    else if (e.key === "Tab") {
      const controls = [...d.querySelectorAll('input,select,textarea,button,a[href],[tabindex="0"]')].filter(el => !el.disabled && el.getClientRects().length);
      const first = controls[0], last = controls[controls.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
    } else if (e.key === "Enter" && !e.target.matches("textarea,select,button,a")) { const ok = d.querySelector(".actions button.primary:not(:disabled)"); if (ok) { e.preventDefault(); ok.click(); } }
  };
  setTimeout(() => { if (!d.classList.contains("open")) return; const f = d.querySelector("input,select,textarea,button"); if (f) f.focus(); }, 30);
  if (id === "#dlgExport") refreshExportPreflight();
}
function closeDlg(id) { const d = $(id); d.classList.remove("open"); if (d._returnFocus?.isConnected) d._returnFocus.focus(); }
let preflightRequest = 0, exportPending = false;
let exportEncoderDetails = null, exportEncoderRequest = null, exportEncoderLoad = 0;
const escapeExportText = text => String(text ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
function renderResultLinks(job, buttons = false) {
  const link = (url, label, review = false) => {
    // Job outputs are local render paths. Escaping an attribute alone cannot make a script URL safe.
    if (typeof url !== "string" || (review ? !/^\/review\/job-[a-f0-9]{8,32}$/.test(url) : !url.startsWith("/renders/")) || /[\\\u0000-\u001f\u007f]/.test(url)) return "";
    let href; try { href = escapeExportText(encodeURI(url)); } catch (e) { return ""; }
    const text = escapeExportText(label);
    return `<a href="${href}" target="_blank" rel="noopener">${buttons ? `<button>${text}</button>` : text}</a>`;
  };
  if (job.output_kind === "png_sequence") return ["PNG sequence", link(job.out, "Sequence manifest"), link(job.frames?.first_frame, "First frame")].filter(Boolean).join(" · ");
  const output = link(job.out, buttons ? "Open" : job.out);
  return [output, output && /\.(mp4|mov|webm)$/i.test(job.out) ? link(job.review_url, "Review", true) : ""].filter(Boolean).join(" · ");
}
function renderResultQA(job) {
  const history = job.history?.message ? `<span${job.history.status === "error" || job.history.status === "unreadable" ? ' class="err"' : ''}>${escapeExportText(job.history.message)}</span>` : "";
  const q = job.qa; if (!q) return history;
  const sequence = job.output_kind === "png_sequence", parts = [];
  const number = n => typeof n === "number" && Number.isFinite(n);
  if (sequence && Number.isInteger(q.frame_count) && q.frame_count >= 0) parts.push(`${q.frame_count} ${q.frame_count === 1 ? "frame" : "frames"}`);
  if (number(q.width) && q.width > 0 && number(q.height) && q.height > 0) parts.push(`${q.width}×${q.height}`);
  if (number(q.duration) && q.duration >= 0) parts.push(`${q.duration}s`);
  if (number(q.size_mb) && q.size_mb >= 0) parts.push(`${q.size_mb} MB${sequence ? " total" : ""}`);
  if (number(q.integrated_lufs)) parts.push(`${q.integrated_lufs} LUFS`);
  if (number(q.true_peak_dbtp)) parts.push(`${q.true_peak_dbtp} dBTP`);
  if (q.delivery_color?.status === "matched") parts.push("Rec.709 limited-range tags checked");
  if (q.delivery_color?.status === "mismatch") parts.push('<span class="err">Delivery color tags need review</span>');
  if (q.status) parts.push(`QA ${escapeExportText(q.status)}`);
  if (Array.isArray(q.flags) && q.flags.length) parts.push(`<span class="err">⚠ ${q.flags.map(escapeExportText).join("; ")}</span>`);
  if (q.error) parts.push(`<span class="err">${escapeExportText(q.error)}</span>`);
  return [parts.join(" · "), history].filter(Boolean).join(" · ");
}
function exportContextStamp() { return JSON.stringify([S.context?.workspace, S.context?.project, S.context?.revision, S.seq?.id]); }
async function refreshExportPreflight(options = {}) {
  const stamp = exportContextStamp();
  const request = ++preflightRequest, area = $("#exPreflight"), start = $("#exStart");
  const dialog = $("#dlgExport"), fallback = dialog.querySelector(".actions button:not(.primary)");
  const restoreFocus = document.activeElement === start;
  if (restoreFocus) fallback?.focus();
  start.disabled = true; area.textContent = "Checking sources and the selected encoder on this device…";
  try {
    const report = await api.json("POST", "/api/render/preflight", { sequence: S.seq.id, preset: options.preset || exportSettings(), all_sequences: options.all_sequences ?? $("#exAll").checked, _context: S.context });
    if (request !== preflightRequest) return null;
    const view = CR.previewView?.();
    if (stamp !== exportContextStamp() || (view && (view.pending || view.error || view.gesture || view.switching))) { area.textContent = "Project changed or saving is unresolved during export checks. Run Check again."; return null; }
    area.replaceChildren(); const summary = document.createElement("div");
    summary.textContent = report.ok ? (report.warnings ? `Sources available · ${report.warnings} warning(s) to review` : "Export checks passed for the selected settings.") : `Export needs attention · ${report.errors} problem(s)`;
    area.append(summary);
    const colorDescriptions = new Set((report.reports || []).map(r => r.delivery_color?.description).filter(Boolean));
    for (const description of colorDescriptions) { const row = document.createElement("div"); row.textContent = description; area.append(row); }
    for (const issue of report.issues) {
      const row = document.createElement("div"); row.style.marginTop = "6px"; row.className = issue.severity === "error" ? "err" : "";
      const text = document.createElement("span"); text.textContent = `${issue.message}${issue.name ? " · " + issue.name : ""}${issue.track ? " · " + issue.sequence + "/" + issue.track : ""}`; row.append(text);
      if (issue.media_id && S.proj.media[issue.media_id] && !S.proj.media[issue.media_id]?.sequence_frames) {
        const button = document.createElement("button"); button.textContent = "Locate original"; button.dataset.locateOriginal = "true"; button.title = "Alt+L: locate the first missing original"; button.setAttribute("aria-keyshortcuts", "Alt+L"); button.setAttribute("aria-label", "Locate original " + (issue.name || issue.media_id));
        const ownerProject = S.proj, ownerSequence = S.seq; button.onclick = () => { const owner = CR.beginSourceRelink(issue.media_id, { current: () => button.isConnected && ownerProject === S.proj && ownerSequence === S.seq && stamp === exportContextStamp() && dialog.classList.contains("open") }); if (owner) closeDlg("#dlgExport"); }; row.append(" ", button);
      }
      area.append(row);
    }
    start.disabled = !report.ok || exportPending;
    if (restoreFocus && !start.disabled && dialog.classList.contains("open") && document.activeElement === fallback) start.focus();
    return report;
  } catch (error) {
    if (request === preflightRequest) area.textContent = "Export check failed: " + error.message;
    return null;
  }
}
function exportSettings() { const q = $("#exQuality").value, preset = q.endsWith("M") ? { bitrate: q } : { crf: +q }; if ($("#exLoud").checked) preset.loudnorm = true; if ($("#exRange").checked) preset.range = true; preset.color_processing = $("#exColor").value || "rgb"; const fmt = $("#exFormat").value; if (["h264", "h264mov", "hevc"].includes(fmt)) { const encoder = $("#exEnc"); if (!encoder.value || encoder.selectedOptions?.[0]?.disabled) throw new Error("Choose an available encoder for this format."); preset.vcodec = encoder.value; } if (fmt.startsWith("audio:")) { preset.format = "audio"; preset.acodec = fmt.split(":")[1]; } else if (fmt === "h264mov") { preset.format = "h264"; preset.container = "mov"; } else preset.format = fmt; if ($("#exChapters").checked) preset.chapters = true; preset.incremental = $("#exIncremental").checked; if ($("#exPlatform").value) preset.platform = $("#exPlatform").value; if ($("#exWmPos").value) { const mid = [...S.binSel][0]; const m = mid && S.proj.media[mid]; if (m && m.is_image) preset.watermark = { path: m.path, position: $("#exWmPos").value, opacity: parseFloat($("#exWmOp").value) || 0.6 }; else throw new Error("Select the intended logo still in the Project panel before exporting with a watermark."); } return preset; }
function applyExportSettings(p) { if (!p) return; $("#exColor").value = p.color_processing || "rgb"; $("#exQuality").value = p.bitrate || String(p.crf || 18); $("#exLoud").checked = !!p.loudnorm; $("#exRange").checked = !!p.range;  $("#exPlatform").value = p.platform || ""; $("#exFormat").value = (p.format === "h264" && p.container === "mov") ? "h264mov" : p.format === "audio" ? "audio:" + (p.acodec || "wav") : (p.format || "h264"); exportEncoderRequest = p.vcodec || null; if (exportEncoderDetails) updateExportEncoders(exportEncoderRequest, !!exportEncoderRequest); else if (p.vcodec) $("#exEnc").value = p.vcodec; if (p.outputs) $$("#exOutputs input").forEach(i => i.checked = p.outputs.includes(i.dataset.o)); }
async function loadExportPresets() { try { const st = await api.get("/api/settings"); const bundled = await api.get("/api/presets/export_presets"); S.exportPresets = { ...bundled, ...(st.export_presets || {}) }; const sel = $("#exPreset"); sel.innerHTML = `<option value="">(custom)</option>` + Object.keys(S.exportPresets).map(n => `<option>${n}</option>`).join(""); } catch (e) { S.exportPresets = {}; } }
async function doExport() { if (exportPending) return; exportPending = true; let preset;
  const origin = JSON.stringify([S.context?.workspace, S.context?.project, S.seq?.id]);
  const start = $("#exStart"), dialog = $("#dlgExport"), fallback = dialog.querySelector(".actions button:not(.primary)");
  const restoreFocus = document.activeElement === start;
  const finishPending = async () => {
    exportPending = false; await refreshExportPreflight();
    if (restoreFocus && !start.disabled && dialog.classList.contains("open") && document.activeElement === fallback) start.focus();
  };
  if (restoreFocus) fallback?.focus();
  start.disabled = true;
  try {
    await CR.flushSaves?.();
    if (origin !== JSON.stringify([S.context?.workspace, S.context?.project, S.seq?.id])) throw new Error("Project changed while saving. Start Export again from the intended sequence.");
    const view = CR.previewView?.();
    if (view && (view.pending || view.error || view.gesture || view.switching)) throw new Error("Finish the active edit or resolve saving before exporting.");
    preset = exportSettings();
  } catch (error) { $("#exOut").textContent = "Export could not start: " + error.message; exportPending = false; await refreshExportPreflight(); return; }
  const allSequences = $("#exAll").checked;
  const outs = $$("#exOutputs input:checked").map(i => i.dataset.o).filter(o => o !== "native").map(o => { const [w, h] = o.split("x").map(Number); return { suffix: o, width: w, height: h, fit: $("#exFit").value }; }); const native = $$("#exOutputs input:checked").some(i => i.dataset.o === "native");
  const body = { sequence: S.seq.id, preset, name: $("#exName").value || "export", actor: "human", _context: S.context }; if (outs.length) body.outputs = native ? [{ suffix: "native", width: S.seq.width, height: S.seq.height, fit: "pad" }, ...outs] : outs;
  const report = await refreshExportPreflight({ preset, all_sequences: allSequences }); if (!report?.ok) { exportPending = false; return; }
  body._context = report.context || body._context;
  let r;
  try { r = allSequences ? await api.json("POST", "/api/render_all", { preset, prefix: body.name, actor: "human", _context: body._context }) : await api.json("POST", "/api/render", body); }
  catch (error) { $("#exOut").textContent = "Export could not start: " + error.message; await finishPending(); return; }
  const jobs = r.jobs || [r]; $("#exOut").textContent = `Rendering ${jobs.length} job(s)…`;
  const poll = async () => { try { const all = await Promise.all(jobs.map(j => api.get("/api/render/" + encodeURIComponent(j.id)))); $("#exOut").innerHTML = all.map(j => { const qa = renderResultQA(j); return `${escapeExportText(j.name || j.id)}: ${j.status === "done" ? `${renderResultLinks(j) || "done"}${qa ? " · " + qa : ""}` : j.status === "error" ? `error — ${escapeExportText((j.error || "").slice(-600))}` : j.status === "queued" ? "queued…" : j.status === "running" && j.resource?.state === "waiting" ? "Waiting for processing slot…" : `${j.status === "cancelling" ? "cancelling" : "rendering"}… ${Math.round((j.progress || 0) * 100)}%`}`; }).join("<br>"); if (all.some(j => ["running", "queued", "cancelling"].includes(j.status))) setTimeout(poll, 1500); else await finishPending(); } catch (error) { $("#exOut").textContent = "Export status unavailable; retrying: " + error.message; setTimeout(poll, 1500); } }; poll(); }
function updateExportEncoders(desired = $("#exEnc").value, explicit = false) {
  if (!exportEncoderDetails) return;
  const model = window.FilmocityExportEncoders.choices(exportEncoderDetails, $("#exFormat").value, desired, explicit);
  const select = $("#exEnc"); select.replaceChildren();
  for (const item of model.options) { const option = document.createElement("option"); option.value = item.id; option.textContent = item.label; option.disabled = !!item.disabled; select.append(option); }
  select.disabled = model.disabled; select.value = model.selected || "";
  $("#exEncoderStatus").textContent = model.hint;
}
async function loadEncoders() {
  const request = ++exportEncoderLoad;
  $("#exEncoderStatus").textContent = "Finding available encoders…";
  try {
    const report = await api.get("/api/encoders");
    if (request !== exportEncoderLoad) return;
    if (!Array.isArray(report.details)) throw new Error("Restart Filmocity to load its current media tools.");
    exportEncoderDetails = report.details;
    updateExportEncoders(exportEncoderRequest || $("#exEnc").value, !!exportEncoderRequest);
    if ($("#dlgExport").classList.contains("open")) await refreshExportPreflight();
  } catch (error) {
    if (request === exportEncoderLoad) $("#exEncoderStatus").textContent = "Encoder discovery failed: " + error.message;
  }
}

function relinkReviewLines(owner, report) {
  const summary = report.summary, replacement = summary.replacement || report.info, lines = [
    `Selected source: ${owner.name}`, `Shared original: ${owner.physicalName}`, `Current file: ${owner.originalPath}`, `Replacement: ${report.path}`,
    summary.message || (report.ok ? "Review this source-wide replacement before applying." : "This replacement cannot be used."),
    `Replacement metadata: ${replacement.width || 0}×${replacement.height || 0}; ${replacement.frame_rate || replacement.fps || "unknown"} fps; ${Number.isFinite(replacement.duration) ? replacement.duration.toFixed(6) : "unknown"} native seconds; picture ${replacement.has_video ? "yes" : "no"}; audio ${replacement.has_audio ? "yes" : "no"}${replacement.channels ? `, ${replacement.channels} channel(s)` : ""}${replacement.sample_rate ? `, ${replacement.sample_rate} Hz` : ""}.`,
    "Shared source: every use is affected, including locked tracks. Clip positions, edits and track locks stay in place."
  ];
  const seconds = value => Number.isFinite(value) ? value.toFixed(6) : "unknown";
  const append = (values, describe, noun) => {
    if (!Array.isArray(values)) return;
    for (const value of values.slice(0, 50)) lines.push(describe(value));
    if (values.length > 50) lines.push(`${values.length - 50} more ${noun}; the complete inspection response contains every entry.`);
  };
  lines.push(`Affected media: ${(summary.affected_media_ids || []).length}; dependent source items: ${(summary.dependents || []).length}; timeline uses: ${(summary.uses || []).length}.`);
  append(summary.dependents, item => `${item.kind || "Dependent"} ${item.name || item.media_id}: logical In ${seconds(item.logical_in)}, duration ${seconds(item.logical_duration)}; native ${seconds(item.native_in)}–${seconds(item.native_out)}; interpretation factor ${item.factor}.`, "dependent sources");
  append(summary.uses, item => `Use ${item.sequence}/${item.track_id}/${item.clip_id}${item.locked ? " [locked track]" : ""}${item.hold ? " [held source frame]" : ""}: native ${seconds(item.native_in)}–${seconds(item.native_out)}; timeline duration ${seconds(item.duration)}.`, "timeline uses");
  if (summary.cleared_fields?.length) lines.push("Source-bound data to reset: " + summary.cleared_fields.join(", ") + ".");
  const warnings = [...new Set([...(summary.warnings || []), ...report.issues.map(issue => `${issue.severity}: ${issue.message}`)])];
  append(warnings, value => String(value), "warnings or issues");
  if (!report.ok) lines.push("Relink is blocked. Choose a compatible file or cancel; the project has not changed.");
  return lines;
}
function retireRelink(owner) {
  const dialog = $("#dlgSourceRelink");
  if (dialog.relinkOwner === owner) { dialog.relinkOwner = null; closeDlg("#dlgSourceRelink"); }
  const banner = $("#fsRelinkIntent"); if (banner?.relinkOwner === owner) banner.remove();
}
async function browseRelink(owner) {
  if (!CR.sourceRelinkCurrent(owner)) return false;
  CR.showTab("browser");
  await renderBrowser(S.browserPath || "");
  if (CR.sourceRelinkCurrent(owner)) $("#fsPath")?.focus();
  return true;
}
async function relinkFromBrowser(owner, path) {
  if (!CR.sourceRelinkCurrent(owner) || owner.pending) { if (owner && window.FilmocitySync.sameProject(owner.context, S.context)) status("This relink selection expired. Choose its current Relink control.", "err"); return false; }
  const dialog = $("#dlgSourceRelink"), output = $("#sourceRelinkReview"), apply = $("#sourceRelinkApply"), choose = $("#sourceRelinkChoose"), token = {};
  dialog.relinkOwner = owner; dialog.relinkGeneration = token;
  let report = null, pending = true;
  const visible = () => dialog.relinkOwner === owner && dialog.relinkGeneration === token && dialog.classList.contains("open");
  const current = () => visible() && CR.sourceRelinkCurrent(owner);
  const busy = value => { if (!visible()) return; pending = value; output.setAttribute("aria-busy", String(value)); apply.disabled = value || !report?.ok; choose.disabled = value; };
  $("#sourceRelinkCancel").onclick = () => CR.cancelSourceRelink(owner);
  choose.onclick = () => { if (!current() || owner.submitted) return; owner.generation++; owner.review = null; dialog.relinkOwner = null; closeDlg("#dlgSourceRelink"); browseRelink(owner); };
  apply.onclick = async () => {
    if (pending || !current() || !report || !CR.sourceRelinkReviewCurrent(owner, report)) {
      if (visible()) output.textContent = "The inspected source or saved revision changed. Choose a file and inspect it again.";
      return false;
    }
    busy(true);
    try { const result = await CR.applySourceRelink(owner, report, { current }); if (visible()) closeDlg("#dlgSourceRelink"); return result; }
    catch (error) { if (visible() && S.proj === owner.project && window.FilmocitySync.sameProject(owner.context, S.context)) { output.textContent = error.message || String(error); status(output.textContent, "err"); } return false; }
    finally { if (visible()) { busy(false); apply.disabled = true; choose.disabled = !!owner.submitted; } }
  };
  openDlg("#dlgSourceRelink"); output.textContent = `Inspecting replacement for ${owner.name}…\n${path}\nNo source change has been submitted.`; busy(true);
  try {
    report = await CR.inspectSourceRelink(owner, path, { current });
    if (!current()) return false;
    output.textContent = relinkReviewLines(owner, report).join("\n"); output.focus(); return report;
  } catch (error) {
    report = null;
    if (visible() && S.proj === owner.project && window.FilmocitySync.sameProject(owner.context, S.context)) { output.textContent = error.message || String(error); status(output.textContent, "err"); }
    return false;
  } finally { busy(false); }
}
async function snapshot(label) {
  try { const r = await CR.saveSnapshot(label); status(r.warning || (label === "human_final" && r.pair ? `Snapshot saved. ${r.pair.summary.n_changes} changes compared with the agent proposal.` : `Saved snapshot ${r.snapshot}`), r.warning ? "err" : ""); }
  catch (error) { status("Snapshot not confirmed: " + (error.message || error), "err"); }
}
function exportFrame() { return CR.exportProgramFrame(); }
function speedDialog() { const sel = CR.selectedClips().filter(x => x.c.media_id); if (sel.length !== 1) { status("Select one clip for speed/duration."); return; } const { c, tr } = sel[0]; $("#spSpeed").value = Math.round((c.speed || 1) * 100); $("#spReverse").checked = !!c.reverse; $("#spPitch").checked = (c.audio || {}).maintain_pitch !== false; $("#spInterp").value = c.time_interpolation || "frame_sampling"; const upd = () => { $("#spDurIn").value = fmtTC((c.out - c.in_) / (parseFloat($("#spSpeed").value) / 100), S.seq.fps); }; $("#spSpeed").oninput = upd; upd(); $("#spDurIn").onchange = () => { const d = parseTC($("#spDurIn").value); if (d > 0) $("#spSpeed").value = Math.round((c.out - c.in_) / d * 100); }; openDlg("#dlgSpeed");
  $("#spOk").onclick = () => { const sp = Math.max(0.1, parseFloat($("#spSpeed").value) / 100), oldD = clipDur(c), newD = (c.out - c.in_) / sp, ops = [{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, speed: sp, reverse: $("#spReverse").checked, time_interpolation: $("#spInterp").value, audio: { ...(c.audio || {}), maintain_pitch: $("#spPitch").checked } } }];
    if ($("#spRipple").checked) for (const x of tr.clips) if (x.start >= clipEnd(c) - 1e-6 && x.id !== c.id) ops.push({ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: x.id, start: x.start + (newD - oldD) } }); applyOps(ops, "speed", `speed ${Math.round(sp * 100)}%`); closeDlg("#dlgSpeed"); }; }
async function restoreDialog() { return window.FilmocityVersions.open(CR, "snapshots"); }
function projectsDialog() { return window.FilmocityProjects.open(CR, "open"); }
function prefsDialog() { const p = S.prefs; $("#pfProxySize").value = String(p.proxy?.max_edge || 1280); $("#pfProxyQuality").value = p.proxy?.quality || "balanced"; $("#pfVT").value = p.vt; $("#pfAT").value = p.at; $("#pfStill").value = p.still; $("#pfTitle").value = p.title; $("#pfAuto").value = p.autosave; $("#pfThumbs").checked = p.thumbs !== false; $("#pfWorkers").value = String(p.render_workers || 1); $("#pfAutoMin").value = p.autosave_minutes == null ? 5 : p.autosave_minutes; $("#pfDraft").checked = !!p.draft; $("#pfGpu").checked = p.gpu !== false; $("#pfTransSfx").checked = !!p.trans_sfx; $("#pfBgRender").checked = !!p.bg_render; $("#pfPre").value = p.preroll || 2; $("#pfPost").value = p.postroll || 2; openDlg("#dlgPrefs"); $("#pfCancel").onclick = () => closeDlg("#dlgPrefs");
  $("#pfSave").onclick = async () => { const button = $("#pfSave"); button.disabled = true; try { const next = Object.assign({}, S.prefs, { proxy: {max_edge: Number($("#pfProxySize").value), quality: $("#pfProxyQuality").value}, vt: parseFloat($("#pfVT").value) || 1, at: parseFloat($("#pfAT").value) || 1, still: parseFloat($("#pfStill").value) || 5, title: parseFloat($("#pfTitle").value) || 3, autosave: parseInt($("#pfAuto").value) || 25, thumbs: $("#pfThumbs").checked, render_workers: parseInt($("#pfWorkers").value) || 1, autosave_minutes: parseInt($("#pfAutoMin").value) || 0, draft: $("#pfDraft").checked, gpu: $("#pfGpu").checked, trans_sfx: $("#pfTransSfx").checked, bg_render: $("#pfBgRender").checked, preroll: parseFloat($("#pfPre").value) || 2, postroll: parseFloat($("#pfPost").value) || 2 }); await api.json("PUT", "/api/settings", {prefs: next}); S.prefs = next; S.tlopt.thumbs = next.thumbs; closeDlg("#dlgPrefs"); CR.renderTimeline(); status("Preferences saved. New media preparation uses the selected proxy size and quality."); } catch (error) { status("Preferences could not be saved: " + error.message, "err"); } finally { button.disabled = false; } }; }
function syncDialogReview(reviewed, names, alignment) {
  const result = reviewed.result, lines = ["Review synchronization — positive offsets start later than the reference:"];
  for (const id of result.media_ids) {
    const match = result.matches.find(value => value.id === id), offset = result.offsets[id];
    lines.push(`${names[id] || id}: ${offset >= 0 ? "+" : ""}${offset.toFixed(6)} s` + (match ? ` · correlation ${Number(match.correlation).toFixed(3)} · confidence ${Number(match.confidence).toFixed(3)} · overlap ${Number(match.overlap_seconds).toFixed(3)} s · resolution ${(Number(match.resolution) * 1000).toFixed(3)} ms · ${match.method || "measured"}` : " · reference"));
  }
  if (alignment) {
    lines.push(`Picture positions use the ${alignment.fps.toFixed(6)} fps sequence grid; audio positions use 48 kHz samples. Full source heads are retained.`);
    for (const placement of alignment.placements) {
      if (placement.picture_start != null) lines.push(`${names[placement.id] || placement.id} picture start: ${placement.picture_start.toFixed(6)} s · grid residual ${(placement.picture_residual * 1000).toFixed(3)} ms`);
      if (placement.audio_start != null) lines.push(`${names[placement.id] || placement.id} audio start: ${placement.audio_start.toFixed(6)} s · sample residual ${(placement.audio_residual * 1000).toFixed(3)} ms`);
    }
  }
  for (const warning of result.warnings || []) lines.push(String(warning));
  lines.push("Inspect the offsets before Create. Correlation is an estimate; verify picture and audio alignment afterward.");
  return lines.join("\n");
}
function multicamDialog() {
  const ids = [...S.binSel], project = S.proj, sequence = S.seq, context = { ...S.context }, source = JSON.stringify(ids.map(id => project.media[id])), names = Object.fromEntries(ids.map(id => [id, project.media[id]?.name || id]));
  const dialog = $("#dlgMulticam"), button = $("#mcGo"), output = $("#mcOut"), token = {}; dialog.syncOwner = token;
  let active = true, pending = false, reviewed = null, settings = null;
  const current = () => active && dialog.syncOwner === token && dialog.classList.contains("open") && S.proj === project && S.seq === sequence && S.context?.workspace === context.workspace && S.context?.project === context.project && JSON.stringify(ids.map(id => S.proj.media[id])) === source;
  const fields = () => ({ kind: "multicam", media_ids: [...ids], name: $("#mcName").value || "Multicam 01", sync: $("#mcSync").value, audioMode: $("#mcAudio").value });
  const reset = () => { reviewed = null; settings = null; button.textContent = $("#mcSync").value === "audio" ? "Analyze audio" : "Create"; };
  output.textContent = `${ids.length} captured source(s): ${ids.map(id => names[id]).join(", ")}`;
  button.disabled = false; reset(); openDlg("#dlgMulticam");
  $("#mcCancel").onclick = () => { active = false; closeDlg("#dlgMulticam"); };
  for (const selector of ["#mcName", "#mcSync", "#mcAudio"]) { $(selector).onchange = reset; $(selector).oninput = reset; }
  button.onclick = async () => {
    if (pending) return; pending = true; button.disabled = true;
    try {
      if (!current()) throw Error("The project, sequence or captured sources changed. Reopen Multicam.");
      const chosen = fields();
      if (chosen.sync === "audio" && !reviewed) {
        settings = chosen; output.textContent = "Queuing audio synchronization… Cancel closes this dialog; analysis remains in Tasks.";
        const queued = await CR.startSyncTask({ media_ids: ids });
        const valid = () => current() && JSON.stringify(fields()) === JSON.stringify(chosen);
        if (!valid()) throw Error("The multicam choices changed while synchronization was queued.");
        const result = await CR.waitSyncTask(queued, { isCurrent: valid, onProgress: task => { if (valid()) output.textContent = `${task.stage || task.status}… Cancel closes this dialog; analysis remains in Tasks.`; } });
        if (!valid()) throw Error("The multicam choices changed while synchronization completed.");
        reviewed = result; output.textContent = syncDialogReview(result, names, CR.previewSyncSequence(result, chosen)); button.textContent = "Create reviewed multicam"; return;
      }
      if (reviewed && JSON.stringify(settings) !== JSON.stringify(chosen)) throw Error("The multicam choices changed. Analyze and review them again.");
      const result = await CR.createSyncSequence(reviewed, { ...chosen, isCurrent: () => current() && JSON.stringify(fields()) === JSON.stringify(chosen) });
      if (active && dialog.syncOwner === token) { active = false; closeDlg("#dlgMulticam"); status(`Created ${result.name} — drag it to a track, then use Multi-Camera View and keys 1–9 to cut. Verify synchronization before editing.`); }
    } catch (error) { if (active && dialog.syncOwner === token) { output.textContent = error.message || String(error); status(output.textContent, "err"); } }
    finally { pending = false; if (dialog.syncOwner === token) button.disabled = false; }
  };
}
async function saveGraphicTemplate() { const sel = CR.selectedClips().filter(x => x.c.graphic || x.c.title); if (sel.length !== 1) { status("Select one graphic or text clip."); return; } const n = prompt("Template name", sel[0].c.graphic ? sel[0].c.graphic.name : "Text style"); if (!n) return; const st = await api.get("/api/settings"); st.graphics_templates = { ...(st.graphics_templates || {}), [n]: sel[0].c.graphic || { name: n, layers: [{ kind: "text", ...sel[0].c.title }] } }; await api.json("PUT", "/api/settings", st); S.gfxTemplates = st.graphics_templates; status(`Saved template "${n}"`); renderGfx(); }
function wireAudioMatch(c, tr) {
  const button = $("#amGo"); if (!button) return;
  const project = S.proj, sequence = S.seq;
  button.onclick = () => {
    if (S.proj !== project || S.seq !== sequence || !sequence.tracks.includes(tr) || !tr.clips.includes(c) || S.sel.size !== 1 || !S.sel.has(c.id)) { status("The inspected clip changed. Select it again before matching loudness.", "err"); return; }
    gainDialog({ mode: "loudness", target: Number($("#amTarget").value), clipIds: [c.id] });
  };
}
function audioGainReview(reviewed, names) {
  const result = reviewed.result, summary = reviewed.plan.summary, lines = [];
  for (const [i, value] of result.measurements.entries()) {
    const measured = result.mode === "peak" ? value.peak_db : value.integrated_lufs, unit = result.mode === "peak" ? "dBFS sample peak" : "LUFS";
    lines.push(`${names[i] || value.id}: ${measured == null ? "unmeasurable / silent" : measured.toFixed(2) + " " + unit}`);
    const gain = summary.gains?.find(item => item.clip_id === value.id);
    if (gain) lines.push(`Target ${gain.target} · shift ${gain.delta_db >= 0 ? "+" : ""}${gain.delta_db.toFixed(4)} dB · base ${gain.from_db} → ${gain.to_db} dB · ${gain.automation_points} gain points shifted`);
    if (gain && Number.isFinite(gain.predicted_peak_db)) lines.push(`Predicted sample peak ${gain.predicted_peak_db.toFixed(2)} dBFS`);
    if (gain && Number.isFinite(gain.predicted_true_peak_dbtp)) lines.push(`Predicted true peak ${gain.predicted_true_peak_dbtp.toFixed(2)} dBTP`);
    for (const warning of value.warnings.slice(0,20)) if (!(summary.warnings || []).includes(warning) && !lines.includes(String(warning))) lines.push(String(warning));
  }
  for (const warning of (summary.warnings || []).slice(0,50)) lines.push(String(warning));
  lines.push(summary.message || "Review these changes before applying.");
  if (!reviewed.plan.ops.length) lines.push("No gain changes are needed.");
  return lines.join("\n");
}
function gainDialog(options = {}) {
  const previous = $("#dlgGain"); previous.audioOwner = {}; if (previous.classList.contains("open")) closeDlg("#dlgGain");
  let capture; try { capture = CR.captureAudioTargets(options.clipIds); } catch (error) { status(error.message, "err"); return; }
  const dialog = $("#dlgGain"), button = $("#gOk"), output = $("#gInfo"), token = {}; dialog.audioOwner = token;
  let active = true, pending = false, reviewed = null, settings = null, generation = 0;
  const visible = () => active && dialog.audioOwner === token && dialog.classList.contains("open");
  const current = () => visible() && CR.audioTargetsCurrent(capture);
  const radios = $$('#dlgGain input[name="gm"]');
  for (const radio of radios) radio.checked = radio.value === (options.mode === "loudness" ? "lufs" : "set");
  $("#gSet").value = capture.gains[0]; $("#gAdj").value = 0;
  if (options.mode === "loudness") $("#gLufs").value = options.target ?? -18;
  const fields = () => {
    const mode = document.querySelector('input[name="gm"]:checked')?.value, selector = { set: "#gSet", adj: "#gAdj", peak: "#gPeak", lufs: "#gLufs" }[mode];
    const raw = selector ? String($(selector).value).trim() : "";
    if (!raw || !Number.isFinite(Number(raw))) throw Error("Enter a finite gain or normalization target.");
    return { mode, value: Number(raw) };
  };
  const reset = () => {
    generation++; reviewed = null; settings = null;
    const mode = document.querySelector('input[name="gm"]:checked')?.value;
    button.textContent = ["peak", "lufs"].includes(mode) ? "Analyze clip audio" : "Apply gain";
    button.disabled = pending;
    output.textContent = `${capture.clip_ids.length} selected clip(s). Set changes the base gain; Adjust adds a delta. Both shift every existing absolute gain point by the same amount, preserving its shape. Peak and loudness normalization require measurement and review.`;
  };
  dialog.oninput = reset; dialog.onchange = reset;
  openDlg("#dlgGain"); reset();
  $("#gCancel").onclick = () => { active = false; generation++; closeDlg("#dlgGain"); status("Audio controls closed. Queued analysis and any already submitted gain changes remain; inspect Tasks or the saved project."); };
  button.onclick = async () => {
    if (pending || !visible()) return;
    let chosen, epoch;
    try { if (!current()) throw Error("The selected clips or project changed. Reopen Audio Gain for the current selection."); chosen = fields(); epoch = generation; }
    catch (error) { output.textContent = error.message; return; }
    const unchanged = () => current() && epoch === generation && JSON.stringify(fields()) === JSON.stringify(chosen);
    pending = true; button.disabled = true;
    try {
      if (["set", "adj"].includes(chosen.mode)) {
        await CR.applyManualGain(capture, chosen.mode === "adj" ? "adjust" : "set", chosen.value, { isCurrent: unchanged });
      } else if (["peak", "lufs"].includes(chosen.mode)) {
        if (!reviewed) {
          const queued = await CR.startAudioTask(chosen.mode === "peak" ? "peak" : "loudness", { target: chosen.value, openTasks: false, isCurrent: unchanged }, capture);
          if (!unchanged()) throw Error("The audio choices changed. The analysis remains in Tasks.");
          output.textContent = "Audio analysis queued. Closing this dialog keeps the task in Tasks; no gain has been applied.";
          const result = await CR.waitAudioTask(queued, { isCurrent: unchanged, onProgress: task => { if (unchanged()) output.textContent = task.message || task.stage || "Measuring isolated clip audio…"; } });
          if (!unchanged()) throw Error("The audio choices changed while review loaded.");
          reviewed = result; settings = chosen; output.textContent = audioGainReview(result, capture.names); output.focus?.(); button.textContent = "Apply reviewed gain"; return;
        }
        if (JSON.stringify(settings) !== JSON.stringify(chosen)) throw Error("The gain choices changed. Analyze and review them again.");
        await CR.applyAudioTask(reviewed.task.id, reviewed, { isCurrent: unchanged });
      } else throw Error("Choose an audio gain mode.");
      if (visible()) { active = false; closeDlg("#dlgGain"); status("Clip gain applied. Existing automation shape was preserved; verify the final mix with track and master processing."); }
    } catch (error) { if (visible() && generation === epoch) { output.textContent = error.message || String(error); status(output.textContent, "err"); } }
    finally { pending = false; if (dialog.audioOwner === token) button.disabled = !!reviewed && !reviewed.plan.ops.length; }
  };
}
function removeAttrDialog() { const sel = CR.selectedClips(); if (!sel.length) return; openDlg("#dlgRemoveAttr"); $("#raCancel").onclick = () => closeDlg("#dlgRemoveAttr"); $("#raOk").onclick = () => { const picks = $$("#dlgRemoveAttr [data-ra]:checked").map(i => i.dataset.ra); const ops = sel.map(({ c, tr }) => { const p = { id: c.id }; if (picks.includes("transform")) p.transform = { ...(c.transform || {}), x: 0, y: 0, scale: 1, rotation: 0, anchor_x: 0, anchor_y: 0 }; if (picks.includes("opacity")) p.transform = { ...(p.transform || c.transform || {}), opacity: 1 }; if (picks.includes("color")) p.color = {}; if (picks.includes("fx_stack")) p.fx_stack = []; if (picks.includes("effects")) p.effects = {}; if (picks.includes("mask")) p.mask = {}; if (picks.includes("keyframes")) p.keyframes = {}; if (picks.includes("transitions")) { p.transition_in = null; p.transition_out = null; p.audio_transition_in = null; p.audio_transition_out = null; } if (picks.includes("audio")) p.audio = { ...(c.audio || {}), gain_db: 0, pan: 0, fade_in: 0, fade_out: 0 }; if (picks.includes("afx_stack")) { p.afx_stack = []; p.audio_fx = {}; } if (picks.includes("speed")) { p.speed = 1; p.time_remap = null; p.reverse = false; } if (picks.includes("blend")) p.blend = "normal"; return { op: "set_clip", sequence: S.seq.id, track: tr.id, clip: p }; }); applyOps(ops, "remove_attributes", `remove attributes (${picks.join(", ")})`); closeDlg("#dlgRemoveAttr"); }; }
function reframeDialog() { openDlg("#dlgReframe"); $("#rfCancel").onclick = () => closeDlg("#dlgReframe"); $("#rfOk").onclick = () => { const [w, h] = $("#rfAspect").value.split("x").map(Number); const sq = deep(S.seq); sq.id = "seq_" + CR.uid().slice(0, 6); sq.name = `${S.seq.name} (${w}×${h})`; sq.width = w; sq.height = h; sq.multicam = false; for (const t of sq.tracks) for (const c of t.clips) { c.id = CR.uid(); if (c.media_id || c.sequence_id) { c.fit = "cover"; c.transform = { ...(c.transform || {}), scale: 1, x: 0, y: 0 }; } } applyOps([{ op: "insert", path: `/sequences/${S.proj.sequences.length}`, value: sq }], "auto_reframe", `auto reframe to ${w}x${h}`); CR.switchSeq(sq.id); closeDlg("#dlgReframe"); status("Reframed copy created — clips fill the frame; keyframe Position to follow the subject."); }; }
function mergeDialog() {
  const selected = [...S.binSel], project = S.proj, sequence = S.seq, context = { ...S.context };
  const video = selected.map(id => project.media[id]).filter(m => m?.has_video), audio = selected.map(id => project.media[id]).filter(m => m?.has_audio && !m.has_video);
  const ids = video.length === 1 && audio.length === 1 && selected.length === 2 ? [video[0].id, audio[0].id] : [], names = Object.fromEntries(ids.map(id => [id, project.media[id]?.name || id])), source = JSON.stringify(ids.map(id => project.media[id]));
  const dialog = $("#dlgMerge"), button = $("#mgOk"), output = $("#mgInfo"), token = {}; dialog.syncOwner = token;
  let active = true, pending = false, reviewed = null, settings = null;
  const current = () => active && dialog.syncOwner === token && dialog.classList.contains("open") && S.proj === project && S.seq === sequence && S.context?.workspace === context.workspace && S.context?.project === context.project && JSON.stringify(ids.map(id => S.proj.media[id])) === source;
  const fields = () => ({ kind: "merge", media_ids: [...ids], name: $("#mgName").value || "Merged Clip", sync: "audio" });
  const reset = () => { reviewed = null; settings = null; button.textContent = "Analyze audio"; };
  output.textContent = ids.length === 2 ? `Captured video: ${names[ids[0]]} · Audio: ${names[ids[1]]}` : "Select exactly one video and one audio-only source in the Project panel.";
  button.disabled = ids.length !== 2; reset(); openDlg("#dlgMerge");
  $("#mgCancel").onclick = () => { active = false; closeDlg("#dlgMerge"); };
  $("#mgName").oninput = reset; $("#mgName").onchange = reset;
  button.onclick = async () => {
    if (pending || ids.length !== 2) return; pending = true; button.disabled = true;
    try {
      if (!current()) throw Error("The project, sequence or captured sources changed. Reopen Merge Clips.");
      const chosen = fields();
      if (!reviewed) {
        settings = chosen; output.textContent = "Queuing audio synchronization… Cancel closes this dialog; analysis remains in Tasks.";
        const queued = await CR.startSyncTask({ media_ids: ids });
        const valid = () => current() && JSON.stringify(fields()) === JSON.stringify(chosen);
        if (!valid()) throw Error("The merge choices changed while synchronization was queued.");
        const result = await CR.waitSyncTask(queued, { isCurrent: valid, onProgress: task => { if (valid()) output.textContent = `${task.stage || task.status}… Cancel closes this dialog; analysis remains in Tasks.`; } });
        if (!valid()) throw Error("The merge choices changed while synchronization completed.");
        reviewed = result; output.textContent = syncDialogReview(result, names, CR.previewSyncSequence(result, chosen)); button.textContent = "Create reviewed merge"; return;
      }
      if (JSON.stringify(settings) !== JSON.stringify(chosen)) throw Error("The merge choices changed. Analyze and review them again.");
      const result = await CR.createSyncSequence(reviewed, { ...chosen, isCurrent: () => current() && JSON.stringify(fields()) === JSON.stringify(chosen) });
      if (active && dialog.syncOwner === token) { active = false; closeDlg("#dlgMerge"); status(`Merged clip ${result.name} created — drag it from the Project panel. Verify the reviewed audio alignment.`); }
    } catch (error) { if (active && dialog.syncOwner === token) { output.textContent = error.message || String(error); status(output.textContent, "err"); } }
    finally { pending = false; if (dialog.syncOwner === token) button.disabled = false; }
  };
}
async function newItem(kind) { let color = "#000000"; if (kind === "color") { color = prompt("Matte color (#RRGGBB)", "#E8631C"); if (!color) return; } const dur = kind === "counting_leader" ? 11 : (S.prefs.still || 5); await api.json("POST", "/api/media/synthetic", { kind, color, duration: dur, width: S.seq.width, height: S.seq.height, actor: "human" }); status(`Created ${kind.replace("_", " ")} item`); }
async function renderReplace() {
  try { return await CR.startRenderReplace(); }
  catch (error) { status(error.message || String(error), "err"); return false; }
}
function replaceFromBin() { return CR.replaceFromBinSource(); }
function renameClip() { const sel = CR.selectedClips(); if (sel.length !== 1) return; const { c, tr } = sel[0]; const n = prompt("Clip name", c.name || (c.media_id && S.proj.media[c.media_id] ? S.proj.media[c.media_id].name : "")); if (n == null) return; applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, name: n } }], "rename", "rename clip"); }
function duplicateMedia() { return CR.duplicateBinSources(); }
function breakoutAudio() { return CR.breakoutAudioSource(); }
function removeUnused() { const used = new Set(); for (const sq of S.proj.sequences) for (const t of sq.tracks) for (const c of t.clips) if (c.media_id) { used.add(c.media_id); const m = S.proj.media[c.media_id]; if (m && m.subclip_of) used.add(m.subclip_of); } const unused = Object.keys(S.proj.media).filter(id => !used.has(id)); if (!unused.length) { status("No unused media."); return; } if (!confirm(`Remove ${unused.length} unused item(s) from the project? (files are not deleted)`)) return; applyOps(unused.map(id => ({ op: "remove", path: `/media/${id}` })), "remove_unused", `remove ${unused.length} unused`); }
function renderInOut() { return CR.getRenderedPreview().start({ range: true }); }
async function remixDialog() {
  const selected = CR.selectedClips();
  if (selected.length !== 1 || selected[0].tr.kind !== "audio" || !S.proj.media[selected[0].c.media_id]?.has_audio) { status("Select one music clip on an audio track."); return; }
  const { c, tr } = selected[0], project = S.proj, sequence = S.seq, media = project.media[c.media_id];
  const value = prompt(`Remix "${media.name || "music"}" to duration (seconds), currently ${clipDur(c).toFixed(3)} s. Review the proposed joins in Tasks before applying.`, String(clipDur(c)));
  if (value == null || !value.trim()) return;
  const target = Number(value);
  if (!Number.isFinite(target) || target < 1 / 48000 || target > 3600) { status("Choose a finite target from one audio sample to one hour.", "err"); return; }
  if (S.proj !== project || S.seq !== sequence || project.media[c.media_id] !== media || !sequence.tracks.includes(tr) || !tr.clips.includes(c) || CR.selectedClips().length !== 1 || CR.selectedClips()[0].c !== c) { status("The remix target changed. Select the clip again.", "err"); return false; }
  try { return await CR.startClipAnalysis("remix", { target, bars_per_phrase: 4 }); }
  catch (error) { status(error.message || String(error), "err"); return false; }
}
function timeTuner() { const cur = CR.seqDur(); const v = prompt(`Time Tuner — fit the sequence to a duration (seconds). Current ${cur.toFixed(2)} s. Premiere-style: tiny uniform speed changes on every clip, cuts kept in order; up to ±10% is invisible.`, String(Math.round(cur))); if (!v) return; const target = parseFloat(v); if (!(target > 1) || Math.abs(target - cur) < 0.01) return; const f = cur / target; if (f < 0.7 || f > 1.4) { if (!confirm(`This needs ${((f - 1) * 100).toFixed(0)}% speed change — beyond Time Tuner's invisible range. Apply anyway?`)) return; }
  const si = S.proj.sequences.indexOf(S.seq); const ops = []; for (const tr of S.seq.tracks) for (const c of tr.clips) { const patch = { id: c.id, start: +(c.start / f).toFixed(4) }; if (c.media_id && !(S.proj.media[c.media_id] || {}).is_image && !c.hold && !c.time_remap) patch.speed = +((c.speed || 1) * f).toFixed(4); else if (!c.media_id || (S.proj.media[c.media_id] || {}).is_image) patch.out = +(c.in_ + (c.out - c.in_) / f).toFixed(4); ops.push({ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: patch }); }
  ops.push({ op: "set", path: `/sequences/${si}/markers`, value: (S.seq.markers || []).map(m => ({ ...m, time: +(m.time / f).toFixed(4) })) }); ops.push({ op: "set", path: `/sequences/${si}/captions`, value: (S.seq.captions || []).map(cp => ({ ...cp, start: +(cp.start / f).toFixed(3), end: +(cp.end / f).toFixed(3) })) }); applyOps(ops, "time_tuner", `time tuner ${cur.toFixed(2)}s → ${target}s (${((f - 1) * 100).toFixed(1)}% speed)`); }
let cacheControls;
async function diagnosticsDialog() { openDlg("#dlgDiag"); $("#diagClose").onclick = () => closeDlg("#dlgDiag");
  cacheControls ||= window.FilmocityCacheControls.create(CR, {buttons: $$("[data-cache]"), info: $("#cacheInfo"), output: $("#cacheStatus"), dialog: $("#dlgDiag"), refreshButton: $("#cacheRefresh")});
  cacheControls.refresh(); const dw = $("#diagWhisper"); if (dw) dw.onclick = async () => { dw.disabled = true; dw.textContent = "Installing…"; try { const r = await api.json("POST", "/api/install/whisper", {}); status(r.message, r.ok ? "" : "err"); dw.textContent = r.ok ? "Auto-captions ready" : "Install failed"; } catch (e) { dw.textContent = "Install failed"; } };
  const d = await api.get("/api/diagnostics"); const ok = v => v ? "✅" : "✗";
  $("#diagOut").innerHTML = `<b>${d.platform}</b> · Python ${d.python} · data root ${d.data_root}${d.disk_free_gb != null ? ` · ${d.disk_free_gb} GB free` : ""}<br>FFmpeg: ${d.ffmpeg || "<span style='color:#ffb3b3'>not found</span>"}<br>Encoders: ${d.encoders.join(", ") || "—"}<br>Filters: ${Object.entries(d.filters).map(([k, v]) => `${ok(v)} ${k}`).join(" · ")}<br>Fonts bundled ${ok(d.fonts_bundled)} · fontconfig ${ok(d.fontconfig)} · faster-whisper ${ok(d.whisper)}<br><br>${d.issues.length ? "<b>Attention</b><br>" + d.issues.map(i => "• " + i).join("<br>") : "<b style='color:#9fe0b7'>Listed components are present. Export checks the selected encoder on this device.</b>"}`; }
async function backupsDialog() { return window.FilmocityVersions.open(CR, "backups"); }
function recipeDialogControls(mode, capture, prefix, fields) {
  const control = { reel: "reel", talking_head: "th", explainer: "xp", variants: "hv" }[mode];
  const dialog = $("#dlg" + prefix), button = $("#" + control + "Go"), output = $("#" + control + "Out"), token = {};
  dialog.recipeOwner = token;
  let active = true, pending = false, reviewed = null, choices = null, generation = 0, loading = false;
  const visible = () => active && dialog.recipeOwner === token && dialog.classList.contains("open");
  const current = () => visible() && CR.recipeTargetsCurrent(capture);
  const reset = () => { generation++; reviewed = null; choices = null; button.textContent = "Prepare recipe"; button.disabled = pending || loading; if (visible()) output.textContent = "Review the recipe plan before applying. No project changes have been submitted."; };
  dialog.oninput = reset; dialog.onchange = reset;
  openDlg("#dlg" + prefix); reset();
  $("#" + control + "Cancel").onclick = () => { active = false; generation++; closeDlg("#dlg" + prefix); status("Recipe controls closed. Queued tasks and any already submitted Apply remain; inspect Tasks or the saved project."); };
  button.onclick = async () => {
    if (pending || loading || !visible()) return;
    let chosen, epoch;
    try { if (!current()) throw Error("The recipe targets or project changed. Reopen these controls for the current selection."); chosen = fields(); epoch = generation; }
    catch (error) { output.textContent = error.message; return; }
    const unchanged = () => current() && epoch === generation && JSON.stringify(fields()) === JSON.stringify(chosen);
    pending = true; button.disabled = true;
    try {
      if (!reviewed) {
        const queued = await CR.startRecipeTask(mode, { ...chosen, openTasks: false, isCurrent: unchanged }, capture);
        if (!unchanged()) throw Error("The recipe choices changed. Its task remains in Tasks.");
        output.textContent = "Preparing the recipe. Closing these controls keeps the task in Tasks for review or cancellation.";
        const result = await CR.waitRecipeTask(queued, { isCurrent: unchanged, onProgress: task => { if (unchanged()) output.textContent = task.message || task.stage || "Preparing recipe…"; } });
        if (!unchanged()) throw Error("The recipe targets changed while review loaded.");
        reviewed = result; choices = chosen; output.textContent = CR.recipeReviewLines(result).join("\n"); output.focus?.(); button.textContent = mode === "reel" ? "Create reviewed sequence" : mode === "variants" ? "Create reviewed variants" : "Apply reviewed recipe"; return;
      }
      if (JSON.stringify(choices) !== JSON.stringify(chosen)) throw Error("The recipe choices changed. Prepare and review the plan again.");
      await CR.applyRecipeTask(reviewed.task.id, reviewed, { isCurrent: unchanged });
      if (visible()) { active = false; closeDlg("#dlg" + prefix); status(mode === "reel" ? "New Reel sequence created. Review playback before exporting." : mode === "variants" ? "Hook variants created. Review the new sequences before exporting." : mode === "explainer" ? "Explainer cards added. Review timing and text before exporting." : "Talking Head recipe applied. Review cuts, captions and audio before exporting."); }
    } catch (error) { if (visible() && generation === epoch) { output.textContent = error.message || String(error); status(output.textContent, "err"); } }
    finally { pending = false; if (dialog.recipeOwner === token) button.disabled = loading || !!reviewed && !reviewed.plan.ops.length; }
  };
  return { current, visible, output, loading(value) { loading = value; if (dialog.recipeOwner === token) button.disabled = value || pending || !!reviewed && !reviewed.plan.ops.length; } };
}
function recipeNumber(selector, label) {
  const value = String($(selector).value).trim();
  if (!value || !Number.isFinite(Number(value))) throw Error("Enter a finite " + label + ".");
  return Number(value);
}
function recipeOptions(selector, choices, selected = "") {
  const input = $(selector); input.replaceChildren();
  for (const [value, name] of choices) { const option = document.createElement("option"); option.value = value; option.textContent = name; input.appendChild(option); }
  input.value = selected;
}
async function newReelDialog() {
  const previous = $("#dlgReel"); previous.recipeOwner = {}; if (previous.classList.contains("open")) closeDlg("#dlgReel");
  let capture; try { capture = CR.captureRecipeTargets("reel"); } catch (error) { status(error.message, "err"); return; }
  $("#reelShots").textContent = capture.shots.map(id => capture.names[id]).join(", ");
  $("#reelName").value = "New Reel"; $("#reelCanvas").value = "portrait"; $("#reelRhythm").value = "even"; $("#reelFraming").value = "auto";
  recipeOptions("#reelMusic", [["", "None"], ...capture.music.map(id => [id, capture.names[id]])]);
  recipeOptions("#reelLook", [["", "None"]]); recipeOptions("#reelCaps", [["", "None"]]);
  let presets = {}, looks = [];
  const fields = () => {
    const look = $("#reelLook").value || null, style = $("#reelCaps").value;
    if (look && !looks.some(item => item.path === look) || style && !Object.hasOwn(presets, style)) throw Error("Choose a captured look and caption style.");
    return { name: $("#reelName").value.trim(), canvas: $("#reelCanvas").value, framing: $("#reelFraming").value, rhythm: $("#reelRhythm").value,
      music: $("#reelMusic").value || null, target: recipeNumber("#reelLen", "reel duration"), hook: $("#reelHook").value.trim(), hook_sub: $("#reelHookSub").value.trim(), cta: $("#reelCta").value.trim(),
      look, captions: !!style, caption_style: style ? CR.applyBrand(deep(presets[style])) : null, sfx: $("#reelSfx").checked };
  };
  const controls = recipeDialogControls("reel", capture, "Reel", fields); controls.loading(true); controls.output.textContent = "Loading looks and caption styles…";
  try {
    const loaded = await Promise.all([S.luts || api.get("/api/luts"), S.capPresets || api.get("/api/presets/caption_styles")]);
    if (!controls.current()) throw Error("The project or selected shots changed while recipe controls loaded. Reopen New Reel.");
    if (!Array.isArray(loaded[0]) || !loaded[1] || typeof loaded[1] !== "object" || Array.isArray(loaded[1])) throw Error("Looks or caption styles could not be loaded. Reopen New Reel.");
    looks = deep(loaded[0]); presets = deep(loaded[1]);
    recipeOptions("#reelLook", [["", "None"], ...looks.map(item => [item.path, item.name || item.path])]);
    recipeOptions("#reelCaps", [["", "None"], ...Object.keys(presets).map(name => [name, name])], Object.hasOwn(presets, "Bold Pop") ? "Bold Pop" : "");
    controls.output.textContent = `Creates a new sequence at ${capture.fps} fps. Existing sequences remain intact. Choose music explicitly; onset timing requires a reliable measured result.`;
    controls.loading(false);
  } catch (error) { if (controls.visible()) controls.output.textContent = error.message || String(error); }
}
function talkingHeadDialog() {
  const previous = $("#dlgTalkingHead"); previous.recipeOwner = {}; if (previous.classList.contains("open")) closeDlg("#dlgTalkingHead");
  let capture; try { capture = CR.captureRecipeTargets("talking_head"); } catch (error) { status(error.message, "err"); return; }
  $("#thClip").textContent = capture.clip_name; $("#thBrollSources").textContent = capture.broll.length ? capture.broll.map(id => capture.names[id]).join(", ") : "No B-roll selected in the bin";
  $("#thPunch").value = 3; $("#thThreshold").value = -38; $("#thGap").value = .45; $("#thPad").value = .08;
  for (const id of ["thSilences", "thVoice", "thCaptions"]) $("#" + id).checked = true;
  $("#thBroll").checked = capture.broll.length > 0; $("#thBroll").disabled = !capture.broll.length;
  recipeDialogControls("talking_head", capture, "TalkingHead", () => ({ punch_every: recipeNumber("#thPunch", "punch-in interval"), threshold_db: recipeNumber("#thThreshold", "silence threshold"), min_gap: recipeNumber("#thGap", "minimum silence gap"), pad: recipeNumber("#thPad", "silence padding"),
    silences: $("#thSilences").checked, voice_preset: $("#thVoice").checked, captions: $("#thCaptions").checked, broll: $("#thBroll").checked ? [...capture.broll] : [] }));
}
async function variantsDialog() {
  const previous = $("#dlgHookVariants"); previous.recipeOwner = {}; if (previous.classList.contains("open")) closeDlg("#dlgHookVariants");
  let capture; try { capture = CR.captureRecipeTargets("variants"); } catch (error) { status(error.message, "err"); return; }
  $("#hvPrefix").value = capture.sequence_name; $("#hvHooks").value = "";
  const targets = $("#hvTargets"), inputs = []; targets.replaceChildren();
  for (const choice of capture.text_targets) {
    const label = document.createElement("label"), input = document.createElement("input"), text = document.createElement("span");
    input.type = "checkbox"; input.checked = false; input.disabled = choice.locked; text.textContent = choice.label + (choice.locked ? " (locked — unlock to select)" : "");
    label.appendChild(input); label.appendChild(text); targets.appendChild(label); inputs.push({ input, target: choice.target });
  }
  recipeDialogControls("variants", capture, "HookVariants", () => ({ name_prefix: $("#hvPrefix").value.trim(), hooks: $("#hvHooks").value.split(/\n/).map(value => value.trim()).filter(Boolean), targets: inputs.filter(value => value.input.checked).map(value => deep(value.target)) }));
}
async function renderCutSummary() {
  const el = $("#cutSummary"); if (!el) return;
  const token = {}, project = S.proj, sequence = S.seq, context = { ...S.context }; el.summaryOwner = token;
  const current = () => el.isConnected !== false && $("#cutSummary") === el && el.summaryOwner === token && S.proj === project && S.seq === sequence && S.context?.workspace === context.workspace && S.context?.project === context.project;
  el.textContent = "Loading saved sequence summary…"; el.setAttribute("aria-busy", "true");
  try { const result = await CR.requestSequenceDescription({ current }); if (current()) el.textContent = result.text; }
  catch (error) { if (current()) el.textContent = error.message || "The saved summary could not be loaded."; }
  finally { if (current()) el.removeAttribute("aria-busy"); }
}
async function coverDialog() {
  const dialog = $("#dlgCover"), token = {}; dialog.recipeOwner = token; if (dialog.classList.contains("open")) closeDlg("#dlgCover");
  let capture; try { capture = CR.captureRecipeTargets("cover"); } catch (error) { status(error.message, "err"); return; }
  const output = $("#cvOut"), results = $("#cvResults"), button = $("#cvGo"); let active = true, pending = false, loading = true, generation = 0, reviewed = null;
  const visible = () => active && dialog.recipeOwner === token && dialog.classList.contains("open"), current = () => visible() && CR.recipeTargetsCurrent(capture);
  const clear = () => { reviewed = null; results.replaceChildren(); button.textContent = "Render covers"; button.disabled = loading || pending; };
  const reset = () => { generation++; clear(); if (visible()) output.textContent = "The complete captured composition is preserved. Rendered covers do not change the timeline."; };
  $("#cvTime").value = capture.duration > 0 ? Math.max(0, Math.min(capture.time, capture.duration - CR.frame())) : 0;
  $("#cvHeadline").value = ""; $("#cvSub").value = ""; $("#cvFraming").value = "blur_fill";
  $("#cvSizes").value = capture.width + "x" + capture.height; recipeOptions("#cvTemplate", [["Hook — Big Statement", "Hook — Big Statement"]], "Hook — Big Statement");
  dialog.oninput = reset; dialog.onchange = reset;
  $("#cvCancel").onclick = () => { active = false; generation++; results.replaceChildren(); closeDlg("#dlgCover"); status("Cover controls closed. Any queued render remains in Tasks for review or cancellation."); };
  openDlg("#dlgCover"); clear(); output.textContent = "Loading captured template choices…";
  const fields = () => {
    const sizes = $("#cvSizes").value.split(/\n/).map(value => value.trim()).filter(Boolean).map(value => { const match = value.match(/^(\d+)\s*[x×]\s*(\d+)$/i); if (!match) throw Error("Enter one output size per line, for example 1920x1080."); return [+match[1], +match[2]]; });
    return { time: recipeNumber("#cvTime", "cover frame time"), headline: $("#cvHeadline").value, sub: $("#cvSub").value, sizes, framing: $("#cvFraming").value, template: $("#cvTemplate").value };
  };
  button.onclick = async () => {
    if (loading || pending || !visible() || reviewed) return;
    let choices, epoch;
    try { if (!current()) throw Error("The cover project or sources changed. Reopen Cover."); choices = fields(); epoch = generation; } catch (error) { output.textContent = error.message; return; }
    const unchanged = () => current() && epoch === generation && JSON.stringify(fields()) === JSON.stringify(choices);
    pending = true; button.disabled = true;
    try {
      const queued = await CR.startCoverTask({ ...choices, openTasks: false, isCurrent: unchanged }, capture);
      if (!unchanged()) throw Error("The cover choices changed. Its task remains in Tasks.");
      output.textContent = "Rendering covers. Closing these controls keeps the task in Tasks.";
      const value = await CR.waitCoverTask(queued, { isCurrent: unchanged, onProgress: task => { if (unchanged()) output.textContent = task.message || task.stage || "Rendering covers…"; } });
      if (!unchanged() || !CR.coverReviewCurrent(value)) throw Error("The sequence changed while covers loaded. Review the task again.");
      reviewed = value; output.textContent = [value.plan.summary.message || "Review the rendered covers below.", `Captured sequence frame: ${value.result.time.toFixed(6)} s`, ...value.plan.summary.warnings].join("\n");
      for (const cover of value.result.covers) {
        const figure = document.createElement("figure"), image = document.createElement("img"), caption = document.createElement("figcaption"), link = document.createElement("a");
        image.src = cover.url; image.alt = `Rendered cover ${cover.width} × ${cover.height}`; image.style.maxWidth = "100%";
        image.onerror = () => { if (visible() && reviewed === value) output.textContent = "The cover preview could not load. Review its task again before downloading."; };
        caption.textContent = `${cover.width} × ${cover.height} · ${cover.size} bytes · SHA-256 ${cover.sha256}`;
        link.textContent = `Download ${cover.width} × ${cover.height} PNG`; link.href = cover.url + "&download=1&revision=" + encodeURIComponent(value.context.revision); link.download = cover.filename;
        link.onclick = event => { if (!unchanged() || !CR.coverReviewCurrent(value)) { event.preventDefault(); output.textContent = "The cover owner changed. Review the task again before downloading."; } };
        figure.appendChild(image); figure.appendChild(caption); figure.appendChild(link); results.appendChild(figure);
      }
      button.textContent = "Covers ready"; output.focus?.();
    } catch (error) { if (visible() && generation === epoch) { output.textContent = error.message || String(error); status(output.textContent, "err"); } }
    finally { pending = false; if (dialog.recipeOwner === token) button.disabled = loading || !!reviewed; }
  };
  try {
    const templates = await api.get("/api/templates");
    if (!current()) throw Error("The cover project changed while templates loaded. Reopen Cover.");
    if (!templates || typeof templates !== "object" || Array.isArray(templates) || Object.keys(templates).length > 512) throw Error("The template catalog was not confirmed.");
    recipeOptions("#cvTemplate", Object.keys(templates).map(name => [name, name]), Object.hasOwn(templates, "Hook — Big Statement") ? "Hook — Big Statement" : Object.keys(templates)[0] || "");
    loading = false; reset();
  } catch (error) { if (visible()) { output.textContent = error.message || String(error); button.disabled = true; } }
}
async function explainerDialog() {
  const previous = $("#dlgExplainer"); previous.recipeOwner = {}; if (previous.classList.contains("open")) closeDlg("#dlgExplainer");
  let capture; try { capture = CR.captureRecipeTargets("explainer"); } catch (error) { status(error.message, "err"); return; }
  $("#xpLower").checked = false; $("#xpName").value = ""; $("#xpRole").value = ""; $("#xpAt").value = 1; $("#xpLowerDuration").value = 4.5;
  $("#xpChapters").checked = true; $("#xpChapterDuration").value = 3; $("#xpEnd").value = ""; $("#xpEndDuration").value = 3;
  recipeDialogControls("explainer", capture, "Explainer", () => ({ lower_third: $("#xpLower").checked ? { name: $("#xpName").value.trim(), role: $("#xpRole").value.trim(), at: recipeNumber("#xpAt", "lower-third start"), duration: recipeNumber("#xpLowerDuration", "lower-third duration") } : null,
    chapters: $("#xpChapters").checked, chapter_duration: recipeNumber("#xpChapterDuration", "chapter duration"), end_card: $("#xpEnd").value.trim(), end_duration: recipeNumber("#xpEndDuration", "end-card duration") }));
}
function recentDialog() { return window.FilmocityProjects.open(CR, "recent"); }
function duplicateProjectDialog() { return window.FilmocityProjects.open(CR, "duplicate"); }
function addTracksDialog() { const v = parseInt(prompt("Add how many video tracks?", "1")) || 0; const a = parseInt(prompt("Add how many audio tracks?", "1")) || 0; for (let i = 0; i < v; i++) CR.addTrack("video"); for (let i = 0; i < a; i++) CR.addTrack("audio"); status(`Added ${v} video and ${a} audio track(s)`); }
function tourDialog() { alert(`Filmocity in ten minutes\n\n1. Help › System Check, then Info › Brand kit and the Brief.\n2. Drop footage in the Project panel (anything FFmpeg opens).\n3. Select shots, pick music, File › New Reel from Footage.\n4. Type tool: click text in the monitor to reword it. Effect Controls: type a Duration. Graphics workspace: Layer Animator, template gallery.\n5. Header: agent mode. Proposals panel: Preview, then accept/reject with a reason — that trains the advisor. Ctrl+Z undoes agent edits too.\n6. Sequence › Create Hook Variants for A/B.\n7. Enter renders the exact preview; Export with a platform preset; Render Queue › Manifest CSV.\n\nFull text: docs/PLAYBOOK.md`); }

function duckAll() { return window.FilmocityDucking.open(); }
function audioXfadeAll() { const ops = []; let n = 0; for (const tr of S.seq.tracks) { const cs = [...tr.clips].sort((a, b) => a.start - b.start); for (let i = 1; i < cs.length; i++) { const a = cs[i - 1], b = cs[i]; if (Math.abs(CR.clipEnd(a) - b.start) < CR.frame() / 2 && (tr.kind === "audio" || (a.media_id && b.media_id))) { ops.push({ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: b.id, audio_transition_in: { type: "constant_power", duration: 0.25 } } }); n++; } } } if (!n) { status("No adjacent cuts found."); return; } applyOps(ops, "audio_xfade_all", `constant power on ${n} cuts`); status(`Constant-power crossfade on ${n} cut(s)`); }
function reviewLink() { api.get("/api/jobs").then(jobs => { const done = jobs.filter(j => j.status === "done" && (j.out || "").match(/\.(mp4|mov|webm)$/)).sort((a, b) => (b.finished || 0) - (a.finished || 0))[0]; if (!done) { status("No finished render yet."); return; } window.open(done.review_url || `/review/${encodeURIComponent(done.name)}`, "_blank"); status(`Review page opened for ${done.name} — comments come back as note markers`); }); }
function collectProject() { return window.FilmocityCollection.open(); }
function menuAction(act) {
  if (act === "commands") return window.FilmocityCommands.open(CR);
  if (act === "recovery") return window.FilmocityRecovery.open(CR);
  if (!S.proj && !["newProject", "openProject", "recent", "sample", "shortcuts", "guide", "matrix", "about", "diag", "tour", "prefs"].includes(act)) { status("Open or recover a project first.", "err"); return; }
  const si = S.proj ? S.proj.sequences.indexOf(S.seq) : 0; const acts = { packageProject: () => window.FilmocityPackage.open("package"), importPackage: () => window.FilmocityPackage.open("package_import"), newProject: () => window.FilmocityProjects.open(CR, "new"), openProject: projectsDialog, saveAs: () => window.FilmocityProjects.open(CR, "save_as"), collect: collectProject,
  // Registered 2026-09-07. These four handlers are defined just above
  // (talkingHeadDialog 273, duckAll 274, audioXfadeAll 275, reviewLink 276)
  // and index.html carries a menu button for each, but none of them were in
  // this map. The dispatcher is `if (acts[act]) acts[act]()`, so the miss was
  // swallowed: clicking any of the four produced no fetch, no status text and
  // no console error -- verified live, which is what distinguishes an
  // unregistered action from one that threw. Their backends were alive the
  // whole time: /api/audio/duck_all (server.py:1187),
  // /api/recipes/talking_head (1120), /review/{name} (1090).
  // Note the name mismatch: the button says talkingHead, the function is
  // talkingHeadDialog -- which is presumably how it was missed.
  talkingHead: talkingHeadDialog, duckAll: duckAll, audioXfadeAll: audioXfadeAll, reviewLink: reviewLink,
  uiReels: () => CR.setUiOverlay("reels"), uiTiktok: () => CR.setUiOverlay("tiktok"), uiShorts: () => CR.setUiOverlay("shorts"), uiOff: () => CR.setUiOverlay(null),
  recent: recentDialog, duplicateProject: duplicateProjectDialog, addTracks: addTracksDialog, tour: tourDialog, cover: coverDialog, explainer: explainerDialog, variants: variantsDialog, newReel: newReelDialog, diag: diagnosticsDialog, backups: backupsDialog, sample: async () => { status("Generating sample footage…"); await api.json("POST", "/api/projects/sample", {}); status("Sample project ready — press Space."); },
  beatMarkers: CR.beatMarkers, removeSilences: CR.removeSilences, timeTuner, remix: remixDialog, interpret: interpretDialog, extractAudio, breakout: breakoutAudio, rulers: () => { $("#prgScreen").classList.toggle("showrulers"); CR.renderGuides(); }, addGuideH: () => CR.addGuide("h"), addGuideV: () => CR.addGuide("v"), clearGuides: () => applyOps([{ op: "set", path: `/sequences/${S.proj.sequences.indexOf(S.seq)}/guides`, value: { h: [], v: [] } }], "guides", "clear guides"), snapGuides: () => { S.snapGuides = !S.snapGuides; status(S.snapGuides ? "Snap to guides: on" : "Snap to guides: off"); }, trimEdit: () => CR.ACTIONS.trim_edit[2](), trimType: CR.cycleTrimType, revMatch: CR.reverseMatchFrame, throughEdits: () => { S.tlopt.through = !S.tlopt.through; CR.renderTimeline(); }, distributeH: () => CR.distributeSel("h"), distributeV: () => CR.distributeSel("v"),
  cut: CR.cutSel, pasteInsert: CR.pasteInsert, removeAttr: removeAttrDialog, dupMedia: duplicateMedia, selectLabel: CR.selectMatchingLabel, find: CR.findInTimeline, removeUnused, renameClip, audioGain: gainDialog, replaceBin: replaceFromBin, renderReplace, sync: CR.synchronizeSel, merge: mergeDialog, flatten: CR.flattenMulticam, addEditAll: CR.addEditAllTracks, defTransSel: CR.defaultTransitionsToSelection, selFollow: () => CR.ACTIONS.sel_follow[2](), renderIO: renderInOut, autoReframe: reframeDialog, markClip: CR.markClip, markSel: CR.markClip, gotoIn: CR.gotoIn, gotoOut: CR.gotoOut, editMarker: () => editMarker(null), guides: () => { $("#prgScreen").classList.toggle("showguides"); }, alpha: () => { S.alphaMode = !S.alphaMode; CR.renderProgram(); status(S.alphaMode ? "Display: alpha" : "Display: composite"); }, preview: async () => { await CR.getRenderedPreview().toggle(); CR.renderProgram(); }, edl: () => window.open(`/api/export/edl?sequence=${S.seq.id}&track=${S.target.video}`), vtt: () => window.open(`/api/captions/export_vtt?sequence=${S.seq.id}`), chapters: () => window.open(`/api/markers/export?sequence=${S.seq.id}&fmt=chapters`),
  renderSeq: CR.renderEntireSequence, seqFromClip: CR.seqFromClip, multicam: multicamDialog, scenes: CR.sceneDetect, fitCover: () => CR.setFit("cover"), fitContain: () => CR.setFit("contain"), fitBlur: () => CR.setFit("blur_fill"), saveTemplate: saveGraphicTemplate, group: () => CR.groupSel(true), ungroup: () => CR.groupSel(false), prefs: prefsDialog, expandTracks: () => CR.setAllTall(true), minTracks: () => CR.setAllTall(false), newSeq: () => CR.newSequence(), undo: CR.undo, redo: CR.redo, copy: CR.copySel, paste: CR.pasteClips, del: () => CR.deleteSel(false), rippleDel: () => CR.deleteSel(true), selectAll: () => CR.ACTIONS.select_all[2](), deselect: () => CR.ACTIONS.deselect[2](), nest: CR.nestSelected, insert: () => CR.insertFromSource("insert"), overwrite: () => CR.insertFromSource("overwrite"), adjLayer: CR.addAdjustmentLayer, marker: CR.addMarker, prevMarker: () => CR.gotoMarker(-1), nextMarker: () => CR.gotoMarker(1), clearMarkers: () => applyOps([{ op: "set", path: `/sequences/${si}/markers`, value: [] }], "marker", "clear markers"), title: CR.addTitle, safe: () => CR.toggleSafe(), exact: () => CR.toggleExact(), proxies: () => { $("#prgRes").value = S.useProxy ? "full" : "proxy"; CR.setProxy(!S.useProxy); }, hideRight: () => showRight(false), api: () => window.open("/api/project", "_blank"), guide: () => window.open("/docs/USER_GUIDE.html", "_blank"), matrix: () => window.open("/docs/FEATURE_MATRIX.html", "_blank"), about: async () => { let v = "?"; try { v = (await api.get("/api/version")).version; } catch (e) { } alert(`Filmocity ${v} — the workstation's video editor.\nOpen project format, agent API (/api/docs), training data.\nNot affiliated with Adobe; Premiere Pro is a trademark of Adobe Inc.`); }, importPath: () => openDlg("#dlgPath"), upload: () => $("#fileInput").click(), save: () => snapshot("manual_save"), restore: restoreDialog, export: () => openDlg("#dlgExport"), frame: exportFrame,
  fcpxml: () => window.open(`/api/export/fcpxml?sequence=${S.seq.id}`), otio: () => window.open(`/api/export/otio?sequence=${S.seq.id}`), srt: () => window.open(`/api/captions/export?sequence=${S.seq.id}`),
  seqSettings: () => { window.FilmocitySequenceSettings.open(CR, document); openDlg("#dlgSeq"); }, addV: () => CR.addTrack("video"), addA: () => CR.addTrack("audio"), addEdit: CR.addEditAtPlayhead, defTrans: () => CR.applyTransition("dissolve", 1.0), speed: speedDialog, unlink: CR.toggleLink,
  shortcuts: () => CR.extras.keysDialog(), importXml: () => $("#xmlInput").click(), hold: CR.addFrameHold, extend: CR.extendEdit, replace: CR.replaceWithSource, fit: CR.zoomToFit, enable: CR.toggleEnabled, lift: () => CR.liftExtract(false), extract: () => CR.liftExtract(true), pasteAttr: CR.pasteAttributes, audioTrans: () => CR.applyAudioTransition(1.0),
  setIn: () => { S.focus = "program"; CR.markIO("in"); }, setOut: () => { S.focus = "program"; CR.markIO("out"); }, clearIO: CR.clearIO }; if (acts[act]) return acts[act](); }
function showTab(name) { $$("#tabs button").forEach(b => b.classList.toggle("on", b.dataset.pane === name)); $$(".pane").forEach(p => p.classList.toggle("on", p.id === "pane-" + name)); }
function bind() {
  $$(".tabs button[data-pane]").forEach(b => b.onclick = () => showTab(b.dataset.pane)); $$(".hd .pmenu").forEach(b => b.onclick = ev => { ev.stopPropagation(); panelMenu(ev, b.closest(".panel")); }); $$("[data-ws]").forEach(b => b.onclick = () => setWorkspace(b.dataset.ws)); $$("[data-win]").forEach(b => b.onclick = () => showTab(b.dataset.win)); $("#rightClose").onclick = () => showRight(false);
  $$(".modes button").forEach(b => b.onclick = () => { $$(".modes button").forEach(x => x.classList.toggle("on", x === b)); if (b.dataset.mode === "import") { showTab("project"); openDlg("#dlgPath"); } else if (b.dataset.mode === "export") openDlg("#dlgExport"); });
  api.get("/api/settings").then(st => { SETTINGS_CACHE = st || {}; try { if (st.panes) applyPaneLayout(st.panes); } catch (e) { console.warn("pane layout", e); } try { setWorkspace(st.workspace || "editing", false); } catch (e) { console.warn("workspace", e); } }).catch(() => { }); $$(".dd button[data-act]").forEach(b => b.onclick = () => menuAction(b.dataset.act)); $$(".dd button[data-gfx]").forEach(b => b.onclick = () => CR.addGraphic(b.dataset.gfx)); $$(".dd button[data-new]").forEach(b => b.onclick = () => newItem(b.dataset.new)); $$(".dd button[data-sfx]").forEach(b => b.onclick = async () => { const m = await api.json("POST", "/api/media/sfx", { kind: b.dataset.sfx, actor: "human" }); status(`${m.name} added to the bin (${m.duration.toFixed(2)} s) — drop it on an audio track at the cut`); }); $$(".dd button[data-align]").forEach(b => b.onclick = () => CR.alignSel(b.dataset.align)); loadFonts();
  $("#btnExport").onclick = () => openDlg("#dlgExport"); $("#exEncoderRefresh").onclick = loadEncoders; loadEncoders(); loadExportPresets();
  $("#exPreset").onchange = () => applyExportSettings(S.exportPresets[$("#exPreset").value]); $("#exPresetSave").onclick = async () => { const n = prompt("Preset name"); if (!n) return; const st = await api.get("/api/settings"); st.export_presets = { ...(st.export_presets || {}), [n]: { ...exportSettings(), outputs: $$("#exOutputs input:checked").map(i => i.dataset.o) } }; await api.json("PUT", "/api/settings", st); await loadExportPresets(); $("#exPreset").value = n; }; $("#exCancel").onclick = () => closeDlg("#dlgExport"); $("#exStart").onclick = doExport; $("#exCheck").onclick = refreshExportPreflight; $("#dlgExport").addEventListener("change", event => { if (event.target.id === "exFormat") { exportEncoderRequest = null; updateExportEncoders(); } if (event.target.id === "exEnc") { exportEncoderRequest = $("#exEnc").value; updateExportEncoders(exportEncoderRequest, true); } if (event.target.id !== "exName") refreshExportPreflight(); });
  $("#exShowCmd").onclick = async () => { const r = await api.get("/api/render_command?sequence=" + S.seq.id); $("#exOut").textContent = (r.cwd ? `Working directory: ${r.cwd}\n\n` : "") + r.cmd.map(x => (/\s/.test(x) ? `'${x}'` : x)).join(" "); };
  $("#btnSnapAgent").onclick = () => snapshot("agent_proposal"); $("#btnSnapHuman").onclick = () => snapshot("human_final");
  $("#sqCancel").onclick = () => closeDlg("#dlgSeq"); $("#sqSave").onclick = () => { if (window.FilmocitySequenceSettings.save(CR, document)) closeDlg("#dlgSeq"); };
  $("#pathCancel").onclick = () => closeDlg("#dlgPath"); $("#pathImport").onclick = async () => { const paths = $("#pathList").value.split("\n").map(s => s.trim()).filter(Boolean); const r = await api.json("POST", "/api/media/import", { paths, actor: "human" }); closeDlg("#dlgPath"); status(`Imported ${r.added ? r.added.length : 0} file(s)`); };
  $("#spCancel").onclick = () => closeDlg("#dlgSpeed"); $("#rsCancel").onclick = () => closeDlg("#dlgRestore");
}
function diffSequences(a, b) { const out = []; const ca = {}, cb = {}; for (const t of a.tracks) for (const c of t.clips) ca[c.id] = { c, t: t.id }; for (const t of b.tracks) for (const c of t.clips) cb[c.id] = { c, t: t.id }; const nm = c => c.name || (c.media_id && S.proj.media[c.media_id] ? S.proj.media[c.media_id].name : c.title ? "Text: " + (c.title.text || "") : c.graphic ? c.graphic.name : c.sequence_id ? "nested" : c.id);
  for (const id of Object.keys(cb)) if (!ca[id]) out.push({ kind: "added", clip: cb[id].c, track: cb[id].t, text: `+ ${nm(cb[id].c)} on ${cb[id].t} at ${fmtTC(cb[id].c.start, S.seq.fps)}` });
  for (const id of Object.keys(ca)) if (!cb[id]) out.push({ kind: "removed", clip: ca[id].c, track: ca[id].t, text: `− ${nm(ca[id].c)} from ${ca[id].t} (was at ${fmtTC(ca[id].c.start, S.seq.fps)})` });
  for (const id of Object.keys(ca)) if (cb[id]) { const x = ca[id].c, y = cb[id].c; const ch = []; for (const k of ["start", "in_", "out", "speed", "transform", "color", "fx_stack", "afx_stack", "audio", "keyframes", "transition_in", "transition_out", "title", "graphic", "enabled", "blend", "mask", "effects", "label", "note"]) if (JSON.stringify(x[k]) !== JSON.stringify(y[k])) ch.push(k === "start" ? `moved ${fmtTC(x.start, S.seq.fps)}→${fmtTC(y.start, S.seq.fps)}` : k === "out" || k === "in_" ? `trimmed (${k} ${(x[k] ?? 0).toFixed(2)}→${(y[k] ?? 0).toFixed(2)})` : k); if (ca[id].t !== cb[id].t) ch.push(`track ${ca[id].t}→${cb[id].t}`); if (ch.length) out.push({ kind: "changed", clip: y, track: cb[id].t, text: `~ ${nm(y)}: ${ch.join(", ")}` }); }
  if (JSON.stringify(a.captions || []) !== JSON.stringify(b.captions || [])) out.push({ kind: "changed", text: `~ captions (${(a.captions || []).length}→${(b.captions || []).length})` }); if (JSON.stringify(a.markers || []) !== JSON.stringify(b.markers || [])) out.push({ kind: "changed", text: "~ markers" }); return out; }
async function compareDialog() { const snaps = await api.get("/api/snapshots"); if (!snaps.length) { status("No snapshots yet — save one (Ctrl+S) or make an agent proposal."); return; } const pane = $("#pane-hist"); const sel = document.createElement("div"); sel.className = "grp"; sel.innerHTML = `<h4>Compare current cut to…<span class="sp"></span><button id="cmpClose">×</button></h4><select id="cmpSel">${snaps.slice().reverse().map(sn => `<option value="${escapeExportText(sn.file)}">${new Date((sn.ts || 0) * 1000).toLocaleString()} — ${escapeExportText(sn.label || sn.file)}</option>`).join("")}</select><div id="cmpOut" style="margin-top:6px;font-family:var(--mono);font-size:11px"></div>`; pane.insertBefore(sel, pane.firstChild);
  const run = async () => { const f = $("#cmpSel").value; const sn = await api.get("/api/snapshots/get?file=" + encodeURIComponent(f)); const old = (sn.project || sn).sequences.find(x => x.id === S.seq.id); if (!old) { $("#cmpOut").textContent = "snapshot has no matching sequence"; return; } const d = diffSequences(old, S.seq); $("#cmpOut").innerHTML = d.length ? d.map(x => `<div style="color:${x.kind === "added" ? "#9fe0b7" : x.kind === "removed" ? "#ffb3b3" : "#ddd"};cursor:${x.clip ? "pointer" : "default"}" data-cmp="${x.clip ? x.clip.id : ""}">${x.text}</div>`).join("") : "No differences."; $$("[data-cmp]", sel).forEach(el => el.onclick = () => { if (!el.dataset.cmp) return; const r = CR.clipById(el.dataset.cmp); if (r) { S.sel = new Set([r.c.id]); CR.refreshSel(); CR.seekTo(r.c.start); } }); }; $("#cmpSel").onchange = run; $("#cmpClose").onclick = () => sel.remove(); run(); }
CR.compareDialog = compareDialog;
function renderHist() { const pane = $("#pane-hist"); const labels = S.histLabels || []; pane.innerHTML = `<div class="grp"><h4>History<span class="sp"></span><span style="color:var(--dim);font-weight:400">${labels.length} states</span></h4><div class="hist">${labels.map((l, i) => `<div class="${i === S.hist_i ? "cur" : ""}" data-h="${i}">${String(i).padStart(3, "0")} · ${l}</div>`).reverse().join("")}</div></div>`; $$("[data-h]", pane).forEach(d => d.onclick = () => CR.gotoHist(+d.dataset.h)); }
function showRight(on) { const row = $("#rowTop"), columns = row.style.gridTemplateColumns.trim().split(/\s+/).filter(Boolean); if (on && columns.length === 2) row.style.gridTemplateColumns = columns.concat("340px").join(" "); else if (!on && columns.length === 3) row.style.gridTemplateColumns = columns.slice(0, 2).join(" "); $("#rightTop").style.display = on ? "" : "none"; row.classList.toggle("with-right", on); $("#rowBottom").classList.toggle("with-right", on); $("#rightBottom").style.display = "none"; CR.renderProgram(); }
function showTab(name) { const pane = $("#pane-" + name); if (!pane) return; if ($("#rightTop").contains(pane)) showRight(true); const bd = pane.parentElement; $$(":scope > .pane", bd).forEach(p => p.classList.toggle("on", p === pane)); const panel = bd.parentElement; $$(".tabs button[data-pane]", panel).forEach(b => b.classList.toggle("on", b.dataset.pane === name)); if (S.seq && ["events", "queue", "meta", "browser", "scopes", "ref", "markers", "info"].includes(name) && CR.panels) { if (name === "browser" && !pane.innerHTML) CR.panels.renderBrowser(""); else if (name !== "browser") CR.panels.render(); } }
const WORKSPACES = { editing: { top: "1fr 1fr", bottom: "460px 62px 1fr", rows: "24px 34px 1fr 6px 340px 20px", right: null, tab: "ec" }, color: { top: "0.8fr 1.4fr 360px", bottom: "400px 62px 1fr 360px", rows: "24px 34px 1.3fr 6px 300px 20px", right: "color" }, audio: { top: "1fr 1fr 360px", bottom: "400px 62px 1fr 360px", rows: "24px 34px 1fr 6px 380px 20px", right: "audio" }, graphics: { top: "0.7fr 1.6fr 340px", bottom: "400px 62px 1fr 340px", rows: "24px 34px 1.4fr 6px 300px 20px", right: "gfx" }, captions: { top: "0.8fr 1.4fr 380px", bottom: "400px 62px 1fr 380px", rows: "24px 34px 1.2fr 6px 320px 20px", right: "caps" } };
function applyPaneLayout(panes) { for (const [name, dock] of Object.entries(panes || {})) { const bar = document.querySelector(`.tabs[data-dock="${dock}"]`), btn = document.querySelector(`.tabs button[data-pane="${name}"]`), pane = document.querySelector(`#pane-${name}`); if (!bar || !btn || !pane || bar.contains(btn)) continue; bar.appendChild(btn); pane.classList.remove("on"); bar.closest(".panel").querySelector(":scope > .bd").appendChild(pane); } $$(".tabs[data-dock]").forEach(bar => { if (!$$("button.on", bar).length && bar.firstElementChild) showTab(bar.firstElementChild.dataset.pane); }); }
let SETTINGS_CACHE = {};
function setWorkspace(name, save = true) { const w = WORKSPACES[name]; if (!w) return; S.workspace = name; if (!S.seq) { $("#rowTop").style.gridTemplateColumns = ((SETTINGS_CACHE.layouts || {})[name] || {}).top || w.top; $("#rowBottom").style.gridTemplateColumns = ((SETTINGS_CACHE.layouts || {})[name] || {}).bottom || w.bottom; $("#app").style.gridTemplateRows = ((SETTINGS_CACHE.layouts || {})[name] || {}).rows || w.rows; $$("#wsbar button").forEach(b => b.classList.toggle("on", b.dataset.ws === name)); S.pendingWorkspace = name; return; } showRight(!!w.right); const saved = (SETTINGS_CACHE.layouts || {})[name] || {}; $("#rowTop").style.gridTemplateColumns = saved.top || w.top; $("#rowBottom").style.gridTemplateColumns = saved.bottom || w.bottom; $("#app").style.gridTemplateRows = saved.rows || w.rows; $$("#wsbar button").forEach(b => b.classList.toggle("on", b.dataset.ws === name)); if (w.right) showTab(w.right); if (w.tab) showTab(w.tab); CR.renderProgram(); if (save) { SETTINGS_CACHE.workspace = name; api.json("PUT", "/api/settings", { workspace: name }); } }
function saveLayout() { if (!S.workspace) return; SETTINGS_CACHE.layouts = { ...(SETTINGS_CACHE.layouts || {}), [S.workspace]: { top: $("#rowTop").style.gridTemplateColumns, bottom: $("#rowBottom").style.gridTemplateColumns, rows: $("#app").style.gridTemplateRows } }; clearTimeout(saveLayout._t); saveLayout._t = setTimeout(() => api.json("PUT", "/api/settings", { layouts: SETTINGS_CACHE.layouts }).catch(() => { }), 600); }
CR.saveLayout = saveLayout;
async function renderBrowser(path) {
  const pane = $("#pane-browser"); if (!pane.classList.contains("on") && path === undefined) return;
  path = path ?? (S.browserPath || "");
  const token = {}, project = S.proj, context = { ...S.context }, relink = CR.currentSourceRelink?.(); pane.browserOwner = token;
  const current = () => pane.browserOwner === token && S.proj === project && window.FilmocitySync.sameProject(context, S.context);
  let d;
  try { d = await api.get("/api/fs?path=" + encodeURIComponent(path)); }
  catch (error) { if (current()) pane.textContent = "Cannot read that folder: " + error.message; return; }
  if (!current() || relink !== CR.currentSourceRelink?.()) return;
  if (relink && !CR.sourceRelinkCurrent(relink)) { CR.cancelSourceRelink(relink, "The source or selection changed. Start Relink again."); return; }
  S.browserPath = d.path;
  const escape = escapeExportText;
  pane.innerHTML = `<div class="browser">${relink ? `<div id="fsRelinkIntent" role="status" aria-live="polite">Choose a replacement for ${escape(relink.name)}. The file will be inspected before you can Relink. <button id="fsCancelRelink">Cancel relink</button></div>` : ""}<div class="path"><button id="fsUp" ${d.parent ? "" : "disabled"} title="parent folder">↑</button><input id="fsPath" aria-label="Folder path" value="${escape(d.path)}"><select id="fsRoots" aria-label="File system root">${d.roots.map(r => `<option value="${escape(r)}">${escape(r)}</option>`).join("")}</select><button id="fsImportAll" ${relink ? "disabled" : ""} title="Import all media files in this folder">Import all</button></div><div class="list">${d.dirs.map(n => `<div class="fsrow dir" data-dir="${escape(n)}"><span>📁</span><span class="nm">${escape(n)}</span><span></span><span></span></div>`).join("")}${d.files.map(f => `<div class="fsrow ${f.imported ? "imported" : ""}" data-file="${escape(f.name)}" title="${relink ? "Inspect replacement original" : "Double-click to import"}"><span>${/\.(png|jpe?g|webp|tiff?|bmp|gif)$/i.test(f.name) ? "🖼" : /\.(wav|mp3|aac|m4a|flac|ogg)$/i.test(f.name) ? "♫" : "🎞"}</span><span class="nm">${escape(f.name)}${f.imported ? " ✓" : ""}</span><span class="meta">${(f.size / 1e6).toFixed(1)} MB</span><span class="meta">${new Date(f.mtime * 1000).toLocaleDateString()}</span></div>`).join("")}${!d.dirs.length && !d.files.length ? `<div class="empty">Empty folder (media files only are listed).</div>` : ""}</div></div>`;
  if (relink) { $("#fsRelinkIntent").relinkOwner = relink; $("#fsCancelRelink").onclick = () => { if (current()) { CR.cancelSourceRelink(relink); renderBrowser(d.path); } }; }
  const join = n => d.path.endsWith("/") || d.path.endsWith("\\") ? d.path + n : d.path + (d.path.includes("\\") ? "\\" : "/") + n;
  $("#fsUp").onclick = () => { if (current()) renderBrowser(d.parent); };
  $("#fsPath").onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); if (current()) renderBrowser(e.target.value).then(() => { if (S.proj === project && window.FilmocitySync.sameProject(context, S.context)) pane.querySelector(".fsrow[data-file]")?.focus(); }); } };
  $("#fsRoots").onchange = e => { if (current()) renderBrowser(e.target.value); };
  $$(".fsrow.dir", pane).forEach(row => row.ondblclick = () => { if (current()) return renderBrowser(join(row.dataset.dir)); });
  $$(".fsrow[data-file]", pane).forEach(row => row.ondblclick = async () => {
    if (!current() || !row.isConnected || relink !== CR.currentSourceRelink?.()) return false;
    if (relink) return relinkFromBrowser(relink, join(row.dataset.file));
    await api.json("POST", "/api/media/import", { paths: [join(row.dataset.file)], actor: "human" });
    if (current()) { status(`Imported ${row.dataset.file}`); renderBrowser(d.path); }
  });
  $$(".fsrow", pane).forEach(row => { row.setAttribute("role", "button"); row.tabIndex = 0; row.setAttribute("aria-label", row.dataset.dir ? "Open folder " + row.dataset.dir : (relink ? "Inspect replacement " : "Use file ") + row.dataset.file); row.onkeydown = event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); event.stopPropagation(); return row.ondblclick?.(); } }; });
  $("#fsImportAll").onclick = async () => {
    if (!current() || relink || CR.currentSourceRelink?.()) return;
    const paths = d.files.filter(f => !f.imported).map(f => join(f.name)); if (!paths.length) return;
    await api.json("POST", "/api/media/import", { paths, actor: "human" });
    if (current()) { status(`Imported ${paths.length} file(s)`); renderBrowser(d.path); }
  };
}
const REASONS = ["pacing", "story", "brand", "legal/claims", "audio", "color", "motion", "typography", "framing", "platform fit", "too long", "too short", "wrong take", "great moment"];
function reasonChips(id) { return `<div class="reasons" data-reasons="${escapeExportText(id)}">${REASONS.map(r => `<button data-r="${r}">${r}</button>`).join("")}</div>`; }
function wireReasons(root) { $$("[data-reasons] button", root).forEach(b => b.onclick = e => { e.preventDefault(); b.classList.toggle("on"); }); }
function pickedReasons(root, id) { return $$(`[data-reasons="${CSS.escape(id)}"] button.on`, root).map(b => b.dataset.r); }
function renderMarkersPane() { const pane = $("#pane-markers"); if (!pane) return; const si = S.proj.sequences.indexOf(S.seq); const ms = [...(S.seq.markers || [])].sort((a, b) => a.time - b.time);
  pane.innerHTML = `<div class="grp"><h4>Markers<span class="sp"></span><a href="/api/markers/export?sequence=${S.seq.id}&fmt=chapters" target="_blank"><button>Chapters</button></a><a href="/api/markers/export?sequence=${S.seq.id}&fmt=csv" target="_blank"><button>CSV</button></a><button id="mkAdd2">+ at playhead</button></h4>${ms.length ? ms.map((m, i) => `<div class="prop" data-mk="${i}" style="cursor:pointer;border-left:4px solid ${m.color || "green"}"><div style="display:flex;gap:8px;align-items:center"><b style="font-family:var(--mono)">${fmtTC(m.time, S.seq.fps)}</b>${m.duration ? `<span style="color:var(--dim)">+${fmtTC(m.duration, CR.mediaRate(m), 'ndf')}</span>` : ""}<span style="color:var(--dim)">${m.type || "comment"}</span>${m.author && m.author !== "human" ? `<span style="color:#9fe0b7;font-size:10.5px" title="left by the agent">${m.author}</span>` : ""}<span class="sp"></span>${m.type === "note" || m.author ? `<button class="tb" data-mkres="${i}" title="${m.resolved ? "reopen" : "mark resolved"}">${m.resolved ? "↺" : "✓"}</button>` : ""}<button class="tb" data-mkedit="${i}" title="edit">✎</button><button class="tb danger" data-mkdel="${i}" title="delete">×</button></div><div style="${m.resolved ? "text-decoration:line-through;color:var(--dim)" : ""}">${m.name || "<span style='color:var(--dim)'>(no name)</span>"}</div></div>`).join("") : `<div class="empty">No markers. Press <span class="kbd">M</span>.</div>`}</div>`;
  const save = (list, reason) => applyOps([{ op: "set", path: `/sequences/${si}/markers`, value: list }], "marker", reason);
  $$("[data-mkres]", pane).forEach(b => b.onclick = ev => { ev.stopPropagation(); const m = ms[+b.dataset.mkres]; save((S.seq.markers || []).map(x => x.id === m.id ? { ...x, resolved: !x.resolved } : x), x => "note " + (m.resolved ? "reopened" : "resolved")); });
  $("#mkAdd2").onclick = CR.addMarker; $$("[data-mk]", pane).forEach(el => el.onclick = e => { if (!e.target.closest("button")) CR.seekTo(ms[+el.dataset.mk].time); });
  $$("[data-mkdel]", pane).forEach(b => b.onclick = () => { const list = deep(ms); list.splice(+b.dataset.mkdel, 1); save(list, "delete marker"); }); $$("[data-mkedit]", pane).forEach(b => b.onclick = () => editMarker(ms[+b.dataset.mkedit])); }
function editMarker(m) { if (!m) { const ms = [...(S.seq.markers || [])].sort((a, b) => Math.abs(a.time - S.t) - Math.abs(b.time - S.t)); m = ms[0]; if (!m || Math.abs(m.time - S.t) > 0.5) { status("No marker near the playhead."); return; } } const name = prompt(`Marker at ${fmtTC(m.time, S.seq.fps)} — name / comment`, m.name || ""); if (name == null) return; const dur = prompt("Duration (seconds, 0 for a point marker)", String(m.duration || 0)); const si = S.proj.sequences.indexOf(S.seq); applyOps([{ op: "set", path: `/sequences/${si}/markers`, value: (S.seq.markers || []).map(x => x.id === m.id ? { ...x, name, duration: parseFloat(dur) || 0 } : x) }], "marker", "edit marker"); }
async function renderEvents() { const pane = $("#pane-events"); if (!pane || !pane.classList.contains("on")) return; let d; try { d = await api.get("/api/app_events?limit=60"); } catch (e) { return; }
  pane.innerHTML = `<div class="grp"><h4>Events<span class="sp"></span><span style="color:var(--dim);font-weight:400">${d.queued_renders} render(s) queued/running</span></h4>${d.offline_media.length ? `<div class="evrow err"><span class="t">now</span><span>offline</span><span>${d.offline_media.join(", ")} — relink from the Project panel</span></div>` : ""}${[...d.events].reverse().map(e => `<div class="evrow ${e.job && e.job.status === "error" ? "err" : ""}"><span class="t">${new Date(e.ts * 1000).toLocaleTimeString()}</span><span>${e.type}${e.actor ? " · " + e.actor : ""}</span><span>${e.job ? `${e.job.name || ""}: ${e.job.status}${e.job.qa && e.job.qa.flags && e.job.qa.flags.length ? " — " + e.job.qa.flags.join("; ") : ""}${e.job.error ? " — " + e.job.error.slice(-160) : ""}` : (e.reason || e.title || e.source || e.decision || (e.media ? e.media.length + " media" : ""))}</span></div>`).join("") || `<div class="empty">Nothing yet.</div>`}</div>`; }
function renderRefPane() { const pane = $("#pane-ref"); if (!pane) return; if (!pane.innerHTML) pane.innerHTML = `<div class="grp"><h4>Reference Monitor<span class="sp"></span><label style="color:var(--dim);font-weight:400"><input type="checkbox" id="refGang" checked> gang to playhead</label></h4><canvas id="refCanvas" style="width:100%;background:#000;border:1px solid var(--line2)"></canvas><div class="scrub" id="refScrub" style="margin-top:4px"><i></i></div><div style="font-family:var(--mono);color:var(--accent2);margin-top:4px" id="refTC">00:00:00:00</div></div>`;
  const cv = $("#refCanvas"); if (!cv) return; cv.width = S.seq.width; cv.height = S.seq.height; const gang = $("#refGang").checked; const t = gang ? S.t : (S.refT == null ? S.t : S.refT); CR.drawSequence(cv, S.seq, t, new Set(), 1); $("#refTC").textContent = fmtTC(t, S.seq.fps); $("#refScrub").firstElementChild.style.width = Math.min(100, t / Math.max(CR.seqDur(), 0.01) * 100) + "%";
  $("#refScrub").onmousedown = ev => { const bar = $("#refScrub"), r = bar.getBoundingClientRect(); const go = e => { $("#refGang").checked = false; S.refT = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * CR.seqDur(); renderRefPane(); }; go(ev); const up = () => { window.removeEventListener("mousemove", go); window.removeEventListener("mouseup", up); }; window.addEventListener("mousemove", go); window.addEventListener("mouseup", up); }; }
CR.renderRef = renderRefPane;
async function renderQueue() { const pane = $("#pane-queue"); if (!pane || !pane.classList.contains("on")) return; let jobs = []; try { jobs = await api.get("/api/jobs"); } catch (e) { return; } jobs = [...jobs].sort((a, b) => (b.started || 0) - (a.started || 0));
  pane.innerHTML = `<div class="grp"><h4>Render Queue<span class="sp"></span><span style="color:var(--dim);font-weight:400">${jobs.filter(j => j.status === "queued" || j.status === "running").length} active</span>${jobs.some(j => j.status === "done") ? `<button id="qManifest" title="Deliverables sheet: every finished render with its QA, as CSV">Manifest CSV</button>` : ""}</h4>${jobs.length ? jobs.map(j => { const qa = renderResultQA(j); return `<div class="prop" style="border-color:${j.status === "done" ? "var(--ok)" : j.status === "error" ? "var(--danger)" : "var(--line2)"}"><div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center"><b>${escapeExportText(j.name || j.id)}</b><span style="color:var(--dim)">${escapeExportText(j.status === "running" && j.resource?.state === "waiting" ? "Waiting for processing slot" : j.status)}${j.status === "running" && j.resource?.state !== "waiting" && j.progress != null ? ` ${Math.round(j.progress * 100)}%` : ""}${j.mode === "incremental" ? ` · ${escapeExportText(j.reused)}/${escapeExportText(j.segments)} segments from cache` : ""}</span><span class="sp"></span>${j.status === "done" ? renderResultLinks(j, true) : ""}${j.status === "queued" || j.status === "running" ? `<button class="danger" data-cancel="${escapeExportText(j.id)}">Cancel</button>` : ""}</div>${j.status === "running" && j.resource?.state !== "waiting" ? `<div class="prog"><i style="width:${Math.round((j.progress || 0) * 100)}%"></i></div>` : ""}${qa ? `<div style="font-family:var(--mono);font-size:10.5px;color:var(--dim)">${qa}</div>` : ""}${j.error ? `<div style="color:#ffb3b3;font-size:11px">${escapeExportText(j.error.slice(-200))}</div>` : ""}</div>`; }).join("") : `<div class="empty">No renders yet. Quick Export (Ctrl+M) queues one.</div>`}</div>`;
  $$("[data-cancel]", pane).forEach(b => b.onclick = async () => { await api.json("POST", `/api/render/${encodeURIComponent(b.dataset.cancel)}/cancel`, {}); renderQueue(); });
  const qm = $("#qManifest"); if (qm) qm.onclick = async () => { const rows = await api.get("/api/renders/manifest"); if (!rows.length) return; const cols = Object.keys(rows[0]); const csv = [cols.join(",")].concat(rows.map(r => cols.map(k => `"${String(r[k] == null ? "" : r[k]).replace(/"/g, '""')}"`).join(","))).join("\n"); const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" })); a.download = "deliverables.csv"; a.click(); }; if (jobs.some(j => j.status === "queued" || j.status === "running" || j.status === "cancelling")) setTimeout(renderQueue, 1200); }
CR.renderQueue = renderQueue;
function renderTimecodePane() { const pane = $("#pane-tc"); if (!pane) return; pane.innerHTML = `<div class="bigtc" id="bigTC">${fmtTC(S.t, S.seq.fps)}</div><div style="text-align:center;color:var(--dim)">${S.seq.name} · ${S.seq.fps} fps · duration ${fmtTC(CR.seqDur(), S.seq.fps)}</div>`; }
let meterCtx = null, meterAn = null, meterBuf = null, meterPeak = [0, 0], meterConnected = new Set();
function renderMetersPane() { const pane = $("#pane-meters"); if (!pane || pane.innerHTML) return; pane.innerHTML = `<div class="grp"><h4>Audio Meters<span class="sp"></span><span style="color:var(--dim);font-weight:400">peak hold · dBFS</span></h4><div class="meterbar"><div class="m" id="mL"><i style="height:0%"></i><b style="bottom:0"></b></div><div class="m" id="mR"><i style="height:0%"></i><b style="bottom:0"></b></div><div style="font-family:var(--mono);font-size:10px;color:var(--dim);display:flex;flex-direction:column;justify-content:space-between;height:100%"><span>0</span><span>-6</span><span>-12</span><span>-18</span><span>-24</span><span>-36</span><span>-48</span></div></div><div style="margin-top:6px;font-family:var(--mono)" id="mVal">L −∞  R −∞</div></div>`; }
function metersTick() { const pane = $("#pane-meters"); if (!pane || !pane.classList.contains("on") || !S.playing) return; try { const ctx = CR.audioCtx(); if (!ctx || !CR.AG.an) return; meterAn = CR.AG.an; if (!meterBuf) meterBuf = new Float32Array(512);
  const vals = meterAn.map((a, i) => { a.getFloatTimeDomainData(meterBuf); let pk = 0; for (let j = 0; j < meterBuf.length; j++) pk = Math.max(pk, Math.abs(meterBuf[j])); const db = 20 * Math.log10(pk || 1e-6); meterPeak[i] = Math.max(meterPeak[i] * 0.995, db); return db; }); const pct = db => Math.max(0, Math.min(100, (db + 48) / 48 * 100));
  ["mL", "mR"].forEach((id, i) => { const m = document.getElementById(id); if (!m) return; m.firstElementChild.style.height = pct(vals[i]) + "%"; m.lastElementChild.style.bottom = pct(meterPeak[i]) + "%"; }); const f = db => db < -47 ? "−∞" : db.toFixed(1); $("#mVal").textContent = `L ${f(vals[0])}  R ${f(vals[1])}`; } catch (e) { } }
let metadataRequest = 0;
async function renderMeta() { const ticket = ++metadataRequest, project = S.proj, expected = { ...S.context }; const pane = $("#pane-meta"); if (!pane || !pane.classList.contains("on")) return; const mid = [...S.binSel][0] || (S.src && S.src.id); const m = mid && S.proj.media[mid]; if (!m) { pane.innerHTML = `<div class="empty">Select a bin item to see its metadata.</div>`; return; }
  let pr = {}; try { pr = (await api.get("/api/media/probe?media_id=" + encodeURIComponent(mid))).probe || {}; } catch (e) { } if (ticket !== metadataRequest || !pane.classList.contains("on") || S.proj !== project || project.media[mid] !== m || S.context?.workspace !== expected.workspace || S.context?.project !== expected.project || ([...S.binSel][0] || (S.src && S.src.id)) !== mid) return; const fmt = pr.format || {}, vs = (pr.streams || []).find(x => x.codec_type === "video") || {}, as_ = (pr.streams || []).find(x => x.codec_type === "audio") || {}; const md = m.meta || {};
  const safe = value => String(value ?? "—").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const row = (k, v) => `<div class="fld" style="grid-template-columns:110px 1fr"><label>${k}</label><span style="font-family:var(--mono);font-size:11px;user-select:text">${safe(v)}</span></div>`; const ed = (k, ph) => `<div class="fld" style="grid-template-columns:110px 1fr"><label>${k}</label><input data-md="${k}" value="${safe(md[k] ?? "")}" placeholder="${ph}"></div>`;
  pane.innerHTML = `<div class="grp"><h4>${safe(m.name)}</h4>${row("File", m.path)}${row("Container", fmt.format_long_name || fmt.format_name)}${row("Duration", fmtTC(m.duration, CR.mediaRate(m), 'ndf'))}${row("Bitrate", fmt.bit_rate ? Math.round(fmt.bit_rate / 1000) + " kb/s" : null)}${row("Size", fmt.size ? (fmt.size / 1e6).toFixed(1) + " MB" : null)}${row("Video", vs.codec_long_name ? `${vs.codec_long_name} · ${vs.width}×${vs.height} · ${vs.r_frame_rate} · ${vs.pix_fmt}${vs.color_space ? " · " + vs.color_space : ""}` : null)}${row("Audio", as_.codec_long_name ? `${as_.codec_long_name} · ${as_.sample_rate} Hz · ${as_.channel_layout || as_.channels + " ch"}` : null)}${row("Created", fmt.tags && fmt.tags.creation_time)}${row("Interpreted fps", m.interpret_fps || "native")}</div>
    <div id="mediaColorHost"></div>
    <div class="grp"><h4>Clip metadata<span class="sp"></span><span style="color:var(--dim);font-weight:400">searchable · exported with training data</span></h4>${ed("start_tc", "start timecode 00:00:00:00 (used by the Timecode effect)")}${ed("description", "what this shot is")}${ed("log_note", "log note")}${ed("scene", "scene")}${ed("shot", "shot / take")}${ed("keywords", "comma-separated tags")}${ed("good", "good take? yes / no")}</div>`;
  window.FilmocityMediaColor.mount(CR, $("#mediaColorHost"), mid);
  $$("[data-md]", pane).forEach(inp => inp.onchange = () => applyOps([{ op: "set", path: `/media/${mid}/meta`, value: { ...(m.meta || {}), [inp.dataset.md]: inp.value } }], "metadata", `metadata ${inp.dataset.md}`)); }
function renderInfo() { const pane = $("#pane-info"); const sel = CR.selectedClips(); const m = S.src; const nClips = S.seq.tracks.reduce((n, t) => n + t.clips.length, 0);
  const br = S.proj.brief || {}; const bf = (k, ph) => `<div class="fld" style="grid-template-columns:96px 1fr"><label>${k}</label><input data-brief="${k}" value="${(br[k] || "").replace(/"/g, "&quot;")}" placeholder="${ph}"></div>`;
  pane.innerHTML = `<div class="grp"><h4>Brief<span class="sp"></span><span style="color:var(--dim);font-weight:400">shared with the agent · attached to training data</span></h4>${bf("client", "who this is for")}${bf("objective", "what the piece must achieve")}${bf("platform", "reels / youtube / ctv …")}${bf("audience", "who watches")}${bf("constraints", "claims, legal, brand rules")}${bf("notes", "anything else")}</div><div class="grp"><h4>Sequence</h4><div style="font-family:var(--mono);font-size:11px;color:var(--dim)">${S.seq.name} · ${S.seq.width}×${S.seq.height} @ ${S.seq.fps} fps · ${fmtTC(CR.seqDur(), S.seq.fps)} · ${nClips} clips · ${S.seq.tracks.length} tracks</div></div>
    ${sel.length === 1 ? `<div class="grp"><h4>Selected clip</h4><div style="font-family:var(--mono);font-size:11px;color:var(--dim);white-space:pre-wrap">${Object.entries(sel[0].c).filter(([k]) => !["keyframes"].includes(k)).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join("\n")}</div></div>` : ""}
    ${m ? `<div class="grp"><h4>Source media</h4><div style="font-family:var(--mono);font-size:11px;color:var(--dim);white-space:pre-wrap">${m.name}\n${m.path}\n${m.width}×${m.height} · ${m.fps ? m.fps.toFixed(3) + " fps" : "still"} · ${fmtTC(m.duration, CR.mediaRate(m), 'ndf')} · ${m.codec || ""}${m.has_audio ? " · audio" : ""}</div></div>` : ""}
    <div class="grp"><h4>Cut summary<span class="sp"></span><button id="cutCopy" title="copy as markdown">Copy</button></h4><pre id="cutSummary" role="status" aria-live="polite" style="white-space:pre-wrap;font:11px var(--mono);color:var(--dim);margin:0;max-height:220px;overflow:auto;user-select:text">…</pre></div>
    <div class="grp"><h4>Brand kit<span class="sp"></span><span style="color:var(--dim);font-weight:400">premium templates and caption presets use these</span></h4>${[["primary", "Primary", "#E8631C"], ["secondary", "Secondary", "#7A2E9E"], ["text", "Text", "#FFFFFF"]].map(([k, n, d]) => `<div class="fld"><label>${n}</label><input type="color" data-brand="${k}" value="${(S.proj.brand || {})[k] || d}"></div>`).join("")}<div class="fld"><label>Font</label><div class="pair"><select data-brand="font"><option value="">DejaVu Sans (bundled)</option>${(CR.fonts || []).map(f => `<option ${(S.proj.brand || {}).font === f ? "selected" : ""}>${f}</option>`).join("")}</select><button class="tb" data-fontadd="1" title="Add your own fonts">+</button></div></div></div>
    <div class="grp"><h4>Learned preferences<span class="sp"></span><button id="advTrain">Retrain</button></h4><div id="advReport" style="font-family:var(--mono);font-size:10.5px;color:var(--dim)">loading…</div></div>
    <div class="grp"><h4>Agent</h4><div style="color:var(--dim)">Live over WebSocket. Proposals, snapshots, events and QA are described in docs/AGENT_API.md. <button id="trainExport" style="margin-left:6px">Export training dataset</button> <span id="trainOut" style="font-family:var(--mono);font-size:10.5px"></span></div></div>`;
  $$("[data-brand]", pane).forEach(inp => inp.onchange = () => applyOps([{ op: "set", path: "/brand", value: { ...(S.proj.brand || {}), [inp.dataset.brand]: inp.value } }], "brand", `brand ${inp.dataset.brand}`)); $$("[data-fontadd]", pane).forEach(b => b.onclick = uploadFontDialog);
  $$("[data-brief]", pane).forEach(inp => inp.onchange = () => applyOps([{ op: "set", path: "/brief", value: { ...(S.proj.brief || {}), [inp.dataset.brief]: inp.value } }], "brief", `brief ${inp.dataset.brief}`)); $("#trainExport").onclick = async () => { const r = await api.get("/api/training/export"); $("#trainOut").textContent = `${r.records} records → ${r.file}`; };
  const showRep = rep => { const el = $("#advReport"); if (!el) return; el.innerHTML = `${rep.examples} decision(s)${rep.model_examples ? ` · model trained on ${rep.model_examples}` : " · model not trained yet"}<br>` + (rep.rules && rep.rules.length ? rep.rules.slice(0, 12).map(r => `<div>${r.pattern.replace("op:set_clip|", "edit: ").replace("op:remove_clip", "remove clip").replace("reason_code:", "reason ")} — <b style="color:${r.acceptance >= 0.6 ? "#9fe0b7" : r.acceptance <= 0.4 ? "#ffb3b3" : "#ddd"}">${Math.round(r.acceptance * 100)}%</b> accepted (${r.n})</div>`).join("") : "No decisions yet — accept or reject agent proposals with reason codes and patterns will appear here."); };
  renderCutSummary(); const cc = $("#cutCopy"); if (cc) cc.onclick = () => { navigator.clipboard && navigator.clipboard.writeText($("#cutSummary").textContent); status("Cut summary copied"); };
  api.get("/api/advisor/report").then(showRep).catch(() => { const el = $("#advReport"); if (el) el.textContent = "advisor unavailable"; }); $("#advTrain").onclick = async () => { const r = await api.json("POST", "/api/advisor/train", {}); showRep({ examples: r.examples, model_examples: r.examples, rules: r.rules }); status(`Advisor retrained on ${r.examples} decisions`); }; }
function renderMixer() { const pane = $("#pane-mixer"); const act = S.seq.tracks.filter(t => !t.muted).flatMap(tr => tr.clips.filter(c => c.media_id && S.proj.media[c.media_id] && S.proj.media[c.media_id].has_audio && S.t >= c.start && S.t < CR.clipEnd(c) && (tr.kind === "audio" || (c.audio || {}).linked !== false)).map(c => ({ c, tr })));
  pane.innerHTML = `<div class="grp"><h4>Audio Clip Mixer<span class="sp"></span><span style="color:var(--dim);font-weight:400">clips under the playhead</span></h4>${act.length ? `<div class="mixer">${act.map(({ c, tr }) => `<div class="ch"><span class="nm">${tr.id}</span><input type="range" data-cg="${c.id}" min="-40" max="12" step="0.5" value="${(c.audio || {}).gain_db || 0}"><span class="db">${((c.audio || {}).gain_db || 0).toFixed(1)} dB</span><span class="db" title="${(S.proj.media[c.media_id] || {}).name}">${((S.proj.media[c.media_id] || {}).name || "").slice(0, 8)}</span></div>`).join("")}</div>` : `<div class="empty">No audio clips under the playhead.</div>`}</div>`;
  $$("[data-cg]", pane).forEach(inp => { inp.oninput = () => { inp.nextElementSibling.textContent = parseFloat(inp.value).toFixed(1) + " dB"; }; inp.onchange = () => { const r = CR.clipById(inp.dataset.cg); if (r) applyOps([{ op: "set_clip", sequence: S.seq.id, track: r.tr.id, clip: { id: r.c.id, audio: { ...(r.c.audio || {}), gain_db: parseFloat(inp.value) } } }], "clip_mixer", "clip gain"); }; }); }
const ANIMS_IN = [["none", "None"], ["fade", "Fade"], ["rise", "Rise (fade + up)"], ["drop", "Drop (fade + down)"], ["slide_left", "Slide from left"], ["slide_right", "Slide from right"], ["slide_up", "Slide from below"], ["slide_down", "Slide from above"], ["pop", "Pop"], ["zoom", "Zoom in"], ["wipe_left", "Wipe →"], ["wipe_right", "Wipe ←"], ["wipe_up", "Wipe ↑"], ["wipe_down", "Wipe ↓"], ["typewriter", "Typewriter (text)"], ["rotate_in", "Rotate in"]];
const ANIMS_OUT = ANIMS_IN.filter(([v]) => v !== "typewriter" && v !== "rotate_in").map(([v, n]) => [v, n.replace("from", "to").replace("in", "out").replace("Rise (fade + up)", "Rise out").replace("Drop (fade + down)", "Drop out")]);
const EASES = [["ease_out", "Ease out"], ["ease_in", "Ease in"], ["ease_in_out", "Ease in-out"], ["linear", "Linear"], ["back_out", "Overshoot"], ["bounce", "Bounce"]];
function animatorHtml(c, tr) { const layers = c.graphic ? c.graphic.layers : [{ ...c.title, kind: "text" }]; const sel = (name, opts, v) => `<select data-la="${name}">${opts.map(([o, n]) => `<option value="${o}" ${(v || "none") === o ? "selected" : ""}>${n}</option>`).join("")}</select>`;
  return `<div class="grp"><h4>Layer Animator<span class="sp"></span><span style="color:var(--dim);font-weight:400">${c.graphic ? c.graphic.name || "graphic" : "text"} · ${layers.length} layer(s)</span></h4>${!c.graphic ? `<p style="color:var(--dim);margin:0 0 6px">This is a single text clip. <button id="laConvert">Convert to layered graphic</button> to animate layers.</p>` : ""}
    ${layers.map((L, i) => { const ai = L.anim_in || {}, ao = L.anim_out || {}; return `<div class="prop" style="margin:4px 0"><div style="display:flex;gap:8px;align-items:center"><b>${i + 1}. ${L.kind === "text" ? "Text: " + (L.text || "").slice(0, 26) : L.kind === "shape" ? "Shape: " + (L.shape || "rect") : "Box"}</b><span class="sp"></span>${L.kind === "text" && c.graphic ? `<button class="tb" data-lasplit="${i}" title="Split this text layer into words; replace its In animation with a 0.35 s pop and 0.12 s stagger" aria-label="Split layer ${i + 1} into words" ${tr.locked ? "disabled" : ""}>Split words</button>` : ""}</div>
      <div class="fld" style="grid-template-columns:22px 60px 1fr 1fr 1fr 1fr"><span></span><label>In</label>${c.graphic ? sel(`${i}.anim_in.type`, ANIMS_IN, ai.type) : `<span style="color:var(--dim)">convert first</span>`}<input type="number" data-la="${i}.anim_in.duration" step="0.05" min="0.05" value="${ai.duration == null ? 0.6 : ai.duration}" title="duration s"><input type="number" data-la="${i}.anim_in.delay" step="0.05" min="0" value="${ai.delay || 0}" title="delay s">${sel(`${i}.anim_in.ease`, EASES, ai.ease || "ease_out")}</div>
      <div class="fld" style="grid-template-columns:22px 60px 1fr 1fr 1fr 1fr"><span></span><label>Out</label>${c.graphic ? sel(`${i}.anim_out.type`, ANIMS_OUT, ao.type) : ""}<input type="number" data-la="${i}.anim_out.duration" step="0.05" min="0.05" value="${ao.duration == null ? 0.5 : ao.duration}" title="duration s"><input type="number" data-la="${i}.anim_out.delay" step="0.05" min="0" value="${ao.delay || 0}" title="delay before end s">${sel(`${i}.anim_out.ease`, EASES, ao.ease || "ease_out")}</div>
      <div class="fld" style="grid-template-columns:22px 60px 1fr 1fr 1fr 1fr"><span></span><label>Keys</label><input type="number" data-lk="${i}.x" step="1" value="${Math.round(CR.kfVal((c.keyframes || {})[`g${i}.x`], S.t - c.start, 0))}" title="position x offset (px) at the playhead — Enter adds/updates a keyframe"><input type="number" data-lk="${i}.y" step="1" value="${Math.round(CR.kfVal((c.keyframes || {})[`g${i}.y`], S.t - c.start, 0))}" title="position y offset (px) at the playhead"><input type="number" data-lk="${i}.scale" step="0.05" value="${(+CR.kfVal((c.keyframes || {})[`g${i}.scale`], S.t - c.start, 1)).toFixed(2)}" title="scale keyframe at the playhead"><input type="number" data-lk="${i}.opacity" step="0.05" min="0" max="1" value="${(+CR.kfVal((c.keyframes || {})[`g${i}.opacity`], S.t - c.start, 1)).toFixed(2)}" title="opacity keyframe at the playhead">${Object.keys(c.keyframes || {}).some(k => k.startsWith(`g${i}.`)) ? `<button class="tb" data-lkclear="${i}" title="clear this layer's keyframes">✕</button>` : ""}</div>
      ${L.kind === "shape" ? `<div class="fld" style="grid-template-columns:22px 60px 1fr 1fr 1fr 1fr"><span></span><label>Fill</label><input type="color" data-la="${i}.color" value="${(L.color || "#ffffff").startsWith("#") ? L.color : "#ffffff"}" title="colour"><input type="color" data-la="${i}.gradient.to" value="${(L.gradient && L.gradient.to) || "#000000"}" title="gradient end colour"><input type="number" data-la="${i}.gradient.angle" step="5" value="${(L.gradient && L.gradient.angle) || 0}" title="gradient angle °"><label style="color:var(--dim)"><input type="checkbox" data-la="${i}.shadow" ${L.shadow ? "checked" : ""}> shadow</label></div>` : ""}
      <div class="fld" style="grid-template-columns:22px 60px 1fr 1fr 1fr 1fr"><span></span><label>Layer</label><input type="number" data-la="${i}.opacity" step="0.05" min="0" max="1" value="${L.opacity == null ? 1 : L.opacity}" title="opacity"><input type="number" data-la="${i}.scale" step="0.05" min="0.05" value="${L.scale == null ? 1 : L.scale}" title="scale"><input type="number" data-la="${i}.rotation" step="1" value="${L.rotation || 0}" title="rotation °">${L.kind === "text" ? `<label style="color:var(--dim)"><input type="checkbox" data-la="${i}.glow" ${L.glow ? "checked" : ""}> glow</label>` : `<input type="number" data-la="${i}.blur" step="1" min="0" value="${L.blur || 0}" title="blur px">`}</div></div>`; }).join("")}
    ${c.graphic ? `<div class="lbls" style="margin-top:6px"><button id="laImage" title="Add the selected bin still (logo) as an image layer">+ Image layer from bin</button><button id="laShape" title="Add a gradient card shape layer">+ Card</button><button id="laCascade" title="Stagger every layer's In delay by 0.15 s in order">Cascade in</button><button id="laMirror" title="Give every layer an Out animation matching its In">Mirror outs</button><button id="laPreview" title="Play the graphic from its start">▶ Preview</button></div>` : ""}</div>`; }
function wireAnimator(pane, c, tr) { const save = (g, reason) => applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, graphic: g } }], "animate", reason);
  $$("[data-la]", pane).forEach(inp => inp.onchange = () => { if (!c.graphic) return; const [li, ...path] = inp.dataset.la.split("."); const g = deep(c.graphic); let o = g.layers[+li]; for (const k of path.slice(0, -1)) { o[k] = o[k] || {}; o = o[k]; } const last = path[path.length - 1]; o[last] = inp.type === "checkbox" ? inp.checked : (inp.tagName === "SELECT" || inp.type === "color") ? inp.value : parseFloat(inp.value); if (last === "type" && o[last] === "none") delete g.layers[+li][path[0]]; save(g, `layer ${+li + 1} ${path.join(".")}`); });
  $$("[data-lk]", pane).forEach(inp => inp.onkeydown = e => { if (e.key !== "Enter") return; const [li, prop] = inp.dataset.lk.split("."); const key = `g${li}.${prop}`; const rel = +(S.t - c.start).toFixed(4); const list = [...((c.keyframes || {})[key] || [])]; const v = parseFloat(inp.value); const j = list.findIndex(k => Math.abs(k.t - rel) < CR.frame() / 2); if (j >= 0) list[j] = { ...list[j], v }; else list.push({ t: rel, v, e: "ease" }); applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, keyframes: { ...(c.keyframes || {}), [key]: list.sort((a, b) => a.t - b.t) } } }], "keyframe", `layer ${+li + 1} ${prop} keyframe`); });
  $$("[data-lkclear]", pane).forEach(b => b.onclick = () => { const li = b.dataset.lkclear; const kf = { ...(c.keyframes || {}) }; for (const k of Object.keys(kf)) if (k.startsWith(`g${li}.`)) delete kf[k]; applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, keyframes: kf } }], "keyframe", `clear layer ${+li + 1} keyframes`); });
  const li_ = $("#laImage"); if (li_) li_.onclick = () => { const mid = [...S.binSel][0]; const m = mid && S.proj.media[mid]; if (!m || !m.is_image) { status("Select a still (PNG/JPG logo) in the Project panel first."); return; } const g = deep(c.graphic); g.layers.push({ kind: "image", path: m.path, x: 0.1, y: 0.1, w: 0.3, h: 0.15, fit: "contain", opacity: 1, anim_in: { type: "pop", duration: 0.5, ease: "back_out" } }); save(g, "image layer"); };
  const ls_ = $("#laShape"); if (ls_) ls_.onclick = () => { const g = deep(c.graphic); g.layers.unshift({ kind: "shape", shape: "rect", x: 0.08, y: 0.62, w: 0.84, h: 0.16, radius: 28, color: "#E8631C", gradient: { to: "#7A2E9E", angle: 20 }, shadow: true, opacity: 0.95, anim_in: { type: "rise", duration: 0.5 } }); save(g, "card layer"); };
  const cv = $("#laConvert"); if (cv) cv.onclick = () => applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, title: null, graphic: { name: (c.title.text || "Text").slice(0, 24), layers: [{ kind: "text", ...c.title }] } } }], "convert", "convert text to graphic");
  const cas = $("#laCascade"); if (cas) cas.onclick = () => { const g = deep(c.graphic); g.layers.forEach((L, i) => { L.anim_in = { type: (L.anim_in || {}).type || "rise", duration: (L.anim_in || {}).duration || 0.5, ease: (L.anim_in || {}).ease || "ease_out", ...(L.anim_in || {}), delay: +(i * 0.15).toFixed(2) }; }); save(g, "cascade in"); };
  const mir = $("#laMirror"); if (mir) mir.onclick = () => { const g = deep(c.graphic); const inv = { slide_left: "slide_left", slide_right: "slide_right", slide_up: "slide_up", slide_down: "slide_down", rise: "rise", drop: "drop", pop: "pop", zoom: "zoom", fade: "fade", typewriter: "fade", rotate_in: "fade", wipe_left: "wipe_right", wipe_right: "wipe_left", wipe_up: "wipe_down", wipe_down: "wipe_up" }; g.layers.forEach(L => { const t = (L.anim_in || {}).type; if (t && t !== "none") L.anim_out = { type: inv[t] || "fade", duration: (L.anim_in || {}).duration || 0.5, ease: (L.anim_in || {}).ease || "ease_out", delay: 0 }; }); save(g, "mirror outs"); };
  const pv = $("#laPreview"); if (pv) pv.onclick = () => { CR.seekTo(c.start); S.stopAt = CR.clipEnd(c); CR.togglePlay(true); };
  const splitProject = S.proj, splitSequence = S.seq;
  $$("[data-lasplit]", pane).forEach(b => {
    const token = {}; b.splitOwner = token;
    const current = () => b.isConnected !== false && b.splitOwner === token && pane.contains(b) && S.proj === splitProject && S.seq === splitSequence;
    b.onclick = async () => {
      if (b.disabled || !current()) return false;
      b.disabled = true; b.setAttribute("aria-busy", "true"); b.textContent = "Splitting…";
      try { return await CR.splitGraphicWords(c, tr, +b.dataset.lasplit, { current }); }
      finally { if (current()) { b.disabled = !!tr.locked; b.removeAttribute("aria-busy"); b.textContent = "Split words"; } }
    };
  }); }
function renderGfx() { const pane = $("#pane-gfx"); const selG = CR.selectedClips().find(x => x.c.graphic || x.c.title); pane.innerHTML = (selG ? animatorHtml(selG.c, selG.tr) : `<div class="grp"><h4>Layer Animator</h4><p style="color:var(--dim);margin:0">Select a graphic or text clip to animate its layers: in/out motion per layer, easing, delays, cascade, kinetic word splits.</p></div>`) + `<div class="grp"><h4>Text</h4><div class="lbls"><button id="gfxTitle">New Text Layer (Ctrl+T)</button></div><p style="color:var(--dim);margin:6px 0 0">Or pick the Type tool (T) and click in the Program Monitor.</p></div>
    <div class="grp"><h4>Templates</h4>${[["lower_third", "Lower third", "name + role bar, pushes in"], ["card", "Card", "centered headline + support line"], ["tag", "Tag", "small accent tag"]].map(([k, n, d]) => `<div class="fxi" data-gfx="${k}" style="display:flex;justify-content:space-between;padding:6px 8px;border:1px solid var(--line2);border-radius:4px;margin:4px 0;cursor:pointer"><b>${n}</b><span style="color:var(--dim)">${d}</span></div>`).join("")}
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:8px">${Object.entries(S.gfxTemplates || {}).sort((a, b) => (b[1].premium ? 1 : 0) - (a[1].premium ? 1 : 0)).map(([n, g]) => `<div class="fxi" data-utpl="${n}" title="${n} — click to insert at the playhead${g.premium ? " (premium, uses your brand kit)" : ""}" style="border:1px solid ${g.premium ? "var(--accent)" : "var(--line2)"};border-radius:6px;overflow:hidden;cursor:pointer;background:#1a1a1e"><img loading="lazy" src="/api/templates/preview?name=${encodeURIComponent(n)}&w=240&v=${encodeURIComponent(JSON.stringify(S.proj.brand || {}))}" style="width:100%;display:block;aspect-ratio:9/16;object-fit:cover" onerror="this.style.display='none'"><div style="padding:4px 6px;font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${g.premium ? "★ " : ""}${n}</div></div>`).join("")}</div><p style="color:var(--dim);margin:6px 0 0">Bundled library + your saved templates (Clip → Save Graphic as Template).</p></div>
    <div class="grp"><h4>Fonts available</h4><div style="color:var(--dim);font-size:11px;max-height:140px;overflow:auto">${(CR.fonts || []).slice(0, 120).join(" · ") || "loading…"}</div></div>`;
  $$("[data-fontadd]", pane).forEach(b => b.onclick = uploadFontDialog); if (selG) wireAnimator(pane, selG.c, selG.tr); $("#gfxTitle").onclick = CR.addTitle; $$("[data-gfx]", pane).forEach(el => el.onclick = () => CR.addGraphic(el.dataset.gfx)); $$("[data-utpl]", pane).forEach(el => el.onclick = () => { const g = CR.applyBrand(deep(S.gfxTemplates[el.dataset.utpl])); const tr = S.seq.tracks.filter(x => x.kind === "video").sort((a, b) => b.index - a.index)[0]; const c = { id: CR.uid(), media_id: null, start: S.t, in_: 0, out: 4.0, speed: 1, graphic: g, transform: { opacity: 1 }, transition_in: { type: "fade", duration: 0.3 }, transition_out: { type: "fade", duration: 0.3 }, keyframes: {} }; applyOps([{ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: c }], "graphic", `template ${el.dataset.utpl}`); }); if (!S.gfxTemplates) api.get("/api/templates").then(t => { S.gfxTemplates = t || {}; renderGfx(); }); }
const FX_TREE = [["Video Transitions", [["Cross Dissolve", () => CR.applyTransition("dissolve", 1.0), "Ctrl+D"], ["Dip to Black", () => CR.applyTransition("dip_black", 1.0)], ["Dip to White", () => CR.applyTransition("dip_white", 1.0)], ["Wipe (left)", () => CR.applyTransition("wipe_left", 1.0)], ["Wipe (right)", () => CR.applyTransition("wipe_right", 1.0)], ["Push (left)", () => CR.applyTransition("push_left", 0.6)], ["Push (right)", () => CR.applyTransition("push_right", 0.6)], ["Slide (left)", () => CR.applyTransition("slide_left", 0.6)], ["Slide (up)", () => CR.applyTransition("slide_up", 0.6)], ["Iris Round (open)", () => CR.applyTransition("iris", 0.8)], ["Iris Round (close)", () => CR.applyTransition("iris_close", 0.8)], ["Iris Diamond / Diagonal", () => CR.applyTransition("diagonal_tl", 0.8)], ["Barn Doors (horizontal)", () => CR.applyTransition("barn_h", 0.8)], ["Barn Doors (vertical)", () => CR.applyTransition("barn_v", 0.8)], ["Clock Wipe", () => CR.applyTransition("clock", 1.0)], ["Checker Wipe", () => CR.applyTransition("checker", 0.8)], ["Cross Zoom", () => CR.applyTransition("cross_zoom", 0.7)], ["Wipe (up)", () => CR.applyTransition("wipe_up", 0.8)], ["Wipe (down)", () => CR.applyTransition("wipe_down", 0.8)]]],
  ["Audio Transitions", [["Constant Power", () => CR.applyAudioTransition(1.0), "Ctrl+Shift+D"], ["Constant Gain", () => CR.applyAudioTransition(1.0, "constant_gain")], ["Exponential Fade", () => CR.applyAudioTransition(1.0, "exponential")]]],
  ["Video Effects", [["Color Correction", () => { showTab("color"); }], ["Mask (rectangle)", () => setSel(c => ({ mask: { type: "rect", x: 0.1, y: 0.1, w: 0.8, h: 0.8, feather: 20, invert: false } }), "mask")], ["Mask (ellipse)", () => setSel(c => ({ mask: { type: "ellipse", x: 0.1, y: 0.1, w: 0.8, h: 0.8, feather: 20, invert: false } }), "mask")], ["Blend: Multiply", () => setSel(c => ({ blend: "multiply" }), "blend")], ["Blend: Screen", () => setSel(c => ({ blend: "screen" }), "blend")], ["Time Remapping", () => { showTab("ec"); }], ["Frame Hold", () => CR.addFrameHold(), "Ctrl+Shift+H"]]],
  ["Audio Effects", [["Parametric EQ (+3 dB low)", () => setSel(c => ({ audio_fx: { ...(c.audio_fx || {}), eq: { low_db: 3, mid_db: 0, high_db: 0 } } }), "audio_fx")], ["Compressor", () => setSel(c => ({ audio_fx: { ...(c.audio_fx || {}), comp: { enabled: true, threshold_db: -18, ratio: 3, attack_ms: 20, release_ms: 200, makeup_db: 2 } } }), "audio_fx")], ["DeNoise", () => setSel(c => ({ audio_fx: { ...(c.audio_fx || {}), denoise: { enabled: true, db: 12 } } }), "audio_fx")], ["Hard Limiter", () => setSel(c => ({ audio_fx: { ...(c.audio_fx || {}), limiter: true } }), "audio_fx")]]],
  ["Graphics", [["Lower Third", () => CR.addGraphic("lower_third"), "Ctrl+Shift+L"], ["Card", () => CR.addGraphic("card")], ["Tag", () => CR.addGraphic("tag")]]]];
function setSel(fn, tool) { const sel = CR.selectedClips(); if (!sel.length) { status("Select a clip first."); return; } applyOps(sel.map(({ c, tr }) => ({ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, ...fn(c) } })), tool, `apply ${tool}`); }
async function loadCatalog() { if (CR.catalog) return CR.catalog; try { CR.catalog = await api.get("/api/effects"); } catch (e) { CR.catalog = { video: [], audio: [] }; } return CR.catalog; }
function addStackFx(kind, type) { const sel = CR.selectedClips().filter(x => kind === "video" ? (x.tr.kind === "video" && (x.c.media_id || x.c.sequence_id || x.c.title || x.c.graphic)) : (x.c.media_id && S.proj.media[x.c.media_id].has_audio)); if (!sel.length) { status("Select a clip first."); return; } const key = kind === "video" ? "fx_stack" : "afx_stack"; const cat = (CR.catalog || {})[kind] || []; const spec = cat.find(e => e.type === type); const params = Object.fromEntries(Object.entries((spec || {}).params || {}).map(([k, v]) => [k, v.default]));
  applyOps(sel.map(({ c, tr }) => ({ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, [key]: [...(c[key] || []), { id: CR.uid(), type, enabled: true, params }] } })), "effect_add", `add ${spec ? spec.name : type}`); showTab("ec"); }
function renderFx() { const pane = $("#pane-fx"); const q = (S.fxQuery || "").toLowerCase(); const cat = CR.catalog || { video: [], audio: [] }; const groups = {}; for (const e of cat.video) (groups["Video Effects › " + e.cat] = groups["Video Effects › " + e.cat] || []).push(["video", e]); for (const e of cat.audio) (groups["Audio Effects › " + e.cat] = groups["Audio Effects › " + e.cat] || []).push(["audio", e]);
  const catalogHtml = Object.entries(groups).sort().map(([g, items]) => { const its = items.filter(([k, e]) => !q || e.name.toLowerCase().includes(q) || g.toLowerCase().includes(q)); return its.length ? `<details ${q ? "open" : ""}><summary>${g}</summary>${its.map(([k, e]) => `<div class="fxi" draggable="true" data-stack="${k}|${e.type}" title="${e.note || (e.preview ? "previews live; renders exactly" : "renders exactly (preview unchanged)")} — double-click or drag onto a clip">${e.name}<span>${e.preview ? "◐" : ""}</span></div>`).join("")}</details>` : ""; }).join("");
  if (!S.fxPresetsLoaded) { S.fxPresetsLoaded = true; Promise.all([api.get("/api/settings"), api.get("/api/presets/effect_presets")]).then(([st, b]) => { S.fxPresets = { ...(b || {}), ...((st && st.effect_presets) || {}) }; renderFx(); }).catch(() => { }); }
  const presets = S.fxPresets || {}; const cats = {}; for (const [n, pz] of Object.entries(presets)) (cats[pz.category || "My presets"] = cats[pz.category || "My presets"] || []).push([n, pz]);
  const presetHtml = Object.keys(presets).length ? Object.entries(cats).sort().map(([cat, items]) => `<details open><summary>${cat}</summary>${items.map(([n, pz]) => `<div class="preset-card" data-fxpreset="${n}" title="double-click to apply to the selected clip(s)"><b>${n}</b>${pz.description ? `<span>${pz.description}</span>` : ""}</div>`).join("")}</details>`).join("") : "";
  pane.innerHTML = `<div class="binbar"><input id="fxSearch" placeholder="Search effects (${cat.video.length + cat.audio.length + 24} available)" value="${S.fxQuery || ""}"><button id="fxSavePreset" title="Save the selected clip's effects as a preset">Save preset</button></div><div class="fxlist">${presetHtml}${FX_TREE.map(([g, items]) => { const its = items.filter(([n]) => !q || n.toLowerCase().includes(q) || g.toLowerCase().includes(q)); return its.length ? `<details ${q ? "open" : ""}><summary>${g}</summary>${its.map(([n, fn, k]) => `<div class="fxi" data-fx="${g}|${n}" title="double-click to apply to the selected clip(s)">${n}<small>${k || ""}</small></div>`).join("")}</details>` : ""; }).join("")}${catalogHtml}</div>`;
  $("#fxSearch").oninput = e => { S.fxQuery = e.target.value; renderFx(); $("#fxSearch").focus(); }; $$("[data-fx]", pane).forEach(el => el.ondblclick = () => { const [g, n] = el.dataset.fx.split("|"); const it = FX_TREE.find(x => x[0] === g)[1].find(x => x[0] === n); it[1](); });
  $$("[data-stack]", pane).forEach(el => { el.ondblclick = () => { const [k, t] = el.dataset.stack.split("|"); addStackFx(k, t); }; el.ondragstart = ev => ev.dataTransfer.setData("text/fx", el.dataset.stack); }); if (!CR.catalog) loadCatalog().then(renderFx);
  $("#fxSavePreset").onclick = async () => { const sel = CR.selectedClips(); if (sel.length !== 1) { status("Select one clip whose effects to save."); return; } const n = prompt("Preset name"); if (!n) return; const c = sel[0].c; const st = await api.get("/api/settings"); st.effect_presets = { ...(st.effect_presets || {}), [n]: { fx_stack: deep(c.fx_stack || []), afx_stack: deep(c.afx_stack || []), color: deep(c.color || {}), effects: deep(c.effects || {}) } }; await api.json("PUT", "/api/settings", { effect_presets: st.effect_presets }); S.fxPresets = { ...(S.fxPresets || {}), ...st.effect_presets }; renderFx(); status(`Saved preset "${n}"`); };
  $$("[data-fxpreset]", pane).forEach(el => el.ondblclick = () => { const p = presets[el.dataset.fxpreset]; const sel = CR.selectedClips(); if (!sel.length) { status("Select a clip first."); return; } applyOps(sel.map(({ c, tr }) => ({ op: "set_clip", sequence: S.seq.id, track: tr.id, clip: { id: c.id, fx_stack: [...(c.fx_stack || []), ...deep(p.fx_stack || []).map(f => ({ ...f, id: CR.uid() }))], afx_stack: [...(c.afx_stack || []), ...deep(p.afx_stack || []).map(f => ({ ...f, id: CR.uid() }))], color: { ...(c.color || {}), ...(p.color || {}) }, effects: { ...(c.effects || {}), ...(p.effects || {}) } } })), "effect_preset", `preset ${el.dataset.fxpreset}`); }); if (!S.fxPresets) api.get("/api/settings").then(st => { S.fxPresets = st.effect_presets || {}; if (Object.keys(S.fxPresets).length) renderFx(); }); }
CR.addStackFx = addStackFx;
function renderScopes() { const pane = $("#pane-scopes"); if (pane.innerHTML) return; pane.innerHTML = `<div class="grp"><h4>Scopes<span class="sp"></span><span style="color:var(--dim);font-weight:400">from the Program frame (paused)</span></h4><canvas class="scope" id="scWave" width="360" height="160"></canvas><p style="color:var(--dim);margin:2px 0 6px">RGB parade (waveform)</p><canvas class="scope" id="scHist" width="360" height="120"></canvas><p style="color:var(--dim);margin:2px 0 6px">Histogram (luma)</p><canvas class="scope" id="scVec" width="360" height="220" style="height:220px"></canvas><p style="color:var(--dim);margin:2px 0 0">Vectorscope (YUV chroma; boxes = 75% color bar targets)</p></div>`; }
CR.metersTick = metersTick;
CR.scopes = cv => { const pane = $("#pane-scopes"); if (!pane || !pane.classList.contains("on")) return; const w = $("#scWave"), h = $("#scHist"); if (!w) return; const sw = 96, sh = 54; const tmp = CR._scopeTmp || (CR._scopeTmp = document.createElement("canvas")); tmp.width = sw; tmp.height = sh; const tc = tmp.getContext("2d", { willReadFrequently: true }); tc.drawImage(cv, 0, 0, sw, sh); const d = tc.getImageData(0, 0, sw, sh).data;
  const wc = w.getContext("2d"); wc.fillStyle = "#101010"; wc.fillRect(0, 0, 360, 160); const colw = 360 / 3; for (let ch = 0; ch < 3; ch++) { wc.fillStyle = ["rgba(255,80,80,.7)", "rgba(80,255,80,.7)", "rgba(80,140,255,.7)"][ch]; for (let x = 0; x < sw; x++) for (let y = 0; y < sh; y++) { const v = d[(y * sw + x) * 4 + ch]; wc.fillRect(ch * colw + x / sw * colw, 158 - v / 255 * 156, 1.5, 1.5); } } wc.strokeStyle = "#333"; for (let i = 0; i <= 4; i++) { wc.beginPath(); wc.moveTo(0, 2 + i * 39); wc.lineTo(360, 2 + i * 39); wc.stroke(); }
  const hist = new Array(64).fill(0); for (let i = 0; i < d.length; i += 4) hist[Math.min(63, Math.floor((0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2]) / 4))]++; const mx = Math.max(...hist, 1); const hc = h.getContext("2d"); hc.fillStyle = "#101010"; hc.fillRect(0, 0, 360, 120); hc.fillStyle = "#9fc3ff"; hist.forEach((v, i) => hc.fillRect(i * 360 / 64, 120 - v / mx * 116, 360 / 64 - 1, v / mx * 116));
  const vs = $("#scVec"); if (vs) { const vc = vs.getContext("2d"); const cx = 180, cy = 110, R = 100; vc.fillStyle = "#101010"; vc.fillRect(0, 0, 360, 220); vc.strokeStyle = "#333"; vc.beginPath(); vc.arc(cx, cy, R, 0, Math.PI * 2); vc.stroke(); vc.beginPath(); vc.moveTo(cx - R, cy); vc.lineTo(cx + R, cy); vc.moveTo(cx, cy - R); vc.lineTo(cx, cy + R); vc.stroke();
    const targets = { R: [255, 0, 0], G: [0, 255, 0], B: [0, 0, 255], Cy: [0, 255, 255], Mg: [255, 0, 255], Yl: [255, 255, 0] }; vc.font = "10px sans-serif"; vc.fillStyle = "#666"; vc.strokeStyle = "#555"; for (const [n, [r, g, b]] of Object.entries(targets)) { const u = (-0.1471 * r - 0.2889 * g + 0.436 * b) * 0.75, v = (0.615 * r - 0.5149 * g - 0.1 * b) * 0.75; const x = cx + u / 128 * R, y = cy - v / 128 * R; vc.strokeRect(x - 5, y - 5, 10, 10); vc.fillText(n, x + 7, y + 3); }
    vc.fillStyle = "rgba(159,195,255,.55)"; for (let i = 0; i < d.length; i += 8) { const r = d[i], g = d[i + 1], b = d[i + 2]; const u = -0.1471 * r - 0.2889 * g + 0.436 * b, v = 0.615 * r - 0.5149 * g - 0.1 * b; vc.fillRect(cx + u / 128 * R, cy - v / 128 * R, 1.5, 1.5); } } };
function attributeFormat(format) { return `${format.width}×${format.height} · ${format.fps} fps`; }
function attributeReviewLines(owner, report) {
  const sum = report.summary, source = report.donor, copied = owner.donors.find(item => item.bundle.clip.id === source.clip.id), lines = [sum.message,
    `Donor: ${copied?.name || sum.donor.name} [${sum.donor.clip_id}] · ${sum.donor.duration.toFixed(6)} s · ${attributeFormat(source.sequence)}.`,
    `Copied source range: ${source.clip.in_.toFixed(6)}–${source.clip.out.toFixed(6)} s${source.clip.hold ? " · held" : source.clip.reverse ? " · reverse" : ""}${source.clip.time_remap?.length ? " · speed curve" : ""}.`,
    `Target sequence: ${owner.sequence.name || owner.sequence.id} · ${attributeFormat(owner.sequence)}.`,
    `Categories: ${sum.groups.map(id => CR.ATTRIBUTE_GROUPS.find(group => group.id === id)?.label || id).join(", ")}.`,
    sum.include_animation ? "Copy animation only for the selected categories." : "Values only: existing target animation stays and can override the copied static values.",
    sum.timing === "scale" ? "Timing: scale copied animation/fade timing by each target duration ÷ donor duration." : "Timing: keep copied seconds; out-of-range copied points and fade tails are disclosed below."];
  if (attributeFormat(source.sequence) !== attributeFormat(owner.sequence)) lines.push("Source and target formats differ. Timing follows the selected seconds/duration policy; pixel-valued settings retain their values.");
  for (const target of sum.targets) {
    const range = owner.ranges.find(item => item.id === target.clip_id);
    lines.push(`${target.clip_id} on ${target.track}: ${target.duration.toFixed(6)} s; timing ratio ${target.ratio.toFixed(6)}; ${target.changed ? "changes" : "no change"}.`);
    if (range) lines.push(`Timeline ${range.start.toFixed(6)}–${range.end.toFixed(6)} s; source ${range.in_.toFixed(6)}–${range.out.toFixed(6)} s${range.hold ? " · held" : range.reverse ? " · reverse" : ""}${range.ramp ? " · speed curve" : ""}.`);
    if (target.fields.length) lines.push("  Fields: " + target.fields.join(", "));
    if (target.curves.length) lines.push("  Curves: " + target.curves.join(", "));
    for (const warning of target.warnings) if (!sum.warnings.includes(warning)) lines.push("  Warning: " + warning);
  }
  lines.push(...report.issues.map(issue => `${issue.severity.toUpperCase()}: ${issue.message}`), ...sum.warnings.map(warning => "Warning: " + warning));
  lines.push("Source media, source timing, clip positions and linked/detached audio associations are preserved. Apply saves the reviewed changes together; a no-op adds no Undo entry."); return lines;
}
function pasteAttributesDialog(owner) {
  const dialog = $("#dlgPasteAttributes"), output = $("#paReview"), donor = $("#paDonor"), groups = $("#paGroups"), animation = $("#paAnimation"), timing = $("#paTiming"), review = $("#paInspect"), apply = $("#paApply");
  dialog.attributeOwner?.retire(false); let active = true, busy = false, report = null, reviewedChoices = null;
  const visible = () => active && dialog.attributeOwner === owner && dialog.classList.contains("open"), sameProject = () => window.FilmocitySync.sameProject(owner.context, S.context);
  donor.replaceChildren(); owner.donors.forEach((item, index) => { const option = document.createElement("option"); option.value = String(index); option.textContent = `${index + 1}. ${item.name} [${item.bundle.clip.id}] · ${item.duration.toFixed(6)} s`; donor.appendChild(option); }); donor.value = "0";
  groups.replaceChildren(); const inputs = CR.ATTRIBUTE_GROUPS.map(group => {
    const label = document.createElement("label"), input = document.createElement("input"); input.type = "checkbox"; input.checked = group.selected; input.dataset.attributeGroup = group.id; input.id = "paGroup_" + group.id; label.htmlFor = input.id;
    const text = document.createElement("span"); text.textContent = group.label; label.append(input, text); groups.appendChild(label); return input;
  });
  animation.checked = true; timing.value = "seconds";
  const choices = () => ({ donor: Number(donor.value), groups: inputs.filter(input => input.checked).map(input => input.dataset.attributeGroup), include_animation: animation.checked, timing: timing.value });
  const choicesKey = () => JSON.stringify(choices()), current = () => visible() && (reviewedChoices === null || choicesKey() === reviewedChoices);
  const setBusy = value => {
    busy = value; if (!visible()) return; donor.disabled = animation.disabled = timing.disabled = value || owner.submitted; for (const input of inputs) input.disabled = value || owner.submitted;
    review.disabled = value || owner.submitted; apply.disabled = value || owner.submitted || !report?.ok || !CR.attributeReviewCurrent(owner, report) || !current(); output.setAttribute("aria-busy", String(value));
  };
  const describe = () => {
    const item = owner.donors[Number(donor.value)]; $("#paSourceDetails").textContent = item ? `Copied donor: ${item.name} · ${attributeFormat(item.bundle.sequence)} · ${item.duration.toFixed(6)} s. ${owner.clipIds.length} target clip(s) in ${owner.sequence.name || owner.sequence.id} · ${attributeFormat(owner.sequence)}.` : "Choose a copied donor.";
    $("#paAnimationNotice").textContent = animation.checked ? "Only selected categories receive copied animation. Source speed curves and linked audio associations stay unchanged." : "Values only: target keyframes are kept, including selected categories, and can override copied static values.";
  };
  const changed = () => { if (!visible() || owner.submitted) return; owner.generation++; owner.review = null; report = null; reviewedChoices = null; describe(); output.textContent = "Choices changed. Review these attributes before Apply."; setBusy(busy); };
  donor.onchange = animation.onchange = timing.onchange = changed; for (const input of inputs) input.onchange = changed;
  owner.retire = (announce = true) => {
    if (!active) return; active = false; owner.cancelled = true; owner.generation++; owner.review = null; window.removeEventListener("keydown", owner.escape, true);
    if (dialog.attributeOwner === owner) { dialog.attributeOwner = null; closeDlg("#dlgPasteAttributes"); }
    if (announce && sameProject()) status(owner.submitted ? "Attribute controls closed. An already submitted edit may be saved; inspect its saved outcome or Recovery before repeating it." : "Paste Attributes canceled before Apply.");
  };
  owner.escape = event => { if (event.key === "Escape") { event.preventDefault(); owner.retire(); } };
  dialog.attributeOwner = owner; $("#paCancel").onclick = () => owner.retire();
  review.onclick = async () => {
    if (!visible() || busy || owner.submitted) return false; report = null; reviewedChoices = choicesKey(); const captured = choices(); setBusy(true); output.textContent = "Saving pending edits, then reviewing the copied attributes. No attribute edit has been submitted.";
    try { const result = await CR.reviewAttributePaste(owner, captured, current); if (!current()) return false; report = result; output.textContent = attributeReviewLines(owner, result).join("\n"); output.focus(); return result; }
    catch (error) { if (visible() && sameProject()) { output.textContent = error.message || String(error); status(output.textContent, "err"); } return false; }
    finally { setBusy(false); }
  };
  apply.onclick = async () => {
    if (!visible() || busy || owner.submitted || !report?.ok) return false; setBusy(true);
    try { const result = await CR.applyAttributePaste(owner, report, current); owner.retire(false); return result; }
    catch (error) { report = null; owner.review = null; if (visible() && sameProject()) { output.textContent = error.message || String(error); status(output.textContent, "err"); } return false; }
    finally { setBusy(false); }
  };
  openDlg("#dlgPasteAttributes"); window.addEventListener("keydown", owner.escape, true); describe(); setBusy(false); output.textContent = "Choose a copied donor and categories. Review exact target changes, timing and warnings before Apply."; donor.focus(); return owner;
}
function flattenReviewLines(report) {
  const sum = report.summary, lines = [sum.message, `${sum.selected_count} selected multicamera clip(s).`,
    "Source tracks: " + sum.source_tracks.map(tr => `${tr.name || tr.id} [${tr.id}] · ${tr.kind}`).join("; "),
    "New tracks: " + (sum.added_tracks.map(tr => `${tr.name || tr.id} [${tr.id}] · ${tr.kind}`).join("; ") || "none")];
  let count = 0, omitted = 0;
  for (const value of sum.resolved) {
    lines.push(`Clip ${value.clip_id}: camera ${value.angle + 1} in ${value.source_sequence}; audio policy ${value.audio_mode}.`);
    for (const [kind, ranges] of [["Picture", value.picture_ranges], ["Sound", value.audio_ranges]]) for (const range of ranges) {
      if (++count > 100) { omitted++; continue; }
      lines.push(`${kind}: ${range.start.toFixed(6)}–${range.end.toFixed(6)} s → ${range.media_id} source ${range.source_in.toFixed(6)}–${range.source_out.toFixed(6)} s · ${range.speed}×${range.reverse ? " reverse" : ""}${range.hold ? " held" : ""}.`);
    }
  }
  if (omitted) lines.push(`${omitted} additional ranges are in the Flatten review API response.`);
  lines.push(...sum.processing.map(value => "Processing: " + value));
  const messages = [...report.issues.map(issue => `${issue.severity.toUpperCase()}: ${issue.message}`), ...sum.warnings.map(value => "Warning: " + value)];
  lines.push(...messages.slice(0, 100)); if (messages.length > 100) lines.push(`${messages.length - 100} additional messages are in the Flatten review API response.`);
  if (sum.affected_fields.length) lines.push("Changed: " + sum.affected_fields.join(", "));
  lines.push("Apply saves the reviewed direct source clips and sound routing together as one undoable edit. Original camera sequences remain available. Unsupported processing is refused instead of discarded."); return lines;
}
function flattenDialog(owner) {
  const dialog = $("#dlgMulticamFlatten"), output = $("#multicamFlattenReview"), review = $("#multicamFlattenInspect"), apply = $("#multicamFlattenApply");
  dialog.flattenOwner?.retire(false); let active = true, busy = false, report = null;
  const visible = () => active && dialog.flattenOwner === owner && dialog.classList.contains("open"), sameProject = () => window.FilmocitySync.sameProject(owner.context, S.context);
  const setBusy = value => { busy = value; if (!visible()) return; review.disabled = value || owner.submitted; apply.disabled = value || owner.submitted || !report?.ok || !CR.flattenReviewCurrent(owner, report); output.setAttribute("aria-busy", String(value)); };
  owner.retire = (announce = true) => {
    if (!active) return; active = false; owner.cancelled = true; owner.generation++; owner.review = null; window.removeEventListener("keydown", owner.escape, true);
    if (dialog.flattenOwner === owner) { dialog.flattenOwner = null; closeDlg("#dlgMulticamFlatten"); }
    if (announce && sameProject()) status(owner.submitted ? "Flatten controls closed. An already submitted edit may be saved; inspect the saved project or Recovery before repeating it." : "Flatten canceled before Apply.");
  };
  owner.escape = event => { if (event.key === "Escape") { event.preventDefault(); owner.retire(); } };
  dialog.flattenOwner = owner; $("#multicamFlattenCancel").onclick = () => owner.retire();
  review.onclick = async () => {
    if (!visible() || busy || owner.submitted) return false; report = null; setBusy(true); output.textContent = "Saving pending edits, then reviewing camera ranges, sound routing and processing. No Flatten edit has been submitted.";
    try { const result = await CR.reviewFlattenSelection(owner, visible); if (!visible()) return false; report = result; output.textContent = flattenReviewLines(result).join("\n"); output.focus(); return result; }
    catch (error) { if (visible() && sameProject()) { output.textContent = error.message || String(error); status(output.textContent, "err"); } return false; }
    finally { setBusy(false); }
  };
  apply.onclick = async () => {
    if (!visible() || busy || owner.submitted || !report?.ok) return false; setBusy(true);
    try { const result = await CR.applyFlattenSelection(owner, report, visible); owner.retire(false); return result; }
    catch (error) { report = null; owner.review = null; if (visible() && sameProject()) { output.textContent = error.message || String(error); status(output.textContent, "err"); } return false; }
    finally { setBusy(false); }
  };
  $("#multicamFlattenSelection").textContent = `${owner.clipIds.length} selected multicamera clip(s) in ${owner.sequence.name || owner.sequence.id}.`;
  openDlg("#dlgMulticamFlatten"); window.addEventListener("keydown", owner.escape, true); setBusy(false); output.textContent = "Review direct picture and sound ranges, preserved processing and any refusal before Apply."; review.focus(); return owner;
}
function nestReviewLines(report) {
  const s = report.summary, lines = [s.message, `Sequence: ${s.name} [${s.child_sequence || "not created"}]`,
    `Range: ${s.range.start.toFixed(6)}–${s.range.end.toFixed(6)} s (${s.range.duration.toFixed(6)} s); ${s.selected_count} selected clip(s).`,
    "Source tracks: " + s.source_tracks.map(tr => `${tr.name || tr.id} [${tr.id}] · ${tr.kind} · ${tr.selected_clip_ids.length} clip(s)`).join("; "),
    "Wrappers: " + (s.wrapper_clip_ids.join(", ") || "none — resolve the issues below"),
    "New tracks: " + (s.added_tracks.map(tr => `${tr.name || tr.id} [${tr.id}] · ${tr.kind}`).join("; ") || "none")];
  if (Number.isFinite(s.range.selected_start) && s.range.selected_start > s.range.start + 1e-10) lines.push(`Selected content starts at ${s.range.selected_start.toFixed(6)} s; the wrapper has ${(s.range.selected_start - s.range.start).toFixed(6)} s of transparent lead-in to preserve its picture/sample phase.`);
  if (Array.isArray(s.picture_ranges) && s.picture_ranges.length) {
    lines.push("Picture wrapper spans: " + s.picture_ranges.slice(0, 100).map(range => `${range[0].toFixed(6)}–${range[1].toFixed(6)} s`).join("; "));
    if (s.picture_ranges.length > 100) lines.push(`${s.picture_ranges.length - 100} additional picture spans are in the Nest review API response.`);
  }
  if (Number.isFinite(s.child_duration)) lines.push(`Child render canvas: ${s.child_duration.toFixed(6)} s. Tail padding: ${(s.tail_padding || 0).toFixed(6)} s; padding does not extend the parent wrapper endpoints.`);
  for (const value of s.processing) lines.push("Processing: " + value);
  if (s.affected_fields.length) lines.push("Changed: " + s.affected_fields.join(", "));
  const messages = [...report.issues.map(issue => `${issue.severity.toUpperCase()}: ${issue.message}`), ...s.warnings.map(text => "Warning: " + text)];
  lines.push(...messages.slice(0, 100));
  if (messages.length > 100) lines.push(`${messages.length - 100} additional messages are in the Nest review API response.`);
  lines.push("Apply saves the child sequence and its linked wrapper clips together as one undoable edit. The child remains available in the Project panel; the current timeline stays open.");
  return lines;
}
function nestDialog(owner) {
  const dialog = $("#dlgSequenceNest"), name = $("#sequenceNestName"), output = $("#sequenceNestReview"), review = $("#sequenceNestInspect"), apply = $("#sequenceNestApply");
  dialog.nestOwner?.retire(false);
  let active = true, busy = false, settings = "", report = null;
  const visible = () => active && dialog.nestOwner === owner && dialog.classList.contains("open");
  const sameProject = () => window.FilmocitySync.sameProject(owner.context, S.context);
  const setBusy = value => { busy = value; if (!visible()) return; name.disabled = review.disabled = value || owner.submitted; apply.disabled = value || owner.submitted || !report?.ok || !CR.nestReviewCurrent(owner, report); output.setAttribute("aria-busy", String(value)); };
  owner.retire = (announce = true) => {
    if (!active) return; active = false; owner.cancelled = true; owner.generation++; owner.review = null;
    window.removeEventListener("keydown", owner.escape, true);
    if (dialog.nestOwner === owner) { dialog.nestOwner = null; closeDlg("#dlgSequenceNest"); }
    if (announce && sameProject()) status(owner.submitted ? "Nest controls closed. An already submitted edit may be saved; inspect the saved project or Recovery before repeating it." : "Nest canceled before Apply.");
  };
  owner.escape = event => { if (event.key === "Escape") { event.preventDefault(); owner.retire(); } };
  dialog.nestOwner = owner;
  $("#sequenceNestCancel").onclick = () => owner.retire();
  name.oninput = name.onchange = () => { owner.generation++; owner.review = null; report = null; apply.disabled = true; if (visible()) output.textContent = "Name changed. Review the selected clips before Apply."; };
  review.onclick = async () => {
    if (!visible() || busy || owner.submitted) return false;
    settings = name.value; const captured = settings; report = null; setBusy(true);
    output.textContent = "Saving pending edits, then reviewing the selected tracks and dependencies. No nesting change has been submitted.";
    try {
      const result = await CR.reviewNestSelection(owner, captured, () => visible() && name.value === captured);
      if (!visible() || name.value !== captured) return false;
      report = result; output.textContent = nestReviewLines(result).join("\n"); output.focus(); return result;
    } catch (error) { if (visible() && sameProject()) { output.textContent = error.message || String(error); status(output.textContent, "err"); } return false; }
    finally { setBusy(false); }
  };
  apply.onclick = async () => {
    if (!visible() || busy || owner.submitted || !report || name.value !== settings) return false;
    setBusy(true);
    try {
      const result = await CR.applyNestSelection(owner, report, () => visible() && name.value === settings);
      owner.retire(false); return result;
    } catch (error) { report = null; owner.review = null; if (visible() && sameProject()) { output.textContent = error.message || String(error); status(output.textContent, "err"); } return false; }
    finally { setBusy(false); }
  };
  name.value = owner.name; $("#sequenceNestSelection").textContent = `${owner.clipIds.length} selected clip(s) in ${owner.sequence.name || owner.sequence.id}.`;
  openDlg("#dlgSequenceNest"); window.addEventListener("keydown", owner.escape, true); setBusy(false);
  output.textContent = "Review the range, locks, overlapping clips and processing before Apply. Unsupported dependencies are refused without changing the timeline.";
  name.focus(); name.select?.(); return owner;
}
function interpretationRate(value) {
  const text = String(value ?? "").trim();
  if (!text || text.length > 64 || !/^(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+|[0-9]+\/[0-9]+)$/.test(text)) throw Error("Enter a positive decimal or fraction, such as 29.97 or 30000/1001; choose Use native to restore the original rate.");
  window.FilmocityTime.frameRate(text);
  return text;
}
function interpretationFamily(owner) {
  const media = S.proj?.media || {}, selected = media[owner.mediaId];
  if (!selected) return null;
  const physical = selected.audio_alias?.physical_media_id || selected.subclip_of || selected.id;
  return Object.entries(media).filter(([id, item]) => id === owner.mediaId || id === physical || item.subclip_of === physical || item.audio_alias?.physical_media_id === physical).sort(([a], [b]) => a.localeCompare(b));
}
function interpretationReviewLines(report) {
  const summary = report.summary, seconds = value => Number.isFinite(value) ? value.toFixed(6) : "unknown", lines = [
    summary.message, `Selected source: ${summary.source_name || report.media_id}`,
    `Native rate: ${summary.native_rate} fps; current: ${summary.current_rate} fps; target: ${summary.target_rate} fps.`,
    "The native source window stays fixed. Timeline clips, their timing and track locks stay unchanged; incompatible existing uses block Apply.",
    "If an affected source is displayed in Source, an actual rate change pauses and reloads that monitor and clears its In/Out marks. Preview preparation is separate from the saved change."
  ];
  if (summary.required_fullmix_ids.length) lines.push("Coupled full-mix audio sources: " + summary.required_fullmix_ids.map(id => `${S.proj.media[id]?.name || id} [${id}]`).join(", ") + ". These require explicit group consent when changing the physical original.");
  const append = (values, describe, noun) => {
    for (const item of values.slice(0, 50)) lines.push(describe(item));
    if (values.length > 50) lines.push(`${values.length - 50} more ${noun}; the complete interpretation review API response contains every entry.`);
  };
  append(summary.windows, item => `${item.name || item.media_id} [${item.media_id}]: duration ${seconds(item.old_duration)} → ${seconds(item.new_duration)} s; source In ${seconds(item.old_sub_in)} → ${seconds(item.new_sub_in)} s; retained native window ${seconds(item.native_in)}–${seconds(item.native_out)} s.`, "source windows");
  append(summary.uses, item => `Use ${item.sequence}/${item.track_id}/${item.clip_id}${item.locked ? " [locked track]" : ""}${item.hold ? " [held frame]" : ""}: logical ${seconds(item.logical_in)}–${seconds(item.logical_out)} s; native ${seconds(item.native_in)}–${seconds(item.native_out)} s; timeline duration ${seconds(item.duration)} s.`, "timeline uses");
  if (summary.cleared_fields.length) lines.push("Source-bound fields to reset: " + summary.cleared_fields.join(", ") + ".");
  const warnings = [...new Set([...summary.warnings, ...report.issues.map(issue => `${issue.severity}: ${issue.message}`)])];
  for (const warning of warnings.length > 50 ? [...warnings.slice(0, 40), `${warnings.length - 50} additional warnings are in the complete review API response.`, ...warnings.slice(-10)] : warnings) lines.push(warning);
  if (!report.ok) lines.push("Apply is blocked. Review the issues and change the settings or cancel; no source change has been made.");
  else if (!summary.changed) lines.push("These settings are unchanged. Applying this reviewed no-op creates no Undo step.");
  return lines;
}
function interpretationReport(report, owner, context, settings) {
  const sync = window.FilmocitySync, strings = value => Array.isArray(value) && value.every(item => typeof item === "string"), summary = report?.summary;
  if (typeof report?.ok !== "boolean" || report.kind !== "source_interpretation" || report.media_id !== owner.mediaId || report.requested_media_id !== owner.mediaId ||
    !sync.validContext(report.context) || !sync.sameProject(context, report.context) || report.context.revision !== context.revision || !/^[a-f0-9]{64}$/.test(report.fingerprint || "") ||
    !report.settings || report.settings.include_fullmix !== settings.include_fullmix || (settings.fps === null ? report.settings.fps !== null : typeof report.settings.fps !== "string" || !/^[1-9][0-9]*\/[1-9][0-9]*$/.test(report.settings.fps) || Math.abs(window.FilmocityTime.frameRate(report.settings.fps) - window.FilmocityTime.frameRate(settings.fps)) > 1e-9) ||
    !strings(report.affected_media_ids) || !report.affected_media_ids.includes(owner.mediaId) || new Set(report.affected_media_ids).size !== report.affected_media_ids.length ||
    !summary || summary.kind !== "source_interpretation" || typeof summary.changed !== "boolean" || typeof summary.message !== "string" || summary.scope !== "selected_source" ||
    ![summary.native_rate, summary.current_rate, summary.target_rate].every(value => typeof value === "string" || typeof value === "number") ||
    !strings(summary.required_fullmix_ids) || !strings(summary.timing_media_ids) || !strings(summary.affected_media_ids) || JSON.stringify(summary.affected_media_ids) !== JSON.stringify(report.affected_media_ids) || !strings(summary.warnings) || !strings(summary.cleared_fields) ||
    !Array.isArray(summary.windows) || !summary.windows.length || summary.windows.some(item => !item || typeof item.media_id !== "string" || !["old_factor", "new_factor", "old_duration", "new_duration", "old_sub_in", "new_sub_in", "native_in", "native_out"].every(key => Number.isFinite(item[key]))) ||
    !Array.isArray(summary.uses) || !Array.isArray(report.issues) || report.issues.some(issue => !issue || typeof issue.message !== "string" || !["error", "warning", "info"].includes(issue.severity))) throw Error("Review did not confirm this saved source and these exact settings. Review again before applying.");
  return report;
}
function interpretDialog() {
  try {
    if (!CR.canEdit() || CR.projectSaveState().error || !window.FilmocitySync.validContext(S.context) || !S.proj?.sequences?.includes(S.seq)) throw Error("Resolve pending project controls or Recovery before interpreting footage.");
    const ids = [...S.binSel], media = ids.length === 1 && S.proj.media[ids[0]];
    if (!media) throw Error("Select one source item in the Project panel.");
    window.FilmocityTime.frameRate(media.frame_rate ?? media.native_fps ?? media.fps);
    const dialog = $("#dlgSourceInterpret"), output = $("#sourceInterpretReview"), mode = $("#sourceInterpretMode"), rate = $("#sourceInterpretRate"), fullmix = $("#sourceInterpretFullmix"), reviewButton = $("#sourceInterpretInspect"), applyButton = $("#sourceInterpretApply");
    dialog.interpretOwner?.retire(false);
    const owner = { project: S.proj, sequence: S.seq, context: { ...S.context }, mediaId: media.id, selection: JSON.stringify(ids), active: true, pending: false, submitted: false, generation: 0, report: null };
    owner.family = interpretationFamily(owner); owner.signature = JSON.stringify(owner.family); owner.timeline = JSON.stringify(S.proj.sequences); dialog.interpretOwner = owner;
    const visible = () => owner.active && dialog.interpretOwner === owner && dialog.classList.contains("open");
    const sameOwner = () => S.proj === owner.project && S.seq === owner.sequence && window.FilmocitySync.sameProject(owner.context, S.context);
    const current = () => visible() && sameOwner() && JSON.stringify([...S.binSel]) === owner.selection && owner.signature === JSON.stringify(interpretationFamily(owner)) && owner.timeline === JSON.stringify(S.proj.sequences);
    const controls = () => ({ mode: mode.value, rate: rate.value, include_fullmix: fullmix.checked });
    const settings = () => ({ fps: mode.value === "native" ? null : mode.value === "assume" ? interpretationRate(rate.value) : (() => { throw Error("Choose Use native or Assume frame rate."); })(), include_fullmix: !!fullmix.checked });
    const setBusy = busy => {
      if (!visible()) return;
      owner.pending = busy; output.setAttribute("aria-busy", String(busy)); mode.disabled = fullmix.disabled = reviewButton.disabled = busy || owner.submitted; rate.disabled = busy || owner.submitted || mode.value === "native";
      applyButton.disabled = busy || owner.submitted || !owner.report?.ok || owner.report.issues.some(issue => issue.severity === "error");
    };
    const invalidate = () => { owner.generation++; owner.report = null; owner.reviewSnapshot = null; applyButton.disabled = true; if (visible()) { output.textContent = "Settings changed. Review the new source timing before Apply."; rate.disabled = owner.pending || owner.submitted || mode.value === "native"; } };
    owner.retire = (announce = true) => { if (!owner.active) return; owner.active = false; owner.generation++; owner.report = null; window.removeEventListener("keydown", owner.escape, true); if (dialog.interpretOwner === owner) { dialog.interpretOwner = null; closeDlg("#dlgSourceInterpret"); } if (announce && window.FilmocitySync.sameProject(owner.context, S.context)) status(owner.submitted ? "Interpretation controls closed. An already submitted change may be saved; inspect its saved outcome or Recovery before repeating it." : "Interpretation canceled before submission."); };
    owner.escape = event => { if (event.key === "Escape") { event.preventDefault(); owner.retire(); } };
    $("#sourceInterpretCancel").onclick = () => owner.retire();
    for (const control of [mode, rate, fullmix]) { control.oninput = invalidate; control.onchange = invalidate; }
    reviewButton.onclick = async () => {
      if (owner.pending || owner.submitted || !current() || !CR.canEdit()) { if (visible()) output.textContent = "The original source, selection or project changed. Cancel and reopen Interpret Footage."; return false; }
      let choice; try { choice = settings(); } catch (error) { output.textContent = error.message; applyButton.disabled = true; owner.report = null; rate.focus(); return false; }
      const generation = ++owner.generation, captured = JSON.stringify(controls()); owner.report = null; owner.reviewSnapshot = null; setBusy(true);
      output.textContent = "Saving pending edits, then reviewing source timing. No interpretation change has been submitted.";
      const valid = () => current() && owner.generation === generation && captured === JSON.stringify(controls());
      try {
        const state = await CR.flushSaves();
        if (!valid() || state !== CR.projectSaveState() || state.error || !CR.canEdit()) throw Error("The selected source or project changed, or Recovery needs attention. Cancel and reopen Interpret Footage.");
        const context = { ...state.context };
        const report = await api.json("POST", "/api/media/interpret/review", { media_id: owner.mediaId, ...choice, _context: context, actor: "human", client: CR.CLIENT });
        if (!valid() || state !== CR.projectSaveState() || state.pending || state.error || S.context.revision !== context.revision) throw Error("The saved source or edit changed while reviewing. Review the current settings again.");
        owner.report = interpretationReport(report, owner, context, choice); owner.reviewSnapshot = JSON.stringify(report); owner.reviewControls = captured; owner.reviewGeneration = generation;
        output.textContent = interpretationReviewLines(report).join("\n"); output.focus(); return report;
      } catch (error) { if (visible() && sameOwner()) { output.textContent = error.message || String(error); status(output.textContent, "err"); } return false; }
      finally { if (visible()) setBusy(false); }
    };
    const reviewCurrent = () => {
      const state = CR.projectSaveState(), report = owner.report;
      return current() && report?.ok === true && !report.issues.some(issue => issue.severity === "error") && owner.reviewSnapshot === JSON.stringify(report) && owner.reviewControls === JSON.stringify(controls()) && owner.reviewGeneration === owner.generation &&
        !state.pending && !state.error && state.context.revision === report.context.revision && S.context.revision === report.context.revision;
    };
    applyButton.onclick = async () => {
      if (owner.pending || owner.submitted || !reviewCurrent() || !CR.canEdit()) { if (visible()) { output.textContent = "The reviewed settings, source or saved revision changed. Review again before Apply."; applyButton.disabled = true; } return false; }
      const report = owner.report, source = S.src && report.affected_media_ids.includes(S.src.id) ? { id: S.src.id, in_: S.srcIn, out: S.srcOut } : null;
      setBusy(true);
      try {
        if (S.playing) CR.togglePlay(false, { commitTrim: false });
        if (source) $("#srcVideo").pause();
        const result = await CR.workflowRequest(async context => {
          if (!reviewCurrent()) { const error = Error("Interpretation was canceled or its reviewed source changed before submission."); error.status = 409; throw error; }
          owner.submitted = true;
          const reply = await api.json("POST", "/api/media/interpret", { media_id: owner.mediaId, ...report.settings, fingerprint: report.fingerprint, _context: context, actor: "human", client: CR.CLIENT });
          if (!sameOwner()) throw Error("The original project or sequence changed while interpretation was submitted. Inspect its saved outcome before continuing.");
          if (reply?.ok !== true || reply.kind !== "source_interpretation" || reply.media_id !== owner.mediaId || reply.requested_media_id !== owner.mediaId || reply.project !== owner.context.project || typeof reply.changed !== "boolean" || reply.media?.id !== owner.mediaId ||
            JSON.stringify(reply.affected_media_ids) !== JSON.stringify(report.affected_media_ids) || typeof reply.summary?.message !== "string" || !Array.isArray(reply.warnings) || reply.warnings.some(value => typeof value !== "string") || !Array.isArray(reply.preparation?.tasks) || !Array.isArray(reply.preparation?.warnings)) throw Error("Interpretation did not confirm the reviewed source change.");
          return reply;
        }, { ...report.context, sequence: owner.sequence.id });
        if (window.FilmocitySync.sameProject(owner.context, S.context) && S.seq?.id === owner.sequence.id) {
          if (result.changed && source && S.src?.id === source.id && S.srcIn === source.in_ && S.srcOut === source.out) CR.loadSource(source.id);
          status([result.summary.message, ...new Set([...result.warnings, ...result.preparation.warnings])].join(" "));
        }
        owner.retire(false); return result;
      } catch (error) {
        owner.report = null;
        if ([400, 401, 403, 404, 409, 422, 501].includes(error.status)) owner.submitted = false;
        if (visible() && sameOwner()) { output.textContent = error.message || String(error); status(output.textContent, "err"); }
        return false;
      } finally { if (visible()) setBusy(false); }
    };
    mode.value = media.interpret_fps == null ? "native" : "assume"; rate.value = media.interpret_fps == null ? "" : String(media.interpret_fps); fullmix.checked = false;
    $("#sourceInterpretSelection").textContent = `${media.name || media.id} [${media.id}]`;
    const fullmixItems = owner.family.filter(([, item]) => item.audio_alias && !Object.hasOwn(item.audio_alias, "channel_index"));
    $("#sourceInterpretGroupNames").textContent = fullmixItems.length ? "Coupled full-mix sources: " + fullmixItems.map(([id, item]) => `${item.name || id} [${id}]`).join(", ") : "No coupled full-mix audio sources in this family.";
    $("#sourceInterpretGroup").hidden = !fullmixItems.length;
    openDlg("#dlgSourceInterpret"); window.addEventListener("keydown", owner.escape, true); setBusy(false);
    output.textContent = "Choose a rate or explicitly restore native, then Review. The selected native source window is preserved; no change is made until Apply.";
    return owner;
  } catch (error) { status(error.message || String(error), "err"); return null; }
}
function extractAudio() { return CR.extractAudioSource(); }
function floatPanel(panel) { if (!panel || panel.classList.contains("floating")) return; const r = panel.getBoundingClientRect(); panel.dataset.home = ""; const ph = document.createElement("div"); ph.className = "panel-placeholder"; ph.style.display = "none"; panel.parentElement.insertBefore(ph, panel); panel._ph = ph; document.body.appendChild(panel); panel.classList.add("floating"); panel.style.left = (r.left + 24) + "px"; panel.style.top = (r.top + 24) + "px"; panel.style.width = Math.max(320, r.width * 0.9) + "px"; panel.style.height = Math.max(200, r.height * 0.9) + "px";
  const hd = panel.querySelector(":scope > .hd"); hd.onmousedown = ev => { if (ev.target.closest("button,input,select")) return; const x0 = ev.clientX - panel.offsetLeft, y0 = ev.clientY - panel.offsetTop; const move = e => { panel.style.left = (e.clientX - x0) + "px"; panel.style.top = (e.clientY - y0) + "px"; }; const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); }; window.addEventListener("mousemove", move); window.addEventListener("mouseup", up); }; CR.renderProgram(); }
function dockPanel(panel) { if (!panel || !panel.classList.contains("floating")) return; panel.classList.remove("floating"); panel.style.cssText = ""; if (panel._ph) { panel._ph.parentElement.insertBefore(panel, panel._ph); panel._ph.remove(); panel._ph = null; } panel.querySelector(":scope > .hd").onmousedown = null; CR.renderProgram(); }
function panelMenu(ev, panel) { document.querySelectorAll(".ctx").forEach(x => x.remove()); const m = document.createElement("div"); m.className = "ctx"; m.style.left = (ev.clientX - 180) + "px"; m.style.top = ev.clientY + "px"; const fl = panel.classList.contains("floating"); m.innerHTML = `<button data-i="0">${fl ? "Dock Panel" : "Undock Panel (float)"}</button><button data-i="1">Maximize / Restore</button><button data-i="2">Close Right Dock</button>`; document.body.appendChild(m); const acts = [() => fl ? dockPanel(panel) : floatPanel(panel), () => { document.querySelector("#" + panel.id).dispatchEvent(new MouseEvent("mouseover", { bubbles: true })); CR.toggleMaximize(); }, () => showRight(false)]; m.querySelectorAll("[data-i]").forEach(b => b.onclick = () => { acts[+b.dataset.i](); m.remove(); }); setTimeout(() => document.addEventListener("mousedown", e => { if (!m.contains(e.target)) m.remove(); }, { once: true }), 0); }
CR.panels = { menuAction, nestDialog, flattenDialog, pasteAttributesDialog, refreshExportPreflight, gainDialog, editMarker, floatPanel, dockPanel, renderMeta, renderQueue, render: () => { window.FilmocityWorkflow?.refresh(); renderScopes(); renderMarkersPane(); renderTimecodePane(); renderMetersPane(); renderEvents(); renderRefPane(); renderMeta(); renderQueue(); renderEC(); renderColor(); renderCaps(); renderProps(); renderAudio(); renderHist(); renderInfo(); renderMixer(); renderGfx(); if (!$("#pane-fx").innerHTML) renderFx(); if (!$("#pane-browser").innerHTML) renderBrowser(""); }, renderEC, meter, setWorkspace, showRight, renderBrowser, browseRelink, retireRelink, openDlg, closeDlg, snapshot, exportFrame, speedDialog };
CR.showTab = showTab; bind();
})();

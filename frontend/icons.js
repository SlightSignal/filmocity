/* Filmocity icon set — original 24×24 line icons (not Adobe artwork). Applied to tools, transport and track headers on load. */
(() => {
const P = (d, extra = "") => `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" ${extra}>${d}</svg>`;
const I = {
  select: P('<path d="M6 3l12 9-5 1.5L15 20l-2 1-2.2-6.3L6 18z"/>'), trackfwd: P('<path d="M5 6h6M5 12h6M5 18h6"/><path d="M14 6l6 6-6 6"/>'), trackback: P('<path d="M13 6h6M13 12h6M13 18h6"/><path d="M10 6l-6 6 6 6"/>'),
  ripple: P('<path d="M4 6v12M9 12H4M14 6l-4 6 4 6"/><path d="M20 12h-6" stroke-dasharray="2 2"/>'), roll: P('<path d="M12 5v14M8 9l-4 3 4 3M16 9l4 3-4 3"/>'), ratestretch: P('<path d="M4 12h16M4 7v10M20 7v10M13 9l3 3-3 3"/>'),
  razor: P('<path d="M6 21l9-9M15 12l3-3a3 3 0 1 1 4 4l-3 3zM8 4l4 4"/>'), slip: P('<path d="M4 8h16M4 16h16M9 5l-3 3 3 3M15 13l3 3-3 3"/>'), slide: P('<path d="M4 12h16M8 8l-4 4 4 4M16 8l4 4-4 4"/><rect x="9" y="9" width="6" height="6"/>'),
  pen: P('<path d="M4 20l4-1L19 8l-3-3L5 16z"/><path d="M14 7l3 3"/>'), rect: P('<rect x="4" y="6" width="16" height="12" rx="1"/>'), ellipse: P('<ellipse cx="12" cy="12" rx="8" ry="6"/>'), hand: P('<path d="M8 12V6a1.5 1.5 0 0 1 3 0v5M11 6a1.5 1.5 0 0 1 3 0v5M14 8a1.5 1.5 0 0 1 3 0v4M8 12l-2-2a1.5 1.5 0 0 0-2 2l4 6a4 4 0 0 0 3 2h4a4 4 0 0 0 4-4v-4"/>'),
  zoom: P('<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5M8 10.5h5M10.5 8v5"/>'), type: P('<path d="M5 6V4h14v2M12 4v16M9 20h6"/>'), snap: P('<path d="M7 4v7a5 5 0 0 0 10 0V4"/><path d="M5 4h4M15 4h4"/>'),
  markin: P('<path d="M10 5H6v14h4"/><path d="M14 12h6"/>'), markout: P('<path d="M14 5h4v14h-4"/><path d="M4 12h6"/>'), marker: P('<path d="M12 4l7 8-7 8-7-8z"/>'), play: P('<path d="M7 5v14l11-7z" fill="currentColor"/>'), pause: P('<path d="M8 5v14M16 5v14" stroke-width="3"/>'),
  stepback: P('<path d="M14 6l-6 6 6 6"/><path d="M6 6v12"/>'), stepfwd: P('<path d="M10 6l6 6-6 6"/><path d="M18 6v12"/>'), tostart: P('<path d="M18 6l-8 6 8 6zM6 6v12"/>'), toend: P('<path d="M6 6l8 6-8 6zM18 6v12"/>'),
  lift: P('<path d="M12 20V8M8 12l4-4 4 4"/><path d="M4 4h16"/>'), extract: P('<path d="M12 4v12M8 12l4 4 4-4"/><path d="M4 20h16"/>'), frame: P('<rect x="4" y="5" width="16" height="14" rx="1"/><path d="M4 9h16M8 5v4"/>'), safe: P('<rect x="3" y="5" width="18" height="14"/><rect x="6" y="8" width="12" height="8" stroke-dasharray="2 2"/>'),
  exact: P('<circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="2" fill="currentColor"/>'), multicam: P('<rect x="4" y="4" width="7" height="7"/><rect x="13" y="4" width="7" height="7"/><rect x="4" y="13" width="7" height="7"/><rect x="13" y="13" width="7" height="7"/>'),
  lock: P('<rect x="5" y="11" width="14" height="9" rx="1"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>'), unlock: P('<rect x="5" y="11" width="14" height="9" rx="1"/><path d="M8 11V7a4 4 0 0 1 7.5-2"/>'), sync: P('<path d="M12 4v16M8 8l4-4 4 4M8 16l4 4 4-4"/>'), eye: P('<path d="M2 12s4-6 10-6 10 6 10 6-4 6-10 6S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>'),
  eyeoff: P('<path d="M3 3l18 18M10 6.5A10 10 0 0 1 22 12s-1.5 2.3-4 4M6 8.5C3.5 10.3 2 12 2 12s4 6 10 6c1 0 2-.2 3-.5"/>'), mute: P('<path d="M4 10v4h4l5 4V6L8 10z"/><path d="M17 9l4 6M21 9l-4 6"/>'), solo: P('<path d="M4 10v4h4l5 4V6L8 10z"/><path d="M17 8a6 6 0 0 1 0 8"/>'),
  wrench: P('<path d="M14 6a4 4 0 0 0 5 5l-9 9-2-2z"/><path d="M14 6l4 4"/>'), plus: P('<path d="M12 5v14M5 12h14"/>'), undo: P('<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>'), redo: P('<path d="M15 14l5-5-5-5"/><path d="M20 9H10a6 6 0 0 0 0 12h3"/>'),
  bin: P('<path d="M4 7h16v12H4zM4 7l2-3h5l2 3"/>'), sequence: P('<path d="M4 6h16M4 12h10M4 18h16"/><rect x="15" y="10" width="5" height="4"/>'), grip: P('<path d="M9 5v14M15 5v14" stroke-dasharray="2 2"/>'), subclip: P('<path d="M4 8h16v10H4zM8 8V5h8v3"/><path d="M10 13h4"/>'),
};
const map = { tool: { select: "select", trackfwd: "trackfwd", trackback: "trackback", ripple: "ripple", roll: "roll", ratestretch: "ratestretch", razor: "razor", slip: "slip", slide: "slide", pen: "pen", rect: "rect", ellipse: "ellipse", hand: "hand", zoom: "zoom", type: "type" },
  id: { toolSnap: "snap", zoomOut: null, zoomIn: null, srcIn: "markin", srcOut: "markout", srcMarker: "marker", srcPlay: "play", srcStepBack: "stepback", srcStep: "stepfwd", srcFrame: "frame", prgIn: "markin", prgOut: "markout", btnMarker: "marker", prgToStart: "tostart", prgStepBack: "stepback", prgPlay: "play", prgStep: "stepfwd", prgToEnd: "toend", prgLift: "lift", prgExtract: "extract", prgFrame: "frame", prgSafe: "safe", prgExact: "exact", prgMulti: "multicam", btnAddTitle: "type", tlSnapInd: "snap", tlLinked: "sync", tlMarkerBtn: "marker", btnUndo: "undo", btnRedo: "redo", binNew: "bin", binNewSeq: "sequence", srcSubclip: "subclip" } };
function apply() {
  for (const [tool, ic] of Object.entries(map.tool)) { const b = document.querySelector(`[data-tool="${tool}"]`); if (b && I[ic]) { b.dataset.label = b.textContent; b.innerHTML = I[ic]; } }
  for (const [id, ic] of Object.entries(map.id)) { const b = document.getElementById(id); if (b && ic && I[ic]) { if (b.classList.contains("wide")) continue; b.dataset.label = b.textContent; b.innerHTML = I[ic]; } }
  document.querySelectorAll(".hd .grip").forEach(g => { g.innerHTML = ""; });
}
window.CR_ICONS = I; window.applyIcons = apply;
// track-header icons are rendered dynamically: expose helpers used by app.js renderTimeline
window.trackIcon = (kind) => I[kind] || "";
apply();
// re-apply play/pause glyph when transport state changes
const obs = new MutationObserver(() => { const b = document.getElementById("prgPlay"); if (!b) return; const txt = b.textContent.trim(); if (txt === "▶") b.innerHTML = I.play; else if (txt === "❚❚") b.innerHTML = I.pause; });
obs.observe(document.getElementById("prgPlay"), { childList: true, characterData: true, subtree: true });
})();

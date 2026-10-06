const vm = require('node:vm');
function section(source, from, to) { const a = source.indexOf(from), b = source.indexOf(to, a); if (a < 0 || b <= a) throw new Error('Missing source region: ' + from); return source.slice(a, b); }
class Element {
  constructor(tag, allocations) { this.tagName = tag; this.children = []; this.parentElement = null; this.style = {}; this.dataset = {}; this.className = ''; this.scrollLeft = 0; this.scrollTop = 0; this.allocations = allocations; allocations.count++; }
  appendChild(node) { if (node.parentElement) node.remove(); this.children.push(node); node.parentElement = this; return node; }
  insertBefore(node, before) { if (!before) return this.appendChild(node); const i = this.children.indexOf(before); this.children.splice(i < 0 ? this.children.length : i, 0, node); node.parentElement = this; return node; }
  remove() { if (this.parentElement) { const parent = this.parentElement; parent.children.splice(parent.children.indexOf(this), 1); this.parentElement = null; } }
  setAttribute(key, value) { if (key === 'class') this.className = value; else this[key] = value; }
  replaceChildren(...nodes) { for (const c of this.children) c.parentElement = null; this.children = []; nodes.forEach(n => this.appendChild(n)); }
  set innerHTML(html) {
    this.replaceChildren(); this.html = html;
    // Approximate element allocations for the small static fragments used by
    // renderTimeline. This is not HTML layout, paint, decoding, or a real DOM.
    for (const [, tag, attrs] of String(html).matchAll(/<(div|span|button|i)\b([^>]*)>/g)) {
      const node = new Element(tag, this.allocations); node.className = /class="([^"]*)"/.exec(attrs)?.[1] || '';
      this.appendChild(node);
    }
  }
  get innerHTML() { return this.html || ''; }
  set textContent(value) { this.text = String(value); this.replaceChildren(); }
  get textContent() { return this.text || ''; }
  get firstChild() { return this.children[0] || null; }
  get firstElementChild() { return this.firstChild; }
  matches(selector) {
    const track = /\[data-track="([^"]*)"\]/.exec(selector)?.[1];
    if (track !== undefined && this.dataset.track !== track) return false;
    const cls = /^\.([\w-]+)/.exec(selector)?.[1];
    return cls ? this.className.split(' ').includes(cls) : selector.startsWith('#') ? this.id === selector.slice(1) : this.tagName === selector;
  }
  querySelectorAll(selector) { return this.children.flatMap(node => [...(node.matches(selector) ? [node] : []), ...node.querySelectorAll(selector)]); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
function makeProject(seconds, tracks = 4) {
  const seq = { id: 's', name: 'Benchmark', width: 1920, height: 1080, fps: 30, captions: [], markers: [], tracks: [] };
  for (let j = 0; j < tracks; j++) {
    const tr = { id: 'V' + j, kind: 'video', index: j, clips: [] };
    for (let start = 0; start < seconds; start += 5) tr.clips.push({ id: j + '-' + start, start, in_: 0, out: Math.min(5, seconds - start), speed: 1, media_id: 'm' });
    seq.tracks.push(tr);
  }
  for (let start = 0; start < seconds; start += 3) seq.captions.push({ id: 'c' + start, start, end: Math.min(seconds, start + 2.5), text: 'Caption ' + start });
  for (let start = 0; start < seconds; start += 10) seq.markers.push({ id: 'm' + start, time: start, name: 'Marker ' + start });
  return { id: 'benchmark', media: { m: { id: 'm', has_audio: false } }, sequences: [seq], proposals: [] };
}
function createHarness(source, project, { scroll = 0, viewport = 1200, pps = 60 } = {}) {
  const allocations = { count: 0 }, nodes = {};
  for (const id of ['tlInner', 'heads', 'ruler', 'tlBody', 'tl', 'prgPlay']) { nodes[id] = new Element('div', allocations); nodes[id].id = id; }
  nodes.tlBody.scrollLeft = scroll; nodes.tlBody.clientWidth = viewport;
  const pending = new Map(), calls = [], requestFrames = [], S = { proj: project, seq: project.sequences[0], seqId: project.sequences[0].id,
    pps, sel: new Set(), tall: {}, target: { video: 'V0', audio: 'A0' }, tlopt: { linked: true, captions: true, ghosts: true, through: true }, tool: 'select', t: 0, playing: false, rate: 1, lastRaf: 0 };
  let frameId = 0, time = 0;
  const $ = id => nodes[id.slice(1)] || Object.values(nodes).map(node => node.querySelector(id)).find(Boolean) || null;
  const scope = { S, $, timing: require('../frontend/timeline-time.js'), document: { createElement: tag => new Element(tag, allocations) }, window: {},
    CR: { panels: { editMarker() {} }, showTab: name => calls.push(name) },
    fmtTC: t => '00:' + String(Math.floor(t / 60)).padStart(2, '0') + ':' + String(Math.floor(t % 60)).padStart(2, '0') + ':00',
    trackRows: () => S.seq.tracks, linkedAudioTrack: () => null,
    clipEl(c) { const el = new Element('div', allocations); el.className = 'clip'; el.dataset.clip = c.id; el.style.left = c.start * S.pps + 'px'; return el; },
    clipById(id) { for (const tr of S.seq.tracks) { const c = tr.clips.find(c => c.id === id); if (c) return { c, tr }; } return null; },
    positionPlayhead() {}, refreshRenderBar() {}, trackMetersTick() {}, renderProgram() { calls.push('render'); }, updatePlayhead() { calls.push('playhead'); },
    audioCtx() {}, status: (...args) => calls.push(args), api: { json: async () => ({ ok: true }) },
    setTimeout() {}, performance: { now: () => time },
    requestAnimationFrame(fn) { const id = ++frameId; pending.set(id, fn); requestFrames.push(id); return id; },
    cancelAnimationFrame(id) { pending.delete(id); },
  };
  try { scope.window.FilmocityTimeline = require('../frontend/timeline-window.js'); } catch (error) { if (error.code !== 'MODULE_NOT_FOUND') throw error; }
  vm.createContext(scope);
  const timing = section(source, 'function remapSegments(', 'function adoptTracks(');
  const timeline = section(source, 'function renderTimeline()', 'function positionAgentCursor()');
  const begin = source.includes('let playbackFrame =') ? 'let playbackFrame =' : 'function tick(';
  const playback = section(source, begin, '// ---------- source monitor');
  vm.runInContext(timing + '\n' + timeline + '\n' + playback + '\nglobalThis.durationOf = seqDurOf;', scope);
  scope.seekTo = t => { S.t = Math.max(0, t); scope.updatePlayhead(); };
  return { scope, S, nodes, allocations, calls, pending, requestFrames,
    elapse(milliseconds) { time += milliseconds; },
    render() { allocations.count = 0; scope.renderTimeline(); return allocations.count; },
    advance(milliseconds) { time += milliseconds; const jobs = [...pending.entries()]; for (const [id, fn] of jobs) { pending.delete(id); fn(time); } },
    elements(cls) { return [...nodes.tlInner.querySelectorAll('.' + cls), ...nodes.ruler.querySelectorAll('.' + cls)]; },
  };
}
module.exports = { createHarness, makeProject };

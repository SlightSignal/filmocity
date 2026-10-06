// Production gesture callbacks and save queue, with controlled DOM/network adapters.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const sync = require('../../frontend/project-sync.js');
const gestures = require('../../frontend/edit-gesture.js');
const timing = require('../../frontend/timeline-time.js');
const audioPreview = require('../../frontend/audio-preview.js');
const clipEdits = require('../../frontend/clip-split.js');
const timelineRange = require('../../frontend/timeline-range.js');
const timelineAnnotations = require('../../frontend/timeline-annotations.js');
const source = fs.readFileSync(require('node:path').join(__dirname, '../../frontend/app.js'), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
function section(start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert.ok(a >= 0 && b > a, `Missing production section: ${start}`);
  return source.slice(a, b);
}
function node() {
  const listeners = new Map();
  return {
    style: {}, dataset: {}, classList: { contains: () => false }, width: 600, height: 300,
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 600, height: 300 }),
    addEventListener(type, callback, capture) { if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push({ callback, capture }); },
    removeEventListener(type, callback, capture) { listeners.set(type, (listeners.get(type) || []).filter(x => x.callback !== callback || x.capture !== capture)); },
    emit(type, event = {}) { for (const x of [...(listeners.get(type) || [])]) x.callback(event); },
    callbacks(type) { return (listeners.get(type) || []).map(x => x.callback); },
    get listenerCount() { return [...listeners.values()].reduce((n, xs) => n + xs.length, 0); },
  };
}
function event(extra = {}) { return { clientX: 0, clientY: 0, button: 0, buttons: 1, target: node(), stopPropagation() {}, preventDefault() {}, stopImmediatePropagation() {}, ...extra }; }
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
const context = (revision = 'r0', project = 'folder-a') => ({ workspace: 'workspace', project, revision });
const response = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
const saved = (revision = 'r1', extra = {}) => response(200, { ok: true, context: context(revision), ...extra });
const read = (project, revision = 'r0') => response(200, { project: plain(project), context: context(revision) });
async function until(predicate) { for (let i = 0; i < 100 && !predicate(); i++) await Promise.resolve(); assert.ok(predicate(), 'Expected asynchronous work did not advance'); }
function fixture() {
  const requests = [], storage = new Map(), messages = [], nodes = new Map();
  const localStorage = { get length() { return storage.size; }, key: i => [...storage.keys()][i], setItem: (k, v) => storage.set(k, v), getItem: k => storage.get(k) ?? null, removeItem: k => storage.delete(k) };
  const clips = ['a', 'b', 'c'].map((id, i) => ({ id, media_id: 'm', start: 3 * i, in_: 5, out: 8, speed: 1 }));
  const tr = { id: 'v1', kind: 'video', index: 0, clips };
  const seq = { id: 's1', width: 600, height: 300, fps: 30, tracks: [tr], markers: [{ time: 1 }, { time: 4 }] };
  const project = { id: 'document', media: { m: { duration: 100 } }, sequences: [seq, { ...plain(seq), id: 's2' }] };
  const window = Object.assign(node(), { localStorage, FilmocityTime: timing, FilmocitySourceClock: require('../../frontend/source-clock.js'), FilmocitySync: sync, FilmocityGestures: gestures, FilmocityAudioPreview: audioPreview, FilmocityClipSplit: clipEdits, FilmocityTimelineRange: timelineRange, FilmocityTimelineAnnotations: timelineAnnotations });
  const document = Object.assign(node(), { hidden: false, elementsFromPoint: () => [], querySelector: () => null });
  const scope = {
    S: { proj: project, seq, seqId: seq.id, context: context(), t: 1, hist: [], histLabels: [], hist_i: -1, sel: new Set(['a']), tool: 'select', pps: 60 },
    timing, CLIENT: 'test-client', CR: { panels: { render() {} }, showTab() {} }, window, document,
    $: selector => { if (!nodes.has(selector)) nodes.set(selector, node()); return nodes.get(selector); },
    status: message => messages.push(message), renderAll() {}, renderTimeline() {}, renderProgram() {}, refreshSel() {}, hideSnap() {}, showSnap() {}, trimReadout() {}, autoScroll() {},
    frame: () => timing.fromFrames(1, seq.fps), clipDur: c => (c.out - c.in_) / (c.speed || 1), clipEnd: c => c.start + (c.out - c.in_) / (c.speed || 1), snapT: t => t,
    uid: () => 'new-shape', fmtTC: t => String(t), monitorHit: () => scope.hit, togglePlay: playing => { scope.S.playing = playing; },
    selectedClips: () => scope.S.seq.tracks.flatMap(tr => tr.clips.filter(c => scope.S.sel.has(c.id)).map(c => ({ c, tr }))),
    fetch(url, options) { const pending = deferred(); requests.push({ url, options, ...pending }); return pending.promise; },
  };
  vm.createContext(scope);
  vm.runInContext('(() => {\n' + section('function remapSegments(', 'const seqDurOf') + '\nObject.assign(globalThis, { clipDur, clipEnd, sourceOffset, speedAt, bezierY }); })();', scope);
  vm.runInContext([
    section('function seekPreviewPicture(', 'function activeClipsOf('),
    section('function mediaRate(', 'function stepSourceFrame('),
    section('const deep =', 'const linkedAudioTrack ='),
    section('const kfVal =', '// ---------- sync ----------'),
    section('// ---------- sync ----------', '// ---------- render ----------'),
    section('function snapT(', 'function showSnap('),
    section('function timelineRangeHooks(', 'function insertFromSource('),
    section('function startDrag(', 'function marqueeDrag('),
    section('function snapToGuides(', 'function monitorHit('),
    section('function canvasDrag(', 'function canvasWheel('),
    'globalThis.testApi = api; globalThis.evaluateKeyframes = kfVal;',
  ].join('\n'), scope);
  return { scope, requests, storage, messages, window, document, tr, clips, project, seq,
    start(handle, index = 0, ev = event()) { scope.S.sel = new Set([clips[index].id]); scope.startDrag(ev, clips[index], tr, handle, node()); },
    move(extra) { window.emit('mousemove', event(extra)); },
    up(extra) { window.emit('mouseup', event(extra)); },
    escape() { window.emit('keydown', event({ key: 'Escape' })); },
    body(index = 0) { return JSON.parse(requests[index].options.body); },
  };
}


module.exports = { fixture, event, node, plain, until, saved, read, response, context, section };

// Production placement and clipboard wrappers with actual planner/save queue.
const fs = require('node:fs'), vm = require('node:vm');
const base = require('./gesture-fixture.cjs');
function fixture() {
  const f = base.fixture(), s = f.scope; let id = 0; s.uid = () => `placed-${++id}`;
  s.window.FilmocityTimelineRange = require('../../frontend/timeline-range.js');
  s.window.FilmocityTimelineAnnotations = require('../../frontend/timeline-annotations.js');
  s.S.prefs = { still: 5 }; s.S.target = { video: 'v1', audio: 'a1' }; s.S.focus = 'timeline'; s.updatePlayhead = () => {};
  Object.assign(f.project.media.m, { id: 'm', path: '/fixtures/source.mov', has_video: true, has_audio: true });
  vm.runInContext(base.section('const seqDurOf =', 'const frame =') + '\n' +
    base.section('function timelineRangeHooks(', '// ---------- editing commands ----------') + '\n' +
    base.section('function cutSel(', 'function selectMatchingLabel(') + '\n' +
    base.section('let clipboard =', 'function pasteAttributes(') + '\n' +
    'globalThis.readClipboard = () => ({ items: deep(clipboard), owner: deep(clipboardOwner) });', s);
  f.copy = ids => { s.S.sel = new Set(ids); return s.copySel(); };
  f.place = (options = {}) => s.placeMedia(options.mediaId || (typeof options.media === 'string' ? options.media : 'm'), options.track || 'v1', options.at ?? 1, options.in_ ?? 0, options.out ?? 1, options.mode || 'overwrite', 'placement fixture', options.options || {});
  f.save = async (index = 0) => { await base.until(() => f.requests.length > index); f.requests[index].resolve(base.saved('r' + (index + 1))); await s.flushSaves(); };
  f.dragUI = () => {
    const media = Object.keys(s.S.proj.media).map(id => Object.assign(base.node(), { dataset: { id } }));
    const sequences = s.S.proj.sequences.map(sq => Object.assign(base.node(), { dataset: { seq: sq.id } }));
    const bins = [{ id: '' }, ...(s.S.proj.bins || [])].map(b => Object.assign(base.node(), { dataset: { bin: b.id } }));
    s.$$ = selector => ({ '.media[data-id]': media, '.media[data-seq]': sequences, '[data-bin]': bins }[selector] || []); s.S.binSel = new Set();
    vm.runInContext(base.section('function wireBin(', 'function trackRows(') + '\n' +
      base.section('  $("#srcDragV").ondragstart', '  $$("[data-trim]")') + '\n' +
      '(() => {\n' + base.section('  const scr = $("#prgScreen"); scr.ondragover', '  $("#prgGrid").onclick') + '\n})();\n' +
      'globalThis.bindDragRow = (row, tr) => {\n' + base.section('const rowProject =', 'const row = document.createElement') +
      base.section('row.ondragover =', 'row.oncontextmenu =') + '\n};', s);
    s.wireBin(base.node()); s.xToT = x => x; s.snapT = x => x;
    return { media, sequences, bins, video: s.$('#srcDragV'), audio: s.$('#srcDragA'), program: s.$('#prgScreen'), row(track = f.tr) { const row = base.node(); s.bindDragRow(row, track); return row; } };
  };

  return f;
}
function dragEvent(extra = {}) {
  const data = new Map();
  return base.event({ prevented: false, preventDefault() { this.prevented = true; }, dataTransfer: {
    get types() { return [...data.keys()]; }, setData(key, value) { data.set(key, String(value)); }, getData: key => data.get(key) || '', clearData: () => data.clear(),
  }, ...extra });
}
module.exports = { ...base, fixture, dragEvent };
if (require.main === module) {
  const input = JSON.parse(fs.readFileSync(0, 'utf8') || '{}'), f = fixture();
  if (input.tracks) f.seq.tracks = input.tracks; if (input.media) f.project.media = input.media; if (input.fps) f.seq.fps = input.fps;
  if (input.markers) f.seq.markers = input.markers; if (input.captions) f.seq.captions = input.captions;
  if (input.copy) f.copy(input.copy); const before = base.plain(f.project); f.scope.S.t = input.at ?? 1;
  if (input.command) f.scope[input.command](); else f.place(input);
  process.stdout.write(JSON.stringify({ before, body: f.requests.length ? f.body() : null, optimistic: base.plain(f.project), messages: f.messages, selection: [...f.scope.S.sel] }));
}

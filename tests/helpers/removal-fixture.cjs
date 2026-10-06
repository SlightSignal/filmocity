// Production removal commands and range planning with the actual guarded save queue.
// DOM/request adapters are controlled; no native input or HTTP server is implied.
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const base = require('./cut-fixture.cjs');
const source = fs.readFileSync(path.join(__dirname, '../../frontend/app.js'), 'utf8');
function section(a, b) { const start = source.indexOf(a), end = source.indexOf(b, start); if (start < 0 || end < 0) throw Error('Missing ' + a); return source.slice(start, end); }
function fixture(options = {}) {
  const f = base.fixture(), s = f.scope;
  s.window.FilmocityTimelineRange = require('../../frontend/timeline-range.js');
  s.window.FilmocityTimelineAnnotations = require('../../frontend/timeline-annotations.js');
  s.S.binSel = new Set(); s.S.focus = 'timeline'; s.seekTo = t => { s.S.t = t; };
  s.ACTIONS = { bin_delete: ['', '', () => { f.binDeletes = (f.binDeletes || 0) + 1; }] };
  // Root integration provides these production range helpers as one section.
  vm.runInContext(section('function timelineRangeHooks(', 'function insertFromSource(') + '\n' +
    section('function rippleMarkers(', 'function applyTransition(') + '\n' +
    section('function liftExtract(', 'let clipboard ='), s);
  if (options.override) vm.runInContext(options.override, s);
  f.select = (...ids) => { s.S.sel = new Set(ids); };
  f.gap = (before, after, tr = f.tr) => { s.S.sel.clear(); s.S.gap = { track: tr.id, start: s.cutClock.duration(before) + before.start, end: after.start, before: before.id, after: after.id, project: f.project, sequence: f.seq }; return s.S.gap; };
  f.save = async (index = 0) => { await base.until(() => f.requests.length > index); f.requests[index].resolve(base.saved('r' + (index + 1))); await base.until(() => !s.projectSaveState().pending); };
  return f;
}
module.exports = { ...base, fixture };
if (require.main === module) {
  const input = JSON.parse(fs.readFileSync(0, 'utf8')), f = fixture({ override: input.override });
  if (input.sequence) { Object.assign(f.seq, input.sequence); f.seq.id = input.sequence.id || 's1'; f.scope.S.seqId = f.seq.id; }
  if (input.media) f.project.media = input.media;
  f.select(...(input.selection || [])); f.scope.S.t = input.time || 0;
  if (input.gap) { const tr = f.seq.tracks.find(t => t.id === input.gap.track); f.gap(tr.clips.find(c => c.id === input.gap.before), tr.clips.find(c => c.id === input.gap.after), tr); }
  const before = base.plain(f.project); f.scope[input.command](...(input.args || []));
  Promise.resolve().then(() => Promise.resolve()).then(() => process.stdout.write(JSON.stringify({ before, body: f.requests.length ? f.body() : null, optimistic: base.plain(f.project), messages: f.messages, selection: [...f.scope.S.sel], time: f.scope.S.t })));
}

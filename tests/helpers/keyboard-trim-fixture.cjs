// Production keyboard trim/nudge commands + source clocks and actual guarded save queue.
// DOM, media and HTTP replies are controlled; native decoding/keyboard dispatch is separate QA.
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const base = require('./cut-fixture.cjs');
const source = fs.readFileSync(path.join(__dirname, '../../frontend/app.js'), 'utf8');
function section(a, b) { const start = source.indexOf(a), end = source.indexOf(b, start); if (start < 0 || end < 0) throw Error('Missing ' + a); return source.slice(start, end); }
function fixture(options = {}) {
  const f = base.fixture(), s = f.scope;
  s.seekTo = t => { s.S.t = t; };
  vm.runInContext(section('const seqDurOf =', 'const frame =') + '\n' +
    section('function keyboardEditReady(', 'function zoomToFit(') + '\n' +
    section('function trimByType(', 'function addClipMarker(') + '\n' +
    section('function nudge(', 'function applyAudioTransition(') + '\n' +
    section('function rippleTrimToPlayhead(', 'function toggleLink('), s);
  if (options.override) vm.runInContext(options.override, s);
  f.edit = (clip, side = 'l', fps = 30) => { const installed = f.install(clip, fps); s.S.editPoint = { clipId: installed.id, side }; s.S.sel = new Set([installed.id]); return installed; };
  f.save = async (index = 0) => { await base.until(() => f.requests.length > index); f.requests[index].resolve(base.saved('r' + (index + 1))); await base.until(() => !s.projectSaveState().pending); };
  return f;
}
module.exports = { ...base, fixture };
if (require.main === module) { const input = JSON.parse(fs.readFileSync(0, 'utf8')), f = fixture({ override: input.override }); f.seq.tracks = input.tracks; f.seq.fps = input.fps || 30; if (input.media) f.project.media = input.media; f.scope.S.editPoint = input.editPoint; f.scope.S.sel = new Set(input.selection || []); f.scope.S.t = input.time || 0; if (input.trimType) f.scope.S.trimType = input.trimType; f.scope[input.command](...(input.args || [])); Promise.resolve().then(() => Promise.resolve()).then(() => process.stdout.write(JSON.stringify({ request: f.requests.length ? f.body() : null, tracks: f.seq.tracks, messages: f.messages }))); }

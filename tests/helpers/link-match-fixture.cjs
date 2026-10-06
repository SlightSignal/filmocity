// Production link/group/Match Frame commands and guarded save queue.
// DOM, media seeking and HTTP transport are controlled; Windows decoding is separate QA.
const fs = require('node:fs'), vm = require('node:vm');
const base = require('./gesture-fixture.cjs');
function fixture(options = {}) {
  const f = base.fixture(), s = f.scope; let next = 0;
  s.uid = () => `link-${++next}`; s.S.target = { video: 'v1', audio: 'a1' }; s.S.focus = 'timeline';
  Object.assign(f.project.media.m, { id: 'm', path: '/fixtures/source.mov', has_video: true, has_audio: true, fps: 30 });
  const mediaNode = s.$('#srcVideo'); mediaNode.currentTime = 0; mediaNode.duration = 100; mediaNode.pause = () => { mediaNode.paused = true; };
  s.updateSrcIO = () => {}; s.loadSource = id => { s.S.src = s.S.proj.media[id]; s.S.srcIn = s.S.srcOut = null; s.S.focus = 'source'; };
  s.seekTo = t => { s.S.t = t; return true; }; s.setFocus = value => { s.S.focus = value; };
  s.navigateTo = t => { s.togglePlay(false, { commitTrim: false }); s.S.t = t; return true; };
  vm.runInContext(base.section('const linkedAudioTrack =', 'const kfVal =') + '\n' +
    base.section('function groupSel(', 'function setFit(') + '\n' +
    base.section('function reverseMatchFrame(', 'function distributeSel(') + '\n' +
    base.section('function matchFrame(', 'function nudge(') + '\n' +
    base.section('function toggleLink(', 'function setZoom('), s);
  if (options.override) vm.runInContext(options.override, s);
  f.select = (...ids) => { s.S.sel = new Set(ids); };
  f.save = async (index = 0) => { await base.until(() => f.requests.length > index); f.requests[index].resolve(base.saved('r' + (index + 1))); await s.flushSaves(); };
  f.mediaNode = mediaNode;
  return f;
}
module.exports = { ...base, fixture };
if (require.main === module) {
  const input = JSON.parse(fs.readFileSync(0, 'utf8') || '{}'), f = fixture({ override: input.override });
  if (input.sequence) Object.assign(f.seq, input.sequence);
  if (input.media) f.project.media = input.media;
  f.select(...(input.selection || [])); f.scope.S.t = input.time || 0;
  if (input.source) { f.scope.S.src = f.project.media[input.source]; f.mediaNode.currentTime = input.sourceTime || 0; }
  const before = base.plain(f.project);
  f.scope[input.command || 'toggleLink'](...(input.args || []));
  Promise.resolve().then(() => Promise.resolve()).then(() => process.stdout.write(JSON.stringify({ before, body: f.requests.length ? f.body() : null, optimistic: base.plain(f.project), selection: [...f.scope.S.sel], time: f.scope.S.t, sourceTime: f.mediaNode.currentTime, messages: f.messages })));
}

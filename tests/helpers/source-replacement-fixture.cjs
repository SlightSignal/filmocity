// Actual Source/Project replacement callbacks and guarded save lifecycle.
const fs = require('node:fs'), vm = require('node:vm'), base = require('./placement-fixture.cjs');
const replacement = require('../../frontend/source-replacement.js');
function fixture() {
  const f = base.fixture(), s = f.scope;
  s.window.FilmocitySourceReplacement = replacement;
  s.FilmocityProxyPreview = { replaceSource() {} }; s.sourceMediaUrl = media => media.path; s.sourcePreviewError = () => {}; s.updateSourcePreviewState = () => {};
  s.window.FilmocityWorkflowTransaction = require('../../frontend/workflow-transaction.js');
  Object.assign(f.project.media.m, { fps: 30 });
  f.project.media.n = { id: 'n', path: '/fixtures/replacement.mov', duration: 40, has_video: true, has_audio: true, fps: 30 };
  s.S.src = f.project.media.n; s.S.srcIn = null; s.S.srcOut = null; s.S.binSel = new Set(['n']); s.$('#srcVideo').currentTime = 0;
  vm.runInContext(base.section('function sourceReplacementMediaChain(', 'function setLabel('), s);
  s.CR.replaceFromBinSource = s.replaceFromBinSource;
  const panels = fs.readFileSync(require('node:path').join(__dirname, '../../frontend/panels.js'), 'utf8');
  vm.runInContext(panels.slice(panels.indexOf('function replaceFromBin('), panels.indexOf('function renameClip(', panels.indexOf('function replaceFromBin('))), s);
  f.plan = (c = f.tr.clips[0], media = f.project.media.n, options = {}) => replacement.replace(c, media,
    { duration: s.clipDur(c), sourceOffset: t => s.sourceOffset(c, t), speedAt: t => s.speedAt(c, t) }, { trackKind: f.tr.kind, ...options });
  f.reply = (i, value, status = 200) => f.requests[i].resolve(base.response(status, value));
  return f;
}
module.exports = { ...base, fixture, replacement };
if (require.main === module) (async () => {
  const input = JSON.parse(fs.readFileSync(0, 'utf8') || '{}'), f = fixture(), s = f.scope;
  if (input.sequence) { f.project.sequences = [input.sequence]; s.S.seq = input.sequence; s.S.seqId = input.sequence.id; }
  if (input.media) f.project.media = input.media;
  const clip = s.S.seq.tracks.flatMap(tr => tr.clips).find(c => c.id === (input.clipId || 'a'));
  s.S.sel = new Set([clip.id]); s.S.src = f.project.media[input.mediaId || 'n']; s.S.binSel = new Set([input.mediaId || 'n']); s.S.srcIn = input.in ?? null; s.S.srcOut = input.out ?? null; s.$('#srcVideo').currentTime = input.nativeTime ?? 0;
  const before = base.plain(f.project); s[input.origin === 'bin' ? 'replaceFromBin' : 'replaceWithSource']();
  for (let i = 0; i < 100 && !f.requests.length; i++) await Promise.resolve();
  process.stdout.write(JSON.stringify({ before, body: f.requests.length ? f.body() : null, messages: f.messages, optimistic: base.plain(f.project), planned: f.requests.length ? f.plan(clip, s.S.src, { sourceIn: f.body().in, sourceOut: f.body().out, trackKind: s.S.seq.tracks.find(tr => tr.clips.includes(clip)).kind }) : null }));
})();

// Production Render and Replace menu/queue/review/apply with real save/context guards.
const vm = require('node:vm'), fs = require('node:fs'), path = require('node:path');
const base = require('./placement-fixture.cjs');
function fixture() {
  const f = base.fixture(), s = f.scope;
  s.window.FilmocityWorkflowTransaction = require('../../frontend/workflow-transaction.js');
  s.crypto = require('node:crypto').webcrypto;
  let opened = 0; s.window.FilmocityTasks = { open() { opened++; } }; f.opened = () => opened;
  vm.runInContext(base.section('const RENDER_REPLACE_REVIEWS =', 'function seqFromClip('), s);
  s.CR.startRenderReplace = s.startRenderReplace;
  const panels = fs.readFileSync(path.join(__dirname, '../../frontend/panels.js'), 'utf8');
  vm.runInContext(panels.slice(panels.indexOf('async function renderReplace()'), panels.indexOf('function replaceFromBin(')), s);
  f.reply = (index, value, status = 200) => f.requests[index].resolve(base.response(status, value));
  f.review = async (patch = {}) => {
    const promise = s.reviewRenderReplaceTask('render-task'), index = f.requests.length;
    await base.until(() => f.requests.length > index);
    f.reply(index, { ok: true, task: { id: 'render-task', kind: 'render_replace', status: 'ready' }, context: { ...s.S.context },
      result: { version: 1, kind: 'render_replace', sequence: s.S.seq.id, clip_id: 'a', track_id: 'v1', duration: 3, media: { id: 'baked', path: '/fixtures/baked.mkv' } },
      plan: { ops: [{ op: 'set_clip', sequence: 's1', track: 'v1', clip: { id: 'a', media_id: 'baked' } }], fingerprint: 'render-fingerprint', summary: { kind: 'render_replace', message: 'Bake picture; retain clip audio processing and outer transitions', format: 'FFV1 BGRA + float PCM', duration: 3 } }, ...patch });
    return promise;
  };
  return f;
}
module.exports = { ...base, fixture };

// Actual menu/task adapters with production save/transaction/context guards.
const vm = require('node:vm'), base = require('./placement-fixture.cjs');
function fixture() {
  const f = base.fixture(), s = f.scope;
  s.window.FilmocityWorkflowTransaction = require('../../frontend/workflow-transaction.js');
  let opened = 0; s.window.FilmocityTasks = { open() { opened++; } }; f.opened = () => opened;
  s.prompt = () => '.35'; s.crypto = require('node:crypto').webcrypto;
  vm.runInContext(base.section('const ANALYSIS_REVIEWS =', 'function seqFromClip(') + '\n' + base.section('async function removeSilences(', 'function autoPunchIns('), s);
  f.reply = (index, value, status = 200) => f.requests[index].resolve(base.response(status, value));
  f.review = async (patch = {}) => {
    const promise = s.reviewAnalysisTask('task'), index = f.requests.length;
    await base.until(() => f.requests.length > index);
    f.reply(index, { ok: true, task: { id: 'task', kind: 'analysis', status: 'ready' }, context: { ...s.S.context }, result: { version: 1, clock: 'media', kind: 'scenes', media_id: 'm', sequence: s.S.seq.id, clip_id: 'a' }, plan: { ops: [{ op: 'set', path: '/sequences/0/tracks/0/clips', value: [] }], fingerprint: 'review-fingerprint', summary: { message: 'review', tracks: ['v1'], cuts: [1] } }, ...patch });
    return promise;
  };
  return f;
}
module.exports = { ...base, fixture };

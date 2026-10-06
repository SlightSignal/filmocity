// Actual synchronization queue/review/apply and sequence dialogs with real save guards.
const vm = require('node:vm'), fs = require('node:fs'), path = require('node:path');
const base = require('./placement-fixture.cjs');
function fixture({ override = '' } = {}) {
  const f = base.fixture(), s = f.scope;
  s.window.FilmocityWorkflowTransaction = require('../../frontend/workflow-transaction.js'); s.crypto = require('node:crypto').webcrypto;
  let opened = 0; s.window.FilmocityTasks = { open() { opened++; } }; f.opened = () => opened;
  f.tr.clips = [{ ...f.tr.clips[0], start: 10 }];
  f.second = { id: 'v2', kind: 'video', index: 1, clips: [{ id: 'b', media_id: 'n', start: 9, in_: 0, out: 3, speed: 1 }] };
  f.seq.tracks.push(f.second); s.S.sel = new Set(['a', 'b']);
  Object.assign(f.project.media.m, { name: 'Camera one', frame_rate: '30000/1001', fps: 29.97, width: 640, height: 360 });
  f.project.media.n = { ...base.plain(f.project.media.m), id: 'n', name: 'Camera two', path: '/fixtures/second.mov', duration: 20 };
  vm.runInContext(base.section('const SYNC_REVIEWS =', 'function finishPolygon(') + '\n' + base.section('async function createMulticam(', 'function multicamAt('), s);
  Object.assign(s.CR, { startSyncTask: s.startSyncTask, waitSyncTask: s.waitSyncTask, reviewSyncTask: s.reviewSyncTask, applySyncTask: s.applySyncTask, createSyncSequence: s.createSyncSequence, previewSyncSequence: s.previewSyncSequence, createMulticam: s.createMulticam });
  f.timers = []; s.setTimeout = callback => { f.timers.push(callback); return f.timers.length; }; f.tick = () => f.timers.shift()?.();
  for (const selector of ['#dlgMulticam', '#dlgMerge']) {
    const classes = new Set(); s.$(selector).classList = { add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x) };
  }
  s.openDlg = id => s.$(id).classList.add('open'); s.closeDlg = id => s.$(id).classList.remove('open');
  s.$('#mcName').value = 'My multicam'; s.$('#mcSync').value = 'audio'; s.$('#mcAudio').value = 'A1'; s.$('#mgName').value = 'My merged clip'; s.S.binSel = new Set(['m', 'n']);
  const panels = fs.readFileSync(path.join(__dirname, '../../frontend/panels.js'), 'utf8');
  vm.runInContext(panels.slice(panels.indexOf('function syncDialogReview('), panels.indexOf('async function saveGraphicTemplate(')) + '\n' + panels.slice(panels.indexOf('function mergeDialog('), panels.indexOf('async function newItem(')), s);
  if (override) vm.runInContext(override, s);
  f.reply = (index, value, status = 200) => f.requests[index].resolve(base.response(status, value));
  f.queued = () => ({ ok: true, task: { id: 'sync-task', kind: 'sync', status: 'queued', context: { ...s.S.context } }, context: { ...s.S.context } });
  f.result = (mode = 'timeline') => {
    const timeline = mode === 'timeline', ids = timeline ? ['a', 'b'] : ['m', 'n'];
    return { ok: true, task: { id: 'sync-task', kind: 'sync', status: 'ready', context: { ...s.S.context } }, context: { ...s.S.context },
      result: { version: 1, kind: 'sync', mode, clock: 'timeline-local', sequence: timeline ? s.S.seq.id : null, clip_ids: timeline ? ids : [], media_ids: ['m', 'n'], reference: ids[0], offsets: { [ids[0]]: 0, [ids[1]]: -.2375 },
        matches: ids.map((id, i) => ({ id, media_id: ['m', 'n'][i], offset: i ? -.2375 : 0, correlation: .93, runner_up: .2, overlap_seconds: 2.5, confidence: .9, resolution: i ? .000125 : 0, method: i ? 'pcm' : 'reference' })), warnings: ['Verify alignment by listening.'] },
      plan: { fingerprint: 'sync-fingerprint', summary: { kind: 'sync', message: 'Review measured offsets before applying.' }, ops: timeline ? [{ op: 'set_clip', sequence: s.S.seq.id, track: 'v2', clip: { id: 'b', start: 9.7625 } }] : [] } };
  };
  f.review = async (mode = 'timeline', edit = null) => {
    const at = f.requests.length, promise = s.reviewSyncTask('sync-task'); await base.until(() => f.requests.length > at);
    const reply = f.result(mode); if (edit) edit(reply); f.reply(at, reply); return promise;
  };
  f.queue = async (mode = 'timeline') => {
    const at = f.requests.length, promise = s.startSyncTask(mode === 'timeline' ? { sequence: s.S.seq.id, clip_ids: ['a', 'b'] } : { media_ids: ['m', 'n'] });
    await base.until(() => f.requests.length > at); f.reply(at, f.queued()); return promise;
  };
  f.catalog = (status = 'ready') => ({ context: { ...s.S.context }, tasks: [{ id: 'sync-task', kind: 'sync', context: { ...s.S.context }, status, message: status === 'error' ? 'Ambiguous correlation; no alignment available.' : '' }] });
  f.completeCreate = async (pending, at, refreshed = null) => {
    const body = f.body(at), project = base.plain(f.project);
    for (const op of body.ops) if (op.op === 'insert' && op.path.startsWith('/sequences/')) project.sequences.push(base.plain(op.value));
    f.reply(at, { ok: true, context: base.context('r1') }); await base.until(() => f.requests.length === at + 2);
    f.requests[at + 1].resolve(base.read(refreshed || project, 'r1')); await pending; return body;
  };
  return f;
}
module.exports = { ...base, fixture };

/* Controlled Effect Controls transport checks; native acceptance is separate. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../frontend/extras.js'), 'utf8');
const start = source.indexOf('let stabilizationPending = false;');
const end = source.indexOf('\nfunction videoFx', start);
assert(start >= 0 && end > start);

function fixture() {
  const clip = {media_id: 'm'}, track = {}, button = {isConnected: true};
  const project = {media: {m: {stab_trf: 'old.trf'}}}, sequence = {id: 's'};
  const context = {workspace: 'w', project: 'p', revision: 'r'};
  const state = {}, calls = [], messages = [];
  const S = {proj: project, seq: sequence, context};
  const CR = {CLIENT: 'human-test', canEdit: () => true,
    propertyTarget: () => () => button.isConnected && S.proj === project && S.seq === sequence,
    flushSaves: async () => state, projectSaveState: () => state,
    workflowRequest: async (request, basis) => {
      assert.deepEqual(JSON.parse(JSON.stringify(basis)), {...context, sequence: 's'});
      return request(context);
    }};
  const api = {json: async (method, path, body) => {
    calls.push({method, path, body: JSON.parse(JSON.stringify(body))});
    if (path.endsWith('/inspect')) return {ok: true, kind: 'stabilization', context: {...context},
      requested_media_id: 'm', media_id: 'm', settings: {shakiness: 5, force: true}, fingerprint: 'a'.repeat(64),
      source: {path: 'p', stamp: [['p','1','2','3']], sha256: 'd'.repeat(64)}, analyzer: {sha256: 'e'.repeat(64)}};
    return {ok: true, kind: 'stabilization', requested_media_id: 'm', media_id: 'm', cached: false, changed: true,
      analysis: {source: {path: 'p', stamp: [['p','1','2','3']], sha256: 'd'.repeat(64)}, settings: {shakiness: 5},
        analyzer: {sha256: 'e'.repeat(64)}, owner: {workspace: 'w', project: 'p'}}, warning: ''};
  }};
  const status = (...args) => messages.push(args);
  vm.runInNewContext(source.slice(start, end), {CR, S, api, status});
  return {CR, S, clip, track, button, state, calls, messages, api};
}

(async () => {
  {
    const f = fixture(); await f.CR.analyzeStabilization(f.clip, f.track, f.button);
    assert.equal(f.calls.length, 2); assert.equal(f.calls[0].path, '/api/stabilize/inspect');
    assert.equal(f.calls[1].path, '/api/stabilize'); assert.equal(f.calls[1].body.force, true);
    assert.equal(f.calls[1].body.fingerprint, 'a'.repeat(64)); assert.equal(f.button.disabled, false);
  }
  {
    const f = fixture(); f.CR.flushSaves = async () => {f.S.proj = {}; return f.state;};
    assert.equal(await f.CR.analyzeStabilization(f.clip, f.track, f.button), false);
    assert.equal(f.calls.length, 0);
  }
  {
    const f = fixture(), original = f.api.json;
    f.api.json = async (...args) => {const reply = await original(...args); f.S.seq = {}; return reply;};
    assert.equal(await f.CR.analyzeStabilization(f.clip, f.track, f.button), false);
    assert.equal(f.calls.length, 1);
  }
  {
    const f = fixture(), original = f.api.json;
    f.api.json = async (...args) => {const reply = await original(...args); reply.context.project = 'foreign'; return reply;};
    assert.equal(await f.CR.analyzeStabilization(f.clip, f.track, f.button), false);
    assert.equal(f.calls.length, 1);
  }
  {
    const f = fixture(); let release;
    f.CR.flushSaves = () => new Promise(resolve => {release = () => resolve(f.state);});
    const pending = f.CR.analyzeStabilization(f.clip, f.track, f.button);
    assert.equal(await f.CR.analyzeStabilization(f.clip, f.track, f.button), false);
    release(); await pending; assert.equal(f.calls.length, 2);
  }
  {
    const f = fixture(); f.CR.workflowRequest = async () => {throw new Error('Outcome not confirmed. Open Recovery; do not repeat.');};
    assert.equal(await f.CR.analyzeStabilization(f.clip, f.track, f.button), false);
    assert.equal(f.calls.length, 1); assert.match(f.messages.at(-1)[0], /Recovery/);
  }
  {
    const f = fixture(), original = f.api.json;
    f.api.json = async (...args) => {const reply = await original(...args); if (!args[1].endsWith('/inspect')) reply.media_id = 'foreign'; return reply;};
    assert.equal(await f.CR.analyzeStabilization(f.clip, f.track, f.button), false);
    assert.equal(f.calls.length, 2); assert.match(f.messages.at(-1)[0], /did not confirm/);
  }
  console.log('7 stabilization frontend transport checks passed; native acceptance is separate.');
})().catch(error => {console.error(error); process.exitCode = 1;});

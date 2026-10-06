// Actual editorial callbacks, save queue, command receipt/reload, controlled DOM/network.
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const base = require('./gesture-fixture.cjs');
const panels = fs.readFileSync(path.join(__dirname, '../../frontend/panels.js'), 'utf8');
const part = (a, b) => panels.slice(panels.indexOf(a), panels.indexOf(b, panels.indexOf(a)));
function fixture(options = {}) {
  const f = base.fixture(), s = f.scope;
  s.window.FilmocityWorkflowTransaction = require('../../frontend/workflow-transaction.js');
  Object.assign(f.project.media.m, { id: 'm', name: 'Source.mov', path: '/fixtures/source.mov', has_audio: true, has_video: true });
  f.clips[0].media_id = null; f.clips[0].graphic = { name: 'Authored title', layers: [{ kind: 'text', text: 'Two words', size: 30 }] };
  s.S.binSel = new Set(['m']);
  const originalNode = s.$;
  s.$ = selector => {
    const n = originalNode(selector);
    if (!n.attributes) Object.assign(n, { attributes: {}, isConnected: true, textContent: '', setAttribute(k, v) { this.attributes[k] = v; }, removeAttribute(k) { delete this.attributes[k]; } });
    return n;
  };
  s.$$ = () => []; s.switchSeq = id => { s.S.seqId = id; s.S.seq = s.S.proj.sequences.find(seq => seq.id === id); };
  s.togglePlay = value => { s.S.playing = value; };
  vm.runInContext(base.section('let editorialCommandPending =', 'function sourceReplacementMediaChain('), s);
  Object.assign(s.CR, { S: s.S, splitGraphicWords: s.splitGraphicWords, extractAudioSource: s.extractAudioSource, requestSequenceDescription: s.requestSequenceDescription,
    applyOps: s.applyOps, canEdit: s.canEdit, status: s.status });
  vm.runInContext(part('async function renderCutSummary()', 'async function coverDialog(') + '\n' + part('function extractAudio()', 'function floatPanel(') + '\n' + part('function wireAnimator(', 'function renderGfx('), s);
  if (options.override) vm.runInContext(options.override, s);
  f.reply = (i, value, code = 200) => f.requests[i].resolve(base.response(code, value));
  f.splitUI = () => {
    const button = s.$('#testSplit'); Object.assign(button, { dataset: { lasplit: '0' }, disabled: false });
    const pane = { contains: node => node === button && button.isConnected };
    s.$$ = (selector, owner) => selector === '[data-lasplit]' && owner === pane ? [button] : [];
    s.wireAnimator(pane, f.clips[0], f.tr); return button;
  };
  f.aliasPreview = () => {
    s.FilmocityProxyPreview = require('../../frontend/proxy-preview.js');
    Object.assign(f.project.media.m, { frame_rate: '60000/1001', fps: 59.94, interpret_fps: '30000/1001', duration: 20 });
    const alias = f.project.media.audio = { ...base.plain(f.project.media.m), id: 'audio', name: 'Selected range — Audio',
      audio_alias: { version: 1, source_media_id: 'sub', physical_media_id: 'm' }, subclip_of: 'm', sub_in: 6, duration: 4,
      has_video: false, has_audio: true, proxy: '/proxies/audio-token.m4a', ingest_token: 'audio-token',
      proxy_info: { codec: 'aac', lossy: true, sample_rate: 48000, channels: 2, native_duration: 10, validation: 'audio_alias_common_clock_metadata' } };
    s.S.useProxy = true; s.S.mediaAvailability = Object.fromEntries(['m', 'audio'].map(id => [id, { source_id: id, generation: id + '-generation', original_online: true, proxy_available: true, proxy_state: 'ready', original_lease: id + '-original', proxy_lease: id + '-proxy' }]));
    const video = s.$('#srcVideo'); Object.assign(video, { currentTime: 0, duration: 10, paused: true, playbackRate: 1, defaultPlaybackRate: 1,
      pause() { this.paused = true; }, load() {}, getAttribute(name) { return this.attributes[name] || null; } });
    Object.defineProperty(video, 'src', { configurable: true, get() { return this.attributes.src; }, set(value) { this.attributes.src = value; this.currentTime = 0; this.paused = true; } });
    s.sourcePreviewError = () => {}; s.updateSourcePreviewState = () => s.updateSourcePresentation(); s.updateSrcIO = () => {}; s.renderBin = () => {}; s.setFocus = value => s.S.focus = value;
    vm.runInContext(base.section('function sourceMediaUrl(', 'function updateSourcePreviewState(') + '\n' + base.section('const NATIVE_OK =', 'function vidFor(') + '\n' + base.section('function loadSource(', 'function updateSrcIO('), s);
    s.window.CR = { ...s.CR, updateSourcePresentation: s.updateSourcePresentation };
    s.loadSource('audio'); return { alias, video, availability: s.S.mediaAvailability.audio };
  };
  f.summary = () => s.renderCutSummary();
  f.ack = async (promise, after, extra = {}, index = 0) => {
    f.reply(index, { ok: true, context: base.context('r1'), changed: true, project: 'folder-a', summary: { message: 'Canonical edit saved.' }, warnings: [], ...extra });
    await base.until(() => f.requests.length > index + 1); f.requests[index + 1].resolve(base.read(after, 'r1')); return await promise;
  };
  return f;
}
module.exports = { ...base, fixture, part };

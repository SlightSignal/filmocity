// Production navigation/keyboard functions with controlled media and DOM adapters.
// This does not decode video or emulate native WebView keyboard/media seeking.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const timing = require('../../frontend/timeline-time.js');
const keyboard = require('../../frontend/keyboard.js');
const source = fs.readFileSync(path.join(__dirname, '../../frontend/app.js'), 'utf8');
function section(begin, end) {
  const a = source.indexOf(begin), b = source.indexOf(end, a);
  assert.ok(a >= 0 && b > a, `${begin} -> ${end}`);
  return source.slice(a, b);
}
function create(options = {}) {
  const S = { proj: {}, seq: { fps: options.fps || 30000 / 1001, timecode_format: 'ndf', tracks: [], markers: [] },
    src: { frame_rate: '24000/1001' }, target: { video: 'V1', audio: 'A1' }, t: 0, pps: 60,
    focus: 'program', playing: false, sel: new Set(), ...options.state };
  const calls = { updates: 0, selections: 0, stops: 0, sourceStops: 0, messages: [], prompts: [] };
  const video = { currentTime: 0, duration: 120, paused: true, pause() { this.paused = true; calls.sourceStops++; } };
  const body = { scrollLeft: 0, clientWidth: 600 };
  const env = { window: { FilmocitySourceClock: require('../../frontend/source-clock.js'), FilmocityTime: timing, FilmocityKeyboard: keyboard }, S,
    $: id => ({ '#srcVideo': video, '#tlBody': body }[id]), document: { querySelector: () => null },
    status: (...args) => calls.messages.push(args), updatePlayhead: () => calls.updates++,
    refreshSel: () => calls.selections++, trackOf: id => S.seq.tracks.find(t => t.id === id),
    togglePlay: value => { calls.stops++; S.playing = value; },
    prompt: (...args) => { calls.prompts.push(args); return options.answer ?? null; },
    TIMELINE_ONLY: new Set() };
  vm.createContext(env);
  vm.runInContext(section('const timing =', 'function bezierY(') + '\n' + section('const seqDurOf =', 'const frame =') + '\n' +
    section('function seekPreviewPicture(', 'function activeClipsOf(') + '\n' +
    section('function seekTo(', 'function trackMetersTick(') + '\n' +
    section('function navigateTo(', 'function rippleTrimToPlayhead(') + '\n' +
    section('function gotoMarker(', '// ---------- keyboard:') + '\n' +
    `const ACTIONS = {${source.split(/\r?\n/).filter(line => /^  (frame_back|prev_edit|prev_target_edit):/.test(line)).join('\n')}};\n` +
    'S.keymap = window.FilmocityKeyboard.readKeymap(ACTIONS);\n' +
    section('function comboOf(', 'async function refreshMediaStatus(') + '\n' +
    'this.ACTIONS = ACTIONS; this.frame = () => 1 / timing.frameRate(S.seq.fps);', env);
  if (options.override) vm.runInContext(options.override, env);
  return { env, S, calls, video, body, timing, section };
}
function keyEvent(key, options = {}) {
  const target = options.target || { matches: () => false, closest: () => null, isContentEditable: false };
  return { key, target, preventDefault() { this.defaultPrevented = true; }, ...options };
}
module.exports = { create, keyEvent, timing, section };

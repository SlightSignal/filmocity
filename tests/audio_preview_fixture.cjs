// Exercises production mixer/compositor functions with controlled Web Audio
// nodes. This checks ownership, routing and parameter values, not browser DSP.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const audio = require('../frontend/audio-preview.js');
const timeFx = require('../frontend/audio-time-fx.js');
const app = fs.readFileSync(path.join(__dirname, '../frontend/app.js'), 'utf8');
const panels = fs.readFileSync(path.join(__dirname, '../frontend/panels.js'), 'utf8');

function fixture({source=app}={}) {
  const contexts = [], elements = [], messages = [], fields = {}, nodes = [], buffers = [];
  let clock = 100, failGraph = false, scans = 0;
  const param = (value = 0) => ({value, setValueAtTime(v) {this.value = v;}, setTargetAtTime(v) {this.value = v;}});
  function node(kind) {
    const result = {kind, edges: [], disconnects: 0, stops: 0, started: false,
      connect(to, ...ports) {this.edges.push({to, ports}); return to;},
      disconnect() {this.edges = []; this.disconnects++;},
      gain: param(1), frequency: param(), Q: param(), threshold: param(), ratio: param(),
      attack: param(), release: param(), knee: param(), delayTime: param(),
      getFloatTimeDomainData(data) {data.fill(.1);}};
    if (kind === 'oscillator') {result.start = () => result.started = true; result.stop = () => result.stops++;}
    nodes.push(result); return result;
  }
  class AudioContext {
    constructor() {this.currentTime = 10; this.sampleRate = 48000; this.state = 'running'; this.destination = node('speaker'); this.sources = new Map(); contexts.push(this);}
    createMediaElementSource(element) {
      if (this.sources.has(element)) throw new Error('Element already captured');
      const source = node('source'); source.element = element; this.sources.set(element, source); return source;
    }
    createGain() {if (failGraph) throw new Error('node allocation failed'); return node('gain');}
    createDynamicsCompressor() {return node('compressor');}
    createAnalyser() {return node('analyser');}
    createChannelSplitter() {return node('splitter');}
    createChannelMerger() {return node('merger');}
    createBiquadFilter() {return node('filter');}
    createDelay() {return node('delay');}
    createConvolver() {return node('convolver');}
    createOscillator() {return node('oscillator');}
    createBuffer(channels, length, rate) {const data=Array.from({length:channels},()=>new Float32Array(16));const buffer={channels,length,rate,getChannelData:channel=>data[channel]};buffers.push(buffer);return buffer;}
    close() {this.state='closed';return Promise.resolve();}
  }
  const canvas = () => ({width: 64, height: 48, getContext: () => new Proxy({}, {get: (o, k) => o[k] || (() => {}), set: (o, k, v) => {o[k] = v; return true;}})});
  function video() {
    const element = {style: {}, paused: true, currentTime: 0, src: '', readyState: 2, muted: false, volume: 1,
      plays: 0, pauses: 0, loads: 0, removed: false, addEventListener() {},
      getAttribute(key) {return this[key];}, removeAttribute(key) {this[key] = '';},
      load() {this.loads++;}, remove() {this.removed = true;},
      play() {this.plays++; this.paused = false; return Promise.resolve();},
      pause() {this.pauses++; this.paused = true;}};
    elements.push(element); return element;
  }
  const clip = {id: 'c', media_id: 'm', start: 0, in_: 0, out: 1, audio: {}};
  const track = {id: 'V1', kind: 'video', index: 1, clips: [clip]};
  const bus = {id: 'A1', kind: 'audio', index: 1, clips: []};
  const seq = {id: 's', fps: 30, width: 64, height: 48, tracks: [track, bus], master: {gain_db: 0}};
  const S = {context: {workspace: 'w', project: 'p'}, seq, proj: {media: {m: {id: 'm', path: 'm.wav', has_audio: true, has_video: false, channels: 2}}, sequences: [seq]}, playing: true, useProxy: false};
  const env = {S, window: {AudioContext, FilmocitySourceClock: require('../frontend/source-clock.js')}, performance: {now: () => clock},
    document: {createElement: kind => kind === 'canvas' ? canvas() : video(), body: {appendChild() {}}},
    FilmocityAudioTimeFX: timeFx,
    FilmocityAudioPreview: {...audio, limitations(...args) {scans++; return audio.limitations(...args);}},
    FilmocityProxyPreview: {channelAlias: require('../frontend/proxy-preview.js').channelAlias, playbackUrl: (m, a, c, proxy) => `/media/${m.id}?proxy=${proxy}`, availabilityMessage: () => 'ready'},
    $: id => fields[id] ||= {textContent: '', firstChild: {style: {}}}, status: message => messages.push(message),
    kfVal: (keys, time, value) => keys?.[0]?.value ?? value,
    clipDur: c => (c.out - c.in_) / (c.speed || 1), clipEnd: c => c.start + (c.out - c.in_) / (c.speed || 1),
    speedAt: c => c.speed || 1, sourceOffset: (c, rel) => c.hold ? (c.hold_at || 0) : rel * (c.speed || 1), offscreen: {}};
  vm.createContext(env);
  vm.runInContext(source.slice(source.indexOf('const pool ='), source.indexOf('const imageCache =')) +
    source.slice(source.indexOf('function drawSequence('), source.indexOf('function wrapCaption(')) +
    '\nglobalThis.exposed = {pool, AG};', env);
  env.CR = {AG: env.exposed.AG};
  vm.runInContext('let meterData;\n' + panels.slice(panels.indexOf('function meter()'), panels.indexOf('// ---------- dialogs')), env);
  return {...env.exposed, env, S, seq, track, bus, clip, elements, nodes, buffers, contexts, messages, fields, canvas,
    advance(ms) {clock += ms;}, fail() {failGraph = true;}, recover() {failGraph = false;}, scans: () => scans,
    draw(time = .5, voicePath = ['program']) {const used = new Set(); env.drawSequence(canvas(), S.seq, time, used, 0, voicePath); env.sweepVoices(used); return used;}};
}


function nestedFixture(options={}) {
  const f = fixture(options), child = f.seq;
  const nest = {id:'nest', sequence_id:'s', start:0, in_:0, out:1, audio:{}};
  const parentBus = {id:'PA', kind:'audio', index:1, clips:[]};
  const parentTrack = {id:'PV', kind:'video', index:1, clips:[nest]};
  f.S.seq = {id:'parent', width:64, height:48, fps:30, tracks:[parentTrack, parentBus], master:{gain_db:0}};
  f.S.proj.sequences.push(f.S.seq);
  const graphs = () => {const entries = Object.entries(f.AG.nodes);return {
    leaf: entries.find(([,n]) => !n.element._mixOwner)?.[1],
    master: entries.find(([k]) => k.endsWith(':master'))?.[1],
    parent: entries.find(([k,n]) => n.element._mixOwner && !k.endsWith(':master'))?.[1]};};
  return {...f, child, nest, parentBus, parentTrack, graphs};
}

function multicamFixture() {
  const f=nestedFixture();f.child.multicam=true;f.child.multicam_audio='follow';f.clip.audio.linked=false;
  f.bus.clips=[{...f.clip,id:'a1',audio:{}}];
  f.S.proj.media.m2={...f.S.proj.media.m,id:'m2'};
  f.child.tracks.push({id:'V2',kind:'video',index:2,clips:[{...f.clip,id:'c2',media_id:'m2'}]},
    {id:'A2',kind:'audio',index:2,muted:true,clips:[{...f.clip,id:'a2',media_id:'m2',audio:{}}]});
  return f;
}
module.exports={fixture,nestedFixture,multicamFixture};

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const model = require('../frontend/export-encoders.js');
const source = fs.readFileSync(require('node:path').join(__dirname, '../frontend/panels.js'), 'utf8');
const details = [
  { id: 'libx264', format: 'h264', label: 'Software H.264', hardware: false, listed: true },
  { id: 'h264_nvenc', format: 'h264', label: 'NVIDIA H.264', hardware: true, listed: true },
  { id: 'h264_amf', format: 'h264', label: 'AMD H.264', hardware: true, listed: false },
  { id: 'libx265', format: 'hevc', label: 'Software HEVC', hardware: false, listed: true },
  { id: 'hevc_nvenc', format: 'hevc', label: 'NVIDIA HEVC', hardware: true, listed: true },
];

test('H.264, MOV and HEVC offer only encoders for the selected codec family', () => {
  for (const format of ['h264', 'h264mov', 'hevc']) {
    const result = model.choices(details, format, 'libx264');
    assert.ok(result.options.every(e => e.format === model.family(format) && e.listed));
    assert.equal(result.selected, format === 'hevc' ? 'libx265' : 'libx264');
  }
});
test('changing formats retains the hardware family where it is available', () => {
  assert.equal(model.choices(details, 'hevc', 'h264_nvenc').selected, 'hevc_nvenc');
  assert.equal(model.choices(details, 'h264', 'hevc_nvenc').selected, 'h264_nvenc');
});
test('a saved unavailable or incompatible encoder remains visible and requires a choice', () => {
  for (const codec of ['h264_amf', 'libx265', '<missing>']) {
    const result = model.choices(details, 'h264', codec, true);
    assert.equal(result.selected, codec);
    assert.equal(result.options.find(e => e.id === codec).disabled, true);
    assert.match(result.hint, /Choose an available/);
  }
});
test('PNG, audio and fixed-codec formats do not inherit the previous delivery encoder', () => {
  for (const format of ['png_sequence', 'audio:wav', 'webm', 'prores', 'av1', 'gif']) {
    const result = model.choices(details, format, 'h264_nvenc');
    assert.equal(result.disabled, true); assert.equal(result.selected, '');
  }
});
test('hardware quality hint explains the bitrate behavior instead of implying CRF support', () => {
  assert.match(model.choices(details, 'h264', 'h264_nvenc').hint, /10 Mbps/);
  assert.doesNotMatch(model.choices(details, 'h264', 'libx264').hint, /10 Mbps/);
});
test('missing codec family cannot silently select an encoder for another format', () => {
  const result = model.choices(details.filter(e => e.format === 'h264'), 'hevc', 'libx264');
  assert.equal(result.selected, ''); assert.equal(result.options[0].disabled, true);
});

function fixture() {
  const nodes = new Map();
  function node(id) {
    if (!nodes.has(id)) nodes.set(id, { value: '', textContent: '', children: [], classList: { contains: () => false },
      replaceChildren() { this.children = []; }, append(value) { this.children.push(value); },
      get selectedOptions() { return this.children.filter(x => x.value === this.value); } });
    return nodes.get(id);
  }
  node('#exFormat').value = 'h264'; node('#exEnc').value = 'libx264'; node('#exQuality').value = '18';
  let request;
  const f = { node, get: async () => ({ details }) };
  const scope = vm.createContext({ window: { FilmocityExportEncoders: model }, $: node, $$: () => [],
    document: { createElement: () => ({}) }, api: { get: () => f.get() }, refreshExportPreflight: async () => {},
    S: { binSel: new Set(), proj: { media: {} } } });
  const a = source.indexOf('function updateExportEncoders('), b = source.indexOf('async function relinkFromBrowser', a);
  vm.runInContext('let exportEncoderDetails = null, exportEncoderRequest = null, exportEncoderLoad = 0;\n' + source.slice(a,b) +
    '\n' + source.match(/^function exportSettings\(\).*$/m)[0] + '\n' + source.match(/^function applyExportSettings\(p\).*$/m)[0], scope);
  f.scope = scope; return f;
}
test('production loader uses text nodes and preserves a preset selected while discovery runs', async () => {
  const f = fixture(); let resolve;
  f.get = () => new Promise(r => { resolve = r; });
  const loading = f.scope.loadEncoders();
  f.scope.applyExportSettings({ format: 'hevc', vcodec: 'hevc_nvenc' });
  resolve({ details }); await loading;
  assert.equal(f.node('#exEnc').value, 'hevc_nvenc');
  assert.ok(f.node('#exEnc').children.every(x => !x.textContent.includes('H.264')));
  const preset = f.scope.exportSettings(); assert.equal(preset.format, 'hevc'); assert.equal(preset.vcodec, 'hevc_nvenc');
});
test('production preset restore keeps invalid choices blocked and fixed formats omit vcodec', async () => {
  const f = fixture(); await f.scope.loadEncoders();
  f.scope.applyExportSettings({ format: 'h264', vcodec: '<missing>' });
  assert.equal(f.node('#exEnc').children[0].textContent, 'Unavailable for this format: <missing>');
  assert.throws(() => f.scope.exportSettings(), /available encoder/);
  f.scope.applyExportSettings({ format: 'png_sequence', vcodec: 'h264_nvenc' });
  assert.equal(f.scope.exportSettings().vcodec, undefined); assert.equal(f.node('#exEnc').disabled, true);
});
test('obsolete discovery replies cannot overwrite newer encoder choices', async () => {
  const f = fixture(); let resolve;
  f.get = () => new Promise(r => { resolve = r; }); const old = f.scope.loadEncoders();
  f.get = async () => ({ details }); await f.scope.loadEncoders();
  resolve({ details: [] }); await old;
  assert.equal(f.node('#exEnc').value, 'libx264');
});
test('discovery failures are visible instead of silently returning an empty menu', async () => {
  const f = fixture(); f.get = async () => { throw new Error('tools missing'); };
  await f.scope.loadEncoders(); assert.match(f.node('#exEncoderStatus').textContent, /tools missing/);
});

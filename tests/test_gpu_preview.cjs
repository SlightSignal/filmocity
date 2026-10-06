const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { capture } = require('./helpers/gpu-capture.cjs');

test('wheel parameters preserve band/channel mapping and export precision without changing clip data', () => {
  const clip = { color: { wheels: { shadows: { r: -.123456, g: .23456, b: -.765432 }, midtones: { r: .5, b: -.8 }, highlights: { g: 1 } } } };
  const result = capture(clip).uniforms;
  assert.deepEqual(result.wS.value, [-.123, .235, -.765]);
  assert.deepEqual(result.wM.value, [.5, 0, -.8]); assert.deepEqual(result.wH.value, [0, 1, 0]);
  assert.deepEqual(result.wheelOn.value, [1]);
  assert.ok(Object.values(result).every(uniform => uniform.value.every(Number.isFinite)));
});

test('subthreshold values do not activate wheels and a subsequent neutral clip resets reused GPU state', () => {
  const states = capture([
    { color: { wheels: { shadows: { r: .5 } } } },
    { color: { wheels: { shadows: { r: .001, g: -.00099 } } } },
    {},
  ]);
  assert.deepEqual(states.map(state => state.uniforms.wheelOn.value[0]), [1, 0, 0]);
  for (const name of ['wS', 'wM', 'wH']) assert.deepEqual(states[2].uniforms[name].value, [0, 0, 0]);
});

test('previewing a neutral clip after a graded clip restores the identity texture', () => {
  const states = capture([{ color: { curves: [[0, 0], [1, .5]] } }, {}]);
  assert.notDeepEqual(states[0].images[1].data, states[1].images[1].data);
  assert.deepEqual(states[1].images[1].data, Array.from({ length: 256 }, (_, i) => [i, i, i, 255]).flat());
});

const app = fs.readFileSync(path.join(__dirname, '../frontend/app.js'), 'utf8');
const start = app.indexOf('      const colorMedia ='), end = app.indexOf('      if ((c.transition_in', start);
assert.ok(start >= 0 && end > start);
function compositor(options = {}) {
  const source = { readyState: 2 }, processed = {}, draws = [], calls = [];
  const c = { id: 'c', color: { wheels: { midtones: { r: .5 } } }, _inputTransform: 'old-runtime-value' };
  const before = JSON.stringify(c);
  const ctx = { drawImage: (...args) => draws.push(args), translate() {}, rotate() {} };
  const gpu = { needs: clip => { calls.push(['needs', clip]); return true; }, process: (...args) => { calls.push(['process', ...args]); return options.unavailable ? null : processed; } };
  const scope = { FilmocityMediaColor: require('../frontend/media-color.js'), c, m: { input_transform: options.input, ...(options.parent?{subclip_of:'parent'}:{}) }, window: { CR_GPU: gpu }, CR_GPU: gpu,
    S: { proj: {media:options.parent?{parent:options.parent}:{}}, prefs: options.prefs || {}, playing: options.playing || false }, v: source,
    ctx, alpha: .5, fx: {}, fit: 1, tf: {}, cr: { l: .1, r: .2 }, fw: 100, fh: 50, W: 200, H: 100,
    dw: 200, dh: 100, ox: 0, oy: 0, push: 0, aox: 0, aoy: 0, rr: 0,
    stackCssNonColor: () => 'blur(1px)', cssFilter: () => 'contrast(1.2)', BLEND_CANVAS: {},
  };
  if (options.notReady) source.readyState = 1;
  if (options.noGPU) delete scope.window.CR_GPU;
  vm.runInNewContext(app.slice(start, end), scope);
  assert.equal(JSON.stringify(c), before, 'Rendering changed stored clip fields');
  return { source, processed, draws, calls, ctx };
}

test('the production compositor uses the graded source once and preserves crop, opacity and noncolor effects', () => {
  const { processed, source, draws, calls, ctx } = compositor({ input: 'slog3' });
  assert.equal(calls[1][1], source); assert.equal(calls[1][2]._inputTransform, 'slog3');
  assert.equal(draws.length, 1); assert.equal(draws[0][0], processed);
  assert.deepEqual(draws[0].slice(1), [10, 0, 70, 50, -70, -50, 140, 100]);
  assert.equal(ctx.globalAlpha, .5); assert.equal(ctx.filter, 'blur(1px)');
});

test('an absent or disabled media input transform cannot reuse a stale transform from project data', () => {
  for (const input of [undefined, 'none']) {
    const { calls } = compositor({ input }); assert.equal(calls[1][2]._inputTransform, null);
  }
});

test('GPU preferences, draft playback, unavailable processing and unready media keep their fallback behavior', () => {
  for (const options of [{ prefs: { gpu: false } }, { noGPU: true }, { playing: true, prefs: { draft: true } }, { unavailable: true }]) {
    const { source, draws, calls, ctx } = compositor(options);
    assert.equal(draws.length, 1); assert.equal(draws[0][0], source);
    assert.equal(ctx.filter, options.playing ? 'none' : 'contrast(1.2)');
    assert.equal(calls.length, options.unavailable ? 2 : 0);
  }
  const unready = compositor({ notReady: true }); assert.equal(unready.draws.length, 0); assert.equal(unready.calls.length, 0);
});

test('subclip live camera LUT follows its parent instead of a stale copied setting',()=>{
  const result=compositor({input:'slog3',parent:{input_transform:'vlog'}});
  assert.equal(result.calls.find(c=>c[0]==='process')[2]._inputTransform,'vlog');
});


test('a packaged camera LUT uses its parent binding and a changed choice releases it',()=>{
  const parent={input_transform:'vlog',input_transform_resource:{name:'vlog',path:'C:/project/resources/camera.cube'}};
  const result=compositor({input:'slog3',parent});assert.equal(result.calls.find(c=>c[0]==='process')[2]._inputTransformPath,parent.input_transform_resource.path);
  parent.input_transform='clog3';const changed=compositor({parent});assert.equal(changed.calls.find(c=>c[0]==='process')[2]._inputTransformPath,null);
});

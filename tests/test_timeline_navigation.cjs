const test = require('node:test'), assert = require('node:assert/strict');
const { create, keyEvent, timing } = require('./helpers/navigation-fixture.cjs');
const clip = (id, start, end) => ({ id, start, in_: 0, out: end - start, speed: 1 });
const track = (id, clips, extra = {}) => ({ id, kind: id.startsWith('A') ? 'audio' : 'video', clips, ...extra });

test('program frame stepping advances from the displayed frame, even near the next boundary', () => {
  for (const fps of [24, 25, 30, 23.976, 29.97, 59.94, 119.88]) {
    const h = create({ fps }); h.S.t = timing.fromFrames(100.8, fps);
    assert.equal(h.env.stepFrame(1), true); assert.equal(h.S.t, timing.fromFrames(101, fps));
    h.S.t = timing.fromFrames(100.8, fps); h.env.stepFrame(-1); assert.equal(h.S.t, timing.fromFrames(99, fps));
    h.S.t = timing.fromFrames(100.8, fps); h.env.stepFrame(5); assert.equal(h.S.t, timing.fromFrames(105, fps));
  }
});
test('thousands of forward/backward NTSC steps do not accumulate time drift', () => {
  for (const fps of [23.976, 29.97, 59.94]) {
    const h = create({ fps }); h.S.t = timing.fromFrames(107892, fps);
    for (let n = 0; n < 1000; n++) h.env.stepFrame(1);
    assert.equal(h.S.t, timing.fromFrames(108892, fps));
    for (let n = 0; n < 1000; n++) h.env.stepFrame(-1);
    assert.equal(h.S.t, timing.fromFrames(107892, fps));
    h.S.t = 0; h.env.stepFrame(-5); assert.equal(h.S.t, 0);
  }
});
test('fractional-rate edit navigation lands on the exact stored boundary and displayed frame', () => {
  for (const fps of [23.976, 29.97, 59.94]) {
    const h = create({ fps }), starts = [1, 1800, 107892].map(n => timing.fromFrames(n, fps));
    h.S.seq.tracks = [track('V1', starts.map((start, i) => clip(String(i), start, start + 1)))];
    for (let i = 0; i < starts.length; i++) {
      h.S.t = starts[i] - timing.fromFrames(.5, fps); h.env.gotoEdit(1);
      assert.equal(h.S.t, starts[i]); assert.equal(timing.displayFrame(h.S.t, fps), [1, 1800, 107892][i]);
    }
  }
});
test('navigation preserves audio subframe boundaries and ignores only arithmetic noise', () => {
  const h = create(); h.S.seq.tracks = [track('A1', [clip('a', 1, 2), clip('b', 1 + Number.EPSILON, 3), clip('c', 1.00002, 4)])];
  h.S.t = 1; h.env.gotoEdit(1); assert.equal(h.S.t, 1.00002);
  h.env.gotoEdit(-1); assert.ok(Math.abs(h.S.t - 1) < 1e-15);
  h.env.gotoEdit(-1); assert.equal(h.S.t, 0);
});
test('all-track edit navigation includes locked/disabled tracks while targeted commands filter only destinations', () => {
  const h = create(); h.S.seq.tracks = [track('V1', [clip('a', 2, 6)]), track('V2', [clip('b', 1, 4)], { locked: true, hidden: true }), track('A1', [clip('c', 3, 5)])];
  h.env.gotoEdit(1); assert.equal(h.S.t, 1);
  h.S.t = 0; h.env.ACTIONS.next_target_edit[2](); assert.equal(h.S.t, 2);
  h.env.ACTIONS.next_target_edit[2](); assert.equal(h.S.t, 3);
  h.env.ACTIONS.next_target_edit[2](); assert.equal(h.S.t, 5);
  h.env.ACTIONS.prev_target_edit[2](); assert.equal(h.S.t, 3);
});
test('edit/marker navigation safely stops at ends and skips malformed marker values', () => {
  const h = create(); h.S.seq.markers = [{ time: NaN }, { time: -1 }, { time: 1 }, { time: 1.00002 }, { time: Infinity }];
  h.env.gotoMarker(1); assert.equal(h.S.t, 1); h.env.gotoMarker(1); assert.equal(h.S.t, 1.00002);
  const updates = h.calls.updates; assert.equal(h.env.gotoMarker(1), false); assert.equal(h.calls.updates, updates);
  h.env.gotoMarker(-1); assert.equal(h.S.t, 1);
  assert.equal(h.env.gotoEdit(0), false); h.S.t = 0; assert.equal(h.env.gotoEdit(-1), false);
});
test('discrete navigation pauses playback and reveals the destination without scrolling visible positions', () => {
  const h = create(); h.S.playing = true; h.env.navigateTo(100);
  assert.equal(h.S.playing, false); assert.equal(h.calls.stops, 1); assert.equal(h.body.scrollLeft, 5700);
  h.env.navigateTo(101); assert.equal(h.body.scrollLeft, 5700); assert.equal(h.calls.stops, 1);
  h.env.navigateTo(0); assert.equal(h.body.scrollLeft, 0);
});
test('general scrubbing retains subframe precision and playback; invalid seeks cannot poison playhead state', () => {
  const h = create(); h.S.playing = true; h.env.seekTo(.00123456789);
  assert.equal(h.S.t, .00123456789); assert.equal(h.S.playing, true);
  for (const value of [NaN, Infinity, -Infinity, undefined, '12']) assert.equal(h.env.seekTo(value), false);
  assert.equal(h.S.t, .00123456789); assert.equal(h.calls.updates, 1);
});
test('source-focused arrow shortcuts use native source frames and leave Program unchanged', () => {
  const h = create(); h.S.focus = 'source'; h.S.src.interpret_fps = 25; h.S.t = 8;
  h.video.currentTime = timing.fromFrames(80.8, 24000 / 1001); h.video.paused = false;
  const e = keyEvent('ArrowRight'); h.env.keys(e);
  assert.equal(e.defaultPrevented, true); assert.equal(h.video.currentTime, timing.fromFrames(81, 24000 / 1001));
  assert.equal(h.S.t, 8); assert.equal(h.video.paused, true); assert.equal(h.calls.updates, 0);
  h.env.keys(keyEvent('ArrowLeft', { shiftKey: true })); assert.equal(h.video.currentTime, timing.fromFrames(76, 24000 / 1001));
});
test('source stepping clamps to the final frame rather than exclusive media duration', () => {
  const h = create(); h.video.duration = timing.fromFrames(30, 24000 / 1001);
  h.video.currentTime = timing.fromFrames(29, 24000 / 1001); h.env.stepSourceFrame(1);
  assert.equal(h.video.currentTime, timing.fromFrames(29, 24000 / 1001));
  h.video.duration = timing.fromFrames(30.5, 24000 / 1001); h.env.stepSourceFrame(5);
  assert.equal(h.video.currentTime, timing.fromFrames(30, 24000 / 1001));
  h.video.currentTime = 0; h.env.stepSourceFrame(-1); assert.equal(h.video.currentTime, 0);
});
test('source navigation with no loaded source is inert and unknown-duration End is refused', () => {
  const h = create(); h.S.focus = 'source'; h.S.src = null;
  assert.equal(h.env.stepSourceFrame(1), false); assert.equal(h.env.navigateEdge(true), false);
  h.S.src = { fps: 30 }; h.video.duration = NaN;
  assert.equal(h.env.navigateEdge(true), false); assert.equal(h.calls.sourceStops, 0);
  assert.equal(h.env.stepSourceFrame(.5), false); assert.equal(h.env.stepFrame(NaN), false);
});
test('Home/End follow Source focus and Program controls retain separate timeline navigation', () => {
  const h = create(); h.S.seq.tracks = [track('V1', [clip('a', 0, 7)])];
  h.S.focus = 'source'; h.video.duration = timing.fromFrames(48, 24000 / 1001); h.S.t = 5;
  h.env.keys(keyEvent('End')); assert.equal(h.video.currentTime, timing.fromFrames(47, 24000 / 1001)); assert.equal(h.S.t, 5);
  h.env.keys(keyEvent('Home')); assert.equal(h.video.currentTime, 0);
  h.S.focus = 'program'; h.env.keys(keyEvent('End')); assert.equal(h.S.t, 7);
  h.env.keys(keyEvent('Home')); assert.equal(h.S.t, 0);
});
test('Go to relative frame labels starts at displayed frame while relative seconds retain subframes', () => {
  const h = create({ answer: '+00:00:00:01' }); h.S.t = timing.fromFrames(100.8, h.S.seq.fps);
  assert.equal(h.env.goToTimecode(), true); assert.equal(h.S.t, timing.fromFrames(101, h.S.seq.fps));
  h.env.prompt = () => '+.00125'; h.env.goToTimecode(); assert.equal(h.S.t, timing.fromFrames(101, h.S.seq.fps) + .00125);
  h.env.prompt = () => '-100'; h.env.goToTimecode(); assert.equal(h.S.t, 0);
});
test('Go to DF boundaries validates skipped labels and leaves state intact on cancellation/invalid input', () => {
  const h = create(); h.S.seq.timecode_format = 'df'; h.S.t = 60.06;
  for (const answer of [null, '', 'junk', '+bad', '00:01:00;00']) { h.env.prompt = () => answer; assert.equal(h.env.goToTimecode(), false); assert.equal(h.S.t, 60.06); }
  h.env.prompt = () => '00:01:00;02'; assert.equal(h.env.goToTimecode(), true); assert.equal(h.S.t, 60.06);
  h.env.prompt = () => '+9007199254740991'; assert.equal(h.env.goToTimecode(), false); assert.equal(h.S.t, 60.06);
});
test('Source timecode entry uses source rate, preserves valid decimals, and clamps only beyond source duration', () => {
  const h = create(); h.S.focus = 'source'; h.S.t = 9; h.video.duration = 2;
  h.env.prompt = () => '00:00:00:12'; h.env.ACTIONS.goto_timecode[2]();
  assert.equal(h.video.currentTime, timing.fromFrames(12, 24000 / 1001)); assert.equal(h.S.t, 9);
  h.env.prompt = () => '1.999'; h.env.goToTimecode(true); assert.equal(h.video.currentTime, 1.999);
  h.env.prompt = () => '999'; h.env.goToTimecode(true); assert.equal(h.video.currentTime, timing.fromFrames(47, 24000 / 1001));
});
test('native fields and custom navigation widgets retain their arrow/Home/End keys', () => {
  const h = create(); h.S.t = 4;
  for (const key of ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End']) {
    for (const target of [{ matches: () => true, closest: () => null }, { matches: () => false, closest: () => ({}), isContentEditable: false }, { matches: () => false, closest: () => null, isContentEditable: true }]) {
      const e = keyEvent(key, { target }); h.env.keys(e); assert.equal(e.defaultPrevented, undefined); assert.equal(h.S.t, 4);
    }
  }
  assert.equal(h.calls.updates, 0);
});
test('timeline arrow key repeats advance one frame each and honor modal/composition/gesture guards', () => {
  const h = create(); h.env.keys(keyEvent('ArrowRight')); h.env.keys(keyEvent('ArrowRight', { repeat: true }));
  assert.equal(h.S.t, timing.fromFrames(2, h.S.seq.fps));
  for (const extra of [{ defaultPrevented: true }, { isComposing: true }]) h.env.keys(keyEvent('ArrowRight', extra));
  h.S.gesture = {}; h.env.keys(keyEvent('ArrowRight')); h.S.gesture = null;
  h.env.document.querySelector = () => ({}); h.env.keys(keyEvent('ArrowRight'));
  assert.equal(h.S.t, timing.fromFrames(2, h.S.seq.fps));
});
test('navigation while Trim Edit plays cannot schedule an edit; explicit Stop keeps its established trim behavior', () => {
  const vm = require('node:vm'), h = create(), callbacks = [], playButton = {};
  const originalQuery = h.env.$; h.env.$ = id => id === '#prgPlay' ? playButton : originalQuery(id);
  Object.assign(h.env, { setTimeout: callback => callbacks.push(callback), trimToPlayhead: () => {}, renderProgram: () => {}, cancelAnimationFrame: () => {} });
  vm.runInContext('let playbackFrame = null;\n' + h.section('function togglePlay(', '// ---------- source monitor'), h.env);
  h.S.trimMode = true; h.S.playing = true; h.env.navigateTo(10);
  assert.equal(h.S.t, 10); assert.equal(h.S.playing, false); assert.equal(h.S.trimMode, true); assert.equal(callbacks.length, 0);
  h.S.playing = true; h.env.togglePlay(false); assert.equal(callbacks.length, 1); assert.equal(callbacks[0], h.env.trimToPlayhead);
});
test('edit navigation and sequence End use retimed and hold durations from the production timeline', () => {
  const h = create(), ramp = { id: 'ramp', start: 1, in_: 0, out: 8, speed: 1, time_remap: [{ t: 0, v: 1 }, { t: 2, v: 3 }] };
  const held = { id: 'held', start: 5, in_: 8, out: 9, speed: 4, hold: true };
  h.S.seq.tracks = [track('V1', [ramp, held])]; h.S.t = 1;
  h.env.gotoEdit(1); assert.equal(h.S.t, 1 + 2 + 4 / 3);
  h.env.gotoEdit(1); assert.equal(h.S.t, 5); h.env.gotoEdit(1); assert.equal(h.S.t, 6);
  h.S.t = 0; h.env.navigateEdge(true); assert.equal(h.S.t, 6);
});
test('Program frame controls retain Program ownership when Source has keyboard focus', () => {
  const h = create(); h.S.focus = 'source'; h.video.currentTime = 3; h.env.stepFrame(1);
  assert.equal(h.S.t, timing.fromFrames(1, h.S.seq.fps)); assert.equal(h.video.currentTime, 3); assert.equal(h.calls.sourceStops, 0);
});

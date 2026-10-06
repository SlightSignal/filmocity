// Actual mouse edit requests for Python project-store and history integration.
const fs = require('node:fs');
const { fixture, plain, context } = require('./gesture-fixture.cjs');
function plan({ mode = 'reverse_head', cancel = null, patch = {}, fps = 30 } = {}) {
  const f = fixture(), c = f.clips[1]; f.seq.fps = fps; f.scope.S.snap = false;
  Object.assign(c, { in_: 3, out: 9, note: 'source edit integration', markers: [{ t: 0.5, name: 'early' }, { t: 2, name: 'later' }], keyframes: { 'transform.scale': [{ t: 0, v: 1, e: 'ease' }, { t: 5, v: 2 }] } });
  let handle = 'l', delta = 1;
  if (mode === 'reverse_head') c.reverse = true;
  else if (mode === 'hold_tail') { Object.assign(c, { hold: true, in_: 9, out: 15 }); f.project.media.m.duration = 10; handle = 'r'; delta = -1; }
  else if (mode === 'ramp_head' || mode === 'ramp_slip') {
    c.time_remap = [{ t: 0, v: 0.5 }, { t: 2, v: 2 }, { t: 4, v: 1, e: 'hold' }];
    if (mode === 'ramp_slip') { f.scope.S.tool = 'slip'; handle = null; delta = 0.5; }
  } else if (mode === 'fade_head') {
    c.audio = { fade_in: 3, fade_out: 3, fade_window: { duration: 9, offset: 1, settings: [3, 3, true, '', 0, '', 0] } };
    c.keyframes['audio.duck_db'] = [{ t: 0, v: 0 }, { t: 2, v: -18 }, { t: 5, v: -3 }];
  } else if (mode === 'reverse_roll') { c.reverse = true; f.clips[0].reverse = true; f.scope.S.tool = 'roll'; delta = 0.5; }
  else if (mode === 'ripple_head') { f.scope.S.tool = 'ripple'; delta = -1; }
  else if (mode === 'invalid_neighbor') { f.scope.S.tool = 'roll'; f.clips[0].transition_out = { type: 'dissolve', duration: 1 }; delta = 0.5; }
  else throw Error('Unknown source-edit plan mode');
  Object.assign(c, plain(patch)); f.clips[2].start = c.start + f.scope.clipDur(c);
  const before = plain(f.project); f.start(handle, 1); f.move({ clientX: delta * f.scope.S.pps });
  if (cancel === 'escape') f.escape();
  else if (cancel === 'context') { f.scope.S.context = context('r0', 'other-project'); f.up(); }
  else f.up();
  if (!cancel && mode !== 'invalid_neighbor' && f.requests.length !== 1) throw Error(`Expected one edit: ${f.messages.join('; ')}`);
  return { mode, before, body: f.requests.length ? f.body() : null, optimistic: plain(f.project), messages: f.messages, retainedDrafts: f.storage.size };
}
module.exports = { plan };
if (require.main === module) process.stdout.write(JSON.stringify(plan(JSON.parse(fs.readFileSync(0, 'utf8') || '{}'))));

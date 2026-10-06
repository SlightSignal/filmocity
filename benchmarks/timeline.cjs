// node benchmarks/timeline.cjs [source-file] [output-json]
const fs = require('node:fs'), path = require('node:path'), os = require('node:os'), crypto = require('node:crypto');
const { performance } = require('node:perf_hooks');
const { createHarness, makeProject } = require('./timeline-harness.cjs');
const sourceFile = path.resolve(process.argv[2] || path.join(__dirname, '../frontend/app.js'));
const source = fs.readFileSync(sourceFile, 'utf8');
const cases = [];
for (const [label, seconds] of [['short', 180], ['medium', 1200], ['long', 7200]]) {
  const project = makeProject(seconds), app = createHarness(source, project, { scroll: seconds / 2 * 60 });
  const samples = []; let allocated;
  for (let i = 0; i < 7; i++) { const start = performance.now(); allocated = app.render(); const elapsed = performance.now() - start; if (i > 1) samples.push(elapsed); }
  samples.sort((a, b) => a - b);
  cases.push({ label, seconds, clips: project.sequences[0].tracks.reduce((n, tr) => n + tr.clips.length, 0), captions: project.sequences[0].captions.length,
    allocations: allocated, ruler_ticks: app.elements('ruler-tick').length, caption_nodes: app.elements('capclip').length, marker_nodes: app.elements('marker').length,
    median_fixture_ms: +samples[Math.floor(samples.length / 2)].toFixed(3), worst_fixture_ms: +samples.at(-1).toFixed(3) });
}
const playback = createHarness(source, makeProject(180)); for (let i = 0; i < 20; i++) playback.scope.togglePlay(true);
const report = { scope: 'Production renderTimeline and playback functions in a controlled Node allocation fixture. No browser layout, paint, media decoding or GPU timing.',
  measured_at: new Date().toISOString(), platform: process.platform, node: process.version, cpu: os.cpus()[0]?.model,
  source: path.relative(path.join(__dirname, '..'), sourceFile), sha256: crypto.createHash('sha256').update(fs.readFileSync(sourceFile)).digest('hex'),
  harness_sha256: crypto.createHash('sha256').update(fs.readFileSync(path.join(__dirname, 'timeline-harness.cjs'))).digest('hex'),
  geometry_sha256: source.includes('window.FilmocityTimeline') ? crypto.createHash('sha256').update(fs.readFileSync(path.join(__dirname, '../frontend/timeline-window.js'))).digest('hex') : null,
  viewport_px: 1200, pixels_per_second: 60,
  cases, pending_frames_after_20_play_calls: playback.pending.size };
if (process.argv[3]) fs.writeFileSync(process.argv[3], JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report, null, 2));

/* Run: node tests/test_png_sequence_ui.cjs
 * Executes the actual panel entry points with in-memory API/DOM fixtures.
 * No browser, server, render process, project files or extra dependencies.
 * Markup/lifecycle regression only; native layout and navigation remain release gates.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'frontend', 'panels.js'), 'utf8');
const settle = () => new Promise(resolve => setImmediate(resolve));
const decodeAttribute = text => text.replace(/&(amp|lt|gt|quot|#39);/g,
  (_, entity) => ({ amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'" }[entity]));

class Element {
  constructor() {
    this.innerHTML = ''; this.textContent = ''; this.value = ''; this.checked = false;
    this.disabled = false; this.style = {}; this.dataset = {}; this.children = [];
    this.classList = { contains: name => name === 'on' };
  }
  addEventListener() {}
  replaceChildren() { this.children = []; }
  append(...children) { this.children.push(...children); }
}

function fixture(initialJobs) {
  const nodes = new Map(), timers = [], calls = [];
  const doc = { body: new Element(), createElement: () => new Element() };
  doc.activeElement = doc.body;
  let jobs = initialJobs;
  const node = selector => {
    if (!nodes.has(selector)) {
      const element = new Element(), classes = new Set(['on']);
      if (selector === '#dlgExport') classes.add('open');
      element.classList = { contains: name => classes.has(name), add: name => classes.add(name), remove: name => classes.delete(name) };
      element.focus = () => { doc.activeElement = element; };
      element.querySelector = query => query === '.actions button:not(.primary)' ? node('#exportCancel') : null;
      let disabled = false;
      Object.defineProperty(element, 'disabled', { get: () => disabled, set: value => {
        disabled = value;
        // Native WebView evidence confirms disabling its focused Export button drops focus to the page.
        if (value && doc.activeElement === element) doc.activeElement = doc.body;
      } });
      nodes.set(selector, element);
    }
    return nodes.get(selector);
  };
  const $ = selector => selector === '#qManifest' && !node('#pane-queue').innerHTML.includes('id="qManifest"') ? null : node(selector);
  node('#exQuality').value = '18'; node('#exEnc').value = 'libx264';
  node('#exFormat').value = 'png_sequence'; node('#exName').value = 'fixture';
  const $$ = (selector, pane) => {
    if (selector === '#exOutputs input:checked') return [{ dataset: { o: 'native' } }];
    if (selector === '[data-cancel]') {
      pane.cancelButtons = [...pane.innerHTML.matchAll(/data-cancel="([^"]*)"/g)].map(match => {
        const button = new Element(); button.dataset.cancel = decodeAttribute(match[1]); return button;
      });
      return pane.cancelButtons;
    }
    return [];
  };
  const api = {
    async get(url) {
      calls.push({ method: 'GET', url });
      if (url === '/api/jobs') return jobs;
      if (url.startsWith('/api/render/')) {
        const id = decodeURIComponent(url.slice('/api/render/'.length));
        const job = jobs.find(j => j.id === id); assert.ok(job, `unknown fixture job: ${id}`); return job;
      }
      // Other calls are unrelated startup loaders, each with its own catch handler.
      throw new Error(`Unstubbed startup API: ${url}`);
    },
    async json(method, url, body) {
      calls.push({ method, url, body });
      if (url === '/api/render/preflight') return { ok: true, warnings: 0, errors: 0, issues: [] };
      if (url === '/api/render' || url === '/api/render_all') return { jobs: jobs.map(j => ({ id: j.id })) };
      if (url.endsWith('/cancel')) return { ok: true };
      assert.fail(`Unstubbed mutation: ${method} ${url}`);
    }
  };
  const CR = { S: { seq: { id: 'fixture', width: 64, height: 48 }, binSel: new Set(), proj: { media: {} } }, $, $$, api };
  vm.runInNewContext(source, {
    window: { CR }, document: doc, console,
    setTimeout: (run, ms) => { timers.push({ run, ms }); return timers.length; }
  }, { filename: 'frontend/panels.js', timeout: 2000 });
  return {
    CR, node, timers, calls, api, document: doc,
    setJobs(next) { jobs = next; },
    async export() { await node('#exStart').onclick(); await settle(); return node('#exOut').innerHTML; },
    async queue() { await CR.renderQueue(); return node('#pane-queue').innerHTML; },
    async poll() {
      const index = timers.findIndex(timer => timer.ms === 1500);
      assert.ok(index >= 0, 'export should schedule another poll');
      await timers.splice(index, 1)[0].run(); await settle(); return node('#exOut').innerHTML;
    }
  };
}

const sequenceJob = changes => ({
  id: 'png', name: 'PNG fixture', status: 'done', output_kind: 'png_sequence',
  out: '/renders/fixture.frames/sequence.json',
  frames: { directory: '/renders/fixture.frames', pattern: 'frame_%05d.png', first_frame: '/renders/fixture.frames/frame_00001.png' },
  qa: { status: 'checked', output_kind: 'png_sequence', frame_count: 12, width: 64, height: 48, duration: 0.5, size_mb: 0.02, flags: [] },
  ...changes
});

test('shared processing wait is visible in queue and export dialog without pretending to encode', async () => {
  const job=sequenceJob({status:'running',progress:.4,resource:{state:'waiting',operation:'encode'},qa:null});
  const app=fixture([job]);let markup=await app.queue();
  assert.match(markup,/Waiting for processing slot/);assert.doesNotMatch(markup,/40%/);assert.match(markup,/data-cancel="png"/);
  assert.match(await app.export(),/Waiting for processing slot/);
  app.setJobs([{...job,resource:{state:'running',operation:'encode'}}]);markup=await app.queue();
  assert.doesNotMatch(markup,/Waiting for processing slot/);assert.match(markup,/40%/);assert.match(await app.poll(),/rendering… 40%/);
});

test('export waits for saved edits, suppresses duplicate clicks and uses the saved revision', async () => {
  const app = fixture([sequenceJob()]);
  app.CR.S.context = { workspace: 'w', project: 'p', revision: 1 };
  let saved; app.CR.flushSaves = () => new Promise(resolve => { saved = resolve; });
  const pending = app.export(); await settle(); await app.export();
  assert.equal(app.calls.filter(c => c.url.startsWith('/api/render')).length, 0);
  app.CR.S.context = { ...app.CR.S.context, revision: 2 }; saved(); await pending;
  const requests = app.calls.filter(c => c.method === 'POST' && c.url === '/api/render');
  assert.equal(requests.length, 1); assert.equal(requests[0].body._context.revision, 2);
});

test('a switch during saving cannot export the newly opened project', async () => {
  const app = fixture([sequenceJob()]);
  app.CR.S.context = { workspace: 'w', project: 'original', revision: 1 };
  let saved; app.CR.flushSaves = () => new Promise(resolve => { saved = resolve; });
  const pending = app.export(); await settle();
  app.CR.S.context = { workspace: 'w', project: 'other', revision: 1 }; saved(); await pending;
  assert.equal(app.calls.filter(c => ['/api/render', '/api/render_all'].includes(c.url)).length, 0);
  assert.match(app.node('#exOut').textContent, /Project changed while saving/);
});

test('failed saving blocks export and permits a later resolved attempt', async () => {
  const app = fixture([sequenceJob()]);
  app.CR.previewView = () => ({ error: 'disk write failed' });
  await app.export();
  assert.equal(app.calls.filter(c => c.url === '/api/render').length, 0);
  assert.match(app.node('#exOut').textContent, /resolve saving/);
  app.CR.previewView = () => ({}); await app.export();
  assert.equal(app.calls.filter(c => c.method === 'POST' && c.url === '/api/render').length, 1);
});

test('a slow preflight cannot change the captured batch mode or output name', async () => {
  const app = fixture([sequenceJob()]), original = app.api.json;
  let finish; app.api.json = async (method, url, body) => {
    if (url === '/api/render/preflight' && !finish) await new Promise(resolve => { finish = resolve; });
    return original(method, url, body);
  };
  const pending = app.export(); await settle();
  app.node('#exAll').checked = true; app.node('#exName').value = 'changed during check';
  finish(); await pending;
  assert.equal(app.calls.filter(c => c.url === '/api/render_all').length, 0);
  assert.equal(app.calls.find(c => c.method === 'POST' && c.url === '/api/render').body.name, 'fixture');
});

test('a failed status read resumes the same export without submitting another job', async () => {
  const app = fixture([sequenceJob()]), original = app.api.get;
  let fail = true; app.api.get = async url => {
    if (url.startsWith('/api/render/') && fail) { fail = false; throw new Error('connection interrupted'); }
    return original(url);
  };
  await app.export(); assert.match(app.node('#exOut').textContent, /retrying/);
  assert.equal(app.node('#exStart').disabled, true);
  assert.match(await app.poll(), /Sequence manifest/);
  assert.equal(app.node('#exStart').disabled, false);
  assert.equal(app.calls.filter(c => c.method === 'POST' && c.url === '/api/render').length, 1);
});

test('an edit waiting to save during preflight cannot export the older saved revision', async () => {
  const app = fixture([sequenceJob()]), original = app.api.json;
  let pendingSave = false; app.CR.previewView = () => ({ pending: pendingSave });
  app.api.json = async (method, url, body) => {
    if (url === '/api/render/preflight') pendingSave = true;
    return original(method, url, body);
  };
  await app.export();
  assert.match(app.node('#exPreflight').textContent, /saving is unresolved/);
  assert.equal(app.calls.filter(c => c.url === '/api/render').length, 0);
});

test('PNG completion has manifest/first-frame links and measured sequence QA in both views', async () => {
  const app = fixture([sequenceJob()]);
  for (const html of [await app.queue(), await app.export()]) {
    for (const text of ['PNG sequence', 'Sequence manifest', 'First frame', '12 frames', '64×48', '0.5s', '0.02 MB total', 'QA checked']) assert.ok(html.includes(text), text);
    assert.match(html, /href="\/renders\/fixture\.frames\/sequence\.json"/);
    assert.match(html, /href="\/renders\/fixture\.frames\/frame_00001\.png"/);
    assert.equal((html.match(/rel="noopener"/g) || []).length, 2);
    assert.doesNotMatch(html, />Open<|undefined|null|LUFS|dBTP/);
  }
  assert.equal(app.node('#exStart').disabled, false, 'completion releases the pending export');
  assert.equal(app.timers.length, 0, 'finished jobs stop polling');
});

test('repeated names retain distinct Open and Review links for their exact export jobs', async () => {
  const jobs = ['a'.repeat(16), 'b'.repeat(16)].map(id => ({ id, name: 'same', status: 'done',
    out: `/renders/job-${id}/same.mp4`, review_url: `/review/job-${id}` }));
  const app = fixture(jobs);
  for (const html of [await app.queue(), await app.export()]) {
    for (const job of jobs) { assert.ok(html.includes(`href="${job.out}"`)); assert.ok(html.includes(`href="${job.review_url}"`)); }
  }
  jobs[0].review_url = 'javascript:alert(1)';
  assert.doesNotMatch(await app.queue(), /javascript:/);
});

test('restored exports keep exact output links and make their restored state visible', async () => {
  const app = fixture([sequenceJob({ history: { status: 'restored', message: 'Restored after restart.' } })]);
  for (const html of [await app.queue(), await app.export()]) {
    assert.match(html, /Restored after restart/); assert.match(html, /Sequence manifest/);
  }
});

test('interrupted and unreadable records cannot look complete or offer result actions', async () => {
  const app = fixture([sequenceJob({ status: 'error', error: 'Export interrupted. Start a new export.', qa: undefined,
    history: { status: 'unreadable', message: 'Keep <original> & inspect the receipt.' } })]);
  const html = await app.queue();
  assert.match(html, /interrupted/); assert.match(html, /Keep &lt;original&gt; &amp; inspect/);
  assert.doesNotMatch(html, /href=|data-cancel=|QA checked|<original>/);
  assert.equal(app.timers.length, 0);
});

test('history-save failures warn without hiding a completed output or claiming render failure', async () => {
  const app = fixture([sequenceJob({ history: { status: 'error', message: 'Export history could not be saved.' } })]);
  for (const html of [await app.queue(), await app.export()]) {
    assert.match(html, /Export history could not be saved/); assert.match(html, /Sequence manifest/);
  }
});

test('mixed batch keeps file Open links, audio QA and each PNG result separate', async () => {
  const file = { id: 'mp4', name: 'Video', status: 'done', out: '/renders/video.mp4',
    qa: { status: 'checked', width: 1920, height: 1080, duration: 2, size_mb: 1.25, integrated_lufs: -14, true_peak_dbtp: -1, flags: [] } };
  const audio = { id: 'audio', name: 'Audio', status: 'done', out: '/renders/audio.wav',
    qa: { status: 'checked', width: null, height: null, duration: 2, size_mb: 0.19, integrated_lufs: -15, flags: [] } };
  const app = fixture([sequenceJob(), file, audio]); app.node('#exAll').checked = true;
  const queue = await app.queue(), exported = await app.export();
  assert.equal((queue.match(/>Open<\/button>/g) || []).length, 2);
  assert.match(exported, />\/renders\/video\.mp4<\/a>/);
  assert.match(exported, />\/renders\/audio\.wav<\/a>/);
  for (const html of [queue, exported]) {
    assert.equal((html.match(/PNG sequence/g) || []).length, 1);
    assert.match(html, /1920×1080 · 2s · 1\.25 MB · -14 LUFS · -1 dBTP/);
    assert.match(html, /2s · 0\.19 MB · -15 LUFS · QA checked/);
    assert.doesNotMatch(html, /undefined|null|0×0/);
  }
  assert.ok(app.calls.some(call => call.url === '/api/render_all' && call.method === 'POST'));
});

test('missing sequence fields never invent metrics, QA success or a first-frame URL', async () => {
  const app = fixture([sequenceJob({ out: undefined, frames: { directory: '/renders/unknown.frames' }, qa: { flags: [] } })]);
  for (const html of [await app.queue(), await app.export()]) {
    assert.match(html, /PNG sequence/);
    const result = html.split('</h4>').pop(); // The queue's CSV tooltip mentions QA independently of a job's metrics.
    assert.doesNotMatch(result, /href=|Sequence manifest|First frame|undefined|null|\d+ frames|\d+s|MB|LUFS|dBTP|QA/);
  }
  const singular = fixture([sequenceJob({ qa: { frame_count: 1, size_mb: 0 } })]);
  assert.match(await singular.queue(), /1 frame · 0 MB total/);
});

test('names, QA warnings/errors and render URLs are escaped in both views', async () => {
  const root = `/renders/Shot "a" & O'Brien.frames`;
  const app = fixture([sequenceJob({
    name: '<img src=x onerror="bad()"> &', out: root + '/sequence.json',
    frames: { first_frame: root + '/frame_00001.png' },
    qa: { status: 'error', flags: ['<script>bad()</script> & "warning"'], error: '<img src=x> failed',
      width: '<img>', height: 48, duration: null, size_mb: 'unknown', frame_count: false }
  })]);
  for (const html of [await app.queue(), await app.export()]) {
    assert.match(html, /&lt;img src=x onerror=&quot;bad\(\)&quot;&gt; &amp;/);
    assert.match(html, /&lt;script&gt;bad\(\)&lt;\/script&gt; &amp; &quot;warning&quot;/);
    assert.match(html, /&lt;img src=x&gt; failed/);
    assert.match(html, /href="\/renders\/Shot%20%22a%22%20&amp;%20O&#39;Brien\.frames\/sequence\.json"/);
    assert.match(html, /QA error/);
    assert.doesNotMatch(html, /<script|<img|QA checked|undefined|null|unknown MB|false frames/);
  }
});

test('unsafe or invalid metadata URLs do not become links', async () => {
  for (const url of ['javascript:alert(1)', 'data:text/html,bad', '//evil.test/x', 'https://evil.test/x', '/renders/a\\b', '/renders/a\nb', '/renders/\ud800', null]) {
    const app = fixture([sequenceJob({ out: url, frames: { first_frame: url } })]);
    for (const html of [await app.queue(), await app.export()]) {
      assert.match(html, /PNG sequence/);
      assert.doesNotMatch(html, /href=|Sequence manifest|First frame/);
    }
  }
  const file = fixture([{ id: 'bad', name: 'Bad URL', status: 'done', out: 'javascript:alert(1)' }]);
  assert.doesNotMatch(await file.queue(), /href=/);
  assert.doesNotMatch(await file.export(), /href=/);
});

test('queued/running/cancelling progress and cancellation survive the PNG handoff', async () => {
  const id = 'job"/?&', app = fixture([sequenceJob({ id, status: 'queued' })]);
  assert.match(await app.export(), /queued…/);
  assert.equal(app.node('#exStart').disabled, true);
  const queued = await app.queue();
  assert.match(queued, /data-cancel="job&quot;\/\?&amp;"/);
  assert.doesNotMatch(queued, /Sequence manifest|First frame|href=/);
  assert.ok(app.timers.some(timer => timer.ms === 1200), 'queue continues refreshing');
  await app.node('#pane-queue').cancelButtons[0].onclick(); await settle();
  assert.ok(app.calls.some(call => call.method === 'POST' && call.url === '/api/render/job%22%2F%3F%26/cancel'));
  assert.ok(app.calls.some(call => call.method === 'GET' && call.url === '/api/render/job%22%2F%3F%26'));

  app.setJobs([sequenceJob({ id, status: 'running', progress: 0.42, mode: 'incremental', reused: 2, segments: 3 })]);
  assert.match(await app.poll(), /rendering… 42%/);
  const running = await app.queue();
  assert.match(running, /running 42% · 2\/3 segments from cache/);
  assert.match(running, /width:42%/);
  assert.equal(app.node('#pane-queue').cancelButtons.length, 1);
  app.setJobs([sequenceJob({ id, status: 'cancelling', progress: 0.42 })]);
  assert.match(await app.poll(), /cancelling… 42%/);
  assert.doesNotMatch(await app.queue(), /data-cancel=|Sequence manifest|First frame/);
  app.setJobs([sequenceJob({ id, status: 'error', error: 'cancelled <img src=x>' })]);
  assert.match(await app.poll(), /error — cancelled &lt;img src=x&gt;/);
  assert.match(await app.queue(), /cancelled &lt;img src=x&gt;/);
  assert.equal(app.node('#exStart').disabled, false);
  assert.ok(!app.timers.some(timer => timer.ms === 1500), 'terminal status stops export polling');
});

test('queue escapes status and cache metadata, and QA errors never imply success', async () => {
  const app = fixture([sequenceJob({ status: '<img src=x>', mode: 'incremental', reused: '<script>x</script>', segments: '" &',
    qa: { status: '<img src=x>', error: '<script>failed</script>', flags: null } })]);
  const html = await app.queue();
  assert.match(html, /&lt;img src=x&gt;/);
  assert.match(html, /&lt;script&gt;x&lt;\/script&gt;\/&quot; &amp; segments from cache/);
  assert.match(html, /&lt;script&gt;failed&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<img|<script|QA checked|undefined/);
});

test('keyboard export keeps focus in its dialog while pending and restores Export after completion', async () => {
  const app = fixture([sequenceJob({ status: 'queued' })]);
  app.node('#exStart').focus(); await app.export();
  assert.equal(app.document.activeElement, app.node('#exportCancel'));
  assert.equal(app.node('#exStart').disabled, true);
  app.setJobs([sequenceJob()]); await app.poll();
  assert.equal(app.node('#exStart').disabled, false);
  assert.equal(app.document.activeElement, app.node('#exStart'));
});

test('finished exports preserve another field focus and never reclaim a closed dialog', async () => {
  for (const closed of [false, true]) {
    const app = fixture([sequenceJob({ status: 'queued' })]);
    app.node('#exStart').focus(); await app.export();
    app.node('#exName').focus();
    if (closed) app.node('#dlgExport').classList.remove('open');
    app.setJobs([sequenceJob({ status: 'error', error: 'cancelled' })]); await app.poll();
    assert.equal(app.document.activeElement, app.node('#exName'));
  }
});

test('the chosen color mode reaches preflight and export, including the legacy path', async () => {
  for (const mode of ['rgb', 'legacy']) {
    const app = fixture([sequenceJob()]); app.node('#exColor').value = mode;
    await app.export();
    assert.equal(app.calls.find(call => call.url === '/api/render/preflight').body.preset.color_processing, mode);
    assert.equal(app.calls.find(call => call.url === '/api/render').body.preset.color_processing, mode);
  }
});

test('loading presets restores color mode and resets legacy when an older preset omits it', () => {
  const app = fixture([]);
  app.CR.S.exportPresets = { old: { format: 'png_sequence', color_processing: 'legacy' }, modern: { format: 'png_sequence' } };
  app.node('#exPreset').value = 'old'; app.node('#exPreset').onchange();
  assert.equal(app.node('#exColor').value, 'legacy');
  app.node('#exPreset').value = 'modern'; app.node('#exPreset').onchange();
  assert.equal(app.node('#exColor').value, 'rgb');
});

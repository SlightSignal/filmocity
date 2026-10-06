const test = require('node:test');
const assert = require('node:assert/strict');
const { start } = require('../frontend/review.js');
const fs = require('node:fs'), vm = require('node:vm');

function fixture(handler) {
  class Element {
    constructor() { this.textContent = ''; this.value = ''; this.children = []; this.dataset = {}; }
    append(...values) { this.children.push(...values); }
    replaceChildren(...values) { this.children = values; }
    set innerHTML(value) { throw new Error('Review content must use text nodes'); }
  }
  const nodes = new Map();
  const document = { getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); },
    createElement() { return new Element(); } };
  const node = id => document.getElementById(id), calls = [];
  node('review').dataset.ref = 'job-123'; node('v').currentTime = 2.25;
  const api = start(document, async (url, options) => {
    calls.push({ url, options });
    const response = await handler(url, options);
    return { ok: true, json: async () => response.body ?? response, ...response };
  });
  return { node, calls, ...api };
}

test('review notes use text nodes and seek by exported time, preserving sequence offset separately', async () => {
  const app = fixture(async () => [{ name: '<img src=x onerror=bad()>', author: '<script>bad</script>', time: 32.5, review_time: 2.5 }]);
  await app.ready;
  const [seek, author, text] = app.node('list').children[0].children;
  assert.equal(text.textContent, ' <img src=x onerror=bad()>'); assert.equal(author.textContent, '<script>bad</script>');
  assert.equal(seek.textContent, '0:02.5'); seek.onclick(); assert.equal(app.node('v').currentTime, 2.5);
});

test('legacy note fields display and malformed times cannot seek', async () => {
  const app = fixture(async () => [{ text: 'legacy comment', time: 1.2 }, { name: 'bad time', time: 'NaN' }]);
  await app.ready;
  assert.equal(app.node('list').children[0].children[2].textContent, ' legacy comment');
  const seek = app.node('list').children[1].children[0]; assert.equal(seek.disabled, true);
  seek.onclick(); assert.equal(app.node('v').currentTime, 2.25);
});

test('project mismatch and other write failures keep the draft and reenable submission', async () => {
  const app = fixture(async (_, options) => options ? { ok: false, body: { detail: 'Open the original project.' } } : []);
  await app.ready; app.node('txt').value = 'Keep my note'; await app.node('send').onclick();
  assert.match(app.node('reviewStatus').textContent, /Open the original project/);
  assert.equal(app.node('txt').value, 'Keep my note'); assert.equal(app.node('send').disabled, false);
});

test('duplicate submissions are suppressed and text typed during a pending save survives', async () => {
  let resolve;
  const app = fixture(async (_, options) => options ? new Promise(r => { resolve = r; }) : []);
  await app.ready; app.node('txt').value = 'first';
  const pending = app.node('send').onclick(); await app.node('send').onclick();
  assert.equal(app.calls.filter(c => c.options).length, 1);
  app.node('txt').value = 'second'; resolve({ warning: 'Note saved; audit unavailable.' }); await pending;
  assert.equal(app.node('txt').value, 'second'); assert.match(app.node('reviewStatus').textContent, /audit unavailable/);
  assert.equal(app.node('send').disabled, false);
  const body = JSON.parse(app.calls.find(c => c.options).options.body); assert.equal(body.text, 'first'); assert.equal(body.time, 2.25);
});

test('a saved note stays successful if subsequent refresh fails', async () => {
  let reads = 0;
  const app = fixture(async (_, options) => {
    if (options) return { id: 'saved' };
    if (++reads === 1) return [];
    throw new Error('connection interrupted');
  });
  await app.ready; app.node('txt').value = 'saved'; await app.node('send').onclick();
  assert.equal(app.node('txt').value, ''); assert.match(app.node('reviewStatus').textContent, /Note saved.*Could not refresh/);
});

test('initial note failures are visible and canonical job reference is used', async () => {
  const app = fixture(async () => { throw new Error('not available'); }); await app.ready;
  assert.equal(app.calls[0].url, '/api/review/job-123/notes'); assert.match(app.node('reviewStatus').textContent, /not available/);
});

test('production Review action opens the latest completed job by its canonical URL', async () => {
  const source = fs.readFileSync(require('node:path').join(__dirname, '../frontend/panels.js'), 'utf8');
  const code = source.match(/^function reviewLink\(\).*$/m)[0], opened = [];
  const scope = { api: { get: async () => [
    { name: 'same', status: 'done', out: '/renders/job-first/same.mp4', finished: 1, review_url: '/review/job-first' },
    { name: 'same', status: 'done', out: '/renders/job-second/same.mp4', finished: 2, review_url: '/review/job-second' }
  ] }, window: { open: (...args) => opened.push(args) }, status() {} };
  vm.runInNewContext(code, scope); scope.reviewLink(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(opened[0][0], '/review/job-second');
});

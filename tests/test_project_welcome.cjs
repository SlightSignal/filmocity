const assert = require('node:assert/strict');
const test = require('node:test');
const { renderWelcome } = require('../frontend/project-welcome.js');
const { fixture } = require('./dom_fixture.cjs');
function welcome(options = {}) {
  const dom = fixture(['host']), calls = [];
  renderWelcome({ document: dom.document, host: dom.nodes.host, importFiles: () => calls.push('import'), browse: () => calls.push('browse'), findCommands: () => calls.push('commands'), clearFilters: () => calls.push('clear'), ...options });
  return { section: dom.nodes.host.children[0], calls };
}
test('empty project offers direct file import, folder browsing and command discovery', () => {
  const app = welcome({ hasMedia: false });
  assert.match(app.section.children[1].textContent, /footage/);
  for (const button of app.section.children[3].children) button.onclick();
  assert.deepEqual(app.calls, ['import', 'browse', 'commands']);
});
test('no media matches offers clearing filters without implying imported media was lost', () => {
  const app = welcome({ hasMedia: true, filtered: true });
  assert.equal(app.section.children[1].textContent, 'No matching media'); app.section.children[3].children[0].onclick();
  assert.deepEqual(app.calls, ['clear']);
});

test('production bin filtering exposes a working reset and escapes names and typed queries', () => {
  const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
  const source = fs.readFileSync(path.join(__dirname, '../frontend/app.js'), 'utf8');
  const bin = { innerHTML: '' }, host = {}, search = { focus() { this.focused = true; } }; let rendered;
  const scope = { S: { proj: { media: { m: { id: 'm', name: '<clip & title>', path: 'C:\\Media & Clips\\take.mp4', duration: 1 } }, bins: [], sequences: [{ id: 's', name: 'Cut & finish', tracks: [], fps: 30 }] }, seq: { fps: 30 }, binQuery: '" onfocus="unsafe', binFilter: null },
    $: id => id === '#bin' ? bin : id === '#binSearch' ? search : bin.innerHTML.includes('projectWelcome') ? host : null,
    document: {}, window: { FilmocityMediaInfo: require('../frontend/media-info.js'), FilmocityWelcome: { renderWelcome: options => { rendered = options; } } },
    CR: {}, mediaRate:()=>30,fmtTC: () => '00:00:00:00', seqDurOf: () => 0, wireBin() {},
  };
  scope.FilmocityMediaColor=require('../frontend/media-color.js'); scope.FilmocityProxyPreview=require('../frontend/proxy-preview.js'); vm.createContext(scope); vm.runInContext(source.slice(source.indexOf('function renderBin()'), source.indexOf('function wireBin(')), scope);
  scope.renderBin(); assert.match(bin.innerHTML, /&quot; onfocus=&quot;unsafe/); assert.equal(rendered.hasMedia, true); assert.equal(rendered.filtered, true);
  rendered.clearFilters(); assert.equal(scope.S.binQuery, ''); assert.equal(scope.S.binFilter, null); assert.equal(search.focused, true);
  assert.match(bin.innerHTML, /&lt;clip &amp; title&gt;/); assert.match(bin.innerHTML, /Cut &amp; finish/); assert.ok(!bin.innerHTML.includes('<clip & title>'));
});

// Production source Relink callbacks/save/reload with controlled DOM and network adapters.
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const base = require('./editorial-workflow-fixture.cjs');
const panels = fs.readFileSync(path.join(__dirname, '../../frontend/panels.js'), 'utf8');
const part = (a, b) => { const start = panels.indexOf(a), end = panels.indexOf(b, start); if (start < 0 || end <= start) throw Error('Missing panel section: ' + a); return panels.slice(start, end); };
function fixture({ override = '' } = {}) {
  const f = base.fixture(), s = f.scope, oldNode = s.$, timers = [];
  f.seq.name = 'Edit A'; f.clips[0].media_id = 'm'; delete f.clips[0].graphic;
  Object.assign(f.project.media.m, { duration: 100, frame_rate: '30000/1001', fps: 29.97, sample_rate: 48000, channels: 2 });
  function enhance(n, selector = '') {
    if (n.relinkFixture) return n;
    n.relinkFixture = true; const classes = new Set(); Object.assign(n, { id: selector.replace(/^#/, ''), tagName: 'DIV', children: [], disabled: false, isConnected: true, value: '',
      classList: { add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x), toggle(x, yes) { if (yes) classes.add(x); else classes.delete(x); } },
      focus() { s.document.activeElement = this; }, click() { return this.onclick?.(base.event({ target: this })); },
      matches(query) { return query.split(',').includes(this.tagName.toLowerCase()); }, getClientRects() { return this.isConnected ? [{}] : []; },
      append(...nodes) { for (const node of nodes) this.children.push(node); }, appendChild(node) { this.children.push(node); return node; }, replaceChildren(...nodes) { this.children = nodes; },
      contains(node) { return this.children.includes(node); }, remove() { this.isConnected = false; },
      querySelector(query) {
        if (this.id === 'dlgSourceRelink') {
          if (query === 'h3') return s.$('#sourceRelinkTitle');
          if (query === '.actions button:not(.primary)' || query === 'input,select,textarea,button') return s.$('#sourceRelinkCancel');
          if (query === '.actions button.primary:not(:disabled)') return s.$('#sourceRelinkApply').disabled ? null : s.$('#sourceRelinkApply');
        }
        if (this.id === 'dlgExport') return s.$('#exCancel');
        return null;
      },
      querySelectorAll() { return this.id === 'dlgSourceRelink' ? ['#sourceRelinkReview', '#sourceRelinkCancel', '#sourceRelinkChoose', '#sourceRelinkApply'].map(s.$) : []; },
    });
    return n;
  }
  s.$ = selector => enhance(oldNode(selector), selector);
  s.document.createElement = tag => { const n = enhance(base.node()); n.tagName = tag.toUpperCase(); n.attributes = {}; n.setAttribute = (k, v) => { n.attributes[k] = v; }; return n; };
  s.setTimeout = fn => { timers.push(fn); return timers.length; }; s.clearTimeout = () => {};
  s.S.binView = 'list'; s.$$ = () => [];
  vm.runInContext(base.section('let sourceRelinkOwner =', 'let editorialCommandPending =') + '\n' +
    part('function openDlg(', 'let preflightRequest =') + '\n' +
    part('const escapeExportText =', 'function renderResultLinks(') + '\n' +
    part('function relinkReviewLines(', 'async function snapshot(') + '\n' +
    part('async function renderBrowser(', 'const REASONS ='), s);
  Object.assign(s.CR, { beginSourceRelink: s.beginSourceRelink, currentSourceRelink: s.currentSourceRelink, sourceRelinkCurrent: s.sourceRelinkCurrent,
    cancelSourceRelink: s.cancelSourceRelink, inspectSourceRelink: s.inspectSourceRelink, sourceRelinkReviewCurrent: s.sourceRelinkReviewCurrent, applySourceRelink: s.applySourceRelink });
  Object.assign(s.CR.panels, { browseRelink: s.browseRelink, retireRelink: s.retireRelink }); s.window.CR = s.CR;
  s.CR.showTab = name => { s.$('#pane-' + name).classList.add('on'); };
  for (const id of ['#sourceRelinkCancel', '#sourceRelinkChoose', '#sourceRelinkApply', '#fsUp', '#fsImportAll', '#fsCancelRelink']) s.$(id).tagName = 'BUTTON';
  s.$('#sourceRelinkTitle').tagName = 'H3'; s.$('#fsPath').tagName = 'INPUT';
  f.dialog = () => ({ box: s.$('#dlgSourceRelink'), output: s.$('#sourceRelinkReview'), apply: s.$('#sourceRelinkApply'), choose: s.$('#sourceRelinkChoose'), cancel: s.$('#sourceRelinkCancel') });
  f.flushTimers = () => { for (const fn of timers.splice(0)) fn(); };
  f.begin = (id = 'm', browse = false) => {
    const original = s.CR.panels.browseRelink; if (!browse) s.CR.panels.browseRelink = () => {};
    try { return s.beginSourceRelink(id); } finally { s.CR.panels.browseRelink = original; }
  };
  f.report = (owner, extra = {}) => ({ ok: true, context: { ...s.S.context }, media_id: owner.physicalId, requested_media_id: owner.mediaId,
    path: '/replacement.mov', info: { duration: 100, width: 1920, height: 1080, fps: 29.97, has_video: true, has_audio: true }, issues: [], fingerprint: 'a'.repeat(64),
    summary: { kind: 'source_relink', changed: true, message: 'Review replacement original.', media_id: owner.physicalId, requested_media_id: owner.mediaId, scope: 'physical_source',
      source_name: 'Source.mov', path: '/replacement.mov', affected_media_ids: [owner.physicalId, 'sub'], warnings: ['Interpretation remains 30000/1001.'], cleared_fields: ['proxy', 'transcript'],
      replacement: { duration: 100, width: 1920, height: 1080, frame_rate: '30000/1001', fps: 29.97, channels: 2, sample_rate: 48000, has_video: true, has_audio: true },
      dependents: [{ media_id: 'sub', name: '<range>', kind: 'audio_alias', logical_in: 6, logical_duration: 4, native_in: 3, native_out: 5, factor: 2 }],
      uses: [{ sequence: 's1', track_id: 'v1', clip_id: 'a', locked: true, hold: true, native_in: 1, native_out: 1, logical_in: 1, logical_out: 11, duration: 10 }] }, ...extra });
  f.inspect = async (owner = f.begin(), extra = {}, ui = true) => {
    const at = f.requests.length, pending = ui ? s.relinkFromBrowser(owner, '/replacement.mov') : s.inspectSourceRelink(owner, '/replacement.mov');
    await base.until(() => f.requests.length === at + 1); const report = f.report(owner, extra); f.reply(at, report); await pending; return { owner, report };
  };
  f.complete = async (pending, at = f.requests.length - 1, extra = {}) => {
    const project = base.plain(f.project); project.media.m.path = '/replacement.mov';
    f.reply(at, { ok: true, context: base.context('r1'), changed: true, project: 'folder-a', media_id: 'm', media: project.media.m,
      affected_media_ids: ['m'], summary: { message: 'Original relinked with one saved change.' }, warnings: [], preparation: {}, ...extra });
    await base.until(() => f.requests.length === at + 2); f.requests[at + 1].resolve(base.read(project, 'r1')); return pending;
  };
  f.browserRows = () => {
    const file = enhance(s.document.createElement('div')); file.dataset = { file: 'replacement.mov' };
    const dir = enhance(s.document.createElement('div')); dir.dataset = { dir: 'child' };
    s.$$ = (query, pane) => pane === s.$('#pane-browser') ? query === '.fsrow[data-file]' ? [file] : query === '.fsrow.dir' ? [dir] : query === '.fsrow' ? [dir, file] : [] : [];
    return { file, dir };
  };
  f.folder = () => ({ path: '/files', parent: '/', roots: ['/'], dirs: ['child'], files: [{ name: 'replacement.mov', size: 1000, mtime: 1, imported: false }] });
  if (override) vm.runInContext(override, s);
  return f;
}
module.exports = { ...base, fixture, part };

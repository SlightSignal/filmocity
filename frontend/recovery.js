/* Explicit project recovery. Data stays in the local Filmocity application. */
(function (global) {
  function createRecoveryController({ document, api, state, onRestore, report, pending = () => false, drafts,
      hasUnsaved = () => false, download = () => {} }) {
    const get = id => document.getElementById(id);
    const dialog = get('dlgRecovery'), list = get('recoveryVersions'), message = get('recoveryMessage');
    const restore = get('recoveryRestore'), refresh = get('recoveryRefresh'), close = get('recoveryClose');
    const downloadButton = get('recoveryDownload');
    let catalog = null, selected = null, restoring = false, previousFocus = null, generation = 0, target = null, originContext = null;
    const tell = (text, error = false) => { message.textContent = text; message.className = error ? 'recovery-message error' : 'recovery-message'; };
    function controls(loading = false) {
      restore.disabled = loading || restoring || !selected || selected.incompatible || pending();
      refresh.disabled = loading || restoring;
      close.disabled = restoring;
      list.disabled = loading || restoring;
      dialog.setAttribute('aria-busy', String(loading || restoring));
      if (downloadButton) {
        downloadButton.disabled = restoring || !(selected?.document || state.proj);
        downloadButton.textContent = selected?.document ? 'Download selected copy' : 'Download editor copy';
      }
    }
    async function load() {
      const request = ++generation;
      selected = null; catalog = null; list.replaceChildren(); controls(true); tell('Looking for saved versions…');
      try {
        const result = await api.get('/api/projects/recovery' + (target ? '?project=' + encodeURIComponent(target) : ''));
        if (request !== generation) return;
        if (target && (state.context?.workspace !== originContext?.workspace || state.context?.project !== originContext?.project)) throw new Error("The active project changed. Close and reopen Recovery.");
        catalog = result;
        const legend = document.createElement('legend'); legend.textContent = 'Available versions'; list.appendChild(legend);
        const candidates = [...result.candidates];
        let browserNotice = '';
        const localCandidate = (id, project, context, savedAt, key) => ({
          id, name: project.name || 'Untitled', kind: id === 'editor' ? 'Open editor copy' : 'Unsaved browser draft',
          saved_at: savedAt, sequences: project.sequences?.length || 0,
          clips: (project.sequences || []).reduce((n, sq) => n + (sq.tracks || []).reduce((m, tr) => m + (tr.clips || []).length, 0), 0),
          document: JSON.parse(JSON.stringify(project)), context, key,
          incompatible: context?.project !== result.project || context?.workspace !== result.workspace,
        });
        if (state.proj && hasUnsaved()) candidates.unshift(localCandidate('editor', state.proj, state.context, Date.now() / 1000));
        if (drafts && result.workspace) {
          try {
            const stored = drafts.list(result.workspace);
            for (const draft of stored.drafts) candidates.push(localCandidate(draft.key, draft.project, draft.context, draft.saved_at, draft.key));
            if (stored.unavailable.length) browserNotice = ` ${stored.unavailable.length} browser draft(s) could not be read.`;
          } catch (error) { browserNotice = ' Browser drafts are unavailable: ' + (error.message || error); }
        }
        for (const candidate of candidates) {
          const label = document.createElement('label'); label.className = 'recovery-version';
          const radio = document.createElement('input'); radio.type = 'radio'; radio.name = 'recovery-version'; radio.value = candidate.id;
          radio.onchange = () => { selected = candidate; controls(); };
          const details = document.createElement('span'), title = document.createElement('strong'), meta = document.createElement('span');
          title.textContent = candidate.name;
          meta.textContent = `${candidate.kind} · ${new Date(candidate.saved_at * 1000).toLocaleString()} · ${candidate.sequences} sequence(s) · ${candidate.clips} clip(s)` +
            (candidate.incompatible ? ` · Open project “${candidate.context?.project || candidate.name}” from File to restore this copy; you can download it here.` : '');
          details.appendChild(title); details.appendChild(meta); label.appendChild(radio); label.appendChild(details); list.appendChild(label);
        }
        const unavailable = result.unavailable.length ? ` ${result.unavailable.length} unreadable version(s) were excluded.` : '';
        const current = result.current_valid ? 'The current project is readable.' : result.current_error;
        tell(`${current} ${candidates.length ? 'Select the version you want to restore.' : 'No readable recovery versions were found. You can close this window and open another project from File.'}${unavailable}${browserNotice}`);
        if (pending()) tell('Wait for outstanding edits to finish, then refresh these versions.');
      } catch (error) { if (request === generation) tell('Could not load recovery versions: ' + (error.message || error), true); }
      finally { if (request === generation) controls(); }
    }
    async function apply() {
      if (restoring || !selected || selected.incompatible || !catalog) return;
      if (target && (state.context?.workspace !== originContext?.workspace || state.context?.project !== originContext?.project)) { tell("The active project changed. Close and reopen Recovery.", true); return; }
      if (pending()) { tell('Wait for outstanding edits to finish before restoring.', true); controls(); return; }
      restoring = true; controls(); tell('Preserving current copies and restoring the selected version…');
      let result = null;
      try {
        const body = {
          project: catalog.project, candidate: selected.id, sha256: selected.sha256, current_sha256: catalog.current_sha256,
          editor_project: state.context?.project === catalog.project && state.context?.workspace === catalog.workspace ? state.proj || null : null,
          ...(catalog.origin_context ? { _context: catalog.origin_context } : {}),
        };
        if (selected.document) { body.draft_project = selected.document; body.workspace = catalog.workspace; }
        result = await api.json('POST', '/api/projects/recovery', body);
        if (!result || result.ok !== true) throw new Error('The server did not confirm recovery');
        await onRestore(catalog.project, !!target);
        if (selected.key) {
          try { drafts.remove(selected.key); }
          catch (error) { result.warning = (result.warning ? result.warning + ' ' : '') + 'The restored browser draft could not be cleared.'; }
        }
        dialog.close();
        report(result.warning || `Restored “${result.name}”. Previous copies are kept in recovery history.`, result.warning ? 'err' : '');
      } catch (error) {
        tell((result && result.ok ? 'The version was restored, but the editor could not reload: ' : 'Recovery did not complete: ') + (error.message || error), true);
      } finally { restoring = false; controls(); }
    }
    refresh.onclick = load; restore.onclick = apply; close.onclick = () => dialog.close();
    if (downloadButton) downloadButton.onclick = () => {
      const project = selected?.document || state.proj;
      if (!project || restoring) return;
      try { download(JSON.parse(JSON.stringify(project))); }
      catch (error) { tell('Could not download the editor copy: ' + (error.message || error), true); }
    };
    dialog.addEventListener('cancel', event => { if (restoring) event.preventDefault(); });
    // Native dialog focus containment keeps keyboard editing away from the timeline.
    dialog.addEventListener('keydown', event => event.stopPropagation());
    dialog.addEventListener('close', () => { generation++; previousFocus?.focus?.(); });
    return {
      async open(options = {}) {
        if (dialog.open) return;
        target = options.project || null; originContext = { ...state.context };
        previousFocus = document.activeElement;
        dialog.showModal(); close.focus();
        await load();
      },
    };
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { createRecoveryController };
  else {
    let controller;
    global.FilmocityRecovery = { open(cr, options = {}) {
      controller ||= createRecoveryController({ document: global.document, api: cr.api, state: cr.S,
        pending: () => cr.savePendingCount() > 0, report: cr.status,
        drafts: cr.drafts, hasUnsaved: cr.hasUnsavedEdits,
        download: project => {
          const blob = new Blob([JSON.stringify(project, null, 2)], { type: 'application/json' });
          const url = URL.createObjectURL(blob), link = document.createElement('a');
          link.href = url; link.download = (project.name || 'Filmocity').replace(/[<>:"/\\|?*\x00-\x1f]/g, '_') + '-editor-copy.json';
          document.body.appendChild(link); link.click(); link.remove();
          setTimeout(() => URL.revokeObjectURL(url), 30000);
        },
        onRestore: async (project, targeted) => { if (targeted && project !== cr.S.context?.project) { await cr.api.json('POST','/api/projects/open',{id:project}); return; } cr.S.seqId = null; if (!await cr.reloadAfterRecovery()) throw new Error('The project changed again during recovery. Refresh to inspect it.'); },
      });
      return controller.open(options);
    } };
  }
})(typeof window === 'undefined' ? globalThis : window);

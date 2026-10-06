/* Snapshot/backup selection stays bound to the inspected saved project. */
(function (global) {
  function createVersionsController({ document, catalog: inspect, restore: commit, report }) {
    const get = id => document.getElementById(id);
    const dialog = get('dlgRestore'), list = get('rsList'), message = get('rsMessage');
    const apply = get('rsRestore'), refresh = get('rsRefresh'), close = get('rsCancel');
    let kind, catalog, selected, busy = false, loading = false, generation = 0, origin;
    function tell(text, error = false) { message.textContent = text; message.className = 'recovery-message' + (error ? ' error' : ''); }
    function controls() {
      apply.disabled = busy || loading || !selected;
      refresh.disabled = busy || loading; close.disabled = busy; list.disabled = busy || loading;
      dialog.setAttribute('aria-busy', String(busy || loading));
    }
    async function load() {
      if (busy) return;
      const request = ++generation;
      catalog = selected = null; loading = true; list.replaceChildren(); controls(); tell('Loading saved versions…');
      try {
        const result = await inspect(kind);
        if (request !== generation || !dialog.open) return;
        catalog = result;
        const legend = document.createElement('legend'); legend.textContent = 'Choose a version'; list.appendChild(legend);
        for (const version of result.versions) {
          const label = document.createElement('label'); label.className = 'recovery-version';
          const radio = document.createElement('input'); radio.type = 'radio'; radio.name = 'saved-version'; radio.value = version.file;
          radio.onchange = () => { selected = version; controls(); };
          const text = document.createElement('span'), title = document.createElement('strong'), details = document.createElement('span');
          title.textContent = version.label + ' · ' + version.name;
          details.textContent = `${new Date(version.ts * 1000).toLocaleString()} · ${version.sequences} sequence(s) · ${version.clips} clip(s)`;
          text.appendChild(title); text.appendChild(details); label.appendChild(radio); label.appendChild(text); list.appendChild(label);
        }
        tell((result.versions.length ? 'Choose a version to restore.' : 'No readable saved versions are available.') +
          (result.unavailable.length ? ` ${result.unavailable.length} unreadable version(s) were excluded. Use Recovery to inspect saved project copies.` : ''));
      } catch (error) { if (request === generation) tell(error.message || String(error), true); }
      finally { if (request === generation) { loading = false; controls(); } }
    }
    apply.onclick = async () => {
      if (busy || loading || !selected || !catalog) return;
      busy = true; controls(); tell('Saving a checkpoint and restoring the selected version…');
      try {
        const result = await commit(kind, selected, catalog.context);
        if (result?.ok !== true) throw new Error('The restore was not confirmed');
        dialog.close(); report(result.warning || 'Version restored. Undo returns to your previous edit; a checkpoint is also saved.', result.warning ? 'err' : '');
      } catch (error) {
        selected = null; tell(error.message || String(error), true);
      } finally { busy = false; controls(); }
    };
    refresh.onclick = load; close.onclick = () => { if (!busy) dialog.close(); };
    dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
    dialog.addEventListener('keydown', event => event.stopPropagation());
    dialog.addEventListener('close', () => { generation++; origin?.focus?.(); });
    return { async open(nextKind) {
      if (dialog.open) return;
      kind = nextKind; origin = document.activeElement;
      get('rsTitle').textContent = kind === 'backups' ? 'Restore an automatic backup' : 'Restore a snapshot';
      dialog.showModal(); close.focus(); await load();
    } };
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { createVersionsController };
  else {
    let controller;
    global.FilmocityVersions = { open(cr, kind) {
      controller ||= createVersionsController({ document: global.document, catalog: cr.savedVersionCatalog, restore: cr.restoreSavedVersion, report: cr.status });
      return controller.open(kind);
    } };
  }
})(typeof window === 'undefined' ? globalThis : window);

/* Search existing editor commands without maintaining a second set of actions. */
(function (global) {
  const menuActions = {
    dupMedia: 'duplicate_media', newSeq: 'new_seq', importPath: 'import', save: 'save', export: 'export', frame: 'frame_export', cut: 'cut', copy: 'copy', paste: 'paste',
    pasteInsert: 'paste_insert', pasteAttr: 'paste_attr', undo: 'undo', redo: 'redo', del: 'del', rippleDel: 'ripple_del',
    selectAll: 'select_all', deselect: 'deselect', selectLabel: 'select_label', find: 'find', group: 'group', ungroup: 'ungroup',
    speed: 'speed', audioGain: 'audio_gain', unlink: 'link', enable: 'enable', hold: 'hold', extend: 'extend', replace: 'replace',
    nest: 'nest', insert: 'insert', overwrite: 'overwrite', adjLayer: 'adjust', title: 'title', fit: 'fit',
    addEdit: 'add_edit', addEditAll: 'add_edit_all', defTrans: 'dissolve', defTransSel: 'def_trans_sel', audioTrans: 'audio_trans',
    trimEdit: 'trim_edit', trimType: 'trim_type', revMatch: 'rev_match', renderSeq: 'render_seq',
    markClip: 'mark_clip', markSel: 'mark_sel', gotoIn: 'goto_in', gotoOut: 'goto_out', prevMarker: 'prev_marker', nextMarker: 'next_marker',
    lift: 'lift', extract: 'extract', selFollow: 'sel_follow', expandTracks: 'expand', minTracks: 'minimize', commands: 'commands',
  };
  const aliases = { dissolve2: 'dissolve', del2: 'del', ripple_del2: 'ripple_del' };
  const globalMenus = new Set(['recovery', 'newProject', 'openProject', 'recent', 'sample', 'shortcuts', 'guide', 'matrix', 'about', 'diag', 'tour', 'prefs']);
  const selectedRequired = new Set(['copy', 'link', 'enable', 'nest', 'ungroup', 'paste_attr', 'def_trans_sel', 'reveal', 'nudge_l', 'nudge_r', 'replace']);
  const sourceRequired = new Set(['insert', 'overwrite', 'subclip', 'replace']);
  const keywords = {
    export: 'render mp4 video deliver encode output', frame_export: 'screenshot image png still thumbnail',
    import: 'load footage video audio image file path', save: 'backup checkpoint snapshot',
    ripple_del: 'remove close gap', fit: 'zoom entire timeline', new_seq: 'timeline sequence',
    audio_gain: 'volume loudness sound', speed: 'slow fast motion duration',
    goto_timecode: 'seek jump timecode frame source program offset', prev_target_edit: 'previous cut target video audio', next_target_edit: 'next cut target video audio',
    'menu.recovery': 'unsaved draft restore lost crash recover', 'menu.shortcuts': 'keyboard hotkey keymap binding',
    'menu.upload': 'import footage video audio image files browse', 'menu.collect': 'copy verify original media relink collect files',
    'menu.sample': 'demo example tutorial start', 'panel.props': 'agent ai suggestions proposals review accept reject',
    'panel.workflow': 'guided creator footage story transcript dialogue sound captions versions deliver',
    'panel.browser': 'import files folders footage browse', 'panel.queue': 'export render progress jobs cancel',
  };
  const descriptions = {
    export: 'Choose a format and check media before exporting.', import: 'Link footage using its path on this computer.',
    save: 'Create a named checkpoint of the current project.', fit: 'Fit the whole sequence in the timeline.',
    goto_timecode: 'Enter a frame label or seconds; +/- moves relative to the current position.',
    prev_edit: 'Navigate clip boundaries on all tracks, including locked tracks.', next_edit: 'Navigate clip boundaries on all tracks, including locked tracks.',
    prev_target_edit: 'Navigate clip boundaries only on targeted video and audio tracks.', next_target_edit: 'Navigate clip boundaries only on targeted video and audio tracks.',
    'menu.recovery': 'Review saved versions and unsaved editor copies.', 'menu.upload': 'Choose files to copy into the media library.',
    'menu.shortcuts': 'Search, change, or clear keyboard shortcuts.', 'menu.collect': 'Copy and verify originals in Tasks, then apply their paths as one undoable edit.',
    'panel.props': 'Review edits suggested by an agent.', 'panel.browser': 'Browse folders and import footage.',
  };
  function normalized(text) { return String(text || '').normalize('NFKD').replace(/\p{M}/gu, '').toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim(); }
  function termScore(term, text) {
    const at = text.indexOf(term);
    if (at >= 0) return at === 0 ? 0 : text[at - 1] === ' ' ? 5 : 12;
    if (term.length < 2) return Infinity;
    let next = 0, first = -1, last = -1;
    for (let i = 0; i < text.length && next < term.length; i++) if (text[i] === term[next]) { if (first < 0) first = i; last = i; next++; }
    return next === term.length ? 45 + (last - first - term.length) : Infinity;
  }
  function searchCommands(commands, query, recent = []) {
    const q = normalized(query), terms = q.split(' ').filter(Boolean);
    return commands.map((command, order) => {
      const title = normalized(command.title), text = normalized([command.title, command.category, command.description, command.keywords, ...(command.shortcuts || [])].join(' '));
      let score = terms.reduce((sum, term) => sum + termScore(term, text), 0);
      if (q && title === q) score -= 100; else if (q && title.startsWith(q)) score -= 30;
      if (!q) { const recency = recent.indexOf(command.id); score = recency >= 0 ? -100 + recency : -(command.priority || 0); }
      return { command, score, order };
    }).filter(item => Number.isFinite(item.score)).sort((a, b) => a.score - b.score || a.order - b.order).map(item => item.command);
  }
  function selectionOf(state) {
    return (state.seq?.tracks || []).flatMap(track => (track.clips || []).filter(clip => state.sel?.has(clip.id)).map(clip => ({ clip, track })));
  }
  function disabledReason(command, state, selection) {
    if (command.global) return '';
    if (!state.proj || !state.seq) return 'Open a project first.';
    if (state.recoveryRequired) return 'Recover or open a readable project first.';
    if (state.switching || state.commandPending) return 'Wait for the current project action to finish.';
    const id = command.action;
    if (sourceRequired.has(id) && !state.src) return 'Open a clip in the Source monitor first.';
    if (['bin_delete','duplicate_media'].includes(id) && !state.binSel?.size) return 'Select media in the Project panel first.';
    if (!selectedRequired.has(id) && !['cut', 'del', 'ripple_del', 'group', 'speed', 'audio_gain', 'hold'].includes(id)) return '';
    const selected = selection || selectionOf(state);
    if (selectedRequired.has(id) && !selected.length) return 'Select clips in the timeline first.';
    if (['cut', 'del', 'ripple_del'].includes(id) && !selected.length && !(state.focus === 'project' && state.binSel?.size) && !state.gap) return 'Select clips, project media, or a timeline gap first.';
    if (id === 'group' && selected.length < 2) return 'Select at least two clips to group.';
    if (id === 'speed' && selected.filter(x => x.clip.media_id).length !== 1) return 'Select one media clip for speed and duration.';
    if (id === 'audio_gain' && !selected.some(x => state.proj.media[x.clip.media_id]?.has_audio)) return 'Select clips with audio first.';
    if (id === 'hold' && selected.filter(x => x.clip.media_id && !x.clip.hold).length !== 1) return 'Select one media clip to hold.';
    return '';
  }
  function commandCategory(id) {
    if (id.startsWith('tool_')) return 'Tools';
    if (/^(play|shuttle|stop|frame_|frames_|prev_|next_|goto_timecode$|home$|end$|loop$)/.test(id)) return 'Playback';
    return 'Editing';
  }
  function buildCatalog({ actions, keymap, menus, runAction, runMenu, panels = [] }) {
    const commands = new Map();
    for (const [id, action] of Object.entries(actions)) {
      if (aliases[id] || id === 'commands') continue;
      const shortcuts = [keymap[id], ...Object.keys(aliases).filter(alias => aliases[alias] === id).map(alias => keymap[alias])].filter(Boolean);
      commands.set('action.' + id, { id: 'action.' + id, action: id, title: action[0], category: commandCategory(id), shortcuts,
        keywords: keywords[id] || '', description: descriptions[id] || '', priority: id === 'export' ? 15 : id === 'import' ? 12 : id === 'find' ? 9 : 0,
        run: () => runAction(id) });
    }
    for (const menu of menus) {
      if (menu.kind === 'act' && menu.value === 'commands') continue;
      const action = menu.kind === 'act' ? menuActions[menu.value] : menu.kind === 'gfx' && menu.value === 'lower_third' ? 'lower_third' : null;
      const mapped = action && commands.get('action.' + action);
      if (mapped) { mapped.category = menu.category; mapped.keywords += ' ' + menu.title; continue; }
      const id = menu.kind === 'act' ? 'menu.' + menu.value : menu.kind === 'win' ? 'panel.' + menu.value : menu.kind + '.' + menu.value;
      if (commands.has(id)) continue;
      commands.set(id, { id, title: menu.kind === 'win' ? 'Show ' + menu.title : menu.title, category: menu.category,
        shortcuts: [], global: menu.kind === 'act' && globalMenus.has(menu.value), keywords: keywords[id] || '', description: descriptions[id] || '',
        priority: id === 'menu.upload' ? 20 : id === 'menu.recovery' ? 10 : id === 'menu.shortcuts' ? 8 : 0,
        run: () => runMenu(menu) });
    }
    for (const panel of panels) if (!commands.has('panel.' + panel.value)) commands.set('panel.' + panel.value, {
      id: 'panel.' + panel.value, title: 'Show ' + panel.title, category: 'Window', shortcuts: [],
      keywords: keywords['panel.' + panel.value] || '', description: descriptions['panel.' + panel.value] || '', run: panel.run,
    });
    return [...commands.values()];
  }

  function createCommandController({ document, getCommands, getState, formatShortcut, report }) {
    const get = id => document.getElementById(id), dialog = get('dlgCommands'), input = get('commandSearch');
    const list = get('commandResults'), message = get('commandMessage'), close = get('commandClose');
    let results = [], active = -1, previousFocus = null, focusReturned = true, recent = [];
    const running = new Set();
    function restoreFocus() { const target = previousFocus?.isConnected !== false ? previousFocus : get('btnCommands'); target?.focus?.(); }
    const reason = (command, state = getState(), selection) => running.has(command.id) ? 'This command is already running.' : disabledReason(command, state, selection);
    function select(index, scroll = false) {
      active = results.length ? Math.max(0, Math.min(results.length - 1, index)) : -1;
      [...list.children].forEach((node, i) => node.setAttribute('aria-selected', String(i === active)));
      if (active < 0) input.removeAttribute('aria-activedescendant');
      else { const node = list.children[active]; input.setAttribute('aria-activedescendant', node.id); if (scroll) node.scrollIntoView?.({ block: 'nearest' }); }
    }
    function render() {
      results = searchCommands(getCommands(), input.value, recent); list.replaceChildren();
      const state = getState(), selection = selectionOf(state), reasons = new Map(results.map(command => [command.id, reason(command, state, selection)]));
      if (!input.value.trim()) results.sort((a, b) => Number(!!reasons.get(a.id)) - Number(!!reasons.get(b.id)));
      for (const [index, command] of results.entries()) {
        const row = document.createElement('div'); row.id = 'command-result-' + index; row.className = 'command-result'; row.setAttribute('role', 'option');
        const unavailable = reasons.get(command.id); row.setAttribute('aria-disabled', String(!!unavailable));
        const copy = document.createElement('span'), title = document.createElement('strong'), detail = document.createElement('span');
        copy.className = 'command-copy'; title.textContent = command.title;
        detail.textContent = `${command.category}${unavailable || command.description ? ' · ' + (unavailable || command.description) : ''}`;
        copy.appendChild(title); copy.appendChild(detail); row.appendChild(copy);
        for (const shortcut of command.shortcuts || []) { const key = document.createElement('kbd'); key.textContent = formatShortcut(shortcut); row.appendChild(key); }
        row.onmousedown = event => event.preventDefault(); // Keep typing focus on the combobox.
        row.onclick = () => { select(index); void activate(); };
        list.appendChild(row);
      }
      select(0); input.setAttribute('aria-expanded', String(results.length > 0)); list.hidden = results.length === 0;
      message.textContent = results.length ? `${results.length} commands. Use ↑ and ↓ to choose; Enter runs the selected command.` : 'No matching commands. Try “import”, “captions”, or “recovery”.';
    }
    function dismiss() { input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-activedescendant'); focusReturned = true; dialog.close(); restoreFocus(); }
    async function activate() {
      if (!dialog.open || active < 0) return;
      // Re-evaluate context at activation: another client can switch projects
      // or change selection while this dialog is open.
      const command = getCommands().find(item => item.id === results[active]?.id);
      if (!command) { render(); return; }
      const unavailable = reason(command);
      if (unavailable) { list.children[active]?.setAttribute('aria-disabled', 'true'); message.textContent = unavailable; return; }
      running.add(command.id); dismiss();
      try { await command.run(); recent = [command.id, ...recent.filter(id => id !== command.id)].slice(0, 8); }
      catch (error) { report(`Could not run “${command.title}”: ${error.message || error}`, 'err'); }
      finally { running.delete(command.id); }
    }
    input.oninput = render;
    input.addEventListener('keydown', event => {
      if (event.isComposing || event.keyCode === 229) return;
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); select(active + (event.key === 'ArrowDown' ? 1 : -1), true); }
      else if (event.key === 'Enter' && !event.repeat) { event.preventDefault(); void activate(); }
    });
    dialog.addEventListener('keydown', event => event.stopPropagation());
    dialog.addEventListener('cancel', event => { event.preventDefault(); dismiss(); });
    dialog.addEventListener('close', () => { if (dialog.open) return; if (!focusReturned) restoreFocus(); focusReturned = true; });
    close.onclick = dismiss;
    return { open(query = '') {
      if (dialog.open) { input.focus(); return; }
      if (document.querySelector('dialog[open], .dialog.open')) return;
      const origin = document.activeElement;
      previousFocus = origin?.closest?.('.menu')?.querySelector(':scope > button') || origin;
      focusReturned = false; input.value = query;
      render(); dialog.showModal(); input.focus(); input.select?.();
    } };
  }
  function readMenus(document) {
    return [...document.querySelectorAll('.menubar .dd button')].map(node => {
      const kind = ['act', 'new', 'sfx', 'gfx', 'align', 'ws', 'win'].find(key => node.dataset[key]);
      if (!kind) return null;
      const copy = node.cloneNode(true); copy.querySelectorAll('.k').forEach(el => el.remove());
      return { kind, value: node.dataset[kind], title: copy.textContent.trim(), category: node.closest('.menu').querySelector(':scope > button').textContent.trim(), node };
    }).filter(Boolean);
  }
  function updateShortcutHints(cr, document = global.document) {
    const format = global.FilmocityKeyboard.formatShortcut;
    for (const menu of readMenus(document)) {
      const id = menu.kind === 'act' ? menuActions[menu.value] : menu.kind === 'gfx' && menu.value === 'lower_third' ? 'lower_third' : null;
      if (!id || !cr.ACTIONS[id]) continue;
      let hint = menu.node.querySelector('.k');
      if (!hint) { hint = document.createElement('span'); hint.className = 'k'; menu.node.appendChild(hint); }
      hint.textContent = format(cr.S.keymap[id]);
    }
    for (const [selector, action] of [['btnCommands', 'commands'], ['btnUndo', 'undo'], ['btnRedo', 'redo'], ['btnExport', 'export']]) {
      const button = document.getElementById(selector); if (!button) continue;
      const shortcut = format(cr.S.keymap[action]);
      button.title = cr.ACTIONS[action][0] + (shortcut ? ` (${shortcut})` : '');
      const hint = button.querySelector('kbd'); if (hint) { hint.textContent = shortcut; hint.hidden = !shortcut; }
      button.removeAttribute('aria-keyshortcuts');
      if (shortcut) button.setAttribute('aria-keyshortcuts', shortcut.replace('Ctrl+', 'Control+').replace(/Esc$/,'Escape').replace('←', 'ArrowLeft').replace('→', 'ArrowRight').replace('↑', 'ArrowUp').replace('↓', 'ArrowDown'));
    }
  }
  const api = { buildCatalog, searchCommands, disabledReason, createCommandController, updateShortcutHints };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else {
    let controller;
    global.FilmocityCommands = { ...api, open(cr, query = '') {
      controller ||= createCommandController({ document: global.document, getState: () => cr.S, formatShortcut: global.FilmocityKeyboard.formatShortcut, report: cr.status,
        getCommands: () => buildCatalog({ actions: cr.ACTIONS, keymap: cr.S.keymap, menus: readMenus(global.document), runAction: id => cr.ACTIONS[id][2](),
          runMenu: menu => menu.kind === 'act' ? cr.panels.menuAction(menu.value) : menu.node.onclick?.(),
          panels: [{ value: 'browser', title: 'Media Browser', run: () => cr.showTab('browser') }],
        }),
      });
      document.querySelectorAll('.menu.open').forEach(menu => menu.classList.remove('open'));
      controller.open(query);
    } };
  }
})(typeof window === 'undefined' ? globalThis : window);

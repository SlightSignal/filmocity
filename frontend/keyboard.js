/* Shared shortcut interpretation, display, and editing. */
(function (global) {
  const modifierKeys = new Set(['control', 'alt', 'shift', 'meta', 'altgraph']);
  const shifted = { Digit0: [')', '0'], Digit1: ['!', '1'], Digit2: ['@', '2'], Digit3: ['#', '3'],
    Digit4: ['$', '4'], Digit5: ['%', '5'], Digit6: ['^', '6'], Digit7: ['&', '7'], Digit8: ['*', '8'], Digit9: ['(', '9'],
    Equal: ['+', '='], Minus: ['_', '-'], Slash: ['?', '/'], Backquote: ['~', '`'],
    BracketLeft: ['{', '['], BracketRight: ['}', ']'], Backslash: ['|', '\\'], Semicolon: [':', ';'], Quote: ['"', "'"], Comma: ['<', ','], Period: ['>', '.'] };
  function comboOf(event) {
    if (event.isComposing || event.keyCode === 229 || event.getModifierState?.('AltGraph')) return '';
    let key = String(event.key || '').toLowerCase();
    if (!key || modifierKeys.has(key) || ['dead', 'unidentified', 'process'].includes(key)) return '';
    // Keep the published Shift+/ and Shift+= combinations usable on Windows.
    // Only translate a known shifted pair; other keyboard layouts keep e.key.
    const pair = shifted[event.code];
    if (event.shiftKey && pair && key === pair[0]) key = pair[1];
    return (event.ctrlKey || event.metaKey ? 'ctrl+' : '') + (event.altKey ? 'alt+' : '') + (event.shiftKey ? 'shift+' : '') + key;
  }
  function formatShortcut(shortcut) {
    if (!shortcut) return '';
    const parts = []; let key = shortcut;
    for (;;) { const match = /^(ctrl|alt|shift)\+/.exec(key); if (!match) break;
      parts.push({ ctrl: 'Ctrl', alt: 'Alt', shift: 'Shift' }[match[1]]); key = key.slice(match[0].length); }
    parts.push(({ ' ': 'Space', escape: 'Esc', arrowleft: '←', arrowright: '→', arrowup: '↑', arrowdown: '↓',
      backspace: 'Backspace', delete: 'Delete', enter: 'Enter', home: 'Home', end: 'End', tab: 'Tab', pageup: 'Page Up', pagedown: 'Page Down' })[key] || (key.length === 1 ? key.toUpperCase() : key.replace(/^./, c => c.toUpperCase())));
    return parts.join('+');
  }
  function readKeymap(actions, saved) {
    const assigned = new Set(Object.keys(actions).filter(id => typeof saved?.[id] === 'string' && saved[id]).map(id => saved[id]));
    return Object.fromEntries(Object.entries(actions).map(([id, action]) => [id,
      typeof saved?.[id] === 'string' ? saved[id] : assigned.has(action[1]) ? '' : action[1]]));
  }
  function resolveAction(event, keymap, actions) {
    const combo = comboOf(event);
    return combo ? Object.keys(actions).find(id => keymap[id] === combo) : undefined;
  }

  function createShortcutController({ document, actions, getKeymap, save, changed = () => {} }) {
    const get = id => document.getElementById(id), dialog = get('dlgKeys'), search = get('keySearch');
    const list = get('keyList'), message = get('keyMessage'), done = get('keysClose'), reset = get('keysReset');
    let editing = null, pending = false, previousFocus = null, focusReturned = true;
    function restoreFocus() { const target = previousFocus?.isConnected !== false ? previousFocus : get('btnCommands'); target?.focus?.(); }
    const tell = (text, error = false) => { message.textContent = text; message.className = error ? 'shortcut-message error' : 'shortcut-message'; };
    function render(focusId) {
      list.replaceChildren(); const map = getKeymap(), query = search.value.toLocaleLowerCase().trim(); let count = 0, focusTarget = null;
      for (const [id, action] of Object.entries(actions)) {
        const shortcut = formatShortcut(map[id]);
        if (query && !`${action[0]} ${shortcut}`.toLocaleLowerCase().includes(query)) continue;
        count++;
        const row = document.createElement('div'); row.className = 'shortcut-row';
        const name = document.createElement('span'); name.textContent = action[0];
        const bind = document.createElement('button'); bind.className = 'shortcut-binding'; bind.dataset.key = id;
        bind.textContent = editing === id ? 'Press a combination…' : shortcut || 'Unassigned';
        bind.setAttribute('aria-label', `${action[0]}: ${shortcut || 'Unassigned'}. Change shortcut`);
        bind.disabled = pending; bind.onclick = () => { editing = id; tell('Press a combination. Escape cancels; modifier keys can be held together.'); render(id); };
        const clear = document.createElement('button'); clear.textContent = 'Clear'; clear.disabled = pending || !map[id];
        clear.setAttribute('aria-label', 'Clear shortcut for ' + action[0]); clear.onclick = () => commit(id, '');
        row.appendChild(name); row.appendChild(bind); row.appendChild(clear); list.appendChild(row);
        if (focusId === id) focusTarget = bind;
      }
      if (!count) { const empty = document.createElement('p'); empty.className = 'shortcut-empty'; empty.textContent = 'No matching commands. Try a command name or a shortcut.'; list.appendChild(empty); }
      done.disabled = pending; reset.disabled = pending; search.disabled = pending;
      dialog.setAttribute('aria-busy', String(pending));
      if (focusId && !pending) (focusTarget || search).focus();
    }
    async function persist(map, text, focusId) {
      if (pending) return;
      editing = null; pending = true; tell('Saving shortcuts…'); render();
      try {
        await save(map); changed(); tell(text);
      } catch (error) { tell('Shortcut changes were not confirmed: ' + (error.message || error), true); }
      finally { pending = false; render(focusId); if (!focusId) reset.focus(); }
    }
    function commit(id, combo) {
      const conflict = combo && Object.keys(actions).find(other => other !== id && getKeymap()[other] === combo);
      if (conflict) { editing = null; tell(`${formatShortcut(combo)} is assigned to “${actions[conflict][0]}”. Clear that binding first, or choose another combination.`, true); render(id); return; }
      return persist({ ...getKeymap(), [id]: combo }, combo ? `Shortcut saved for ${actions[id][0]}.` : `Shortcut cleared for ${actions[id][0]}.`, id);
    }
    function dismiss() { if (pending) return; editing = null; focusReturned = true; dialog.close(); restoreFocus(); }
    search.oninput = () => { editing = null; render(); };
    done.onclick = dismiss;
    reset.onclick = () => persist(readKeymap(actions), 'Default shortcuts restored.');
    dialog.addEventListener('cancel', event => { event.preventDefault(); if (editing) { const id = editing; editing = null; tell('Shortcut change canceled.'); render(id); } else dismiss(); });
    dialog.addEventListener('keydown', event => {
      event.stopPropagation();
      if (!editing) return;
      if (event.isComposing || event.keyCode === 229) return;
      event.preventDefault();
      if (event.key === 'Escape') { const id = editing; editing = null; tell('Shortcut change canceled.'); render(id); return; }
      if (event.key === 'Tab') { tell('Tab is reserved for moving between controls. Choose another combination.', true); return; }
      if (event.repeat) return;
      const combo = comboOf(event); if (combo) void commit(editing, combo);
    }, true);
    dialog.addEventListener('close', () => { if (dialog.open) return; editing = null; if (!focusReturned) restoreFocus(); focusReturned = true; });
    return { open() { if (dialog.open) return; const origin = document.activeElement; previousFocus = origin?.closest?.('.menu')?.querySelector(':scope > button') || origin;
      focusReturned = false; editing = null; search.value = ''; tell('Choose a command to change its shortcut. Changes are saved on this workstation.'); render(); dialog.showModal(); search.focus(); } };
  }
  const api = { comboOf, formatShortcut, readKeymap, resolveAction, createShortcutController };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else global.FilmocityKeyboard = api;
})(typeof window === 'undefined' ? globalThis : window);

/* Bounded transcript rendering and small, version-bound correction drafts. */
(function (global) {
  const PAGE_SIZE = 30;
  const normalize = text => String(text).normalize('NFKD').replace(/\p{M}/gu, '').toLocaleLowerCase();
  function passages(words) {
    const result = []; let group = [];
    words.forEach((word, i) => {
      if (group.length && (group.length >= 12 || word.s - words[group.at(-1)].e > .65)) { result.push(group); group = []; }
      group.push(i);
      if (/[.!?]$/.test(word.w.trim())) { result.push(group); group = []; }
    });
    if (group.length) result.push(group);
    return result;
  }
  const time = seconds => `${Math.floor(seconds / 60)}:${(seconds % 60).toFixed(1).padStart(4, '0')}`;
  function editsFor(words, edits) {
    return Object.entries(edits).map(([index, text]) => ({ index: Number(index), expected: words[index].w, text, s: words[index].s, e: words[index].e }));
  }
  function compatible(draft, context, sequence, words) {
    return draft?.version === 1 && draft.sequence === sequence && ['workspace', 'project', 'revision'].every(k => draft.context?.[k] === context[k]) &&
      Array.isArray(draft.edits) && draft.edits.every(e => Number.isInteger(e.index) && words[e.index]?.w === e.expected && words[e.index]?.s === e.s && words[e.index]?.e === e.e);
  }
  function createDraftStore(getStorage, writer) {
    const prefix = 'filmocity:transcript-draft:v1:'; let serial = 0;
    const scope = context => prefix + [context.workspace, context.project].map(encodeURIComponent).join(':') + ':';
    function save(context, sequence, edits, previous = null) {
      const storage = getStorage(), base = scope(context) + encodeURIComponent(writer) + ':';
      if (previous && !previous.startsWith(base)) throw new Error('The correction draft belongs to another editor');
      let key = previous;
      if (!key) { do { key = base + (++serial); } while (storage.getItem(key) !== null); }
      const value = { version: 1, context: { ...context }, sequence, edits, saved_at: Date.now() };
      const raw = JSON.stringify(value); storage.setItem(key, raw);
      if (storage.getItem(key) !== raw) throw new Error('The browser could not verify the correction draft');
      return key;
    }
    function list(context, sequence) {
      const storage = getStorage(), drafts = [], unavailable = [];
      for (let i = 0; i < storage.length; i++) {
        const key = storage.key(i); if (!key?.startsWith(scope(context))) continue;
        try {
          const raw = storage.getItem(key), value = JSON.parse(raw);
          if (value.version !== 1 || !Array.isArray(value.edits) || value.edits.length > 500 ||
              !value.context || value.context.workspace !== context.workspace || value.context.project !== context.project ||
              !value.edits.every(e => Number.isInteger(e.index) && e.index >= 0 && typeof e.expected === 'string' && typeof e.text === 'string' && e.text.length <= 200 && Number.isFinite(e.s) && Number.isFinite(e.e))) throw new Error('Invalid correction draft');
          if (value.sequence === sequence) drafts.push({ ...value, key, raw });
        } catch (error) { unavailable.push({ key, reason: error.message }); }
      }
      drafts.sort((a, b) => b.saved_at - a.saved_at); return { drafts, unavailable };
    }
    function remove(key, expectedRaw) {
      if (typeof key !== 'string' || !key.startsWith(prefix)) throw new Error('Invalid correction draft key');
      const storage = getStorage();
      if (expectedRaw !== undefined && storage.getItem(key) !== expectedRaw) throw new Error('The correction draft changed in another editor');
      storage.removeItem(key);
    }
    return { save, list, remove };
  }
  function create({ document, container, words, keep, draft, onSave, onPreview, onDraft = () => {}, onMessage = () => {} }) {
    draft.edits ||= {}; draft.page ||= 0; draft.query ||= '';
    const records = passages(words).map((ids, i) => ({ id: i, ids, text: ids.map(i => words[i].w).join(' '),
      search: normalize(ids.map(i => words[i].w).join(' ')), low: ids.some(i => typeof words[i].p === 'number' && words[i].p < .6) }));
    let matches = [], anchor = null, saving = false, visible = [];
    const el = (tag, text, cls) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (cls) node.className = cls; return node; };
    const append = (p, ...nodes) => { nodes.forEach(n => p.appendChild(n)); return p; };
    const button = (text, fn, parent, cls) => { const node = el('button', text, cls); node.type = 'button'; node.addEventListener('click', fn); parent.appendChild(node); return node; };
    const check = (text, parent, fn) => { const label = el('label', undefined, 'wf-check'), node = el('input'); node.type = 'checkbox'; node.addEventListener('change', fn); append(label, node, el('span', text)); parent.appendChild(label); return node; };
    const searchLabel = el('label', undefined, 'wf-field'), search = el('input'); search.type = 'search'; search.value = draft.query;
    append(searchLabel, el('span', 'Find a passage'), search); container.appendChild(searchLabel);
    const low = check('Only passages with low-confidence words', container, () => { draft.low = low.checked; draft.page = 0; filter(); }); low.checked = !!draft.low;
    const tools = el('div', undefined, 'wf-transcript-tools'); container.appendChild(tools);
    button('Keep all', () => { words.forEach((_, i) => keep.add(i)); updateSelection(); }, tools);
    button('Clear selection', () => { keep.clear(); updateSelection(); }, tools);
    button('Keep search results', () => { matches.forEach(r => r.ids.forEach(i => keep.add(i))); updateSelection(); }, tools);
    button('Exclude search results', () => { matches.forEach(r => r.ids.forEach(i => keep.delete(i))); updateSelection(); }, tools);
    const count = el('p', '', 'wf-count'); count.setAttribute('role', 'status'); container.appendChild(count);
    const pageInfo = el('p', '', 'wf-note'); pageInfo.setAttribute('aria-live', 'polite'); container.appendChild(pageInfo);
    const pager = el('div', undefined, 'wf-pager'); container.appendChild(pager);
    const prev = button('Previous passages', () => { draft.page--; renderRows(); prev.focus(); }, pager);
    const next = button('Next passages', () => { draft.page++; renderRows(); next.focus(); }, pager);
    const list = el('div', undefined, 'wf-list wf-passages'); container.appendChild(list);
    const details = el('div', undefined, 'wf-word-editor'); container.appendChild(details);
    const pending = el('p', '', 'wf-note'); pending.setAttribute('role', 'status'); container.appendChild(pending);
    const save = button('Save word corrections', async () => {
      if (saving || !Object.keys(draft.edits).length) return;
      const edits = editsFor(words, draft.edits);
      if (edits.length > 500 || edits.some(e => !e.text.trim() || e.text.trim().length > 120 || /\s/u.test(e.text.trim()))) {
        onMessage('Use one word per correction, up to 120 characters. Save up to 500 corrections at once.', true); return;
      }
      saving = true; save.disabled = true;
      try { await onSave(edits.map(({ index, expected, text }) => ({ index, expected, text }))); }
      catch (error) { onMessage(error.message, true); }
      finally { saving = false; updatePending(); }
    }, container, 'primary');
    function updatePending() {
      const n = Object.keys(draft.edits).length;
      pending.textContent = n ? `${n} word correction${n === 1 ? '' : 's'} pending. Save to update the transcript; timing stays unchanged.` : 'Edit words to correct names or punctuation. Selection changes which words are kept in a new cut.';
      save.disabled = saving || !n;
    }
    function updateSelection() {
      count.textContent = `${keep.size} of ${words.length} words kept`;
      visible.forEach(({ box, ids }) => { const n = ids.filter(i => keep.has(i)).length; box.checked = n === ids.length; box.indeterminate = n > 0 && n < ids.length; });
      details.querySelectorAll?.('input[data-word]')?.forEach(box => { box.checked = keep.has(Number(box.dataset.word)); });
    }
    function openWords(record, focus = true) {
      draft.open = record.id; details.replaceChildren();
      const title = el('h4', `Words · ${time(words[record.ids[0]].s)}`); title.tabIndex = -1;
      append(details, title, el('p', 'Check words to keep them. Shift-click spans a range. Correct one word at a time without changing its timing.', 'wf-note'));
      for (const i of record.ids) {
        const row = el('div', undefined, 'wf-word-row'), word = words[i];
        const box = check(`Keep word ${i + 1}`, row, () => { box.checked ? keep.add(i) : keep.delete(i); anchor = i; updateSelection(); });
        box.checked = keep.has(i); box.dataset.word = String(i);
        box.addEventListener('click', event => {
          if (event.shiftKey && anchor !== null) {
            for (let j = Math.min(i, anchor); j <= Math.max(i, anchor); j++) box.checked ? keep.add(j) : keep.delete(j);
            updateSelection();
          }
        });
        const input = el('input'); input.type = 'text'; input.value = Object.hasOwn(draft.edits, i) ? draft.edits[i] : word.w; input.maxLength = 120;
        input.setAttribute('aria-label', `Correct word ${i + 1}: ${word.w}`); input.autocomplete = 'off';
        input.addEventListener('input', () => {
          if (!Object.hasOwn(draft.edits, i) && Object.keys(draft.edits).length >= 500) {
            input.value = word.w; onMessage('Save the first 500 corrections before adding more.', true); return;
          }
          if (input.value === word.w) delete draft.edits[i]; else draft.edits[i] = input.value;
          updatePending(); onDraft(editsFor(words, draft.edits));
        });
        row.appendChild(input);
        const play = button('▶', () => onPreview(word.s, word.e), row); play.setAttribute('aria-label', `Play word ${i + 1} at ${time(word.s)}`);
        details.appendChild(row);
      }
      button('Close word editor', () => { draft.open = null; details.replaceChildren(); }, details);
      if (focus) title.focus();
    }
    function renderRows() {
      const pages = Math.max(1, Math.ceil(matches.length / PAGE_SIZE)); draft.page = Math.max(0, Math.min(pages - 1, draft.page));
      const start = draft.page * PAGE_SIZE, page = matches.slice(start, start + PAGE_SIZE);
      list.replaceChildren(); visible = [];
      pageInfo.textContent = matches.length ? `Passages ${start + 1}–${start + page.length} of ${matches.length} · page ${draft.page + 1} of ${pages}` : 'No passages match. Change the search or confidence filter; kept words are unchanged.';
      prev.disabled = draft.page === 0; next.disabled = draft.page + 1 >= pages;
      page.forEach(record => {
        const row = el('div', undefined, 'wf-passage');
        const box = check(record.text, row, () => { record.ids.forEach(i => box.checked ? keep.add(i) : keep.delete(i)); updateSelection(); });
        button(`Play passage at ${time(words[record.ids[0]].s)}`, () => onPreview(words[record.ids[0]].s, Math.max(...record.ids.map(i => words[i].e))), row);
        button('Words…', () => openWords(record), row);
        if (record.low) append(row, el('span', 'Check recognition', 'wf-confidence'));
        list.appendChild(row); visible.push({ box, ids: record.ids });
      });
      updateSelection();
    }
    function filter() {
      const terms = normalize(draft.query).split(/\s+/).filter(Boolean);
      matches = records.filter(r => (!draft.low || r.low) && terms.every(term => r.search.includes(term)));
      renderRows();
    }
    search.addEventListener('input', () => { draft.query = search.value; draft.page = 0; filter(); });
    filter(); updatePending();
    if (Number.isInteger(draft.open) && records[draft.open]) openWords(records[draft.open], false);
    return { refresh: updateSelection, get visibleCount() { return visible.length; }, get total() { return records.length; } };
  }
  const api = { create, passages, normalize, createDraftStore, compatible, editsFor, PAGE_SIZE };
  global.FilmocityTranscript = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window === 'undefined' ? globalThis : window);

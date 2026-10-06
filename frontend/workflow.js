/* Guided creator workflow. All persistent changes use the guarded editor adapter. */
(function (global) {
  const STEPS = ['Footage', 'Story', 'Sound', 'Captions', 'Versions', 'Deliver'];
  const Transcript = global.FilmocityTranscript || (typeof require !== 'undefined' ? require('./transcript-editor.js') : null);
  function passages(words) { return Transcript.passages(words); }
  function readableTime(value) { return `${Math.floor(value / 60)}:${(value % 60).toFixed(1).padStart(4, '0')}`; }
  function editStamp(project) {
    // Thumbnail/proxy completion must not discard a passage or footage selection.
    const media = Object.fromEntries(Object.entries(project.media || {}).map(([id, item]) => [id,
      Object.fromEntries(Object.entries(item).filter(([key]) => !['thumb', 'strip', 'wave', 'proxy', 'proxy_status', 'proxy_error', 'ingest_error', 'status', 'task_id'].includes(key)))]));
    return JSON.stringify({ ...project, updated: null, media });
  }
  function create(CR, document, pane) {
    const state = { step: 0, basis: null, busy: false, changed: false, selected: [], keep: null, draft: {}, message: '', error: false };
    let lastRevision, heading, content, notice, message, fieldset, tabs;
    const draftStore = CR.transcriptDrafts || (global.document ? Transcript.createDraftStore(() => global.localStorage, global.crypto?.randomUUID?.() || String(Date.now()) + Math.random()) : null);
    const correctionMemory = new Map();
    const correctionKey = basis => JSON.stringify([basis.workspace, basis.project, basis.sequence]);
    function persistCorrections(edits) {
      const key = correctionKey(state.basis);
      if (!edits.length) {
        correctionMemory.delete(key);
        if (state.correctionKey && draftStore) { try { draftStore.remove(state.correctionKey); } catch (error) { feedback(error.message, true); } }
        state.correctionKey = null; return;
      }
      const value = { version: 1, context: { ...state.basis }, sequence: state.basis.sequence, edits, saved_at: Date.now() };
      correctionMemory.set(key, value);
      try {
        if (!draftStore) throw new Error('Browser draft storage unavailable');
        state.correctionKey = draftStore.save(state.basis, state.basis.sequence, edits, state.correctionKey);
        value.key = state.correctionKey;
      } catch (error) { feedback('Corrections remain in this window, but the browser could not save their draft. Download the draft before closing. ' + error.message, true); }
    }
    function downloadCorrectionDraft(value) {
      if (CR.downloadTranscriptDraft) return CR.downloadTranscriptDraft(value);
      const url = global.URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
      const link = el('a'); link.href = url; link.download = 'Filmocity-word-corrections.json'; link.click();
      global.setTimeout(() => global.URL.revokeObjectURL(url), 1000);
    }

    const el = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
    const append = (parent, ...children) => { children.forEach(child => parent.appendChild(child)); return parent; };
    function button(text, fn, parent = content, primary = false) {
      const node = el('button', text, primary ? 'primary' : ''); node.type = 'button'; node.addEventListener('click', fn); parent.appendChild(node); return node;
    }
    function note(text, parent = content) { return append(parent, el('p', text, 'wf-note')); }
    function input(label, key, value, type = 'text', parent = content, options = {}) {
      const wrap = el('label', undefined, 'wf-field'), node = el('input'); node.type = type; node.value = state.draft[key] ?? value;
      Object.assign(node, options); node.addEventListener('input', () => { state.draft[key] = node.value; });
      append(wrap, el('span', label), node); parent.appendChild(wrap); return node;
    }
    function select(label, key, options, parent = content) {
      const wrap = el('label', undefined, 'wf-field'), node = el('select');
      options.forEach(([value, text]) => { const opt = el('option', text); opt.value = value; node.appendChild(opt); });
      node.value = state.draft[key] ?? options[0][0]; node.addEventListener('change', () => { state.draft[key] = node.value; });
      append(wrap, el('span', label), node); parent.appendChild(wrap); return node;
    }
    function checkbox(text, checked, fn, parent = content) {
      const row = el('label', undefined, 'wf-check'), node = el('input'); node.type = 'checkbox'; node.checked = checked;
      node.addEventListener('change', () => fn(node.checked)); append(row, node, el('span', text)); parent.appendChild(row); return node;
    }
    function context() { return CR.S.context && { ...CR.S.context, sequence: CR.S.seq?.id }; }
    function samePlace(a, b) { return a && b && a.workspace === b.workspace && a.project === b.project && a.sequence === b.sequence; }
    function reset() { state.basis = context(); state.stamp = editStamp(CR.S.proj); state.changed = false; state.selected = []; state.keep = null; state.draft = {}; state.correctionKey = null; state.restoredCorrection = null; lastRevision = CR.S.context?.revision; }
    function feedback(text, error = false) { state.message = text; state.error = error; message.textContent = text; message.className = error ? 'wf-message wf-error' : 'wf-message'; }
    function readiness() {
      const seq = CR.S.seq;
      if (!seq) return 'Open a project to begin.';
      const clips = seq.tracks.reduce((n, t) => n + t.clips.length, 0);
      return `${clips} clips · ${(seq.transcript || []).length} words · ${(seq.captions || []).length} captions`;
    }
    async function run(action, body, next) {
      if (state.busy) return;
      if (state.changed) { feedback('The edit changed. Refresh this step before applying.', true); return; }
      if (action !== 'transcript_edit' && Object.keys(state.draft.transcript?.edits || {}).length) { feedback('Save or discard the pending word corrections before applying another workflow action.', true); return; }
      const oldBasis = { ...state.basis }, oldWords = CR.S.seq?.transcript || [], oldKeep = state.keep, oldDraft = state.draft;
      const correction = state.correctionKey, restored = state.restoredCorrection;
      let outcome;
      state.busy = true; fieldset.disabled = true; pane.setAttribute('aria-busy', 'true');
      tabs.forEach(tab => { tab.disabled = true; });
      feedback(action === 'transcribe' ? 'Queueing transcription…' : action === 'import' ? 'Copying and inspecting footage…' : 'Applying to the saved project…');
      try {
        if (action === 'transcribe' && CR.startTranscription) {
          const queued = await CR.startTranscription(body, state.basis);
          feedback(queued.message); return queued;
        }
        const result = action === 'import' ? await CR.workflowUpload(body, state.basis) : await CR.workflowAction(action, body, state.basis);
        let draftWarning = '';
        if (action === 'transcript_edit') {
          correctionMemory.delete(correctionKey(oldBasis));
          try {
            if (correction && draftStore) draftStore.remove(correction);
            if (restored?.key && restored.key !== correction && draftStore) draftStore.remove(restored.key, restored.raw);
          } catch (error) { draftWarning = 'Saved, but an older correction draft could not be cleared: ' + error.message; }
        }
        reset();
        const newWords = CR.S.seq?.transcript || [];
        if (action === 'transcript_edit' && CR.S.seq?.id === oldBasis.sequence && oldWords.length === newWords.length && oldWords.every((w, i) => w.s === newWords[i].s && w.e === newWords[i].e)) {
          state.keep = oldKeep; state.draft = { ...oldDraft, transcript: { ...oldDraft.transcript, edits: {} } };
        }
        if (Number.isInteger(next)) state.step = next;
        outcome = result; feedback([result.message, result.warning, draftWarning].filter(Boolean).join(' '));
      } catch (error) { feedback(error.message || String(error), true); }
      finally {
        state.busy = false; pane.setAttribute('aria-busy', 'false');
        refresh(); renderStep();
      }
      return outcome;
    }
    function footage() {
      note('Choose footage in story order. Build a new sequence when you are ready.');
      delete state.draft.upload;
      const upload = input('Add files to this project', 'upload', '', 'file', content, { multiple: true, accept: 'video/*,audio/*,image/*' });
      upload.addEventListener('change', () => { if (upload.files?.length) run('import', Array.from(upload.files)); });
      const media = Object.values(CR.S.proj.media || {});
      const count = el('p', '', 'wf-count'); content.appendChild(count);
      const ordered = el('div', undefined, 'wf-order'); content.appendChild(ordered);
      function order() {
        count.textContent = `${state.selected.length} selected · order of appearance`;
        ordered.replaceChildren();
        state.selected.forEach((id, index) => {
          const row = el('div', undefined, 'wf-order-row'), name = CR.S.proj.media[id]?.name || id;
          append(row, el('span', `${index + 1}. ${name}`));
          const up = button('↑', () => { [state.selected[index - 1], state.selected[index]] = [id, state.selected[index - 1]]; order(); }, row);
          up.setAttribute('aria-label', `Move ${name} earlier`); up.disabled = index === 0;
          const down = button('↓', () => { [state.selected[index + 1], state.selected[index]] = [id, state.selected[index + 1]]; order(); }, row);
          down.setAttribute('aria-label', `Move ${name} later`); down.disabled = index === state.selected.length - 1;
          ordered.appendChild(row);
        });
      }
      const list = el('div', undefined, 'wf-list'); content.appendChild(list);
      media.forEach(m => checkbox(`${m.name || m.id} · ${readableTime(m.duration || 0)}`, state.selected.includes(m.id), on => {
        state.selected = on ? [...state.selected, m.id] : state.selected.filter(id => id !== m.id); order();
      }, list));
      if (!media.length) note('Your media library is empty. Import video, audio, or images above.');
      const name = input('Sequence name', 'roughName', 'Rough cut');
      const format = select('Starting format', 'roughFormat', [['landscape', 'Landscape · 16:9'], ['portrait', 'Portrait · 9:16'], ['square', 'Square · 1:1']]);
      const fps = select('Frame rate', 'fps', [['30', '30 fps'], ['23.976', '23.976 fps'], ['24', '24 fps'], ['25', '25 fps'], ['29.97', '29.97 fps'], ['50', '50 fps'], ['59.94', '59.94 fps'], ['60', '60 fps']]);
      button('Build rough cut', () => run('assemble', { media_ids: [...state.selected], name: name.value, format: format.value, fps: Number(fps.value) }, 1), content, true);
      button('Use current timeline →', () => go(1)); order();
    }
    function story() {
      const seq = CR.S.seq, words = seq.transcript || [];
      note('Keep the passages that tell your story. A story cut creates a new timeline and preserves the original.');
      const model = select('Speech model', 'model', [['base', 'Base · balanced'], ['tiny', 'Tiny · fastest'], ['small', 'Small · more accurate'], ['medium', 'Medium · slower']]);
      button(words.length ? 'Transcribe again' : 'Transcribe this sequence', () => run('transcribe', { model: model.value }), content, !words.length);
      button('Speech tools / System Check', () => CR.panels.menuAction('diag'));
      note('Speech recognition runs in Tasks. Keep editing, then apply the completed transcript. The first run may download a model.');
      button('Open background tasks', () => CR.showTab('tasks'));
      if (!words.length) { note('No transcript yet. You can continue to Sound without one.'); return; }
      if (!state.keep) state.keep = new Set(words.map((_, i) => i));
      state.draft.transcript ||= {};
      const editor = el('div'); content.appendChild(editor);
      Transcript.create({ document, container: editor, words, keep: state.keep, draft: state.draft.transcript,
        onSave: edits => run('transcript_edit', { edits }), onDraft: persistCorrections, onMessage: feedback,
        onPreview: (start, end) => { CR.seekTo(start); CR.S.stopAt = end; CR.togglePlay(true); } });
      const draftsPanel = el('details', undefined, 'wf-correction-drafts'); content.appendChild(draftsPanel);
      append(draftsPanel, el('summary', 'Recover or download word corrections'));
      let recovered = [];
      try {
        if (draftStore) {
          const result = draftStore.list(state.basis, seq.id); recovered = result.drafts;
          if (result.unavailable.length) note(`${result.unavailable.length} unreadable draft(s) were preserved.`, draftsPanel);
        }
      } catch (error) { note('Could not read browser drafts: ' + error.message, draftsPanel); }
      const memory = correctionMemory.get(correctionKey(state.basis));
      if (memory && !recovered.some(d => d.key && d.key === memory.key)) recovered.unshift(memory);
      if (!recovered.length) note('Word corrections are saved here as small browser drafts while you type. Drafts stay in this browser and address.', draftsPanel);
      for (const saved of recovered.slice(0, 20)) {
        const row = el('div', undefined, 'wf-correction-draft'); draftsPanel.appendChild(row);
        const compatible = Transcript.compatible(saved, state.basis, seq.id, words);
        note(`${saved.edits.length} corrections · ${new Date(saved.saved_at).toLocaleString()} · ${compatible ? 'matches this edit' : 'earlier edit; download to review'}`, row);
        if (compatible) button('Restore corrections', () => {
          if (state.changed || !Transcript.compatible(saved, context(), CR.S.seq?.id, CR.S.seq?.transcript || [])) { feedback('This draft no longer matches the saved edit. Download it to review the corrections.', true); return; }
          if (Object.keys(state.draft.transcript.edits || {}).length) { feedback('Save or discard the current corrections first.', true); return; }
          state.draft.transcript.edits = Object.fromEntries(saved.edits.map(e => [e.index, e.text])); state.restoredCorrection = saved;
          renderStep(); feedback('Corrections restored for review. Save when you are ready.');
        }, row);
        button('Download draft', () => downloadCorrectionDraft(saved), row);
        button('Delete saved draft', () => {
          try {
            if (saved.key && draftStore) draftStore.remove(saved.key, saved.raw);
            const key = correctionKey(state.basis), current = correctionMemory.get(key);
            if (!Object.keys(state.draft.transcript.edits || {}).length && (current === saved || (saved.key && current?.key === saved.key))) correctionMemory.delete(key);
            renderStep();
          }
          catch (error) { feedback(error.message, true); }
        }, row);
      }
      button('Download current corrections', () => downloadCorrectionDraft({ version: 1, context: state.basis, sequence: seq.id,
        edits: Transcript.editsFor(words, state.draft.transcript.edits || {}) }));
      button('Discard pending corrections', () => {
        state.draft.transcript.edits = {}; persistCorrections([]); renderStep(); feedback('Pending corrections discarded.');
      });
      const name = input('Story cut name', 'storyName', seq.name + ' · Story');
      const padding = input('Breathing room around cuts (ms)', 'padding', 80, 'number', content, { min: 0, max: 500, step: 10 });
      button('Create cut from kept passages', () => run('story', { words: [...state.keep], name: name.value, padding: Number(padding.value) / 1000 }, 2), content, true);
      button('Open captions editor', () => CR.showTab('caps'));
    }
    function sound() {
      note('Choose dialogue clips. Start with gentle cleanup, then listen to the rendered preview before export.');
      const chosen = new Set(), list = el('div', undefined, 'wf-list'); content.appendChild(list);
      CR.S.seq.tracks.forEach(track => track.clips.forEach(clip => {
        const media = CR.S.proj.media[clip.media_id];
        if (!media?.has_audio || media.synthetic || track.locked || track.muted || clip.hold || clip.enabled === false || clip.audio?.mute ||
            (track.kind === 'video' && clip.audio?.linked === false) || (CR.S.seq.tracks.some(t => t.solo) && !track.solo)) return;
        const selected = state.draft.audioIds ? state.draft.audioIds.includes(clip.id) : CR.S.sel.has(clip.id);
        if (selected) chosen.add(clip.id);
        checkbox(`${track.id} · ${media.name || media.id} · ${readableTime(clip.start)}`, selected, on => {
          on ? chosen.add(clip.id) : chosen.delete(clip.id); state.draft.audioIds = [...chosen];
        }, list);
      }));
      if (!list.children.length) note('No unlocked audible media clips here. Open a version’s content sequence to adjust its mix.');
      const highpass = input('Remove rumble below (Hz)', 'highpass', 80, 'number', content, { min: 40, max: 180, step: 10 });
      const denoise = input('Noise reduction (dB, 0 = off)', 'denoise', 0, 'number', content, { min: 0, max: 18, step: 1 });
      const ratio = input('Compression ratio', 'ratio', 2, 'number', content, { min: 1, max: 6, step: .5 });
      note('These are adjustable filters, not voice isolation or a loudness target. Reapplying replaces this workflow’s filters and keeps other effects.');
      button('Apply dialogue cleanup', () => run('cleanup', { clip_ids: [...chosen], highpass: highpass.value, denoise: denoise.value, ratio: ratio.value }), content, true);
      button('Render preview with sound', preview);
      button('Open audio controls', () => CR.showTab('audio'));
    }
    function captions() {
      const seq = CR.S.seq;
      captionNotice(seq);
      note('Build readable captions from the current transcript, then correct the wording and timing in the captions editor.');
      if (!seq.transcript?.length) { button('Go to transcription', () => go(1), content, true); return; }
      const max = input('Maximum words per caption', 'maxWords', 7, 'number', content, { min: 2, max: 16, step: 1 });
      const style = select('Caption style', 'captionStyle', [['clean', 'Clean · white with outline'], ['boxed', 'Boxed · dark background'], ['highlight', 'Highlight · spoken word']]);
      if (seq.captions?.length) note(`Regeneration replaces ${seq.captions.length} existing captions. Undo restores them.`);
      button('Generate captions', () => run('captions', { max_words: max.value, style: style.value }), content, true);
      button('Edit captions and timing', () => CR.showTab('caps'));
      button('Render caption preview', preview);
      if (seq.captions?.length) {
        const list = el('div', undefined, 'wf-list'); content.appendChild(list);
        seq.captions.slice(0, 30).forEach(cap => button(`${readableTime(cap.start)} · ${cap.text}`, () => CR.seekTo(cap.start), list));
        if (seq.captions.length > 30) note('Showing the first 30 captions. The captions editor contains the full list.');
      }
    }
    function captionNotice(seq) {
      const ids = seq.workflow?.caption_review_ids || [];
      if (!ids.length) return;
      note(`${ids.length} captions need review after word corrections. Their independent edits were preserved.`);
      button('Review captions', () => CR.showTab('caps'));
      button('Mark captions reviewed', () => run('caption_review', { caption_ids: ids }));
    }
    function versions() {
      note('Make editable delivery versions. Each gets its own content sequence, so changes to the original do not alter these versions.');
      const chosen = new Set(state.draft.formats || ['portrait']);
      [['portrait', 'Portrait · 1080 × 1920'], ['square', 'Square · 1080 × 1080'], ['landscape', 'Landscape · 1920 × 1080']].forEach(([id, text]) => {
        checkbox(text, chosen.has(id), on => { on ? chosen.add(id) : chosen.delete(id); state.draft.formats = [...chosen]; });
      });
      const fit = select('Framing', 'fit', [['contain', 'Fit · keep the full picture'], ['cover', 'Fill · crop to the frame']]);
      note('Fill uses a center crop. Review faces, titles and important action; this does not track a subject automatically.');
      button('Create delivery versions', () => run('versions', { formats: [...chosen], fit: fit.value }, 5), content, true);
      if (CR.S.seq.workflow?.content_sequence) button('Open this version’s content', () => CR.switchSeq(CR.S.seq.workflow.content_sequence));
    }
    function preview() {
      if (CR.hasUnsavedEdits()) { feedback('Wait for edits to save before rendering the preview.', true); return; }
      Promise.resolve(CR.getRenderedPreview().start({ range: false })).catch(error => feedback(error.message || String(error), true));
    }
    function deliver() {
      const seq = CR.S.seq;
      captionNotice(seq);
      note(`${seq.width} × ${seq.height} · ${seq.fps} fps · ${readableTime(CR.seqDur())}`);
      note('Review the full rendered preview for sound, captions and framing. Export opens the existing media and encoder checks.');
      button('Render final preview', preview, content, true);
      button('Export this sequence…', () => CR.panels.menuAction('export'), content, true);
      button('Exports, progress and review files', () => CR.showTab('queue'));
      if (seq.captions?.length) {
        const link = el('a', 'Download captions (.srt)'); link.href = '/api/captions/export?sequence=' + encodeURIComponent(seq.id) + '&context=' + encodeURIComponent(JSON.stringify(CR.S.context)); link.download = seq.name + '.srt'; content.appendChild(link);
      }
      if (seq.workflow?.content_sequence) button('Edit this version’s content', () => CR.switchSeq(seq.workflow.content_sequence));
      const outputs = CR.S.proj.sequences.filter(s => s.workflow?.version);
      if (outputs.length) {
        note('Delivery versions'); const list = el('div', undefined, 'wf-list'); content.appendChild(list);
        outputs.forEach(s => { const b = button(s.name, () => CR.switchSeq(s.id), list); b.disabled = s.id === seq.id; });
      }
      note('An export is finished only when the Exports panel reports completion.');
    }
    function renderStep() {
      if (!CR.S.seq || state.busy) return;
      tabs.forEach((tab, i) => { tab.disabled = false; tab.setAttribute('aria-current', i === state.step ? 'step' : 'false'); });
      heading.textContent = CR.S.seq.name;
      notice.textContent = state.changed ? 'This project changed. Refresh the step to use the latest edit.' : readiness();
      fieldset.disabled = state.changed;
      content.replaceChildren(); const title = el('h3', `${state.step + 1}. ${STEPS[state.step]}`); title.tabIndex = -1; append(content, title);
      [footage, story, sound, captions, versions, deliver][state.step]();
      const footer = el('div', undefined, 'wf-footer'); content.appendChild(footer);
      if (state.step > 0) button('← ' + STEPS[state.step - 1], () => go(state.step - 1), footer);
      if (state.step < STEPS.length - 1) button(STEPS[state.step + 1] + ' →', () => go(state.step + 1), footer);
    }
    function go(step) {
      if (state.busy) return;
      state.step = step; renderStep(); content.querySelector?.('h3')?.focus?.();
    }
    function refresh() {
      if (state.busy || !CR.S.context || !CR.S.seq) return;
      const current = context();
      if (!samePlace(state.basis, current)) { reset(); feedback(''); renderStep(); }
      else if (lastRevision !== current.revision) {
        lastRevision = current.revision;
        if (state.stamp === editStamp(CR.S.proj)) { state.basis = current; if (Object.keys(state.draft.transcript?.edits || {}).length) persistCorrections(Transcript.editsFor(CR.S.seq.transcript, state.draft.transcript.edits)); return; }
        state.changed = true;
        notice.textContent = 'This project changed. Refresh the step to use the latest edit.'; fieldset.disabled = true;
      }
    }
    function mount() {
      pane.className += ' wf';
      pane.addEventListener('focusin', () => { CR.S.focus = 'panel'; });
      append(pane, el('p', 'GUIDED EDIT', 'wf-eyebrow'));
      heading = el('h2', 'Create your edit'); pane.appendChild(heading);
      const nav = el('nav', undefined, 'wf-steps'); nav.setAttribute('aria-label', 'Editing workflow'); pane.appendChild(nav);
      tabs = STEPS.map((step, i) => button(`${i + 1} ${step}`, () => go(i), nav));
      notice = el('p', '', 'wf-note'); pane.appendChild(notice);
      const refreshButton = button('Refresh step', async () => {
        if (state.busy) return;
        if (CR.hasUnsavedEdits()) { feedback('Wait for edits to save, or resolve them in Recovery, before refreshing.', true); return; }
        state.busy = true; fieldset.disabled = true; pane.setAttribute('aria-busy', 'true');
        try {
          if (!await CR.loadProject(true)) throw new Error('The editor could not refresh. Check saves and Recovery.');
          reset(); feedback('Choices reset to the latest saved edit.');
        } catch (error) { feedback(error.message || String(error), true); }
        finally { state.busy = false; pane.setAttribute('aria-busy', 'false'); renderStep(); }
      }, pane);
      refreshButton.title = 'Use the latest edit; clears the choices in this workflow';
      message = el('p', '', 'wf-message'); message.setAttribute('role', 'status'); message.setAttribute('aria-live', 'polite'); pane.appendChild(message);
      fieldset = el('fieldset', undefined, 'wf-body'); content = el('div'); append(fieldset, content); pane.appendChild(fieldset);
      refresh();
    }
    mount();
    return { refresh, go, state, run, hasDrafts: () => Object.keys(state.draft.transcript?.edits || {}).length > 0 || correctionMemory.size > 0 };
  }
  let controller;
  global.FilmocityWorkflow = { create, passages, go: step => controller?.go(step), hasDrafts: () => controller?.hasDrafts() || false, refresh: () => controller?.refresh(), open: () => { global.CR.showTab('workflow'); controller?.refresh(); } };
  if (typeof module !== 'undefined') module.exports = { create, passages, readableTime };
  if (global.document && global.CR) {
    const pane = global.document.getElementById('pane-workflow');
    if (pane) controller = create(global.CR, global.document, pane);
    global.document.getElementById('btnWorkflow')?.addEventListener('click', global.FilmocityWorkflow.open);
  }
})(typeof window === 'undefined' ? globalThis : window);

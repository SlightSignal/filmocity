/* Snapshot review never installs candidate data in the editable project. */
(function (global) {
  'use strict';
  const sameContext = (a, b) => !!a && !!b && ['workspace', 'project', 'revision'].every(k => a[k] === b[k]);
  function createReviewController({ document, prepare, decide, release, frame, current, pause,
    makeURL, revokeURL, now = Date.now, later = setTimeout, cancel = clearTimeout }) {
    const get = id => document.getElementById('proposalReview' + id), dialog = document.getElementById('dlgProposalReview');
    const image = get('Image'), placeholder = get('Placeholder'), message = get('Message'), sequence = get('Sequence'), time = get('Time');
    let generation = 0, view = null, items = [], previousFocus, focusReturned = true, accepting = false, stale = false;
    let desired = null, worker = null, debounce, expiry, imageURL = null;
    const tell = (text, error = false) => { message.textContent = text; message.className = error ? 'error' : ''; };
    function hideFrame(text) {
      image.hidden = true; image.removeAttribute('src'); placeholder.hidden = false; placeholder.textContent = text;
      if (imageURL) revokeURL(imageURL); imageURL = null;
    }
    function controls() {
      const disabled = !view || stale || accepting;
      for (const id of ['Sequence', 'Time', 'Back', 'Next', 'Retry', 'Accept']) get(id).disabled = disabled;
      get('Close').disabled = accepting;
      dialog.setAttribute('aria-busy', String(accepting || (!view && !stale)));
    }
    function retire(snapshot) { if (snapshot?.id) Promise.resolve(release(snapshot.id)).catch(() => {}); }
    function invalidate(text) {
      if (stale) return;
      stale = true; desired = null; cancel(debounce); cancel(expiry);
      worker?.abort.abort(); retire(view); hideFrame('Preview unavailable'); controls(); tell(text, true);
    }
    function valid() {
      if (!view || stale || !dialog.open) return false;
      const state = current();
      if (!sameContext(view.context, state.context) || state.unsaved || now() >= view.expires_at * 1000) {
        invalidate('This preview expired or the project changed. Close this review and preview the edits again.'); return false;
      }
      return true;
    }
    function watch() { if (accepting || valid()) expiry = later(watch, 1000); }
    function selected() { return view?.sequences.find(s => s.id === sequence.value); }
    async function renderDesired() {
      if (worker || !desired || !valid()) return;
      const target = desired, snapshot = view, task = { abort: new AbortController() }; worker = task; cancel(debounce);
      try {
        const url = `/api/proposals/preview/${encodeURIComponent(snapshot.id)}/frame?sequence=${encodeURIComponent(target.sequence)}&t=${target.t}`;
        const blob = await frame(url, task.abort.signal);
        if (desired !== target || !valid()) return;
        imageURL = makeURL(blob); image.src = imageURL; image.hidden = false; placeholder.hidden = true;
        image.alt = `Proposed ${selected().name}, frame ${target.index + 1}, ${target.t.toFixed(3)} seconds`;
        tell('Rendered from the prepared edit. Scrub to inspect another frame, or accept the reviewed items.');
      } catch (error) {
        if (desired !== target || !dialog.open || stale) return;
        if (error.status === 409) invalidate(error.message || 'The preview changed. Preview again.');
        else { hideFrame('Frame could not be rendered'); tell('Frame unavailable: ' + (error.message || error), true); }
      } finally {
        if (worker === task) worker = null;
        // Scrubbing coalesces to the latest requested frame, without flooding FFmpeg.
        if (desired && desired !== target) renderDesired();
      }
    }
    function requestFrame(immediate = false, frameIndex = +time.value || 0) {
      if (!valid()) return;
      const seq = selected(), fps = +seq.fps || 30, count = Math.max(1, Math.ceil(seq.duration * fps - 1e-7));
      time.max = String(count - 1); time.value = String(Math.max(0, Math.min(count - 1, frameIndex)));
      const index = +time.value, t = index / fps;
      get('Position').textContent = `${t.toFixed(3)} s · frame ${index + 1} / ${count}`;
      time.setAttribute('aria-valuetext', get('Position').textContent);
      desired = { sequence: seq.id, index, t }; hideFrame('Rendering selected frame…'); tell('Rendering selected frame…'); cancel(debounce);
      if (immediate) renderDesired(); else debounce = later(renderDesired, 180);
    }
    function cleanup() {
      generation++; desired = null; cancel(debounce); cancel(expiry); worker?.abort.abort();
      retire(view); view = null; hideFrame('Review closed');
    }
    function restoreFocus() {
      if (focusReturned) return;
      focusReturned = true;
      const target = previousFocus?.isConnected && !previousFocus.disabled ? previousFocus : document.querySelector('[data-pane="props"]');
      target?.focus?.();
    }
    function dismiss() {
      if (accepting) return;
      cleanup(); dialog.close(); restoreFocus();
    }
    image.onerror = () => { if (!image.hidden && valid()) { hideFrame('Frame could not be displayed'); tell('The rendered image could not be displayed. Try rendering the frame again.', true); } };
    get('Close').onclick = dismiss;
    dialog.addEventListener('cancel', event => { event.preventDefault(); dismiss(); });
    dialog.addEventListener('keydown', event => event.stopPropagation());
    dialog.addEventListener('close', () => { if (dialog.open) return; cleanup(); restoreFocus(); });
    sequence.onchange = () => { time.value = '0'; requestFrame(true); };
    time.oninput = () => requestFrame();
    get('Back').onclick = () => { time.value = String(+time.value - 1); requestFrame(true); };
    get('Next').onclick = () => { time.value = String(+time.value + 1); requestFrame(true); };
    get('Retry').onclick = () => requestFrame(true);
    get('Accept').onclick = async () => {
      if (accepting || !valid()) return;
      accepting = true; controls(); tell('Saving the reviewed edit…');
      try {
        const confirmed = await decide(view.proposal, items, view);
        if (!confirmed) throw new Error('The decision was not confirmed. Close review to inspect the save status and refresh proposals.');
        accepting = false; dismiss();
      } catch (error) { accepting = false; invalidate(error.message || String(error)); controls(); }
    };
    return { async open(proposal, selectedItems, title) {
      if (dialog.open) return;
      previousFocus = document.activeElement; focusReturned = false; stale = false; accepting = false;
      items = JSON.parse(JSON.stringify(selectedItems)); const request = ++generation;
      get('Title').textContent = title; get('Changes').textContent = ''; get('Warnings').textContent = '';
      get('Summary').textContent = 'Normalized project changes'; sequence.replaceChildren();
      get('Scope').textContent = `${items.length} item${items.length === 1 ? '' : 's'} selected. Your review notes and reasons are included in the decision.`;
      hideFrame('Preparing review…'); tell('Waiting for saved edits and preparing the proposal…'); controls();
      dialog.showModal(); get('Close').focus();
      try {
        pause();
        const result = await prepare(proposal, items);
        if (request !== generation || !dialog.open) { retire(result); return; }
        view = result;
        get('Warnings').textContent = result.warnings.length ? result.warnings.join('\n') : 'No overlap normalization was needed.';
        get('Summary').textContent = `${result.changes.length} normalized project change${result.changes.length === 1 ? '' : 's'} — inspect details`;
        get('Changes').textContent = JSON.stringify(result.changes, null, 2);
        for (const seq of result.sequences) { const option = document.createElement('option'); option.value = seq.id; option.textContent = seq.name; sequence.appendChild(option); }
        const active = current().sequence;
        sequence.value = result.sequences.some(s => s.id === active) ? active : result.sequences[0].id;
        const index = Math.floor((current().time || 0) * (+selected().fps || 30));
        controls(); watch(); requestFrame(true, index);
      } catch (error) {
        if (request === generation && dialog.open) invalidate('Could not prepare review: ' + (error.message || error));
      }
    } };
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { createReviewController, sameContext };
  else {
    let controller;
    global.FilmocityProposalReview = { open(cr, proposal, items, title) {
      controller ||= createReviewController({ document: global.document,
        prepare: cr.prepareProposalPreview, decide: (pid, chosen, view) => cr.proposalDecision(pid, chosen, 'accept', view),
        release: id => cr.api.json('DELETE', `/api/proposals/preview/${encodeURIComponent(id)}`),
        current: () => ({ context: cr.S.context, unsaved: cr.hasUnsavedEdits(), sequence: cr.S.seq?.id, time: cr.S.t }),
        pause: () => { if (cr.S.trimMode) cr.exitTrimMode(); cr.togglePlay(false); },
        makeURL: blob => URL.createObjectURL(blob), revokeURL: url => URL.revokeObjectURL(url),
        frame: async (url, signal) => {
          const response = await fetch(url, { signal, cache: 'no-store' });
          if (!response.ok) {
            const body = await response.json().catch(() => ({}));
            const error = new Error(body.detail?.message || body.detail || `Render failed (HTTP ${response.status})`);
            error.status = response.status; throw error;
          }
          return response.blob();
        },
      });
      return controller.open(proposal, items, title);
    } };
  }
})(typeof window === 'undefined' ? globalThis : window);

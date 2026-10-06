/* Standalone review page; note text is always rendered as text, never HTML. */
(function(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else api.start(root.document, root.fetch.bind(root));
})(globalThis, function() {
  function start(document, fetch) {
    const get = id => document.getElementById(id);
    const video = get('v'), button = get('send'), input = get('txt'), status = get('reviewStatus');
    const url = '/api/review/' + encodeURIComponent(get('review').dataset.ref) + '/notes';
    let pending = false;
    async function request(options) {
      const response = await fetch(url, options), result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Review request failed.');
      return result;
    }
    async function load() {
      const notes = await request(), list = get('list');
      list.replaceChildren();
      for (const note of notes) {
        const row = document.createElement('div'), seek = document.createElement('button');
        row.className = 'note'; seek.className = 't'; seek.type = 'button';
        const t = Number(note.review_time ?? note.time);
        seek.textContent = Number.isFinite(t) ? `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, '0')}` : 'Unknown time';
        seek.disabled = !Number.isFinite(t) || t < 0;
        seek.onclick = () => { if (!seek.disabled) video.currentTime = t; };
        const author = document.createElement('b'), text = document.createElement('span');
        author.textContent = note.author || 'client'; text.textContent = ' ' + (note.name ?? note.text ?? '');
        row.append(seek, author, text); list.append(row);
      }
    }
    button.onclick = async () => {
      if (pending || !input.value.trim()) return;
      const submitted = input.value;
      pending = true; button.disabled = true; status.textContent = 'Saving note…';
      try {
        const note = await request({ method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ time: video.currentTime, text: submitted.trim(), author: get('who').value || 'client' }) });
        if (input.value === submitted) input.value = '';
        status.textContent = note.warning || 'Note saved.';
        try { await load(); } catch (error) { status.textContent += ' Could not refresh notes: ' + error.message; }
      } catch (error) { status.textContent = 'Note could not be saved: ' + error.message; }
      finally { pending = false; button.disabled = false; }
    };
    const ready = load().catch(error => { status.textContent = 'Could not load notes: ' + error.message; });
    return { ready, load };
  }
  return { start };
});

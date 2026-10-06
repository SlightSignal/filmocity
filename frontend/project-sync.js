/* Ordered saves and explicit browser drafts. No network retries are implicit. */
(function (global) {
  const clone = value => JSON.parse(JSON.stringify(value));
  function validContext(context) {
    return context && ['workspace', 'project', 'revision'].every(key => typeof context[key] === 'string' && context[key]);
  }
  function sameProject(a, b) { return !!a && !!b && a.workspace === b.workspace && a.project === b.project; }

  function createSaveQueue(context, { send, changed = () => {}, committed = () => {} }) {
    if (!validContext(context)) throw new Error('Cannot save without a project version. Reload the project.');
    const state = { context: clone(context), pending: 0, revision: 0, error: '', saved: false };
    const queue = [];
    let running = false;
    async function drain() {
      if (running) return;
      running = true;
      try {
        while (queue.length && !state.error) {
          const item = queue[0];
          try {
            const result = await send(item.method, { ...item.body, _context: clone(state.context) });
            if (!result || result.ok !== true || !validContext(result.context) || !sameProject(result.context, state.context)) {
              throw new Error('The server did not confirm this project version');
            }
            state.context = clone(result.context); state.saved = true;
            queue.shift();
            // An observer failure is not a failed save and must not trigger a
            // replay of a committed insert. The caller reports refresh errors.
            try { await committed(result, item, queue.length === 0); }
            catch (error) { state.error = 'Saved, but the editor could not refresh: ' + (error.message || error); }
            state.pending--;
            item.resolve({ ok: true, result }); changed(state);
          } catch (error) {
            state.error = error.message || String(error); state.saved = false;
          }
        }
        if (state.error) {
          for (const item of queue.splice(0)) item.resolve({ ok: false, error: state.error });
          state.pending = 0; changed(state);
        }
      } finally { running = false; }
    }
    return Object.assign(state, {
      enqueue(method, body) {
        state.revision++;
        if (state.error) { changed(state); return Promise.resolve({ ok: false, error: state.error }); }
        const savedBody = clone(body);
        const promise = new Promise(resolve => queue.push({ method, body: savedBody, resolve, revision: state.revision }));
        state.pending++; changed(state); void drain();
        return promise;
      },
    });
  }

  function createDraftStore(getStorage, writer) {
    const prefix = 'filmocity:draft:v1:';
    let serial = 0;
    function save(context, project, previousKey = null) {
      if (!validContext(context)) throw new Error('The project identity is unavailable');
      const storage = getStorage(), base = prefix + [context.workspace, context.project, writer].map(encodeURIComponent).join(':') + ':';
      let key = previousKey;
      if (key && !key.startsWith(base)) throw new Error('The draft belongs to another editor');
      if (!key) { do { key = base + (++serial); } while (storage.getItem(key) !== null); }
      const raw = JSON.stringify({ version: 1, context, project, saved_at: Date.now() / 1000 });
      storage.setItem(key, raw);
      if (storage.getItem(key) !== raw) throw new Error('The browser could not verify the draft');
      return key;
    }
    function list(workspace) {
      const storage = getStorage(), drafts = [], unavailable = [];
      for (let index = 0; index < storage.length; index++) {
        const key = storage.key(index);
        if (!key?.startsWith(prefix + encodeURIComponent(workspace) + ':')) continue;
        try {
          const value = JSON.parse(storage.getItem(key));
          if (value.version !== 1 || !validContext(value.context) || value.context.workspace !== workspace || !Array.isArray(value.project?.sequences)) throw new Error('Invalid browser draft');
          drafts.push({ ...value, key });
        } catch (error) { unavailable.push({ key, reason: error.message || String(error) }); }
      }
      drafts.sort((a, b) => b.saved_at - a.saved_at);
      return { drafts, unavailable };
    }
    function remove(key) {
      if (typeof key !== 'string' || !key.startsWith(prefix)) throw new Error('Invalid draft key');
      getStorage().removeItem(key);
    }
    return { save, list, remove };
  }
  const api = { createSaveQueue, createDraftStore, validContext, sameProject };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else global.FilmocitySync = api;
})(typeof window === 'undefined' ? globalThis : window);

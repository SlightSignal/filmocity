/* One mouse edit owns its listeners until commit or explicit cancellation. */
(function (global) {
  function attachGesture({ target, document, valid, move, commit, cancel, failed, settled, control = false }) {
    let active = true;
    const subscriptions = [];
    function listen(node, type, handler) {
      node.addEventListener(type, handler, true);
      subscriptions.push(() => node.removeEventListener(type, handler, true));
    }
    function finish(mode, event, reason) {
      if (!active) return;
      active = false;
      for (const detach of subscriptions) detach();
      try { if (mode === 'commit') commit(event); else cancel(reason); }
      catch (error) { failed(error, mode); }
      finally { settled(mode); }
    }
    const abort = reason => finish('cancel', null, reason);
    function update(event) {
      if (!active) return;
      if (!valid()) return abort('The project changed during the drag.');
      try { move(event); }
      catch (error) { abort('Drag cancelled: ' + (error.message || error)); }
    }
    function accept(event) {
      if (!active) return;
      if (!valid()) return abort('The project changed during the drag.');
      finish('commit', event);
    }
    if (!control) listen(target, 'mousemove', event => {
      if (event.buttons !== undefined && !(event.buttons & 1)) return abort('The mouse was released outside the editor.');
      update(event);
    });
    listen(target, 'mouseup', event => {
      if (event.button !== undefined && event.button !== 0) return;
      accept(event);
    });
    listen(target, 'keydown', event => {
      if (!active || event.key !== 'Escape') return;
      event.preventDefault(); event.stopImmediatePropagation(); abort('Drag cancelled.');
    });
    listen(target, 'blur', () => abort('Drag cancelled when the editor lost focus.'));
    listen(target, 'pointercancel', () => abort('Drag cancelled by the input device.'));
    listen(document, 'visibilitychange', () => { if (document.hidden) abort('Drag cancelled when the editor was hidden.'); });
    return { cancel: abort, commit: accept, update, get active() { return active; } };
  }
  const api = {
    attachMouseGesture: options => attachGesture(options),
    attachControlGesture: options => attachGesture({ ...options, control: true }),
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else global.FilmocityGestures = api;
})(typeof window === 'undefined' ? globalThis : window);

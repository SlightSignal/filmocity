/* Native property inputs and label scrubs share one captured, reversible edit. */
(function (global) {
  const rangeKeys = new Set(['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown']);
  function target(CR, c, tr, element) {
    const project = CR.S.proj, sequence = CR.S.seq, context = { ...CR.S.context };
    return () => element.isConnected && CR.S.proj === project && CR.S.seq === sequence &&
      CR.S.context?.workspace === context.workspace && CR.S.context?.project === context.project && sequence.tracks.includes(tr) && tr.clips.includes(c);
  }
  function bind(CR, input, { c, tr, fields = [], preview = () => {}, commit }) {
    const { S } = CR, project = S.proj, sequence = S.seq;
    const document = input.ownerDocument;
    const pane = input.closest('.pane');
    const attributes = [...input.attributes].filter(a => a.name === 'id' || a.name.startsWith('data-')).map(a => [a.name, a.value]);
    const current = target(CR, c, tr, input);
    const value = () => input.type === 'checkbox' ? input.checked : input.value;
    let last = value(), controller = null, focusWanted = false;
    function focus() {
      if (!focusWanted || S.proj !== project || S.seq !== sequence) return;
      focusWanted = false;
      const root = pane && document.getElementById(pane.id);
      const replacement = root && [...root.querySelectorAll('input,select,textarea')].find(el => attributes.length && attributes.every(([k, v]) => el.getAttribute(k) === v));
      if (replacement) replacement.focus({ preventScroll: true });
    }
    function begin(ev, move, control = true) {
      if (controller?.active) return controller;
      if (!current() || !CR.canEdit()) { ev?.preventDefault(); return null; }
      const before = last, restore = CR.captureGestureFields(c, fields);
      const resetInput = () => { if (input.type === 'checkbox') input.checked = before; else input.value = before; };
      const rollback = () => { restore(); resetInput(); };
      controller = CR.watchEditGesture(ev || {}, move, () => {
        const next = value(); focusWanted = input.type === 'range' && document.activeElement === input;
        if ((input.type === 'number' || input.type === 'range') && (!Number.isFinite(Number(next)) || next === '' || !input.checkValidity())) {
          rollback(); CR.status('Enter a valid value within the control’s range.', 'err'); CR.renderAll(); return;
        }
        restore(); last = next;
        if (next !== before) commit(); else CR.renderAll();
      }, rollback, { control, valid: current, settled: focus });
      if (!controller) resetInput();
      return controller;
    }
    function start(ev) { return begin(ev, preview); }
    // Mouseup and change can arrive in either order. A finished value is saved once.
    input.addEventListener('change', ev => {
      if (controller && !controller.active && value() === last) return;
      const edit = controller?.active ? controller : start(ev);
      edit?.commit(ev);
    });
    if (input.type === 'range') {
      input.addEventListener('mousedown', ev => { if (ev.button === 0) start(ev); });
      input.addEventListener('keydown', ev => { if (rangeKeys.has(ev.key)) start(ev); });
      input.addEventListener('input', ev => { const edit = controller?.active ? controller : start(ev); edit?.update(ev); });
      input.addEventListener('keyup', ev => { if (rangeKeys.has(ev.key)) controller?.commit(ev); });
      input.addEventListener('blur', () => { if (controller?.active) controller.cancel('Property edit cancelled when the control lost focus.'); });
    }
    input.filmocityScrub = ev => {
      if (ev.button !== 0) return;
      ev.preventDefault();
      const x0 = ev.clientX, v0 = parseFloat(input.value) || 0;
      const step = parseFloat(input.step) || (input.type === 'range' ? (parseFloat(input.max) - parseFloat(input.min)) / 200 : 1);
      const decimals = step < 1 ? Math.min(4, Math.ceil(-Math.log10(step))) : 0;
      begin(ev, e => {
        let next = v0 + (e.clientX - x0) * step * (e.shiftKey ? 10 : 1);
        if (input.min !== '') next = Math.max(parseFloat(input.min), next);
        if (input.max !== '') next = Math.min(parseFloat(input.max), next);
        input.value = +next.toFixed(decimals); preview();
      }, false);
    };
    return { cancel: reason => controller?.cancel(reason) };
  }
  const api = { bind, target };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else global.FilmocityPropertyControls = api;
})(typeof window === 'undefined' ? globalThis : window);

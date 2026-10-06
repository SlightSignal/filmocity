/* Precise keyboard editing for existing curve data. Drafts never mutate clips. */
(function (global) {
  const clone = value => JSON.parse(JSON.stringify(value));
  const DEFAULT_CURVE = [[0, 0], [0.25, 0.25], [0.5, 0.5], [0.75, 0.75], [1, 1]];
  const EASING = [['linear', 'Linear'], ['ease', 'Ease in and out'], ['ease_in', 'Ease in'], ['ease_out', 'Ease out'], ['bezier', 'Bézier'], ['hold', 'Hold']];
  function makeModel({ kind, key, value, duration, playhead = 0 }) {
    if (!['keyframes', 'rgb', 'wheel'].includes(kind)) throw new Error('Unknown curve editor');
    let points = clone(kind === 'wheel' ? [value || {}] : value || (kind === 'rgb' ? DEFAULT_CURVE : []));
    if (kind !== 'wheel') points.sort((a, b) => (kind === 'rgb' ? a[0] - b[0] : a.t - b.t));
    // Trimming or retiming a clip can leave useful keys beyond its visible end.
    const initial = JSON.stringify(points), limit = kind === 'rgb' ? 1 : points.reduce((end, p) => Number.isFinite(p.t) ? Math.max(end, p.t) : end, Math.max(0, duration));
    const x = point => kind === 'rgb' ? point[0] : point.t;
    const valueOf = () => clone(kind === 'wheel' ? points[0] : points);
    const number = (name, label, value, min, max) => ({ name, label, value, min, max, type: 'number' });
    function fields(index) {
      const point = points[index]; if (!point) return [];
      if (kind === 'wheel') return ['r', 'g', 'b'].map((k, i) => number(k, ['Red', 'Green', 'Blue'][i], point[k] ?? 0, -1, 1));
      if (kind === 'rgb') return [number('0', 'Input level (0–1)', point[0], 0, 1), number('1', 'Output level (0–1)', point[1], 0, 1)];
      const easing = EASING.filter(([id]) => key !== 'speed' || ['linear', 'hold'].includes(id));
      if (point.e && !easing.some(([id]) => id === point.e)) easing.push([point.e, point.e + ' (existing)']);
      const result = [number('t', 'Time from clip start (seconds)', point.t, 0, limit), number('v', key === 'speed' ? 'Speed multiplier' : 'Value', point.v, key === 'speed' ? 0 : undefined),
        { name: 'e', label: 'Easing to next keyframe', value: point.e || 'linear', type: 'select', options: easing }];
      if (key !== 'speed') for (const [handle, label] of [['o', 'Outgoing'], ['i', 'Incoming']]) {
        result.push(number(handle + '.0', label + ' time fraction', (point[handle] || [0.33, 0])[0], 0, 1));
        result.push(number(handle + '.1', label + ' value offset', (point[handle] || [0.33, 0])[1]));
      }
      return result;
    }
    function set(index, name, raw) {
      const field = fields(index).find(f => f.name === name); if (!field) throw new Error('Choose an existing point');
      if (String(raw) === String(field.value)) return;
      let value;
      if (field.type === 'select') {
        if (!field.options.some(([id]) => id === raw)) throw new Error('Choose a listed easing'); value = raw;
      } else {
        value = Number(raw);
        if (String(raw).trim() === '' || !Number.isFinite(value)) throw new Error(field.label + ': enter a finite number.');
        if (key === 'speed' && name === 'v' && value <= 0) throw new Error('Speed multiplier: value is outside the allowed range; use a positive number.');
        if ((field.min !== undefined && value < field.min) || (field.max !== undefined && value > field.max)) throw new Error(field.label + ': value is outside the allowed range.');
      }
      const [name0, part] = name.split('.');
      if (part === undefined) points[index][name0] = value;
      else { points[index][name0] = [...(points[index][name0] || [0.33, 0])]; points[index][name0][+part] = value; }
    }
    function validate() {
      if (kind === 'rgb' && points.length < 2) throw new Error('A color curve needs at least two points.');
      for (let i = 0; i < points.length; i++) for (const field of fields(i)) {
        if (field.type !== 'number') continue;
        const value = Number(field.value);
        if (!Number.isFinite(value) || field.value === null || (key === 'speed' && field.name === 'v' && value <= 0) || (field.min !== undefined && value < field.min) || (field.max !== undefined && value > field.max)) throw new Error('Point ' + (i + 1) + ': check ' + field.label.toLowerCase() + '.');
      }
      if (kind !== 'wheel') {
        const sorted = [...points].sort((a, b) => x(a) - x(b));
        const spacing = kind === 'rgb' ? 0.001 : 0.000001;
        for (let i = 1; i < sorted.length; i++) if (x(sorted[i]) - x(sorted[i - 1]) < spacing - 1e-12) throw new Error(kind === 'rgb' ? 'Input levels must be separated by at least 0.001 for export.' : 'Keyframes must have distinct times.');
      }
      return true;
    }
    function add() {
      if (kind === 'wheel') return 0;
      const sorted = [...points].sort((a, b) => x(a) - x(b));
      let at = Math.max(0, Math.min(limit, playhead));
      if (kind === 'rgb' || points.some(p => Math.abs(x(p) - at) < 0.000001)) {
        const bounds = [0, ...sorted.map(x), limit]; let gap = -1;
        for (let i = 1; i < bounds.length; i++) if (bounds[i] - bounds[i - 1] > gap) { gap = bounds[i] - bounds[i - 1]; at = (bounds[i] + bounds[i - 1]) / 2; }
      }
      if (kind === 'rgb') at = +at.toFixed(3);
      if (points.some(p => Math.abs(x(p) - at) < (kind === 'rgb' ? 0.001 - 1e-12 : 0.000001))) throw new Error('There is no space for another point. Move or remove a point first.');
      const before = [...sorted].reverse().find(p => x(p) <= at), after = sorted.find(p => x(p) >= at);
      let value = kind === 'rgb' ? at : key === 'speed' || key === 'transform.scale' ? 1 : 0;
      if (before || after) {
        const a = before || after, b = after || before, read = p => kind === 'rgb' ? p[1] : p.v;
        value = read(a) + (read(b) - read(a)) * (x(a) === x(b) ? 0 : (at - x(a)) / (x(b) - x(a)));
      }
      points.push(kind === 'rgb' ? [at, value] : { t: +at.toFixed(6), v: value }); return points.length - 1;
    }
    return { kind, key, fields, set, validate, add, value: valueOf,
      get count() { return points.length; },
      get canRemove() { return kind === 'keyframes' ? points.length > 0 : kind === 'rgb' && points.length > 2; },
      changed: () => JSON.stringify(points) !== initial,
      sortedValue() { validate(); if (kind === 'wheel') return valueOf(); return clone([...points].sort((a, b) => x(a) - x(b))); },
      remove(index) { if (!this.canRemove) throw new Error('Keep at least two points in a color curve.'); if (!Number.isInteger(index) || index < 0 || index >= points.length) throw new Error('Choose an existing point.'); points.splice(index, 1); return Math.max(0, Math.min(index, points.length - 1)); },
      reset() { if (kind === 'wheel') points[0] = { ...points[0], r: 0, g: 0, b: 0 }; },
      label(index) { const point = points[index]; return kind === 'wheel' ? 'Channel balance' : kind === 'rgb' ? `${index + 1}: ${point[0]} → ${point[1]}` : `${index + 1}: ${point.t} s · ${point.v}`; },
    };
  }
  function makeSession(CR, { kind, key, c, tr }) {
    const { S } = CR, project = S.proj, sequence = S.seq, context = { ...S.context }, duration = CR.clipDur(c);
    const read = () => kind === 'wheel' ? c.color?.wheels?.[key] : kind === 'rgb' ? c.color?.curves : key === 'speed' ? c.time_remap : c.keyframes?.[key];
    const baseline = JSON.stringify(read()), model = makeModel({ kind, key, value: read(), duration, playhead: S.t - c.start });
    const sameTarget = () => S.proj === project && S.seq === sequence && S.context?.workspace === context.workspace && S.context?.project === context.project && sequence.tracks.includes(tr) && tr.clips.includes(c);
    const title = kind === 'wheel' ? key[0].toUpperCase() + key.slice(1) + ' color balance' : kind === 'rgb' ? 'Master color curve' : key.replace(/^transform\./, '').replace(/^audio\./, '').replace(/_/g, ' ') + ' keyframes';
    return { model, title, sameTarget,
      async apply() {
        if (!sameTarget() || JSON.stringify(read()) !== baseline || CR.clipDur(c) !== duration) throw new Error('The project or selected values changed. Close this editor and open it again before applying.');
        if (!CR.canEdit()) throw new Error('Finish the current edit before applying these values.');
        const value = model.sortedValue(); if (!model.changed()) return { saved: true, changed: false };
        const clip = { id: c.id };
        if (kind === 'wheel') clip.color = { ...(c.color || {}), wheels: { ...(c.color?.wheels || {}), [key]: value } };
        else if (kind === 'rgb') clip.color = { ...(c.color || {}), curves: value };
        else if (key === 'speed') clip.time_remap = value.length ? value : null;
        else { clip.keyframes = { ...(c.keyframes || {}), [key]: value }; if (!value.length) delete clip.keyframes[key]; }
        try {
          const saved = await CR.applyOps([{ op: 'set_clip', sequence: sequence.id, track: tr.id, clip }], 'curve_values', title);
          return { saved, changed: true };
        } catch (error) { return { saved: false, changed: true, error: error.message || String(error) }; }
      },
    };
  }
  function createController({ document, report }) {
    const get = id => document.getElementById(id), dialog = get('dlgCurveValues'), form = get('curveValuesForm');
    const list = get('curvePoint'), fields = get('curveFields'), message = get('curveMessage');
    const apply = get('curveApply'), close = get('curveCancel'), add = get('curveAdd'), remove = get('curveRemove'), reset = get('curveReset');
    let session, origin, returnFocus, selected = 0, inputs = [], busy = false, submitted = false, generation = 0;
    const tell = (text, error = false) => { if (message.textContent !== text) message.textContent = text; message.className = 'curve-message' + (error ? ' error' : ''); };
    function controls() {
      const locked = busy || submitted;
      fields.disabled = locked; list.disabled = locked || !session?.model.count;
      apply.disabled = locked; add.disabled = locked; reset.disabled = locked; remove.disabled = locked || !session?.model.canRemove;
      close.textContent = locked ? 'Close' : 'Cancel'; dialog.setAttribute('aria-busy', String(busy));
    }
    function fillList() {
      list.replaceChildren(); const model = session.model;
      for (let i = 0; i < model.count; i++) { const option = document.createElement('option'); option.value = String(i); option.textContent = model.label(i); list.appendChild(option); }
      list.value = String(selected);
    }
    function showFields() {
      fields.replaceChildren(); inputs = [];
      const legend = document.createElement('legend'); legend.textContent = session.model.kind === 'wheel' ? 'Channel balance (−1 to 1)' : 'Selected point'; fields.appendChild(legend);
      for (const field of session.model.fields(selected)) {
        const label = document.createElement('label'), title = document.createElement('span'), input = document.createElement(field.type === 'select' ? 'select' : 'input');
        title.textContent = field.label; label.appendChild(title); label.appendChild(input); fields.appendChild(label);
        input.id = 'curveValue-' + field.name; input.name = field.name;
        if (field.type === 'select') for (const [value, text] of field.options) { const option = document.createElement('option'); option.value = value; option.textContent = text; input.appendChild(option); }
        else { input.type = 'number'; input.step = 'any'; input.required = true; if (field.min !== undefined) input.min = field.min; if (field.max !== undefined) input.max = field.max; }
        input.value = String(field.value); inputs.push({ field, input });
        input.oninput = () => { if (busy || submitted) return; try { session.model.set(selected, field.name, input.value); input.removeAttribute('aria-invalid'); if (list.children[selected]) list.children[selected].textContent = session.model.label(selected); tell('Changes are local until you choose Apply.'); } catch (error) { input.setAttribute('aria-invalid', 'true'); input.setAttribute('aria-describedby', 'curveMessage'); tell(error.message, true); } };
      }
      controls();
    }
    function collect() {
      for (const { field, input } of inputs) {
        try { session.model.set(selected, field.name, input.value); }
        catch (error) { input.setAttribute('aria-invalid', 'true'); input.setAttribute('aria-describedby', 'curveMessage'); input.focus(); throw error; }
      }
    }
    function restoreFocus() { if (origin?.isConnected) origin.focus(); else returnFocus?.(); }
    function closed() { if (dialog.open) return; generation++; session = null; restoreFocus(); }
    close.onclick = () => dialog.close();
    dialog.addEventListener('close', closed);
    dialog.addEventListener('keydown', event => event.stopPropagation());
    list.onchange = () => {
      if (busy || submitted || !session) return;
      const next = Number(list.value);
      try { collect(); selected = next; showFields(); } catch (error) { list.value = String(selected); tell(error.message, true); }
    };
    add.onclick = () => {
      if (busy || submitted || !session) return;
      try { collect(); selected = session.model.add(); fillList(); showFields(); inputs[0]?.input.focus(); tell('Point added. Choose Apply to save it.'); } catch (error) { tell(error.message, true); }
    };
    remove.onclick = () => {
      if (busy || submitted || !session) return;
      try { selected = session.model.remove(selected); fillList(); showFields(); (inputs[0]?.input || add).focus(); tell('Point removed. Choose Apply to save.'); } catch (error) { tell(error.message, true); }
    };
    reset.onclick = () => { if (busy || submitted || !session) return; session.model.reset(); showFields(); inputs[0]?.input.focus(); tell('Channels reset. Choose Apply to save.'); };
    form.onsubmit = async event => {
      event.preventDefault(); if (busy || submitted || !session) return;
      try { collect(); session.model.validate(); } catch (error) { tell(error.message, true); return; }
      busy = true; controls(); tell('Applying changes… Closing this window will not cancel the save.'); const request = generation, editing = session;
      try {
        const result = await editing.apply(); submitted = result.changed;
        if (request !== generation || !dialog.open) { if (!result.saved) report('Curve save was not confirmed. Review your editor copy in Recovery.', 'err'); return; }
        if (result.saved) dialog.close();
        else tell('Your edit is in the editor, but saving was not confirmed. Close this window and review your copy in Recovery.', true);
      } catch (error) { if (request === generation && dialog.open) tell(error.message || String(error), true); }
      finally { busy = false; if (request === generation && dialog.open) controls(); }
    };
    return { open(next, from, fallback) {
      if (dialog.open || busy) return false;
      session = next; origin = from || document.activeElement; returnFocus = fallback; generation++; submitted = false; selected = 0;
      get('curveTitle').textContent = next.title;
      get('curvePointTools').hidden = next.model.kind === 'wheel'; reset.hidden = next.model.kind !== 'wheel';
      get('curveHandleHelp').hidden = next.model.kind !== 'keyframes' || next.model.key === 'speed';
      fillList(); showFields(); tell('Changes are local until you choose Apply. Cancel leaves the project unchanged.');
      dialog.showModal(); (inputs[0]?.input || add).focus(); return true;
    } };
  }
  const api = { makeModel, makeSession, createController };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else {
    let controller;
    global.FilmocityCurveValues = { ...api, open(CR, options) {
      if (!CR.canEdit() || !CR.propertyTarget(options.c, options.tr, options.origin)()) return false;
      if (CR.S.playing) CR.togglePlay(false);
      controller ||= createController({ document: global.document, report: CR.status });
      const id = options.origin.dataset.curveEdit;
      return controller.open(makeSession(CR, options), options.origin, () => {
        const replacement = [...global.document.querySelectorAll('[data-curve-edit]')].find(button => button.dataset.curveEdit === id);
        (replacement || global.document.getElementById('btnCommands'))?.focus();
      });
    } };
  }
})(typeof window === 'undefined' ? globalThis : window);

(function (root, factory) {
  const value = factory();
  if (typeof module === "object" && module.exports) module.exports = value;
  else root.FilmocityRenderedPreview = value;
})(typeof window === "undefined" ? globalThis : window, function () {
  "use strict";
  const sameContext = (a, b) => !!a && !!b && a.workspace === b.workspace && a.project === b.project && a.revision === b.revision;
  const sameView = (a, b) => sameContext(a?.context, b?.context) && a.sequence === b.sequence && a.revision === b.revision;
  const projectView = (a, b) => a?.context?.workspace === b?.context?.workspace && a?.context?.project === b?.context?.project && a?.sequence === b?.sequence;
  function ownedPreviewOutput(result) {
    if (typeof result?.id !== "string" || typeof result.out !== "string") return false;
    const match = /^\/renders\/job-([a-f0-9]{16})\/preview_[a-f0-9]{32}\.mp4$/.exec(result.out);
    // Require the entire canonical URL, including the folder owned by this job.
    // Restored legacy jobs deliberately have no preview binding to reactivate.
    return !!match && match[0] === result.out && match[1] === result.id;
  }
  function transport(fetch, read, { timeout = 60000, later = setTimeout, stopTimer = clearTimeout, Abort = AbortController } = {}) {
    async function json(method, path, body) {
      const controller = new Abort(), timer = later(() => controller.abort(), timeout);
      try {
        const options = { method, signal: controller.signal };
        if (body !== undefined) { options.headers = { "Content-Type": "application/json" }; options.body = JSON.stringify(body); }
        return await read(await fetch(path, options));
      } catch (error) {
        if (controller.signal.aborted) throw new Error("Preview request timed out; checking the same request or job.");
        throw error;
      } finally { stopTimer(timer); }
    }
    return { json, get: path => json("GET", path) };
  }
  function create(options) {
    const later = options.later || setTimeout, stopTimer = options.stopTimer || clearTimeout;
    let generation = 0, timer = null, active = null, ready = null, enabled = false, toggling = false, toggleTicket = 0;
    let state = { phase: "idle", message: "Live preview" };
    const emit = (phase, message) => { state = { phase, message, job: active?.id || null }; options.changed?.(state); };
    const safe = value => value && !value.pending && !value.error && !value.gesture && !value.switching;
    const valid = job => {
      if (active !== job || job.generation !== generation) return false;
      const view = options.capture();
      if (!safe(view) || !sameView(job.view, view)) { invalidate(); return false; }
      return true;
    };
    const clearVideo = () => { ready = null; enabled = false; options.clear?.(); };
    function invalidate(message = "Preview outdated — render again") {
      generation++; toggleTicket++; toggling = false; if (timer !== null) stopTimer(timer); timer = null;
      const job = active; active = null; clearVideo();
      if (job?.id) options.api.json("POST", `/api/render/${encodeURIComponent(job.id)}/cancel`, {}).catch(() => {});
      emit("idle", message);
    }
    function schedule(job, fn, delay = 1000) {
      if (active !== job) return;
      if (timer !== null) stopTimer(timer);
      timer = later(() => { timer = null; fn(job); }, delay);
    }
    function fail(job, error) {
      if (active !== job) return;
      active = null; clearVideo(); emit("error", "Preview unavailable: " + (error.message || error));
    }
    function adopt(result, view, turnOn = false, expectedJob = null) {
      const binding = result?.preview;
      if (!safe(options.capture()) || !sameView(view, options.capture()) || !binding ||
          !sameContext(view.context, binding.context) || binding.sequence !== view.sequence ||
          !Array.isArray(binding.range) || binding.range.length !== 2 || !binding.range.every(Number.isFinite) ||
          binding.range[0] < 0 || binding.range[1] <= binding.range[0] ||
          typeof binding.signature !== "string" || !ownedPreviewOutput(result) ||
          (expectedJob !== null && result.id !== expectedJob)) return false;
      if (ready?.id !== result.id) options.clear?.();
      ready = { ...result, view: { ...view, context: { ...view.context } } }; enabled = turnOn || enabled;
      options.ready?.(ready, enabled); emit("ready", enabled ? "Rendered preview on" : "Rendered preview available");
      return true;
    }
    async function poll(job) {
      if (!valid(job)) { if (active === job) invalidate(); return; }
      try {
        const result = await options.api.get(`/api/render/${encodeURIComponent(job.id)}`);
        if (!valid(job)) return;
        if (["queued", "running", "cancelling"].includes(result.status)) {
          if (job.cancelled && result.status !== "cancelling") { await requestCancel(job); return; }
          emit(result.status, result.status === "cancelling" ? "Cancelling preview…" : `Rendering preview… ${Math.round((result.progress || 0) * 100)}%`);
          schedule(job, poll); return;
        }
        if (result.status !== "done") {
          if (job.cancelled && result.error === "cancelled") { active = null; clearVideo(); emit("idle", "Preview cancelled"); }
          else fail(job, new Error(result.error || "Render did not finish."));
          return;
        }
        emit("checking", "Checking preview inputs…");
        const confirmed = await options.api.json("POST", `/api/render/preview/${encodeURIComponent(job.id)}/validate`, { _context: job.view.context });
        if (!valid(job)) return;
        if (job.cancelled) { active = null; clearVideo(); emit("idle", "Preview not activated; finished render remains in Jobs"); return; }
        if (!adopt(confirmed, job.view, !job.background, job.id)) { fail(job, new Error("The preview does not match this edit.")); return; }
        active = null; options.finished?.();
      } catch (error) {
        if (!valid(job)) return;
        if (error.status >= 400 && error.status < 500) { fail(job, error); return; }
        emit("disconnected", "Preview status unavailable — reconnecting…"); schedule(job, poll, 3000);
      }
    }
    async function submit(job) {
      if (!valid(job)) return;
      try {
        const result = await options.api.json("POST", "/api/render/preview", job.body);
        if (!result?.id) throw new Error("Invalid preview job response");
        job.id = result.id;
        if (!valid(job)) { options.api.json("POST", `/api/render/${encodeURIComponent(job.id)}/cancel`, {}).catch(() => {}); return; }
        if (job.cancelled) await requestCancel(job);
        else await poll(job);
      } catch (error) {
        if (!valid(job)) return;
        if (error.status >= 400 && error.status < 500) { fail(job, error); return; }
        // The server deduplicates this immutable request identity before queuing.
        emit("submitting", "Confirming preview request…"); schedule(job, submit, 3000);
      }
    }
    async function start({ range = false, background = false } = {}) {
      if (active) return false;
      const initial = options.capture();
      if (!initial?.context || initial.gesture || initial.switching || initial.error) {
        if (!background) emit("error", "Finish the current edit and resolve save errors before rendering a preview.");
        return false;
      }
      clearVideo(); const job = active = { generation: ++generation, background, id: null };
      emit("saving", "Waiting for edits to save…");
      try {
        await options.flush();
        if (active !== job) return false;
        const view = options.capture();
        if (!safe(view) || !projectView(initial, view)) throw new Error("The project changed or has unsaved edits.");
        job.view = { ...view, context: { ...view.context } };
        job.body = { sequence: view.sequence, range, actor: background ? "system" : "human",
          _context: { ...view.context }, request_id: options.requestId ? options.requestId() : (globalThis.crypto?.randomUUID?.() || `preview-${Date.now()}-${Math.random().toString(36).slice(2)}`) };
        emit("submitting", "Starting preview render…"); await submit(job); return true;
      } catch (error) { fail(job, error); return false; }
    }
    async function requestCancel(job) {
      if (!valid(job) || !job.id) return;
      emit("cancelling", "Cancelling preview…");
      try { await options.api.json("POST", `/api/render/${encodeURIComponent(job.id)}/cancel`, {}); }
      catch (error) { if (valid(job)) emit("cancelling", "Cancellation unconfirmed — checking job…"); }
      if (valid(job)) schedule(job, poll, 200);
    }
    function cancel() {
      const job = active; if (!job) return;
      job.cancelled = true; clearVideo();
      if (state.phase === "saving") { active = null; generation++; emit("idle", "Preview cancelled"); return; }
      emit("cancelling", "Cancelling preview…");
      if (job.id) requestCancel(job);
    }
    async function toggle() {
      const ticket = ++toggleTicket;
      if (toggling) { toggling = false; enabled = false; options.clear?.(); emit("ready", "Rendered preview off"); return false; }
      if (enabled) { enabled = false; options.clear?.(); emit("ready", "Rendered preview off"); return false; }
      if (!ready || !sameView(ready.view, options.capture()) || !safe(options.capture())) { emit("idle", "Render a preview for this edit first"); return false; }
      const candidate = ready, view = options.capture(), epoch = generation;
      toggling = true;
      emit("checking", "Checking preview inputs…");
      try {
        const result = await options.api.json("POST", `/api/render/preview/${encodeURIComponent(candidate.id)}/validate`, { _context: view.context });
        if (epoch !== generation || ready !== candidate || ticket !== toggleTicket) return false;
        if (adopt(result, view, true, candidate.id)) return true;
        invalidate("Preview outdated or unavailable — render again"); return false;
      } catch (error) { if (epoch === generation && ticket === toggleTicket) invalidate("Preview outdated or unavailable — render again"); return false; }
      finally { if (ticket === toggleTicket) toggling = false; }
    }
    function playable(time, rate = 1) {
      if (ready && (!safe(options.capture()) || !sameView(ready.view, options.capture()))) invalidate();
      const inside = enabled && ready && time >= ready.preview.range[0] && time < ready.preview.range[1];
      if (enabled && ready && !active) {
        const message = rate <= 0 ? "Live preview for reverse playback" : inside ? "Rendered preview on" : "Live preview outside rendered range";
        if (state.message !== message) emit("ready", message);
      }
      return inside && rate > 0 ? ready : null;
    }
    return { start, cancel, invalidate, toggle, playable, adopt,
      get state() { return state; }, get busy() { return !!active || toggling; }, get current() { return ready; }, get enabled() { return enabled; } };
  }
  return { create, transport, sameView, sameContext };
});

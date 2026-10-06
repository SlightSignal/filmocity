"""Filmocity agent SDK — a thin, dependency-free Python client for the agent side of the collaboration.

    from filmocity_client import Filmocity
    cr = Filmocity("http://localhost:8787", actor="agent")
    media = cr.import_paths(["/footage/a.mp4"])
    cr.place(media[0]["id"], track="V1", start=0, in_=1.0, out=4.0, reason="opening wide shot")
    pid = cr.propose("Tighten the open", [ {"ops": [...], "reason": "…"} ])
    decisions = cr.wait_for_decisions(pid, timeout=600)

Conventions the human side relies on: always send `reason` (it is shown on the timeline ghost and stored with the decision),
write the *why* into the clip's `note`, and call `score()` before proposing so you know how the proposal will land."""
import json, math, os, re, time, urllib.request, urllib.error, urllib.parse, uuid
from fractions import Fraction

class FilmocityError(Exception): pass

class Filmocity:
    def __init__(self, base="http://localhost:8787", actor="agent", client=None, token=None):
        self.base = base.rstrip("/"); self.actor = actor; self.client = client or f"agent-{uuid.uuid4().hex[:6]}"; self.last_event_ts = time.time(); self.token = token or os.environ.get("FILMOCITY_TOKEN")

    def _call(self, path, body=None, method=None):
        headers = {"Content-Type": "application/json"}
        if self.token: headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method or ("POST" if body is not None else "GET"))
        try: return json.loads(urllib.request.urlopen(req, timeout=600).read() or b"null")
        except urllib.error.HTTPError as e:
            try: detail = json.loads(e.read())
            except Exception: detail = {}
            raise FilmocityError(f"{method or 'POST'} {path} → {e.code}: {detail.get('errors') or detail.get('detail') or detail.get('error') or detail}")

    # ---- project & media ----
    def project(self): return self._call("/api/project")
    def project_state(self):
        """Read {project, context}; pass this context when submitting edits based on that read."""
        return self._call("/api/project/state")
    def sequence(self, seq_id=None):
        sequences = self.project().get("sequences", [])
        if seq_id is None: return next(iter(sequences), None)
        matches = [sequence for sequence in sequences if sequence.get("id") == seq_id]
        if len(matches) != 1: raise FilmocityError("choose one existing sequence")
        return matches[0]
    def media(self): return self.project()["media"]
    def _stabilization_review(self, review):
        error = "Inspect the stabilization source before analyzing; its owner or settings are invalid"
        if not isinstance(review, dict) or review.get("kind") != "stabilization" or review.get("ok") is not True:
            raise FilmocityError(error)
        owner, _ = self._editorial_owner(review.get("context") if isinstance(review.get("context"), dict) else {})
        identity = self._editorial_identity(review.get("requested_media_id"), "reviewed source")
        self._editorial_identity(review.get("media_id"), "physical source")
        settings, source = review.get("settings"), review.get("source")
        ids, analyzer = review.get("affected_media_ids"), review.get("analyzer")
        if (not isinstance(settings, dict) or type(settings.get("shakiness")) is not int
                or not 1 <= settings["shakiness"] <= 10 or type(settings.get("force")) is not bool
                or not isinstance(source, dict) or not isinstance(source.get("path"), str)
                or not isinstance(source.get("stamp"), list)
                or not re.fullmatch("[0-9a-f]{64}", str(source.get("sha256", "")))
                or not isinstance(analyzer, dict) or not re.fullmatch("[0-9a-f]{64}", str(analyzer.get("sha256", "")))
                or not isinstance(ids, list) or any(not isinstance(i, str) or not i for i in ids)
                or len(ids) != len(set(ids)) or identity not in ids or review["media_id"] not in ids
                or not re.fullmatch("[0-9a-f]{64}", str(review.get("fingerprint", "")))):
            raise FilmocityError(error)
        return owner, identity, dict(settings)

    def inspect_stabilization(self, media_id, *, shakiness=5, force=False, context=None):
        """Read-only source-byte inspection; does not encode or save a project."""
        self._editorial_identity(media_id, "source")
        if type(shakiness) is not int or not 1 <= shakiness <= 10 or type(force) is not bool:
            raise FilmocityError("Use shakiness 1–10 and a boolean force flag")
        owner, project = self._editorial_owner(context)
        if project is not None and media_id not in project.get("media", {}):
            raise FilmocityError("Choose a source in the captured project")
        settings = {"shakiness": shakiness, "force": force}
        review = self._call("/api/stabilize/inspect", {"media_id": media_id, **settings,
            "_context": owner, "actor": self.actor, "client": self.client})
        reviewed_owner, identity, reviewed_settings = self._stabilization_review(review)
        if reviewed_owner != owner or identity != media_id or reviewed_settings != settings:
            raise FilmocityError("Stabilization inspection does not match the captured owner and source")
        return review

    def analyze_stabilization(self, review):
        """Analyze exactly this inspection once; never re-read, re-review or retry.

        One Undo restores the prior metadata. Immutable published analyses stay
        available for Undo/Redo. Inspect saved state after an uncertain outcome.
        """
        owner, identity, settings = self._stabilization_review(review)
        result = self._call("/api/stabilize", {"media_id": identity, **settings,
            "_context": owner, "fingerprint": review["fingerprint"], "actor": self.actor, "client": self.client})
        uncertain = "Stabilization outcome is uncertain; inspect saved state/Recovery before another edit. The command was not replayed."
        if not isinstance(result, dict) or result.get("ok") is not True or result.get("kind") != "stabilization":
            raise FilmocityError(uncertain)
        context, analysis = result.get("context"), result.get("analysis")
        if (not isinstance(context, dict) or any(context.get(k) != owner[k] for k in ("workspace", "project"))
                or not re.fullmatch("[0-9a-f]{64}", str(context.get("revision", "")))
                or result.get("requested_media_id") != identity or result.get("media_id") != review["media_id"]
                or result.get("media") != review["affected_media_ids"]
                or type(result.get("cached")) is not bool or type(result.get("changed")) is not bool
                or not isinstance(result.get("trf"), str) or not isinstance(analysis, dict)
                or analysis.get("source") != review["source"]
                or analysis.get("settings") != {"shakiness": settings["shakiness"]}
                or analysis.get("analyzer") != review.get("analyzer")
                or analysis.get("owner") != {k: owner[k] for k in ("workspace", "project")}
                or not re.fullmatch("[0-9a-f]{64}", str(analysis.get("sha256", "")))):
            raise FilmocityError(uncertain)
        return result
    def import_paths(self, paths): return self._call("/api/media/import", {"paths": list(paths), "actor": self.actor})["added"]
    def browse(self, path=""): return self._call(f"/api/fs?path={urllib.parse.quote(path)}") if path else self._call("/api/fs")
    def brief(self): return self.project().get("brief") or {}
    def set_brief(self, **fields): return self.patch([{"op": "set", "path": "/brief", "value": {**self.brief(), **fields}}], tool="brief", reason="agent brief update")

    # ---- edits ----
    def patch(self, ops, tool="agent", reason=None, normalize=True, *, context=None):
        """Apply ops directly (they appear live, glow green, and are logged with your reason). Raises FilmocityError on invalid ops."""
        return self._call("/api/project", {"ops": ops, "actor": self.actor, "tool": tool, "reason": reason, "client": self.client, "normalize": normalize, **({"_context": context} if context is not None else {})}, method="PATCH")
    def place(self, media_id, track="V1", start=0.0, in_=0.0, out=None, seq_id=None, reason=None, note=None, **extra):
        seq = self.sequence(seq_id); m = self.media().get(media_id, {}); out = out if out is not None else m.get("duration", 5.0)
        clip = {"id": uuid.uuid4().hex[:8], "media_id": media_id, "start": float(start), "in_": float(in_), "out": float(out), "speed": 1, "transform": {"x": 0, "y": 0, "scale": 1, "rotation": 0, "opacity": 1}, "audio": {"gain_db": 0, "linked": True}, "keyframes": {}, "color": {}, **extra}
        if note: clip["note"] = note
        self.patch([{"op": "set_clip", "sequence": seq["id"], "track": track, "clip": clip}], tool="place", reason=reason); return clip
    def text(self, text, start, duration=3.0, track=None, size=None, seq_id=None, reason=None, **title):
        seq = self.sequence(seq_id); track = track or sorted([t for t in seq["tracks"] if t["kind"] == "video"], key=lambda t: -t["index"])[0]["id"]
        clip = {"id": uuid.uuid4().hex[:8], "media_id": None, "start": float(start), "in_": 0, "out": float(duration), "speed": 1, "title": {"text": text, "size": size or int(seq["height"] * 0.05), "color": "white", **title}, "transform": {"opacity": 1}, "keyframes": {}}
        self.patch([{"op": "set_clip", "sequence": seq["id"], "track": track, "clip": clip}], tool="title", reason=reason); return clip
    def captions(self, srt_text, seq_id=None): seq = self.sequence(seq_id); return self._call("/api/captions/import", {"srt": srt_text, "sequence": seq["id"]})

    # ---- proposals (the collaborative path) ----
    def propose(self, title, items, *, context=None):
        """items: [{ops:[…], reason:str, tool?:str}] → proposal id. Invalid ops are rejected with a FilmocityError naming the problem."""
        r = self._call("/api/proposals", {"actor": self.actor, "title": title, "items": items, **({"_context": context} if context is not None else {})}); return r["id"]
    def proposals(self): return self._call("/api/proposals")
    def wait_for_decisions(self, pid, timeout=3600, poll=2.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            pr = next((p for p in self.proposals() if p["id"] == pid), None)
            if pr and all(it["status"] != "pending" for it in pr["items"]): return [{"reason": it["reason"], "decision": it["status"], "reasons": it.get("reasons", []), "note": it.get("note", "")} for it in pr["items"]]
            time.sleep(poll)
        return None
    def score(self, ops, reason=None, seq_id=None):
        seq = self.sequence(seq_id); return self._call("/api/advisor/score", {"sequence": seq["id"], "ops": ops, "reason": reason})["scores"]
    def preferences(self): return self._call("/api/advisor/report")
    def retrain(self): return self._call("/api/advisor/train", {})

    # ---- reading the human ----
    def session_summary(self, since=None):
        r = self._call(f"/api/session/summary?since={since if since is not None else self.last_event_ts}"); self.last_event_ts = r["now"]; return r
    def replace_text(self, clip_id, text, track=None, seq_id=None, reason=None):
        """Change the wording of a text clip in place (the human sees it immediately; smart export re-encodes only that segment)."""
        seq = self.sequence(seq_id)
        for t in seq["tracks"]:
            for c in t["clips"]:
                if c["id"] == clip_id and c.get("title"): return self.patch([{"op": "set_clip", "sequence": seq["id"], "track": t["id"], "clip": {"id": clip_id, "title": {**c["title"], "text": text}}}], tool="type", reason=reason or f"text: {text[:40]}")
        raise FilmocityError(f"no text clip {clip_id}")
    def set_duration(self, clip_id, seconds, ripple=True, seq_id=None, reason=None):
        """Make a section shorter or longer by moving its out point; ripple shifts everything after it."""
        seq = self.sequence(seq_id)
        for t in seq["tracks"]:
            for c in t["clips"]:
                if c["id"] == clip_id:
                    sp = c.get("speed", 1) or 1; cur = (c["out"] - c["in_"]) / sp; nout = c["in_"] + seconds * sp; ops = [{"op": "set_clip", "sequence": seq["id"], "track": t["id"], "clip": {"id": clip_id, "out": nout}}]
                    if ripple:
                        for x in t["clips"]:
                            if x["id"] != clip_id and x["start"] >= c["start"] + cur - 1e-6: ops.append({"op": "set_clip", "sequence": seq["id"], "track": t["id"], "clip": {"id": x["id"], "start": x["start"] + (seconds - cur)}})
                    return self.patch(ops, tool="duration", reason=reason or f"duration {seconds:.2f}s")
        raise FilmocityError(f"no clip {clip_id}")
    def reel(self, shots, music=None, target=15, hook="Stop scrolling.", hook_sub="", cta="FOLLOW FOR PART 2 →", look=None, caption_style=None, sfx=True, seq_id=None,
             *, name="New Reel", canvas="portrait", rhythm="even", framing="auto", captions=None, caption_text="", preview=True, timeout=1800):
        """Review a new editable reel sequence; preview=False explicitly applies it.

        Canvas is portrait, landscape or current. Rhythm is even or measured
        onsets; onset analysis requires chosen music and never silently falls back.
        Existing sequences remain intact. Inspect duration, source handles,
        generated resources and warnings before applying the reviewed plan.
        """
        if captions is None: captions = caption_style is not None
        return self._recipe_convenience("reel", seq_id, preview, timeout, {"shots": shots, "music": music, "target": target,
            "hook": hook, "hook_sub": hook_sub, "cta": cta, "look": look, "captions": captions, "caption_text": caption_text,
            "caption_style": caption_style, "sfx": sfx, "name": name, "canvas": canvas, "rhythm": rhythm, "framing": framing})
    def note(self, time_s, text, duration=0, seq_id=None):
        """Leave a review note at a time (a yellow marker the human can resolve): 'this cut is late', 'caption overlaps the UI'…"""
        seq = self.sequence(seq_id); return self._call("/api/markers/note", {"sequence": seq["id"], "time": time_s, "text": text, "duration": duration, "author": self.actor})
    def describe(self, seq_id=None, *, context=None):
        """Read a captured sequence's canonical timing, sections and cut summary.

        Passing context requires its explicit sequence. A changed owner/revision
        refuses the read; this method never substitutes a newly active project.
        """
        sequence, owner, _ = self._editorial_sequence(seq_id, context)
        query = urllib.parse.urlencode({"sequence": sequence, **owner})
        result = self._call("/api/sequence/describe?" + query)
        if not isinstance(result, dict) or result.get("context") != owner or result.get("sequence") != sequence:
            raise FilmocityError("The sequence description does not match its captured saved context")
        return result
    def variants(self, hooks, seq_id=None, *, targets=None, name_prefix=None, preview=True, timeout=1800):
        """Review separate variants of explicitly chosen graphic/title text.

        targets contains {clip_id, layer} or {clip_id, title: True}. There is
        no automatic replacement of an unrelated text layer. Review is the
        default; preview=False applies all copies in one Undo transaction.
        """
        return self._recipe_convenience("variants", seq_id, preview, timeout,
            {"hooks": hooks, "targets": targets, **({"name_prefix": name_prefix} if name_prefix is not None else {})})

    def explainer(self, lower_third=None, chapters=True, end_card="", seq_id=None,
                  *, chapter_duration=3, end_duration=3, preview=True, timeout=1800):
        """Review new overlay tracks without replacing existing timeline clips."""
        return self._recipe_convenience("explainer", seq_id, preview, timeout,
            {"lower_third": lower_third, "chapters": chapters, "end_card": end_card,
             "chapter_duration": chapter_duration, "end_duration": end_duration})

    def talking_head(self, clip_id, broll=(), punch_every=3, silences=True, voice_preset=True, captions=True, seq_id=None,
                     *, threshold_db=-38, min_gap=.45, pad=.08, preview=True, timeout=1800):
        """Review silence edits, punch-ins, voice processing, captions and b-roll.

        Punch interval zero disables punch-ins. The review discloses overwritten
        processing and mapped transcript/captions; preview=False applies once.
        """
        return self._recipe_convenience("talking_head", seq_id, preview, timeout, {"clip_id": clip_id, "broll": broll,
            "punch_every": punch_every, "silences": silences, "voice_preset": voice_preset, "captions": captions,
            "threshold_db": threshold_db, "min_gap": min_gap, "pad": pad})
    @staticmethod
    def _sequence_recipe_settings(mode, settings):
        allowed = ({"lower_third", "chapters", "end_card", "chapter_duration", "end_duration"} if mode == "explainer"
                   else {"hooks", "targets", "name_prefix"})
        if set(settings)-allowed: raise FilmocityError("unknown or reserved recipe settings: " + ", ".join(sorted(set(settings)-allowed)))
        try: settings = json.loads(json.dumps(settings, allow_nan=False))
        except (TypeError, ValueError, RecursionError) as error: raise FilmocityError("recipe settings must be finite JSON values") from error
        def text(value, name, maximum, required=False):
            if not isinstance(value, str) or len(value)>maximum or required and not value.strip():
                raise FilmocityError(name + " must be supported text")
        def duration(value, name):
            Filmocity._audio_number(value, name, 0, 60)
            if value == 0: raise FilmocityError(name + " must be positive")
        if mode == "explainer":
            if type(settings.get("chapters", True)) is not bool: raise FilmocityError("chapters must be true or false")
            text(settings.get("end_card", ""), "end_card", 500)
            for key in ("chapter_duration", "end_duration"): duration(settings.get(key, 3), key)
            lower = settings.get("lower_third")
            if lower is not None:
                if not isinstance(lower, dict) or set(lower)-{"name", "role", "at", "duration"}: raise FilmocityError("lower_third must contain name, role, at and duration settings")
                text(lower.get("name", ""), "lower_third name", 200); text(lower.get("role", ""), "lower_third role", 200)
                Filmocity._audio_number(lower.get("at", 1), "lower_third position", 0)
                duration(lower.get("duration", 4.5), "lower_third duration")
        else:
            hooks = settings.get("hooks")
            if not isinstance(hooks, list) or not 1<=len(hooks)<=20: raise FilmocityError("choose 1 to 20 hook headlines")
            for hook in hooks: text(hook, "hook", 500, True)
            if "name_prefix" in settings: text(settings["name_prefix"], "name_prefix", 120, True)
            targets = settings.get("targets")
            if not isinstance(targets, list) or not 1<=len(targets)<=100: raise FilmocityError("choose 1 to 100 explicit text targets")
            seen = set()
            for target in targets:
                if not isinstance(target, dict) or not isinstance(target.get("clip_id"), str) or not target["clip_id"]: raise FilmocityError("choose an existing target clip")
                if set(target)=={"clip_id", "title"} and target["title"] is True: key=(target["clip_id"], "title")
                elif set(target)=={"clip_id", "layer"} and type(target["layer"]) is int and target["layer"]>=0: key=(target["clip_id"], target["layer"])
                else: raise FilmocityError("target must name one graphic layer or title")
                if key in seen: raise FilmocityError("choose each text target once")
                seen.add(key)
        return settings

    @staticmethod
    def _recipe_settings(mode, settings):
        if mode in ("explainer", "variants"): return Filmocity._sequence_recipe_settings(mode, settings)
        allowed = {"captions"} | ({"clip_id", "broll", "punch_every", "silences", "voice_preset", "threshold_db", "min_gap", "pad"} if mode == "talking_head" else
            {"shots", "music", "target", "name", "canvas", "rhythm", "framing", "hook", "hook_sub", "cta", "caption_text", "look", "caption_style", "sfx"})
        if set(settings) - allowed: raise FilmocityError("unknown or reserved recipe settings: " + ", ".join(sorted(set(settings)-allowed)))
        try: settings = json.loads(json.dumps(settings, allow_nan=False))
        except (TypeError, ValueError, RecursionError) as error: raise FilmocityError("recipe settings must be finite JSON values") from error
        for key in ("silences", "voice_preset", "captions", "sfx"):
            if key in settings and type(settings[key]) is not bool: raise FilmocityError(key + " must be true or false")
        def ids(key, minimum):
            values = settings.get(key, [])
            if not isinstance(values, list) or not minimum <= len(values) <= 100 or any(not isinstance(value, str) or not value for value in values):
                raise FilmocityError(key + " must contain " + str(minimum) + " to 100 source IDs in the desired order")
        if mode == "talking_head":
            if not isinstance(settings.get("clip_id"), str) or not settings["clip_id"]: raise FilmocityError("choose a talking-head clip")
            ids("broll", 0)
            for key, default, low, high in (("punch_every", 3, 0, 600), ("threshold_db", -38, -120, 0), ("min_gap", .45, 0, 60), ("pad", .08, 0, 10)):
                Filmocity._audio_number(settings.get(key, default), key, low, high)
        else:
            ids("shots", 1); Filmocity._audio_number(settings.get("target", 15), "Reel duration", 1, 600)
            for key, default, choices in (("canvas", "portrait", ("portrait", "landscape", "current")), ("rhythm", "even", ("even", "onsets")), ("framing", "auto", ("auto", "cover", "contain", "blur_fill"))):
                if settings.get(key, default) not in choices: raise FilmocityError("choose a supported " + key)
            for key, maximum in (("name", 120), ("hook", 500), ("cta", 500), ("hook_sub", 1000), ("caption_text", 1000)):
                value = settings.get(key, "New Reel" if key == "name" else "")
                if not isinstance(value, str) or len(value) > maximum or key == "name" and not value.strip(): raise FilmocityError(key + " must be a supported text value")
            for key in ("music", "look"):
                value = settings.get(key)
                if value is not None and (not isinstance(value, str) or not value): raise FilmocityError(key + " must be a nonempty source/path or None")
            if settings.get("rhythm") == "onsets" and not settings.get("music"): raise FilmocityError("measured-onset cuts require a chosen music source")
            if settings.get("caption_style") is not None and not isinstance(settings["caption_style"], dict): raise FilmocityError("caption_style must be a style object or None")
        return settings

    def start_recipe(self, mode, *, sequence=None, context=None, request_id=None, **settings):
        """Queue an owned recipe without editing. Reel creates a new sequence.

        Capture one saved owner; supplied contexts require an explicit sequence.
        Source-clock, resource and plan limits remain server-authoritative.
        """
        if mode not in ("talking_head", "reel", "explainer", "variants"): raise FilmocityError("choose talking_head, reel, explainer or variants")
        settings = self._recipe_settings(mode, settings)
        if request_id is None: request_id = uuid.uuid4().hex
        if not isinstance(request_id, str) or len(request_id) != 32 or any(c not in "0123456789abcdef" for c in request_id):
            raise FilmocityError("recipe request_id must contain 32 lowercase hexadecimal characters")
        if context is None:
            state = self.project_state(); project = state["project"]; sequences = project.get("sequences", [])
            matches = [s for s in sequences if s.get("id") == sequence] if sequence is not None else sequences[:1]
            if len(matches) != 1: raise FilmocityError("choose one existing sequence")
            seq = matches[0]; sequence = seq["id"]; context = state["context"]
            if mode == "talking_head":
                clips = [(tr, c) for tr in seq.get("tracks", []) for c in tr.get("clips", []) if c.get("id") == settings["clip_id"]]
                if len(clips) != 1 or clips[0][0].get("locked"): raise FilmocityError("choose one existing unlocked talking-head clip")
            if mode == "variants":
                for target in settings["targets"]:
                    clips = [(tr, c) for tr in seq.get("tracks", []) for c in tr.get("clips", []) if c.get("id")==target["clip_id"]]
                    if len(clips)!=1 or clips[0][0].get("locked"): raise FilmocityError("choose an existing unlocked text target")
                    clip = clips[0][1]
                    if target.get("title"):
                        if not isinstance(clip.get("title"), dict): raise FilmocityError("the chosen clip has no title")
                    else:
                        layers = (clip.get("graphic") or {}).get("layers", [])
                        if not isinstance(layers, list) or target["layer"]>=len(layers) or not isinstance(layers[target["layer"]], dict) or layers[target["layer"]].get("kind")!="text":
                            raise FilmocityError("choose an existing text layer")
            identities = settings.get("shots", []) if mode == "reel" else settings.get("broll", [])
            if settings.get("music"): identities = [*identities, settings["music"]]
            if any(identity not in project.get("media", {}) for identity in identities): raise FilmocityError("a chosen recipe source is missing")
        elif not isinstance(sequence, str) or not sequence:
            raise FilmocityError("pass the sequence captured with this context")
        path = "/api/sequences/variants" if mode == "variants" else "/api/recipes/" + mode
        return self._call(path, {**settings, "sequence": sequence, "_context": context,
                          "request_id": request_id, "actor": self.actor, "client": self.client})

    def recipe_result(self, task_id, *, context=None):
        """Read owned recipe changes, resource warnings and the exact Apply plan."""
        if context is None: context = self.project_state()["context"]
        return self._call("/api/tasks/" + urllib.parse.quote(task_id, safe="") + "/recipe", {"_context": context})

    def wait_recipe(self, task_id, timeout=1800, poll=.5, *, context=None):
        """Wait for review; a timeout never resubmits or applies the recipe."""
        return self._wait_reviewed_task(task_id, "recipe", self.recipe_result, timeout, poll, context)

    def apply_recipe(self, task_id, reviewed):
        """Apply the exact reviewed recipe once, using its saved context."""
        result = reviewed.get("result", {})
        if result.get("kind") != "recipe" or result.get("mode") not in ("talking_head", "reel", "explainer", "variants") or not reviewed.get("plan", {}).get("ops"):
            raise FilmocityError("this recipe review has no supported changes to apply")
        return self._apply_reviewed_task(task_id, reviewed, "recipe")

    def _recipe_convenience(self, mode, seq_id, preview, timeout, settings):
        if type(preview) is not bool: raise FilmocityError("preview must be true or false")
        self._audio_number(timeout, "Recipe timeout", 0)
        if timeout == 0: raise FilmocityError("Recipe timeout must be positive")
        queued = self.start_recipe(mode, sequence=seq_id, **settings)
        reviewed = self.wait_recipe(queued["task"]["id"], timeout, context=queued["context"])
        if preview: return reviewed
        if not reviewed.get("plan", {}).get("ops"): return {"ok": True, "changed": False, "task": reviewed["task"]}
        return self.apply_recipe(queued["task"]["id"], reviewed)


    @staticmethod
    def _cover_settings(time_s=0, headline="", sub="", sizes=None, template="Hook — Big Statement", framing="blur_fill"):
        Filmocity._audio_number(time_s, "Cover time", 0)
        for value, name, maximum in ((headline, "headline", 500), (sub, "sub", 1000), (template, "template", 120)):
            if not isinstance(value, str) or len(value)>maximum or name=="template" and not value.strip():
                raise FilmocityError(name + " must be supported cover text")
        if framing not in ("cover", "contain", "blur_fill"): raise FilmocityError("choose cover, contain or blur_fill framing")
        settings = {"time": time_s, "headline": headline, "sub": sub, "template": template, "framing": framing}
        if sizes is not None:
            try: sizes = json.loads(json.dumps(sizes, allow_nan=False))
            except (TypeError, ValueError, RecursionError) as error: raise FilmocityError("cover sizes must be finite width/height pairs") from error
            if not isinstance(sizes, list) or not 1<=len(sizes)<=4 or any(not isinstance(pair, list) or len(pair)!=2 or any(type(n) is not int or not 16<=n<=4096 for n in pair) for pair in sizes):
                raise FilmocityError("choose one to four cover sizes with integer dimensions from 16 to 4096")
            if len(set(map(tuple, sizes)))!=len(sizes) or sum(w*h for w,h in sizes)>32*1024*1024:
                raise FilmocityError("choose unique cover sizes totalling at most 32 megapixels")
            settings["sizes"] = sizes
        return settings

    def start_cover(self, *, sequence=None, time_s=0, headline="", sub="", sizes=None,
                    template="Hook — Big Statement", framing="blur_fill", context=None, request_id=None):
        """Queue a captured composed-frame render; no project edit or file download."""
        settings = self._cover_settings(time_s, headline, sub, sizes, template, framing)
        if request_id is None: request_id = uuid.uuid4().hex
        if not isinstance(request_id, str) or len(request_id)!=32 or any(c not in "0123456789abcdef" for c in request_id):
            raise FilmocityError("cover request_id must contain 32 lowercase hexadecimal characters")
        if context is None:
            state = self.project_state(); sequences = state["project"].get("sequences", [])
            matches = [seq for seq in sequences if seq.get("id")==sequence] if sequence is not None else sequences[:1]
            if len(matches)!=1: raise FilmocityError("choose one existing sequence")
            sequence = matches[0]["id"]; context = state["context"]
        elif not isinstance(sequence, str) or not sequence:
            raise FilmocityError("pass the sequence captured with this context")
        return self._call("/api/recipes/cover", {**settings, "sequence": sequence, "_context": context,
                          "request_id": request_id, "actor": self.actor, "client": self.client})

    def cover_result(self, task_id, *, context=None):
        """Review owned PNG descriptors and composition/layout warnings."""
        if context is None: context = self.project_state()["context"]
        return self._call("/api/tasks/" + urllib.parse.quote(task_id, safe="") + "/cover", {"_context": context})

    def wait_cover(self, task_id, timeout=1800, poll=.5, *, context=None):
        """Wait for read-only cover review; never repeat a render after timeout."""
        return self._wait_reviewed_task(task_id, "cover", self.cover_result, timeout, poll, context)

    def cover(self, headline="", time_s=0, sub="", sizes=None, seq_id=None, *,
              template="Hook — Big Statement", framing="blur_fill", timeout=1800):
        """Render and review covers. Empty headline/sub retains a frame-only image.

        The original composed frame is kept, then fitted into each target canvas.
        PNGs stay task-owned. Use download_cover explicitly for verified bytes;
        this method neither edits the project nor opens or overwrites local files.
        """
        self._audio_number(timeout, "Cover timeout", 0)
        if timeout == 0: raise FilmocityError("Cover timeout must be positive")
        queued = self.start_cover(sequence=seq_id, time_s=time_s, headline=headline, sub=sub, sizes=sizes, template=template, framing=framing)
        return self.wait_cover(queued["task"]["id"], timeout, context=queued["context"])

    def download_cover(self, task_id, index, reviewed):
        """Return one verified PNG using the exact reviewed owner/revision.

        The endpoint is constructed locally, never taken from an arbitrary
        returned URL. Refresh Review after a project revision changes.
        """
        import hashlib
        import struct
        if not isinstance(reviewed, dict) or type(index) is not int or index<0:
            raise FilmocityError("choose one reviewed cover image")
        task = reviewed.get("task", {}); result = reviewed.get("result", {}); context = reviewed.get("context", {})
        if task.get("id")!=task_id or task.get("kind")!="cover" or result.get("kind")!="cover" or result.get("version")!=1:
            raise FilmocityError("review this cover task before downloading")
        if any(not isinstance(context.get(key), str) or not context[key] for key in ("workspace", "project", "revision")):
            raise FilmocityError("the reviewed cover context is missing")
        original = result.get("context", {})
        if any(original.get(key)!=context[key] for key in ("workspace", "project")):
            raise FilmocityError("the reviewed cover owner does not match")
        values = result.get("covers", [])
        if not isinstance(values, list): raise FilmocityError("cover descriptors are unavailable")
        matches = [value for value in values if isinstance(value, dict) and value.get("index")==index]
        if len(matches)!=1: raise FilmocityError("choose one reviewed cover image")
        descriptor = matches[0]; size = descriptor.get("size"); digest = descriptor.get("sha256")
        if type(size) is not int or not 33<=size<=80*1024*1024 or not isinstance(digest, str) or len(digest)!=64 or any(c not in "0123456789abcdef" for c in digest):
            raise FilmocityError("the cover size or digest is invalid")
        if any(type(descriptor.get(key)) is not int or not 16<=descriptor[key]<=4096 for key in ("width", "height")):
            raise FilmocityError("the cover dimensions are invalid")
        query = urllib.parse.urlencode({**{key: context[key] for key in ("workspace", "project", "revision")}, "download": 1})
        path = "/api/tasks/" + urllib.parse.quote(task_id, safe="") + "/cover/" + str(index) + "?" + query
        headers = {"Accept": "image/png"}
        if self.token: headers["Authorization"] = "Bearer " + self.token
        request = urllib.request.Request(self.base + path, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=600) as response: data = response.read(size+1)
        except urllib.error.HTTPError as error:
            error.close()
            raise FilmocityError("cover download was refused; refresh the original task review") from error
        if len(data)!=size or hashlib.sha256(data).hexdigest()!=digest:
            raise FilmocityError("cover download differs from the reviewed file")
        if data[:8]!=b"\x89PNG\r\n\x1a\n" or data[8:16]!=b"\x00\x00\x00\x0dIHDR" or struct.unpack(">II", data[16:24])!=(descriptor["width"], descriptor["height"]):
            raise FilmocityError("cover download is not the reviewed PNG geometry")
        return data

    def duck_all(self, amount=-12, fade=0.35, seq_id=None, *, context=None,
                 music_tracks=None, dialogue_tracks=None, attack=None, release=None,
                 hold=0.1, mode="apply", preview=False, preview_plan=None):
        """Add/remove separate clip-span attenuation; manual gain stays intact.

        Review with preview=True, then pass its context, sequence and plan back
        explicitly. Without context, capture the project and owner in one read.
        Defaults choose video/tagged-dialogue tracks and other populated audio
        tracks. Use explicit track lists for voiceover or mixed-purpose tracks.
        """
        if context is None:
            state = self.project_state(); context = state["context"]
            sequences = state["project"]["sequences"]
            seq = next((s for s in sequences if s["id"] == seq_id), None) if seq_id is not None else next(iter(sequences), None)
            if seq is None: raise FilmocityError("Choose an existing sequence")
            seq_id = seq["id"]
        elif seq_id is None:
            raise FilmocityError("Pass the sequence captured with this context")
        body = {"sequence": seq_id, "_context": context, "amount": amount, "fade": fade,
                "hold": hold, "mode": mode, "preview": preview, "actor": self.actor}
        for key, value in (("music_tracks", music_tracks), ("dialogue_tracks", dialogue_tracks),
                           ("attack", attack), ("release", release), ("preview_plan", preview_plan)):
            if value is not None: body[key] = value
        return self._call("/api/audio/duck_all", body)
    def review_url(self, render_name): return f"{self.base}/review/{render_name}"
    def brand(self, **kit):
        """Brand kit: primary, secondary, text (hex colours), font (family). Premium templates and caption presets substitute these."""
        return self.patch([{"op": "set", "path": "/brand", "value": {**(self.project().get("brand") or {}), **kit}}], tool="brand", reason="brand kit")
    def templates(self): return self._call("/api/templates")
    def template(self, name, start, duration=4.0, track=None, seq_id=None, reason=None, **overrides):
        """Insert a bundled/premium graphics template by name with the brand kit applied; override layer text via text0=, text1=…"""
        import re as _re
        t = self.templates().get(name)
        if not t: raise FilmocityError(f"no template {name}")
        b = {"primary": "#E8631C", "secondary": "#7A2E9E", "text": "#FFFFFF", "font": "", **(self.project().get("brand") or {})}
        def walk(v):
            if isinstance(v, str): return _re.sub(r"\{\{(primary|secondary|text|font)\}\}", lambda m: b.get(m.group(1), ""), v)
            if isinstance(v, list): return [walk(x) for x in v]
            if isinstance(v, dict): return {k: walk(x) for k, x in v.items() if not (k == "font" and walk(x) == "")}
            return v
        layers = walk(json.loads(json.dumps(t["layers"]))); ti = 0
        for L in layers:
            if L.get("kind") == "text":
                if f"text{ti}" in overrides: L["text"] = overrides[f"text{ti}"]
                ti += 1
        return self.graphic(layers, start, duration, name=t.get("name", name), track=track, seq_id=seq_id, reason=reason or f"template {name}")
    def upload_font(self, path):
        """Register a TTF/OTF font for the project (available in pickers, monitor and export)."""
        import mimetypes, os as _os
        boundary = "----filmocity" + uuid.uuid4().hex; data = open(path, "rb").read(); name = _os.path.basename(path)
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(self.base + "/api/fonts/upload", data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST"); return json.loads(urllib.request.urlopen(req, timeout=120).read())
    def graphic(self, layers, start, duration=4.0, name="graphic", track=None, seq_id=None, reason=None):
        """A layered graphic. Each layer: {kind:'text'|'box'|'shape', ...style, anim_in:{type,duration,delay,ease}, anim_out:{...}, opacity, scale, rotation, glow, blur}.
        Animation types: fade, rise, drop, slide_left/right/up/down, pop, zoom, wipe_left/right/up/down, typewriter (text), rotate_in. Eases: ease_out, ease_in, ease_in_out, linear, back_out, bounce."""
        seq = self.sequence(seq_id); track = track or sorted([t for t in seq["tracks"] if t["kind"] == "video"], key=lambda t: -t["index"])[0]["id"]
        clip = {"id": uuid.uuid4().hex[:8], "media_id": None, "start": float(start), "in_": 0, "out": float(duration), "speed": 1, "graphic": {"name": name, "layers": layers}, "transform": {"opacity": 1}, "keyframes": {}}
        self.patch([{"op": "set_clip", "sequence": seq["id"], "track": track, "clip": clip}], tool="graphic", reason=reason); return clip
    def animate_layer(self, clip_id, layer_index, anim_in=None, anim_out=None, seq_id=None, reason=None, **layer_props):
        """Set a layer's in/out animation and properties (opacity, scale, rotation, glow, blur) on an existing graphic."""
        seq = self.sequence(seq_id)
        for t in seq["tracks"]:
            for c in t["clips"]:
                if c["id"] == clip_id and c.get("graphic"):
                    g = json.loads(json.dumps(c["graphic"])); L = g["layers"][layer_index]
                    if anim_in is not None: L["anim_in"] = anim_in
                    if anim_out is not None: L["anim_out"] = anim_out
                    L.update(layer_props); return self.patch([{"op": "set_clip", "sequence": seq["id"], "track": t["id"], "clip": {"id": clip_id, "graphic": g}}], tool="animate", reason=reason or f"animate layer {layer_index + 1}")
        raise FilmocityError(f"no graphic {clip_id}")
    @staticmethod
    def _editorial_identity(value, name):
        if not isinstance(value, str) or not value:
            raise FilmocityError("Choose an existing " + name)
        return value

    def _editorial_owner(self, context=None):
        state = self.project_state() if context is None else None
        if context is None and (not isinstance(state, dict) or not isinstance(state.get("project"), dict)):
            raise FilmocityError("The saved project capture is unavailable")
        owner = state.get("context") if state is not None else context
        if not isinstance(owner, dict) or any(not isinstance(owner.get(key), str) or not owner[key] for key in ("workspace", "project", "revision")):
            raise FilmocityError("Capture the saved workspace, project and revision before this command")
        return {key: owner[key] for key in ("workspace", "project", "revision")}, state.get("project") if state is not None else None

    def _editorial_sequence(self, seq_id, context):
        if seq_id is not None: self._editorial_identity(seq_id, "sequence")
        if context is not None and seq_id is None:
            raise FilmocityError("Pass the sequence captured with this context")
        owner, project = self._editorial_owner(context)
        if project is None: return seq_id, owner, None
        sequences = project.get("sequences", [])
        if seq_id is None:
            if not sequences: raise FilmocityError("Choose an existing sequence")
            seq_id = sequences[0].get("id")
        matches = [seq for seq in sequences if seq.get("id") == seq_id]
        if len(matches) != 1: raise FilmocityError("Choose one existing sequence")
        return self._editorial_identity(seq_id, "sequence"), owner, matches[0]

    @staticmethod
    def _sequence_name(name, maximum):
        if name is not None and (not isinstance(name, str) or not 1 <= len(name.strip()) <= maximum or any(ord(c) < 32 for c in name)):
            raise FilmocityError(f"Sequence name must contain 1–{maximum} characters without control characters")
        return name.strip() if name is not None else None

    def _create_sequence(self, mode, media_id, seq_id, name, still_duration, context):
        name = self._sequence_name(name, 256)
        if mode == "source": self._editorial_identity(media_id, "source")
        if seq_id is not None: self._editorial_identity(seq_id, "sequence")
        if context is not None and seq_id is None: raise FilmocityError("Pass the sequence captured with this context")
        if still_duration is not None and (type(still_duration) not in (int, float) or not math.isfinite(still_duration) or not 0 < still_duration <= 86400):
            raise FilmocityError("Still duration must be finite, positive and at most 24 hours")
        owner, project = self._editorial_owner(context)
        if project is not None:
            sequences = project.get("sequences", [])
            if seq_id is None and sequences: seq_id = sequences[0].get("id")
            if len([s for s in sequences if s.get("id") == seq_id]) != 1:
                raise FilmocityError("Choose one captured sequence for its format")
            if mode == "source" and media_id not in project.get("media", {}):
                raise FilmocityError("Choose an existing source in the captured project")
        self._editorial_identity(seq_id, "sequence")
        body = {"mode": mode, "sequence": seq_id, "_context": owner, "actor": self.actor, "client": self.client}
        if mode == "source": body["media_id"] = media_id
        if name is not None: body["name"] = name
        if still_duration is not None: body["still_duration"] = still_duration
        reply = self._call("/api/sequence/create", body)
        uncertain = "Sequence creation outcome is uncertain; inspect saved state/Recovery before further edits. The command was not replayed."
        if not isinstance(reply, dict): raise FilmocityError(uncertain)
        acknowledged = reply.get("context"); summary = reply.get("summary"); created = reply.get("sequence"); clip = reply.get("clip_id")
        if (reply.get("ok") is not True or reply.get("changed") is not True or reply.get("kind") != "sequence_creation"
                or reply.get("mode") != mode or reply.get("project") != owner["project"] or reply.get("source_sequence") != seq_id
                or reply.get("media_id") != media_id or not isinstance(created, str) or not created or created == seq_id
                or (mode == "source" and (not isinstance(clip, str) or not clip)) or (mode == "empty" and clip is not None)
                or not isinstance(acknowledged, dict) or any(acknowledged.get(k) != owner[k] for k in ("workspace", "project"))
                or not isinstance(acknowledged.get("revision"), str) or not acknowledged["revision"]
                or not isinstance(summary, dict) or any(summary.get(k) != reply.get(k) for k in ("kind", "mode", "sequence", "source_sequence", "media_id", "clip_id"))
                or not isinstance(reply.get("warnings"), list)
                or project is not None and created in {s.get("id") for s in project.get("sequences", [])}):
            raise FilmocityError(uncertain)
        return reply

    def create_sequence(self, name=None, seq_id=None, *, context=None):
        """Create one empty sequence from a captured sequence format with one Undo.

        No timeline is changed before acknowledgement. An uncertain reply must
        be reconciled through saved state; this method never retries.
        """
        return self._create_sequence("empty", None, seq_id, name, None, context)

    def sequence_from_source(self, media_id, name=None, seq_id=None, *, still_duration=None, context=None):
        """Create a source-format sequence and whole selected source window atomically.

        seq_id supplies the format for audio/stills. still_duration is optional
        for still images only. Unknown stream/duration metadata refuses the
        entire command; preview availability is reported separately.
        """
        return self._create_sequence("source", media_id, seq_id, name, still_duration, context)

    def _nest_review(self, review):
        error = "Review the captured selection before nesting; the report is invalid"
        if not isinstance(review, dict) or review.get("kind") != "sequence_nesting" or type(review.get("ok")) is not bool:
            raise FilmocityError(error)
        owner, _ = self._editorial_owner(review.get("context") if isinstance(review.get("context"), dict) else {})
        sequence = self._editorial_identity(review.get("sequence"), "reviewed sequence")
        ids = review.get("clip_ids"); settings = review.get("settings"); summary = review.get("summary")
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 1000 or any(not isinstance(i, str) or not i for i in ids)
                or len(ids) != len(set(ids)) or not isinstance(settings, dict) or not isinstance(settings.get("name"), str)
                or self._sequence_name(settings["name"], 120) != settings["name"] or not isinstance(summary, dict)
                or not isinstance(review.get("issues"), list) or not isinstance(review.get("fingerprint"), str)
                or not re.fullmatch("[0-9a-f]{64}", review["fingerprint"])):
            raise FilmocityError(error)
        if review["ok"]:
            wrappers = summary.get("wrapper_clip_ids")
            if (summary.get("kind") != "sequence_nesting" or summary.get("sequence") != sequence
                    or summary.get("name") != settings["name"] or summary.get("selected_count") != len(ids)
                    or not isinstance(summary.get("child_sequence"), str) or not summary["child_sequence"]
                    or summary["child_sequence"] == sequence or not isinstance(wrappers, list) or not wrappers
                    or any(not isinstance(i, str) or not i for i in wrappers) or len(set(wrappers)) != len(wrappers)
                    or set(wrappers).intersection(ids) or any(issue.get("severity") == "error" for issue in review["issues"] if isinstance(issue, dict))):
                raise FilmocityError(error)
        return owner, sequence, list(ids), {"name": settings["name"]}

    def review_nest(self, clip_ids, seq_id=None, *, name=None, context=None):
        """Inspect nesting, including locks, layer dependencies and shared audio buses.

        Read the complete processing, range and warnings before apply_nest().
        Full color output preserves transparency; Legacy retains opaque nesting.
        """
        if (not isinstance(clip_ids, (list, tuple)) or not 1 <= len(clip_ids) <= 1000
                or any(not isinstance(i, str) or not i for i in clip_ids) or len(set(clip_ids)) != len(clip_ids)):
            raise FilmocityError("Choose 1–1000 distinct clip IDs in order")
        name = self._sequence_name(name, 120)
        sequence, owner, captured = self._editorial_sequence(seq_id, context)
        if captured is not None:
            counts = [sum(c.get("id") == identity for t in captured.get("tracks", []) for c in t.get("clips", [])) for identity in clip_ids]
            if any(count != 1 for count in counts): raise FilmocityError("Choose clips in the captured sequence")
        body = {"sequence": sequence, "clip_ids": list(clip_ids), "_context": owner, "actor": self.actor, "client": self.client}
        if name is not None: body["name"] = name
        report = self._call("/api/sequence/nest/review", body)
        actual_owner, actual_sequence, actual_ids, settings = self._nest_review(report)
        if actual_owner != owner or actual_sequence != sequence or actual_ids != list(clip_ids) or name is not None and settings["name"] != name:
            raise FilmocityError("Nest review differs from the captured owner, selection or name")
        return report

    def apply_nest(self, review):
        """Apply exactly one reviewed Nest transaction; no reread, retry or replay."""
        owner, sequence, ids, settings = self._nest_review(review)
        if review["ok"] is not True: raise FilmocityError("Resolve the Nest review issues before applying")
        reply = self._call("/api/sequence/nest", {"sequence": sequence, "clip_ids": ids, **settings,
            "_context": owner, "fingerprint": review["fingerprint"], "actor": self.actor, "client": self.client})
        uncertain = "Nest outcome is uncertain; inspect saved state/Recovery before further edits. The command was not replayed."
        if not isinstance(reply, dict): raise FilmocityError(uncertain)
        acknowledged = reply.get("context")
        if (reply.get("ok") is not True or reply.get("changed") is not True or reply.get("kind") != "sequence_nesting"
                or reply.get("project") != owner["project"] or reply.get("sequence") != sequence or reply.get("source_clip_ids") != ids
                or reply.get("child_sequence") != review["summary"]["child_sequence"]
                or reply.get("wrapper_clip_ids") != review["summary"]["wrapper_clip_ids"] or reply.get("summary") != review["summary"]
                or not isinstance(acknowledged, dict) or any(acknowledged.get(k) != owner[k] for k in ("workspace", "project"))
                or not isinstance(acknowledged.get("revision"), str) or not acknowledged["revision"] or not isinstance(reply.get("warnings"), list)):
            raise FilmocityError(uncertain)
        return reply

    def _multicam_flatten_review(self, review):
        error = "Review the captured multicam selection first; the report is invalid"
        if not isinstance(review, dict) or review.get("kind") != "multicam_flatten" or type(review.get("ok")) is not bool:
            raise FilmocityError(error)
        owner, _ = self._editorial_owner(review.get("context") if isinstance(review.get("context"), dict) else {})
        sequence = self._editorial_identity(review.get("sequence"), "reviewed sequence")
        ids = review.get("clip_ids"); summary = review.get("summary")
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 100
                or any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids)
                or review.get("settings") != {} or not isinstance(summary, dict)
                or not isinstance(review.get("issues"), list) or any(not isinstance(issue, dict) for issue in review["issues"])
                or not isinstance(review.get("fingerprint"), str)
                or not re.fullmatch("[0-9a-f]{64}", review["fingerprint"])):
            raise FilmocityError(error)
        if review["ok"]:
            replacements = summary.get("replacement_clip_ids")
            if (summary.get("kind") != "multicam_flatten" or summary.get("sequence") != sequence
                    or summary.get("selected_count") != len(ids)
                    or not isinstance(replacements, list) or not replacements
                    or any(not isinstance(i, str) or not i for i in replacements)
                    or len(set(replacements)) != len(replacements)
                    or not isinstance(summary.get("warnings"), list) or any(not isinstance(w, str) for w in summary["warnings"])
                    or any(issue.get("severity") == "error" for issue in review["issues"] if isinstance(issue, dict))):
                raise FilmocityError(error)
        return owner, sequence, list(ids)

    def review_multicam_flatten(self, clip_ids, seq_id=None, *, context=None):
        """Review direct-source camera/audio replacements without changing the edit.

        Inspect issues, resolved ranges, processing and warnings. Sampling or
        processing that cannot be preserved refuses rather than approximating.
        """
        if (not isinstance(clip_ids, (list, tuple)) or not 1 <= len(clip_ids) <= 100
                or any(not isinstance(i, str) or not i for i in clip_ids) or len(set(clip_ids)) != len(clip_ids)):
            raise FilmocityError("Choose 1–100 distinct multicam clip IDs in order")
        sequence, owner, captured = self._editorial_sequence(seq_id, context)
        if captured is not None:
            counts = [sum(c.get("id") == identity for t in captured.get("tracks", []) for c in t.get("clips", [])) for identity in clip_ids]
            if any(count != 1 for count in counts): raise FilmocityError("Choose clips in the captured sequence")
        report = self._call("/api/multicam/flatten/review", {"sequence": sequence, "clip_ids": list(clip_ids),
            "_context": owner, "actor": self.actor, "client": self.client})
        actual_owner, actual_sequence, actual_ids = self._multicam_flatten_review(report)
        if actual_owner != owner or actual_sequence != sequence or actual_ids != list(clip_ids):
            raise FilmocityError("Flatten review differs from the captured owner or selection")
        return report

    def apply_multicam_flatten(self, review):
        """Apply the exact reviewed replacements with one Undo; never reread or retry."""
        owner, sequence, ids = self._multicam_flatten_review(review)
        if review["ok"] is not True: raise FilmocityError("Resolve the Flatten review issues before applying")
        reply = self._call("/api/multicam/flatten", {"sequence": sequence, "clip_ids": ids,
            "_context": owner, "fingerprint": review["fingerprint"], "actor": self.actor, "client": self.client})
        uncertain = "Multicam Flatten outcome is uncertain; inspect saved state/Recovery before further edits. The command was not replayed."
        if not isinstance(reply, dict): raise FilmocityError(uncertain)
        acknowledged = reply.get("context")
        if (reply.get("ok") is not True or reply.get("changed") is not True or reply.get("kind") != "multicam_flatten"
                or reply.get("project") != owner["project"] or reply.get("sequence") != sequence or reply.get("source_clip_ids") != ids
                or reply.get("replacement_clip_ids") != review["summary"]["replacement_clip_ids"]
                or reply.get("summary") != review["summary"] or reply.get("warnings") != review["summary"]["warnings"]
                or not isinstance(acknowledged, dict) or any(acknowledged.get(k) != owner[k] for k in ("workspace", "project"))
                or not isinstance(acknowledged.get("revision"), str) or not acknowledged["revision"]):
            raise FilmocityError(uncertain)
        return reply

    _ATTRIBUTE_GROUPS = ('motion', 'color', 'video_effects', 'audio_effects', 'mask', 'audio_gain', 'audio_controls', 'audio_fades', 'picture_transitions')
    _ATTRIBUTE_DEFAULTS = ('motion', 'color', 'video_effects', 'audio_effects', 'audio_gain', 'audio_fades')
    _ATTRIBUTE_MEDIA = ('id', 'path', 'subclip_of', 'sub_in', 'duration', 'frame_rate', 'fps', 'interpret_fps', 'ingest_token', 'source_relink_basis', 'stab_trf', 'sequence_frames', 'input_opts')

    @staticmethod
    def _attribute_json(value):
        try:
            encoded = json.dumps(value, allow_nan=False)
            if len(encoded.encode('utf-8')) > 2 * 1024 * 1024:
                raise ValueError('too large')
            return json.loads(encoded)
        except (TypeError, ValueError, RecursionError) as error:
            raise FilmocityError('Attribute data must be finite JSON of at most 2 MiB') from error

    def _attribute_donor(self, donor):
        donor = self._attribute_json(donor)
        if not isinstance(donor, dict) or type(donor.get('version')) is not int or donor['version'] != 1:
            raise FilmocityError('Capture a version 1 clipboard donor first')
        clip = donor.get('clip'); sequence = donor.get('sequence')
        if not isinstance(clip, dict) or not isinstance(sequence, dict):
            raise FilmocityError('Donor clip and sequence format are required')
        self._editorial_identity(clip.get('id'), 'donor clip')
        self._editorial_identity(sequence.get('id'), 'donor sequence')
        self._editorial_owner(donor.get('context') if isinstance(donor.get('context'), dict) else {})
        for key in ('width', 'height'):
            value = sequence.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or not 1 <= value <= 16384:
                raise FilmocityError('Donor canvas dimensions must be between 1 and 16384')
        rate = sequence.get('fps')
        try:
            if isinstance(rate, bool) or not isinstance(rate, (int, float, str)) or not 0 < Fraction(str(rate)) <= 1000:
                raise ValueError('invalid rate')
        except (ValueError, ZeroDivisionError, OverflowError) as error:
            raise FilmocityError('Donor frame rate must be finite and positive') from error
        if donor.get('media') is not None and not isinstance(donor['media'], dict):
            raise FilmocityError('Donor media must be a captured descriptor or null')
        return donor

    def capture_clip_attributes(self, clip_id, seq_id=None, *, state=None):
        """Freeze a donor and its format/owner; optional state avoids a second read.

        The snapshot stays usable after the donor is edited or deleted. Source-
        dependent effects still require the resource checks shown by Review.
        """
        self._editorial_identity(clip_id, 'donor clip')
        if seq_id is not None: self._editorial_identity(seq_id, 'donor sequence')
        if state is None: state = self.project_state()
        if not isinstance(state, dict) or not isinstance(state.get('project'), dict):
            raise FilmocityError('The saved project capture is unavailable')
        owner, _ = self._editorial_owner(state.get('context') if isinstance(state.get('context'), dict) else {})
        project = state['project']; sequences = project.get('sequences', [])
        if seq_id is None and sequences: seq_id = sequences[0].get('id')
        matches = [s for s in sequences if s.get('id') == seq_id]
        if len(matches) != 1: raise FilmocityError('Choose one captured donor sequence')
        sequence = matches[0]
        clips = [c for t in sequence.get('tracks', []) for c in t.get('clips', []) if c.get('id') == clip_id]
        if len(clips) != 1: raise FilmocityError('Choose one clip in the captured donor sequence')
        clip = clips[0]; media = (project.get('media') or {}).get(clip.get('media_id'))
        return self._attribute_donor({'version': 1, 'clip': clip,
            'sequence': {key: sequence.get(key) for key in ('id', 'fps', 'width', 'height')}, 'context': owner,
            'media': {key: media[key] for key in self._ATTRIBUTE_MEDIA if key in media} if isinstance(media, dict) else None})

    def _attribute_settings(self, groups, include_animation, timing):
        groups = list(self._ATTRIBUTE_DEFAULTS) if groups is None else groups
        if (not isinstance(groups, (list, tuple)) or not groups
                or any(not isinstance(g, str) or g not in self._ATTRIBUTE_GROUPS for g in groups)
                or len(set(groups)) != len(groups) or type(include_animation) is not bool or timing not in ('seconds', 'scale')):
            raise FilmocityError('Choose unique attribute groups, animation true/false, and seconds/scale timing')
        return {'groups': list(groups), 'include_animation': include_animation, 'timing': timing}

    @staticmethod
    def _attribute_ids(clip_ids):
        if (not isinstance(clip_ids, (list, tuple)) or not 1 <= len(clip_ids) <= 100
                or any(not isinstance(i, str) or not i for i in clip_ids) or len(set(clip_ids)) != len(clip_ids)):
            raise FilmocityError('Choose 1–100 distinct target clip IDs in order')
        return list(clip_ids)

    def _attribute_review(self, review):
        error = 'Review the captured attributes first; the report is invalid'
        if not isinstance(review, dict) or review.get('kind') != 'clip_attributes' or type(review.get('ok')) is not bool:
            raise FilmocityError(error)
        owner, _ = self._editorial_owner(review.get('context') if isinstance(review.get('context'), dict) else {})
        sequence = self._editorial_identity(review.get('sequence'), 'reviewed sequence')
        ids = self._attribute_ids(review.get('clip_ids')); donor = self._attribute_donor(review.get('donor'))
        settings = review.get('settings')
        if not isinstance(settings, dict) or set(settings) != {'groups', 'include_animation', 'timing'}:
            raise FilmocityError(error)
        settings = self._attribute_settings(settings['groups'], settings['include_animation'], settings['timing'])
        summary = review.get('summary'); issues = review.get('issues')
        if (not isinstance(summary, dict) or not isinstance(issues, list) or any(not isinstance(i, dict) for i in issues)
                or not isinstance(review.get('fingerprint'), str) or not re.fullmatch('[0-9a-f]{64}', review['fingerprint'])):
            raise FilmocityError(error)
        if review['ok']:
            changed = summary.get('changed_clip_ids'); rows = summary.get('targets'); warnings = summary.get('warnings')
            if (summary.get('kind') != 'clip_attributes' or summary.get('sequence') != sequence
                    or type(summary.get('selected_count')) is not int or summary['selected_count'] != len(ids)
                    or not isinstance(changed, list) or any(not isinstance(i, str) or i not in ids for i in changed)
                    or len(set(changed)) != len(changed) or changed != [i for i in ids if i in changed]
                    or ('changed_count' in summary and (type(summary['changed_count']) is not int or summary['changed_count'] != len(changed or [])))
                    or not isinstance(rows, list) or len(rows) != len(ids)
                    or any(not isinstance(row, dict) for row in rows)
                    or [row.get('clip_id') for row in rows] != ids
                    or any(not isinstance(row.get('track'), str) or not row['track']
                        or type(row.get('changed')) is not bool or row['changed'] != (row['clip_id'] in changed)
                        or not isinstance(row.get('curves'), list) or any(not isinstance(k, str) for k in row['curves']) for row in rows)
                    or not isinstance(warnings, list) or any(not isinstance(w, str) for w in warnings)
                    or issues):
                raise FilmocityError(error)
        return owner, sequence, ids, donor, settings

    def review_clip_attributes(self, donor, clip_ids, seq_id=None, *, groups=None, include_animation=True, timing='seconds', context=None):
        """Review selective settings and animation without mutating the edit.

        Seconds preserves donor-local timing. Scale maps it to each target's
        duration. Animation=False preserves existing target curves. Source
        timing, media, clip identity and linked-audio ownership are never copied.
        Read per-target fields, live dependencies and warnings before Apply.
        """
        donor = self._attribute_donor(donor); ids = self._attribute_ids(clip_ids)
        settings = self._attribute_settings(groups, include_animation, timing)
        sequence, owner, captured = self._editorial_sequence(seq_id, context)
        if captured is not None:
            counts = [sum(c.get('id') == identity for t in captured.get('tracks', []) for c in t.get('clips', [])) for identity in ids]
            if any(n != 1 for n in counts): raise FilmocityError('Choose clips in the captured target sequence')
        report = self._call('/api/clip/attributes/review', {'sequence': sequence, 'clip_ids': ids, 'donor': donor, **settings,
            '_context': owner, 'actor': self.actor, 'client': self.client})
        actual = self._attribute_review(report)
        if actual != (owner, sequence, ids, donor, settings):
            raise FilmocityError('Attribute review differs from the captured owner, donor, selection or settings')
        return report

    def apply_clip_attributes(self, review):
        """Apply one exact reviewed transfer; never reread, retry or replay."""
        owner, sequence, ids, donor, settings = self._attribute_review(review)
        if review['ok'] is not True: raise FilmocityError('Resolve the attribute review issues before applying')
        reply = self._call('/api/clip/attributes', {'sequence': sequence, 'clip_ids': ids, 'donor': donor, **settings,
            '_context': owner, 'fingerprint': review['fingerprint'], 'actor': self.actor, 'client': self.client})
        uncertain = 'Paste Attributes outcome is uncertain; inspect saved state/Recovery before further edits. The command was not replayed.'
        if not isinstance(reply, dict): raise FilmocityError(uncertain)
        acknowledged = reply.get('context'); changed = review['summary']['changed_clip_ids']
        if (reply.get('ok') is not True or type(reply.get('changed')) is not bool or reply['changed'] != bool(changed)
                or reply.get('kind') != 'clip_attributes' or reply.get('project') != owner['project']
                or reply.get('sequence') != sequence or reply.get('target_clip_ids') != ids
                or reply.get('source_clip_id') != donor['clip']['id'] or reply.get('changed_clip_ids') != changed
                or reply.get('summary') != review['summary'] or reply.get('warnings') != review['summary']['warnings']
                or not isinstance(acknowledged, dict) or any(acknowledged.get(k) != owner[k] for k in ('workspace', 'project'))
                or not isinstance(acknowledged.get('revision'), str) or not acknowledged['revision']
                or (not changed and acknowledged != owner) or (changed and acknowledged['revision'] == owner['revision'])):
            raise FilmocityError(uncertain)
        return reply

    def split_words(self, clip_id, layer_index=0, anim=None, stagger=0.12, seq_id=None, *, context=None):
        """Split a captured text layer with one guarded Undo transaction.

        Following-layer animation indices stay attached to their original
        layers. Omitted anim preserves the existing entrance, or uses a pop
        when absent; anim={} explicitly removes the entrance. Unsupported
        typography/effects refuse instead of losing edits.
        An uncertain response must be reconciled through saved state/Recovery;
        this method sends the command once and never retries it automatically.
        """
        self._editorial_identity(clip_id, "graphic clip")
        if type(layer_index) is not int or layer_index < 0:
            raise FilmocityError("Choose a nonnegative text layer index")
        if isinstance(stagger, bool) or not isinstance(stagger, (int, float)) or not math.isfinite(stagger) or stagger < 0:
            raise FilmocityError("Word stagger must be finite nonnegative seconds")
        if anim is not None:
            if not isinstance(anim, dict): raise FilmocityError("Word animation must be an object")
            try: json.dumps(anim, allow_nan=False)
            except (TypeError, ValueError) as error: raise FilmocityError("Word animation must contain finite JSON settings") from error
        sequence, owner, captured = self._editorial_sequence(seq_id, context)
        if captured is not None:
            matches = [(track, clip) for track in captured.get("tracks", []) for clip in track.get("clips", []) if clip.get("id") == clip_id]
            if len(matches) != 1 or matches[0][0].get("locked"):
                raise FilmocityError("Choose one unlocked graphic clip")
            layers = (matches[0][1].get("graphic") or {}).get("layers", [])
            if layer_index >= len(layers) or layers[layer_index].get("kind") != "text":
                raise FilmocityError("Choose an existing text layer")
        body = {"sequence": sequence, "clip_id": clip_id, "layer": layer_index, "stagger": stagger,
                "_context": owner, "actor": self.actor, "client": self.client}
        if anim is not None: body["anim"] = anim
        return self._call("/api/graphics/split_words", body)

    def input_transform(self, media_id, transform, *, context=None):
        """Set the physical source's camera-log transform in one Undo step.

        A subclip resolves to its physical parent; all uses of that source share
        the choice. Read affected_media_ids and warnings in the response. This
        does not qualify HDR, camera-specific LUT accuracy or display color.
        """
        self._editorial_identity(media_id, "source media item")
        if transform not in ("none", "slog3", "vlog", "clog3", "logc3"):
            raise FilmocityError("Choose a supported camera-log input transform")
        owner, project = self._editorial_owner(context)
        if project is not None and media_id not in project.get("media", {}):
            raise FilmocityError("Choose an existing source media item")
        return self._call("/api/media/input_transform", {"media_id": media_id, "transform": transform,
                          "_context": owner, "actor": self.actor, "client": self.client})

    def extract_audio(self, media_id, *, context=None):
        """Create an owned audio-only bin alias and prepare it independently.

        The alias references source media; it does not write a new WAV file.
        Preserve the returned context and inspect preparation warnings. Undo
        removes the saved alias; uncertain replies must not be replayed.
        """
        self._editorial_identity(media_id, "source media item")
        owner, project = self._editorial_owner(context)
        if project is not None:
            source = project.get("media", {}).get(media_id)
            if not isinstance(source, dict) or not source.get("has_audio"):
                raise FilmocityError("Choose an existing source with audio")
        return self._call("/api/media/extract_audio", {"media_id": media_id, "_context": owner,
                          "actor": self.actor, "client": self.client})
    def _create_source(self, mode, media_ids, fields, context):
        owner, project = self._editorial_owner(context)
        if project is not None:
            for identity in media_ids:
                source = project.get("media", {}).get(identity)
                if not isinstance(source, dict) or source.get("id") != identity:
                    raise FilmocityError("Choose an existing source in the captured project")
                if mode == "breakout" and not source.get("has_audio"):
                    raise FilmocityError("Choose a source with audio for channel breakout")
        body = {**fields, "_context": owner, "actor": self.actor, "client": self.client}
        body["media_ids" if mode == "duplicate" else "media_id"] = list(media_ids) if mode == "duplicate" else media_ids[0]
        reply = self._call("/api/media/" + mode, body)
        uncertain = "Source creation reply is uncertain. Inspect saved state/Recovery before retrying; the command was not replayed."
        if not isinstance(reply, dict) or reply.get("ok") is not True or reply.get("changed") is not True or reply.get("kind") != "source_creation" or reply.get("mode") != mode:
            raise FilmocityError(uncertain)
        acknowledged = reply.get("context")
        if (not isinstance(acknowledged, dict) or any(acknowledged.get(key) != owner[key] for key in ("workspace", "project"))
                or not isinstance(acknowledged.get("revision"), str) or not acknowledged["revision"]):
            raise FilmocityError(uncertain)
        ids, records, saved = reply.get("media_ids"), reply.get("media"), reply.get("project")
        if (not isinstance(ids, list) or not ids or any(not isinstance(identity, str) or not identity for identity in ids)
                or len(ids) != len(set(ids)) or not isinstance(records, list) or len(records) != len(ids)
                or saved != owner["project"]):
            raise FilmocityError(uncertain)
        if (mode == "subclip" and len(ids) != 1 or mode == "duplicate" and len(ids) != len(media_ids)
                or mode == "breakout" and not 1 <= len(ids) <= 32):
            raise FilmocityError(uncertain)
        for identity, record in zip(ids, records):
            if (identity in media_ids or project is not None and identity in project.get("media", {})
                    or not isinstance(record, dict) or record.get("id") != identity):
                raise FilmocityError(uncertain)
        summary = reply.get("summary")
        if (not isinstance(summary, dict) or summary.get("kind") != "source_creation" or summary.get("mode") != mode
                or summary.get("source_media_ids") != list(media_ids) or summary.get("created_media_ids") != ids):
            raise FilmocityError(uncertain)
        return reply

    def create_subclip(self, media_id, in_, out, *, name=None, context=None):
        """Create a bin source window with one saved-owner Undo transaction.

        In/Out are exact logical seconds relative to the selected bin item,
        including when that item is already a subclip. Do not add its parent
        offset. Ordinary subclips share the physical parent's preview; timeline
        clips, retiming and Source marks are not edited by this command.
        """
        self._editorial_identity(media_id, "source media item")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in (in_, out)) or in_ < 0 or out <= in_:
            raise FilmocityError("Subclip In/Out must be finite seconds with 0 <= In < Out")
        if name is not None and (not isinstance(name, str) or not 1 <= len(name.strip()) <= 256 or any(ord(c) < 32 for c in name)):
            raise FilmocityError("Choose a subclip name of 1 to 256 characters")
        fields = {"in": in_, "out": out}
        if name is not None: fields["name"] = name
        return self._create_source("subclip", [media_id], fields, context)

    def breakout_audio(self, media_id, *, context=None):
        """Create independently prepared mono aliases of first-stream channels.

        The original file remains unchanged. One to 32 measured source channels
        become separate bin items, with one Undo for the complete command.
        Selected-channel audition needs each alias's verified prepared preview;
        preparation failure is separate from an already saved bin creation.
        """
        self._editorial_identity(media_id, "source media item")
        return self._create_source("breakout", [media_id], {}, context)

    def duplicate_media(self, media_ids, *, context=None):
        """Duplicate 1–50 ordered bin items in one owned Undo transaction.

        Copies receive new identities without borrowing another preparation
        job's state. Read the preparation warnings in the complete response.
        All source-creation commands capture one saved owner unless an explicit
        context is supplied; unknown replies are never retried automatically.
        """
        if not isinstance(media_ids, (list, tuple)) or not 1 <= len(media_ids) <= 50:
            raise FilmocityError("Choose 1 to 50 source media items in order")
        for identity in media_ids: self._editorial_identity(identity, "source media item")
        if len(set(media_ids)) != len(media_ids): raise FilmocityError("Choose each source media item once")
        return self._create_source("duplicate", list(media_ids), {}, context)

    def render_preview(self, seq_id=None, *, context=None, in_out=False, request_id=None):
        """Render saved content; reuse request_id when retrying an uncertain request."""
        seq = self.sequence(seq_id); body = {"sequence": seq["id"], "actor": self.actor, "range": bool(in_out)}
        if context is not None: body["_context"] = context
        if request_id is not None: body["request_id"] = request_id
        return self._call("/api/render/preview", body)
    def segments(self, seq_id=None): seq = self.sequence(seq_id); return self._call(f"/api/render/segments?sequence={seq['id']}")
    def focus(self, t):
        """Show the human where you are looking: a green agent playhead at time t (seconds) in the timeline."""
        return self._call("/api/events", {"type": "focus", "actor": self.actor, "client": self.client, "t": float(t)})
    def snapshot(self, label="agent_proposal", seq_id=None, *, context=None):
        seq = self.sequence(seq_id)
        body = {"label": label, "actor": self.actor, "sequence": seq["id"]}
        if context is not None: body["_context"] = context
        return self._call("/api/snapshot", body)
    def annotate(self, clip_id, label, reasons=(), note="", track="V1", seq_id=None): seq = self.sequence(seq_id); return self._call("/api/annotate", {"target": {"sequence": seq["id"], "track": track, "clip_id": clip_id}, "label": label, "reasons": list(reasons), "note": note, "actor": self.actor})

    # ---- analysis & delivery ----
    def start_analysis(self, kind, media_id, *, context=None, sequence=None, clip_id=None,
                       in_=0, out=None, request_id=None, **settings):
        """Queue cancellable source analysis; no timeline edits are applied."""
        routes = {"scenes": "/api/media/scenes", "silences": "/api/audio/silences", "remix": "/api/audio/remix"}
        if kind not in routes: raise FilmocityError("analysis kind must be scenes, silences or remix")
        if bool(sequence) != bool(clip_id): raise FilmocityError("timeline analysis needs both sequence and clip_id")
        if context is None: context = self.project_state()["context"]
        body = {**settings, "media_id": media_id, "in": in_, "out": out, "_context": context,
                "request_id": request_id or uuid.uuid4().hex}
        if clip_id: body.update(sequence=sequence, clip_id=clip_id)
        return self._call(routes[kind], body)

    def analysis_result(self, task_id, *, context=None):
        """Review against current saved context; retain its fingerprint for apply."""
        if context is None: context = self.project_state()["context"]
        return self._call("/api/tasks/" + urllib.parse.quote(task_id, safe="") + "/analysis", {"_context": context})

    def _wait_reviewed_task(self, task_id, kind, review, timeout, poll, context):
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0 for v in (timeout, poll)):
            raise FilmocityError("task timeout and poll interval must be positive finite numbers")
        end = time.monotonic() + timeout
        while True:
            catalog = self._call("/api/tasks"); current = catalog.get("context", {})
            if context is not None and any(context.get(k) != current.get(k) for k in ("workspace", "project")):
                raise FilmocityError("the active project changed; open the task's original project")
            task = next((t for t in catalog.get("tasks", []) if t.get("id") == task_id), None)
            if not task or task.get("kind") != kind: raise FilmocityError("the requested task is unavailable in the active project")
            if task["status"] == "ready": return review(task_id, context=current)
            if task["status"] in ("applied", "done"):
                raise FilmocityError("task is already complete; inspect its receipt or download the result instead of applying it again")
            if task["status"] in ("error", "cancelled", "interrupted"):
                raise FilmocityError(task.get("message") or "task " + task["status"])
            remaining = end - time.monotonic()
            if remaining <= 0: raise FilmocityError("task has not finished; inspect or cancel it in Tasks before submitting again")
            time.sleep(min(poll, remaining))

    def wait_analysis(self, task_id, timeout=900, poll=.5, *, context=None):
        """Wait without replaying submission or applying; timeout leaves the task inspectable."""
        return self._wait_reviewed_task(task_id, "analysis", self.analysis_result, timeout, poll, context)

    def _apply_reviewed_task(self, task_id, reviewed, kind):
        if reviewed.get("task", {}).get("id") != task_id or reviewed.get("task", {}).get("kind") != kind or not reviewed.get("plan", {}).get("fingerprint"):
            raise FilmocityError("review this task's timeline changes before applying")
        if not reviewed.get("context"): raise FilmocityError("the reviewed saved context is missing")
        return self._call("/api/tasks/" + urllib.parse.quote(task_id, safe="") + "/apply",
                          {"_context": reviewed["context"], "fingerprint": reviewed["plan"]["fingerprint"],
                           "actor": self.actor, "client": self.client})

    def apply_analysis(self, task_id, reviewed):
        """Apply exactly the reviewed server plan with one saved-context transaction."""
        return self._apply_reviewed_task(task_id, reviewed, "analysis")

    @staticmethod
    def _audio_ids(clip_ids, maximum=50):
        if not isinstance(clip_ids, (list, tuple)) or not 1 <= len(clip_ids) <= maximum or any(not isinstance(i, str) or not i for i in clip_ids) or len(set(clip_ids)) != len(clip_ids):
            raise FilmocityError(f"choose one to {maximum} distinct audio clip IDs")
        return list(clip_ids)

    @staticmethod
    def _audio_number(value, label, minimum=None, maximum=None):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
            raise FilmocityError(label + " is outside its finite supported range")
        return value

    def _audio_sequence_capture(self, clip_ids, seq_id=None):
        ids = self._audio_ids(clip_ids)
        state = self.project_state(); project = state["project"]
        seq = next((s for s in project.get("sequences", []) if s["id"] == seq_id), None) if seq_id is not None else next(iter(project.get("sequences", [])), None)
        if seq is None: raise FilmocityError("choose an existing sequence")
        for identity in ids:
            matches = [(tr, c) for tr in seq.get("tracks", []) for c in tr.get("clips", []) if c.get("id") == identity]
            if len(matches) != 1: raise FilmocityError("a selected clip is missing or duplicated")
            if matches[0][0].get("locked"): raise FilmocityError("unlock every selected track first")
        return seq["id"], state["context"]

    def start_audio_analysis(self, mode, media_id=None, *, sequence=None, clip_ids=None,
                             in_=0, out=None, target=None, every=1, context=None, request_id=None):
        """Queue owned clip/source measurement; completion never applies gain or markers.

        Clip normalization measures isolated clip processing, excluding track/master.
        Raw media ranges are logical source seconds and remain read-only. Review
        exact measured scope, warnings and proposed changes before applying.
        """
        routes = {"peak": "/api/audio/peak", "loudness": "/api/audio/measure", "beats": "/api/audio/beats"}
        if mode not in routes: raise FilmocityError("audio mode must be peak, loudness or beats")
        timeline = sequence is not None or clip_ids is not None
        if timeline:
            if not isinstance(sequence, str) or not sequence or media_id is not None or out is not None or in_ != 0:
                raise FilmocityError("timeline audio analysis needs sequence and clip_ids without a raw source range")
            ids = self._audio_ids(clip_ids, 1 if mode == "beats" else 50)
        else:
            if not isinstance(media_id, str) or not media_id: raise FilmocityError("choose source media or timeline clips")
            self._audio_number(in_, "Source In", 0)
            if out is not None:
                self._audio_number(out, "Source Out", 0)
                if out <= in_: raise FilmocityError("Source Out must follow Source In")
        if mode == "beats":
            if target is not None: raise FilmocityError("beat analysis has no gain target")
            if isinstance(every, bool) or not isinstance(every, int) or not 1 <= every <= 128:
                raise FilmocityError("choose an integer beat interval from 1 to 128")
        elif target is not None:
            self._audio_number(target, "Normalization target", -40, 0)
        if request_id is None: request_id = uuid.uuid4().hex
        if not isinstance(request_id, str) or len(request_id) != 32 or any(c not in "0123456789abcdef" for c in request_id):
            raise FilmocityError("audio analysis request_id must contain 32 lowercase hexadecimal characters")
        if context is None: context = self.project_state()["context"]
        body = {"_context": context, "request_id": request_id, "actor": self.actor, "client": self.client}
        if timeline: body.update(sequence=sequence, clip_ids=ids)
        else:
            body.update(media_id=media_id, **{"in": in_})
            if out is not None: body["out"] = out
        if mode == "beats": body["every"] = every
        elif target is not None: body["target"] = target
        return self._call(routes[mode], body)

    def audio_result(self, task_id, *, context=None):
        """Read the current owned measurements and canonical reviewed plan."""
        if context is None: context = self.project_state()["context"]
        return self._call("/api/tasks/" + urllib.parse.quote(task_id, safe="") + "/audio", {"_context": context})

    def wait_audio_analysis(self, task_id, timeout=1800, poll=.5, *, context=None):
        """Wait without resubmitting; timeout leaves the task available in Tasks."""
        return self._wait_reviewed_task(task_id, "audio_analysis", self.audio_result, timeout, poll, context)

    def apply_audio_analysis(self, task_id, reviewed):
        """Apply one exact reviewed gain/marker plan; raw measurements cannot edit."""
        if not reviewed.get("result", {}).get("sequence") or not reviewed.get("plan", {}).get("ops"):
            raise FilmocityError("this audio result has no timeline changes to apply")
        return self._apply_reviewed_task(task_id, reviewed, "audio_analysis")

    def set_gain(self, clip_ids, value, *, mode="set", seq_id=None, context=None):
        """Set or adjust gain through one guarded transaction, preserving automation.

        The same delta shifts base gain and every stored manual-gain keyframe.
        A value that would exceed supported gain bounds refuses atomically.
        """
        ids = self._audio_ids(clip_ids)
        if mode not in ("set", "adjust"): raise FilmocityError("gain mode must be set or adjust")
        self._audio_number(value, "Gain", -40 if mode == "set" else None, 24 if mode == "set" else None)
        if context is None: seq_id, context = self._audio_sequence_capture(ids, seq_id)
        elif not isinstance(seq_id, str) or not seq_id: raise FilmocityError("pass the sequence captured with this context")
        return self._call("/api/audio/gain", {"_context": context, "sequence": seq_id, "clip_ids": ids,
                          "mode": mode, "value": value, "actor": self.actor, "client": self.client})

    def normalize_audio(self, clip_ids, target=None, *, mode="loudness", seq_id=None, preview=True, timeout=1800):
        """Review isolated-clip normalization; preview=False explicitly applies it."""
        if mode not in ("peak", "loudness"): raise FilmocityError("normalization mode must be peak or loudness")
        if target is None: target = -3 if mode == "peak" else -18
        self._audio_number(target, "Normalization target", -40, 0)
        seq_id, context = self._audio_sequence_capture(clip_ids, seq_id)
        queued = self.start_audio_analysis(mode, sequence=seq_id, clip_ids=clip_ids, target=target, context=context)
        reviewed = self.wait_audio_analysis(queued["task"]["id"], timeout, context=context)
        if preview: return reviewed
        if not reviewed.get("plan", {}).get("ops"): return {"ok": True, "changed": False, "task": reviewed["task"]}
        return self.apply_audio_analysis(queued["task"]["id"], reviewed)

    def add_beat_markers(self, clip_id, *, every=1, seq_id=None, preview=True, timeout=1800):
        """Review estimated beat positions; every is spacing, not a musical meter."""
        if isinstance(every, bool) or not isinstance(every, int) or not 1 <= every <= 128:
            raise FilmocityError("choose an integer beat interval from 1 to 128")
        seq_id, context = self._audio_sequence_capture([clip_id], seq_id)
        queued = self.start_audio_analysis("beats", sequence=seq_id, clip_ids=[clip_id], every=every, context=context)
        reviewed = self.wait_audio_analysis(queued["task"]["id"], timeout, context=context)
        if preview: return reviewed
        if not reviewed.get("plan", {}).get("ops"): return {"ok": True, "changed": False, "task": reviewed["task"]}
        return self.apply_audio_analysis(queued["task"]["id"], reviewed)

    def replace_source(self, clip_id, media_id, *, in_=0, out=None, seq_id=None, context=None):
        """Replace one clip's source with preserved timing/effects through guarded server validation.

        in_ selects the new logical lower source bound (reverse playback starts
        at the resulting Out). Optional out caps available source extent. Clip
        duration/retiming stay intact; invalid bounds or locked targets refuse.
        """
        if not isinstance(clip_id, str) or not clip_id or not isinstance(media_id, str) or not media_id:
            raise FilmocityError("choose an existing clip and replacement source")
        if isinstance(in_, bool) or not isinstance(in_, (int, float)) or not math.isfinite(in_) or in_ < 0:
            raise FilmocityError("replacement Source In must be nonnegative finite seconds")
        if out is not None and (isinstance(out, bool) or not isinstance(out, (int, float)) or not math.isfinite(out) or out <= in_):
            raise FilmocityError("replacement Source Out must be finite and greater than In")
        if context is None or seq_id is None:
            state = self.project_state()
            if context is None: context = state["context"]
            if seq_id is None:
                sequences = state["project"].get("sequences", [])
                if not sequences: raise FilmocityError("choose an existing sequence")
                seq_id = sequences[0]["id"]
        body = {"_context": context, "sequence": seq_id, "clip_id": clip_id, "media_id": media_id,
                "in": in_, "actor": self.actor, "client": self.client}
        if out is not None: body["out"] = out
        return self._call("/api/clip/replace-source", body)

    def start_sync(self, *, media_ids=None, sequence=None, clip_ids=None, context=None, request_id=None):
        """Queue captured audio matching. IDs are reference-first; completion never edits."""
        timeline = sequence is not None or clip_ids is not None
        if timeline and (not isinstance(sequence, str) or not sequence or clip_ids is None or media_ids is not None):
            raise FilmocityError("choose either sequence and clip_ids or raw media_ids for sync")
        ids = clip_ids if timeline else media_ids
        if not isinstance(ids, (list, tuple)) or not 2 <= len(ids) <= 8 or any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
            raise FilmocityError("sync needs two to eight unique IDs, with the reference first")
        if context is None: context = self.project_state()["context"]
        body = {"_context": context, "request_id": request_id or uuid.uuid4().hex, "actor": self.actor, "client": self.client}
        if timeline: body.update(sequence=sequence, clip_ids=list(ids))
        else: body["media_ids"] = list(ids)
        return self._call("/api/audio/sync", body)

    def sync_result(self, task_id, *, context=None):
        """Review source ownership, measured offsets/quality and any planned clip moves."""
        if context is None: context = self.project_state()["context"]
        return self._call("/api/tasks/" + urllib.parse.quote(task_id, safe="") + "/sync", {"_context": context})

    def wait_sync(self, task_id, timeout=900, poll=.5, *, context=None):
        """Wait for a read-only review without replaying submission on a lost reply."""
        return self._wait_reviewed_task(task_id, "sync", self.sync_result, timeout, poll, context)

    def apply_sync(self, task_id, reviewed):
        """Apply a timeline sync review; raw-source matching cannot mutate a sequence."""
        if reviewed.get("result", {}).get("mode") != "timeline" or not reviewed.get("plan", {}).get("ops"):
            raise FilmocityError("only a nonempty reviewed timeline sync plan can be applied")
        return self._apply_reviewed_task(task_id, reviewed, "sync")

    def sync(self, media_ids, *, timeout=900):
        """Read-only source alignment; positive offsets place a target after its reference.

        Inspect match confidence, measured resolution and warnings. No zero-offset
        fallback or timeline mutation follows unavailable/ambiguous matching.
        """
        submission = self.start_sync(media_ids=media_ids)
        return self.wait_sync(submission["task"]["id"], timeout, context=submission["context"])["result"]

    def synchronize(self, clip_ids, seq_id=None, *, preview=True, timeout=900):
        """Review selected-clip alignment; explicit preview=False applies one guarded edit."""
        if not isinstance(clip_ids, (list, tuple)) or not 2 <= len(clip_ids) <= 8 or any(not isinstance(i, str) or not i for i in clip_ids) or len(set(clip_ids)) != len(clip_ids):
            raise FilmocityError("select two to eight unique clips, reference first")
        state = self.project_state(); project, context = state["project"], state["context"]
        seq = next((s for s in project["sequences"] if s["id"] == seq_id), None) if seq_id else next(iter(project["sequences"]), None)
        if seq is None: raise FilmocityError("choose an existing sequence for synchronization")
        for identity in clip_ids:
            matches = [(track, clip) for track in seq["tracks"] for clip in track["clips"] if clip["id"] == identity]
            if len(matches) != 1: raise FilmocityError("a synchronization clip is missing or ambiguous")
            track, clip = matches[0]
            if track.get("locked") or clip.get("enabled") is False: raise FilmocityError("unlock and enable every synchronization clip")
            if not project.get("media", {}).get(clip.get("media_id"), {}).get("has_audio"):
                raise FilmocityError("every synchronization clip needs source audio")
        submission = self.start_sync(sequence=seq["id"], clip_ids=clip_ids, context=context)
        reviewed = self.wait_sync(submission["task"]["id"], timeout, context=context)
        if preview: return reviewed
        if not reviewed.get("plan", {}).get("ops"): return {"ok": True, "changed": False, "task": reviewed["task"], "message": "No synchronization moves are needed"}
        return self.apply_sync(submission["task"]["id"], reviewed)

    def start_render_replace(self, sequence, clip_id, *, context=None, request_id=None):
        """Queue an owned lossless bake. Completion does not change the timeline."""
        if not sequence or not clip_id: raise FilmocityError("render replacement needs sequence and clip_id")
        if context is None: context = self.project_state()["context"]
        return self._call("/api/render_replace", {"sequence": sequence, "clip_id": clip_id,
            "_context": context, "request_id": request_id or uuid.uuid4().hex, "actor": self.actor})

    def render_replace_result(self, task_id, *, context=None):
        """Review the ready bake, retained processing, source ownership and output receipt."""
        if context is None: context = self.project_state()["context"]
        return self._call("/api/tasks/" + urllib.parse.quote(task_id, safe="") + "/render-replace", {"_context": context})

    def wait_render_replace(self, task_id, timeout=1800, poll=.5, *, context=None):
        return self._wait_reviewed_task(task_id, "render_replace", self.render_replace_result, timeout, poll, context)

    def apply_render_replace(self, task_id, reviewed):
        """Apply the reviewed bake once; Undo restores the complete original clip."""
        return self._apply_reviewed_task(task_id, reviewed, "render_replace")

    def render_replace(self, clip_id, seq_id=None, *, preview=True, timeout=1800):
        """Prepare/review a lossless replacement; explicit preview=False applies the reviewed result."""
        state = self.project_state(); project, context = state["project"], state["context"]
        seq = next((s for s in project["sequences"] if s["id"] == seq_id), None) if seq_id else next(iter(project["sequences"]), None)
        matches = [(tr, c) for tr in (seq or {}).get("tracks", []) for c in tr["clips"] if c["id"] == clip_id]
        if len(matches) != 1: raise FilmocityError("choose one existing clip to render and replace")
        track, clip = matches[0]
        if track.get("locked") or clip.get("enabled") is False: raise FilmocityError("unlock and enable the selected clip before rendering a replacement")
        submission = self.start_render_replace(seq["id"], clip_id, context=context)
        reviewed = self.wait_render_replace(submission["task"]["id"], timeout, context=context)
        return reviewed if preview else self.apply_render_replace(submission["task"]["id"], reviewed)

    def scenes(self, media_id, threshold=0.35, *, in_=0, out=None, timeout=900):
        """Absolute media-clock scene boundaries from a managed read-only analysis."""
        submission = self.start_analysis("scenes", media_id, threshold=threshold, in_=in_, out=out)
        return self.wait_analysis(submission["task"]["id"], timeout, context=submission["context"])["result"]["cuts"]
    def loudness(self, media_id, in_=0, out=None, *, timeout=1800):
        """Wait for owned read-only source loudness; inspect measurement scope/warnings."""
        queued = self.start_audio_analysis("loudness", media_id, in_=in_, out=out)
        return self.wait_audio_analysis(queued["task"]["id"], timeout, context=queued["context"])["result"]

    def peak(self, media_id, in_=0, out=None, *, timeout=1800):
        """Wait for owned read-only source sample peak measurement."""
        queued = self.start_audio_analysis("peak", media_id, in_=in_, out=out)
        return self.wait_audio_analysis(queued["task"]["id"], timeout, context=queued["context"])["result"]
    def sfx(self, kind="whoosh"):
        """Generated sound effect bin item: whoosh | swoosh_reverse | riser | impact | pop | click."""
        return self._call("/api/media/sfx", {"kind": kind, "actor": self.actor})
    def silences(self, media_id, in_=0, out=None, threshold_db=-38, min_gap=0.45, *, timeout=900):
        """Silent source intervals in absolute media time; no edit is applied."""
        submission = self.start_analysis("silences", media_id, in_=in_, out=out, threshold_db=threshold_db, min_gap=min_gap)
        return self.wait_analysis(submission["task"]["id"], timeout, context=submission["context"])["result"]

    def remove_silences(self, clip_id, threshold_db=-38, min_gap=0.45, seq_id=None, *, preview=False, timeout=900):
        """Analyze then apply the canonical source-aware plan; preview=True returns a review without editing.

        Explicit target and unlocked sync peers participate. Conflicting unselected
        material, unsupported transitions or stale source/context reject atomically.
        """
        state = self.project_state(); project, context = state["project"], state["context"]
        seq = next((s for s in project["sequences"] if s["id"] == seq_id), None) if seq_id else next(iter(project["sequences"]), None)
        if seq is None: raise FilmocityError("no target sequence")
        for track in seq["tracks"]:
            for clip in track["clips"]:
                if clip["id"] != clip_id: continue
                if not clip.get("media_id"): raise FilmocityError("silence analysis needs source media")
                if track.get("locked"): raise FilmocityError("unlock the selected track before removing silence")
                if clip.get("hold"): raise FilmocityError("source silence analysis cannot edit a held frame")
                submission = self.start_analysis("silences", clip["media_id"], sequence=seq["id"], clip_id=clip_id,
                    context=context, in_=clip["in_"], out=clip["out"], threshold_db=threshold_db, min_gap=min_gap)
                reviewed = self.wait_analysis(submission["task"]["id"], timeout, context=context)
                if preview: return reviewed
                if not reviewed.get("plan", {}).get("ops"): return {"ok": True, "removed": 0, "task": reviewed["task"]}
                return self.apply_analysis(submission["task"]["id"], reviewed)
        raise FilmocityError(f"no clip {clip_id}")
    def beats(self, media_id, *, in_=0, out=None, every=1, timeout=1800):
        """Read-only estimated beat positions in the reported clock; no downbeat claim."""
        queued = self.start_audio_analysis("beats", media_id, in_=in_, out=out, every=every)
        return self.wait_audio_analysis(queued["task"]["id"], timeout, context=queued["context"])["result"]
    def caption_style(self, seq_id=None, **style):
        """Caption look, incl. animate: 'highlight'|'pop' (spoken word in highlight_color) and highlight_color."""
        seq = self.sequence(seq_id); idx = [x["id"] for x in self.project()["sequences"]].index(seq["id"]); cur = seq.get("caption_style") or {}
        return self.patch([{"op": "set", "path": f"/sequences/{idx}/caption_style", "value": {**cur, **style}}], tool="captions", reason="caption style")
    def remix(self, media_id, target, *, in_=0, out=None, bars_per_phrase=4, timeout=900):
        """Read-only source remix plan with exact requested/achieved time and editorial warnings."""
        submission = self.start_analysis("remix", media_id, in_=in_, out=out, target=target, bars_per_phrase=bars_per_phrase)
        return self.wait_analysis(submission["task"]["id"], timeout, context=submission["context"])["result"]

    def remix_clip(self, clip_id, target, seq_id=None, *, preview=True, bars_per_phrase=4, timeout=900):
        """Review an audio-track remix; preview=False applies one canonical non-ripple edit."""
        state = self.project_state(); project, context = state["project"], state["context"]
        seq = next((s for s in project["sequences"] if s["id"] == seq_id), None) if seq_id else next(iter(project["sequences"]), None)
        matches = [(tr, c) for tr in (seq or {}).get("tracks", []) for c in tr["clips"] if c["id"] == clip_id]
        if len(matches) != 1: raise FilmocityError("choose one existing music clip")
        track, clip = matches[0]
        if track.get("kind") != "audio" or track.get("locked") or clip.get("hold") or not clip.get("media_id"):
            raise FilmocityError("remix needs an unlocked audio-track source clip without a frame hold")
        submission = self.start_analysis("remix", clip["media_id"], sequence=seq["id"], clip_id=clip_id,
            context=context, in_=clip["in_"], out=clip["out"], target=target, bars_per_phrase=bars_per_phrase)
        reviewed = self.wait_analysis(submission["task"]["id"], timeout, context=context)
        if preview: return reviewed
        if not reviewed.get("plan", {}).get("ops"): return {"ok": True, "task": reviewed["task"], "message": "No remix edit is needed"}
        return self.apply_analysis(submission["task"]["id"], reviewed)
    def render(self, name="export", preset=None, outputs=None, seq_id=None):
        seq = self.sequence(seq_id); body = {"sequence": seq["id"], "preset": preset or {"crf": 18, "loudnorm": True}, "name": name, "actor": self.actor}
        if outputs: body["outputs"] = outputs
        return self._call("/api/render", body)
    def preflight(self, preset=None, seq_id=None, all_sequences=False):
        """Inspect originals, external graphics/LUTs/fonts and nested sequence references without rendering."""
        seq = self.sequence(seq_id)
        return self._call("/api/render/preflight", {"sequence": seq["id"], "preset": preset or {}, "all_sequences": all_sequences})
    @staticmethod
    def _interpretation_rate(value):
        """Validate a complete rate, retaining exact fractions and NTSC aliases."""
        if value is None: return None
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise FilmocityError("Interpretation rate must be a decimal or fraction; use None to restore native")
        text = str(value).strip()
        if len(text) > 64 or not re.fullmatch(r"(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+|[0-9]+/[0-9]+)", text):
            raise FilmocityError("Enter a complete decimal or rational frame rate, such as 30000/1001")
        try: rate = Fraction(text)
        except (ValueError, ZeroDivisionError, OverflowError) as error:
            raise FilmocityError("Interpretation rate is invalid") from error
        if not 0 < rate <= 1000: raise FilmocityError("Interpretation rate must be greater than zero and at most 1000")
        for n in (24, 30, 48, 60, 120):
            standard = Fraction(n * 1000, 1001)
            if abs(rate - standard) < Fraction(1, 2000): rate = standard; break
        return f"{rate.numerator}/{rate.denominator}"

    def _interpretation_review(self, review):
        error = "Review the source interpretation before applying; its owner, settings or affected sources are invalid"
        if not isinstance(review, dict) or review.get("kind") != "source_interpretation" or not isinstance(review.get("ok"), bool):
            raise FilmocityError(error)
        owner, _ = self._editorial_owner(review.get("context") if isinstance(review.get("context"), dict) else {})
        identity = self._editorial_identity(review.get("requested_media_id"), "reviewed source")
        if review.get("media_id") != identity: raise FilmocityError(error)
        settings = review.get("settings")
        if not isinstance(settings, dict) or "fps" not in settings or type(settings.get("include_fullmix")) is not bool:
            raise FilmocityError(error)
        rate = self._interpretation_rate(settings["fps"])
        if rate != settings["fps"]: raise FilmocityError(error)
        ids = review.get("affected_media_ids")
        if (not isinstance(ids, list) or any(not isinstance(i, str) or not i for i in ids)
                or len(ids) != len(set(ids)) or review["ok"] and identity not in ids):
            raise FilmocityError(error)
        if not isinstance(review.get("issues"), list) or not isinstance(review.get("summary"), dict):
            raise FilmocityError(error)
        if review["ok"] and (not isinstance(review.get("fingerprint"), str) or not re.fullmatch("[0-9a-f]{64}", review["fingerprint"])):
            raise FilmocityError(error)
        return owner, identity, {"fps": rate, "include_fullmix": settings["include_fullmix"]}, list(ids)

    def review_interpretation(self, media_id, fps, *, include_fullmix=False, context=None):
        """Review a source rate change without saving it or starting preparation.

        Pass None explicitly to restore native timing. Read all source windows,
        timeline uses and issues before apply_interpretation(). A physical
        source's legacy full-mix aliases require explicit include_fullmix=True.
        Ordinary subclips and selected-channel aliases keep independent rates.
        """
        self._editorial_identity(media_id, "source")
        rate = self._interpretation_rate(fps)
        if type(include_fullmix) is not bool: raise FilmocityError("include_fullmix must be a boolean")
        owner, project = self._editorial_owner(context)
        if project is not None and media_id not in project.get("media", {}):
            raise FilmocityError("Choose an existing source in the captured project")
        settings = {"fps": rate, "include_fullmix": include_fullmix}
        report = self._call("/api/media/interpret/review", {"media_id": media_id, **settings,
            "_context": owner, "actor": self.actor, "client": self.client})
        reviewed_owner, identity, reviewed_settings, _ = self._interpretation_review(report)
        if reviewed_owner != owner or identity != media_id or reviewed_settings != settings:
            raise FilmocityError("Interpretation review does not match the captured owner, source and requested rate")
        return report

    def apply_interpretation(self, review):
        """Save exactly a reviewed rate change with one Undo transaction.

        This never rereads the active project, re-reviews or retries. Inspect
        saved state/Recovery after an uncertain reply before any further edit.
        Timeline payloads remain unchanged; invalid ranges refuse atomically.
        """
        owner, identity, settings, ids = self._interpretation_review(review)
        if review["ok"] is not True: raise FilmocityError("Resolve the interpretation review issues before applying")
        result = self._call("/api/media/interpret", {"media_id": identity, **settings,
            "_context": owner, "fingerprint": review["fingerprint"], "actor": self.actor, "client": self.client})
        uncertain = "Interpretation outcome is uncertain; inspect saved state/Recovery before any further edit. The command was not replayed."
        if not isinstance(result, dict): raise FilmocityError(uncertain)
        acknowledged = result.get("context"); selected = result.get("media")
        if (result.get("ok") is not True or result.get("kind") != "source_interpretation"
                or type(result.get("changed")) is not bool or result.get("project") != owner["project"]
                or result.get("media_id") != identity or result.get("requested_media_id") != identity
                or result.get("affected_media_ids") != ids or not isinstance(acknowledged, dict)
                or any(acknowledged.get(k) != owner[k] for k in ("workspace", "project"))
                or not isinstance(acknowledged.get("revision"), str) or not acknowledged["revision"]
                or not isinstance(selected, dict) or selected.get("id") != identity
                or not isinstance(result.get("summary"), dict) or not isinstance(result.get("warnings"), list)
                or not isinstance(result.get("preparation"), dict)):
            raise FilmocityError(uncertain)
        return result

    def inspect_relink(self, media_id, path, *, context=None):
        """Inspect a replacement against one saved owner; never changes media.

        Review issues, affected sources and clock changes before apply_relink().
        The returned canonical path belongs to the server's filesystem.
        """
        self._editorial_identity(media_id, "source")
        if not isinstance(path, str) or not path.strip() or "\0" in path:
            raise FilmocityError("Choose a replacement file path")
        owner, project = self._editorial_owner(context)
        if project is not None and media_id not in project.get("media", {}):
            raise FilmocityError("Choose an existing source in the captured project")
        report = self._call("/api/media/relink/inspect", {"media_id": media_id, "path": path,
            "_context": owner, "actor": self.actor, "client": self.client})
        if not isinstance(report, dict) or report.get("context") != owner or report.get("requested_media_id") != media_id:
            raise FilmocityError("Relink inspection does not match the captured owner and source")
        if not isinstance(report.get("ok"), bool) or not isinstance(report.get("path"), str) or not report["path"]:
            raise FilmocityError("Relink inspection returned an invalid review")
        self._editorial_identity(report.get("media_id"), "physical source in the review")
        if report["ok"] and (not isinstance(report.get("fingerprint"), str) or not report["fingerprint"]):
            raise FilmocityError("Relink inspection did not return its reviewed fingerprint")
        return report

    def apply_relink(self, review):
        """Apply exactly an accepted inspection with one Undo; never re-inspect.

        Lost replies must be reconciled through saved state; this method never
        retries. Source-wide changes can affect uses on locked tracks.
        """
        if not isinstance(review, dict): raise FilmocityError("Inspect and review the replacement before applying Relink")
        return self.relink(review.get("requested_media_id"), review.get("path"), review=review)

    def relink(self, media_id, path, expected_source=None, *, review=None, context=None):
        """Apply a reviewed replacement using its canonical server path.

        Migration: expected_source alone no longer authorizes Relink. Pass the
        report from inspect_relink as review, or use apply_relink(report).
        Explicit context must equal that report; no new owner is captured.
        """
        self._editorial_identity(media_id, "source")
        if not isinstance(path, str) or not path.strip() or "\0" in path:
            raise FilmocityError("Choose the reviewed replacement file path")
        if not isinstance(review, dict) or review.get("ok") is not True:
            raise FilmocityError("Inspect and review the replacement first; expected_source alone is insufficient")
        owner, _ = self._editorial_owner(review.get("context") if isinstance(review.get("context"), dict) else {})
        if context is not None:
            explicit, _ = self._editorial_owner(context)
            if explicit != owner: raise FilmocityError("The supplied context differs from the reviewed owner")
        if review.get("requested_media_id") != media_id or review.get("path") != path:
            raise FilmocityError("The source/path differs from the review; pass the canonical reviewed path or inspect again")
        physical = self._editorial_identity(review.get("media_id"), "physical source in the review")
        fingerprint = review.get("fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint:
            raise FilmocityError("The review is missing its fingerprint; inspect the replacement again")
        if expected_source is not None and expected_source != review.get("expectedSource"):
            raise FilmocityError("expected_source differs from the reviewed source")
        body = {"media_id": media_id, "path": path, "_context": owner, "fingerprint": fingerprint,
            "actor": self.actor, "client": self.client}
        if expected_source is not None: body["expectedSource"] = expected_source
        result = self._call("/api/media/relink", body)
        acknowledged = result.get("context") if isinstance(result, dict) else None
        if (not isinstance(result, dict) or result.get("ok") is not True or result.get("media_id") != physical or
                not isinstance(acknowledged, dict) or any(acknowledged.get(k) != owner[k] for k in ("workspace", "project")) or
                not isinstance(acknowledged.get("revision"), str) or not acknowledged["revision"]):
            raise FilmocityError("Relink outcome is uncertain: its acknowledgement does not match the reviewed owner/source; inspect saved state before any further edit")
        return result
    def wait_render(self, job, timeout=3600):
        jid = job["id"] if isinstance(job, dict) else job; t0 = time.time()
        while time.time() - t0 < timeout:
            st = self._call(f"/api/render/{jid}")
            if st["status"] not in ("queued", "running"): return st
            time.sleep(2)
        return None

import urllib.parse  # noqa: E402  (used by browse)

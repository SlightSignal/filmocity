"""Filmocity agent SDK — a thin, dependency-free Python client for the agent side of the collaboration.

    from filmocity_client import Filmocity
    cr = Filmocity("http://localhost:8787", actor="agent")
    media = cr.import_paths(["/footage/a.mp4"])
    cr.place(media[0]["id"], track="V1", start=0, in_=1.0, out=4.0, reason="opening wide shot")
    pid = cr.propose("Tighten the open", [ {"ops": [...], "reason": "…"} ])
    decisions = cr.wait_for_decisions(pid, timeout=600)

Conventions the human side relies on: always send `reason` (it is shown on the timeline ghost and stored with the decision),
write the *why* into the clip's `note`, and call `score()` before proposing so you know how the proposal will land."""
import json, os, time, urllib.request, urllib.error, uuid

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
    def sequence(self, seq_id=None):
        p = self.project(); return next((s for s in p["sequences"] if s["id"] == seq_id), p["sequences"][0]) if p["sequences"] else None
    def media(self): return self.project()["media"]
    def import_paths(self, paths): return self._call("/api/media/import", {"paths": list(paths), "actor": self.actor})["added"]
    def browse(self, path=""): return self._call(f"/api/fs?path={urllib.parse.quote(path)}") if path else self._call("/api/fs")
    def brief(self): return self.project().get("brief") or {}
    def set_brief(self, **fields): return self.patch([{"op": "set", "path": "/brief", "value": {**self.brief(), **fields}}], tool="brief", reason="agent brief update")

    # ---- edits ----
    def patch(self, ops, tool="agent", reason=None, normalize=True):
        """Apply ops directly (they appear live, glow green, and are logged with your reason). Raises FilmocityError on invalid ops."""
        return self._call("/api/project", {"ops": ops, "actor": self.actor, "tool": tool, "reason": reason, "client": self.client, "normalize": normalize}, method="PATCH")
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
    def propose(self, title, items):
        """items: [{ops:[…], reason:str, tool?:str}] → proposal id. Invalid ops are rejected with a FilmocityError naming the problem."""
        r = self._call("/api/proposals", {"actor": self.actor, "title": title, "items": items}); return r["id"]
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
    def reel(self, shots, music=None, target=15, hook="Stop scrolling.", hook_sub="", cta="FOLLOW FOR PART 2 →", look=None, caption_style=None, sfx=True, seq_id=None):
        """Build an engaging reel from shots (media ids) + music: hook card, beat-cut shots with blur-fill/push-ins, remixed bed, CTA, captions, whooshes."""
        seq = self.sequence(seq_id); return self._call("/api/recipes/reel", {"sequence": seq["id"], "shots": shots, "music": music, "target": target, "hook": hook, "hook_sub": hook_sub, "cta": cta, "look": look, "captions": bool(caption_style), "caption_style": caption_style, "sfx": sfx, "actor": self.actor})
    def note(self, time_s, text, duration=0, seq_id=None):
        """Leave a review note at a time (a yellow marker the human can resolve): 'this cut is late', 'caption overlaps the UI'…"""
        seq = self.sequence(seq_id); return self._call("/api/markers/note", {"sequence": seq["id"], "time": time_s, "text": text, "duration": duration, "author": self.actor})
    def describe(self, seq_id=None):
        """Whole-cut summary (markdown text + sections + totals) — read this before proposing changes."""
        seq = self.sequence(seq_id); return self._call(f"/api/sequence/describe?sequence={seq['id']}")
    def variants(self, hooks, seq_id=None):
        """Hook variants for A/B testing: one duplicate sequence per headline, hook card re-fitted."""
        seq = self.sequence(seq_id); return self._call("/api/sequences/variants", {"sequence": seq["id"], "hooks": hooks, "actor": self.actor})
    def talking_head(self, clip_id, broll=(), punch_every=3, silences=True, voice_preset=True, captions=True, seq_id=None):
        """Talking-head treatment: silences out (ripple), alternating punch-ins, Voice Clean-up, word-pop captions (from the transcript), b-roll over the speech."""
        seq = self.sequence(seq_id); return self._call("/api/recipes/talking_head", {"sequence": seq["id"], "clip_id": clip_id, "broll": list(broll), "punch_every": punch_every, "silences": silences, "voice_preset": voice_preset, "captions": captions, "actor": self.actor})
    def duck_all(self, amount=-12, fade=0.35, seq_id=None):
        """Dip every music clip under every dialogue span with keyframes."""
        seq = self.sequence(seq_id); return self._call("/api/audio/duck_all", {"sequence": seq["id"], "amount": amount, "fade": fade, "actor": self.actor})
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
    def split_words(self, clip_id, layer_index=0, anim=None, stagger=0.12, seq_id=None):
        """Kinetic typography: split a text layer into per-word layers with staggered animation."""
        seq = self.sequence(seq_id); return self._call("/api/graphics/split_words", {"sequence": seq["id"], "clip_id": clip_id, "layer": layer_index, "anim": anim or {"type": "pop", "duration": 0.35, "ease": "back_out"}, "stagger": stagger, "actor": self.actor})
    def render_preview(self, seq_id=None): seq = self.sequence(seq_id); return self._call("/api/render/preview", {"sequence": seq["id"], "actor": self.actor})
    def segments(self, seq_id=None): seq = self.sequence(seq_id); return self._call(f"/api/render/segments?sequence={seq['id']}")
    def focus(self, t):
        """Show the human where you are looking: a green agent playhead at time t (seconds) in the timeline."""
        return self._call("/api/events", {"type": "focus", "actor": self.actor, "client": self.client, "t": float(t)})
    def snapshot(self, label="agent_proposal", seq_id=None): seq = self.sequence(seq_id); return self._call("/api/snapshot", {"label": label, "actor": self.actor, "sequence": seq["id"]})
    def annotate(self, clip_id, label, reasons=(), note="", track="V1", seq_id=None): seq = self.sequence(seq_id); return self._call("/api/annotate", {"target": {"sequence": seq["id"], "track": track, "clip_id": clip_id}, "label": label, "reasons": list(reasons), "note": note, "actor": self.actor})

    # ---- analysis & delivery ----
    def scenes(self, media_id, threshold=0.35): return self._call("/api/media/scenes", {"media_id": media_id, "threshold": threshold})["cuts"]
    def loudness(self, media_id, in_=0, out=None): return self._call("/api/audio/measure", {"media_id": media_id, "in": in_, "out": out})
    def sfx(self, kind="whoosh"):
        """Generated sound effect bin item: whoosh | swoosh_reverse | riser | impact | pop | click."""
        return self._call("/api/media/sfx", {"kind": kind, "actor": self.actor})
    def silences(self, media_id, in_=0, out=None, threshold_db=-38, min_gap=0.45):
        """Silent gaps in a media file's audio (media time) → {silences:[{start,end}], removed}."""
        return self._call("/api/audio/silences", {"media_id": media_id, "in": in_, "out": out, "threshold_db": threshold_db, "min_gap": min_gap})
    def remove_silences(self, clip_id, threshold_db=-38, min_gap=0.45, seq_id=None):
        """Tighten a talking-head clip: cut its silent gaps and ripple everything after it."""
        seq = self.sequence(seq_id)
        for t in seq["tracks"]:
            for c in t["clips"]:
                if c["id"] == clip_id and c.get("media_id"):
                    r = self.silences(c["media_id"], c["in_"], c["out"], threshold_db, min_gap); sp = c.get("speed", 1) or 1; cur = c["in_"]; pieces = []
                    for g in r["silences"]:
                        a, b_ = max(cur, c["in_"]), min(g["start"], c["out"])
                        if b_ - a > 0.04: pieces.append((a, b_))
                        cur = max(cur, g["end"])
                    if c["out"] - cur > 0.04: pieces.append((cur, c["out"]))
                    if not pieces or len(r["silences"]) == 0: return {"removed": 0}
                    end0 = c["start"] + (c["out"] - c["in_"]) / sp; total = sum((b_ - a) / sp for a, b_ in pieces); removed = (c["out"] - c["in_"]) / sp - total
                    ops = [{"op": "remove_clip", "sequence": seq["id"], "track": t["id"], "clip_id": clip_id}]; tt = c["start"]
                    for i, (a, b_) in enumerate(pieces):
                        nc = json.loads(json.dumps(c)); nc.update(id=(clip_id if i == 0 else uuid.uuid4().hex[:8]), start=round(tt, 4), in_=round(a, 4), out=round(b_, 4)); nc["note"] = (c.get("note", "") + " · " if c.get("note") else "") + f"silence-cut {i + 1}/{len(pieces)}"
                        if i: nc["transition_in"] = None
                        if i < len(pieces) - 1: nc["transition_out"] = None
                        ops.append({"op": "set_clip", "sequence": seq["id"], "track": t["id"], "clip": nc}); tt += (b_ - a) / sp
                    for t2 in seq["tracks"]:
                        for x in t2["clips"]:
                            if x["id"] != clip_id and x["start"] >= end0 - 1e-6: ops.append({"op": "set_clip", "sequence": seq["id"], "track": t2["id"], "clip": {"id": x["id"], "start": round(x["start"] - removed, 4)}})
                    self.patch(ops, tool="silences", reason=f"remove {len(r['silences'])} silences (-{removed:.2f}s)"); return {"removed": round(removed, 2), "pieces": len(pieces)}
        raise FilmocityError(f"no clip {clip_id}")
    def beats(self, media_id):
        """Beat grid for a music item → {bpm, beat, beats:[…], downbeats:[…]} (media time). Cut to the downbeats."""
        return self._call("/api/audio/beats", {"media_id": media_id})
    def caption_style(self, seq_id=None, **style):
        """Caption look, incl. animate: 'highlight'|'pop' (spoken word in highlight_color) and highlight_color."""
        seq = self.sequence(seq_id); idx = [x["id"] for x in self.project()["sequences"]].index(seq["id"]); cur = seq.get("caption_style") or {}
        return self.patch([{"op": "set", "path": f"/sequences/{idx}/caption_style", "value": {**cur, **style}}], tool="captions", reason="caption style")
    def remix(self, media_id, target): return self._call("/api/audio/remix", {"media_id": media_id, "target": target})
    def render(self, name="export", preset=None, outputs=None, seq_id=None):
        seq = self.sequence(seq_id); body = {"sequence": seq["id"], "preset": preset or {"crf": 18, "loudnorm": True}, "name": name, "actor": self.actor}
        if outputs: body["outputs"] = outputs
        return self._call("/api/render", body)
    def wait_render(self, job, timeout=3600):
        jid = job["id"] if isinstance(job, dict) else job; t0 = time.time()
        while time.time() - t0 < timeout:
            st = self._call(f"/api/render/{jid}")
            if st["status"] not in ("queued", "running"): return st
            time.sleep(2)
        return None

import urllib.parse  # noqa: E402  (used by browse)

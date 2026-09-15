"""Filmocity backend — local FastAPI server: media ingest, project store (open JSON EDL), event log, snapshots/diffs for
training data, render jobs (FFmpeg), agent API (REST + WebSocket), and static hosting of the front end.

Run:  cd tools/filmocity && python3 backend/server.py --root ~/filmocity_data --port 8787
Then open http://localhost:8787  (the agent uses the same URL; see docs/AGENT_API.md)
"""
import os, sys, json, time, uuid, shutil, subprocess, threading, argparse, copy, mimetypes
from typing import Any, Optional
import asyncio, hashlib
from fastapi import FastAPI, Request, UploadFile, File, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
sys.path.insert(0, os.path.dirname(__file__))
from render import render as do_render, build_command, render_frame, render_incremental, segment_boundaries, chunk_sequence, chunk_key
from interchange import to_fcp7_xml, to_otio, captions_to_srt, srt_to_captions, from_fcp7_xml, to_edl, captions_to_vtt
from effects import catalog as effects_catalog
import advisor as _advisor

HERE = os.path.dirname(os.path.abspath(__file__)); FRONT = os.path.join(os.path.dirname(HERE), "frontend"); ASSETS = os.path.join(os.path.dirname(HERE), "assets"); DOCS = os.path.join(os.path.dirname(HERE), "docs")
ROOT = os.environ.get("FILMOCITY_ROOT", os.path.expanduser("~/filmocity_data"))
def P(*a): return os.path.join(ROOT, *a)

VERSION = "0.20"
app = FastAPI(title="Filmocity", version=VERSION, docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
LOCK = threading.Lock(); CLIENTS: list[WebSocket] = []; JOBS: dict[str, dict] = {}; OPS_SINCE_AUTOSAVE = 0

# ---------- project store ----------
def active_id():
    p = P("active.json")
    if os.path.exists(p):
        try: return json.load(open(p)).get("id") or "default"
        except Exception: pass
    return "default"

def PP(*a):
    """Project-scoped path (project.json, events.jsonl, snapshots/, training/, proposals live per project)."""
    d = P("projects", active_id()); os.makedirs(d, exist_ok=True); return os.path.join(d, *a)

def migrate_legacy():
    """Move a v0.7-style single project (data_root/project.json + events + snapshots + training) into projects/default/."""
    if os.path.exists(P("project.json")) and not os.path.exists(P("projects", "default", "project.json")):
        os.makedirs(P("projects", "default"), exist_ok=True)
        for item in ("project.json", "events.jsonl", "snapshots", "training"):
            if os.path.exists(P(item)) and not os.path.exists(P("projects", "default", item)): shutil.move(P(item), P("projects", "default", item))

def default_project():
    return {"version": SCHEMA, "id": str(uuid.uuid4())[:8], "name": "Untitled", "media": {}, "bins": [], "brief": {}, "proposals": [],
            "sequences": [{"id": "seq1", "name": "Sequence 01", "width": 1080, "height": 1920, "fps": 30, "duration": None, "markers": [],
                           "tracks": [{"id": "V2", "kind": "video", "index": 2, "muted": False, "locked": False, "clips": []},
                                      {"id": "V1", "kind": "video", "index": 1, "muted": False, "locked": False, "clips": []},
                                      {"id": "A1", "kind": "audio", "index": 1, "muted": False, "locked": False, "clips": []},
                                      {"id": "A2", "kind": "audio", "index": 2, "muted": False, "locked": False, "clips": []}]}],
            "annotations": [], "updated": time.time()}

def load_project():
    p = PP("project.json")
    if not os.path.exists(p):
        save_project(default_project())
    proj = json.load(open(p))
    if proj.get("version", 1) < SCHEMA: proj = migrate_project(proj); save_project(proj)
    return proj

SCHEMA = 3
def migrate_project(proj):
    v = proj.get("version", 1)
    if v < 2:  # v1 → v2: bins/proposals/annotations/brief containers
        proj.setdefault("bins", []); proj.setdefault("proposals", []); proj.setdefault("annotations", []); proj.setdefault("brief", {})
    if v < 3:  # v2 → v3: per-sequence guides, master bus, captions
        for sq in proj.get("sequences", []): sq.setdefault("guides", {"h": [], "v": []}); sq.setdefault("master", {}); sq.setdefault("captions", [])
    proj["version"] = SCHEMA; return proj

def save_project(proj):
    proj["updated"] = time.time(); proj.setdefault("version", SCHEMA)
    tmp = PP("project.json.tmp"); json.dump(proj, open(tmp, "w"), indent=1)
    cur = PP("project.json")
    if os.path.exists(cur):  # rolling backups: one per minute at most, keep the last 30
        bdir = PP("backups"); os.makedirs(bdir, exist_ok=True); last = sorted(os.listdir(bdir))[-1:] if os.listdir(bdir) else []
        if not last or time.time() - float(last[0].split("_")[1].split(".")[0]) > 60:
            shutil.copy2(cur, os.path.join(bdir, f"project_{int(time.time())}.json"))
            for old in sorted(os.listdir(bdir))[:-30]: os.remove(os.path.join(bdir, old))
    os.replace(tmp, cur)

def log_event(ev):
    ev.setdefault("ts", time.time()); ev.setdefault("id", str(uuid.uuid4())[:8]); ev.setdefault("project", active_id())
    with open(PP("events.jsonl"), "a") as f: f.write(json.dumps(ev) + "\n")
    return ev

TOKEN = {"value": os.environ.get("FILMOCITY_TOKEN") or None}
@app.middleware("http")
async def _auth(request: Request, call_next):
    tok = TOKEN["value"]
    if tok and (request.url.path.startswith("/api/") or request.url.path in ("/", "/ws")):
        given = request.headers.get("authorization", "").replace("Bearer ", "") or request.query_params.get("token") or request.cookies.get("filmocity_token")
        if given != tok and request.url.path != "/api/version": return JSONResponse({"error": "access token required (append ?token=… to the URL once)"}, status_code=401)
    resp = await call_next(request)
    if tok and request.query_params.get("token") == tok: resp.set_cookie("filmocity_token", tok, httponly=True, samesite="lax")
    return resp

MAIN_LOOP = {"loop": None}
@app.on_event("startup")
async def _remember_loop(): MAIN_LOOP["loop"] = asyncio.get_running_loop()
def broadcast_threadsafe(ev):
    loop = MAIN_LOOP.get("loop")
    if loop: asyncio.run_coroutine_threadsafe(broadcast(ev), loop)
async def broadcast(ev):
    dead = []
    for ws in CLIENTS:
        try: await ws.send_json(ev)
        except Exception: dead.append(ws)
    for ws in dead:
        if ws in CLIENTS: CLIENTS.remove(ws)

# ---------- json pointer ops ----------
def _walk(obj, path):
    parts = [p for p in path.strip("/").split("/") if p != ""]
    for p in parts[:-1]:
        obj = obj[int(p)] if isinstance(obj, list) else obj[p]
    return obj, (parts[-1] if parts else None)

def validate_ops(proj, ops):
    """Reject malformed ops before they touch the project: unknown op, missing sequence/track, negative or inverted ranges, unknown media."""
    seqs = {sq["id"]: sq for sq in proj["sequences"]}; problems = []
    for i, o in enumerate(ops):
        k = o.get("op")
        if k not in ("set_clip", "remove_clip", "set", "insert", "remove", "replace"): problems.append(f"op[{i}]: unknown op '{k}'"); continue
        if k in ("set_clip", "remove_clip"):
            sq = seqs.get(o.get("sequence"))
            if not sq: problems.append(f"op[{i}]: unknown sequence '{o.get('sequence')}'"); continue
            tr = next((t for t in sq["tracks"] if t["id"] == o.get("track")), None)
            if not tr: problems.append(f"op[{i}]: unknown track '{o.get('track')}' in {sq['id']}"); continue
            if k == "set_clip":
                c = o.get("clip") or {}
                if not c.get("id"): problems.append(f"op[{i}]: clip needs an id"); continue
                cur = next((x for x in tr["clips"] if x["id"] == c["id"]), None); merged = {**(cur or {}), **c}
                if cur is None and not any(merged.get(k2) is not None for k2 in ("media_id", "sequence_id", "title", "graphic", "adjustment")): problems.append(f"op[{i}]: new clip {c['id']} needs media_id, sequence_id, title, graphic or adjustment")
                if merged.get("media_id") and merged["media_id"] not in proj["media"] and not str(merged["media_id"]).startswith("nested:"): problems.append(f"op[{i}]: unknown media '{merged['media_id']}'")
                for fld in ("start", "in_", "out"):
                    if fld in merged and (merged[fld] is None or (isinstance(merged[fld], (int, float)) and merged[fld] < -1e-9)): problems.append(f"op[{i}]: {fld} must be ≥ 0")
                if "in_" in merged and "out" in merged and isinstance(merged.get("in_"), (int, float)) and isinstance(merged.get("out"), (int, float)) and merged["out"] <= merged["in_"] + 1e-9: problems.append(f"op[{i}]: out ({merged['out']}) must be after in ({merged['in_']})")
                if merged.get("speed") is not None and not (0.01 <= float(merged["speed"]) <= 100): problems.append(f"op[{i}]: speed out of range")
                m = proj["media"].get(merged.get("media_id")) if merged.get("media_id") else None
                if m and not m.get("is_image") and isinstance(merged.get("out"), (int, float)) and merged["out"] > float(m.get("duration", 1e9)) + 0.05: problems.append(f"op[{i}]: out ({merged['out']}) exceeds media duration ({m.get('duration')})")
    return problems

def inverse_ops(ops, befores, proj_after):
    """Exact inverses for a group of ops given the before-states captured when they were applied."""
    inv = []
    for o, b in reversed(list(zip(ops, befores))):
        k = o.get("op")
        if k == "set_clip": inv.append({"op": "set_clip", "sequence": o["sequence"], "track": o["track"], "clip": b} if b else {"op": "remove_clip", "sequence": o["sequence"], "track": o["track"], "clip_id": o["clip"]["id"]})
        elif k == "remove_clip":
            if b: inv.append({"op": "set_clip", "sequence": o["sequence"], "track": o["track"], "clip": b})
        elif k == "set": inv.append({"op": "set", "path": o["path"], "value": b} if b is not None else {"op": "remove", "path": o["path"]})
        elif k == "insert": inv.append({"op": "remove", "path": o["path"]})
        elif k == "remove": inv.append({"op": "insert", "path": o["path"], "value": b})
    # a set_clip that restores a clip must restore the *whole* clip, so drop partial merges by re-setting from the before copy (already whole)
    return inv

def undo_stack_path(): return PP("undo_stack.json")
def undo_push(entry):
    st = json.load(open(undo_stack_path())) if os.path.exists(undo_stack_path()) else {"undo": [], "redo": []}
    st["undo"].append(entry); st["undo"] = st["undo"][-200:]; st["redo"] = []; json.dump(st, open(undo_stack_path(), "w"))

def normalize_tracks(proj):
    """Premiere semantics: one clip per instant per track. Later-placed (list order) clips win; earlier clips are trimmed or split around them. Returns human-readable warnings."""
    warnings = []
    for sq in proj["sequences"]:
        for tr in sq["tracks"]:
            clips = tr["clips"]; changed = True; guard = 0
            while changed and guard < 20:
                changed = False; guard += 1
                for i in range(len(clips)):
                    for j in range(len(clips)):
                        if i == j: continue
                        a, b = clips[i], clips[j]
                        if i > j: continue  # b is later in list order → b wins over a
                        ad = (a["out"] - a["in_"]) / max(a.get("speed", 1), 1e-6); bd = (b["out"] - b["in_"]) / max(b.get("speed", 1), 1e-6)
                        a0, a1, b0, b1 = a["start"], a["start"] + ad, b["start"], b["start"] + bd
                        if b0 >= a1 - 1e-6 or b1 <= a0 + 1e-6: continue
                        sp = a.get("speed", 1)
                        if b0 <= a0 + 1e-6 and b1 >= a1 - 1e-6: clips.remove(a); warnings.append(f"{sq['id']}/{tr['id']}: clip {a['id']} fully covered by {b['id']} — removed"); changed = True; break
                        elif b0 <= a0 + 1e-6: a["in_"] += (b1 - a0) * sp; a["start"] = b1; warnings.append(f"{sq['id']}/{tr['id']}: clip {a['id']} head trimmed to {b['id']}"); changed = True; break
                        elif b1 >= a1 - 1e-6: a["out"] -= (a1 - b0) * sp; warnings.append(f"{sq['id']}/{tr['id']}: clip {a['id']} tail trimmed to {b['id']}"); changed = True; break
                        else:
                            right = copy.deepcopy(a); right["id"] = a["id"] + "_r"; right["start"] = b1; right["in_"] = a["in_"] + (b1 - a0) * sp; right["transition_in"] = None; a["out"] = a["in_"] + (b0 - a0) * sp; a["transition_out"] = None; clips.append(right); warnings.append(f"{sq['id']}/{tr['id']}: clip {a['id']} split around {b['id']}"); changed = True; break
                    if changed: break
    return warnings

def apply_ops(proj, ops):
    """ops: [{op:'set'|'insert'|'remove'|'replace_clip'|'set_clip', path, value}]; paths are JSON-pointer-like.
    Convenience: {op:'set_clip', sequence, track, clip:{...}} upserts a clip by id; {op:'remove_clip', sequence, track, clip_id}."""
    befores = []
    for o in ops:
        if o["op"] in ("set_clip", "remove_clip"):
            seq = next(s for s in proj["sequences"] if s["id"] == o["sequence"]); tr = next(t for t in seq["tracks"] if t["id"] == o["track"])
            if o["op"] == "set_clip":
                c = o["clip"]; c.setdefault("id", str(uuid.uuid4())[:8])
                old = next((x for x in tr["clips"] if x["id"] == c["id"]), None); befores.append(copy.deepcopy(old))
                if old: old.update(c)
                else: tr["clips"].append(c)
            else:
                old = next((x for x in tr["clips"] if x["id"] == o["clip_id"]), None); befores.append(copy.deepcopy(old))
                tr["clips"] = [x for x in tr["clips"] if x["id"] != o["clip_id"]]
            continue
        parent, key = _walk(proj, o["path"])
        if isinstance(parent, list): key = int(key)
        if o["op"] == "set":
            befores.append(copy.deepcopy(parent[key]) if (isinstance(parent, dict) and key in parent) or (isinstance(parent, list) and key < len(parent)) else None)
            parent[key] = o["value"]
        elif o["op"] == "insert":
            befores.append(None); parent.insert(key, o["value"])
        elif o["op"] == "remove":
            befores.append(copy.deepcopy(parent[key]))
            if isinstance(parent, list): parent.pop(key)
            else: del parent[key]
        else: raise HTTPException(400, f"unknown op {o['op']}")
    return befores

# ---------- media ----------
def probe(path):
    try: out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path], capture_output=True, text=True, timeout=60).stdout
    except subprocess.TimeoutExpired: out = ""
    j = json.loads(out or "{}"); v = next((s for s in j.get("streams", []) if s["codec_type"] == "video"), None); a = next((s for s in j.get("streams", []) if s["codec_type"] == "audio"), None)
    fps = 0.0
    if v and "/" in v.get("avg_frame_rate", ""): n, d = v["avg_frame_rate"].split("/"); fps = float(n)/float(d) if float(d) else 0
    nbf = int(v.get("nb_frames") or 0) if v and str(v.get("nb_frames", "")).isdigit() else 0
    is_image = bool(v) and (v.get("codec_name") in ("png", "mjpeg", "bmp", "webp", "tiff") or "image2" in j.get("format", {}).get("format_name", "") or (v.get("codec_name") == "gif" and nbf <= 1)) and not a
    dur = float(j.get("format", {}).get("duration", 0) or 0)
    hdr = bool(v) and (v.get("color_transfer") in ("smpte2084", "arib-std-b67") or v.get("color_primaries") == "bt2020"); rot = 0
    try:
        for sd in (v or {}).get("side_data_list", []) or []:
            if "rotation" in sd: rot = int(sd["rotation"])
        rot = rot or int((v or {}).get("tags", {}).get("rotate", 0) or 0)
    except (ValueError, TypeError): rot = 0
    w_, h_ = (int(v["width"]), int(v["height"])) if v else (0, 0)
    if rot % 180 != 0: w_, h_ = h_, w_  # display dimensions after autorotation
    return {"hdr": hdr, "rotation": rot, "color_transfer": (v or {}).get("color_transfer"), "pix_fmt": (v or {}).get("pix_fmt"), "vcodec": (v or {}).get("codec_name"), "acodec": (a or {}).get("codec_name"), "vfr": bool(v) and v.get("r_frame_rate") != v.get("avg_frame_rate"), "duration": 5.0 if is_image else dur, "width": w_, "height": h_, "is_image": is_image,
            "fps": 0.0 if is_image else fps, "has_video": v is not None, "has_audio": a is not None, "codec": v["codec_name"] if v else (a["codec_name"] if a else None)}

def ingest(path, name=None):
    """Fast path: probe only (≈50 ms) so imports return immediately; thumbnails, filmstrip, waveform and proxy are produced in the background
    and announced with a `media_ready` event. Media carry status: 'ingesting' → 'ready'."""
    mid = str(uuid.uuid4())[:8]; info = probe(path); os.makedirs(P("thumbs"), exist_ok=True)
    m = {"id": mid, "name": name or os.path.basename(path), "path": os.path.abspath(path), **info, "thumb": None, "strip": None, "wave": None, "status": "ingesting", "added": time.time()}
    threading.Thread(target=finish_ingest, args=(mid, path, info), daemon=True).start()
    return mid, m

def finish_ingest(mid, path, info):
    thumb = P("thumbs", f"{mid}.jpg"); wave = P("thumbs", f"{mid}_wave.png"); upd = {}
    try:
        if info["has_video"]:
            iopts = list(info.get("input_opts") or [])
            subprocess.run(["ffmpeg", "-hide_banner", "-y"] + iopts + ([] if info.get("is_image") else ["-ss", f"{min(1.0, info['duration']/2):.2f}"]) + ["-i", path, "-frames:v", "1", "-update", "1", "-vf", "scale=320:-2", thumb], capture_output=True, timeout=180)
            if info.get("is_image"): shutil.copy(thumb, P("thumbs", f"{mid}_strip.jpg"))
            else: subprocess.run(["ffmpeg", "-hide_banner", "-y"] + iopts + ["-i", path, "-vf", f"fps=10/{max(info['duration'],0.1)},scale=160:-2,tile=10x1", "-frames:v", "1", "-update", "1", P("thumbs", f"{mid}_strip.jpg")], capture_output=True, timeout=600)
            upd.update(thumb=f"/thumbs/{mid}.jpg", strip=f"/thumbs/{mid}_strip.jpg")
        if info["has_audio"]:
            subprocess.run(["ffmpeg", "-hide_banner", "-y", "-i", path, "-filter_complex", "showwavespic=s=1600x80:colors=0x8fb6ff", "-frames:v", "1", wave], capture_output=True, timeout=600); upd.update(wave=f"/thumbs/{mid}_wave.png")
    except subprocess.TimeoutExpired: upd["ingest_error"] = "thumbnail generation timed out"
    except Exception as e: upd["ingest_error"] = str(e)[-200:]
    upd["status"] = "ready"
    with LOCK:
        proj = load_project()
        if mid in proj["media"]: proj["media"][mid].update(upd); save_project(proj)
    broadcast_threadsafe({"type": "media_ready", "media": [mid], "ts": time.time()})
    if info["has_video"] and info["duration"] > 0 and not info.get("is_image"): make_proxy(mid, path, info.get("input_opts"))
    elif info["has_audio"] and not info["has_video"] and os.path.splitext(path)[1].lower() not in WEB_AUDIO_EXT: make_audio_proxy(mid, path)

WEB_AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".oga", ".opus", ".flac", ".webm"}
def make_audio_proxy(mid, path):
    """AAC proxy for audio files browsers cannot play natively (AIFF, CAF, WMA, AC-3, DTS, APE, …) so the monitors always have sound."""
    os.makedirs(P("proxies"), exist_ok=True); out = P("proxies", f"{mid}.m4a")
    r = subprocess.run(["ffmpeg", "-hide_banner", "-y", "-i", path, "-vn", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", out], capture_output=True, timeout=1800)
    if r.returncode == 0:
        with LOCK:
            proj = load_project()
            if mid in proj["media"]: proj["media"][mid]["proxy"] = f"/proxies/{mid}.m4a"; save_project(proj)
        ev = log_event({"type": "proxy_ready", "actor": "system", "media": mid}); broadcast_threadsafe(ev)

def make_proxy(mid, path, input_opts=None):
    """Low-res H.264 (yuv420p, 720p max) proxy for smooth, universally decodable preview of any source (ProRes, HEVC, MKV, AV1, image
    sequences, VFR phone video…); recorded on the media entry when done."""
    os.makedirs(P("proxies"), exist_ok=True); out = P("proxies", f"{mid}.mp4")
    r = subprocess.run(["ffmpeg", "-hide_banner", "-y"] + list(input_opts or []) + ["-i", path, "-vf", "scale='min(1280,iw)':-2,fps=30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p", "-g", "30", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out], capture_output=True, timeout=7200)
    if r.returncode == 0:
        with LOCK:
            proj = load_project()
            if mid in proj["media"]: proj["media"][mid]["proxy"] = f"/proxies/{mid}.mp4"; save_project(proj)
        ev = log_event({"type": "proxy_ready", "actor": "system", "media": mid}); broadcast_threadsafe(ev)

# ---------- API ----------
@app.get("/", response_class=HTMLResponse)
def index(): return open(os.path.join(FRONT, "index.html")).read()

@app.get("/api/project")
def get_project(): return load_project()

@app.put("/api/project")
async def put_project(req: Request):
    body = await req.json()
    with LOCK: save_project(body)
    ev = log_event({"type": "project_replaced", "actor": body.get("_actor", "human"), "source": body.get("_source", "ui")}); await broadcast(ev); return {"ok": True}

@app.patch("/api/project")
async def patch_project(req: Request):
    body = await req.json(); ops = body.get("ops", []); actor = body.get("actor", "human")
    if actor != "human":
        try: mode = (json.load(open(P("settings.json"))).get("agent_mode") if os.path.exists(P("settings.json")) else None) or "direct"
        except Exception: mode = "direct"
        if mode == "proposals_only": return JSONResponse({"ok": False, "errors": ["agent_mode is proposals_only: submit these ops as a proposal (POST /api/proposals) for the human to accept"], "agent_mode": mode}, status_code=403)
    with LOCK:
        proj = load_project(); problems = validate_ops(proj, ops)
        if problems: return JSONResponse({"ok": False, "errors": problems}, status_code=422)
        befores = apply_ops(proj, ops); warnings = normalize_tracks(proj) if body.get("normalize", True) else []; save_project(proj)
        touched = {}
        for o in ops:
            if o.get("op") in ("set_clip", "remove_clip"): touched.setdefault(o["sequence"], set()).add(o["track"])
        for w in warnings:
            try: sq_, tr_ = w.split(":")[0].split("/"); touched.setdefault(sq_, set()).add(tr_)
            except ValueError: pass
        applied = {sid: {tid: next((t["clips"] for sq in proj["sequences"] if sq["id"] == sid for t in sq["tracks"] if t["id"] == tid), None) for tid in tids} for sid, tids in touched.items()}
    LAST_OPS_TS["t"] = time.time()
    if not body.get("_no_undo"): undo_push({"ops": ops, "befores": befores, "actor": actor, "reason": body.get("reason") or body.get("tool"), "ts": time.time()})
    ev = log_event({"type": "ops", "actor": actor, "tool": body.get("tool"), "reason": body.get("reason"), "ops": ops, "befores": befores, "client": body.get("client"), "warnings": warnings})
    global OPS_SINCE_AUTOSAVE; OPS_SINCE_AUTOSAVE += 1
    if OPS_SINCE_AUTOSAVE >= 25:
        OPS_SINCE_AUTOSAVE = 0; os.makedirs(PP("snapshots"), exist_ok=True); json.dump(proj, open(PP("snapshots", f"{int(time.time())}_autosave.json"), "w"))
    await broadcast({**{k: v for k, v in ev.items() if k != "befores"}, "applied": applied}); return {"ok": True, "event_id": ev["id"], "warnings": warnings, "applied": applied}

@app.post("/api/media/import")
async def media_import(req: Request):
    body = await req.json(); added = []
    with LOCK:
        proj = load_project()
        for path in body.get("paths", []):
            if not os.path.exists(path): raise HTTPException(404, f"not found: {path}")
            mid, m = await asyncio.to_thread(ingest, path); proj["media"][mid] = m; added.append(m)
        save_project(proj)
    ev = log_event({"type": "media_added", "actor": body.get("actor", "human"), "media": [m["id"] for m in added]}); await broadcast(ev); return {"added": added}

@app.post("/api/media/upload")
async def media_upload(file: UploadFile = File(...)):
    os.makedirs(P("media"), exist_ok=True); dest = P("media", f"{int(time.time())}_{file.filename}")
    with open(dest, "wb") as f: shutil.copyfileobj(file.file, f)
    with LOCK:
        proj = load_project(); mid, m = await asyncio.to_thread(ingest, dest, file.filename); proj["media"][mid] = m; save_project(proj)
    ev = log_event({"type": "media_added", "actor": "human", "media": [mid]}); await broadcast(ev); return m

def _range_stream(path, start, end, chunk=1 << 20):
    with open(path, "rb") as f:
        f.seek(start); remaining = end - start + 1
        while remaining > 0:
            data = f.read(min(chunk, remaining))
            if not data: break
            remaining -= len(data); yield data

@app.get("/api/media/file/{mid}")
def media_file(mid: str, request: Request):
    proj = load_project(); m = proj["media"].get(mid)
    if not m: raise HTTPException(404)
    path = m["path"]
    # Proxy fallback, added 2026-09-07. m["proxy"] records that a proxy was
    # MADE, not that it is still there. It is absent whenever a project moves
    # machines (proxies live in the data dir, not beside the project), while
    # generation is still running, or after the data dir is cleared. Without
    # the exists() check getsize() raises and the clip 500s, which the editor
    # renders as a black program monitor -- indistinguishable from a broken
    # decode, and the reason this was hunted for hours. Fall back to the
    # original, which is always playable if it is present at all.
    if request.query_params.get("proxy") == "1" and m.get("proxy"):
        _px = P("proxies", os.path.basename(m["proxy"]))
        if os.path.exists(_px): path = _px
    if not os.path.exists(path):
        raise HTTPException(404, f"media file missing on this machine: {path} — use Relink to point at it")
    size = os.path.getsize(path); mt = mimetypes.guess_type(path)[0] or "application/octet-stream"
    rng = request.headers.get("range")
    if rng:
        s, e = rng.replace("bytes=", "").split("-"); start = int(s); end = int(e) if e else size - 1
        return StreamingResponse(_range_stream(path, start, end), status_code=206, media_type=mt,
                                 headers={"Content-Range": f"bytes {start}-{end}/{size}", "Accept-Ranges": "bytes", "Content-Length": str(end - start + 1)})
    return FileResponse(path, media_type=mt, headers={"Accept-Ranges": "bytes"})

@app.get("/api/events")
def events(since: float = 0, limit: int = 500):
    out = []
    if os.path.exists(PP("events.jsonl")):
        for line in open(PP("events.jsonl")):
            try: ev = json.loads(line)
            except Exception: continue
            if ev.get("ts", 0) > since: out.append(ev)
    return out[-limit:]

@app.post("/api/annotate")
async def annotate(req: Request):
    body = await req.json()  # {target:{sequence,track,clip_id}|{time}, label, note, actor}
    with LOCK:
        proj = load_project(); body["id"] = str(uuid.uuid4())[:8]; body["ts"] = time.time(); proj.setdefault("annotations", []).append(body); save_project(proj)
    ev = log_event({"type": "annotation", **body}); await broadcast(ev); return body

@app.post("/api/snapshot")
async def snapshot(req: Request):
    """Save a labeled snapshot of the project (e.g., label='agent_proposal' or 'human_final'). When a human_final follows an
    agent_proposal for the same sequence, a diff pair is appended to training/edit_pairs.jsonl."""
    body = await req.json(); label = body.get("label", "snapshot"); actor = body.get("actor", "human"); seq_id = body.get("sequence", "seq1")
    os.makedirs(PP("snapshots"), exist_ok=True); os.makedirs(PP("training"), exist_ok=True)
    proj = load_project(); sid = f"{int(time.time())}_{label}"; json.dump(proj, open(PP("snapshots", f"{sid}.json"), "w"))
    pair = None
    if label == "human_final":
        props = sorted([f for f in os.listdir(PP("snapshots")) if "agent_proposal" in f])
        if props:
            a = json.load(open(PP("snapshots", props[-1]))); pair = diff_sequences(a, proj, seq_id); pair.update({"agent_snapshot": props[-1], "human_snapshot": sid + ".json", "ts": time.time()})
            with open(PP("training", "edit_pairs.jsonl"), "a") as f: f.write(json.dumps(pair) + "\n")
    ev = log_event({"type": "snapshot", "label": label, "actor": actor, "snapshot": sid}); await broadcast(ev); return {"snapshot": sid, "pair": pair}

def diff_sequences(a, b, seq_id):
    def clips(p):
        seq = next((s for s in p["sequences"] if s["id"] == seq_id), None); out = {}
        if not seq: return out
        for t in seq["tracks"]:
            for c in t["clips"]: out[c["id"]] = {**c, "_track": t["id"]}
        return out
    ca, cb = clips(a), clips(b); changes = []
    for cid, c in ca.items():
        if cid not in cb: changes.append({"clip": cid, "change": "removed", "before": c})
        else:
            d = {k: {"before": c.get(k), "after": cb[cid].get(k)} for k in set(c) | set(cb[cid]) if c.get(k) != cb[cid].get(k)}
            if d: changes.append({"clip": cid, "change": "modified", "fields": d})
    for cid, c in cb.items():
        if cid not in ca: changes.append({"clip": cid, "change": "added", "after": c})
    def total(p):
        seq = next((s for s in p["sequences"] if s["id"] == seq_id), None)
        return max([c["start"] + (c["out"] - c["in_"]) / max(c.get("speed", 1), 1e-6) for t in seq["tracks"] for c in t["clips"]] + [0]) if seq else 0
    return {"sequence": seq_id, "changes": changes, "summary": {"n_changes": len(changes), "duration_before": total(a), "duration_after": total(b),
            "removed": sum(1 for c in changes if c["change"] == "removed"), "added": sum(1 for c in changes if c["change"] == "added"), "modified": sum(1 for c in changes if c["change"] == "modified")}}

import queue as _queue
RENDER_Q = _queue.Queue(); RENDER_WORKERS = []; RENDER_PROCS = {}
LAST_OPS_TS = {"t": 0.0}
def _autosave_loop():
    while True:
        time.sleep(60)
        try:
            mins = int(((json.load(open(P("settings.json"))).get("prefs") or {}).get("autosave_minutes", 5)) if os.path.exists(P("settings.json")) else 5)
            if mins <= 0 or not LAST_OPS_TS["t"]: continue
            d = PP("snapshots"); os.makedirs(d, exist_ok=True); last = sorted(f for f in os.listdir(d) if f.endswith("_autosave.json"))
            if not last or time.time() - float(last[-1].split("_")[0]) >= mins * 60:
                if time.time() - LAST_OPS_TS["t"] < mins * 60 * 2: json.dump(load_project(), open(os.path.join(d, f"{int(time.time())}_autosave.json"), "w"))
                for old in last[:-20]: os.remove(os.path.join(d, old))
        except Exception: pass
threading.Thread(target=_autosave_loop, daemon=True).start()

def render_workers():
    """Render queue: exports run sequentially by default (like a render queue), or N at a time via settings.prefs.render_workers."""
    try: n = int((json.load(open(P("settings.json"))).get("prefs") or {}).get("render_workers", 1)) if os.path.exists(P("settings.json")) else 1
    except Exception: n = 1
    n = max(1, min(4, n))
    while len(RENDER_WORKERS) < n:
        t = threading.Thread(target=_render_worker, daemon=True); t.start(); RENDER_WORKERS.append(t)

def _render_worker():
    while True:
        jid, proj, seq_id, preset, name, actor, out = RENDER_Q.get()
        if JOBS.get(jid, {}).get("status") == "error": RENDER_Q.task_done(); continue  # cancelled while queued
        JOBS[jid].update(status="running", started_run=time.time(), progress=0.0)
        try:
            holder = {"last": time.time()}; RENDER_PROCS[jid] = holder
            def _watch(h=holder, j=jid):
                while j in JOBS and JOBS[j]["status"] in ("running", "cancelling"):
                    time.sleep(30)
                    if time.time() - h.get("last", time.time()) > 900 and h.get("proc") and h["proc"].poll() is None: h["cancelled"] = True; JOBS[j]["error"] = "stalled: no progress for 15 minutes"; h["proc"].kill(); break
            threading.Thread(target=_watch, daemon=True).start()
            with open(P("renders", f"{name}.cmd.txt"), "w") as log:
                prog = lambda f, j=jid, h=holder: (JOBS[j].update(progress=round(f, 3)), h.update(last=time.time()))
                if preset.get("incremental", True) and preset.get("format", "h264") in ("h264", "hevc") and not preset.get("range"):
                    _, stats = render_incremental(proj, seq_id, out, preset, log, progress=prog, proc_holder=holder, cache_dir=P("renders", "cache")); JOBS[jid].update(stats)
                else: do_render(proj, seq_id, out, preset, log, progress=prog, proc_holder=holder)
            JOBS[jid].update(status="done", finished=time.time(), qa=render_qa(out, preset))
        except Exception as e: JOBS[jid].update(status="error", error=str(e)[-2000:])
        log_event({"type": "render", "actor": actor, "job": {k: v for k, v in JOBS[jid].items() if k != "preset"}})
        RENDER_Q.task_done()

def start_render(proj, seq_id, preset, name, actor):
    os.makedirs(P("renders"), exist_ok=True); fmt_ = preset.get("format", "h264"); ext_ = "mov" if (fmt_ in ("h264", "hevc") and preset.get("container") == "mov") else ("mp4" if fmt_ in ("h264", "hevc", "av1") else {"prores": "mov", "gif": "gif", "png_sequence": "png", "webm": "webm", "audio": preset.get("acodec", "wav")}.get(fmt_, "mp4")); out = P("renders", f"{name}.{ext_}"); jid = str(uuid.uuid4())[:8]
    JOBS[jid] = {"id": jid, "status": "queued", "out": "/renders/" + os.path.basename(out), "name": name, "started": time.time(), "preset": preset}
    render_workers(); RENDER_Q.put((jid, proj, seq_id, preset, name, actor, out)); return JOBS[jid]

PLATFORM_RULES = {  # delivery specs: max seconds, max MB, required aspect (w/h), notes — checked on every export against preset.platform
    "reels": {"max_s": 90, "max_mb": 4000, "aspect": (9, 16), "min_short_side": 1080},
    "tiktok": {"max_s": 600, "max_mb": 4000, "aspect": (9, 16), "min_short_side": 1080},
    "shorts": {"max_s": 60, "max_mb": 4000, "aspect": (9, 16), "min_short_side": 1080},
    "youtube": {"max_s": 43200, "max_mb": 256000, "aspect": (16, 9), "min_short_side": 1080},
    "linkedin": {"max_s": 600, "max_mb": 5000, "aspect": None, "min_short_side": 720},
    "x": {"max_s": 140, "max_mb": 512, "aspect": None, "min_short_side": 720},
    "facebook": {"max_s": 240 * 60, "max_mb": 10000, "aspect": None, "min_short_side": 720},
    "stories": {"max_s": 60, "max_mb": 4000, "aspect": (9, 16), "min_short_side": 1080}}

def render_qa(path, preset=None):
    """Post-render checks: spec via ffprobe, integrated loudness / true peak via ebur128, plus platform delivery rules when the preset
    names a platform (reels, tiktok, shorts, youtube, linkedin, x, facebook, stories). Returned on the job and logged."""
    qa = {}
    try:
        j = json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path], capture_output=True, text=True).stdout or "{}")
        v = next((x for x in j.get("streams", []) if x["codec_type"] == "video"), {}); a = next((x for x in j.get("streams", []) if x["codec_type"] == "audio"), {})
        qa.update(width=v.get("width"), height=v.get("height"), vcodec=v.get("codec_name"), acodec=a.get("codec_name"), duration=round(float(j.get("format", {}).get("duration", 0) or 0), 3), size_mb=round(os.path.getsize(path) / 1e6, 2))
        if a:
            r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-filter_complex", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True, timeout=600).stderr
            import re as _re; tail = r.split("Summary:")[-1]
            m = _re.search(r"I:\s+(-?[\d.]+) LUFS", tail); tp = _re.search(r"Peak:\s+(-?[\d.]+) dBFS", tail); lra = _re.search(r"LRA:\s+([\d.]+) LU", tail)
            if m: qa["integrated_lufs"] = float(m.group(1))
            if tp: qa["true_peak_dbtp"] = float(tp.group(1))
            if lra: qa["lra_lu"] = float(lra.group(1))
        flags = []
        if qa.get("integrated_lufs") is not None and not (-16.5 <= qa["integrated_lufs"] <= -12.5): flags.append(f"loudness {qa['integrated_lufs']} LUFS (target -14 ±1.5 for social)")
        if qa.get("true_peak_dbtp") is not None and qa["true_peak_dbtp"] > -1.0: flags.append(f"true peak {qa['true_peak_dbtp']} dBTP (> -1.0)")
        plat = (preset or {}).get("platform"); rules = PLATFORM_RULES.get(str(plat).lower()) if plat else None
        if rules:
            qa["platform"] = plat
            if qa.get("duration") and qa["duration"] > rules["max_s"]: flags.append(f"{plat}: {qa['duration']}s exceeds the {rules['max_s']}s limit")
            if qa.get("size_mb") and qa["size_mb"] > rules["max_mb"]: flags.append(f"{plat}: {qa['size_mb']} MB exceeds {rules['max_mb']} MB")
            if rules.get("aspect") and qa.get("width") and qa.get("height"):
                want = rules["aspect"][0] / rules["aspect"][1]; have = qa["width"] / qa["height"]
                if abs(want - have) > 0.02: flags.append(f"{plat}: aspect {qa['width']}x{qa['height']} is not {rules['aspect'][0]}:{rules['aspect'][1]}")
            if qa.get("width") and min(qa["width"], qa["height"]) < rules["min_short_side"]: flags.append(f"{plat}: resolution below {rules['min_short_side']}p")
            if qa.get("vcodec") and qa["vcodec"] not in ("h264", "hevc", "av1", "vp9"): flags.append(f"{plat}: codec {qa['vcodec']} may be rejected (use H.264)")
        qa["flags"] = flags
    except Exception as e: qa["error"] = str(e)[-300:]
    return qa

def user_fonts():
    """User-installed fonts (data/fonts/*.ttf|otf) → [{family, style, file, url}] using the font's own name table."""
    from PIL import ImageFont
    d = P("fonts"); out = []
    if not os.path.isdir(d): return out
    for fn in sorted(os.listdir(d)):
        if not fn.lower().endswith((".ttf", ".otf", ".ttc")): continue
        try: fam, sty = ImageFont.truetype(os.path.join(d, fn), 24).getname()
        except Exception: fam, sty = os.path.splitext(fn)[0], "Regular"
        out.append({"family": fam, "style": sty, "file": fn, "url": f"/fonts/{fn}", "path": os.path.join(d, fn)})
    return out

@app.get("/api/fonts")
def fonts():
    """Font families for the pickers: user fonts (data/fonts), bundled DejaVu, then system fonts via fontconfig when present."""
    users = user_fonts(); fams = []
    for u in users:
        if u["family"] not in fams: fams.append(u["family"])
    bundled = ["DejaVu Sans", "DejaVu Sans Mono"]
    sysf = []
    try:
        out = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True, timeout=10).stdout
        sysf = sorted({ln.split(",")[0].strip() for ln in out.splitlines() if ln.strip()})[:400]
    except Exception: pass
    return {"fonts": fams + [b for b in bundled if b not in fams] + [f for f in sysf if f not in fams and f not in bundled], "user": users}

@app.post("/api/fonts/upload")
async def fonts_upload(file: UploadFile = File(...)):
    """Add your own font (TTF/OTF). It becomes available in every text picker, in the monitor (via @font-face) and in the export."""
    d = P("fonts"); os.makedirs(d, exist_ok=True); name = os.path.basename(file.filename or "font.ttf")
    if not name.lower().endswith((".ttf", ".otf", ".ttc")): raise HTTPException(400, "TTF, OTF or TTC only")
    dest = os.path.join(d, name); open(dest, "wb").write(await file.read())
    try:
        from PIL import ImageFont; fam, sty = ImageFont.truetype(dest, 24).getname()
    except Exception as e: os.remove(dest); raise HTTPException(400, f"not a usable font: {e}")
    import render as _r; _r._FONT_MAP.clear()
    return {"family": fam, "style": sty, "file": name, "url": f"/fonts/{name}"}

@app.delete("/api/fonts/{file}")
def fonts_delete(file: str):
    f = P("fonts", os.path.basename(file))
    if os.path.exists(f): os.remove(f)
    import render as _r; _r._FONT_MAP.clear(); return {"ok": True}

@app.post("/api/media/relink")
async def media_relink(req: Request):
    body = await req.json()
    with LOCK:
        proj = load_project(); m = proj["media"].get(body["media_id"])
        if not m: raise HTTPException(404)
        if not os.path.exists(body["path"]): raise HTTPException(404, "file not found")
        mid, nm = await asyncio.to_thread(ingest, body["path"], m.get("name")); nm["id"] = m["id"]; proj["media"][m["id"]] = nm; save_project(proj)
    ev = log_event({"type": "media_added", "actor": body.get("actor", "human"), "media": [m["id"]], "relink": body["path"]}); await broadcast(ev); return nm

@app.get("/api/media/status")
def media_status():
    proj = load_project(); return {mid: os.path.exists(m["path"]) for mid, m in proj["media"].items()}

@app.post("/api/captions/auto")
async def captions_auto(req: Request):
    """Transcribe the sequence's audio to captions with faster-whisper or openai-whisper if installed on the workstation."""
    body = await req.json(); seq_id = body.get("sequence", "seq1"); model = body.get("model", "base")
    proj = load_project(); os.makedirs(P("renders"), exist_ok=True); wav = P("renders", f"_auto_{seq_id}.wav")
    cmd, graph = build_command(proj, seq_id, wav, {"format": "audio", "acodec": "wav"})
    r = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True)
    if r.returncode != 0: raise HTTPException(500, r.stderr[-800:])
    caps = []
    try:
        from faster_whisper import WhisperModel
        segs, _ = WhisperModel(model, compute_type="int8").transcribe(wav, vad_filter=True)
        caps = [{"id": str(uuid.uuid4())[:8], "start": float(sg.start), "end": float(sg.end), "text": sg.text.strip()} for sg in segs]
    except ImportError:
        try:
            import whisper
            res = whisper.load_model(model).transcribe(wav)
            caps = [{"id": str(uuid.uuid4())[:8], "start": float(sg["start"]), "end": float(sg["end"]), "text": sg["text"].strip()} for sg in res["segments"]]
        except ImportError:
            return JSONResponse({"error": "No transcriber installed. Run the installer with --with-whisper (or pip install faster-whisper in Filmocity/.venv), then retry."}, status_code=501)
    with LOCK:
        proj = load_project(); seq = next(s for s in proj["sequences"] if s["id"] == seq_id); seq["captions"] = caps; save_project(proj)
    ev = log_event({"type": "ops", "actor": body.get("actor", "human"), "tool": "captions_auto", "reason": f"{len(caps)} captions", "ops": []}); await broadcast(ev); return {"count": len(caps)}

# ---------- bundled assets: LUTs, graphics templates, presets ----------
@app.get("/api/luts")
def luts():
    out = []
    for d in (os.path.join(ASSETS, "luts"), P("luts")):
        if os.path.isdir(d): out += [{"name": os.path.splitext(f)[0].replace("_", " "), "path": os.path.join(d, f)} for f in sorted(os.listdir(d)) if f.lower().endswith(".cube")]
    return out

@app.get("/api/media/path")
def media_by_path(p: str):
    """Serve a file by path for graphics image layers — only paths that belong to project media, assets, or the data root."""
    ap = os.path.abspath(p); proj = load_project(); ok = any(os.path.abspath(m.get("path", "")) == ap for m in proj["media"].values()) or ap.startswith(os.path.abspath(ASSETS) + os.sep) or ap.startswith(os.path.abspath(ROOT) + os.sep)
    if not ok or not os.path.exists(ap): raise HTTPException(404)
    return FileResponse(ap)

@app.get("/api/luts/file")
def lut_file(path: str):
    from fastapi.responses import PlainTextResponse
    ap = os.path.abspath(path); allowed = [os.path.abspath(os.path.join(ASSETS, "luts")), os.path.abspath(os.path.join(ASSETS, "luts", "input")), os.path.abspath(P("luts"))]
    if not any(ap.startswith(a + os.sep) for a in allowed) or not ap.lower().endswith(".cube") or not os.path.exists(ap): raise HTTPException(404)
    return PlainTextResponse(open(ap, encoding="utf-8", errors="ignore").read())

@app.get("/api/templates/preview")
def template_preview(name: str, w: int = 270):
    """A rendered preview frame of a template (brand kit applied) — cached by template + brand + sequence size."""
    from render import render_frame
    import re as _re
    tpls = templates_list(); t = tpls.get(name)
    if not t: raise HTTPException(404)
    proj = load_project(); seq = proj["sequences"][0]; b = {"primary": "#E8631C", "secondary": "#7A2E9E", "text": "#FFFFFF", "font": "", **(proj.get("brand") or {})}
    def walk(v):
        if isinstance(v, str): return _re.sub(r"\{\{(primary|secondary|text|font)\}\}", lambda m: b.get(m.group(1), ""), v)
        if isinstance(v, list): return [walk(x) for x in v]
        if isinstance(v, dict): return {k: walk(x) for k, x in v.items() if not (k == "font" and walk(x) == "")}
        return v
    layers = walk(json.loads(json.dumps(t["layers"])))
    for L in layers: L.pop("anim_in", None); L.pop("anim_out", None)  # static preview: first frame only, so it renders in ~1 s
    key = hashlib.sha1(json.dumps([layers, seq["width"], seq["height"]], sort_keys=True).encode()).hexdigest()[:16]
    os.makedirs(P("thumbs", "tpl"), exist_ok=True); out = P("thumbs", "tpl", f"{key}.png")
    if not os.path.exists(out):
        mini = {"version": SCHEMA, "media": {}, "sequences": [{"id": "tp", "name": "tp", "width": seq["width"], "height": seq["height"], "fps": 30, "tracks": [{"id": "V1", "kind": "video", "index": 1, "clips": [{"id": "g", "media_id": None, "start": 0, "in_": 0, "out": 3, "speed": 1, "graphic": {"name": name, "layers": layers}, "transform": {"opacity": 1}, "keyframes": {}}]}], "captions": [], "markers": []}]}
        try: render_frame(mini, "tp", 0.05, out)
        except Exception as e: raise HTTPException(500, f"preview failed: {e}")
        try:
            from PIL import Image
            im = Image.open(out).convert("RGBA"); bg = Image.new("RGBA", im.size, (28, 28, 32, 255)); bg.alpha_composite(im); bg.convert("RGB").resize((w, int(w * im.height / im.width))).save(out)
        except Exception: pass
    return FileResponse(out, media_type="image/png")

def templates_list():
    return templates()

@app.get("/api/templates")
def templates():
    out = {}; d = os.path.join(ASSETS, "templates")
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.endswith(".json"):
                try: g = json.load(open(os.path.join(d, f))); out[g.get("name", f)] = g
                except Exception: pass
    p = P("settings.json")
    if os.path.exists(p):
        try: out.update(json.load(open(p)).get("graphics_templates") or {})
        except Exception: pass
    return out

@app.get("/api/presets/{kind}")
def presets(kind: str):
    f = os.path.join(ASSETS, "presets", f"{kind}.json"); return json.load(open(f)) if os.path.exists(f) else {}

@app.post("/api/undo")
async def undo(req: Request):
    """Project-wide undo: reverts the most recent op group by anyone (human or agent). Survives reloads; agent ops are undoable too."""
    body = await req.json() if req.headers.get("content-length") not in (None, "0") else {}
    with LOCK:
        st = json.load(open(undo_stack_path())) if os.path.exists(undo_stack_path()) else {"undo": [], "redo": []}
        if not st["undo"]: return {"ok": False, "reason": "nothing to undo"}
        entry = st["undo"].pop(); proj = load_project()
        try: inv = inverse_ops(entry["ops"], entry["befores"], proj); befores = apply_ops(proj, inv)
        except Exception as e: json.dump(st, open(undo_stack_path(), "w")); return JSONResponse({"ok": False, "reason": f"could not invert: {e}"}, status_code=409)
        st["redo"].append(entry); st["redo"] = st["redo"][-200:]; json.dump(st, open(undo_stack_path(), "w")); save_project(proj)
    ev = log_event({"type": "ops", "actor": body.get("actor", "human"), "tool": "undo", "reason": f"undo: {entry.get('reason') or ''} ({entry.get('actor')})", "ops": inv, "befores": befores, "client": body.get("client")}); await broadcast({k: v for k, v in ev.items() if k != "befores"}); return {"ok": True, "undone": entry.get("reason"), "by": entry.get("actor"), "remaining": len(st["undo"])}

@app.post("/api/redo")
async def redo(req: Request):
    body = await req.json() if req.headers.get("content-length") not in (None, "0") else {}
    with LOCK:
        st = json.load(open(undo_stack_path())) if os.path.exists(undo_stack_path()) else {"undo": [], "redo": []}
        if not st["redo"]: return {"ok": False, "reason": "nothing to redo"}
        entry = st["redo"].pop(); proj = load_project(); befores = apply_ops(proj, copy.deepcopy(entry["ops"])); normalize_tracks(proj); entry["befores"] = befores; st["undo"].append(entry); json.dump(st, open(undo_stack_path(), "w")); save_project(proj)
    ev = log_event({"type": "ops", "actor": body.get("actor", "human"), "tool": "redo", "reason": f"redo: {entry.get('reason') or ''}", "ops": entry["ops"], "befores": befores, "client": body.get("client")}); await broadcast({k: v for k, v in ev.items() if k != "befores"}); return {"ok": True, "redone": entry.get("reason")}

@app.get("/api/undo/stack")
def undo_stack():
    st = json.load(open(undo_stack_path())) if os.path.exists(undo_stack_path()) else {"undo": [], "redo": []}
    return {"undo": [{"reason": e.get("reason"), "actor": e.get("actor"), "ts": e.get("ts"), "n": len(e.get("ops", []))} for e in st["undo"][-50:]], "redo": [{"reason": e.get("reason"), "actor": e.get("actor")} for e in st["redo"][-50:]]}

@app.get("/api/version")
def version(): return {"version": VERSION, "schema": SCHEMA}

# ---------- effects catalog + stabilization analysis ----------
@app.get("/api/effects")
def effects_list(): return effects_catalog()

@app.post("/api/stabilize")
async def stabilize(req: Request):
    """Warp Stabilizer analysis pass (vidstabdetect) for a media file → data_root/stab/<id>.trf, stored on the media entry."""
    body = await req.json(); proj = load_project(); m = proj["media"].get(body["media_id"])
    if not m or not m.get("has_video"): raise HTTPException(404)
    src = proj["media"][m["subclip_of"]] if m.get("subclip_of") else m
    os.makedirs(P("stab"), exist_ok=True); trf = P("stab", f"{src['id']}.trf")
    if not os.path.exists(trf) or body.get("force"):
        r = await asyncio.to_thread(subprocess.run, ["ffmpeg", "-hide_banner", "-nostats", "-y", "-i", src["path"], "-vf", f"vidstabdetect=shakiness={int(body.get('shakiness', 5))}:accuracy=15:result='{trf.replace(chr(92), '/').replace(':', chr(92) + ':')}'", "-f", "null", "-"], capture_output=True, text=True, timeout=3600)
        if r.returncode != 0 or not os.path.exists(trf): raise HTTPException(500, "vidstab analysis failed — this FFmpeg build may lack libvidstab. " + r.stderr[-300:])
    with LOCK:
        proj = load_project()
        for mid, mm in proj["media"].items():
            if mid == src["id"] or mm.get("subclip_of") == src["id"]: mm["stab_trf"] = trf
        save_project(proj)
    ev = log_event({"type": "media_added", "actor": body.get("actor", "human"), "media": [src["id"]], "stabilized": True}); await broadcast(ev); return {"trf": trf}

# ---------- projects ----------
@app.get("/api/projects")
def projects_list():
    out = []
    for pid in sorted(os.listdir(P("projects"))) if os.path.isdir(P("projects")) else []:
        pj = P("projects", pid, "project.json")
        if os.path.exists(pj):
            try: j = json.load(open(pj)); out.append({"id": pid, "name": j.get("name", pid), "updated": j.get("updated"), "sequences": len(j.get("sequences", [])), "media": len(j.get("media", {})), "active": pid == active_id()})
            except Exception: pass
    return out

async def _switch(pid, actor):
    json.dump({"id": pid}, open(P("active.json"), "w")); proj = load_project()
    ev = log_event({"type": "project_replaced", "actor": actor, "source": "open_project", "project": pid}); await broadcast(ev); return proj

@app.post("/api/projects/new")
async def projects_new(req: Request):
    body = await req.json(); name = body.get("name") or "Untitled"; pid = "".join(ch for ch in name.lower().replace(" ", "_") if ch.isalnum() or ch in "_-")[:32] or "project"; pid = pid + "_" + str(uuid.uuid4())[:4]
    os.makedirs(P("projects", pid), exist_ok=True); proj = default_project(); proj["name"] = name
    if body.get("copy_media"): proj["media"] = load_project().get("media", {})
    json.dump(proj, open(P("projects", pid, "project.json"), "w"), indent=1); await _switch(pid, body.get("actor", "human")); return {"id": pid, "name": name}

@app.post("/api/projects/open")
async def projects_open(req: Request):
    body = await req.json(); pid = os.path.basename(body["id"])
    if not os.path.exists(P("projects", pid, "project.json")): raise HTTPException(404)
    await _switch(pid, body.get("actor", "human")); return {"id": pid}

@app.post("/api/projects/save_as")
async def projects_save_as(req: Request):
    body = await req.json(); name = body.get("name") or "Copy"; pid = "".join(ch for ch in name.lower().replace(" ", "_") if ch.isalnum() or ch in "_-")[:32] + "_" + str(uuid.uuid4())[:4]
    src = P("projects", active_id()); dst = P("projects", pid); shutil.copytree(src, dst, ignore=shutil.ignore_patterns("*.tmp")); j = json.load(open(os.path.join(dst, "project.json"))); j["name"] = name; json.dump(j, open(os.path.join(dst, "project.json"), "w"), indent=1)
    await _switch(pid, body.get("actor", "human")); return {"id": pid, "name": name}

@app.post("/api/projects/collect")
async def projects_collect(req: Request):
    """Project Manager: copy every media file into data_root/collected/<project>/ and relink."""
    body = await req.json(); pid = active_id(); dest = P("collected", pid); os.makedirs(dest, exist_ok=True); copied = 0
    with LOCK:
        proj = load_project()
        for m in proj["media"].values():
            if m.get("subclip_of") or not os.path.exists(m["path"]): continue
            target = os.path.join(dest, os.path.basename(m["path"]))
            if os.path.abspath(target) != os.path.abspath(m["path"]):
                if not os.path.exists(target): shutil.copy2(m["path"], target); copied += 1
                m["path"] = os.path.abspath(target)
        save_project(proj)
    ev = log_event({"type": "project_replaced", "actor": body.get("actor", "human"), "source": "collect", "copied": copied}); await broadcast(ev); return {"dest": dest, "copied": copied}

@app.post("/api/media/scenes")
async def media_scenes(req: Request):
    """Scene edit detection: cut points of a media file (FFmpeg scene score). {media_id, threshold, in, out}"""
    body = await req.json(); proj = load_project(); m = proj["media"].get(body["media_id"])
    if not m or not m.get("has_video"): raise HTTPException(404)
    src = proj["media"][m["subclip_of"]] if m.get("subclip_of") else m; off = float(m.get("sub_in", 0) or 0)
    i, o = float(body.get("in", 0)) + off, float(body.get("out", m["duration"])) + off; th = float(body.get("threshold", 0.35))
    r = (await asyncio.to_thread(subprocess.run, ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{i:.3f}", "-t", f"{max(o - i, 0.1):.3f}", "-i", src["path"], "-vf", f"scale=320:-2,select='gt(scene,{th})',showinfo", "-an", "-f", "null", "-"], capture_output=True, text=True, timeout=900)).stderr
    import re as _re; times = sorted({round(float(t), 3) for t in _re.findall(r"pts_time:([\d.]+)", r)})
    return {"cuts": [t for t in times if 0.2 < t < (o - i) - 0.2], "threshold": th}

@app.post("/api/transcript")
async def transcript(req: Request):
    """Word-level transcript of the sequence audio (faster-whisper with word timestamps) → sequence.transcript = [{w, s, e, p}] and captions."""
    body = await req.json(); seq_id = body.get("sequence", "seq1"); model = body.get("model", "base")
    proj = load_project(); os.makedirs(P("renders"), exist_ok=True); wav = P("renders", f"_tr_{seq_id}.wav")
    cmd, graph = build_command(proj, seq_id, wav, {"format": "audio", "acodec": "wav"})
    r = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True)
    if r.returncode != 0: raise HTTPException(500, r.stderr[-800:])
    words, caps = [], []
    try:
        from faster_whisper import WhisperModel
        segs, _ = WhisperModel(model, compute_type="int8").transcribe(wav, vad_filter=True, word_timestamps=True)
        for sg in segs:
            caps.append({"id": str(uuid.uuid4())[:8], "start": float(sg.start), "end": float(sg.end), "text": sg.text.strip()})
            for w in (sg.words or []): words.append({"w": w.word.strip(), "s": float(w.start), "e": float(w.end), "p": float(getattr(w, "probability", 1.0))})
    except ImportError:
        return JSONResponse({"error": "faster-whisper is not installed. Run the installer with --with-whisper (or pip install faster-whisper in Filmocity/.venv)."}, status_code=501)
    with LOCK:
        proj = load_project(); seq = next(x for x in proj["sequences"] if x["id"] == seq_id); seq["transcript"] = words
        if body.get("captions", True): seq["captions"] = caps
        save_project(proj)
    ev = log_event({"type": "ops", "actor": body.get("actor", "human"), "tool": "transcript", "reason": f"{len(words)} words", "ops": []}); await broadcast(ev); return {"words": len(words), "captions": len(caps)}

# ---------- feed-forward advisor: learn preferences from decisions, score proposals ----------
ADVISOR = {"model": None, "rules": {}, "trained": 0}
def advisor_dirs(): return [P("projects", d) for d in (os.listdir(P("projects")) if os.path.isdir(P("projects")) else [])]
def advisor_load():
    p = P("advisor_model.json")
    if os.path.exists(p) and ADVISOR["model"] is None:
        try: j = json.load(open(p)); ADVISOR["model"] = _advisor.Model.from_json(j); ADVISOR["rules"] = j.get("rules", {}); ADVISOR["trained"] = j.get("trained", 0)
        except Exception: pass

@app.post("/api/advisor/train")
async def advisor_train(req: Request):
    """Retrain from every project's proposal decisions. Returns example count and mined rules."""
    rows, rules = _advisor.load_examples(advisor_dirs()); m = _advisor.Model().train(rows)
    ADVISOR.update(model=m, rules={k: v for k, v in rules.items()}, trained=time.time())
    json.dump({**m.to_json(), "rules": ADVISOR["rules"], "trained": ADVISOR["trained"]}, open(P("advisor_model.json"), "w"))
    return {"examples": len(rows), "rules": _advisor.report(rules)[:40], "trained": ADVISOR["trained"]}

@app.get("/api/advisor/report")
def advisor_report():
    advisor_load(); rows, rules = _advisor.load_examples(advisor_dirs())
    return {"examples": len(rows), "rules": _advisor.report(rules)[:40], "model_trained": ADVISOR["trained"], "model_examples": ADVISOR["model"].n if ADVISOR["model"] else 0}

@app.post("/api/advisor/score")
async def advisor_score(req: Request):
    """Score proposed ops before proposing: {sequence, ops:[…], reason} → per-op acceptance probability + matching rules."""
    body = await req.json(); advisor_load(); proj = load_project(); seq = next((s for s in proj["sequences"] if s["id"] == body.get("sequence", "seq1")), None)
    sd = max([c["start"] + (c["out"] - c["in_"]) / max(c.get("speed", 1), 1e-6) for t in seq["tracks"] for c in t["clips"]] + [0]) if seq else 0
    out = []
    for op in body.get("ops", []):
        tr = next((t for t in (seq["tracks"] if seq else []) if t["id"] == op.get("track")), None); cur = None
        if tr: cid = op.get("clip", {}).get("id") if op.get("op") == "set_clip" else op.get("clip_id"); cur = next((c for c in tr["clips"] if c["id"] == cid), None)
        f = _advisor.op_features(op, {"clip": cur or {}, "seq_duration": sd, "reason": body.get("reason"), "brief": proj.get("brief"), "track_kind": tr["kind"] if tr else None})
        p = ADVISOR["model"].predict(f) if ADVISOR["model"] and ADVISOR["model"].n else None
        key = "op:" + op.get("op", "?") + ("|" + ",".join(sorted(k for k in (op.get("clip") or {}) if k != "id"))[:60] if op.get("op") == "set_clip" else "")
        rule = ADVISOR["rules"].get(key)
        out.append({"p_accept": None if p is None else round(p, 3), "history": {"accepted": rule[0], "rejected": rule[1]} if rule else None, "confidence": "low" if not ADVISOR["model"] or ADVISOR["model"].n < 20 else "medium" if ADVISOR["model"].n < 100 else "high"})
    return {"scores": out, "model_examples": ADVISOR["model"].n if ADVISOR["model"] else 0}

# ---------- metadata (ffprobe + editable fields), nested bins, timecode ----------
@app.get("/api/media/probe")
def media_probe(media_id: str):
    proj = load_project(); m = proj["media"].get(media_id)
    if not m: raise HTTPException(404)
    if m.get("synthetic"): return {"media": m, "probe": {"format": {"format_name": "generator"}, "streams": []}}
    src = proj["media"][m["subclip_of"]] if m.get("subclip_of") else m
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", src["path"]], capture_output=True, text=True).stdout
    return {"media": m, "probe": json.loads(out or "{}")}

# ---------- diagnostics + sample project + backups ----------
PREVIEW_PRESET = {"crf": 20, "x264_preset": "veryfast", "vcodec": "libx264"}
@app.get("/api/render/segments")
def render_segments(sequence: str = "seq1"):
    """Render bar: the sequence's export segments and whether each is already cached for the preview preset (green) or not (red)."""
    proj = load_project(); seq = next((s for s in proj["sequences"] if s["id"] == sequence), None)
    if not seq: raise HTTPException(404)
    pts = segment_boundaries(seq); cache = P("renders", "cache"); out = []
    for t0, t1 in zip(pts, pts[1:]):
        key = chunk_key(proj, chunk_sequence(seq, t0, t1), PREVIEW_PRESET); out.append({"t0": t0, "t1": t1, "cached": os.path.exists(os.path.join(cache, key + ".mp4"))})
    return {"segments": out, "preview": f"/renders/preview_{sequence}.mp4" if os.path.exists(P("renders", f"preview_{sequence}.mp4")) else None}

@app.post("/api/render/{jid}/cancel")
async def render_cancel(jid: str):
    j = JOBS.get(jid)
    if not j: raise HTTPException(404)
    if j["status"] == "queued": j.update(status="error", error="cancelled"); return {"ok": True}
    h = RENDER_PROCS.get(jid)
    if h and h.get("proc") and h["proc"].poll() is None:
        h["cancelled"] = True; j["status"] = "cancelling"; h["proc"].terminate()
        def _kill(p=h["proc"]):
            time.sleep(2)
            if p.poll() is None: p.kill()
        threading.Thread(target=_kill, daemon=True).start(); return {"ok": True}
    return {"ok": False}

@app.get("/api/diagnostics")
def diagnostics():
    """System check for first launch: FFmpeg build details, needed filters/encoders, Python, fonts, whisper, disk, data root."""
    import platform, shutil as _sh
    out = {"python": platform.python_version(), "platform": f"{platform.system()} {platform.release()} ({platform.machine()})", "data_root": ROOT, "ffmpeg": None, "ffprobe": _sh.which("ffprobe") is not None, "filters": {}, "encoders": [], "fonts_bundled": os.path.isdir(ASSETS + "/fonts"), "fontconfig": _sh.which("fc-list") is not None, "whisper": False, "disk_free_gb": None, "issues": []}
    try:
        v = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=10).stdout; out["ffmpeg"] = v.splitlines()[0] if v else None
        flt = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, timeout=10).stdout
        for name in ("drawtext", "vidstabtransform", "libvidstab", "loudnorm", "chromakey", "minterpolate", "lut3d", "superequalizer", "afftdn", "xfade", "gblur", "geq", "curves", "colorbalance"):
            out["filters"][name] = (f" {name} " in flt or f" {name}\n" in flt)
        enc = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=10).stdout
        out["encoders"] = [e for e in ("libx264", "libx265", "h264_nvenc", "hevc_nvenc", "h264_qsv", "hevc_qsv", "h264_amf", "hevc_amf", "h264_videotoolbox", "hevc_videotoolbox", "h264_vaapi", "prores_ks", "libvpx-vp9", "libmp3lame", "aac") if f" {e} " in enc]
    except Exception as e: out["issues"].append(f"ffmpeg not runnable: {e}")
    try:
        import faster_whisper  # noqa
        out["whisper"] = True
    except ImportError: pass
    try: out["disk_free_gb"] = round(_sh.disk_usage(ROOT).free / 1e9, 1)
    except Exception: pass
    if not out["ffmpeg"]: out["issues"].append("FFmpeg not found — run the installer or put ffmpeg/ffprobe in Filmocity/bin")
    for need in ("drawtext", "loudnorm", "chromakey", "lut3d", "gblur"):
        if out["filters"].get(need) is False: out["issues"].append(f"FFmpeg build lacks the {need} filter")
    if out["filters"].get("vidstabtransform") is False: out["issues"].append("no libvidstab: Warp Stabilizer unavailable (other features fine)")
    if not out["whisper"]: out["issues"].append("faster-whisper not installed: auto-captions/transcript unavailable (installer --with-whisper)")
    if out["disk_free_gb"] is not None and out["disk_free_gb"] < 5: out["issues"].append(f"low disk: {out['disk_free_gb']} GB free")
    return out

@app.post("/api/projects/sample")
async def projects_sample(req: Request):
    """Create a sample project with generated footage (no downloads): three shots, a music bed, a still — cut with captions, a lower third, ducking and a look."""
    body = await req.json(); d = P("sample_media"); os.makedirs(d, exist_ok=True)
    gens = [("shot_01_open.mp4", "testsrc2=s=1280x720:r=30", 330), ("shot_02_product.mp4", "smptehdbars=s=1280x720:r=30", 440), ("shot_03_ride.mp4", "rgbtestsrc=s=1280x720:r=30", 550)]
    for name, src, hz in gens:
        f = os.path.join(d, name)
        if not os.path.exists(f): await asyncio.to_thread(subprocess.run, ["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", src, "-f", "lavfi", "-i", f"sine=frequency={hz}:sample_rate=48000", "-t", "8", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", f], capture_output=True, timeout=300)
    mus = os.path.join(d, "music_bed.m4a")
    if not os.path.exists(mus): await asyncio.to_thread(subprocess.run, ["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", "sine=frequency=110:sample_rate=48000,tremolo=f=2:d=0.9", "-t", "30", "-c:a", "aac", mus], capture_output=True, timeout=120)
    still = os.path.join(d, "end_card.png")
    if not os.path.exists(still): await asyncio.to_thread(subprocess.run, ["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", "color=c=0xE8631C:s=1080x1920", "-frames:v", "1", "-update", "1", still], capture_output=True, timeout=60)
    name = body.get("name", "Sample — Filmocity tour"); pid = "sample_" + str(uuid.uuid4())[:4]; os.makedirs(P("projects", pid), exist_ok=True)
    proj = default_project(); proj["name"] = name; proj["brief"] = {"client": "Sample Co", "objective": "Show every panel with real content", "platform": "reels", "audience": "you, on first launch", "constraints": "none — this is a tour", "notes": "generated footage; delete this project any time"}
    json.dump(proj, open(P("projects", pid, "project.json"), "w"), indent=1); json.dump({"id": pid}, open(P("active.json"), "w"))
    ids = {}
    with LOCK:
        proj = load_project()
        for f in [os.path.join(d, g[0]) for g in gens] + [mus, still]:
            mid, m = ingest(f, os.path.basename(f)); proj["media"][mid] = m; ids[os.path.basename(f)] = mid
        seq = proj["sequences"][0]; seq["name"] = "Tour 9x16"; v1 = next(t for t in seq["tracks"] if t["id"] == "V1"); v2 = next(t for t in seq["tracks"] if t["id"] == "V2"); a2 = next(t for t in seq["tracks"] if t["id"] == "A2")
        v1["clips"] = [{"id": "s1", "media_id": ids["shot_01_open.mp4"], "start": 0, "in_": 1.0, "out": 4.0, "speed": 1, "transform": {"x": 0, "y": 0, "scale": 1, "rotation": 0, "opacity": 1}, "audio": {"gain_db": 0, "linked": True}, "keyframes": {"transform.scale": [{"t": 0, "v": 1.0}, {"t": 2.8, "v": 1.1, "e": "ease"}]}, "color": {"lut": os.path.join(ASSETS, "luts", "Teal_Orange.cube")}, "note": "the hook — slow push-in, warm look", "fit": "cover"},
                       {"id": "s2", "media_id": ids["shot_02_product.mp4"], "start": 3.0, "in_": 0.0, "out": 2.5, "speed": 1, "transition_in": {"type": "dissolve", "duration": 0.6}, "transform": {"x": 0, "y": 0, "scale": 1, "rotation": 0, "opacity": 1}, "audio": {"gain_db": 0, "linked": True}, "keyframes": {}, "color": {}, "note": "product beat", "fit": "cover"},
                       {"id": "s3", "media_id": ids["shot_03_ride.mp4"], "start": 5.5, "in_": 2.0, "out": 5.0, "speed": 1, "transition_in": {"type": "wipe_left", "duration": 0.5}, "transform": {"x": 0, "y": 0, "scale": 1, "rotation": 0, "opacity": 1}, "audio": {"gain_db": 0, "linked": True}, "keyframes": {}, "color": {}, "fx_stack": [{"id": "fx1", "type": "vignette_fx", "enabled": True, "params": {"amount": 0.5}}], "note": "ride beat with a vignette", "fit": "cover"},
                       {"id": "s4", "media_id": ids["end_card.png"], "start": 8.5, "in_": 0, "out": 3.0, "speed": 1, "transition_in": {"type": "dip_black", "duration": 0.5}, "transform": {"x": 0, "y": 0, "scale": 1, "rotation": 0, "opacity": 1}, "keyframes": {}, "color": {}, "note": "end card"}]
        v2["clips"] = [{"id": "lt", "media_id": None, "start": 0.4, "in_": 0, "out": 3.0, "speed": 1, "graphic": {"name": "Lower third", "layers": [{"kind": "box", "x": 0.06, "y": 0.72, "w": 0.55, "h": 0.008, "color": "0xE8631C@1.0"}, {"kind": "text", "text": "Jordan Reyes", "size": 70, "align": "left", "valign": "bottom", "y": -40, "color": "white", "shadow": True}, {"kind": "text", "text": "Reviewer · @jr.rides", "size": 38, "align": "left", "valign": "bottom", "y": 40, "color": "0xDDDDDD", "weight": "regular"}]}, "transform": {"opacity": 1}, "transition_in": {"type": "push_left", "duration": 0.35}, "transition_out": {"type": "fade", "duration": 0.3}, "keyframes": {}},
                       {"id": "url", "media_id": None, "start": 8.7, "in_": 0, "out": 2.8, "speed": 1, "title": {"text": "sampleco.com", "size": 88, "color": "white", "valign": "bottom", "y": -160}, "transform": {"opacity": 1}, "transition_in": {"type": "fade", "duration": 0.3}, "keyframes": {}}]
        a2["clips"] = [{"id": "mus", "media_id": ids["music_bed.m4a"], "start": 0, "in_": 0, "out": 11.5, "speed": 1, "audio": {"gain_db": -8, "linked": True, "fade_out": 1.0}, "keyframes": {"audio.gain_db": [{"t": 0, "v": -8}, {"t": 0.8, "v": -18, "e": "ease"}, {"t": 8.0, "v": -18}, {"t": 8.6, "v": -8, "e": "ease"}]}, "note": "music bed, ducked under the shots"}]
        seq["captions"] = [{"id": "c1", "start": 0.3, "end": 2.6, "text": "Built for the rider other e-bikes weren't built for."}, {"id": "c2", "start": 3.2, "end": 5.2, "text": "450 lb payload. Two riders. All day."}, {"id": "c3", "start": 5.8, "end": 8.2, "text": "Full suspension, both ends."}]
        seq["markers"] = [{"id": "m1", "time": 0.0, "name": "Hook", "type": "chapter", "color": "green"}, {"id": "m2", "time": 3.0, "name": "Product", "type": "chapter", "color": "blue"}, {"id": "m3", "time": 8.5, "name": "End card", "type": "chapter", "color": "orange"}]
        save_project(proj)
    ev = log_event({"type": "project_replaced", "actor": "human", "source": "sample_project", "project": pid}); await broadcast(ev); return {"id": pid, "name": name}

@app.get("/api/backups")
def backups_list():
    d = PP("backups"); return [{"file": f, "ts": float(f.split("_")[1].split(".")[0])} for f in sorted(os.listdir(d), reverse=True)] if os.path.isdir(d) else []

@app.post("/api/backups/restore")
async def backups_restore(req: Request):
    body = await req.json(); f = PP("backups", os.path.basename(body["file"]))
    if not os.path.exists(f): raise HTTPException(404)
    with LOCK: proj = json.load(open(f)); save_project(proj)
    ev = log_event({"type": "project_replaced", "actor": body.get("actor", "human"), "source": "backup_restore", "file": body["file"]}); await broadcast(ev); return {"ok": True}

@app.post("/api/media/breakout")
async def media_breakout(req: Request):
    """Breakout to Mono: two audio-only bin items (Left, Right) from a stereo clip, each mapped to one channel."""
    body = await req.json(); out = []
    with LOCK:
        proj = load_project(); m = proj["media"].get(body["media_id"])
        if not m or not m.get("has_audio"): raise HTTPException(404)
        for side in ("left", "right"):
            nid = str(uuid.uuid4())[:8]; proj["media"][nid] = {**m, "id": nid, "name": f"{os.path.splitext(m['name'])[0]} {side.capitalize()}.wav", "has_video": False, "thumb": None, "strip": None, "channel_mode": side, "breakout_of": m["id"], "added": time.time()}; out.append(nid)
        save_project(proj)
    ev = log_event({"type": "media_added", "actor": body.get("actor", "human"), "media": out, "breakout": True}); await broadcast(ev); return {"media": out}

@app.post("/api/render/preview")
async def render_preview(req: Request):
    """Render Entire Sequence (Premiere's Enter): fills the segment cache and writes renders/preview_<seq>.mp4 for exact playback in the monitor."""
    body = await req.json(); seq_id = body.get("sequence", "seq1"); proj = copy.deepcopy(load_project())
    return start_render(proj, seq_id, dict(PREVIEW_PRESET, incremental=True, loudnorm=False), f"preview_{seq_id}", body.get("actor", "human"))

@app.post("/api/render_all")
async def render_all(req: Request):
    """Export every sequence in the project with one preset (batch)."""
    body = await req.json(); preset = body.get("preset", {}); proj = copy.deepcopy(load_project()); jobs = []
    for sq in proj["sequences"]:
        if sq.get("multicam") or sq.get("merged"): continue
        name = "".join(ch for ch in sq["name"] if ch.isalnum() or ch in "-_ ").strip().replace(" ", "_") or sq["id"]; jobs.append(start_render(proj, sq["id"], preset, f"{body.get('prefix', 'batch')}_{name}", body.get("actor", "human")))
    return {"jobs": jobs}

# ---------- interpret footage / extract audio ----------
@app.post("/api/media/interpret")
async def media_interpret(req: Request):
    """Interpret Footage: assume a frame rate for a media file (e.g. 60 → 30 for 2× slow motion). {media_id, fps|null}"""
    body = await req.json()
    with LOCK:
        proj = load_project(); m = proj["media"].get(body["media_id"])
        if not m: raise HTTPException(404)
        fps = body.get("fps"); native = float(m.get("native_fps") or m.get("fps") or 0)
        if not native: raise HTTPException(400, "media has no frame rate")
        m["native_fps"] = native
        if fps: m["interpret_fps"] = float(fps); m["duration"] = float(m.get("native_duration") or m["duration"]) * native / float(fps); m.setdefault("native_duration", float(m.get("native_duration") or m["duration"] * float(fps) / native))
        else: m.pop("interpret_fps", None); m["duration"] = float(m.get("native_duration") or m["duration"])
        save_project(proj)
    ev = log_event({"type": "media_added", "actor": body.get("actor", "human"), "media": [m["id"]], "interpret_fps": fps}); await broadcast(ev); return m

def fit_hook_text(txt, size, W):
    """Wrap a headline into two balanced lines and shrink it so the longest line fits ~90% of the frame width."""
    txt = str(txt).strip(); words = txt.split()
    if len(words) > 1 and len(txt) > 9:
        best = min(range(1, len(words)), key=lambda i: abs(len(" ".join(words[:i])) - len(" ".join(words[i:])))); lines = [" ".join(words[:best]), " ".join(words[best:])]
    else: lines = [txt]
    longest = max(len(l) for l in lines); est = longest * size * 0.62
    if est > W * 0.9: size = int(size * W * 0.9 / est)
    return "\n".join(lines), size

@app.post("/api/markers/note")
async def markers_note(req: Request):
    """Review note at a time: {sequence, time, text, author, duration?, color?} → a marker of type 'note' the human can resolve in the Markers panel."""
    body = await req.json(); seq_id = body.get("sequence", "seq1")
    with LOCK:
        proj = load_project(); seq = next(x for x in proj["sequences"] if x["id"] == seq_id); mk = {"id": uuid.uuid4().hex[:6], "time": float(body.get("time", 0)), "duration": float(body.get("duration", 0) or 0), "name": str(body.get("text", ""))[:300], "type": "note", "color": body.get("color", "yellow"), "author": body.get("author", "agent"), "resolved": False, "ts": time.time()}
        seq["markers"] = (seq.get("markers") or []) + [mk]; save_project(proj); si = proj["sequences"].index(seq)
    ev = log_event({"type": "ops", "actor": body.get("author", "agent"), "tool": "note", "reason": f"note: {mk['name'][:60]}", "ops": [{"op": "set", "path": f"/sequences/{si}/markers", "value": seq["markers"]}]}); await broadcast({k: v for k, v in ev.items() if k != "befores"}); return mk

@app.post("/api/sequences/variants")
async def sequence_variants(req: Request):
    """Hook variants for ad testing: {sequence, hooks:[text…], layer_match?: 'hook'} → N duplicate sequences, each with the hook card's
    headline replaced (any graphic layer whose text equals the current hook, or the first text layer of a graphic named 'Hook')."""
    body = await req.json(); seq_id = body.get("sequence", "seq1"); hooks = [h for h in body.get("hooks", []) if str(h).strip()]
    if not hooks: raise HTTPException(400, "give at least one hook text")
    with LOCK:
        proj = load_project(); base = next(x for x in proj["sequences"] if x["id"] == seq_id); made = []
        for i, h in enumerate(hooks):
            sq = copy.deepcopy(base); sq["id"] = f"{seq_id}_v{i + 1}_{uuid.uuid4().hex[:4]}"; sq["name"] = f"{base['name']} — V{i + 1}: {h[:28]}"; sq["variant_of"] = seq_id; sq["variant_hook"] = h; replaced = 0
            for t in sq["tracks"]:
                for c in t["clips"]:
                    g = c.get("graphic")
                    if g and (g.get("name", "").lower().startswith("hook") or c.get("note", "").lower().startswith("hook")):
                        for L in g["layers"]:
                            if L.get("kind") == "text": L["text"], L["size"] = fit_hook_text(h, int(L.get("size", 150)), sq["width"]); replaced += 1; break
                    elif c.get("title") and c.get("note", "").lower().startswith("hook"): c["title"]["text"] = h; replaced += 1
            if not replaced:  # fall back: the first text layer in the first graphic
                for t in sq["tracks"]:
                    for c in t["clips"]:
                        if c.get("graphic"):
                            for L in c["graphic"]["layers"]:
                                if L.get("kind") == "text": L["text"] = h; replaced = 1; break
                        if replaced: break
                    if replaced: break
            sq["captions"] = [dict(cp, text=h) if cp.get("text") == base.get("variant_hook") else cp for cp in (sq.get("captions") or [])]
            proj["sequences"].append(sq); made.append({"id": sq["id"], "name": sq["name"], "replaced": replaced})
        save_project(proj)
    ev = log_event({"type": "project_replaced", "actor": body.get("actor", "human"), "source": "variants", "made": made}); await broadcast(ev); return {"variants": made}

@app.get("/api/sequence/describe")
def sequence_describe(sequence: str = "seq1"):
    """A human-readable account of the cut: sections (from duration markers), every clip with timing, what it is, notes and animations,
    captions, audio beds, and QA-relevant totals. Feeds the Info panel and gives agents a whole-cut summary in one call."""
    proj = load_project(); seq = next((x for x in proj["sequences"] if x["id"] == sequence), None)
    if not seq: raise HTTPException(404)
    media = proj["media"]; total = max([c["start"] + (c["out"] - c["in_"]) / max(c.get("speed", 1), 1e-6) for t in seq["tracks"] for c in t["clips"]] + [0]); lines = [f"# {seq['name']} — {total:.2f}s, {seq['width']}x{seq['height']} @ {seq['fps']:g}"]
    secs = [m for m in (seq.get("markers") or []) if m.get("duration")]
    if secs: lines.append("Sections: " + "; ".join(f"{m['name']} {m['time']:.2f}–{m['time'] + m['duration']:.2f}s ({m['duration']:.2f}s)" for m in sorted(secs, key=lambda m: m["time"])))
    for t in sorted(seq["tracks"], key=lambda t: (t["kind"] != "video", -t["index"] if t["kind"] == "video" else t["index"])):
        if not t["clips"]: continue
        lines.append(f"## {t['id']} ({t['kind']})")
        for c in sorted(t["clips"], key=lambda c: c["start"]):
            d = (c["out"] - c["in_"]) / max(c.get("speed", 1), 1e-6); m = media.get(c.get("media_id") or "")
            what = m["name"] if m else ("text: " + (c["title"].get("text") or "")[:40] if c.get("title") else f"graphic: {c['graphic'].get('name', '')} ({len(c['graphic'].get('layers', []))} layers)" if c.get("graphic") else "nested" if c.get("sequence_id") else "adjustment" if c.get("adjustment") else "?")
            extras = []
            if c.get("transition_in"): extras.append(f"in: {c['transition_in']['type']} {c['transition_in'].get('duration', 0):.2f}s")
            if c.get("speed") not in (None, 1, 1.0): extras.append(f"speed {c['speed']:g}x")
            if (c.get("color") or {}).get("lut"): extras.append("look " + os.path.basename(c["color"]["lut"]))
            if c.get("fx_stack"): extras.append("fx " + ",".join(f["type"] for f in c["fx_stack"] if f.get("enabled") is not False))
            if c.get("keyframes"): extras.append("keyframed " + ",".join(k.split(".")[-1] for k in c["keyframes"]))
            if c.get("graphic"): extras.append("anim " + ",".join(sorted({(L.get("anim_in") or {}).get("type", "") for L in c["graphic"]["layers"] if (L.get("anim_in") or {}).get("type")})))
            lines.append(f"- {c['start']:.2f}–{c['start'] + d:.2f}s ({d:.2f}s) {what}" + (f" [{'; '.join(extras)}]" if extras else "") + (f" — {c['note']}" if c.get("note") else ""))
    if seq.get("captions"): lines.append("## Captions"); lines += [f"- {cp['start']:.2f}–{cp['end']:.2f}s “{cp['text']}”" for cp in seq["captions"]]
    return {"text": "\n".join(lines), "duration": round(total, 2), "clips": sum(len(t["clips"]) for t in seq["tracks"]), "sections": secs}

@app.get("/api/renders/manifest")
def renders_manifest():
    """Deliverables sheet: every finished render with file, sequence, preset and QA — JSON (and CSV via ?fmt=csv)."""
    rows = [{"name": j["name"], "file": j.get("out"), "sequence": j.get("sequence"), "status": j["status"], "finished": j.get("finished"), **{f"qa_{k}": v for k, v in (j.get("qa") or {}).items() if k != "flags"}, "flags": "; ".join((j.get("qa") or {}).get("flags") or [])} for j in JOBS.values() if j["status"] == "done"]
    return rows

REVIEW_HTML = """<!doctype html><meta charset="utf-8"><title>Review — {name}</title><style>body{{margin:0;background:#111;color:#eee;font:14px system-ui}} .wrap{{max-width:960px;margin:0 auto;padding:16px}} video{{width:100%;max-height:70vh;background:#000}} .c{{display:flex;gap:8px;margin:10px 0}} input,textarea{{flex:1;background:#1c1c1f;color:#eee;border:1px solid #333;border-radius:4px;padding:8px}} button{{background:#2d8ceb;color:#fff;border:0;border-radius:4px;padding:8px 14px;cursor:pointer}} .note{{padding:8px;border-left:3px solid #f6c14a;margin:6px 0;background:#18181b}} .t{{color:#9a9a9a;font-family:monospace;margin-right:8px;cursor:pointer}}</style>
<div class="wrap"><h2>{name}</h2><video id="v" src="{src}" controls playsinline></video><div class="c"><input id="who" placeholder="your name" value="client"><textarea id="txt" rows="2" placeholder="comment at the current time…"></textarea><button id="send">Add note</button></div><div id="list"></div></div>
<script>const v=document.getElementById('v');const fmt=t=>{{const m=Math.floor(t/60),s=(t%60).toFixed(1);return m+':'+String(s).padStart(4,'0')}};async function load(){{const r=await fetch('/api/review/{name}/notes');const j=await r.json();document.getElementById('list').innerHTML=j.map(n=>`<div class="note"><span class="t" onclick="v.currentTime=${{n.time}}">${{fmt(n.time)}}</span><b>${{n.author}}</b> ${{n.text}}</div>`).join('')}}
document.getElementById('send').onclick=async()=>{{const txt=document.getElementById('txt').value.trim();if(!txt)return;await fetch('/api/review/{name}/notes',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{time:v.currentTime,text:txt,author:document.getElementById('who').value||'client'}})}});document.getElementById('txt').value='';load()}};load();</script>"""

@app.get("/review/{name}")
def review_page(name: str):
    """Client review page for a finished render: the video with time-stamped comments that land as note markers in the sequence."""
    j = next((x for x in JOBS.values() if x["name"] == name and x["status"] == "done"), None)
    if not j: raise HTTPException(404, "no finished render with that name")
    return HTMLResponse(REVIEW_HTML.format(name=name, src=j["out"]))

@app.get("/api/review/{name}/notes")
def review_notes(name: str):
    j = next((x for x in JOBS.values() if x["name"] == name), None); seq_id = (j or {}).get("sequence", "seq1"); proj = load_project(); seq = next((x for x in proj["sequences"] if x["id"] == seq_id), None)
    return [m for m in ((seq or {}).get("markers") or []) if m.get("type") == "note" and m.get("review") == name]

@app.post("/api/review/{name}/notes")
async def review_note_add(name: str, req: Request):
    body = await req.json(); j = next((x for x in JOBS.values() if x["name"] == name), None); seq_id = (j or {}).get("sequence", "seq1")
    with LOCK:
        proj = load_project(); seq = next(x for x in proj["sequences"] if x["id"] == seq_id); mk = {"id": uuid.uuid4().hex[:6], "time": round(float(body.get("time", 0)), 3), "duration": 0, "name": str(body.get("text", ""))[:300], "type": "note", "color": "yellow", "author": str(body.get("author", "client"))[:40], "review": name, "resolved": False, "ts": time.time()}
        seq["markers"] = (seq.get("markers") or []) + [mk]; save_project(proj); si = proj["sequences"].index(seq)
    ev = log_event({"type": "ops", "actor": mk["author"], "tool": "review", "reason": f"review note: {mk['name'][:60]}", "ops": [{"op": "set", "path": f"/sequences/{si}/markers", "value": seq["markers"]}]}); await broadcast({k: v for k, v in ev.items() if k != "befores"}); return mk

@app.post("/api/install/whisper")
async def install_whisper():
    """Install faster-whisper into the running Python (venv) for auto-captions and transcripts; returns when done."""
    def run(): return subprocess.run([sys.executable, "-m", "pip", "install", "-q", "faster-whisper"], capture_output=True, text=True, timeout=1800)
    r = await asyncio.to_thread(run)
    try:
        import importlib; importlib.invalidate_caches(); import faster_whisper  # noqa
        return {"ok": True, "message": "faster-whisper installed — the first transcript downloads the model"}
    except ImportError: return {"ok": False, "message": (r.stderr or r.stdout)[-400:]}

@app.post("/api/recipes/talking_head")
async def recipe_talking_head(req: Request):
    """Talking-head clip: {sequence, clip_id, silences: bool, punch_every: s, voice_preset: bool, captions: bool, broll: [media_id…]}.
    Removes silences (ripple), adds alternating punch-ins, applies Voice Clean-up, word-pop captions from the transcript when available,
    and lays b-roll shots over the middle of the speech on the track above."""
    body = await req.json(); seq_id = body.get("sequence", "seq1"); cid = body["clip_id"]
    with LOCK:
        proj = load_project(); seq = next(x for x in proj["sequences"] if x["id"] == seq_id); tr = next(t for t in seq["tracks"] for c in t["clips"] if c["id"] == cid); c = next(x for x in tr["clips"] if x["id"] == cid); m = proj["media"].get(c.get("media_id"))
    if not m or not m.get("has_audio"): raise HTTPException(400, "select a clip with audio")
    ops = []; removed = 0.0; pieces = [(c["in_"], c["out"])]
    if body.get("silences", True):
        src = proj["media"][m["subclip_of"]] if m.get("subclip_of") else m; total = float(m["duration"]); env, rate = await asyncio.to_thread(_envelope, src["path"], min(3600, int(total) + 1), 50)
        import math as _m; db = [20 * _m.log10(max(e, 1e-6)) for e in env]; ref = max(db) if db else 0; gaps = []; start = None
        for i, v in enumerate(db):
            quiet = v < ref - 38
            if quiet and start is None: start = i / rate
            elif not quiet and start is not None:
                if i / rate - start >= 0.45: gaps.append((start + 0.08, i / rate - 0.08))
                start = None
        gaps = [g for g in gaps if g[1] > c["in_"] and g[0] < c["out"] and g[1] - g[0] > 0.05]; cur = c["in_"]; pieces = []
        for a, b_ in gaps:
            if min(b_, c["out"]) - max(cur, c["in_"]) > 0.04 and a > cur: pieces.append((max(cur, c["in_"]), min(a, c["out"])))
            cur = max(cur, b_)
        if c["out"] - cur > 0.04: pieces.append((cur, c["out"]))
        if not pieces: pieces = [(c["in_"], c["out"])]
    sp = c.get("speed", 1) or 1; end0 = c["start"] + (c["out"] - c["in_"]) / sp; t = c["start"]; new_clips = []
    fxp = json.load(open(os.path.join(ASSETS, "presets", "effect_presets.json"))).get("Voice — Clean-up", {}) if body.get("voice_preset", True) else {}
    ops.append({"op": "remove_clip", "sequence": seq_id, "track": tr["id"], "clip_id": cid})
    for i, (a, b_) in enumerate(pieces):
        nc = copy.deepcopy(c); nc.update(id=cid if i == 0 else uuid.uuid4().hex[:8], start=round(t, 4), in_=round(a, 4), out=round(b_, 4), note=(c.get("note", "") + " · " if c.get("note") else "") + f"speech {i + 1}/{len(pieces)}")
        if fxp: nc["afx_stack"] = copy.deepcopy(fxp.get("afx_stack", []))
        d = (b_ - a) / sp; every = float(body.get("punch_every", 3) or 3); kf = []; tt = 0.0; k = 0
        while tt < d: kf.append({"t": round(tt, 3), "v": 1.12 if (k + i) % 2 else 1.0, "e": "hold"}); tt += every; k += 1
        if body.get("punch_every", 3): nc["keyframes"] = {**(nc.get("keyframes") or {}), "transform.scale": kf}
        if i: nc["transition_in"] = None
        ops.append({"op": "set_clip", "sequence": seq_id, "track": tr["id"], "clip": nc}); new_clips.append(nc); t += d
    removed = (end0 - c["start"]) - (t - c["start"])
    for t2 in seq["tracks"]:
        for x in t2["clips"]:
            if x["id"] != cid and x["start"] >= end0 - 1e-6: ops.append({"op": "set_clip", "sequence": seq_id, "track": t2["id"], "clip": {"id": x["id"], "start": round(x["start"] - removed, 4)}})
    # b-roll over the middle third of each speech piece, on the track above
    broll = [proj["media"].get(x) for x in body.get("broll", []) if proj["media"].get(x)]
    if broll:
        vts = sorted([x for x in seq["tracks"] if x["kind"] == "video"], key=lambda x: x["index"]); above = next((x for x in vts if x["index"] > tr["index"]), None)
        if above:
            for i, nc in enumerate(new_clips):
                bm = broll[i % len(broll)]; d = (nc["out"] - nc["in_"]) / sp
                if d < 1.5: continue
                bd = min(2.2, d * 0.5, float(bm["duration"]) - 0.2); bs = nc["start"] + (d - bd) / 2
                ops.append({"op": "set_clip", "sequence": seq_id, "track": above["id"], "clip": {"id": uuid.uuid4().hex[:8], "media_id": bm["id"], "start": round(bs, 3), "in_": round(max(0.0, (float(bm["duration"]) - bd) / 2), 3), "out": round(max(0.0, (float(bm["duration"]) - bd) / 2) + bd, 3), "speed": 1, "fit": "cover" if (bm.get("width") or 0) <= (bm.get("height") or 1) else "blur_fill", "audio": {"gain_db": -60, "linked": False}, "transition_in": {"type": "dissolve", "duration": 0.25}, "transition_out": {"type": "dissolve", "duration": 0.25}, "keyframes": {"transform.scale": [{"t": 0, "v": 1.0}, {"t": round(bd, 3), "v": 1.06, "e": "ease"}]}, "note": "b-roll over speech"}})
    with LOCK:
        proj = load_project(); befores = apply_ops(proj, ops); warnings = normalize_tracks(proj); si = proj["sequences"].index(next(x for x in proj["sequences"] if x["id"] == seq_id))
        if body.get("captions", True):
            seq = proj["sequences"][si]; caps = seq.get("captions") or []
            if seq.get("transcript"):
                words = [w for w in seq["transcript"] if any(nc["start"] <= w["s"] < nc["start"] + (nc["out"] - nc["in_"]) / sp for nc in new_clips)]; block = []; bstart = None
                for w in words:
                    if bstart is None: bstart = w["s"]
                    block.append(w["w"])
                    if len(" ".join(block)) > 34 or w["w"].endswith((".", "?", "!")): caps.append({"id": uuid.uuid4().hex[:6], "start": round(bstart, 2), "end": round(w["e"], 2), "text": " ".join(block)}); block = []; bstart = None
                if block: caps.append({"id": uuid.uuid4().hex[:6], "start": round(bstart, 2), "end": round(words[-1]["e"], 2), "text": " ".join(block)})
                seq["captions"] = caps
            seq["caption_style"] = {**(seq.get("caption_style") or {}), "animate": "pop", "size": int(seq["height"] * 0.037), "borderw": 5, "y": 0.62}
        save_project(proj)
    ev = log_event({"type": "ops", "actor": body.get("actor", "human"), "tool": "recipe_talking_head", "reason": f"talking head: {len(pieces)} speech pieces, −{removed:.2f}s silence, {len(broll)} b-roll", "ops": ops, "befores": befores, "warnings": warnings}); await broadcast({k: v for k, v in ev.items() if k != "befores"})
    return {"ok": True, "pieces": len(pieces), "removed": round(removed, 2), "broll": len(broll)}

@app.post("/api/audio/duck_all")
async def duck_all(req: Request):
    """Auto-duck: every clip on music-tagged tracks (or the given track) dips by `amount` dB while any dialogue clip plays. {sequence, music_track?, amount, fade}"""
    body = await req.json(); seq_id = body.get("sequence", "seq1"); amount = float(body.get("amount", -12)); fade = float(body.get("fade", 0.35))
    with LOCK:
        proj = load_project(); seq = next(x for x in proj["sequences"] if x["id"] == seq_id); media = proj["media"]
        dialogue = []
        for t in seq["tracks"]:
            for c in t["clips"]:
                m = media.get(c.get("media_id") or "")
                if m and m.get("has_audio") and (c.get("audio_tag") == "dialogue" or (t["kind"] == "video" and (c.get("audio") or {}).get("linked") is not False and not c.get("audio_tag"))): dialogue.append((c["start"], c["start"] + (c["out"] - c["in_"]) / max(c.get("speed", 1), 1e-6)))
        dialogue.sort(); ops = []; n = 0
        for t in seq["tracks"]:
            if t["kind"] != "audio": continue
            if body.get("music_track") and t["id"] != body["music_track"]: continue
            for c in t["clips"]:
                m = media.get(c.get("media_id") or "")
                if not m or (c.get("audio_tag") not in (None, "music")) or (body.get("music_track") is None and not (m.get("has_audio") and not m.get("has_video"))): continue
                base = float((c.get("audio") or {}).get("gain_db", 0) or 0); cs, ce = c["start"], c["start"] + (c["out"] - c["in_"]) / max(c.get("speed", 1), 1e-6); kf = [{"t": 0.0, "v": base}]
                for ds, de in dialogue:
                    a, b_ = max(cs, ds), min(ce, de)
                    if b_ - a <= 0.2: continue
                    kf += [{"t": round(max(0, a - cs - fade), 3), "v": base}, {"t": round(a - cs, 3), "v": base + amount, "e": "ease"}, {"t": round(b_ - cs, 3), "v": base + amount}, {"t": round(min(ce - cs, b_ - cs + fade), 3), "v": base, "e": "ease"}]
                if len(kf) > 1:
                    kf = sorted({k["t"]: k for k in kf}.values(), key=lambda k: k["t"]); ops.append({"op": "set_clip", "sequence": seq_id, "track": t["id"], "clip": {"id": c["id"], "keyframes": {**(c.get("keyframes") or {}), "audio.gain_db": kf}}}); n += 1
        befores = apply_ops(proj, ops); save_project(proj)
    ev = log_event({"type": "ops", "actor": body.get("actor", "human"), "tool": "duck_all", "reason": f"auto-duck {n} music clip(s) under dialogue", "ops": ops, "befores": befores}); await broadcast({k: v for k, v in ev.items() if k != "befores"}); return {"ducked": n, "dialogue_spans": len(dialogue)}

@app.post("/api/recipes/cover")
async def recipe_cover(req: Request):
    """Cover / thumbnail: {sequence, time, headline, sub?, template?, sizes:[[1080,1920],[1280,720]]} → PNGs of the frame at `time` with a
    premium headline card (brand kit applied), one per size. For Reels covers and YouTube thumbnails."""
    from render import render_frame
    import re as _re
    body = await req.json(); seq_id = body.get("sequence", "seq1"); t = float(body.get("time", 1.0)); proj = copy.deepcopy(load_project()); seq = next(x for x in proj["sequences"] if x["id"] == seq_id)
    b = {"primary": "#E8631C", "secondary": "#7A2E9E", "text": "#FFFFFF", "font": "", **(proj.get("brand") or {})}
    def walk(v):
        if isinstance(v, str): return _re.sub(r"\{\{(primary|secondary|text|font)\}\}", lambda m: b.get(m.group(1), ""), v)
        if isinstance(v, list): return [walk(x) for x in v]
        if isinstance(v, dict): return {k: walk(x) for k, x in v.items() if not (k == "font" and walk(x) == "")}
        return v
    tp = templates().get(body.get("template") or "Hook — Big Statement"); outs = []
    os.makedirs(P("renders"), exist_ok=True)
    for (w, h) in body.get("sizes") or [[seq["width"], seq["height"]]]:
        sq = copy.deepcopy(seq); sq["id"] = "cover"; sq["captions"] = []; sq["markers"] = []
        # drop graphics/titles at the chosen time (the card replaces them), keep the footage
        for tr in sq["tracks"]:
            if tr["kind"] == "video": tr["clips"] = [c for c in tr["clips"] if c.get("media_id")]
        layers = walk(json.loads(json.dumps(tp["layers"]))) if tp else []
        for L in layers:
            L.pop("anim_in", None); L.pop("anim_out", None)
            if L.get("kind") == "text" and L.get("text", "").startswith("STOP"): L["text"], L["size"] = fit_hook_text(body.get("headline", "Stop scrolling."), int(L.get("size", 150)), sq["width"])
            elif L.get("kind") == "text": L["text"] = body.get("sub", "")
        top = sorted([x for x in sq["tracks"] if x["kind"] == "video"], key=lambda x: -x["index"])[0]
        top["clips"].append({"id": "coverc", "media_id": None, "start": max(0.0, t - 0.5), "in_": 0, "out": 1.5, "speed": 1, "graphic": {"name": "Cover", "layers": layers}, "transform": {"opacity": 1}, "keyframes": {}})
        proj2 = copy.deepcopy(proj); proj2["sequences"] = [sq]; name = f"cover_{seq_id}_{w}x{h}.png"; out = P("renders", name)
        if (w, h) != (sq["width"], sq["height"]):
            sq["width"], sq["height"] = w, h  # re-fit: the frame renders at the new canvas size; footage that no longer matches the aspect gets blur-fill
            for tr in sq["tracks"]:
                for c in tr["clips"]:
                    if c.get("media_id"): c["fit"] = "blur_fill"
        try: await asyncio.to_thread(render_frame, proj2, "cover", t, out)
        except Exception as e: raise HTTPException(500, f"cover failed: {e}")
        outs.append(f"/renders/{name}")
    return {"covers": outs}

@app.post("/api/recipes/explainer")
async def recipe_explainer(req: Request):
    """Long-form dressing: {sequence, lower_third:{name, role}, chapters: bool, end_card: text}. Adds a lower third at the start, a
    Chapter Title card at every chapter marker (numbered, from the marker names), and a CTA end card at the end — brand kit applied."""
    import re as _re
    body = await req.json(); seq_id = body.get("sequence", "seq1")
    with LOCK:
        proj = load_project(); seq = next(x for x in proj["sequences"] if x["id"] == seq_id); b = {"primary": "#E8631C", "secondary": "#7A2E9E", "text": "#FFFFFF", "font": "", **(proj.get("brand") or {})}
        def walk(v):
            if isinstance(v, str): return _re.sub(r"\{\{(primary|secondary|text|font)\}\}", lambda m: b.get(m.group(1), ""), v)
            if isinstance(v, list): return [walk(x) for x in v]
            if isinstance(v, dict): return {k: walk(x) for k, x in v.items() if not (k == "font" and walk(x) == "")}
            return v
        tpls = templates(); vts = sorted([x for x in seq["tracks"] if x["kind"] == "video"], key=lambda x: x["index"])
        while len(vts) < 3:  # cards live above the footage: chapters on the second video track, lower third + end card on the third
            nt = {"id": f"V{len(vts) + 1}", "kind": "video", "index": len(vts) + 1, "clips": []}; seq["tracks"].append(nt); vts.append(nt)
        chap_tr, card_tr = vts[1], vts[2]; total = max([c["start"] + (c["out"] - c["in_"]) / max(c.get("speed", 1), 1e-6) for t in seq["tracks"] for c in t["clips"]] + [0]); ops = []
        lt = body.get("lower_third")
        if lt and tpls.get("Lower Third — Card"):
            layers = walk(json.loads(json.dumps(tpls["Lower Third — Card"]["layers"]))); texts = [L for L in layers if L.get("kind") == "text"]
            if texts: texts[0]["text"] = lt.get("name", "")
            if len(texts) > 1: texts[1]["text"] = lt.get("role", "")
            ops.append({"op": "set_clip", "sequence": seq_id, "track": card_tr["id"], "clip": {"id": uuid.uuid4().hex[:8], "media_id": None, "start": float(lt.get("at", 1.0)), "in_": 0, "out": min(4.5, max(1.0, total - 3.5 - float(lt.get("at", 1.0)))), "speed": 1, "graphic": {"name": "Lower third", "layers": layers}, "transform": {"opacity": 1}, "keyframes": {}, "note": "lower third"}})
        if body.get("chapters", True) and tpls.get("Chapter Title"):
            chs = sorted([m for m in (seq.get("markers") or []) if m.get("type") in ("chapter", "comment") and m.get("name")], key=lambda m: m["time"])
            for i, m in enumerate(chs):
                layers = walk(json.loads(json.dumps(tpls["Chapter Title"]["layers"]))); texts = [L for L in layers if L.get("kind") == "text"]
                if texts: texts[0]["text"] = f"{i + 1:02d}"
                if len(texts) > 1: texts[1]["text"] = m["name"]
                nxt = chs[i + 1]["time"] if i + 1 < len(chs) else total; d = max(0.8, min(3.0, nxt - m["time"] - 0.1, (total - 3.2 - m["time"]) if body.get("end_card") else 3.0))
                ops.append({"op": "set_clip", "sequence": seq_id, "track": chap_tr["id"], "clip": {"id": uuid.uuid4().hex[:8], "media_id": None, "start": round(m["time"], 3), "in_": 0, "out": round(d, 3), "speed": 1, "graphic": {"name": f"Chapter {i + 1}", "layers": layers}, "transform": {"opacity": 1}, "keyframes": {}, "note": f"chapter card: {m['name']}"}})
        if body.get("end_card") and tpls.get("CTA — Follow"):
            layers = walk(json.loads(json.dumps(tpls["CTA — Follow"]["layers"])))
            for L in layers:
                if L.get("kind") == "text": L["text"] = body["end_card"]
            ops.append({"op": "set_clip", "sequence": seq_id, "track": card_tr["id"], "clip": {"id": uuid.uuid4().hex[:8], "media_id": None, "start": round(max(0.0, total - 3.0), 3), "in_": 0, "out": 3.0, "speed": 1, "graphic": {"name": "End card", "layers": layers}, "transform": {"opacity": 1}, "keyframes": {}, "note": "end card"}})
        befores = apply_ops(proj, ops); warnings = normalize_tracks(proj); save_project(proj)
    ev = log_event({"type": "ops", "actor": body.get("actor", "human"), "tool": "recipe_explainer", "reason": f"explainer dressing: {len(ops)} cards", "ops": ops, "befores": befores, "warnings": warnings}); await broadcast({k: v for k, v in ev.items() if k != "befores"}); return {"ok": True, "cards": len(ops)}

@app.get("/api/projects/recent")
def projects_recent():
    """Recent projects (most recently updated first) for the File menu."""
    out = []
    for pid in (os.listdir(P("projects")) if os.path.isdir(P("projects")) else []):
        f = P("projects", pid, "project.json")
        if os.path.exists(f):
            try: j = json.load(open(f)); out.append({"id": pid, "name": j.get("name", pid), "updated": j.get("updated", os.path.getmtime(f)), "sequences": len(j.get("sequences", [])), "media": len(j.get("media", {}))})
            except Exception: pass
    return sorted(out, key=lambda x: -x["updated"])[:12]

@app.post("/api/projects/duplicate")
async def projects_duplicate(req: Request):
    """Duplicate the active project (project file, snapshots and training history stay with the original) as a new project and switch to it."""
    body = await req.json(); name = body.get("name") or None
    with LOCK:
        proj = load_project(); pid = str(uuid.uuid4())[:8]; os.makedirs(P("projects", pid), exist_ok=True); cp = copy.deepcopy(proj); cp["id"] = pid; cp["name"] = name or (proj.get("name", "Untitled") + " copy"); cp["proposals"] = []
        json.dump(cp, open(P("projects", pid, "project.json"), "w"), indent=1); json.dump({"id": pid}, open(P("active.json"), "w"))
    ev = log_event({"type": "project_replaced", "actor": body.get("actor", "human"), "source": "duplicate", "project": pid}); await broadcast(ev); return {"id": pid, "name": cp["name"]}

@app.get("/api/media/usage")
def media_usage(media_id: str):
    """Where a bin item is used: [{sequence, track, clip_id, start}] — Reveal in Timeline / usage counts."""
    proj = load_project(); out = []
    for sq in proj["sequences"]:
        for t in sq["tracks"]:
            for c in t["clips"]:
                if c.get("media_id") == media_id: out.append({"sequence": sq["id"], "sequence_name": sq["name"], "track": t["id"], "clip_id": c["id"], "start": c["start"]})
    return out

@app.get("/api/cache")
def cache_info():
    """Sizes of the regenerable caches (proxies, thumbnails, segment cache, template previews) and the renders folder."""
    def size(d): return round(sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(d) for f in fs) / 1e6, 1) if os.path.isdir(d) else 0.0
    return {"proxies_mb": size(P("proxies")), "thumbs_mb": size(P("thumbs")), "segments_mb": size(P("renders", "cache")), "renders_mb": size(P("renders")) - size(P("renders", "cache")), "sfx_mb": size(P("sfx"))}

@app.post("/api/cache/clear")
async def cache_clear(req: Request):
    """Clear regenerable caches: {what: 'segments'|'proxies'|'thumbs'|'all'}. Proxies and thumbnails regenerate on demand."""
    body = await req.json(); what = body.get("what", "segments"); cleared = []
    for key, d in (("segments", P("renders", "cache")), ("proxies", P("proxies")), ("thumbs", P("thumbs", "tpl"))):
        if what in (key, "all") and os.path.isdir(d):
            for f in os.listdir(d):
                try: os.remove(os.path.join(d, f))
                except OSError: pass
            cleared.append(key)
    if "proxies" in cleared:
        with LOCK:
            proj = load_project()
            for m in proj["media"].values(): m.pop("proxy", None)
            save_project(proj)
    return {"cleared": cleared, **cache_info()}

@app.post("/api/recipes/reel")
async def recipe_reel(req: Request):
    """New Reel: {shots:[media_id…], music: media_id|null, target: seconds, hook: text, cta: text, captions: bool, look: lut path|null, sequence}.
    Builds a 9:16 cut: hook card (premium template), shots cut on the music's downbeats (or evenly), blur-fill framing for landscape
    shots, a light push-in on each, the music bed remixed to length with ducking, a CTA end card, whooshes under cuts, brand kit applied."""
    import re as _re
    body = await req.json(); seq_id = body.get("sequence", "seq1"); target = float(body.get("target", 15)); shots = [m for m in body.get("shots", [])]
    if not shots: raise HTTPException(400, "pick at least one shot")
    with LOCK:
        proj = load_project(); seq = next(x for x in proj["sequences"] if x["id"] == seq_id); media = proj["media"]; b = {"primary": "#E8631C", "secondary": "#7A2E9E", "text": "#FFFFFF", "font": "", **(proj.get("brand") or {})}
        W, H = seq["width"], seq["height"]; vtracks = sorted([t for t in seq["tracks"] if t["kind"] == "video"], key=lambda t: t["index"]); atracks = sorted([t for t in seq["tracks"] if t["kind"] == "audio"], key=lambda t: t["index"])
        v1, v2 = vtracks[0], (vtracks[1] if len(vtracks) > 1 else vtracks[0]); a_music = atracks[-1] if atracks else None; a_sfx = atracks[0] if atracks else None
        def walk(v):
            if isinstance(v, str): return _re.sub(r"\{\{(primary|secondary|text|font)\}\}", lambda m: b.get(m.group(1), ""), v)
            if isinstance(v, list): return [walk(x) for x in v]
            if isinstance(v, dict): return {k: walk(x) for k, x in v.items() if not (k == "font" and walk(x) == "")}
            return v
        tpls = templates()
        # timing: cut points from the music's downbeats when available, else even shares
        hook_d = 1.6 if body.get("hook") else 0.0; cta_d = 2.2 if body.get("cta") else 0.0; body_d = max(2.0, target - hook_d - cta_d)
        cuts = []
        mus = media.get(body.get("music")) if body.get("music") else None
        if mus:
            try:
                src = media[mus["subclip_of"]] if mus.get("subclip_of") else mus; beat, phase = _beats(src["path"], min(600, int(mus["duration"]) + 1)); bar = beat * 4; t = phase
                while t < body_d + bar: cuts.append(t); t += bar
                cuts = [c for c in cuts if 1.2 <= c <= body_d - 1.0] or []  # no cut in the first 1.2 s or the last second
            except Exception: cuts = []
        n = len(shots); bounds = [0.0]
        if cuts and len(cuts) >= n - 1:
            step = max(1, len(cuts) // max(1, n - 1)); bounds += cuts[step - 1::step][:n - 1]
        else: bounds += [body_d * i / n for i in range(1, n)]
        bounds.append(body_d); ops = []; t = hook_d
        for i, mid in enumerate(shots):
            m = media.get(mid)
            if not m: continue
            d = max(0.4, bounds[i + 1] - bounds[i]); avail = max(0.2, float(m["duration"]) - 0.2); d = min(d, avail); in_ = max(0.0, (float(m["duration"]) - d) / 2)
            landscape = (m.get("width") or W) > (m.get("height") or H)
            clip = {"id": uuid.uuid4().hex[:8], "media_id": mid, "start": round(t, 3), "in_": round(in_, 3), "out": round(in_ + d, 3), "speed": 1, "fit": "blur_fill" if landscape else "cover", "transform": {"x": 0, "y": 0, "scale": 1, "rotation": 0, "opacity": 1}, "audio": {"gain_db": -3, "linked": True}, "keyframes": {"transform.scale": [{"t": 0, "v": 1.0}, {"t": round(d, 3), "v": 1.08, "e": "ease"}]}, "color": {"lut": body.get("look")} if body.get("look") else {}, "note": f"reel shot {i + 1}/{n}" + (" on the downbeat" if cuts else "")}
            if i > 0: clip["transition_in"] = {"type": "dip_black" if i % 3 == 2 else "dissolve", "duration": 0.25}
            ops.append({"op": "set_clip", "sequence": seq_id, "track": v1["id"], "clip": clip}); t += d
        end_t = t
        if body.get("hook"):
            tp = tpls.get("Hook — Big Statement"); layers = walk(json.loads(json.dumps(tp["layers"])))
            for L in layers:
                if L.get("kind") == "text" and L.get("text", "").startswith("STOP"): L["text"], L["size"] = fit_hook_text(body["hook"], int(L.get("size", 150)), W)
                elif L.get("kind") == "text": L["text"] = body.get("hook_sub", "")
            ops.append({"op": "set_clip", "sequence": seq_id, "track": v2["id"], "clip": {"id": uuid.uuid4().hex[:8], "media_id": None, "start": 0.0, "in_": 0, "out": round(hook_d + 0.6, 3), "speed": 1, "graphic": {"name": "Hook", "layers": layers}, "transform": {"opacity": 1}, "keyframes": {}, "note": "hook card"}})
        if body.get("cta"):
            tp = tpls.get("CTA — Follow"); layers = walk(json.loads(json.dumps(tp["layers"])))
            for L in layers:
                if L.get("kind") == "text": L["text"] = body["cta"]
            ops.append({"op": "set_clip", "sequence": seq_id, "track": v2["id"], "clip": {"id": uuid.uuid4().hex[:8], "media_id": None, "start": round(max(0.0, end_t - 0.3), 3), "in_": 0, "out": round(cta_d + 0.3, 3), "speed": 1, "graphic": {"name": "CTA", "layers": layers}, "transform": {"opacity": 1}, "keyframes": {}, "note": "CTA end card"}})
            end_t += cta_d
        if mus and a_music:
            ml = float(mus["duration"]); segs = [{"in": 0.0, "out": min(ml, end_t)}]
            if ml > end_t + 1.0:
                try: segs = json.loads(json.dumps((await audio_remix_segments(mus, end_t))))
                except Exception: pass
            st = 0.0
            for i, sg in enumerate(segs):
                remaining = end_t - st
                if remaining <= 0.2: break
                out_ = min(sg["out"], sg["in"] + remaining)  # the bed ends exactly with the picture
                ops.append({"op": "set_clip", "sequence": seq_id, "track": a_music["id"], "clip": {"id": uuid.uuid4().hex[:8], "media_id": mus["id"], "start": round(st, 3), "in_": sg["in"], "out": round(out_, 3), "speed": 1, "audio": {"gain_db": -10, "linked": True, "fade_out": 0.8 if (i == len(segs) - 1 or out_ < sg["out"]) else 0}, "audio_transition_in": {"type": "constant_power", "duration": 0.4} if i else None, "note": "music bed"}}); st += out_ - sg["in"]
                if out_ < sg["out"]: break
        if body.get("captions", True) and body.get("hook"): seq_caps = [{"id": uuid.uuid4().hex[:6], "start": round(hook_d + 0.2, 2), "end": round(min(end_t, hook_d + 2.6), 2), "text": body.get("caption_text") or body["hook"]}]; ops.append({"op": "set", "path": f"/sequences/{proj['sequences'].index(seq)}/captions", "value": (seq.get("captions") or []) + seq_caps})
        if body.get("caption_style"): ops.append({"op": "set", "path": f"/sequences/{proj['sequences'].index(seq)}/caption_style", "value": walk(body["caption_style"])})
        secs = ([{"id": uuid.uuid4().hex[:6], "time": 0.0, "duration": round(hook_d, 2), "name": "Hook", "type": "section", "color": "green"}] if hook_d else []) + [{"id": uuid.uuid4().hex[:6], "time": round(hook_d, 2), "duration": round(t - hook_d, 2), "name": "Body", "type": "section", "color": "blue"}] + ([{"id": uuid.uuid4().hex[:6], "time": round(t, 2), "duration": round(cta_d, 2), "name": "CTA", "type": "section", "color": "orange"}] if cta_d else [])
        ops.append({"op": "set", "path": f"/sequences/{proj['sequences'].index(seq)}/markers", "value": (seq.get("markers") or []) + secs})
        befores = apply_ops(proj, ops); warnings = normalize_tracks(proj); save_project(proj)
    # whooshes under the cuts (generated sfx) — done after the lock since ingest touches the project
    if body.get("sfx", True) and a_sfx:
        try:
            m = await media_sfx_internal("whoosh"); cuts_t = [o["clip"]["start"] for o in ops if o["op"] == "set_clip" and o.get("track") == v1["id"] and o["clip"].get("transition_in")]
            sops = [{"op": "set_clip", "sequence": seq_id, "track": a_sfx["id"], "clip": {"id": uuid.uuid4().hex[:8], "media_id": m["id"], "start": round(max(0.0, ct - 0.2), 3), "in_": 0, "out": m["duration"], "speed": 1, "audio": {"gain_db": -8, "linked": True}, "note": "whoosh under cut"}} for ct in cuts_t]
            with LOCK:
                proj = load_project(); apply_ops(proj, sops); normalize_tracks(proj); save_project(proj)
            ops += sops
        except Exception: pass
    ev = log_event({"type": "ops", "actor": body.get("actor", "human"), "tool": "recipe_reel", "reason": f"reel: {len(shots)} shots, {'beat-cut' if cuts else 'even'}, hook={'yes' if body.get('hook') else 'no'}", "ops": ops, "befores": befores, "warnings": warnings}); await broadcast({k: v for k, v in ev.items() if k != "befores"})
    return {"ok": True, "duration": round(end_t, 2), "beat_cut": bool(cuts), "clips": len(ops)}

async def audio_remix_segments(m, target):
    proj = load_project(); src = proj["media"][m["subclip_of"]] if m.get("subclip_of") else m; total = float(m["duration"])
    env, rate = await asyncio.to_thread(_envelope, src["path"], min(600, int(total) + 1), 50)
    on = [max(0.0, env[i] - env[i - 1]) for i in range(1, len(env))]; best, bestlag = -1, int(rate * 60 / 120)
    for lag in range(int(rate * 60 / 180), int(rate * 60 / 60) + 1):
        s_ = sum(on[i] * on[i - lag] for i in range(lag, len(on)))
        if s_ > best: best, bestlag = s_, lag
    beat = bestlag / rate; phrase = beat * 16; bestp = 0.0
    cut = total - target; remove = max(1, int(round(cut / phrase))) * phrase
    if remove > total * 0.8: remove = phrase * max(1, int((total * 0.8) // phrase))
    start_rm = bestp + phrase * max(1, int(((total - bestp) / phrase) // 3)); end_rm = min(total - phrase, start_rm + remove)
    return [{"in": 0.0, "out": round(start_rm, 3)}, {"in": round(end_rm, 3), "out": total}]

async def media_sfx_internal(kind):
    d = P("sfx"); os.makedirs(d, exist_ok=True); f = os.path.join(d, f"{kind}.wav")
    if not os.path.exists(f): await asyncio.to_thread(lambda: subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", SFX[kind][0], "-t", str(SFX[kind][1]), "-c:a", "pcm_s16le", f], capture_output=True, timeout=60))
    with LOCK:
        proj = load_project()
        for m in proj["media"].values():
            if m.get("sfx") == kind: return m
        mid, m = await asyncio.to_thread(ingest, f, f"SFX {kind}"); m["sfx"] = kind; proj["media"][mid] = m; save_project(proj); return m

@app.post("/api/graphics/split_words")
async def graphics_split_words(req: Request):
    """Kinetic typography: split a text layer into one layer per word with exact pixel offsets (PIL metrics of the render font), each
    inheriting the style and given a staggered animation. {sequence, clip_id, layer, anim:{type,duration,ease}, stagger}"""
    from PIL import ImageFont
    from render import font_file, seq_total
    body = await req.json(); seq_id = body.get("sequence", "seq1"); cid = body["clip_id"]; li = int(body.get("layer", 0)); an = body.get("anim") or {"type": "pop", "duration": 0.35, "ease": "back_out"}; stagger = float(body.get("stagger", 0.12))
    with LOCK:
        proj = load_project(); seq = next(x for x in proj["sequences"] if x["id"] == seq_id); tr = next(t for t in seq["tracks"] for c in t["clips"] if c["id"] == cid); c = next(x for x in tr["clips"] if x["id"] == cid)
        g = c.get("graphic"); L = g["layers"][li]
        if L.get("kind") != "text": raise HTTPException(400, "layer is not text")
        size = int(L.get("size", seq["height"] * 0.05)); font = ImageFont.truetype(font_file(L.get("font"), L.get("weight", "bold")), size); words = str(L.get("text", "")).split(); space = font.getlength(" ")
        widths = [font.getlength(w) for w in words]; total = sum(widths) + space * (len(words) - 1); W = seq["width"]
        align = L.get("align", "center"); x0 = {"center": -total / 2, "left": 0.0, "right": -total}[align if align in ("center", "left", "right") else "center"]  # offsets relative to the layer's anchor
        # anchor the words as left-aligned pieces positioned from the original alignment point
        base_x = {"center": W / 2, "left": W * 0.06, "right": W * 0.94}[align if align in ("center", "left", "right") else "center"] + float(L.get("x", 0))
        new_layers = []; cx = base_x + x0; full_top = font.getbbox(" ".join(words))[1] if words else 0
        for i, (w, ww) in enumerate(zip(words, widths)):
            wl = {k: v for k, v in L.items() if k not in ("anim_in", "anim_out")}; wl.update(text=w, align="left", x=round(cx - W * 0.06, 1), baseline_dy=int(font.getbbox(w)[1] - full_top), anim_in={**an, "delay": round(float((L.get("anim_in") or {}).get("delay", 0) or 0) + i * stagger, 3)}, word_of=li)
            if L.get("anim_out"): wl["anim_out"] = {**L["anim_out"], "delay": round(float(L["anim_out"].get("delay", 0) or 0) + (len(words) - 1 - i) * stagger * 0.5, 3)}
            new_layers.append(wl); cx += ww + space
        g["layers"] = g["layers"][:li] + new_layers + g["layers"][li + 1:]; save_project(proj)
    ev = log_event({"type": "ops", "actor": body.get("actor", "human"), "tool": "kinetic", "reason": f"split into {len(new_layers)} words", "ops": [{"op": "set_clip", "sequence": seq_id, "track": tr["id"], "clip": {"id": cid, "graphic": g}}]}); await broadcast(ev); return {"layers": len(new_layers)}

@app.post("/api/media/input_transform")
async def media_input_transform(req: Request):
    """Assign a camera log → Rec.709 input transform to a media item: {media_id, transform: none|slog3|vlog|clog3|logc3}"""
    body = await req.json(); tr = body.get("transform") or "none"
    if tr not in ("none", "slog3", "vlog", "clog3", "logc3"): raise HTTPException(400, "unknown transform")
    with LOCK:
        proj = load_project(); m = proj["media"].get(body["media_id"])
        if not m: raise HTTPException(404)
        if tr == "none": m.pop("input_transform", None)
        else: m["input_transform"] = tr
        save_project(proj)
    ev = log_event({"type": "media_added", "actor": body.get("actor", "human"), "media": [m["id"]], "input_transform": tr}); await broadcast(ev); return m

@app.post("/api/media/extract_audio")
async def media_extract_audio(req: Request):
    """Extract Audio: an audio-only bin item referencing the same file."""
    body = await req.json()
    with LOCK:
        proj = load_project(); m = proj["media"].get(body["media_id"])
        if not m or not m.get("has_audio"): raise HTTPException(404)
        nid = str(uuid.uuid4())[:8]; proj["media"][nid] = {**m, "id": nid, "name": os.path.splitext(m["name"])[0] + " Extracted.wav", "has_video": False, "thumb": None, "strip": None, "extracted_from": m["id"], "added": time.time()}; save_project(proj)
    ev = log_event({"type": "media_added", "actor": body.get("actor", "human"), "media": [nid], "extracted": True}); await broadcast(ev); return proj["media"][nid]

# ---------- synthetic media (New Item), audio gain, render & replace, EDL/VTT, app events ----------
SFX = {  # generated sound effects (no assets needed): lavfi graphs producing stereo 48 kHz WAV
    "whoosh": ("anoisesrc=c=pink:r=48000:d=0.8,lowpass=f=6000,afade=t=in:d=0.25,afade=t=out:st=0.35:d=0.45,volume=0.9,aformat=channel_layouts=stereo", 0.8),
    "riser": ("aevalsrc='0.5*sin(2*PI*t*(120+900*t*t))|0.5*sin(2*PI*t*(120+900*t*t))':s=48000:d=2.0,afade=t=in:d=1.5,afade=t=out:st=1.8:d=0.2,volume=0.8", 2.0),
    "impact": ("sine=f=55:r=48000:d=0.6,afade=t=out:st=0.05:d=0.55,volume=1.0,aformat=channel_layouts=stereo", 0.6),
    "click": ("sine=f=2000:r=48000:d=0.05,afade=t=out:d=0.05,aformat=channel_layouts=stereo", 0.05),
    "swoosh_reverse": ("anoisesrc=c=pink:r=48000:d=0.7,lowpass=f=5000,afade=t=in:d=0.6,afade=t=out:st=0.6:d=0.1,volume=0.9,aformat=channel_layouts=stereo", 0.7),
    "pop": ("sine=f=440:r=48000:d=0.15,afade=t=out:d=0.15,volume=0.6,aformat=channel_layouts=stereo", 0.15)}

@app.post("/api/media/import_sequence")
async def media_import_sequence(req: Request):
    """Import a numbered image sequence as one clip: {folder, fps} — frames like shot_0001.png … become a video-like media item."""
    import re as _re
    body = await req.json(); folder = body["folder"]; fps = float(body.get("fps", 24))
    files = sorted(f for f in os.listdir(folder) if f.lower().endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".dpx")))
    if len(files) < 2: raise HTTPException(400, "need at least two numbered frames")
    m_ = _re.match(r"^(.*?)(\d+)(\.[^.]+)$", files[0])
    if not m_: raise HTTPException(400, "frames must be numbered (name0001.png)")
    prefix, num, ext = m_.groups(); pattern = os.path.join(folder, f"{prefix}%0{len(num)}d{ext}"); start = int(num)
    first = os.path.join(folder, files[0]); info = probe(first); mid = str(uuid.uuid4())[:8]
    m = {"id": mid, "name": f"{prefix or os.path.basename(folder)} [{len(files)} frames]", "path": pattern, "duration": len(files) / fps, "width": info["width"], "height": info["height"], "fps": fps, "is_image": False, "has_video": True, "has_audio": False, "sequence_frames": len(files), "input_opts": ["-framerate", f"{fps:g}", "-start_number", str(start)], "thumb": None, "strip": None, "wave": None, "status": "ingesting", "added": time.time()}
    with LOCK:
        proj = load_project(); proj["media"][mid] = m; save_project(proj)
    threading.Thread(target=finish_ingest, args=(mid, pattern, {**info, "has_video": True, "has_audio": False, "duration": m["duration"], "is_image": False, "input_opts": m["input_opts"]}), daemon=True).start()
    ev = log_event({"type": "media_added", "actor": body.get("actor", "human"), "media": [mid]}); await broadcast(ev); return m

@app.post("/api/media/sfx")
async def media_sfx(req: Request):
    """Generated sound effect as a bin item: {kind: whoosh|riser|impact|click|swoosh_reverse|pop}. Files are written to data/sfx once."""
    body = await req.json(); kind = body.get("kind", "whoosh")
    if kind not in SFX: raise HTTPException(400, "unknown sfx")
    d = P("sfx"); os.makedirs(d, exist_ok=True); f = os.path.join(d, f"{kind}.wav")
    if not os.path.exists(f): await asyncio.to_thread(lambda: subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", SFX[kind][0], "-t", str(SFX[kind][1]), "-c:a", "pcm_s16le", f], capture_output=True, timeout=60))
    with LOCK:
        proj = load_project(); mid, m = await asyncio.to_thread(ingest, f, f"SFX {kind}"); m["sfx"] = kind; proj["media"][mid] = m; save_project(proj)
    ev = log_event({"type": "media_added", "actor": body.get("actor", "human"), "media": [mid]}); await broadcast(ev); return m

@app.post("/api/media/synthetic")
async def media_synthetic(req: Request):
    """New Item: {kind: black|color|bars|transparent|counting_leader, color, duration, tone_hz, name}. Rendered from generators, never a file."""
    body = await req.json(); kind = body.get("kind", "black"); dur = float(body.get("duration", 5)); mid = str(uuid.uuid4())[:8]
    names = {"black": "Black Video", "color": "Color Matte", "bars": "Bars and Tone", "transparent": "Transparent Video", "counting_leader": "Counting Leader", "gradient": "Animated Gradient", "light_leak": "Light Leak (blend: screen)", "grain": "Film Grain (blend: overlay)"}
    m = {"id": mid, "name": body.get("name") or names.get(kind, kind), "path": f"synthetic://{kind}", "duration": dur, "width": int(body.get("width", 1920)), "height": int(body.get("height", 1080)), "fps": 0, "is_image": False, "has_video": True, "has_audio": kind in ("bars", "counting_leader"), "synthetic": {"kind": kind, "color": body.get("color", "#000000"), "tone_hz": body.get("tone_hz", 1000), "color2": body.get("color2"), "color3": body.get("color3"), "speed": body.get("speed"), "intensity": body.get("intensity")}, "added": time.time()}
    with LOCK:
        proj = load_project(); proj["media"][mid] = m; save_project(proj)
    ev = log_event({"type": "media_added", "actor": body.get("actor", "human"), "media": [mid], "synthetic": kind}); await broadcast(ev); return m

@app.post("/api/audio/peak")
async def audio_peak(req: Request):
    """Max peak (dBFS) of a media range via volumedetect — for Audio Gain → Normalize Max Peak. {media_id, in, out}"""
    body = await req.json(); proj = load_project(); m = proj["media"].get(body["media_id"])
    if not m or not m.get("has_audio") or m.get("synthetic"): raise HTTPException(404)
    src = proj["media"][m["subclip_of"]] if m.get("subclip_of") else m; off = float(m.get("sub_in", 0) or 0)
    i, o = float(body.get("in", 0)) + off, float(body.get("out", m["duration"])) + off
    r = (await asyncio.to_thread(subprocess.run, ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{i:.3f}", "-t", f"{max(o - i, 0.1):.3f}", "-i", src["path"], "-vn", "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True, timeout=300)).stderr
    import re as _re; mx = _re.search(r"max_volume:\s*(-?[\d.]+) dB", r); mean = _re.search(r"mean_volume:\s*(-?[\d.]+) dB", r)
    return {"max_peak_db": float(mx.group(1)) if mx else None, "mean_db": float(mean.group(1)) if mean else None}

@app.post("/api/render_replace")
async def render_replace(req: Request):
    """Render and Replace: bake one clip (with all effects) to a new media file and swap it into the timeline."""
    body = await req.json(); seq_id = body.get("sequence", "seq1"); cid = body["clip_id"]
    proj = load_project(); seq = next(s for s in proj["sequences"] if s["id"] == seq_id)
    tr = next((t for t in seq["tracks"] for c in t["clips"] if c["id"] == cid), None); c = next(x for x in tr["clips"] if x["id"] == cid)
    from render import clip_dur as _cd
    tmp = copy.deepcopy(proj); tseq = next(s for s in tmp["sequences"] if s["id"] == seq_id); tseq["captions"] = []; tseq["in_point"] = None; tseq["out_point"] = None
    for t in tseq["tracks"]: t["clips"] = [dict(x, start=0.0, transition_in=None, transition_out=None) for x in t["clips"] if x["id"] == cid] if t["id"] == tr["id"] else []
    os.makedirs(P("media"), exist_ok=True); out = P("media", f"rendered_{cid}_{int(time.time())}.mp4")
    await asyncio.to_thread(do_render, tmp, seq_id, out, {"crf": 16, "x264_preset": "fast"})
    with LOCK:
        proj = load_project(); mid, m = await asyncio.to_thread(ingest, out, f"{cid} (rendered)"); proj["media"][mid] = m
        seq = next(s for s in proj["sequences"] if s["id"] == seq_id); tr = next(t for t in seq["tracks"] for x in t["clips"] if x["id"] == cid); c = next(x for x in tr["clips"] if x["id"] == cid)
        keep = {k: c[k] for k in ("id", "start", "label", "group", "markers", "transition_in", "transition_out", "audio_transition_in", "audio_transition_out") if k in c}
        c.clear(); c.update({"media_id": mid, "in_": 0.0, "out": _cd(dict(keep, **{"in_": 0, "out": m["duration"], "speed": 1})) if False else m["duration"], "speed": 1.0, "transform": {"x": 0, "y": 0, "scale": 1, "rotation": 0, "opacity": 1}, "audio": {"gain_db": 0, "linked": True}, "keyframes": {}, "color": {}, "rendered_from": cid, **keep}); save_project(proj)
    ev = log_event({"type": "ops", "actor": body.get("actor", "human"), "tool": "render_replace", "reason": f"baked {cid}", "ops": []}); await broadcast(ev); return {"media": mid, "path": out}

@app.get("/api/export/edl")
def export_edl(sequence: str = "seq1", track: str = "V1"):
    from fastapi.responses import PlainTextResponse; return PlainTextResponse(to_edl(load_project(), sequence, track), headers={"Content-Disposition": f"attachment; filename={sequence}_{track}.edl"})

@app.get("/api/captions/export_vtt")
def captions_export_vtt(sequence: str = "seq1"):
    from fastapi.responses import PlainTextResponse; proj = load_project(); seq = next(s for s in proj["sequences"] if s["id"] == sequence)
    return PlainTextResponse(captions_to_vtt(seq.get("captions") or []), media_type="text/vtt", headers={"Content-Disposition": f"attachment; filename={sequence}.vtt"})

@app.get("/api/app_events")
def app_events(limit: int = 100):
    """Events panel: render results, errors, offline media, agent activity — the latest N from the event log."""
    evs = []
    if os.path.exists(PP("events.jsonl")):
        for line in open(PP("events.jsonl")):
            try: e = json.loads(line)
            except Exception: continue
            if e.get("type") in ("render", "proposal", "proposal_decision", "media_added", "project_replaced", "snapshot") or (e.get("type") == "ops" and e.get("actor") == "agent"): evs.append({k: e.get(k) for k in ("ts", "type", "actor", "reason", "tool", "job", "source", "media", "title", "decision") if e.get(k) is not None})
    proj = load_project(); missing = [m["name"] for m in proj["media"].values() if not m.get("synthetic") and not os.path.exists(m["path"])]
    return {"events": evs[-limit:], "offline_media": missing, "queued_renders": sum(1 for j in JOBS.values() if j["status"] in ("queued", "running"))}

# ---------- audio sync (multicam) + markers export ----------
def _envelope(path, seconds=90, rate=50):
    """Mono RMS envelope at `rate` Hz over the first `seconds` (8 kHz PCM via ffmpeg, pure Python RMS)."""
    import struct, math as _m
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-t", str(seconds), "-i", path, "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"], capture_output=True, timeout=300)
    data = r.stdout; n = len(data) // 2; win = 8000 // rate; env = []
    for i in range(0, n - win, win):
        chunk = struct.unpack(f"<{win}h", data[i * 2:(i + win) * 2]); env.append(_m.sqrt(sum(x * x for x in chunk) / win))
    m = max(env) or 1; return [e / m for e in env], rate

@app.post("/api/audio/sync")
async def audio_sync(req: Request):
    """Offsets (seconds) that align each media's audio to the first one, by cross-correlating loudness envelopes. {media_ids:[…]}"""
    body = await req.json(); proj = load_project(); ids = body.get("media_ids", [])
    if len(ids) < 2: raise HTTPException(400, "need two or more media ids")
    envs = {}
    for mid in ids:
        m = proj["media"].get(mid)
        if not m or not m.get("has_audio"): envs[mid] = None; continue
        src = proj["media"][m["subclip_of"]] if m.get("subclip_of") else m; envs[mid] = await asyncio.to_thread(_envelope, src["path"])
    ref = envs[ids[0]]; out = {ids[0]: 0.0}
    if not ref: raise HTTPException(400, "reference clip has no audio")
    a, rate = ref; la = len(a)
    for mid in ids[1:]:
        e = envs.get(mid)
        if not e: out[mid] = None; continue
        b = e[0]; best, best_lag = -1e9, 0; maxlag = min(len(b), la) - 5
        for lag in range(-maxlag, maxlag):
            s_ = 0.0; cnt = 0
            for i in range(0, la, 2):
                j = i + lag
                if 0 <= j < len(b): s_ += a[i] * b[j]; cnt += 1
            if cnt > 20:
                s_ /= cnt
                if s_ > best: best, best_lag = s_, lag
        out[mid] = round(-best_lag / rate, 3)  # positive: this clip starts later than the reference
    return {"offsets": out, "rate": rate}

def _beats(path, seconds):
    env, rate = _envelope(path, seconds=seconds, rate=50); on = [max(0.0, env[i] - env[i - 1]) for i in range(1, len(env))]; best, bestlag = -1, int(rate * 60 / 120)
    for lag in range(int(rate * 60 / 180), int(rate * 60 / 60) + 1):
        s_ = sum(on[i] * on[i - lag] for i in range(lag, len(on)))
        if s_ > best: best, bestlag = s_, lag
    beat = bestlag / rate; bestp, bestv = 0.0, -1
    for k in range(int(beat * rate)):
        v = sum(on[i] for i in range(k, len(on), max(1, int(round(beat * rate)))))
        if v > bestv: bestv, bestp = v, k / rate
    return beat, bestp

@app.post("/api/audio/silences")
async def audio_silences(req: Request):
    """Silences in a media file's audio: {media_id, in?, out?, threshold_db (default -38), min_gap (s, default 0.45), pad (s, default 0.08)} →
    [{start, end}] in media time. Used by Sequence › Remove Silences (talking-head tightening) and by agents."""
    body = await req.json(); proj = load_project(); m = proj["media"].get(body["media_id"])
    if not m or not m.get("has_audio"): raise HTTPException(404)
    src = proj["media"][m["subclip_of"]] if m.get("subclip_of") else m; total = float(m["duration"]); thr = float(body.get("threshold_db", -38)); ming = float(body.get("min_gap", 0.45)); pad = float(body.get("pad", 0.08))
    env, rate = await asyncio.to_thread(_envelope, src["path"], min(3600, int(total) + 1), 50)
    import math as _m
    db = [20 * _m.log10(max(e, 1e-6)) for e in env]; ref = max(db) if db else 0; gaps = []; start = None
    for i, v in enumerate(db):
        quiet = v < ref + thr
        if quiet and start is None: start = i / rate
        elif not quiet and start is not None:
            if i / rate - start >= ming: gaps.append({"start": round(start + pad, 3), "end": round(i / rate - pad, 3)})
            start = None
    if start is not None and total - start >= ming: gaps.append({"start": round(start + pad, 3), "end": round(total, 3)})
    lo, hi = float(body.get("in", 0)), float(body.get("out", total)); gaps = [g for g in gaps if g["end"] > lo and g["start"] < hi and g["end"] - g["start"] > 0.05]
    return {"silences": gaps, "removed": round(sum(g["end"] - g["start"] for g in gaps), 2), "threshold_db": thr}

@app.post("/api/audio/beats")
async def audio_beats(req: Request):
    """Beat grid for a music item: {media_id, in?, out?} → {bpm, beats:[t…] (media time), downbeats:[t…]} — cut on the beat, or let the agent."""
    body = await req.json(); proj = load_project(); m = proj["media"].get(body["media_id"])
    if not m or not m.get("has_audio"): raise HTTPException(404)
    src = proj["media"][m["subclip_of"]] if m.get("subclip_of") else m; total = float(m["duration"])
    beat, phase = await asyncio.to_thread(_beats, src["path"], min(600, int(total) + 1))
    beats = []; t = phase
    while t < total: beats.append(round(t, 3)); t += beat
    return {"bpm": round(60 / beat, 1), "beat": round(beat, 4), "beats": beats, "downbeats": beats[::4]}

@app.post("/api/audio/remix")
async def audio_remix(req: Request):
    """Remix (music retarget): shorten/lengthen a music file to a target duration by removing or repeating whole bars found from the
    loudness envelope's tempo. {media_id, target, bars_per_phrase} → segments [{in, out}] to place back-to-back with crossfades."""
    body = await req.json(); proj = load_project(); m = proj["media"].get(body["media_id"])
    if not m or not m.get("has_audio"): raise HTTPException(404)
    src = proj["media"][m["subclip_of"]] if m.get("subclip_of") else m; target = float(body["target"]); total = float(m["duration"]); bpp = int(body.get("bars_per_phrase", 4))
    env, rate = await asyncio.to_thread(_envelope, src["path"], seconds=min(600, int(total) + 1), rate=50)
    # tempo: autocorrelation of the onset (positive envelope difference) between 60 and 180 BPM
    on = [max(0.0, env[i] - env[i - 1]) for i in range(1, len(env))]; best, bestlag = -1, int(rate * 60 / 120)
    for lag in range(int(rate * 60 / 180), int(rate * 60 / 60) + 1):
        s_ = sum(on[i] * on[i - lag] for i in range(lag, len(on)))
        if s_ > best: best, bestlag = s_, lag
    beat = bestlag / rate; bar = beat * 4; phrase = bar * bpp
    # downbeat phase: the offset (within one bar) whose beats hit the strongest onsets
    bestp, bestv = 0.0, -1
    for k in range(int(bar * rate)):
        v = sum(on[i] for i in range(k, len(on), max(1, int(round(beat * rate)))))
        if v > bestv: bestv, bestp = v, k / rate
    if target >= total - 0.05:  # lengthen: repeat the middle phrases
        need = target - total; segs = [{"in": 0.0, "out": total}]; loop_in = bestp + phrase * max(1, int(((total - bestp) / phrase) // 3)); loop_out = min(total, loop_in + phrase)
        while need > 0.2 and loop_out > loop_in + 0.5: segs.insert(1, {"in": loop_in, "out": loop_out}); need -= (loop_out - loop_in)
        return {"segments": segs, "bpm": round(60 / beat, 1), "bar": round(bar, 3), "phrase": round(phrase, 3), "downbeat": round(bestp, 3), "achieved": round(sum(sg["out"] - sg["in"] for sg in segs), 3)}
    cut = total - target; n_phr = max(1, int(round(cut / phrase))); remove = n_phr * phrase
    if remove > total * 0.8: remove = phrase * max(1, int((total * 0.8) // phrase))
    # remove whole phrases from the middle third, keeping the intro and the outro intact
    start_rm = bestp + phrase * max(1, int(((total - bestp) / phrase) // 3)); end_rm = min(total - phrase, start_rm + remove)
    segs = [{"in": 0.0, "out": round(start_rm, 3)}, {"in": round(end_rm, 3), "out": total}]
    return {"segments": segs, "bpm": round(60 / beat, 1), "bar": round(bar, 3), "phrase": round(phrase, 3), "downbeat": round(bestp, 3), "achieved": round(sum(sg["out"] - sg["in"] for sg in segs), 3)}

@app.get("/api/markers/export")
def markers_export(sequence: str = "seq1", fmt: str = "chapters"):
    from fastapi.responses import PlainTextResponse
    proj = load_project(); seq = next(s for s in proj["sequences"] if s["id"] == sequence); ms = sorted(seq.get("markers") or [], key=lambda m: m["time"])
    def tc(t): return f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}"
    if fmt == "csv": txt = "time,duration,type,color,name\n" + "\n".join(f"{m['time']:.3f},{m.get('duration', 0)},{m.get('type', 'comment')},{m.get('color', '')},\"{(m.get('name') or '').replace(chr(34), chr(39))}\"" for m in ms)
    else: txt = "\n".join(f"{tc(m['time'])} {m.get('name') or 'Chapter'}" for m in ms)
    return PlainTextResponse(txt, headers={"Content-Disposition": f"attachment; filename={sequence}_markers.{ 'csv' if fmt == 'csv' else 'txt'}"})

# ---------- media browser / subclips / audio measurement ----------
MEDIA_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mts", ".m2ts", ".ts", ".mxf", ".wmv", ".flv", ".3gp", ".mpg", ".mpeg", ".vob", ".dv", ".r3d", ".braw", ".y4m", ".ogv", ".hevc", ".h264", ".264", ".265",
             ".wav", ".mp3", ".aac", ".m4a", ".flac", ".ogg", ".oga", ".opus", ".aif", ".aiff", ".caf", ".wma", ".ac3", ".dts", ".ape", ".amr",
             ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".gif", ".heic", ".heif", ".avif", ".jxl", ".psd", ".exr", ".dpx", ".svg"}
@app.get("/api/fs")
def fs_list(path: str = ""):
    """Media Browser: list folders and media files of a directory on the workstation (home by default)."""
    path = os.path.abspath(os.path.expanduser(path or "~"))
    if not os.path.isdir(path): raise HTTPException(404)
    dirs, files = [], []
    try:
        for name in sorted(os.listdir(path), key=str.lower):
            if name.startswith("."): continue
            full = os.path.join(path, name)
            if os.path.isdir(full): dirs.append(name)
            elif os.path.splitext(name)[1].lower() in MEDIA_EXT: files.append({"name": name, "size": os.path.getsize(full), "mtime": os.path.getmtime(full)})
    except PermissionError: raise HTTPException(403)
    proj = load_project(); imported = {os.path.abspath(m["path"]) for m in proj["media"].values()}
    for f in files: f["imported"] = os.path.join(path, f["name"]) in imported
    return {"path": path, "parent": os.path.dirname(path) if os.path.dirname(path) != path else None, "dirs": dirs, "files": files, "roots": [os.path.expanduser("~")] + ([d + ":\\" for d in "CDEFG" if os.path.exists(d + ":\\")] if os.name == "nt" else ["/"])}

@app.post("/api/media/subclip")
async def media_subclip(req: Request):
    body = await req.json()
    with LOCK:
        proj = load_project(); m = proj["media"].get(body["media_id"])
        if not m: raise HTTPException(404)
        i, o = float(body["in"]), float(body["out"])
        if o <= i: raise HTTPException(400, "out must be after in")
        sid = str(uuid.uuid4())[:8]; sub = {**m, "id": sid, "name": body.get("name") or f"{m['name']}.sub{len([x for x in proj['media'].values() if x.get('subclip_of') == m['id']]) + 1}", "subclip_of": m["id"], "sub_in": i, "duration": o - i, "added": time.time()}
        proj["media"][sid] = sub; save_project(proj)
    ev = log_event({"type": "media_added", "actor": body.get("actor", "human"), "media": [sid], "subclip": [body["media_id"], i, o]}); await broadcast(ev); return sub

@app.post("/api/audio/measure")
async def audio_measure(req: Request):
    """Integrated loudness / true peak of a media range (for auto-match). {media_id, in, out}"""
    body = await req.json(); proj = load_project(); m = proj["media"].get(body["media_id"])
    if not m or not m.get("has_audio"): raise HTTPException(404)
    src = proj["media"][m["subclip_of"]] if m.get("subclip_of") else m; off = float(m.get("sub_in", 0) or 0)
    i, o = float(body.get("in", 0)) + off, float(body.get("out", m["duration"])) + off
    r = (await asyncio.to_thread(subprocess.run, ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{i:.3f}", "-t", f"{max(o - i, 0.1):.3f}", "-i", src["path"], "-vn", "-filter_complex", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True, timeout=300)).stderr
    import re as _re; tail = r.split("Summary:")[-1]
    lufs = _re.search(r"I:\s+(-?[\d.]+) LUFS", tail); tp = _re.search(r"Peak:\s+(-?[\d.]+) dBFS", tail)
    return {"integrated_lufs": float(lufs.group(1)) if lufs else None, "true_peak_dbtp": float(tp.group(1)) if tp else None}

# ---------- collaboration data: client events, session summary, training export ----------
@app.post("/api/events")
async def events_post(req: Request):
    """Client-side telemetry (playback ranges, panel focus, review passes). Body: {type, actor, ...}"""
    body = await req.json(); body.setdefault("actor", "human")
    if body.get("type") == "focus":  # presence: broadcast live, don't persist
        body.setdefault("ts", time.time()); await broadcast(body); return {"ok": True}
    ev = log_event(body)
    if body.get("type") != "playback": await broadcast(ev)
    return {"ok": True, "id": ev["id"]}

@app.get("/api/session/summary")
def session_summary(since: float = 0):
    """Compact digest for the agent: what the human did since `since`."""
    evs = [e for e in (json.loads(l) for l in open(PP("events.jsonl"))) if e.get("ts", 0) > since] if os.path.exists(PP("events.jsonl")) else []
    by_tool, clips, reviewed, decisions, annotations = {}, {}, [], [], []
    for e in evs:
        if e.get("type") == "ops" and e.get("actor") == "human":
            by_tool[e.get("tool") or "?"] = by_tool.get(e.get("tool") or "?", 0) + 1
            for o in e.get("ops", []):
                cid = o.get("clip", {}).get("id") if o.get("op") == "set_clip" else o.get("clip_id")
                if cid: clips[cid] = clips.get(cid, 0) + 1
        elif e.get("type") == "playback": reviewed.append([round(e.get("start", 0), 2), round(e.get("end", 0), 2)])
        elif e.get("type") == "project_replaced" and str(e.get("source", "")).startswith(("undo", "redo", "history")): by_tool["undo/redo"] = by_tool.get("undo/redo", 0) + 1
        elif e.get("type") == "proposal_decision": decisions.append({"decision": e.get("decision"), "reason_agent": e.get("reason_agent"), "reasons": e.get("reasons"), "note": e.get("note")})
        elif e.get("type") == "annotation": annotations.append({"label": e.get("label"), "reasons": e.get("reasons"), "note": e.get("note"), "target": e.get("target")})
    proj = load_project(); heat = {}
    for a, b in reviewed:
        for sec in range(int(a), int(b) + 1): heat[sec] = heat.get(sec, 0) + 1
    return {"since": since, "now": time.time(), "n_events": len(evs), "human_ops_by_tool": by_tool, "clips_touched": clips, "reviewed_ranges": reviewed[-50:], "review_heat_by_second": heat, "proposal_decisions": decisions, "annotations": annotations,
            "project": {"name": proj.get("name"), "brief": proj.get("brief"), "sequences": [{"id": s["id"], "name": s["name"], "duration": max([c["start"] + (c["out"] - c["in_"]) / max(c.get("speed", 1), 1e-6) for t in s["tracks"] for c in t["clips"]] + [0])} for s in proj["sequences"]]}}

@app.get("/api/training/export")
def training_export():
    """One dataset bundle (JSONL) for the training pipeline: meta+brief, events, edit pairs, proposal decisions, annotations, snapshot refs."""
    os.makedirs(PP("training"), exist_ok=True); out = PP("training", f"dataset_{int(time.time())}.jsonl"); n = 0; proj = load_project()
    with open(out, "w") as w:
        w.write(json.dumps({"kind": "meta", "schema": 2, "exported": time.time(), "project": proj.get("name"), "brief": proj.get("brief"), "sequences": [s["id"] for s in proj["sequences"]]}) + "\n")
        for src, kind in (("events.jsonl", "event"), (os.path.join("training", "edit_pairs.jsonl"), "edit_pair"), (os.path.join("training", "proposal_decisions.jsonl"), "proposal_decision")):
            if os.path.exists(PP(src)):
                for line in open(PP(src)):
                    line = line.strip()
                    if line: w.write(json.dumps({"kind": kind, **json.loads(line)}) + "\n"); n += 1
        for a in proj.get("annotations", []): w.write(json.dumps({"kind": "annotation", **a}) + "\n"); n += 1
        for sn in (sorted(os.listdir(PP("snapshots"))) if os.path.isdir(PP("snapshots")) else []): w.write(json.dumps({"kind": "snapshot_ref", "file": sn}) + "\n")
    return {"file": out, "records": n}

@app.post("/api/render")
async def render_job(req: Request):
    """{sequence, preset, name, actor, outputs?:[{suffix, width, height, fit}]} — with outputs, one job per derived output (center-crop or pad)."""
    body = await req.json(); seq_id = body.get("sequence", "seq1"); preset = body.get("preset", {}); name = body.get("name") or f"render_{int(time.time())}"
    proj = copy.deepcopy(load_project()); outs = body.get("outputs")
    if not outs: return start_render(proj, seq_id, preset, name, body.get("actor", "human"))
    jobs = []
    for o in outs:
        pr = dict(preset); pr.update({"out_w": o.get("width"), "out_h": o.get("height"), "fit": o.get("fit", "crop")})
        jobs.append(start_render(proj, seq_id, pr, f"{name}_{o.get('suffix') or (str(o.get('width')) + 'x' + str(o.get('height')))}", body.get("actor", "human")))
    return {"jobs": jobs}

@app.get("/api/jobs")
def jobs_list(): return sorted(JOBS.values(), key=lambda j: j["started"], reverse=True)[:50]

@app.get("/api/encoders")
def encoders():
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    avail = [e for e in ("libx264", "h264_nvenc", "h264_videotoolbox", "h264_qsv", "h264_vaapi", "h264_amf", "libx265", "hevc_nvenc", "hevc_videotoolbox") if f" {e} " in out]
    return {"encoders": avail}

@app.get("/api/settings")
def settings_get(): p = P("settings.json"); return json.load(open(p)) if os.path.exists(p) else {}

@app.put("/api/settings")
async def settings_put(req: Request):
    """Merge (shallow) into the workstation settings so independent writers (keymap, prefs, layouts, presets) don't clobber each other."""
    body = await req.json(); cur = {}
    if os.path.exists(P("settings.json")):
        try: cur = json.load(open(P("settings.json")))
        except Exception: cur = {}
    cur.update(body or {}); json.dump(cur, open(P("settings.json"), "w"), indent=1); return cur

@app.post("/api/import/fcpxml")
async def import_fcpxml(req: Request):
    body = await req.json()
    with LOCK:
        proj = load_project()
        def imp(path, name):
            if not path or not os.path.exists(path): return None
            for mid, m in proj["media"].items():
                if os.path.abspath(m["path"]) == os.path.abspath(path): return mid
            mid, m = ingest(path, name); proj["media"][mid] = m; return mid
        seqs = from_fcp7_xml(body["xml"], imp); proj["sequences"].extend(seqs); save_project(proj)
    ev = log_event({"type": "project_replaced", "actor": body.get("actor", "human"), "source": "fcpxml_import", "sequences": [s["id"] for s in seqs]}); await broadcast(ev)
    return {"sequences": [{"id": s["id"], "name": s["name"], "clips": sum(len(t["clips"]) for t in s["tracks"])} for s in seqs]}

@app.get("/api/render/{jid}")
def render_status(jid: str): return JOBS.get(jid) or HTTPException(404)

@app.get("/api/render_command")
def render_command(sequence: str = "seq1"):
    cmd, graph = build_command(load_project(), sequence, P("renders", "preview.mp4")); return {"cmd": cmd, "graph": graph}

# ---------- proposals (agent proposes; human accepts/rejects each; every decision is a labeled training event) ----------
@app.get("/api/proposals")
def proposals_list(): return load_project().get("proposals", [])

@app.post("/api/proposals")
async def proposals_add(req: Request):
    body = await req.json()  # {items:[{ops:[...], reason, tool}], actor:"agent", title}
    proj0 = load_project()
    for it in body.get("items", []):
        problems = validate_ops(proj0, it.get("ops", []))
        if problems: return JSONResponse({"ok": False, "errors": problems, "item": it.get("reason")}, status_code=422)
        try:  # record the advisor's prediction alongside the proposal so decisions can be scored against it later
            advisor_load()
            if ADVISOR["model"] and ADVISOR["model"].n:
                seq = next((x for x in proj0["sequences"] if x["id"] == (it.get("ops") or [{}])[0].get("sequence")), None); sd = max([c["start"] + (c["out"] - c["in_"]) / max(c.get("speed", 1), 1e-6) for t in seq["tracks"] for c in t["clips"]] + [0]) if seq else 0
                ps = []
                for op in it.get("ops", []):
                    tr = next((t for t in (seq["tracks"] if seq else []) if t["id"] == op.get("track")), None); cur = None
                    if tr: cid = op.get("clip", {}).get("id") if op.get("op") == "set_clip" else op.get("clip_id"); cur = next((c for c in tr["clips"] if c["id"] == cid), None)
                    ps.append(ADVISOR["model"].predict(_advisor.op_features(op, {"clip": cur or {}, "seq_duration": sd, "reason": it.get("reason"), "brief": proj0.get("brief"), "track_kind": tr["kind"] if tr else None})))
                if ps: it["advisor_p"] = round(sum(ps) / len(ps), 3); it["advisor_n"] = ADVISOR["model"].n
        except Exception: pass
    with LOCK:
        proj = load_project(); pr = {"id": str(uuid.uuid4())[:8], "ts": time.time(), "actor": body.get("actor", "agent"), "title": body.get("title", "Agent proposal"),
              "items": [{"id": str(uuid.uuid4())[:8], "ops": it["ops"], "reason": it.get("reason", ""), "tool": it.get("tool", "agent"), "status": "pending"} for it in body.get("items", [])]}
        proj.setdefault("proposals", []).append(pr); save_project(proj)
    ev = log_event({"type": "proposal", "actor": pr["actor"], "proposal": pr["id"], "n_items": len(pr["items"]), "title": pr["title"]}); await broadcast(ev); return pr

@app.post("/api/proposals/{pid}/{iid}/{decision}")
async def proposal_decide(pid: str, iid: str, decision: str, req: Request):
    body = await req.json() if req.headers.get("content-length", "0") not in ("0", "") else {}
    with LOCK:
        proj = load_project(); pr = next((p for p in proj.get("proposals", []) if p["id"] == pid), None)
        if not pr: raise HTTPException(404)
        it = next((i for i in pr["items"] if i["id"] == iid), None)
        if not it: raise HTTPException(404)
        befores = apply_ops(proj, it["ops"]) if decision == "accept" else []
        it["status"] = decision; it["decided_ts"] = time.time(); it["note"] = body.get("note", ""); it["reasons"] = body.get("reasons", []); save_project(proj)
    ev = log_event({"type": "proposal_decision", "actor": "human", "proposal": pid, "item": iid, "decision": decision, "reason_agent": it["reason"], "reasons": it["reasons"], "note": it["note"], "ops": it["ops"], "befores": befores, "advisor_p": it.get("advisor_p"), "advisor_n": it.get("advisor_n")})
    os.makedirs(PP("training"), exist_ok=True)
    with open(PP("training", "proposal_decisions.jsonl"), "a") as f: f.write(json.dumps({k: v for k, v in ev.items() if k != "befores"}) + "\n")
    await broadcast({k: v for k, v in ev.items() if k not in ("befores",)}); return it

# ---------- captions / interchange / frames ----------
@app.post("/api/captions/import")
async def captions_import(req: Request):
    body = await req.json(); caps = srt_to_captions(body["srt"]); seq_id = body.get("sequence", "seq1")
    with LOCK:
        proj = load_project(); seq = next(s for s in proj["sequences"] if s["id"] == seq_id); seq["captions"] = (seq.get("captions") or []) + caps if body.get("append") else caps; save_project(proj)
    ev = log_event({"type": "ops", "actor": body.get("actor", "human"), "tool": "captions_import", "reason": f"{len(caps)} captions", "ops": []}); await broadcast(ev); return {"count": len(caps)}

@app.get("/api/captions/export")
def captions_export(sequence: str = "seq1"):
    proj = load_project(); seq = next(s for s in proj["sequences"] if s["id"] == sequence)
    from fastapi.responses import PlainTextResponse; return PlainTextResponse(captions_to_srt(seq.get("captions") or []), media_type="text/plain")

@app.get("/api/export/fcpxml")
def export_fcpxml(sequence: str = "seq1"):
    from fastapi.responses import Response; return Response(to_fcp7_xml(load_project(), sequence), media_type="application/xml", headers={"Content-Disposition": f"attachment; filename={sequence}.xml"})

@app.get("/api/export/otio")
def export_otio(sequence: str = "seq1"):
    return JSONResponse(to_otio(load_project(), sequence), headers={"Content-Disposition": f"attachment; filename={sequence}.otio"})

@app.get("/api/frame")
def frame(sequence: str = "seq1", t: float = 0.0):
    os.makedirs(P("renders"), exist_ok=True); out = P("renders", f"frame_{sequence}_{t:.3f}.png".replace(".", "_", 1))
    render_frame(load_project(), sequence, t, out); return FileResponse(out, media_type="image/png")

@app.get("/api/snapshots")
def snapshots_list():
    d = PP("snapshots")
    if not os.path.isdir(d): return []
    out = []
    for f in sorted(os.listdir(d)):
        try: ts = float(f.split("_")[0])
        except ValueError: ts = os.path.getmtime(os.path.join(d, f))
        out.append({"file": f, "ts": ts, "label": f.split("_", 1)[1].rsplit(".", 1)[0] if "_" in f else f})
    return out

@app.get("/api/snapshots/get")
def snapshots_get(file: str):
    f = PP("snapshots", os.path.basename(file))
    if not os.path.exists(f): raise HTTPException(404)
    return json.load(open(f))

@app.post("/api/snapshots/restore")
async def snapshots_restore(req: Request):
    body = await req.json(); p = PP("snapshots", os.path.basename(body["name"]))
    if not os.path.exists(p): raise HTTPException(404)
    with LOCK: proj = json.load(open(p)); save_project(proj)
    ev = log_event({"type": "project_replaced", "actor": "human", "source": "restore:" + body["name"]}); await broadcast(ev); return {"ok": True}

@app.websocket("/ws")
async def ws(websocket: WebSocket):
    tok = TOKEN["value"]
    if tok and (websocket.query_params.get("token") != tok and websocket.cookies.get("filmocity_token") != tok): await websocket.close(code=4401); return
    await websocket.accept(); CLIENTS.append(websocket)
    try:
        while True: await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in CLIENTS: CLIENTS.remove(websocket)

def mount():
    for d in ("thumbs", "renders", "media", "proxies", "projects"): os.makedirs(P(d), exist_ok=True)
    migrate_legacy()
    os.makedirs(P("fonts"), exist_ok=True); app.mount("/fonts", StaticFiles(directory=P("fonts")), name="fonts"); app.mount("/thumbs", StaticFiles(directory=P("thumbs")), name="thumbs"); app.mount("/renders", StaticFiles(directory=P("renders")), name="renders"); app.mount("/proxies", StaticFiles(directory=P("proxies")), name="proxies")
    app.mount("/static", StaticFiles(directory=FRONT), name="static")
    if os.path.isdir(ASSETS): app.mount("/assets", StaticFiles(directory=ASSETS), name="assets")
    if os.path.isdir(DOCS): app.mount("/docs", StaticFiles(directory=DOCS, html=True), name="docs")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--root", default=ROOT); ap.add_argument("--port", type=int, default=8787); ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--token", default=None, help="require this access token (use with --host 0.0.0.0 to share on a LAN)")
    a = ap.parse_args(); ROOT = a.root; os.makedirs(ROOT, exist_ok=True); os.environ["FILMOCITY_DATA"] = os.path.abspath(ROOT); mount()
    if a.token: TOKEN["value"] = a.token; print(f"access token set — open http://{a.host}:{a.port}/?token={a.token}")
    elif a.host not in ("127.0.0.1", "localhost"): TOKEN["value"] = uuid.uuid4().hex[:16]; print(f"LAN mode: generated access token — open http://<this-machine>:{a.port}/?token={TOKEN['value']}")
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
else:
    os.makedirs(ROOT, exist_ok=True); mount()

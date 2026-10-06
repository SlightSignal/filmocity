"""Filmocity backend — local FastAPI server: media ingest, project store (open JSON EDL), event log, snapshots/diffs for
training data, render jobs (FFmpeg), agent API (REST + WebSocket), and static hosting of the front end.

Run:  cd tools/filmocity && python3 backend/server.py --root ~/filmocity_data --port 8787
Then open http://localhost:8787  (the agent uses the same URL; see docs/AGENT_API.md)
"""
import os, sys, json, time, uuid, shutil, subprocess, threading, argparse, copy, mimetypes
from typing import Any, Optional
import asyncio, hashlib, math
import subprocesses as subprocess
from fastapi import FastAPI, Request, UploadFile, File, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
import uvicorn
sys.path.insert(0, os.path.dirname(__file__))
from render import render as do_render, build_command, render_frame, render_incremental, segment_boundaries, chunk_sequence, index_sequence, chunk_key, png_sequence_directory, seq_total
from render_context import RenderContext
from preview_binding import preview_binding, PREVIEW_PRESET
from interchange import to_fcp7_xml, to_otio, captions_to_srt, srt_to_captions, from_fcp7_xml, to_edl, captions_to_vtt
from effects import catalog as effects_catalog
import advisor as _advisor
from preflight import inspect_resources, require_resources, ResourceError, media_online
from media_collection import MediaCollection, MediaCollectionError
from project_sync import project_context, matches_context, workspace_id
from runtime_identity import read_build_info, REQUEST_SHUTDOWN_GRACE
from workspace_lock import hold_workspace
from background_tasks import TaskManager, TaskError
import task_inputs
import media_preparation
from project_versions import read_version, list_versions, write_snapshot
from project_transaction import commit_pair, recover_transaction, TransactionRecoveryRequired
from project_history import changes_between, apply_changes, HistoryConflict
from proposal_preview import PreviewStore, PreviewUnavailable
from project_recovery import (ProjectRecoveryRequired, RecoveryError, RecoveryConflict,
                              parse_project, has_recovery_files, inspect_recovery, restore_version, preserve_editor_draft)

HERE = os.path.dirname(os.path.abspath(__file__)); FRONT = os.path.join(os.path.dirname(HERE), "frontend"); ASSETS = os.path.join(os.path.dirname(HERE), "assets"); DOCS = os.path.join(os.path.dirname(HERE), "docs")
ROOT = os.environ.get("FILMOCITY_ROOT", os.path.expanduser("~/filmocity_data"))
def P(*a): return os.path.join(ROOT, *a)

VERSION = "0.47.0-rc.1"
INSTANCE_ID = os.environ.get("FILMOCITY_LAUNCH_ID") or uuid.uuid4().hex
BUILD_INFO = read_build_info(os.path.dirname(HERE))
app = FastAPI(title="Filmocity", version=VERSION, docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
TASKS = None
LOCK = threading.Lock(); CLIENTS: list[WebSocket] = []; JOBS: dict[str, dict] = {}; OPS_SINCE_AUTOSAVE = 0

# ---------- project store ----------
def active_id():
    p = P("active.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as stream: return json.load(stream).get("id") or "default"
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
    recover_transaction(p)
    if not os.path.exists(p):
        if has_recovery_files(p):
            raise ProjectRecoveryRequired("Project file is missing; saved recovery versions may be available.")
        save_project(default_project())
    try:
        with open(p, "rb") as stream: proj = parse_project(stream.read())
    except RecoveryError as error:
        raise ProjectRecoveryRequired(str(error)) from error
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

def save_project(proj, project_file=None, *, updated=None, protected_backup=None):
    proj["updated"] = time.time() if updated is None else updated; proj.setdefault("version", SCHEMA)
    cur = project_file or PP("project.json"); tmp = cur + ".tmp"
    # Serialize first so an invalid edit cannot truncate a recoverable temp file.
    payload = json.dumps(proj, indent=1, allow_nan=False)
    with open(tmp, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(payload); stream.flush(); os.fsync(stream.fileno())
    if os.path.exists(cur):  # rolling backups: one per minute at most, keep the last 30
        import re
        bdir = os.path.join(os.path.dirname(cur), "backups"); os.makedirs(bdir, exist_ok=True)
        backups = sorted((f for f in os.listdir(bdir) if re.fullmatch(r"project_\d+\.json", f)), key=lambda f: int(f[8:-5])); last = backups[-1:]
        if not last or time.time() - float(last[0].split("_")[1].split(".")[0]) > 60:
            shutil.copy2(cur, os.path.join(bdir, f"project_{int(time.time())}.json"))
            backups = sorted((f for f in os.listdir(bdir) if re.fullmatch(r"project_\d+\.json", f)), key=lambda f: int(f[8:-5]))
            for old in backups[:-30]:
                if old != protected_backup: os.remove(os.path.join(bdir, old))
    # Windows readers/virus scanners can briefly open the destination without
    # delete sharing. Keep the previous project intact and retry only that
    # bounded permission/sharing condition; other failures remain immediate.
    for attempt in range(6):
        try:
            os.replace(tmp, cur)
            break
        except PermissionError as error:
            if getattr(error, "winerror", None) not in (5, 32, 33) or attempt == 5:
                raise
            time.sleep(min(0.02 * (2 ** attempt), 0.2))

def log_event(ev, project_id=None):
    ev.setdefault("ts", time.time()); ev.setdefault("id", str(uuid.uuid4())[:8]); ev.setdefault("project", project_id if project_id is not None else active_id())
    event_file = P("projects", project_id, "events.jsonl") if project_id is not None else PP("events.jsonl")
    with open(event_file, "a", encoding="utf-8") as f: f.write(json.dumps(ev) + "\n")
    return ev

TOKEN = {"value": os.environ.get("FILMOCITY_TOKEN") or None}
from request_access import access_error, same_token
@app.middleware("http")
async def _auth(request: Request, call_next):
    tok = TOKEN["value"]
    denied = access_error(request, tok)
    if denied: return JSONResponse({"error": denied[1]}, status_code=denied[0])
    resp = await call_next(request)
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    if tok and same_token(request.query_params.get("token"), tok):
        resp.set_cookie("filmocity_token", tok, httponly=True, samesite="strict", secure=request.url.scheme == "https")
    if tok: resp.headers["Cache-Control"] = "private, no-store"
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
    parts = [p.replace("~1", "/").replace("~0", "~") for p in path.strip("/").split("/") if p != ""]
    for p in parts[:-1]:
        obj = obj[int(p)] if isinstance(obj, list) else obj[p]
    return obj, (parts[-1] if parts else None)

def validate_ops(proj, ops):
    """Reject malformed ops before they touch the project: unknown op, missing sequence/track, negative or inverted ranges, unknown media."""
    if not isinstance(ops, list) or any(not isinstance(op, dict) for op in ops): return ["Operations must be a list of objects"]
    seqs = {sq["id"]: sq for sq in proj["sequences"]}; problems = []
    for i, o in enumerate(ops):
        k = o.get("op")
        if k == "set_mix":
            import mixer_edit
            try: mixer_edit.validate(proj, o)
            except (ValueError, TypeError, KeyError) as error: problems.append(f"op[{i}]: {error}")
            continue
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
                if m and not m.get("is_image"):
                    # A held clip stores its timeline length in out-in; only in_
                    # selects a source frame. Its duration may exceed the source.
                    if merged.get("hold"):
                        held = merged.get("in_")
                        if isinstance(held, (int, float)) and held >= float(m.get("duration", 1e9)):
                            problems.append(f"op[{i}]: held frame ({held}) must be before media end ({m.get('duration')})")
                    elif isinstance(merged.get("out"), (int, float)) and merged["out"] > float(m.get("duration", 1e9)) + 0.05:
                        problems.append(f"op[{i}]: out ({merged['out']}) exceeds media duration ({m.get('duration')})")
    return problems

def inverse_ops(ops, befores, proj_after):
    """Exact inverses for a group of ops given the before-states captured when they were applied."""
    inv = []
    for o, b in reversed(list(zip(ops, befores))):
        k = o.get("op")
        if k == "set_mix":
            import mixer_edit
            inv.extend(mixer_edit.inverse(proj_after, o, b)); continue
        if k == "set_clip": inv.append({"op": "set_clip", "sequence": o["sequence"], "track": o["track"], "clip": b} if b else {"op": "remove_clip", "sequence": o["sequence"], "track": o["track"], "clip_id": o["clip"]["id"]})
        elif k == "remove_clip":
            if b: inv.append({"op": "set_clip", "sequence": o["sequence"], "track": o["track"], "clip": b})
        elif k == "set": inv.append({"op": "set", "path": o["path"], "value": b} if b is not None else {"op": "remove", "path": o["path"]})
        elif k == "insert": inv.append({"op": "remove", "path": o["path"]})
        elif k == "remove": inv.append({"op": "insert", "path": o["path"], "value": b})
    # a set_clip that restores a clip must restore the *whole* clip, so drop partial merges by re-setting from the before copy (already whole)
    return inv

def undo_stack_path(): return PP("undo_stack.json")
def read_undo_history(pid):
    path = P("projects", pid, "undo_stack.json")
    if not os.path.exists(path): return {"undo": [], "redo": []}
    try:
        with open(path, encoding="utf-8") as stream: history = json.load(stream)
        if not isinstance(history, dict) or any(not isinstance(history.get(key), list) or any(not isinstance(item, dict) for item in history[key]) for key in ("undo", "redo")):
            raise ValueError("Invalid undo/redo records")
        return history
    except (ValueError, TypeError, UnicodeError) as error:
        raise TransactionRecoveryRequired("Undo history is unreadable. Open Recovery to preserve it and choose a project version.") from error


def commit_project_history(proj, history, pid, *, protected_backup=None):
    proj["updated"] = time.time(); proj.setdefault("version", SCHEMA)
    project_raw = json.dumps(proj, indent=1, allow_nan=False).encode("utf-8")
    parse_project(project_raw)
    history_raw = json.dumps(history, allow_nan=False).encode("utf-8")
    path = P("projects", pid, "project.json")
    options = {"protected_backup": protected_backup} if protected_backup else {}
    return commit_pair(path, project_raw, history_raw, lambda: save_project(proj, path, updated=proj["updated"], **options))


def commit_edit(before, after, pid, entry=None, *, protected_backup=None):
    if after.get("version", 1) < SCHEMA: migrate_project(after)
    history = read_undo_history(pid)
    changes = changes_between(before, after)
    if entry and entry.get("tool") in ("workflow_relink", "workflow_interpret", "collect"):
        from editing_workflow import relink_history_changes
        changes = relink_history_changes(before, after, changes)
    if changes:
        history["redo"] = []
        if entry is not None:
            history["undo"] = (history["undo"] + [{**entry, "changes": changes}])[-200:]
    return commit_project_history(after, history, pid, protected_backup=protected_backup)


def normalize_tracks(proj):
    """Atomically resolve overlaps using original list priority and source clocks."""
    from overlap_normalization import normalize_tracks as normalize
    try: return normalize(proj)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
        raise HTTPException(422, str(error)) from error

def apply_ops(proj, ops):
    """ops: [{op:'set'|'insert'|'remove'|'replace_clip'|'set_clip', path, value}]; paths are JSON-pointer-like.
    Convenience: {op:'set_clip', sequence, track, clip:{...}} upserts a clip by id; {op:'remove_clip', sequence, track, clip_id}."""
    befores = []
    for o in ops:
        if o["op"] == "set_mix":
            import mixer_edit
            try: befores.append(mixer_edit.apply(proj, o))
            except (ValueError, TypeError, KeyError) as error: raise HTTPException(422, str(error)) from error
            continue
        if o["op"] in ("set_clip", "remove_clip"):
            seq = next(s for s in proj["sequences"] if s["id"] == o["sequence"]); tr = next(t for t in seq["tracks"] if t["id"] == o["track"])
            if o["op"] == "set_clip":
                c = o["clip"]; c.setdefault("id", str(uuid.uuid4())[:8])
                old = next((x for x in tr["clips"] if x["id"] == c["id"]), None); befores.append(copy.deepcopy(old))
                if old:
                    window = old.get("source_edit_window")
                    if "source_edit_window" not in c and isinstance(window, dict) and window.get("version") == 1:
                        window = copy.deepcopy(window)
                        if "time_remap" in c and c["time_remap"] != old.get("time_remap"): window.pop("ramp", None)
                        if "keyframes" in c:
                            incoming = c.get("keyframes"); prior = old.get("keyframes")
                            if (incoming.get("audio.duck_db") if isinstance(incoming, dict) else None) != (prior.get("audio.duck_db") if isinstance(prior, dict) else None): window.pop("duck", None)
                        old["source_edit_window"] = window
                    old.update(c)
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
    from media_metadata import summarize
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path], capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired as error: raise ValueError("Media inspection timed out") from error
    if result.returncode: raise ValueError("Could not inspect media: " + (result.stderr or "ffprobe failed")[-400:])
    try: document = json.loads(result.stdout)
    except (ValueError, TypeError) as error: raise ValueError("Media inspection returned invalid metadata") from error
    if not isinstance(document, dict): raise ValueError("Media inspection returned invalid metadata")
    return summarize(document)

def ingest(path, name=None):
    """Fast path: probe only (≈50 ms) so imports return immediately; thumbnails, filmstrip, waveform and proxy are produced in the background
    and announced with a `media_ready` event. Media carry status: 'ingesting' → 'ready'."""
    mid = str(uuid.uuid4())[:8]; info = probe(path); os.makedirs(P("thumbs"), exist_ok=True)
    token = uuid.uuid4().hex[:12]
    m = {"id": mid, "name": name or os.path.basename(path), "path": os.path.abspath(path), **info, "thumb": None, "strip": None, "wave": None, "status": "ingesting", "added": time.time(), "ingest_token": token}
    return mid, m

def _update_ingested_media(mid, path, update, project_file, token, source_basis=None):
    """Late ingest results belong to the captured project and source generation."""
    with LOCK:
        if not os.path.isfile(project_file): return False
        recover_transaction(project_file)
        with open(project_file, encoding="utf-8") as stream: proj = json.load(stream)
        media = proj.get("media", {}).get(mid)
        if not media or media.get("ingest_token") != token: return False
        effective = media
        if source_basis is not None:
            import source_commands
            try:
                effective = source_commands.alias_source(proj, media)
                if effective["audio_alias_basis"] != source_basis: return False
            except (ValueError, KeyError, TypeError): return False
        if os.path.abspath(effective.get("path", "")) != os.path.abspath(path): return False
        history_path = media.get("path", path)
        media.update(update)
        if media.get("workflow_import"):
            from editing_workflow import update_import_history
            pid = os.path.basename(os.path.dirname(project_file))
            history = update_import_history(read_undo_history(pid), mid, history_path, token, update)
            commit_project_history(proj, history, pid)
        else: save_project(proj, project_file)
    if os.path.abspath(project_file) == os.path.abspath(PP("project.json")):
        broadcast_threadsafe({"type": "media_ready", "media": [mid], "ts": time.time()})
    return True

def _task_media_current(payload):
    with LOCK:
        try:
            recover_transaction(payload["project_file"])
            with open(payload["project_file"], encoding="utf-8") as stream: proj = json.load(stream)
            media = proj.get("media", {}).get(payload["media_id"])
            effective = media
            if media and media.get("audio_alias"):
                import source_commands
                effective = source_commands.alias_source(proj, media)
                if effective["audio_alias_basis"] != payload["info"].get("audio_alias_basis"): return False
            return bool(media and media.get("ingest_token") == payload["token"] and os.path.abspath(effective["path"]) == os.path.abspath(payload["path"]))
        except (OSError, ValueError, KeyError): return False

def _task_media_update(payload, fields):
    return _update_ingested_media(payload["media_id"], payload["path"], fields, payload["project_file"], payload["token"], payload["info"].get("audio_alias_basis"))

def _task_prepare_media(payload, task):
    return media_preparation.prepare(ROOT, payload, task, _task_media_current, _task_media_update)

def finish_ingest(mid, path, info, project_file=None, token=None):
    """Queue preparation after registration; failure does not undo a saved import."""
    from background_tasks import MediaTaskBusy
    from proxy_media import settings as proxy_settings
    project_file = project_file or PP("project.json")
    payload = {"media_id": mid, "path": path, "info": {k:v for k,v in info.items() if k not in task_inputs.DERIVED},
               "project_file": project_file, "token": token}
    try:
        settings_path = P("settings.json")
        with open(settings_path, encoding="utf-8") as stream: prefs = json.load(stream).get("prefs") or {}
        payload["proxy_settings"] = proxy_settings(prefs.get("proxy"))
    except FileNotFoundError: payload["proxy_settings"] = proxy_settings()
    except (ValueError, TypeError, OSError, AttributeError) as error:
        _update_ingested_media(mid, path, {"ingest_error": "Cannot read proxy preferences: " + str(error)[:300], "status": "error"}, project_file, token)
        return {"error": "Cannot read proxy preferences: " + str(error)}
    try:
        payload["stamp"] = task_inputs.source_stamp({**info, "path": path})
        if info.get("source_relink_basis") is not None:
            from source_relink_io import check_accepted
            check_accepted({**info, "path": path})
        previous_info = info.get("proxy_info") or {}
        previous_source = previous_info.get("source_signature")
        accepted_alias_source = bool(info.get("audio_alias") and previous_info.get("audio_alias_source_generation") and previous_info["audio_alias_source_generation"] != info.get("audio_alias_source_generation"))
        if previous_source and not accepted_alias_source:
            from media_preview import digest
            if previous_source != digest(payload["stamp"]): raise TaskError("The source changed on disk. Relink it to inspect and accept its current timing and format before preparing a proxy.")
        if TASKS is None: raise TaskError("Background task service is unavailable")
        return TASKS.submit("media", info.get("name") or os.path.basename(path),
            {"workspace": workspace_id(ROOT), "project": os.path.basename(os.path.dirname(project_file))}, payload)
    except MediaTaskBusy as error: return {"error": str(error)}
    except Exception as error:
        _update_ingested_media(mid, path, {"status":"error", "ingest_error":"Could not queue preparation: " + str(error)[:300]}, project_file, token)
        return {"error": str(error)}

# ---------- API ----------
@app.exception_handler(TransactionRecoveryRequired)
@app.exception_handler(ProjectRecoveryRequired)
async def project_recovery_required(req: Request, error: ProjectRecoveryRequired):
    return JSONResponse({"detail": {"code": "project_recovery_required", "message": str(error)}}, status_code=409)

@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(FRONT, "index.html"), encoding="utf-8") as stream:
        return stream.read()

@app.get("/api/project")
def get_project():
    with LOCK: return load_project()

@app.get("/api/project/state")
def get_project_state():
    from project_lifecycle import COPY_WORKER
    from media_preview import catalog
    with LOCK:
        pid = active_id(); proj = load_project()
        return {"project": proj, "context": project_context(ROOT, pid, proj), "media_availability": catalog(ROOT, proj), "project_action_busy": COPY_WORKER.busy}

def require_project_context(body, pid, proj):
    if "_context" in body and not matches_context(body["_context"], project_context(ROOT, pid, proj)):
        raise HTTPException(409, {"code": "project_changed", "message": "The saved project changed. Your editor copy has been kept; review it in Recovery before continuing."})

@app.get("/api/projects/recovery")
def project_recovery_versions(project: str = None):
    from project_lifecycle import project_file
    with LOCK:
        pid = project or active_id()
        try: path = project_file(ROOT, pid)
        except ValueError as error: raise HTTPException(400, str(error)) from error
        origin = project_context(ROOT, active_id(), load_project()) if pid != active_id() else None
        return {"project": pid, "workspace": workspace_id(ROOT), "origin_context": origin, **inspect_recovery(path)}

@app.post("/api/projects/recovery")
async def project_recovery_restore(req: Request):
    body = await req.json()
    with LOCK:
        pid = active_id()
        if "_context" in body: require_project_context(body, pid, load_project())
        if body.get("project") != pid:
            if not body.get("_context"): raise HTTPException(409, "The active project changed. Refresh recovery versions before restoring.")
            require_project_context(body, pid, load_project())
            pid = body["project"]
        try:
            from project_lifecycle import project_file as checked_project_file
            project_file = str(checked_project_file(ROOT, pid))
            candidate, sha = body.get("candidate"), body.get("sha256")
            if "draft_project" in body:
                if body.get("workspace") != workspace_id(ROOT): raise RecoveryConflict("The browser draft belongs to a different workspace.")
                if body.get("current_sha256") != inspect_recovery(project_file)["current_sha256"]:
                    raise RecoveryConflict("The current project changed. Refresh recovery versions before restoring.")
                candidate, sha = preserve_editor_draft(project_file, body["draft_project"])
            result = restore_version(project_file, candidate, sha,
                                     body.get("current_sha256"), save_project, body.get("editor_project"))
        except RecoveryConflict as error:
            raise HTTPException(409, str(error)) from error
        except (RecoveryError, TypeError, ValueError) as error:
            raise HTTPException(422, str(error)) from error
        except OSError as error:
            raise HTTPException(500, "Cannot preserve or restore the project: " + str(error)[:500]) from error
        event = {"type": "project_replaced", "source": "recovery", "actor": "human", "project": pid}
        try: log_event(event, project_id=pid)
        except Exception as error: result["warning"] = (result.get("warning", "") + "; " if result.get("warning") else "") + "Project restored, but history could not be recorded: " + str(error)[:300]
    await broadcast(event)
    return result

@app.put("/api/project")
async def put_project(req: Request):
    body = await req.json()
    with LOCK:
        pid = active_id(); before = load_project(); require_project_context(body, pid, before)
        document = {key: value for key, value in body.items() if key not in ("_context", "_actor", "_source", "_client")}
        try: parse_project(json.dumps(document, allow_nan=False).encode("utf-8"))
        except (RecoveryError, TypeError, ValueError) as error: raise HTTPException(422, str(error)) from error
        commit_warning = commit_edit(before, document, pid, {"ops": [], "actor": body.get("_actor", "human"), "reason": "replace project", "ts": time.time()})
        context = project_context(ROOT, pid, document)
        ev = {"type": "project_replaced", "actor": body.get("_actor", "human"), "source": body.get("_source", "ui"), "client": body.get("_client"), "project": pid}
        warning = commit_warning
        try: log_event(ev, project_id=pid)
        except OSError as error: warning = (warning + "; " if warning else "") + "Project saved; event history could not be recorded: " + str(error)[:300]
    await broadcast({**ev, "context": context})
    return {"ok": True, "context": context, "warning": warning}

@app.patch("/api/project")
async def patch_project(req: Request):
    body = await req.json(); ops = body.get("ops", []); actor = body.get("actor", "human")
    if isinstance(ops, list) and any(isinstance(o, dict) and o.get("op") == "set_mix" for o in ops) and "_context" not in body:
        raise HTTPException(400, "Mixer edits require the saved project context")
    if actor != "human":
        try:
            with open(P("settings.json"), encoding="utf-8") as settings_file: mode = json.load(settings_file).get("agent_mode") or "direct"
        except FileNotFoundError: mode = "direct"
        except (OSError, ValueError, TypeError, AttributeError):
            return JSONResponse({"ok": False, "errors": ["Could not read agent editing preferences; repair settings before direct edits"]}, status_code=403)
        if mode == "proposals_only": return JSONResponse({"ok": False, "errors": ["agent_mode is proposals_only: submit these ops as a proposal (POST /api/proposals) for the human to accept"], "agent_mode": mode}, status_code=403)
    with LOCK:
        pid = active_id(); proj = load_project(); require_project_context(body, pid, proj)
        problems = validate_ops(proj, ops)
        if problems: return JSONResponse({"ok": False, "errors": problems}, status_code=422)
        before = copy.deepcopy(proj)
        befores = apply_ops(proj, ops); warnings = normalize_tracks(proj) if body.get("normalize", True) and not (ops and all(o.get("op") == "set_mix" for o in ops)) else []
        try: parse_project(json.dumps(proj, allow_nan=False).encode("utf-8"))
        except (RecoveryError, TypeError, ValueError) as error: raise HTTPException(422, str(error)) from error
        entry = None if body.get("_no_undo") else {"ops": ops, "befores": befores, "actor": actor, "reason": body.get("reason") or body.get("tool"), "ts": time.time()}
        commit_warning = commit_edit(before, proj, pid, entry)
        touched = {}
        for o in ops:
            if o.get("op") in ("set_clip", "remove_clip"): touched.setdefault(o["sequence"], set()).add(o["track"])
        for w in warnings:
            try: sq_, tr_ = w.split(":")[0].split("/"); touched.setdefault(sq_, set()).add(tr_)
            except ValueError: pass
        applied = {sid: {tid: next((t["clips"] for sq in proj["sequences"] if sq["id"] == sid for t in sq["tracks"] if t["id"] == tid), None) for tid in tids} for sid, tids in touched.items()}
        context = project_context(ROOT, pid, proj)
        LAST_OPS_TS["t"] = time.time()
        # Keep history/snapshots with the same locked project. A secondary
        # history error must not make the UI retry an already committed edit.
        notice = [commit_warning] if commit_warning else []
        ev = {"id": str(uuid.uuid4())[:8], "type": "ops", "project": pid, "actor": actor, "tool": body.get("tool"), "reason": body.get("reason"), "ops": ops, "befores": befores, "client": body.get("client"), "warnings": warnings}
        try: log_event(ev, project_id=pid)
        except OSError as error: notice.append("Event history could not be recorded: " + str(error)[:200])
        global OPS_SINCE_AUTOSAVE; OPS_SINCE_AUTOSAVE += 1
        if OPS_SINCE_AUTOSAVE >= 25:
            OPS_SINCE_AUTOSAVE = 0
            try:
                write_snapshot(P("projects", pid, "project.json"), proj, "autosave")
            except OSError as error: notice.append("Autosave snapshot could not be recorded: " + str(error)[:200])
    try: await broadcast({**{k: v for k, v in ev.items() if k != "befores"}, "applied": applied, "context": context})
    except Exception: notice.append("Saved; another editor may need to refresh")
    return {"ok": True, "event_id": ev["id"], "warnings": warnings, "applied": applied, "context": context, "warning": "; ".join(notice)}

@app.post("/api/media/import")
async def media_import(req: Request):
    body = await req.json(); added = []
    with LOCK:
        proj = load_project(); project_file = PP("project.json")
        for path in body.get("paths", []):
            if not os.path.exists(path): raise HTTPException(404, f"not found: {path}")
            mid, m = await asyncio.to_thread(ingest, path); proj["media"][mid] = m; added.append(m)
        save_project(proj)
    for m in added: finish_ingest(m["id"], m["path"], m, project_file, m["ingest_token"])
    ev = log_event({"type": "media_added", "actor": body.get("actor", "human"), "media": [m["id"] for m in added]}); await broadcast(ev); return {"added": added}

@app.post("/api/media/upload")
async def media_upload(file: UploadFile = File(...)):
    from upload_storage import OwnedUpload, UploadError, display_name, suffix
    name = display_name(file.filename)
    with LOCK:
        pid = active_id(); proj = load_project(); expected = project_context(ROOT, pid, proj)
    try:
        with OwnedUpload(ROOT, "media", file.file, suffix(name)) as upload:
            mid, m = await _owned_render_thread(lambda proc_holder=None: ingest(str(upload.path), name))
            with LOCK:
                proj = load_project(); require_project_context({"_context": expected}, active_id(), proj)
                project_file = PP("project.json"); proj["media"][mid] = m
                # A failed save may have a recoverable pending document. Retain
                # its newly owned media before the uncertain commit boundary.
                upload.retain(); save_project(proj)
    except UploadError as error:
        raise HTTPException(400, str(error)) from error
    except (ValueError, OSError) as error:
        raise HTTPException(400, "Could not read uploaded media: " + str(error)[:400]) from error
    finish_ingest(mid, m["path"], m, project_file, m["ingest_token"])
    ev = log_event({"type": "media_added", "actor": "human", "media": [mid]}, project_id=pid); await broadcast(ev); return m

def _range_stream(path, start, end, chunk=1 << 20):
    with open(path, "rb") as f:
        f.seek(start); remaining = end - start + 1
        while remaining > 0:
            data = f.read(min(chunk, remaining))
            if not data: break
            remaining -= len(data); yield data

@app.get("/api/media/file/{mid}")
def media_file(mid: str, request: Request):
    from media_preview import resolve, byte_range, PreviewError, file_stamp
    with LOCK:
        pid = active_id(); proj = load_project()
        try: path, expected_stamp, headers = resolve(ROOT, proj, mid, request.query_params, workspace=workspace_id(ROOT), project_id=pid)
        except PreviewError as error: raise HTTPException(error.status, str(error)) from error
        except OSError as error: raise HTTPException(404, "Preview media is unavailable. Refresh media status.") from error
        # Open the selected generation now. A later project switch or pathname
        # replacement cannot redirect this response to a different file.
        try: stream = open(path, "rb"); size = os.fstat(stream.fileno()).st_size
        except OSError as error: raise HTTPException(404, "Preview media is unavailable. Refresh media status.") from error
        try:
            st = os.fstat(stream.fileno())
            if [st.st_size, st.st_mtime_ns, st.st_ino] != expected_stamp or file_stamp(path) != expected_stamp: raise PreviewError("Media changed while opening the preview")
            rng = request.headers.get("range")
            if request.headers.get("if-range") not in (None, headers['ETag']): rng = None
            bounds = byte_range(rng, size)
        except PreviewError as error:
            stream.close()
            if error.status == 416: return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
            raise HTTPException(error.status, str(error)) from error
        except Exception:
            stream.close(); raise
    start, end = bounds if bounds else (0, size-1)
    headers['Content-Length'] = str(end-start+1)
    if bounds: headers['Content-Range'] = f"bytes {start}-{end}/{size}"
    def chunks():
        try:
            stream.seek(start); left = end-start+1
            while left > 0:
                current = os.fstat(stream.fileno())
                if [current.st_size, current.st_mtime_ns, current.st_ino] != expected_stamp: raise OSError("Media changed during playback; refresh media status")
                chunk = stream.read(min(left, 1 << 20))
                if not chunk: break
                left -= len(chunk); yield chunk
        finally: stream.close()
    from media_response import owned_stream
    return owned_stream(StreamingResponse, stream, chunks(), status_code=206 if bounds else 200,
        media_type=mimetypes.guess_type(path)[0] or "application/octet-stream", headers=headers)

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
    """Save the captured project; audit/training failures do not undo the snapshot."""
    body = await req.json(); label = body.get("label", "snapshot"); actor = body.get("actor", "human")
    with LOCK:
        pid = active_id(); proj = load_project(); require_project_context(body, pid, proj)
        project_file = P("projects", pid, "project.json")
        try: saved = write_snapshot(project_file, proj, label)
        except (RecoveryError, TypeError, ValueError) as error: raise HTTPException(422, str(error)) from error
        pair, notices = None, []
        if label == "human_final":
            try:
                versions = list_versions(project_file, "snapshots", label="agent_proposal")["versions"]
                previous = next((v for v in versions if v["label"] == "agent_proposal"), None)
                if previous:
                    a, _ = read_version(project_file, "snapshots", previous["file"], previous["sha256"])
                    pair = diff_sequences(a, proj, body.get("sequence", "seq1"))
                    pair.update(agent_snapshot=previous["file"], human_snapshot=saved["file"], ts=time.time())
                    os.makedirs(P("projects", pid, "training"), exist_ok=True)
                    with open(P("projects", pid, "training", "edit_pairs.jsonl"), "a", encoding="utf-8") as stream: stream.write(json.dumps(pair) + "\n")
            except Exception as error: notices.append("Snapshot saved, but the training pair could not be recorded: " + str(error)[:200])
        context = project_context(ROOT, pid, proj)
        ev = {"type": "snapshot", "project": pid, "label": label, "actor": actor, "snapshot": saved["file"][:-5], "client": body.get("client")}
        try: log_event(ev, project_id=pid)
        except OSError as error: notices.append("Snapshot saved, but event history could not be recorded: " + str(error)[:200])
    try: await broadcast(ev)
    except Exception as error: notices.append("Snapshot saved, but notification failed: " + str(error)[:200])
    return {"ok": True, "snapshot": saved["file"][:-5], "pair": pair, "context": context, "sha256": saved["sha256"], "warning": "; ".join(notices)}

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
RENDER_STATE_LOCK = threading.RLock()
SHUTDOWN = threading.Event()


async def _owned_render_thread(function, *args, **kwargs):
    """Request cancellation signals and joins the real thread; to_thread alone does not."""
    holder = kwargs.setdefault("proc_holder", {})
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        holder["cancelled"] = True
        # Repeated cancellation still cannot abandon a live encoder/reader.
        while not task.done():
            try: await asyncio.shield(task)
            except asyncio.CancelledError: continue
            except Exception: break
        if not task.cancelled():
            error = task.exception()
            if error and holder.get("scratch_diagnostics"):
                raise RuntimeError(str(error)) from error
        raise
LAST_OPS_TS = {"t": 0.0}
def autosave_tick():
    with LOCK:
        mins = int(((json.load(open(P("settings.json"))).get("prefs") or {}).get("autosave_minutes", 5)) if os.path.exists(P("settings.json")) else 5)
        if mins <= 0 or not LAST_OPS_TS["t"]: return
        pid = active_id(); project_file = P("projects", pid, "project.json")
        last = [v for v in list_versions(project_file, "snapshots", label="autosave")["versions"] if v["label"] == "autosave"]
        if not last or time.time() - last[0]["ts"] >= mins * 60:
            if time.time() - LAST_OPS_TS["t"] < mins * 60 * 2:
                write_snapshot(project_file, load_project(), "autosave")
                # Keep the newest 20 automatic versions, including this one.
                from project_versions import version_path
                for old in last[19:]: version_path(project_file, "snapshots", old["file"]).unlink()

def _autosave_loop():
    while not SHUTDOWN.wait(60):
        try: autosave_tick()
        except Exception: pass
AUTOSAVE_THREAD = threading.Thread(target=_autosave_loop, name="Filmocity autosave", daemon=True)
AUTOSAVE_THREAD.start()

def render_workers():
    """Render queue: exports run sequentially by default (like a render queue), or N at a time via settings.prefs.render_workers."""
    try:
        with open(P("settings.json"), encoding="utf-8") as stream:
            n = int((json.load(stream).get("prefs") or {}).get("render_workers", 1))
    except (OSError, ValueError, TypeError): n = 1
    n = max(1, min(4, n))
    with RENDER_STATE_LOCK:
        if globals().get("SHUTDOWN") and SHUTDOWN.is_set():
            raise HTTPException(503, "Exports are shutting down")
        RENDER_WORKERS[:] = [worker for worker in RENDER_WORKERS if worker.is_alive()]
        while len(RENDER_WORKERS) < n:
            worker = threading.Thread(target=_render_worker, name="filmocity-render", daemon=True)
            worker.start(); RENDER_WORKERS.append(worker)

def _record_render_event(job, actor):
    """Audit persistence is independent of the render result and queue ownership."""
    try:
        event = {"type": "render", "actor": actor, "job": {k: v for k, v in job.items() if k != "preset"}}
        captured = job.get("context") or (job.get("preview") or {}).get("context")
        if captured: log_event(event, project_id=captured["project"])
        else: log_event(event)
    except Exception as error:
        # Do not retry via the failed event store or overwrite an encoder/cancel
        # error. Keep a bounded, visible diagnostic on the in-memory job instead.
        detail = f"{type(error).__name__}: {error}"[:500]
        message = f"Render event persistence failed; audit record not confirmed: {detail}"
        job["event_persistence"] = {"status": "error", "error": detail}
        diagnostics = job.get("diagnostics")
        job["diagnostics"] = (diagnostics[-7:] if isinstance(diagnostics, list) else []) + [{"phase": "event_persistence", "message": message}]
        qa = job.get("qa")
        if not isinstance(qa, dict):
            qa = job["qa"] = {"status": "warnings", "flags": []}
        flags = qa.get("flags")
        qa["flags"] = (flags if isinstance(flags, list) else []) + [message]
        if qa.get("status") != "error": qa["status"] = "warnings"
    else:
        job["event_persistence"] = {"status": "recorded"}

def _render_worker():
    from job_history import remember
    while True:
        item = RENDER_Q.get()
        if item is None:
            RENDER_Q.task_done()
            return
        jid = None; job = None; completion = None
        try:
            jid, proj, seq_id, preset, name, actor, out = item
            with RENDER_STATE_LOCK:
                job = JOBS.get(jid)
                if job is None or job.get("status") == "error": continue  # removed or cancelled while queued
                holder = {"last": time.time()}; RENDER_PROCS[jid] = holder
                job.update(status="running", started_run=time.time(), progress=0.0)
                remember(ROOT, job)
            require_resources(proj, seq_id, preset)
            if holder.get("cancelled"): raise RuntimeError("cancelled")
            from export_storage import filesystem_path
            with open(filesystem_path(os.path.join(os.path.dirname(out), f"{name}.cmd.txt")), "x", encoding="utf-8") as log:
                prog = lambda f, j=jid, h=holder: (JOBS[j].update(progress=round(f, 3)), h.update(last=time.time()))
                if preset.get("incremental", False) and preset.get("format", "h264") in ("h264", "hevc") and not preset.get("range"):
                    _, stats = render_incremental(proj, seq_id, out, preset, log, progress=prog, proc_holder=holder, cache_dir=P("renders", "cache")); job.update(stats)
                else: do_render(proj, seq_id, out, preset, log, progress=prog, proc_holder=holder)
            qa = holder["sequence_qa"] if preset.get("format") == "png_sequence" else render_qa(out, preset, proc_holder=holder)
            cleanup = job.get("cache_cleanup") or {}
            if cleanup.get("errors") or cleanup.get("status") == "deferred":
                qa.setdefault("flags", []).append("Segment cache retention was deferred or incomplete; inspect cache_cleanup in this export receipt.")
                if qa.get("status") != "error": qa["status"] = "warnings"
            completion = {"status": "done", "finished": time.time(), "qa": qa}
        except Exception as error:
            if job is not None:
                # Direct assignments also recover a failure in job.update itself.
                completion = {"status": "error", "error": job.get("error") or str(error)[-2000:], "finished": time.time()}
                diagnostics = RENDER_PROCS.get(jid, {}).get("scratch_diagnostics")
                if diagnostics: completion["diagnostics"] = (job.get("diagnostics", []) + diagnostics)[-8:]
        finally:
            try:
                if job is not None:
                    # A polling client must not stop at a terminal status before
                    # the final receipt attempt and its warnings are observable.
                    with RENDER_STATE_LOCK:
                        for key, value in (completion or {}).items(): job[key] = value
                        _record_render_event(job, actor)
                        remember(ROOT, job)
            finally:
                try: RENDER_PROCS.pop(jid, None)
                finally: RENDER_Q.task_done()  # exactly once for every acquired item

def check_render_resources(proj, seq_id, preset):
    report = inspect_resources(proj, seq_id, preset)
    if not report["ok"]: raise HTTPException(422, {"message": "Resolve export resources before rendering.", **report})
    return report

def inspect_export_resources(proj, seq_id, preset):
    from encoder_capabilities import inspect_export
    return inspect_export(proj, seq_id, preset)


def check_export_resources(proj, seq_id, preset):
    report = inspect_export_resources(proj, seq_id, preset)
    if not report["ok"]: raise HTTPException(422, {"message": "Resolve export issues before rendering.", **report})
    return report


def start_render(proj, seq_id, preset, name, actor, preflight=None, *, preview=None, request_id=None, context=None):
    from export_storage import reserve_export
    from job_history import remember, discard_unqueued
    # Queued work owns its input values even for internal callers passing a live
    # document or a preset reused by another job.
    proj, preset = copy.deepcopy(proj), copy.deepcopy(preset)
    from timeline_time import frame_range, from_frames
    sequence = next((s for s in proj["sequences"] if s["id"] == seq_id), None)
    # A supplied report cannot admit a sequence absent from the captured document.
    # Keep the existing structured preflight refusal before any range/output work.
    preflight = copy.deepcopy(preflight if sequence is not None and preflight
                              else check_render_resources(proj, seq_id, preset))
    review_start = 0
    if preset.get("range"):
        try:
            first, _ = frame_range(sequence.get("in_point"), sequence.get("out_point"), sequence["fps"])
            review_start = from_frames(first, sequence["fps"])
        except ValueError as error: raise HTTPException(422, str(error)) from error
    try: destination = reserve_export(P("renders"), name, preset)
    except ValueError as error: raise HTTPException(422, str(error))
    except OSError as error: raise HTTPException(503, "Could not reserve export output: " + str(error))
    jid, name, out = destination["id"], destination["name"], destination["path"]
    job = {"id": jid, "status": "queued", "out": destination["url"], "name": name,
           "started": time.time(), "actor": actor, "preset": copy.deepcopy(preset), "sequence": seq_id, "preflight": preflight,
           "command_log": destination["url"].rsplit(".", 1)[0] + ".cmd.txt", "review_url": "/review/job-" + jid}
    sequence = next(s for s in proj["sequences"] if s["id"] == seq_id)
    job["review_start"] = review_start
    if context is not None: job["context"] = copy.deepcopy(context)
    if preview is not None:
        job["preview"] = copy.deepcopy(preview)
        job["preview_request"] = request_id
    if preset.get("format") == "png_sequence":
        directory = destination["url"].rsplit(".", 1)[0] + ".frames"
        job.update(out=directory + "/sequence.json", output_kind="png_sequence",
                   frames={"directory": directory, "pattern": "frame_%05d.png", "first_frame": directory + "/frame_00001.png"})
    receipt = None
    try:
        render_workers()
        with RENDER_STATE_LOCK:
            if globals().get("SHUTDOWN") and SHUTDOWN.is_set():
                raise HTTPException(503, "Exports are shutting down")
            if jid in JOBS: raise RuntimeError("Export job identity already exists.")
            try: receipt = remember(ROOT, job, required=True)
            except (OSError, ValueError, TypeError) as error:
                raise HTTPException(503, "Cannot save export history before starting: " + str(error)[:300]) from error
            JOBS[jid] = job
            try: RENDER_Q.put_nowait((jid, proj, seq_id, preset, name, actor, out))
            except Exception:
                JOBS.pop(jid, None)
                raise
    except Exception:
        # No queue entry owns this folder. Remove only an empty reservation;
        # never recursively delete files another actor may have placed there.
        try: discard_unqueued(ROOT, job, receipt)
        except (OSError, ValueError): pass
        try: os.rmdir(destination["directory"])
        except OSError: pass
        raise
    return job


PLATFORM_RULES = {  # delivery specs: max seconds, max MB, required aspect (w/h), notes — checked on every export against preset.platform
    "reels": {"max_s": 90, "max_mb": 4000, "aspect": (9, 16), "min_short_side": 1080},
    "tiktok": {"max_s": 600, "max_mb": 4000, "aspect": (9, 16), "min_short_side": 1080},
    "shorts": {"max_s": 60, "max_mb": 4000, "aspect": (9, 16), "min_short_side": 1080},
    "youtube": {"max_s": 43200, "max_mb": 256000, "aspect": (16, 9), "min_short_side": 1080},
    "linkedin": {"max_s": 600, "max_mb": 5000, "aspect": None, "min_short_side": 720},
    "x": {"max_s": 140, "max_mb": 512, "aspect": None, "min_short_side": 720},
    "facebook": {"max_s": 240 * 60, "max_mb": 10000, "aspect": None, "min_short_side": 720},
    "stories": {"max_s": 60, "max_mb": 4000, "aspect": (9, 16), "min_short_side": 1080}}

def render_qa(path, preset=None, *, proc_holder=None):
    """Post-render checks: spec via ffprobe, integrated loudness / true peak via ebur128, plus platform delivery rules when the preset
    names a platform (reels, tiktok, shorts, youtube, linkedin, x, facebook, stories). Returned on the job and logged."""
    from work_budget import work
    qa = {}
    try:
        with work(proc_holder, 'probe'):
            probe = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=True)
            j = json.loads(probe.stdout or "{}");
            if not j.get("streams"): raise RuntimeError("No decodable streams were reported")
            v = next((x for x in j.get("streams", []) if x["codec_type"] == "video"), {}); a = next((x for x in j.get("streams", []) if x["codec_type"] == "audio"), {})
            qa.update(width=v.get("width"), height=v.get("height"), vcodec=v.get("codec_name"), acodec=a.get("codec_name"), duration=round(float(j.get("format", {}).get("duration", 0) or 0), 3), size_mb=round(os.path.getsize(path) / 1e6, 2))
            qa.update(pixel_format=v.get("pix_fmt"), frame_rate=v.get("avg_frame_rate"), declared_frames=v.get("nb_frames"), color_space=v.get("color_space"), color_transfer=v.get("color_transfer"), color_primaries=v.get("color_primaries"), color_range=v.get("color_range"))
            with open(path, "rb") as exported: qa["sha256"] = hashlib.file_digest(exported, "sha256").hexdigest()
            if a:
                r = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-i", path, "-filter_complex", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600, check=True).stderr
                import re as _re; tail = r.split("Summary:")[-1]
                m = _re.search(r"I:\s+(-?[\d.]+) LUFS", tail); tp = _re.search(r"Peak:\s+(-?[\d.]+) dBFS", tail); lra = _re.search(r"LRA:\s+([\d.]+) LU", tail)
                if m: qa["integrated_lufs"] = float(m.group(1))
                if tp: qa["true_peak_dbtp"] = float(tp.group(1))
                if lra: qa["lra_lu"] = float(lra.group(1))
            from delivery_color import inspect as inspect_delivery_color
            qa["delivery_color"] = inspect_delivery_color(v, preset if v or preset is not None else {"format": "audio"})
            flags = []
            if qa["delivery_color"]["status"] == "mismatch":
                flags.append("Delivery color metadata does not match the requested conversion; review delivery_color before use.")
            elif qa["delivery_color"]["status"] == "legacy_unmanaged":
                flags.append("Legacy output color tags and display agreement remain unqualified.")
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
            qa["flags"] = flags; qa["status"] = "warnings" if flags else "checked"
            if qa["delivery_color"]["status"] == "mismatch":
                qa.update(status="error", error="Delivery color metadata failed verification; the encoded file is retained for inspection.")
            if rules: qa["platform_rules"] = "legacy bundled profile; verify current destination requirements"
    except Exception as e: qa.update(status="error", error=str(e)[-300:], flags=["Post-render verification failed; review the file before use"])
    return qa


def user_fonts():
    """User-installed fonts (data/fonts/*.ttf|otf) → [{family, style, file, url}] using the font's own name table."""
    from PIL import ImageFont
    from urllib.parse import quote
    d = P("fonts"); out = []
    if not os.path.isdir(d): return out
    for fn in sorted(os.listdir(d)):
        if not fn.lower().endswith((".ttf", ".otf", ".ttc")): continue
        try: fam, sty = ImageFont.truetype(os.path.join(d, fn), 24).getname()
        except Exception: fam, sty = os.path.splitext(fn)[0], "Regular"
        out.append({"family": fam, "style": sty, "file": fn, "url": "/fonts/" + quote(fn, safe=""), "path": os.path.join(d, fn)})
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
    from upload_storage import OwnedUpload, UploadError, FONT_LOCK, font_name
    try:
        name = font_name(file.filename)
        with FONT_LOCK, OwnedUpload(ROOT, "fonts", file.file, ".tmp") as upload:
            from PIL import ImageFont
            font = ImageFont.truetype(str(upload.path), 24); fam, sty = font.getname()
            del font
            upload.publish_font(name)
    except (UploadError, OSError, ValueError) as error:
        raise HTTPException(400, f"not a usable font: {error}") from error
    import render as _r; _r._FONT_MAP.clear()
    from urllib.parse import quote
    return {"family": fam, "style": sty, "file": name, "url": "/fonts/" + quote(name, safe="")}

@app.delete("/api/fonts/{file}")
def fonts_delete(file: str):
    from upload_storage import directory, UploadError, FONT_LOCK, font_name
    from cache_paths import linked
    try:
        with FONT_LOCK:
            f = directory(ROOT, "fonts") / font_name(file)
            if linked(f) or f.exists() and not f.is_file(): raise UploadError("Font destination is linked or is not a regular file")
            if f.exists(): f.unlink()
    except (UploadError, OSError) as error: raise HTTPException(400, str(error)) from error
    import render as _r; _r._FONT_MAP.clear(); return {"ok": True}

async def _relink_candidate(body):
    import source_relink_io
    import source_commands
    project, expected = _workflow_capture(body)
    for key in ("actor", "client"):
        if key in body and (not isinstance(body[key], str) or not body[key].strip() or len(body[key]) > 120):
            raise HTTPException(422, "Relink actor/client must be a nonempty string of at most 120 characters")
    try:
        source_commands.bounded(body)
        path = body.get("path")
        if not isinstance(path, str) or not path.strip() or len(path) > 4096 or "\x00" in path:
            raise ValueError("Replacement path must be a nonempty file path of at most 4096 characters")
        path = os.path.abspath(path)
        if not os.path.isfile(path): raise ValueError("Replacement must be an existing file")
        owner_root, owner_tasks = os.path.abspath(ROOT), TASKS
        project_file = P("projects", expected["project"], "project.json")
        candidate = await _owned_render_thread(source_relink_io.inspect, project, body, path, scratch_parent=owner_root)
        await asyncio.to_thread(source_relink_io.check, path, candidate["stamp"])
    except (ValueError, KeyError, TypeError, OSError) as error: raise HTTPException(422, str(error)) from error
    with LOCK: require_project_context({"_context": expected}, active_id(), load_project())
    planned = candidate["plan"]
    if body.get("expectedSource") is not None and body["expectedSource"] != planned["expectedSource"]:
        raise HTTPException(409, "The source changed after inspection; inspect it again")
    report = {"ok": planned["ok"], "context": expected, "path": path, "info": candidate["info"],
        **{key:planned[key] for key in ("media_id", "requested_media_id", "affected_media_ids", "issues", "summary", "fingerprint", "expectedSource")}}
    return {**candidate, "project":project, "expected":expected, "report":report,
        "owner_root":owner_root, "owner_tasks":owner_tasks, "project_file":project_file}


async def _commit_source_relink(candidate, body, envelope):
    import source_relink_io
    import source_commands
    project, expected, planned = candidate["project"], candidate["expected"], candidate["plan"]
    await asyncio.to_thread(source_relink_io.check, candidate["path"], candidate["stamp"])
    _source_command_policy(body)
    result = await _workflow_commit(project, expected, {**envelope, "message":planned["summary"]["message"]}, "relink", body.get("actor", "human"))
    preparation = {"tasks":[], "warnings":[]}
    # This request-owned coroutine completes every queue attempt even if its HTTP
    # reply is lost. A workspace switch must never redirect that registration.
    for identity in planned["affected_media_ids"]:
        media = project["media"][identity]
        if media.get("subclip_of") and not media.get("audio_alias"): continue
        try:
            if os.path.abspath(ROOT) != candidate["owner_root"] or TASKS is not candidate["owner_tasks"]:
                raise ValueError("Workspace changed; open the source workspace and explicitly Prepare its previews")
            await asyncio.to_thread(source_relink_io.check, candidate["path"], candidate["stamp"])
            if os.path.abspath(ROOT) != candidate["owner_root"] or TASKS is not candidate["owner_tasks"]:
                raise ValueError("Workspace changed during source validation; prepare previews from the original workspace")
            prepared = source_commands.alias_source(project, media) if media.get("audio_alias") else media
            if not _task_media_current({"media_id":identity, "path":prepared["path"], "token":prepared["ingest_token"],
                    "project_file":candidate["project_file"], "info":prepared}):
                raise ValueError("The saved source changed before preparation")
            queued = finish_ingest(identity, prepared["path"], prepared, candidate["project_file"], prepared["ingest_token"])
            if queued.get("error"): raise ValueError(queued["error"])
            preparation["tasks"].append({"media_id":identity, "task":queued})
        except Exception as error:
            preparation["warnings"].append("Source "+identity+" saved; preparation could not be queued: "+str(error)[:300])
    result["preparation"] = preparation
    result["warnings"] = [*result["warnings"], *preparation["warnings"]]
    return result


@app.post("/api/media/relink")
async def media_relink(req: Request):
    body = await req.json()
    if not isinstance(body, dict) or not isinstance(body.get("fingerprint"), str) or len(body["fingerprint"]) != 64 or any(c not in "0123456789abcdef" for c in body["fingerprint"]):
        raise HTTPException(400, "Relink requires the exact reviewed fingerprint and saved context; inspect the replacement first")
    _source_command_policy(body)
    candidate = await _relink_candidate(body); planned = candidate["plan"]
    if body["fingerprint"] != planned["fingerprint"]: raise HTTPException(409, "The reviewed Relink plan changed; inspect the replacement again")
    if not planned["ok"]: raise HTTPException(422, candidate["report"])
    summary = planned["summary"]
    envelope = {"project":candidate["expected"]["project"], "changed":bool(planned["ops"]), "summary":summary,
        "warnings":summary.get("warnings", []), "media_id":planned["media_id"], "requested_media_id":planned["requested_media_id"],
        "affected_media_ids":planned["affected_media_ids"], "media":planned["media"].get(planned["media_id"], candidate["project"]["media"][planned["media_id"]])}
    _source_command_policy(body)
    if not planned["ops"]:
        with LOCK: require_project_context({"_context":candidate["expected"]}, active_id(), load_project())
        return {"ok":True, **envelope, "context":candidate["expected"], "preparation":{"tasks":[],"warnings":[]}}
    try:
        apply_ops(candidate["project"], planned["ops"])
        parse_project(json.dumps(candidate["project"], allow_nan=False).encode("utf-8"))
    except (ValueError, KeyError, TypeError) as error: raise HTTPException(422, str(error)) from error
    commit = asyncio.create_task(_commit_source_relink(candidate, body, envelope))
    try: return await asyncio.shield(commit)
    except asyncio.CancelledError:
        while not commit.done():
            try: await asyncio.shield(commit)
            except asyncio.CancelledError: continue
            except Exception: break
        if not commit.cancelled(): commit.exception()
        raise


@app.post("/api/media/relink/inspect")
async def media_relink_inspect(req: Request):
    return (await _relink_candidate(await req.json()))["report"]

@app.get("/api/media/status")
def media_status():
    proj = load_project(); return {mid: media_online(proj["media"].get(m.get("subclip_of"), m)) for mid, m in proj["media"].items()}

@app.post("/api/captions/auto")
async def captions_auto(req: Request):
    return await _workflow_transcription(await req.json(), words=False)

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
    from project_resources import references
    with LOCK:
        referenced = any(os.path.abspath(obj[key]) == ap for obj, key, _, _ in references(load_project()) if key == "lut" or key == "path" and obj.get("name") in ("slog3", "vlog", "clog3", "logc3"))
    if not (referenced or any(ap.startswith(a + os.sep) for a in allowed)) or not ap.lower().endswith(".cube") or not os.path.isfile(ap): raise HTTPException(404)
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

async def project_history_action(direction, req):
    body = await req.json() if req.headers.get("content-length") not in (None, "0") else {}
    with LOCK:
        pid = active_id(); before = load_project(); require_project_context(body, pid, before)
        history = read_undo_history(pid)
        if not history[direction]: return {"ok": False, "reason": "nothing to " + direction}
        entry = history[direction][-1]
        try:
            if "changes" in entry:
                proj = apply_changes(before, entry["changes"], direction)
            else:
                # Existing projects keep their operation-based history. New
                # entries use exact values so removed fields and normalization
                # also round-trip. A failed legacy inversion retains its entry.
                proj = copy.deepcopy(before)
                ops = inverse_ops(entry["ops"], entry["befores"], proj) if direction == "undo" else copy.deepcopy(entry["ops"])
                befores = apply_ops(proj, ops)
                if direction == "redo":
                    if not (ops and all(o.get("op") == "set_mix" for o in ops)): normalize_tracks(proj)
                    entry["befores"] = befores
                entry["changes"] = changes_between(proj, before) if direction == "undo" else changes_between(before, proj)
            parse_project(json.dumps(proj, allow_nan=False).encode("utf-8"))
        except (HistoryConflict, RecoveryError, ValueError, TypeError, KeyError, IndexError, StopIteration) as error:
            raise HTTPException(409, {"code": "history_conflict", "message": "Cannot " + direction + " this edit: " + str(error)[:300]}) from error
        history[direction].pop()
        other = "redo" if direction == "undo" else "undo"
        history[other] = (history[other] + [entry])[-200:]
        warning = commit_project_history(proj, history, pid)
        context = project_context(ROOT, pid, proj)
        ev = {"type": "project_replaced", "project": pid, "source": direction, "actor": body.get("actor", "human"),
              "reason": entry.get("reason"), "client": body.get("client"), "context": context}
        try: log_event(ev, project_id=pid)
        except OSError as error: warning = (warning + "; " if warning else "") + "Event history could not be recorded: " + str(error)[:200]
    await broadcast(ev)
    return {"ok": True, "undone" if direction == "undo" else "redone": entry.get("reason"), "by": entry.get("actor"),
            "remaining": len(history[direction]), "context": context, "warning": warning}


@app.post("/api/undo")
async def undo(req: Request):
    return await project_history_action("undo", req)


@app.post("/api/redo")
async def redo(req: Request):
    return await project_history_action("redo", req)


@app.get("/api/undo/stack")
def undo_stack():
    with LOCK:
        load_project(); st = read_undo_history(active_id())
        return {"undo": [{"reason": e.get("reason"), "actor": e.get("actor"), "ts": e.get("ts"), "n": len(e.get("ops", []))} for e in st["undo"][-50:]], "redo": [{"reason": e.get("reason"), "actor": e.get("actor")} for e in st["redo"][-50:]]}

@app.get("/api/version")
def version(): return {"app": "Filmocity", "version": VERSION, "schema": SCHEMA, "instance": INSTANCE_ID, "pid": os.getpid(), "workspace": workspace_id(ROOT), "build": BUILD_INFO}

# ---------- effects catalog + stabilization analysis ----------
@app.get("/api/effects")
def effects_list(): return effects_catalog()

async def _stabilization_candidate(body):
    import stabilization
    project, expected = _workflow_capture(body)
    try: review = await _owned_render_thread(stabilization.inspect, project, expected, body)
    except stabilization.StaleAnalysis as error: raise HTTPException(409, str(error)) from error
    except (ValueError, TypeError, KeyError, OSError) as error: raise HTTPException(422, str(error)) from error
    with LOCK: require_project_context({"_context": expected}, active_id(), load_project())
    return project, expected, review


@app.post("/api/stabilize/inspect")
async def stabilization_inspect(req: Request):
    """Read only: review the saved project, physical source bytes and settings."""
    return (await _stabilization_candidate(await req.json()))[2]


@app.post("/api/stabilize")
async def stabilize(req: Request):
    """Analyze exactly a reviewed input, then publish and save to its owner."""
    import stabilization
    body = await req.json()
    if (not isinstance(body, dict) or not isinstance(body.get("fingerprint"), str)
            or len(body["fingerprint"]) != 64 or any(c not in "0123456789abcdef" for c in body["fingerprint"])):
        raise HTTPException(400, "Stabilization requires the reviewed fingerprint and saved project context; inspect first")
    _source_command_policy(body)
    project, expected, review = await _stabilization_candidate(body)
    if body["fingerprint"] != review["fingerprint"]:
        raise HTTPException(409, "The reviewed stabilization input changed; inspect it again")
    root, holder = ROOT, {}
    try:
        prepared = await _owned_render_thread(stabilization.prepare, root, project, review, proc_holder=holder)
        async def commit():
            _source_command_policy(body)
            with LOCK:
                pid = active_id(); before = load_project()
                require_project_context({"_context": expected}, pid, before)
                try: stabilization.check_source(review)
                except (ValueError, OSError) as error: raise HTTPException(409, str(error)) from error
                result = prepared.publish()
                after = copy.deepcopy(before)
                affected = review["affected_media_ids"]
                for mid in affected:
                    after["media"][mid]["stab_trf"] = result["trf"]
                    after["media"][mid]["stab_analysis"] = copy.deepcopy(result["record"])
                changed = after != before
                warning = ""
                if changed:
                    parse_project(json.dumps(after, allow_nan=False).encode("utf-8"))
                    warning = commit_edit(before, after, pid, {"actor": body.get("actor", "human"),
                        "tool": "workflow_stabilize", "reason": "Analyze source motion", "ts": time.time()}) or ""
                context = project_context(root, pid, after)
                event = {"type": "ops", "project": pid, "tool": "workflow_stabilize",
                    "actor": body.get("actor", "human"), "ops": [], "context": context}
                if changed:
                    try: log_event(event, project_id=pid)
                    except OSError as error: warning += " Event history unavailable: " + str(error)[:200]
            if changed:
                try: await broadcast(event)
                except Exception: warning += " Saved; another editor may need to refresh"
            return {"ok": True, "kind": "stabilization", "context": context,
                "requested_media_id": review["requested_media_id"], "media_id": review["media_id"],
                "media": affected, "trf": result["trf"], "analysis": result["record"],
                "cached": result["cached"], "changed": changed, "warning": warning.strip()}
        task = asyncio.create_task(commit())
        try: return await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try: await asyncio.shield(task)
                except asyncio.CancelledError: continue
                except Exception: break
            if not task.cancelled(): task.exception()
            raise
    except stabilization.StaleAnalysis as error: raise HTTPException(409, str(error)) from error
    except (ValueError, TypeError, KeyError, OSError) as error:
        prepared = holder.get("prepared_analysis")
        suffix = (" Published analysis retained at " + prepared.result["trf"]
            + "; inspect saved state/Recovery before retrying.") if prepared and prepared.published else ""
        raise HTTPException(422 if not suffix else 500, str(error) + suffix) from error
    finally:
        prepared = holder.get("prepared_analysis")
        if prepared: prepared.close()

# ---------- projects ----------
@app.get("/api/projects")
def projects_list():
    from project_lifecycle import catalog
    with LOCK: root, pid = ROOT, active_id()
    return catalog(root, pid)

def _read_project_target(pid, target_sha=None):
    from project_lifecycle import project_file, read, ProjectActionError
    try:
        path = project_file(ROOT, pid)
        raw = read(path)
        if target_sha is not None and target_sha != hashlib.sha256(raw).hexdigest():
            raise HTTPException(409, "The selected project changed. Refresh the project list before opening it.")
        recover_transaction(path)
        doc = parse_project(read(path))
        if doc.get("version", 1) < SCHEMA:
            doc = migrate_project(doc); save_project(doc, str(path))
        read_undo_history(pid)
        return doc
    except (OSError, RecoveryError, TransactionRecoveryRequired, ProjectActionError) as error:
        raise HTTPException(422, {"code": "project_target_unavailable", "message": "This project needs attention; the active project was kept. Open its Recovery versions. " + str(error)[:350]}) from error

def _activate_project_locked(pid, actor, expected=None, target_sha=None, source="open_project"):
    # Validate before touching active.json, under the same lock as guarded edits.
    if expected is not None:
        origin = active_id(); require_project_context({"_context": expected}, origin, load_project())
    doc = _read_project_target(pid, target_sha)
    set_active_project(pid)
    result = {"ok": True, "id": pid, "name": doc.get("name", pid), "context": project_context(ROOT, pid, doc)}
    event = {"type": "project_replaced", "actor": actor, "source": source, "project": pid, "context": result["context"]}
    try: log_event(event, project_id=pid)
    except Exception as error: result["warning"] = "Project opened; event history could not be saved: " + str(error)[:300]
    return result, event

async def _project_action_notify(result, event):
    try: await broadcast(event)
    except Exception as error: result["warning"] = (result.get("warning", "") + "; Project opened; other editors may need to refresh: " + str(error)[:200]).lstrip('; ')
    return result

async def _switch(pid, actor, expected=None, target_sha=None):
    with LOCK: result, event = _activate_project_locked(pid, actor, expected, target_sha)
    return await _project_action_notify(result, event)

def set_active_project(pid):
    # Caller holds LOCK, shared with guarded edits. Never expose partial JSON
    # to active_id(), whose compatibility fallback is the default project.
    temporary = P("active.json.tmp")
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump({"id": pid}, stream); stream.flush(); os.fsync(stream.fileno())
    for attempt in range(6):
        try:
            os.replace(temporary, P("active.json")); break
        except PermissionError as error:
            if getattr(error, "winerror", None) not in (5, 32, 33) or attempt == 5: raise
            time.sleep(min(0.02 * 2 ** attempt, 0.2))

def _prepare_project_action(body, kind):
    from project_lifecycle import project_name, ProjectActionError
    project, expected = _workflow_capture(body)
    try:
        fallback = "Untitled" if kind == "new" else (project.get("name", "Untitled") + " copy")
        name = project_name(body.get("name"), fallback)
        if "copy_media" in body and type(body["copy_media"]) is not bool: raise ProjectActionError("Copy media must be true or false")
    except ProjectActionError as error: raise HTTPException(400, str(error)) from error
    pid = "project-" + uuid.uuid4().hex
    document = default_project() if kind == "new" else copy.deepcopy(project)
    document["name"] = name; document["updated"] = time.time()
    if kind == "new" and body.get("copy_media"): document["media"] = copy.deepcopy(project["media"])
    if kind == "duplicate": document["id"] = pid; document["proposals"] = []
    return document, expected, pid

def _commit_prepared_project(document, expected, pid, actor, kind, check=lambda: None):
    from project_lifecycle import PreparedProject, project_file
    source = project_file(ROOT, expected["project"]).parent if kind == "save_as" else None
    prepared = PreparedProject(ROOT, document, pid, source=source, check=check)
    try:
        with LOCK:
            require_project_context({"_context": expected}, active_id(), load_project())
            prepared.publish()
            try: return _activate_project_locked(pid, actor, expected, source=kind)
            except Exception as error:
                raise HTTPException(409, f"The new project was saved at {prepared.path}, but opening it was not confirmed. Refresh the project list before creating another copy. {str(error)[:200]}") from error
    finally: prepared.close()

@app.post("/api/projects/new")
async def projects_new(req: Request):
    body = await req.json(); document, expected, pid = _prepare_project_action(body, "new")
    result, event = _commit_prepared_project(document, expected, pid, body.get("actor", "human"), "new")
    return await _project_action_notify(result, event)

@app.post("/api/projects/open")
async def projects_open(req: Request):
    body = await req.json()
    if isinstance(body, dict) and "_recovery_origin" in body:
        with LOCK:
            receipt = body["_recovery_origin"]; pid = active_id(); report = inspect_recovery(P("projects", pid, "project.json"))
            if (not isinstance(receipt, dict) or receipt.get("workspace") != workspace_id(ROOT) or receipt.get("project") != pid
                    or receipt.get("current_sha256") != report["current_sha256"] or report["current_valid"]):
                raise HTTPException(409, "Recovery project changed. Refresh before opening another project.")
            result, event = _activate_project_locked(body.get("id"), body.get("actor", "human"), target_sha=body.get("_target_sha256"))
        return await _project_action_notify(result, event)
    _, expected = _workflow_capture(body)
    return await _switch(body.get("id"), body.get("actor", "human"), expected, body.get("_target_sha256"))

@app.post("/api/projects/save_as")
async def projects_save_as(req: Request):
    from project_lifecycle import COPY_WORKER, ProjectActionError
    body = await req.json(); document, expected, pid = _prepare_project_action(body, "save_as")
    try:
        result, event = await COPY_WORKER.run(lambda check: _commit_prepared_project(document, expected, pid, body.get("actor", "human"), "save_as", check))
    except ProjectActionError as error: raise HTTPException(409, str(error)) from error
    except (OSError, MediaCollectionError) as error: raise HTTPException(422, "Project copy failed; the original was kept. " + str(error)[:400]) from error
    return await _project_action_notify(result, event)

def _task_collect(payload, task):
    from collection_workflow import prepare
    return prepare(payload, task)

@app.post("/api/projects/collect")
@app.post("/api/tasks/collect")
async def projects_collect(req: Request):
    """Queue verified originals; only a later explicit Apply changes project paths."""
    from collection_workflow import payload
    from project_lifecycle import COPY_WORKER, ProjectActionError
    if TASKS is None: raise HTTPException(503, "Background tasks are unavailable")
    body = await req.json(); project, expected = _workflow_capture(body)
    root = ROOT
    try:
        captured = await COPY_WORKER.run(lambda check: payload(project, root, expected["project"], check=check))
        with LOCK:
            require_project_context({"_context": expected}, active_id(), load_project())
            task = TASKS.submit("collect", project.get("name", "Project"), expected, captured, identity=body.get("request_id"))
    except (TaskError, ProjectActionError) as error: raise HTTPException(409, str(error)) from error
    except (MediaCollectionError, OSError, ValueError) as error: raise HTTPException(422, str(error)) from error
    return {"ok": True, "task": task, "context": expected,
            "message": "Collection queued. Keep editing; apply the verified copies from Tasks when ready."}

def _commit_collection(identity, value, project, expected, actor, check):
    from collection_workflow import verify
    try: after = verify(project, value["payload"], value["result"], check)
    except (MediaCollectionError, OSError, ValueError) as error: raise HTTPException(422, str(error)) from error
    after.setdefault("workflow", {})["collection_task"] = identity
    with LOCK:
        check(); pid = active_id(); before = load_project()
        require_project_context({"_context": expected}, pid, before)
        warning = commit_edit(before, after, pid, {"actor": actor, "tool": "collect", "reason": "Relink to verified collected originals", "ts": time.time()})
        context = project_context(ROOT, pid, after)
        result = {"ok": True, "context": context, "message": "Collected originals applied. Undo restores the previous media paths; copies are retained.", "warning": warning or ""}
        event = {"type": "project_replaced", "source": "collect", "actor": actor, "project": pid, "context": context}
        try: log_event(event, project_id=pid)
        except Exception as error: result["warning"] += ("; " if result["warning"] else "") + "Relink saved; event history unavailable: " + str(error)[:200]
    return result, event

async def _apply_collection(identity, value, body, project, expected):
    from project_lifecycle import COPY_WORKER, ProjectActionError
    if value["record"]["context"]["project"] != expected["project"]: raise HTTPException(409, "Open the original project first")
    if project.get("workflow", {}).get("collection_task") == identity:
        TASKS.finish_apply(identity, success=True, message="Collection already applied")
        return {"ok": True, "context": expected, "message": "Collection already applied; no action repeated"}
    try: claimed = TASKS.begin_apply(identity)
    except (TaskError, OSError) as error: raise HTTPException(409, str(error)) from error
    try:
        result, event = await COPY_WORKER.run(lambda check: _commit_collection(identity, claimed, project, expected, body.get("actor", "human"), check))
    except BaseException as error:
        TASKS.finish_apply(identity, success=False, message="Relink was not confirmed. Inspect the project before applying again; verified copies were retained.")
        if isinstance(error, ProjectActionError): raise HTTPException(409, str(error)) from error
        if isinstance(error, (MediaCollectionError, ValueError)): raise HTTPException(422, str(error)) from error
        raise
    task = TASKS.finish_apply(identity, success=True, message="Collection applied")
    if task.get("warning"): result["warning"] = (result.get("warning", "") + "; " + task["warning"]).strip("; ")
    return await _project_action_notify(result, event)

@app.post("/api/media/scenes")
async def media_scenes(req: Request):
    return _queue_media_analysis(await req.json(), "scenes")

def _queue_media_analysis(body, mode):
    import media_analysis
    if TASKS is None: raise HTTPException(503, "Background tasks are unavailable")
    project, expected = _workflow_capture(body)
    try:
        payload = media_analysis.capture(project, body, mode, expected)
        task = TASKS.submit("analysis", project["media"][payload["media_id"]].get("name", "Source") + {"scenes": " — scene detection", "silences": " — silence detection", "remix": " — music remix"}[mode],
                            expected, payload, identity=body.get("request_id"))
    except (ValueError, KeyError, OSError, TypeError) as error: raise HTTPException(400, str(error)) from error
    return {"ok": True, "task": task, "context": expected, "message": "Analysis queued. Review the result in Tasks before applying edits."}

def _task_media_analysis(payload, task):
    import media_analysis
    return media_analysis.analyze(payload, task)

def _review_media_analysis(value, project, expected):
    import media_analysis
    if value["record"]["kind"] != "analysis" or value["record"]["status"] != "ready" or value.get("result") is None:
        raise HTTPException(409, "This analysis result is not ready for review")
    try:
        media_analysis.validate_current(project, value["payload"], expected)
        result = {"ok": True, "task": value["record"], "context": expected, "result": value["result"]}
        if value["payload"].get("clip_id") is not None:
            import analysis_edits
            result["plan"] = analysis_edits.plan(project, value["payload"], value["result"], identity=value["record"]["id"])
        return result
    except (ValueError, KeyError, OSError, TypeError) as error: raise HTTPException(409, str(error)) from error

@app.post("/api/tasks/{identity}/analysis")
async def background_analysis_review(identity: str, req: Request):
    value = _owned_task(identity); project, expected = _workflow_capture(await req.json())
    return _review_media_analysis(value, project, expected)

async def _apply_media_analysis(identity, value, body, project, expected):
    payload = value["payload"]
    if not payload.get("clip_id"): raise HTTPException(409, "Raw media analysis has no timeline edit to apply")
    sequence = next((seq for seq in project.get("sequences", []) if seq.get("id") == payload["sequence"]), None)
    if sequence is not None and sequence.get("workflow", {}).get("analysis_task") == identity:
        TASKS.finish_apply(identity, success=True, message="Analysis edit already applied")
        return {"ok": True, "context": expected, "message": "Analysis edit already applied; no edit repeated"}
    if body.get("actor", "human") != "human":
        try:
            with open(P("settings.json"), encoding="utf-8") as stream: mode = json.load(stream).get("agent_mode") or "direct"
        except FileNotFoundError: mode = "direct"
        except (OSError, ValueError, TypeError, AttributeError) as error:
            raise HTTPException(403, "Could not read agent editing preferences; repair settings before direct analysis edits") from error
        if mode == "proposals_only": raise HTTPException(403, "Direct agent edits are disabled. Submit the reviewed analysis ops as a proposal for the editor to accept.")
    reviewed = _review_media_analysis(value, project, expected); plan = reviewed["plan"]
    if not isinstance(body.get("fingerprint"), str) or body["fingerprint"] != plan["fingerprint"]:
        raise HTTPException(409, "The analysis edit plan changed. Review it again before applying")
    try: TASKS.begin_apply(identity)
    except (TaskError, OSError) as error: raise HTTPException(409, str(error)) from error
    try:
        apply_ops(project, plan["ops"])
        if normalize_tracks(project): raise ValueError("The analysis plan contains unresolved overlaps; review the timeline")
        sequence = next(seq for seq in project["sequences"] if seq["id"] == payload["sequence"])
        sequence.setdefault("workflow", {})["analysis_task"] = identity
        result = await _workflow_commit(project, expected, plan["summary"], "analysis_" + payload["mode"], body.get("actor", "human"))
    except BaseException:
        TASKS.finish_apply(identity, success=False, message="Analysis apply was not confirmed. Check the project before retrying.")
        raise
    task = TASKS.finish_apply(identity, success=True, message="Analysis edit applied")
    if task.get("warning"): result["warning"] = (result.get("warning", "") + "; " + task["warning"]).strip("; ")
    return result

@app.post("/api/transcript")
async def transcript(req: Request):
    return await _workflow_transcription(await req.json(), words=True)

# ---------- guided editing workflow ----------
def _workflow_capture(body, *, required=True):
    if not isinstance(body, dict) or (required and "_context" not in body):
        raise HTTPException(400, "Workflow actions require the saved project context")
    with LOCK:
        pid = active_id(); proj = load_project()
        require_project_context(body, pid, proj)
        return copy.deepcopy(proj), project_context(ROOT, pid, proj)

async def _workflow_commit(after, expected, summary, action, actor="human"):
    with LOCK:
        pid = active_id(); before = load_project()
        require_project_context({"_context": expected}, pid, before)
        warning = commit_edit(before, after, pid, {"actor": actor, "tool": "workflow_" + action,
            "reason": summary.get("message", action), "ts": time.time()})
        context = project_context(ROOT, pid, after)
        event = {"type": "ops", "project": pid, "tool": "workflow_" + action, "actor": actor, "ops": [], "context": context}
        notices = [warning] if warning else []
        try: log_event(event, project_id=pid)
        except OSError as error: notices.append("Event history unavailable: " + str(error)[:200])
    try: await broadcast(event)
    except Exception: notices.append("Saved; another editor may need to refresh")
    return {"ok": True, **summary, "context": context, "warning": "; ".join(notices)}

async def _sequence_nest_candidate(body):
    import sequence_nesting
    project, expected = _workflow_capture(body)
    for key in ("actor", "client"):
        if key in body and (not isinstance(body[key], str) or not body[key].strip() or len(body[key]) > 120):
            raise HTTPException(422, "Nest actor/client must be nonempty text of at most 120 characters")
    try: planned = await _owned_render_thread(sequence_nesting.plan, project, body)
    except (ValueError, TypeError, KeyError) as error: raise HTTPException(422, str(error)) from error
    with LOCK: require_project_context({"_context": expected}, active_id(), load_project())
    report = {"context": expected, **{key: planned[key] for key in ("ok", "kind", "sequence", "clip_ids", "settings", "issues", "summary", "fingerprint")}}
    return {"project": project, "expected": expected, "plan": planned, "report": report}


async def _commit_sequence_nest(candidate, body):
    _source_command_policy(body)
    planned = candidate["plan"]; summary = planned["summary"]
    envelope = {"kind": "sequence_nesting", "project": candidate["expected"]["project"], "changed": True,
        "sequence": planned["sequence"], "child_sequence": summary["child_sequence"], "source_clip_ids": planned["clip_ids"],
        "wrapper_clip_ids": summary["wrapper_clip_ids"], "summary": summary, "warnings": summary["warnings"], "message": summary["message"]}
    return await _workflow_commit(candidate["project"], candidate["expected"], envelope, "nest", body.get("actor", "human"))


@app.post("/api/sequence/nest/review")
async def sequence_nest_review(req: Request):
    return (await _sequence_nest_candidate(await req.json()))["report"]


@app.post("/api/sequence/nest")
async def sequence_nest(req: Request):
    body = await req.json()
    if not isinstance(body, dict) or not isinstance(body.get("fingerprint"), str) or len(body["fingerprint"]) != 64 or any(c not in "0123456789abcdef" for c in body["fingerprint"]):
        raise HTTPException(400, "Nest requires the exact reviewed fingerprint and saved context; review it first")
    _source_command_policy(body)
    candidate = await _sequence_nest_candidate(body); planned = candidate["plan"]
    if body["fingerprint"] != planned["fingerprint"]: raise HTTPException(409, "The reviewed nesting changed; review the saved selection again")
    if not planned["ok"]: raise HTTPException(422, candidate["report"])
    try:
        apply_ops(candidate["project"], planned["ops"])
        parse_project(json.dumps(candidate["project"], allow_nan=False).encode("utf-8"))
    except (ValueError, TypeError, KeyError) as error: raise HTTPException(422, str(error)) from error
    commit = asyncio.create_task(_commit_sequence_nest(candidate, body))
    try: return await asyncio.shield(commit)
    except asyncio.CancelledError:
        while not commit.done():
            try: await asyncio.shield(commit)
            except asyncio.CancelledError: continue
            except Exception: break
        if not commit.cancelled(): commit.exception()
        raise


async def _clip_attributes_candidate(body):
    import clip_attributes
    project, expected = _workflow_capture(body)
    for key in ("actor", "client"):
        if key in body and (not isinstance(body[key], str) or not body[key].strip() or len(body[key]) > 120):
            raise HTTPException(422, "Attribute actor/client must be nonempty text of at most 120 characters")
    try: planned = await _owned_render_thread(clip_attributes.inspect, project, body)
    except (ValueError, TypeError, KeyError, OSError) as error: raise HTTPException(422, str(error)) from error
    with LOCK: require_project_context({"_context": expected}, active_id(), load_project())
    report = {"context": expected, **{key: planned[key] for key in ("ok", "kind", "sequence", "clip_ids", "donor", "settings", "issues", "summary", "fingerprint")}}
    return {"project": project, "expected": expected, "plan": planned, "report": report}


async def _commit_clip_attributes(candidate, body):
    import clip_attributes
    _source_command_policy(body)
    planned = candidate["plan"]; summary = planned["summary"]
    envelope = {"kind": "clip_attributes", "project": candidate["expected"]["project"], "changed": bool(planned["ops"]),
        "sequence": planned["sequence"], "source_clip_id": planned["donor"]["clip"]["id"], "target_clip_ids": planned["clip_ids"],
        "changed_clip_ids": summary["changed_clip_ids"], "summary": summary, "warnings": summary["warnings"], "message": summary["message"]}
    # Metadata-only stat checks are bounded and final; no file reading or await occurs under the commit lock.
    with LOCK:
        require_project_context({"_context": candidate["expected"]}, active_id(), load_project())
        try:
            if clip_attributes.resource_stamps(planned["resources"]) != planned["resource_stamps"]:
                raise HTTPException(409, "An attribute resource changed; review the transfer again")
        except OSError as error: raise HTTPException(409, "An attribute resource became unavailable; review again") from error
        if not planned["ops"]: return {"ok": True, **envelope, "context": candidate["expected"]}
    return await _workflow_commit(candidate["project"], candidate["expected"], envelope, "clip_attributes", body.get("actor", "human"))


@app.post("/api/clip/attributes/review")
async def clip_attributes_review(req: Request):
    return (await _clip_attributes_candidate(await req.json()))["report"]


@app.post("/api/clip/attributes")
async def clip_attributes_apply(req: Request):
    body = await req.json()
    if not isinstance(body, dict) or not isinstance(body.get("fingerprint"), str) or len(body["fingerprint"]) != 64 or any(c not in "0123456789abcdef" for c in body["fingerprint"]):
        raise HTTPException(400, "Paste Attributes requires its exact reviewed fingerprint and saved context")
    _source_command_policy(body)
    candidate = await _clip_attributes_candidate(body); planned = candidate["plan"]
    if body["fingerprint"] != planned["fingerprint"]: raise HTTPException(409, "The reviewed attribute transfer changed; review it again")
    if not planned["ok"]: raise HTTPException(422, candidate["report"])
    try:
        apply_ops(candidate["project"], planned["ops"])
        parse_project(json.dumps(candidate["project"], allow_nan=False).encode("utf-8"))
    except (ValueError, TypeError, KeyError) as error: raise HTTPException(422, str(error)) from error
    commit = asyncio.create_task(_commit_clip_attributes(candidate, body))
    try: return await asyncio.shield(commit)
    except asyncio.CancelledError:
        while not commit.done():
            try: await asyncio.shield(commit)
            except asyncio.CancelledError: continue
            except Exception: break
        if not commit.cancelled(): commit.exception()
        raise


async def _multicam_flatten_candidate(body):
    import multicam_flatten
    project, expected = _workflow_capture(body)
    for key in ("actor", "client"):
        if key in body and (not isinstance(body[key], str) or not body[key].strip() or len(body[key]) > 120):
            raise HTTPException(422, "Flatten actor/client must be nonempty text of at most 120 characters")
    try: planned = await _owned_render_thread(multicam_flatten.plan, project, body)
    except (ValueError, TypeError, KeyError) as error: raise HTTPException(422, str(error)) from error
    with LOCK: require_project_context({"_context": expected}, active_id(), load_project())
    report = {"context": expected, **{key: planned[key] for key in ("ok", "kind", "sequence", "clip_ids", "settings", "issues", "summary", "fingerprint")}}
    return {"project": project, "expected": expected, "plan": planned, "report": report}


async def _commit_multicam_flatten(candidate, body):
    _source_command_policy(body)
    planned = candidate["plan"]; summary = planned["summary"]
    envelope = {"kind": "multicam_flatten", "project": candidate["expected"]["project"], "changed": bool(planned["ops"]),
        "sequence": planned["sequence"], "source_clip_ids": planned["clip_ids"],
        "replacement_clip_ids": summary["replacement_clip_ids"], "summary": summary, "warnings": summary["warnings"], "message": summary["message"]}
    return await _workflow_commit(candidate["project"], candidate["expected"], envelope, "multicam_flatten", body.get("actor", "human"))


@app.post("/api/multicam/flatten/review")
async def multicam_flatten_review(req: Request):
    return (await _multicam_flatten_candidate(await req.json()))["report"]


@app.post("/api/multicam/flatten")
async def multicam_flatten_apply(req: Request):
    body = await req.json()
    if not isinstance(body, dict) or not isinstance(body.get("fingerprint"), str) or len(body["fingerprint"]) != 64 or any(c not in "0123456789abcdef" for c in body["fingerprint"]):
        raise HTTPException(400, "Flatten requires the exact reviewed fingerprint and saved context; review it first")
    _source_command_policy(body)
    candidate = await _multicam_flatten_candidate(body); planned = candidate["plan"]
    if body["fingerprint"] != planned["fingerprint"]: raise HTTPException(409, "The reviewed multicamera edit changed; review the saved selection again")
    if not planned["ok"]: raise HTTPException(422, candidate["report"])
    try:
        apply_ops(candidate["project"], planned["ops"])
        parse_project(json.dumps(candidate["project"], allow_nan=False).encode("utf-8"))
    except (ValueError, TypeError, KeyError) as error: raise HTTPException(422, str(error)) from error
    commit = asyncio.create_task(_commit_multicam_flatten(candidate, body))
    try: return await asyncio.shield(commit)
    except asyncio.CancelledError:
        while not commit.done():
            try: await asyncio.shield(commit)
            except asyncio.CancelledError: continue
            except Exception: break
        if not commit.cancelled(): commit.exception()
        raise


async def _commit_sequence_creation(project, expected, planned, body):
    _source_command_policy(body)
    envelope = {"kind": "sequence_creation", "mode": planned["mode"], "changed": True, "project": expected["project"],
        "sequence": planned["sequence"]["id"], "source_sequence": planned["source_sequence"],
        "media_id": planned["media_id"], "clip_id": planned["clip_id"], "summary": planned["summary"],
        "warnings": planned["warnings"], "message": planned["summary"]["message"]}
    return await _workflow_commit(project, expected, envelope, "sequence_create", body.get("actor", "human"))


@app.post("/api/sequence/create")
async def sequence_create(req: Request):
    import sequence_creation
    body = await req.json(); project, expected = _workflow_capture(body)
    _source_command_policy(body)
    for key in ("actor", "client"):
        if key in body and (not isinstance(body[key], str) or not body[key].strip() or len(body[key]) > 120):
            raise HTTPException(422, "Sequence actor/client must be nonempty text of at most 120 characters")
    try:
        planned = await asyncio.to_thread(sequence_creation.plan, project, body, identity=uuid.uuid4().hex)
        apply_ops(project, planned["ops"])
        parse_project(json.dumps(project, allow_nan=False).encode("utf-8"))
    except (ValueError, TypeError, KeyError) as error: raise HTTPException(422, str(error)) from error
    with LOCK: require_project_context({"_context": expected}, active_id(), load_project())
    commit = asyncio.create_task(_commit_sequence_creation(project, expected, planned, body))
    try: return await asyncio.shield(commit)
    except asyncio.CancelledError:
        while not commit.done():
            try: await asyncio.shield(commit)
            except asyncio.CancelledError: continue
            except Exception: break
        if not commit.cancelled(): commit.exception()
        raise


@app.post("/api/clip/replace-source")
async def clip_replace_source(req: Request):
    import source_replacement
    body = await req.json(); project, expected = _workflow_capture(body)
    if body.get("actor", "human") != "human":
        try:
            with open(P("settings.json"), encoding="utf-8") as stream: mode = json.load(stream).get("agent_mode") or "direct"
        except FileNotFoundError: mode = "direct"
        except (OSError, ValueError, TypeError, AttributeError) as error:
            raise HTTPException(403, "Could not read agent editing preferences; repair settings before source replacement") from error
        if mode == "proposals_only": raise HTTPException(403, "Direct agent edits are disabled. Ask the editor to replace the source or submit a proposal.")
    try:
        planned = source_replacement.plan(project, body)
        if not planned["changed"]: return {"ok": True, **planned["summary"], "context": expected}
        apply_ops(project, [{"op": "set", "path": planned["path"], "value": planned["clip"]}])
        parse_project(json.dumps(project, allow_nan=False).encode("utf-8"))
    except (ValueError, TypeError, KeyError) as error: raise HTTPException(422, str(error)) from error
    return await _workflow_commit(project, expected, planned["summary"], "replace_source", body.get("actor", "human"))

@app.post("/api/workflow/action")
async def workflow_action(req: Request):
    import editing_workflow
    body = await req.json(); proj, expected = _workflow_capture(body)
    try: after, summary = editing_workflow.build(proj, body)
    except (ValueError, TypeError, KeyError) as error: raise HTTPException(400, str(error)) from error
    return await _workflow_commit(after, expected, summary, body["action"], body.get("actor", "human"))

@app.post("/api/workflow/transcribe")
async def workflow_transcribe(req: Request):
    return await _workflow_transcription(await req.json(), words=True, required=True)

async def _workflow_transcription(body, *, words, required=False):
    import editing_workflow
    proj, expected = _workflow_capture(body, required=required)
    sid = body.get("sequence", "seq1"); model = body.get("model", "base")
    if model not in ("tiny", "base", "small", "medium", "large-v3"):
        raise HTTPException(400, "Choose a supported speech model")
    try: seq = editing_workflow.sequence(proj, sid)
    except ValueError as error: raise HTTPException(400, str(error)) from error
    if not any(t.get("clips") for t in seq["tracks"]): raise HTTPException(400, "Add footage to this sequence first")
    # Fail before rendering a potentially long WAV if the optional engine is absent.
    try:
        import faster_whisper
    except ImportError:
        if getattr(sys, "frozen", False):
            return JSONResponse({"error": "Speech tools are unavailable in this packaged build. Use the source installation with --with-whisper for transcription; in-place installation into an executable is not supported."}, status_code=501)
        if words:
            return JSONResponse({"error": "Speech tools are not installed. Open System Check → Install auto-captions, then retry. The first transcription downloads the selected model."}, status_code=501)
        try: import whisper
        except ImportError:
            return JSONResponse({"error": "Speech tools are not installed. Open System Check → Install auto-captions, then retry."}, status_code=501)
    result = await _owned_render_thread(_transcribe_sequence, proj, sid, model, word_timestamps=words)
    if isinstance(result, JSONResponse): return result
    transcript_words, caps = result
    if words and not transcript_words:
        raise HTTPException(400, "No speech was detected. Check that dialogue is audible and try again.")
    replace_captions = not words or body.get("captions", not required)
    if words:
        old_timing = [(w.get("w"), w.get("s"), w.get("e")) for w in seq.get("transcript", [])]
        new_timing = [(w.get("w"), w.get("s"), w.get("e")) for w in transcript_words]
        if not replace_captions and old_timing != new_timing and seq.get("captions"):
            seq.setdefault("workflow", {})["caption_review_ids"] = [c["id"] for c in seq["captions"]]
        seq["transcript"] = transcript_words
        seq["transcript_basis"] = editing_workflow.transcript_basis(seq, proj)
    if replace_captions:
        seq["captions"] = caps
        seq.setdefault("workflow", {})["caption_review_ids"] = []
    return await _workflow_commit(proj, expected, {"sequence": sid, "words": len(transcript_words), "captions": len(caps), "count": len(caps),
        "message": f"Transcribed {len(transcript_words)} words" if words else f"Created {len(caps)} captions"}, "transcribe", body.get("actor", "human"))

@app.post("/api/workflow/import")
async def workflow_import(req: Request, files: list[UploadFile] = File(...)):
    try: body = {"_context": json.loads(req.headers.get("x-filmocity-context", "null"))}
    except (ValueError, TypeError): raise HTTPException(400, "Invalid project context") from None
    proj, expected = _workflow_capture(body)
    if not 1 <= len(files) <= 100: raise HTTPException(400, "Import 1–100 files at a time")
    project_file = P("projects", expected["project"], "project.json")
    added, paths = [], []
    os.makedirs(P("media"), exist_ok=True); os.makedirs(P("thumbs"), exist_ok=True)
    committed = False
    try:
        for upload in files:
            # A filename is a display label, never a destination path.
            name = (upload.filename or "Media").replace("\\", "/").split("/")[-1][:240] or "Media"
            suffix = os.path.splitext(name)[1]
            if len(suffix) > 12 or not all(c.isalnum() or c == "." for c in suffix): suffix = ""
            dest = P("media", uuid.uuid4().hex + suffix); paths.append(dest)
            def copy_upload(proc_holder=None):
                with open(dest, "xb") as stream: shutil.copyfileobj(upload.file, stream)
            await _owned_render_thread(copy_upload)
            try: info = await _owned_render_thread(lambda proc_holder=None: probe(dest))
            except (ValueError, OSError) as error: raise HTTPException(400, f"Could not read {name}") from error
            if not (info.get("has_video") or info.get("has_audio")) or not math.isfinite(info.get("duration", 0)) or info.get("duration", 0) <= 0:
                raise HTTPException(400, f"{name} has no supported media streams or duration")
            mid, token = uuid.uuid4().hex[:16], uuid.uuid4().hex
            media = {"id": mid, "name": name, "path": dest, **info, "thumb": None, "strip": None, "wave": None,
                     "status": "ingesting", "added": time.time(), "ingest_token": token, "workflow_import": True}
            proj["media"][mid] = media; added.append(media)
        # Keep files on uncertain commit failure: recovery may need them. An
        # optimistic-context rejection is known to precede the transaction.
        committed = True
        try:
            result = await _workflow_commit(proj, expected, {"added": [m["id"] for m in added], "message": f"Imported {len(added)} files"}, "import")
        except HTTPException:
            committed = False; raise
        for media in added:
            try:
                finish_ingest(media["id"], media["path"], media, project_file, media["ingest_token"])
            except RuntimeError: result["warning"] += " Thumbnail preparation could not start; imported originals remain available."
        return result
    finally:
        if not committed:
            for path in paths:
                try: os.remove(path)
                except FileNotFoundError: pass

# ---------- owned sequence transcription ----------
def _transcribe_sequence(proj, seq_id, model, *, word_timestamps=False, proc_holder=None, progress=None):
    """The private WAV lives through encoding and lazy transcription readers."""
    with RenderContext(proc_holder=proc_holder) as context:
        wav = context.new_file(".wav")
        if progress: progress("Preparing audio", None)
        do_render(proj, seq_id, wav, {"format": "audio", "acodec": "wav"}, context=context, progress=(lambda value: progress("Preparing audio", value)) if progress else None)
        context.check_cancelled()
        from work_budget import work
        with work(context.holder, 'speech', check=context.check_cancelled):
            if progress: progress("Loading speech model", None, "First use may download the selected model.")
            words, caps = [], []
            segs = speech_model = None
            try:
                try:
                    from faster_whisper import WhisperModel
                    speech_model = WhisperModel(model, compute_type="int8")
                    segs, _ = speech_model.transcribe(wav, vad_filter=True, word_timestamps=word_timestamps)
                    for sg in segs:
                        context.check_cancelled()
                        if progress: progress("Transcribing", min(.999, float(sg.end) / max(.01, seq_total(next(s for s in proj["sequences"] if s["id"] == seq_id)))))
                        caps.append({"id": str(uuid.uuid4())[:8], "start": float(sg.start), "end": float(sg.end), "text": sg.text.strip()})
                        if word_timestamps:
                            for w in (sg.words or []): words.append({"w": w.word.strip(), "s": float(w.start), "e": float(w.end), "p": float(getattr(w, "probability", 1.0))})
                except ImportError:
                    if word_timestamps:
                        return JSONResponse({"error": "faster-whisper is not installed. Run the installer with --with-whisper (or pip install faster-whisper in Filmocity/.venv)."}, status_code=501)
                    try:
                        import whisper
                        speech_model = whisper.load_model(model)
                        res = speech_model.transcribe(wav)
                        caps = [{"id": str(uuid.uuid4())[:8], "start": float(sg["start"]), "end": float(sg["end"]), "text": sg["text"].strip()} for sg in res["segments"]]
                    except ImportError:
                        return JSONResponse({"error": "No transcriber installed. Run the installer with --with-whisper (or pip install faster-whisper in Filmocity/.venv), then retry."}, status_code=501)
            finally:
                try:
                    close = getattr(segs, 'close', None)
                    if close: close()
                finally:
                    segs = speech_model = None
        context.check_cancelled()
        return words, caps


# ---------- persistent background preparation and analysis ----------
def _task_transcribe(payload, task):
    project, sid = payload["project"], payload["sequence"]
    _, signature = task_inputs.capture(project, sid)
    if signature != payload["signature"]: raise ValueError("Source files changed before transcription; start a new task")
    result = _transcribe_sequence(project, sid, payload["model"], word_timestamps=True, proc_holder=task.holder, progress=task.progress)
    if isinstance(result, JSONResponse): raise ValueError("Speech tools are unavailable; open System Check")
    words, _ = result
    if not words: raise ValueError("No speech detected. Check that dialogue is audible.")
    task.check()
    if task_inputs.capture(project, sid)[1] != signature: raise ValueError("Source files changed during transcription; start a new task")
    import editing_workflow
    seq = editing_workflow.sequence(project, sid); seq["transcript"] = words
    seq["transcript_basis"] = editing_workflow.transcript_basis(seq, project)
    editing_workflow.words_of(seq, project)
    return {"words": words, "sequence": sid, "signature": signature}

def _task_package(payload, task):
    from project_package import export_package, import_package
    common = dict(check=task.check, progress=task.progress, publish=task.commit_result)
    if payload["mode"] == "package":
        task.progress("Collecting project resources", None, "Resolving the captured project and its files")
        return export_package(payload["project"], payload["destination"], task.id, **common)
    task.progress("Verifying package", None, "Checking the manifest and resource hashes")
    return import_package(payload["manifest"], payload["destination"], task.id, **common)

@app.post("/api/tasks/package")
async def background_package(req: Request):
    return _queue_package(await req.json(), "package")

@app.post("/api/tasks/package_import")
async def background_package_import(req: Request):
    return _queue_package(await req.json(), "package_import")

def _queue_package(body, mode):
    if TASKS is None: raise HTTPException(503, "Background tasks are unavailable")
    project, expected = _workflow_capture(body)
    with LOCK:
        root = ROOT
        if workspace_id(root) != expected["workspace"]: raise HTTPException(409, "Workspace changed")
    path = body.get("path")
    if not isinstance(path, str) or not path.strip() or not os.path.isabs(path.strip()):
        raise HTTPException(400, "Enter a full local folder path or package manifest path")
    path = os.path.abspath(path.strip())
    payload = {"mode": mode, "root": root}
    if mode == "package": payload.update(project=project, destination=path)
    else: payload.update(manifest=path, destination=os.path.join(root, "projects"))
    try:
        task = TASKS.submit(mode, project.get("name", "Project") if mode == "package" else "Import project package",
                            expected, payload, identity=body.get("request_id"))
    except (TaskError, OSError) as error: raise HTTPException(409, str(error)) from error
    return {"ok": True, "task": task, "context": expected,
            "message": "Package queued. See Tasks for progress and the verified result."}

@app.get("/api/tasks")
def background_task_list():
    if TASKS is None: raise HTTPException(503, "Background tasks are unavailable")
    with LOCK:
        pid = active_id(); context = project_context(ROOT, pid, load_project())
    return {**TASKS.catalog(pid), "context": context}

@app.post("/api/tasks/transcribe")
async def background_transcribe(req: Request):
    import editing_workflow
    body = await req.json(); proj, expected = _workflow_capture(body)
    sid = body.get("sequence"); model = body.get("model", "base")
    if model not in ("tiny", "base", "small", "medium", "large-v3"): raise HTTPException(400, "Choose a supported speech model")
    try:
        seq = editing_workflow.sequence(proj, sid)
        if not any(t.get("clips") for t in seq["tracks"]): raise ValueError("Add footage before transcription")
        snapshot, signature = task_inputs.capture(proj, sid)
    except (ValueError, OSError, KeyError) as error: raise HTTPException(400, str(error)) from error
    try: import faster_whisper
    except ImportError:
        detail = "Speech tools are unavailable in this packaged build. Use a speech-enabled source installation." if getattr(sys, "frozen", False) else "Speech tools are missing. Open System Check → Install auto-captions."
        raise HTTPException(501, detail)
    try:
        task = TASKS.submit("transcribe", seq.get("name", "Sequence"), expected,
            {"sequence": sid, "model": model, "project": snapshot, "signature": signature}, identity=body.get("request_id"))
    except (TaskError, OSError) as error: raise HTTPException(409, str(error)) from error
    return {"ok": True, "task": task, "context": expected, "message": "Transcription queued. Keep editing; apply the result from Tasks when ready."}

def _owned_task(identity):
    try: value = TASKS.get(identity)
    except TaskError as error: raise HTTPException(404, str(error)) from error
    with LOCK:
        if value["record"]["context"]["workspace"] != workspace_id(ROOT) or value["record"]["context"]["project"] != active_id():
            raise HTTPException(409, "Open the task's original project first")
    return value

@app.post("/api/tasks/{identity}/cancel")
def background_task_cancel(identity: str):
    value = _owned_task(identity)
    try: task = TASKS.cancel(identity)
    except TaskError as error: raise HTTPException(409, str(error)) from error
    if value["record"]["kind"] == "media" and value["record"]["status"] == "queued" and task["status"] == "cancelled":
        _task_media_update(value["payload"], {"status": "cancelled", "ingest_error": "Preparation cancelled before starting", "task_id": identity})
    return {"ok": True, "task": task}

@app.get("/api/tasks/{identity}/result")
def background_task_result(identity: str):
    value = _owned_task(identity)
    if value["result"] is None: raise HTTPException(409, "No completed task result is available")
    return JSONResponse({"task": value["record"], "result": value["result"]}, headers={"Content-Disposition": 'attachment; filename="Filmocity-' + ({'transcribe': 'transcript', 'collect': 'collection-receipt', 'sync': 'sync-result', 'analysis': 'analysis-result', 'audio_analysis': 'audio-result', 'recipe': 'recipe-result', 'cover': 'cover-result', 'render_replace': 'render-receipt'}.get(value['record']['kind'], 'package-receipt')) + '-' + identity + '.json"'})

@app.post("/api/tasks/{identity}/retry")
async def background_task_retry(identity: str, req: Request):
    value = _owned_task(identity); body = await req.json(); proj, expected = _workflow_capture(body)
    record, payload = value["record"], value["payload"]
    if record["context"]["project"] != expected["project"]: raise HTTPException(409, "Project changed")
    if record["status"] not in ("cancelled", "error", "interrupted"): raise HTTPException(409, "Only failed, cancelled or interrupted tasks can be retried")
    try:
        if record["kind"] == "cover":
            import cover_workflow as cover
            await asyncio.to_thread(cover.validate_current, proj, payload, expected, ROOT, ASSETS)
            with LOCK:
                require_project_context({"_context": expected}, active_id(), load_project())
                task = TASKS.submit(record["kind"], record["name"], expected, payload, identity=body.get("request_id"), retry_of=identity)
            return {"ok": True, "task": task}
        elif record["kind"] == "recipe":
            import recipe_workflow as recipe
            await asyncio.to_thread(recipe.validate_current, proj, payload, expected, ROOT, ASSETS)
            with LOCK:
                require_project_context({"_context": expected}, active_id(), load_project())
                task = TASKS.submit(record["kind"], record["name"], expected, payload, identity=body.get("request_id"), retry_of=identity)
            return {"ok": True, "task": task}
        elif record["kind"] == "audio_analysis":
            import audio_workflow as audio
            await asyncio.to_thread(audio.validate_current, proj, payload, expected)
            with LOCK:
                require_project_context({"_context": expected}, active_id(), load_project())
                task = TASKS.submit(record["kind"], record["name"], expected, payload, identity=body.get("request_id"), retry_of=identity)
            return {"ok": True, "task": task}
        elif record["kind"] == "sync":
            import audio_sync as sync
            await asyncio.to_thread(sync.validate_current, proj, payload, expected)
            with LOCK:
                require_project_context({"_context": expected}, active_id(), load_project())
                task = TASKS.submit(record["kind"], record["name"], expected, payload, identity=body.get("request_id"), retry_of=identity)
            return {"ok": True, "task": task}
        elif record["kind"] == "render_replace":
            import render_replace as bake
            bake.validate_current(proj, payload, expected, ROOT)
        elif record["kind"] == "analysis":
            import media_analysis
            media_analysis.validate_current(proj, payload, expected)
        elif record["kind"] == "transcribe":
            if task_inputs.capture(proj, payload["sequence"])[1] != payload["signature"]: raise ValueError("The analysis input changed. Start a new transcription from the current sequence.")
        elif record["kind"] == "collect":
            from collection_workflow import current_inputs
            if os.path.abspath(payload["root"]) != os.path.abspath(ROOT): raise ValueError("Workspace changed")
            current_inputs(proj, payload)
            # The worker revalidates captured file identities before copying.
        elif record["kind"] in ("package", "package_import"):
            if os.path.abspath(payload["root"]) != os.path.abspath(ROOT): raise ValueError("Workspace changed; start a new package task")
            # Retry the captured snapshot. A new request exports current edits.
        elif not _task_media_current(payload) or task_inputs.source_stamp({**payload["info"], "path": payload["path"]}) != payload["stamp"]:
            raise ValueError("Media changed; prepare the current media instead")
        task = TASKS.submit(record["kind"], record["name"], expected, payload, identity=body.get("request_id"), retry_of=identity)
    except (TaskError, ValueError, OSError, MediaCollectionError) as error: raise HTTPException(409, str(error)) from error
    return {"ok": True, "task": task}

@app.post("/api/tasks/media/{mid}/prepare")
async def background_media_prepare(mid: str, req: Request):
    proj, expected = _workflow_capture(await req.json()); media = proj.get("media", {}).get(mid)
    if not media or not media.get("ingest_token"): raise HTTPException(400, "Relink this media to establish a preparation source")
    if media.get("audio_alias"):
        import source_commands
        try: media = source_commands.alias_source(proj, media)
        except (ValueError, KeyError, TypeError) as error: raise HTTPException(409, str(error)) from error
    result = finish_ingest(mid, media["path"], media, P("projects", expected["project"], "project.json"), media["ingest_token"])
    if result.get("error"): raise HTTPException(409, result["error"])
    return {"ok": True, "task": result}

@app.post("/api/tasks/{identity}/apply")
async def background_task_apply(identity: str, req: Request):
    import editing_workflow
    value = _owned_task(identity); body = await req.json(); proj, expected = _workflow_capture(body)
    record, payload = value["record"], value["payload"]
    if record["kind"] == "recipe": return await _apply_recipe_workflow(identity, value, body, proj, expected)
    if record["kind"] == "audio_analysis": return await _apply_audio_workflow(identity, value, body, proj, expected)
    if record["kind"] == "sync": return await _apply_audio_sync(identity, value, body, proj, expected)
    if record["kind"] == "render_replace": return await _apply_render_replace(identity, value, body, proj, expected)
    if record["kind"] == "analysis": return await _apply_media_analysis(identity, value, body, proj, expected)
    if record["kind"] == "collect": return await _apply_collection(identity, value, body, proj, expected)
    if record["kind"] != "transcribe" or record["context"]["project"] != expected["project"]: raise HTTPException(409, "This task cannot update this project")
    try: seq = editing_workflow.sequence(proj, payload["sequence"])
    except ValueError as error: raise HTTPException(409, str(error)) from error
    # The task marker commits atomically with the transcript/history. A lost
    # reply or task-receipt write cannot duplicate the project mutation.
    if seq.get("workflow", {}).get("transcript_task") == identity:
        TASKS.finish_apply(identity, success=True, message="Transcript already applied")
        return {"ok": True, "context": expected, "sequence": seq["id"], "message": "Transcript already applied"}
    try:
        if task_inputs.capture(proj, seq["id"])[1] != payload["signature"]:
            raise ValueError("The sequence, transcript or source changed. Download this result for reference, or start a new transcription.")
        claimed = TASKS.begin_apply(identity)
    except (ValueError, OSError) as error: raise HTTPException(409, str(error)) from error
    try:
        words = claimed["result"]["words"]
        if seq.get("captions") and seq.get("transcript") != words:
            seq.setdefault("workflow", {})["caption_review_ids"] = [c["id"] for c in seq["captions"]]
        seq["transcript"] = copy.deepcopy(words); seq["transcript_basis"] = editing_workflow.transcript_basis(seq, proj)
        editing_workflow.words_of(seq, proj)
        seq.setdefault("workflow", {})["transcript_task"] = identity
        result = await _workflow_commit(proj, expected, {"sequence": seq["id"], "words": len(words),
            "message": f"Applied {len(words)} transcribed words. Existing caption edits were preserved."}, "transcribe", body.get("actor", "human"))
    except BaseException:
        TASKS.finish_apply(identity, success=False, message="Apply did not finish. Check project/recovery before retrying.")
        raise
    task = TASKS.finish_apply(identity, success=True, message="Transcript applied")
    if task.get("warning"): result["warning"] = (result.get("warning", "") + "; " + task["warning"]).strip('; ')
    return result


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
def preview_state(body=None):
    with LOCK:
        pid = active_id(); project = copy.deepcopy(load_project())
        require_project_context(body or {}, pid, project)
        return project, project_context(ROOT, pid, project)

def preview_descriptor(job, project, context, revisions=None):
    binding = job.get("preview")
    if job.get("status") != "done" or not binding or not matches_context(binding["context"], context): return None
    try: current = preview_binding(project, context, binding["sequence"], binding["ranged"], revisions)
    except ValueError: return None
    if current != binding: return None
    from export_storage import output_path
    try: path = output_path(P("renders"), job.get("out"))
    except (ValueError, OSError): return None
    if not os.path.isfile(path) or os.path.getsize(path) <= 0: return None
    return {"id": job["id"], "out": job["out"], "preview": copy.deepcopy(binding)}

@app.get("/api/render/segments")
def render_segments(sequence: str = "seq1", start: float | None = None, end: float | None = None):
    """Cache coverage and a validated preview; arbitrary old filenames are not adopted."""
    proj, context = preview_state(); seq = next((s for s in proj["sequences"] if s["id"] == sequence), None)
    if not seq: raise HTTPException(404)
    import math
    if (start is None) != (end is None) or (start is not None and
            (not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start)):
        raise HTTPException(422, "Render coverage requires a finite, nonnegative start and a later end.")
    pts = segment_boundaries(seq); cache = P("renders", "cache"); out = []; revisions = {}
    index = index_sequence(seq)
    with RENDER_STATE_LOCK: candidates = [copy.deepcopy(j) for j in JOBS.values() if j.get("sequence") == sequence and j.get("preview") and j.get("status") == "done"]
    ready = next((d for j in sorted(candidates, key=lambda j: j["started"], reverse=True)
                  if (d := preview_descriptor(j, proj, context, revisions))), None)
    from segment_cache import reading, usable
    try:
        with reading(cache):
            for t0, t1 in zip(pts, pts[1:]):
                if start is not None and (t1 <= start or t0 >= end): continue
                key = chunk_key(proj, chunk_sequence(seq, t0, t1, index=index), PREVIEW_PRESET, revisions)
                cached = usable(os.path.join(cache, key + ".mp4"))
                rendered = bool(ready and ready["preview"]["range"][0] <= t0 and ready["preview"]["range"][1] >= t1)
                out.append({"t0": t0, "t1": t1, "cached": cached, "rendered": rendered})
    except (OSError, ValueError) as error:
        raise HTTPException(422, "Segment cache is unavailable: " + str(error)[:300]) from error
    # Project changes during filesystem hashing make this response stale.
    preview_state({"_context": context})
    return {"context": context, "sequence": sequence, "segments": out, "window": [start, end] if start is not None else None, "preview": None, "ready_preview": ready}

@app.post("/api/render/preview/{jid}/validate")
async def validate_render_preview(jid: str, req: Request):
    body = await req.json(); proj, context = preview_state(body)
    with RENDER_STATE_LOCK: job = copy.deepcopy(JOBS.get(jid))
    if not job: raise HTTPException(404, "Preview job no longer exists.")
    ready = await asyncio.to_thread(preview_descriptor, job, proj, context)
    preview_state({"_context": context})
    if not ready: raise HTTPException(409, "Preview no longer matches the saved project or source files. Render it again.")
    return ready

@app.post("/api/render/{jid}/cancel")
async def render_cancel(jid: str):
    from job_history import remember
    with RENDER_STATE_LOCK:
        j = JOBS.get(jid)
        if not j: raise HTTPException(404)
        if j["status"] == "queued":
            j.update(status="error", error="cancelled", finished=time.time()); remember(ROOT, j)
            return {"ok": True}
        h = RENDER_PROCS.get(jid)
    if h is not None and j["status"] in ("running", "cancelling"):
        # Build/hash/validation phases need no active encoder. All publication
        # paths use this lock; cancellation after their commit is refused.
        with h.setdefault("png_publication_lock", threading.Lock()):
            if h.get("png_published") or h.get("published"): return {"ok": False}
            with RENDER_STATE_LOCK:
                if j["status"] not in ("running", "cancelling"): return {"ok": False}
                h["cancelled"] = True; j["status"] = "cancelling"; remember(ROOT, j)
            # The encoder supervisor observes this holder, kills, waits, and
            # joins its drainers before its context can retire.
        return {"ok": True}
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

def _prepare_sample_project(name, pid, expected, actor, check):
    from project_lifecycle import sample_file
    d = P("sample_media"); os.makedirs(d, exist_ok=True)
    gens = [("shot_01_open.mp4", "testsrc2=s=1280x720:r=30", 330), ("shot_02_product.mp4", "smptehdbars=s=1280x720:r=30", 440), ("shot_03_ride.mp4", "rgbtestsrc=s=1280x720:r=30", 550)]
    for sample_name, src, hz in gens:
        check()
        f = os.path.join(d, sample_name)
        if not os.path.exists(f): sample_file(f, ["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", src, "-f", "lavfi", "-i", f"sine=frequency={hz}:sample_rate=48000", "-t", "8", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac"], check)
    mus = os.path.join(d, "music_bed.m4a")
    if not os.path.exists(mus): sample_file(mus, ["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", "sine=frequency=110:sample_rate=48000,tremolo=f=2:d=0.9", "-t", "30", "-c:a", "aac"], check)
    still = os.path.join(d, "end_card.png")
    if not os.path.exists(still): sample_file(still, ["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", "color=c=0xE8631C:s=1080x1920", "-frames:v", "1", "-update", "1"], check)
    proj = default_project(); proj["name"] = name; proj["brief"] = {"client": "Sample Co", "objective": "Show every panel with real content", "platform": "reels", "audience": "you, on first launch", "constraints": "none — this is a tour", "notes": "generated footage; delete this project any time"}
    ids = {}
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
    check()
    return _commit_prepared_project(proj, expected, pid, actor, "sample", check)

@app.post("/api/projects/sample")
async def projects_sample(req: Request):
    """Generate and prepare a complete tour before guarded project activation."""
    from project_lifecycle import COPY_WORKER, ProjectActionError, project_name
    body = await req.json(); _, expected = _workflow_capture(body)
    try: name = project_name(body.get("name"), "Sample — Filmocity tour")
    except ProjectActionError as error: raise HTTPException(400, str(error)) from error
    pid = "sample-" + uuid.uuid4().hex
    try:
        result, event = await COPY_WORKER.run(lambda check: _prepare_sample_project(name, pid, expected, body.get("actor", "human"), check))
    except (ProjectActionError, subprocess.SubprocessError) as error:
        raise HTTPException(422, "Sample preparation failed; the active project was kept. " + str(error)[:400]) from error
    with LOCK: proj = _read_project_target(pid)
    for m in proj["media"].values(): finish_ingest(m["id"], m["path"], m, P("projects", pid, "project.json"), m["ingest_token"])
    return await _project_action_notify(result, event)

@app.get("/api/backups")
def backups_list():
    return saved_versions("backups")["versions"]

@app.post("/api/backups/restore")
async def backups_restore(req: Request):
    return await restore_saved_version(await req.json(), "backups")

@app.post("/api/media/breakout")
async def media_breakout(req: Request):
    return await _source_creation_command(await req.json(), "breakout")

@app.post("/api/media/duplicate")
async def media_duplicate(req: Request):
    return await _source_creation_command(await req.json(), "duplicate")

def existing_preview_request(request_id, context, sequence, ranged):
    if not request_id: return None
    with RENDER_STATE_LOCK:
        for job in JOBS.values():
            if job.get("preview_request") == request_id:
                binding = job.get("preview") or {}
                if (binding.get("context") != context or binding.get("sequence") != sequence or binding.get("ranged") != ranged):
                    raise HTTPException(409, "Preview request identity was already used for a different edit.")
                return job
    return None

@app.post("/api/render/preview")
async def render_preview(req: Request):
    """Render a saved sequence or In–Out range to a unique, revision-bound file."""
    body = await req.json(); seq_id = body.get("sequence", "seq1"); proj, context = preview_state(body)
    request_id = body.get("request_id")
    if request_id is not None and (not isinstance(request_id, str) or not 8 <= len(request_id) <= 80 or not all(c.isalnum() or c == "-" for c in request_id)):
        raise HTTPException(422, "Invalid preview request identity.")
    ranged = bool(body.get("range", False)); preset = dict(PREVIEW_PRESET, range=ranged)
    existing = existing_preview_request(request_id, context, seq_id, ranged)
    if existing: return existing
    report = await asyncio.to_thread(check_render_resources, proj, seq_id, preset)
    try: binding = await asyncio.to_thread(preview_binding, proj, context, seq_id, ranged)
    except ValueError as error: raise HTTPException(422, str(error))
    # Recheck after slow source inspection and queue while owning project identity.
    with LOCK:
        require_project_context({"_context": context}, active_id(), load_project())
        existing = existing_preview_request(request_id, context, seq_id, ranged)
        if existing: return existing
        return start_render(proj, seq_id, preset, "preview_" + uuid.uuid4().hex,
                            body.get("actor", "human"), report, preview=binding, request_id=request_id)

@app.post("/api/render_all")
async def render_all(req: Request):
    """Export every sequence in the project with one preset (batch)."""
    body = await req.json(); preset = body.get("preset", {}); proj, context = preview_state(body); jobs = []
    sequences = [sq for sq in proj["sequences"] if not sq.get("multicam") and not sq.get("merged")]
    reports = [await asyncio.to_thread(check_export_resources, proj, sq["id"], preset) for sq in sequences]
    preview_state({"_context": context})
    for sq, report in zip(sequences, reports):
        name = "".join(ch for ch in sq["name"] if ch.isalnum() or ch in "-_ ").strip().replace(" ", "_") or sq["id"]; jobs.append(start_render(proj, sq["id"], preset, f"{body.get('prefix', 'batch')}_{name}", body.get("actor", "human"), report, context=context))
    return {"jobs": jobs}

# ---------- interpret footage / extract audio ----------
async def _interpretation_candidate(body):
    import source_interpretation
    project, expected = _workflow_capture(body)
    for key in ("actor", "client"):
        if key in body and (not isinstance(body[key], str) or not body[key].strip() or len(body[key]) > 120):
            raise HTTPException(422, "Interpretation actor/client must be nonempty text of at most 120 characters")
    owner_root, owner_tasks = os.path.abspath(ROOT), TASKS
    project_file = P("projects", expected["project"], "project.json")
    try:
        candidate = await _owned_render_thread(source_interpretation.inspect, project, body, scratch_parent=owner_root)
        await asyncio.to_thread(source_interpretation.check, candidate["resources"])
    except (ValueError, TypeError, KeyError, OSError) as error: raise HTTPException(422, str(error)) from error
    with LOCK: require_project_context({"_context":expected}, active_id(), load_project())
    planned = candidate["plan"]
    report = {"context":expected, **{key:planned[key] for key in ("ok", "kind", "media_id", "requested_media_id", "settings", "affected_media_ids", "issues", "summary", "fingerprint")}}
    return {**candidate, "project":project, "expected":expected, "report":report,
        "owner_root":owner_root, "owner_tasks":owner_tasks, "project_file":project_file}


async def _commit_source_interpretation(candidate, body, envelope):
    import source_interpretation
    import source_commands
    project, expected, planned = candidate["project"], candidate["expected"], candidate["plan"]
    await asyncio.to_thread(source_interpretation.check, candidate["resources"])
    _source_command_policy(body)
    result = await _workflow_commit(project, expected, {**envelope, "message":planned["summary"]["message"]}, "interpret", body.get("actor", "human"))
    preparation = {"tasks":[], "warnings":[]}
    for identity in planned["prepare_ids"]:
        try:
            if os.path.abspath(ROOT) != candidate["owner_root"] or TASKS is not candidate["owner_tasks"]:
                raise ValueError("Workspace changed; open the original workspace and Prepare its interpreted sources")
            await asyncio.to_thread(source_interpretation.check, candidate["resources"])
            if os.path.abspath(ROOT) != candidate["owner_root"] or TASKS is not candidate["owner_tasks"]:
                raise ValueError("Workspace changed during source validation; prepare previews from the original workspace")
            media = project["media"][identity]
            prepared = source_commands.alias_source(project, media) if media.get("audio_alias") else media
            if not _task_media_current({"media_id":identity, "path":prepared["path"], "token":prepared["ingest_token"],
                    "project_file":candidate["project_file"], "info":prepared}):
                raise ValueError("The interpreted source changed before preparation")
            queued = finish_ingest(identity, prepared["path"], prepared, candidate["project_file"], prepared["ingest_token"])
            if queued.get("error"): raise ValueError(queued["error"])
            preparation["tasks"].append({"media_id":identity, "task":queued})
        except Exception as error:
            preparation["warnings"].append("Source "+identity+" saved; preparation could not be queued: "+str(error)[:300])
    result["preparation"] = preparation
    result["warnings"] = [*result["warnings"], *preparation["warnings"]]
    return result


@app.post("/api/media/interpret/review")
async def media_interpret_review(req: Request):
    return (await _interpretation_candidate(await req.json()))["report"]


@app.post("/api/media/interpret")
async def media_interpret(req: Request):
    body = await req.json()
    if not isinstance(body, dict) or not isinstance(body.get("fingerprint"), str) or len(body["fingerprint"]) != 64 or any(c not in "0123456789abcdef" for c in body["fingerprint"]):
        raise HTTPException(400, "Interpretation requires the exact reviewed fingerprint and saved context; review it first")
    _source_command_policy(body)
    candidate = await _interpretation_candidate(body); planned = candidate["plan"]
    if body["fingerprint"] != planned["fingerprint"]: raise HTTPException(409, "The reviewed interpretation changed; review the saved source again")
    if not planned["ok"]: raise HTTPException(422, candidate["report"])
    summary = planned["summary"]
    envelope = {"kind":"source_interpretation", "project":candidate["expected"]["project"], "changed":bool(planned["ops"]),
        "summary":summary, "warnings":summary["warnings"], "media_id":planned["media_id"], "requested_media_id":planned["requested_media_id"],
        "affected_media_ids":planned["affected_media_ids"], "media":planned["media"][planned["media_id"]]}
    _source_command_policy(body)
    if not planned["ops"]:
        with LOCK: require_project_context({"_context":candidate["expected"]}, active_id(), load_project())
        return {"ok":True, **envelope, "context":candidate["expected"], "preparation":{"tasks":[],"warnings":[]}}
    try:
        apply_ops(candidate["project"], planned["ops"])
        parse_project(json.dumps(candidate["project"], allow_nan=False).encode("utf-8"))
    except (ValueError, TypeError, KeyError) as error: raise HTTPException(422, str(error)) from error
    commit = asyncio.create_task(_commit_source_interpretation(candidate, body, envelope))
    try: return await asyncio.shield(commit)
    except asyncio.CancelledError:
        while not commit.done():
            try: await asyncio.shield(commit)
            except asyncio.CancelledError: continue
            except Exception: break
        if not commit.cancelled(): commit.exception()
        raise

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
    return await _queue_recipe_workflow(await req.json(), "variants")

@app.get("/api/sequence/describe")
def sequence_describe(sequence: str = "", workspace: str = "", project: str = "", revision: str = ""):
    """Owned read of canonical timeline seconds and containing-frame addresses."""
    import source_commands
    if not all((sequence, workspace, project, revision)): raise HTTPException(400, "Sequence description requires sequence and saved workspace/project/revision")
    document, expected = _workflow_capture({"_context": {"workspace": workspace, "project": project, "revision": revision}})
    try: result = source_commands.describe(document, sequence)
    except (ValueError, KeyError, TypeError) as error: raise HTTPException(422, str(error)) from error
    with LOCK: require_project_context({"_context": expected}, active_id(), load_project())
    return {"ok": True, **result, "context": expected, "project": expected["project"]}

@app.get("/api/renders/manifest")
def renders_manifest():
    """Deliverables sheet: every finished render with file, sequence, preset and QA — JSON (and CSV via ?fmt=csv)."""
    rows = [{"job_id": j["id"], "name": j["name"], "file": j.get("out"), "sequence": j.get("sequence"), "status": j["status"], "finished": j.get("finished"), **{f"qa_{k}": v for k, v in (j.get("qa") or {}).items() if k != "flags"}, "flags": "; ".join((j.get("qa") or {}).get("flags") or [])} for j in JOBS.values() if j["status"] == "done"]
    return rows

REVIEW_HTML = """<!doctype html><meta charset="utf-8"><title>Review — {name}</title>
<style>body{{margin:0;background:#111;color:#eee;font:14px system-ui}} .wrap{{max-width:960px;margin:auto;padding:16px}} video{{width:100%;max-height:70vh;background:#000}} .c{{display:flex;gap:8px;margin:10px 0;flex-wrap:wrap}} input,textarea{{flex:1;background:#1c1c1f;color:#eee;border:1px solid #777;border-radius:4px;padding:8px}} button{{background:#246dcc;color:#fff;border:0;border-radius:4px;padding:8px 14px;cursor:pointer}} .note{{padding:8px;border-left:3px solid #f6c14a;margin:6px 0;background:#18181b}} .t{{margin-right:8px}}</style>
<div class="wrap" id="review" data-ref="{ref}"><h2>{name}</h2><video id="v" src="{src}" controls playsinline></video><div class="c"><input id="who" aria-label="Your name" value="client"><textarea id="txt" rows="2" aria-label="Comment at the current time"></textarea><button id="send">Add note</button></div><p id="reviewStatus" role="status" aria-live="polite"></p><div id="list"></div></div>
<script src="/static/review.js?v=1"></script>"""


def review_job(reference):
    with RENDER_STATE_LOCK:
        if reference.startswith("job-"):
            job = JOBS.get(reference[4:])
            candidates = [job] if job and job.get("status") == "done" else []
        else:
            candidates = [job for job in JOBS.values() if job.get("name") == reference and job.get("status") == "done"]
        if not candidates: raise HTTPException(404, "No completed export exists for this review.")
        if len(candidates) != 1: raise HTTPException(409, "Several exports use this name. Open the review link for the intended job.")
        return copy.deepcopy(candidates[0])


def review_project(job):
    # Caller owns LOCK. Review comments must never follow the active-project
    # pointer into a different library/sequence with the same document IDs.
    captured = job.get("context") or (job.get("preview") or {}).get("context")
    if not captured: raise HTTPException(409, "This older export has no project identity. Export a new review version.")
    if captured.get("workspace") != workspace_id(ROOT) or captured.get("project") != active_id():
        raise HTTPException(409, "Open the original project in Filmocity to read or add review notes.")
    project = load_project()
    sequence = next((s for s in project["sequences"] if s["id"] == job.get("sequence")), None)
    if sequence is None: raise HTTPException(409, "The reviewed sequence no longer exists in this project.")
    return project, sequence


@app.get("/review/{name}")
def review_page(name: str):
    from html import escape
    job = review_job(name)
    return HTMLResponse(REVIEW_HTML.format(name=escape(job["name"]), src=escape(job["out"], quote=True), ref=escape("job-" + job["id"], quote=True)))


@app.get("/api/review/{name}/notes")
def review_notes(name: str):
    job = review_job(name)
    with LOCK:
        _, sequence = review_project(job)
        return copy.deepcopy([m for m in sequence.get("markers", []) if m.get("type") == "note" and
                              (m.get("review_job") == job["id"] or (not job.get("review_url") and not m.get("review_job") and m.get("review") == job["name"]))])


@app.post("/api/review/{name}/notes")
async def review_note_add(name: str, req: Request):
    body = await req.json(); job = review_job(name)
    try: position = float(body.get("time", 0))
    except (TypeError, ValueError): raise HTTPException(422, "Review time must be a finite, nonnegative number.")
    if not math.isfinite(position) or position < 0: raise HTTPException(422, "Review time must be a finite, nonnegative number.")
    with LOCK:
        proj, seq = review_project(job); pid = active_id(); before = copy.deepcopy(proj)
        mk = {"id": uuid.uuid4().hex[:8], "time": position + job.get("review_start", 0), "review_time": position, "duration": 0,
              "name": str(body.get("text", ""))[:300], "type": "note", "color": "yellow",
              "author": str(body.get("author", "client"))[:40], "review": job["name"],
              "review_job": job["id"], "resolved": False, "ts": time.time()}
        seq["markers"] = (seq.get("markers") or []) + [mk]; si = proj["sequences"].index(seq)
        ops = [{"op": "set", "path": f"/sequences/{si}/markers", "value": copy.deepcopy(seq["markers"])}]
        event = {"type": "ops", "actor": mk["author"], "tool": "review", "reason": "review note", "ops": ops, "project": pid}
        warning = commit_edit(before, proj, pid, {"ops": ops, "actor": mk["author"], "reason": "review note", "ts": time.time()})
        LAST_OPS_TS["t"] = time.time()
        try: event = log_event(event, project_id=pid)
        except Exception as error: warning = (str(warning) + "; " if warning else "") + "Note saved, but audit recording failed: " + str(error)[:200]
    try: await broadcast({k: v for k, v in event.items() if k != "befores"})
    except Exception as error: warning = (str(warning) + "; " if warning else "") + "Note saved, but editor notification failed: " + str(error)[:200]
    return {**mk, **({"warning": str(warning)} if warning else {})}

@app.post("/api/install/whisper")
async def install_whisper():
    """Install faster-whisper into the running Python (venv) for auto-captions and transcripts; returns when done."""
    if getattr(sys, "frozen", False):
        return {"ok": False, "message": "This packaged build cannot install speech tools in place. Use the source installer with --with-whisper. A packaged speech-enabled build requires separate dependency and native testing."}
    def run(): return subprocess.run([sys.executable, "-m", "pip", "install", "-q", "faster-whisper"], capture_output=True, text=True, timeout=1800)
    r = await asyncio.to_thread(run)
    try:
        import importlib; importlib.invalidate_caches(); import faster_whisper  # noqa
        return {"ok": True, "message": "faster-whisper installed — the first transcript downloads the model"}
    except ImportError: return {"ok": False, "message": (r.stderr or r.stdout)[-400:]}

@app.post("/api/recipes/talking_head")
async def recipe_talking_head(req: Request):
    return await _queue_recipe_workflow(await req.json(), "talking_head")

async def _queue_recipe_workflow(body, mode):
    import recipe_workflow as recipe
    project, expected = _workflow_capture(body)
    if TASKS is None: raise HTTPException(503, "Background tasks are unavailable")
    try:
        import re
        if not isinstance(body.get("request_id"), str) or not re.fullmatch("[a-f0-9]{32}", body["request_id"]): raise ValueError("Supply a unique recipe request_id")
        payload = await asyncio.to_thread(recipe.capture, project, body, mode, expected, ROOT, ASSETS)
        with LOCK:
            require_project_context({"_context": expected}, active_id(), load_project())
            task = TASKS.submit("recipe", {"talking_head": "Talking Head", "reel": "New Reel", "explainer": "Explainer", "variants": "Hook Variants"}[mode], expected, payload, identity=body["request_id"])
    except (TaskError, ValueError, TypeError, KeyError, OSError) as error: raise HTTPException(400, str(error)) from error
    return {"ok": True, "task": task, "context": expected, "message": "Recipe queued. Review its complete plan before applying."}

def _task_recipe_workflow(payload, task):
    import recipe_workflow
    return recipe_workflow.analyze(payload, task)

def _review_recipe_workflow(value, project, expected):
    import recipe_workflow as recipe
    if value["record"]["kind"] != "recipe" or value["record"]["status"] != "ready" or value.get("result") is None:
        raise HTTPException(409, "This recipe is not ready for review")
    try: plan = recipe.review(project, value["payload"], value["result"], expected, value["record"]["id"], ROOT, ASSETS)
    except (ValueError, TypeError, KeyError, OSError) as error: raise HTTPException(409, str(error)) from error
    return {"ok": True, "task": value["record"], "context": expected, "result": value["result"], "plan": plan}

@app.post("/api/tasks/{identity}/recipe")
async def background_recipe_review(identity: str, req: Request):
    value = _owned_task(identity); project, expected = _workflow_capture(await req.json())
    reviewed = await asyncio.to_thread(_review_recipe_workflow, value, project, expected)
    _workflow_capture({"_context": expected})
    if _owned_task(identity)["record"]["status"] != "ready": raise HTTPException(409, "This recipe is no longer ready")
    return reviewed

async def _apply_recipe_workflow(identity, value, body, project, expected):
    import recipe_workflow as recipe
    for sequence in project.get("sequences", []):
        if sequence.get("workflow", {}).get("recipe_tasks", {}).get(identity):
            TASKS.finish_apply(identity, success=True, message="Recipe already applied")
            return {"ok": True, "context": expected, "sequence": sequence["id"], "message": "Recipe already applied; no changes repeated"}
    _audio_edit_policy(body)
    reviewed = await asyncio.to_thread(_review_recipe_workflow, value, project, expected); plan = reviewed["plan"]
    if not isinstance(body.get("fingerprint"), str) or body["fingerprint"] != plan["fingerprint"]:
        raise HTTPException(409, "The recipe review changed; review the plan again before applying")
    _workflow_capture({"_context": expected})
    if not plan["ops"]: return {"ok": True, "context": expected, "changed": False, "message": "The recipe has no changes to apply"}
    try: TASKS.begin_apply(identity)
    except (TaskError, OSError) as error: raise HTTPException(409, str(error)) from error
    try:
        await _owned_render_thread(recipe.publish, value["payload"], value["result"], identity)
        await asyncio.to_thread(recipe.validate_current, project, value["payload"], expected, ROOT, ASSETS)
        apply_ops(project, plan["ops"])
        sequence = next(s for s in project["sequences"] if s["id"] == plan["summary"]["sequence"])
        sequence.setdefault("workflow", {}).setdefault("recipe_tasks", {})[identity] = {"fingerprint": plan["fingerprint"], "mode": value["payload"]["mode"]}
        parse_project(json.dumps(project, allow_nan=False).encode("utf-8"))
        response = await _workflow_commit(project, expected, plan["summary"], "recipe", body.get("actor", "human"))
    except BaseException:
        TASKS.finish_apply(identity, success=False, message="Recipe apply was not confirmed. Inspect the project and Recovery before retrying.")
        raise
    task = TASKS.finish_apply(identity, success=True, message="Recipe applied")
    if task.get("warning"): response["warning"] = (response.get("warning", "") + "; " + task["warning"]).strip("; ")
    return response

@app.post("/api/audio/duck_all")
async def duck_all(req: Request):
    """Review/apply/remove additive music attenuation from audible clip spans."""
    import audio_ducking
    from project_lifecycle import COPY_WORKER, ProjectActionError
    body = await req.json(); project, expected = _workflow_capture(body)
    if type(body.get("preview", False)) is not bool: raise HTTPException(400, "Preview must be true or false")
    try:
        after, summary = await COPY_WORKER.run(lambda check: audio_ducking.build(project, body, check))
    except ProjectActionError as error: raise HTTPException(409, str(error)) from error
    except (ValueError, TypeError, KeyError) as error: raise HTTPException(400, str(error)) from error
    with LOCK:
        require_project_context({"_context": expected}, active_id(), load_project())
        if not body.get("preview") and body.get("preview_plan") is not None and body["preview_plan"] != summary["plan"]:
            raise HTTPException(409, "The ducking choices or saved edit changed. Review the plan again.")
        if body.get("preview") or not summary["ducked"]:
            return {"ok": True, **summary, "context": expected, "preview": bool(body.get("preview"))}
        if body.get("actor", "human") != "human":
            try:
                with open(P("settings.json"), encoding="utf-8") as stream: settings = json.load(stream)
            except FileNotFoundError: settings = {}
            if settings.get("agent_mode") == "proposals_only": raise HTTPException(403, "Direct agent edits are disabled; ask the editor to review ducking.")
    return await _workflow_commit(after, expected, summary, "ducking", body.get("actor", "human"))

@app.post("/api/recipes/cover")
async def recipe_cover(req: Request):
    import cover_workflow as cover
    import re
    body = await req.json(); project, expected = _workflow_capture(body)
    if TASKS is None: raise HTTPException(503, "Background tasks are unavailable")
    try:
        if not isinstance(body.get("request_id"), str) or not re.fullmatch("[a-f0-9]{32}", body["request_id"]): raise ValueError("Supply a unique cover request_id")
        payload = await asyncio.to_thread(cover.capture, project, body, expected, ROOT, ASSETS)
        with LOCK:
            require_project_context({"_context": expected}, active_id(), load_project())
            task = TASKS.submit("cover", "Cover / Thumbnail", expected, payload, identity=body["request_id"])
    except (TaskError, ValueError, TypeError, KeyError, OSError) as error: raise HTTPException(400, str(error)) from error
    return {"ok": True, "task": task, "context": expected, "message": "Cover queued. Review and download its owned PNGs from Tasks."}

def _task_cover_workflow(payload, task):
    import cover_workflow
    return cover_workflow.analyze(payload, task)

def _review_cover_workflow(value, project, expected):
    import cover_workflow as cover
    if value["record"]["kind"] != "cover" or value["record"]["status"] != "ready" or value.get("result") is None:
        raise HTTPException(409, "This cover is not ready for review")
    try: plan = cover.review(project, value["payload"], value["result"], expected, value["record"]["id"], ROOT, ASSETS)
    except (ValueError, TypeError, KeyError, OSError) as error: raise HTTPException(409, str(error)) from error
    return {"ok": True, "task": value["record"], "context": expected, "result": value["result"], "plan": plan}

@app.post("/api/tasks/{identity}/cover")
async def background_cover_review(identity: str, req: Request):
    value = _owned_task(identity); project, expected = _workflow_capture(await req.json())
    reviewed = await asyncio.to_thread(_review_cover_workflow, value, project, expected)
    _workflow_capture({"_context": expected})
    if _owned_task(identity)["record"]["status"] != "ready": raise HTTPException(409, "This cover is no longer ready")
    return reviewed

@app.get("/api/tasks/{identity}/cover/{index}")
async def background_cover_image(identity: str, index: int, workspace: str, project: str, revision: str = None, download: bool = False):
    import cover_workflow as cover
    value = _owned_task(identity)
    owner = value["record"]["context"]
    if workspace != owner["workspace"] or project != owner["project"]: raise HTTPException(409, "Cover download belongs to another project or workspace")
    with LOCK:
        captured = copy.deepcopy(load_project()); expected = project_context(ROOT, active_id(), captured)
        if revision is not None: require_project_context({"_context": {"workspace": workspace, "project": project, "revision": revision}}, active_id(), captured)
    await asyncio.to_thread(_review_cover_workflow, value, captured, expected)
    covers = value["result"]["covers"]
    if type(index) is not int or not 0 <= index < len(covers): raise HTTPException(404, "Cover output not found")
    # Read the verified bounded PNG before responding; a switched project or
    # replaced path cannot be served later by a deferred FileResponse open.
    def read_png():
        path = cover.verify_result(value["payload"], value["result"], identity)[index]
        with path.open("rb") as stream: data = stream.read(min(cover.MAX_OUTPUT_BYTES, covers[index]["size"]) + 1)
        import hashlib
        if len(data) != covers[index]["size"] or hashlib.sha256(data).hexdigest() != covers[index]["sha256"]: raise ValueError("Cover changed before download")
        return data
    try: data = await asyncio.to_thread(read_png)
    except (ValueError, TypeError, KeyError, OSError) as error: raise HTTPException(409, str(error)) from error
    _workflow_capture({"_context": expected})
    if _owned_task(identity)["record"]["status"] != "ready": raise HTTPException(409, "This cover is no longer available")
    headers = {"Cache-Control": "no-store", "Content-Disposition": ('attachment' if download else 'inline') + '; filename="' + covers[index]["filename"] + '"'}
    return Response(content=data, media_type="image/png", headers=headers)

@app.post("/api/recipes/explainer")
async def recipe_explainer(req: Request):
    return await _queue_recipe_workflow(await req.json(), "explainer")

@app.get("/api/projects/recent")
def projects_recent():
    return projects_list()[:12]

@app.post("/api/projects/duplicate")
async def projects_duplicate(req: Request):
    """Copy the saved edit; original snapshots/training/undo remain with the original."""
    body = await req.json(); document, expected, pid = _prepare_project_action(body, "duplicate")
    result, event = _commit_prepared_project(document, expected, pid, body.get("actor", "human"), "duplicate")
    return await _project_action_notify(result, event)

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
    """Framework worker scans captured workspace caches without blocking HTTP."""
    from cache_operations import inspect, CacheBusy
    try: return inspect(ROOT)
    except CacheBusy as error: raise HTTPException(429, str(error)) from error

@app.post("/api/cache/clear")
async def cache_clear(req: Request):
    """The owned worker retains cleanup results even if its request is cancelled."""
    from cache_operations import clear, CacheBusy
    body = await req.json()
    if not isinstance(body, dict): raise HTTPException(422, "Choose a known cache category")
    try: return await clear(ROOT, body.get("what", "segments"))
    except CacheBusy as error: raise HTTPException(409, str(error)) from error
    except ValueError as error: raise HTTPException(422, str(error)) from error

@app.post("/api/recipes/reel")
async def recipe_reel(req: Request):
    return await _queue_recipe_workflow(await req.json(), "reel")

async def audio_remix_segments(m, target, project=None):
    """Bounded recipe ranges from its captured project, never the active project."""
    import audio_remix as remix
    if project is None: raise ValueError("Recipe remix needs its captured project")
    import media_analysis
    captured = copy.deepcopy(project)
    current = captured.get("media", {}).get(m.get("id"))
    if current != m: raise ValueError("The recipe source changed")
    media_analysis.source_inputs(captured, m["id"])
    intervals, _ = remix.design(float(m["duration"]), target)
    return [{"in": begin, "out": end} for begin, end in intervals]

async def media_sfx_internal(kind):
    d = P("sfx"); os.makedirs(d, exist_ok=True); f = os.path.join(d, f"{kind}.wav")
    if not os.path.exists(f): await asyncio.to_thread(lambda: subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", SFX[kind][0], "-t", str(SFX[kind][1]), "-c:a", "pcm_s16le", f], capture_output=True, timeout=60))
    with LOCK:
        proj = load_project()
        for m in proj["media"].values():
            if m.get("sfx") == kind: return m
        mid, m = await asyncio.to_thread(ingest, f, f"SFX {kind}"); m["sfx"] = kind; proj["media"][mid] = m; save_project(proj); return m

def _source_command_policy(body):
    for key in ("actor", "client"):
        if key in body and (not isinstance(body[key], str) or not body[key].strip() or len(body[key]) > 120):
            raise HTTPException(422, "Editorial actor/client must be a nonempty string of at most 120 characters")
    if body.get("actor", "human") == "human": return
    try:
        with open(P("settings.json"), encoding="utf-8") as stream: mode = json.load(stream).get("agent_mode") or "direct"
    except FileNotFoundError: mode = "direct"
    except (OSError, ValueError, TypeError, AttributeError) as error: raise HTTPException(403, "Could not read agent editing preferences") from error
    if mode == "proposals_only": raise HTTPException(403, "Direct agent edits are disabled; submit a proposal or ask the editor to run this command")


async def _commit_source_creation(candidate, body, envelope):
    import source_creation_io
    import source_commands
    project, expected, planned = candidate["project"], candidate["expected"], candidate["plan"]
    await asyncio.to_thread(source_creation_io.check, candidate["resources"])
    _source_command_policy(body)
    result = await _workflow_commit(project, expected, {**envelope, "message":planned["summary"]["message"]}, "import", body.get("actor", "human"))
    preparation = {"tasks":[], "warnings":[]}
    # A saved registration owns each following queue attempt, even when the
    # request disappears. Never bind a late attempt to another workspace.
    for identity in planned["prepare_ids"]:
        try:
            if os.path.abspath(ROOT) != candidate["owner_root"] or TASKS is not candidate["owner_tasks"]:
                raise ValueError("Workspace changed; open the original workspace and explicitly Prepare the saved media")
            await asyncio.to_thread(source_creation_io.check, candidate["resources"])
            if os.path.abspath(ROOT) != candidate["owner_root"] or TASKS is not candidate["owner_tasks"]:
                raise ValueError("Workspace changed during source validation; prepare the saved media from its original workspace")
            media = project["media"][identity]
            prepared = source_commands.alias_source(project, media) if media.get("audio_alias") else media
            if not _task_media_current({"media_id":identity, "path":prepared["path"], "token":prepared["ingest_token"],
                    "project_file":candidate["project_file"], "info":prepared}):
                raise ValueError("The saved item or its physical source changed before preparation")
            queued = finish_ingest(identity, prepared["path"], prepared, candidate["project_file"], prepared["ingest_token"])
            if queued.get("error"): raise ValueError(queued["error"])
            preparation["tasks"].append({"media_id":identity, "task":queued})
        except Exception as error:
            preparation["warnings"].append("Media "+identity+" saved; preparation could not be queued: "+str(error)[:300])
    result["preparation"] = preparation
    result["warnings"] = [*result["warnings"], *preparation["warnings"]]
    return result


async def _source_creation_command(body, mode):
    import source_creation_io
    project, expected = _workflow_capture(body); _source_command_policy(body)
    owner_root, owner_tasks = os.path.abspath(ROOT), TASKS
    project_file = P("projects", expected["project"], "project.json")
    try:
        candidate = await _owned_render_thread(source_creation_io.prepare, project, body, mode,
            identity=uuid.uuid4().hex, added=time.time(), scratch_parent=owner_root)
        planned = candidate["plan"]; summary = planned["summary"]
        envelope = {"kind":"source_creation", "mode":mode, "project":expected["project"], "changed":bool(planned["ops"]),
            "media_ids":planned["media_ids"], "media":planned["media"], "summary":summary, "warnings":summary.get("warnings",[])}
        _source_command_policy(body)
        if not planned["ops"]:
            with LOCK: require_project_context({"_context":expected}, active_id(), load_project())
            return {"ok":True, **envelope, "context":expected, "preparation":{"tasks":[],"warnings":[]}}
        apply_ops(project, planned["ops"])
        parse_project(json.dumps(project, allow_nan=False).encode("utf-8"))
    except (ValueError, KeyError, TypeError, OSError) as error: raise HTTPException(422, str(error)) from error
    candidate.update(project=project, expected=expected, owner_root=owner_root, owner_tasks=owner_tasks, project_file=project_file)
    commit = asyncio.create_task(_commit_source_creation(candidate, body, envelope))
    try: return await asyncio.shield(commit)
    except asyncio.CancelledError:
        while not commit.done():
            try: await asyncio.shield(commit)
            except asyncio.CancelledError: continue
            except Exception: break
        if not commit.cancelled(): commit.exception()
        raise


async def _commit_editorial_source(project, expected, envelope, mode, body, prepared, owner_root, owner_tasks, project_file):
    # Complete an accepted registration and its queue attempt even if the HTTP
    # reply is lost. This owned coroutine is joined by the request wrapper.
    action = "import" if mode == "extract_audio" else mode
    result = await _workflow_commit(project, expected, {**envelope, "message": envelope["summary"]["message"]}, action, body.get("actor", "human"))
    if mode == "extract_audio":
        try:
            if os.path.abspath(ROOT) != owner_root or TASKS is not owner_tasks:
                raise ValueError("Workspace changed; open the alias's workspace and explicitly prepare its preview")
            if not _task_media_current({"media_id":prepared["id"], "path":prepared["path"], "token":prepared["ingest_token"], "project_file":project_file, "info":prepared}):
                raise ValueError("The saved alias or its physical source changed before preparation")
            queued = finish_ingest(prepared["id"], prepared["path"], prepared, project_file, prepared["ingest_token"])
            result["preparation"] = {"warning": queued["error"]} if queued.get("error") else {"task": queued}
        except Exception as error:
            result["preparation"] = {"warning": "Audio alias saved; preparation could not be queued: " + str(error)[:300]}
        if result["preparation"].get("warning"): result["warnings"] = [*result["warnings"], result["preparation"]["warning"]]
    return result


async def _editorial_source_command(body, mode):
    import source_commands
    project, expected = _workflow_capture(body); _source_command_policy(body)
    owner_root, owner_tasks = os.path.abspath(ROOT), TASKS
    project_file = P("projects", expected["project"], "project.json")
    try:
        planned = await asyncio.to_thread(source_commands.plan, project, body, mode,
            identity=uuid.uuid4().hex, token=uuid.uuid4().hex, added=time.time())
        summary = planned["summary"]; summary.setdefault("warnings", [])
        envelope = {"changed": bool(planned["ops"]), "summary": summary, "warnings": summary["warnings"], "project": expected["project"]}
        if mode == "split_words": envelope.update(sequence=summary["sequence"], clip_id=summary["clip_id"], layers=planned["layers"])
        elif mode == "input_transform": envelope.update(media=planned["media"], **{key:summary[key] for key in ("requested_media_id","media_id","affected_media_ids","scope")})
        else: envelope.update(media=planned["media"], media_id=planned["media"]["id"], id=planned["media"]["id"], name=planned["media"]["name"], source_media_id=summary["source_media_id"])
        await asyncio.to_thread(source_commands.check_resources, planned["resources"])
        _source_command_policy(body)
        if not planned["ops"]:
            with LOCK: require_project_context({"_context": expected}, active_id(), load_project())
            return {"ok": True, **envelope, "context": expected}
        apply_ops(project, planned["ops"])
        parse_project(json.dumps(project, allow_nan=False).encode("utf-8"))
    except (ValueError, KeyError, TypeError, OSError) as error: raise HTTPException(422, str(error)) from error
    # Import receipts keep late derived metadata compatible with one Undo.
    commit = asyncio.create_task(_commit_editorial_source(project, expected, envelope, mode, body,
        planned.get("prepared"), owner_root, owner_tasks, project_file))
    try: return await asyncio.shield(commit)
    except asyncio.CancelledError:
        while not commit.done():
            try: await asyncio.shield(commit)
            except asyncio.CancelledError: continue
            except Exception: break
        if not commit.cancelled(): commit.exception()  # Observe any failure without abandoning saved work.
        raise


@app.post("/api/graphics/split_words")
async def graphics_split_words(req: Request):
    return await _editorial_source_command(await req.json(), "split_words")

@app.post("/api/media/input_transform")
async def media_input_transform(req: Request):
    return await _editorial_source_command(await req.json(), "input_transform")

@app.post("/api/media/extract_audio")
async def media_extract_audio(req: Request):
    return await _editorial_source_command(await req.json(), "extract_audio")

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
    from input_options import numbered_input_options
    body = await req.json()
    if not isinstance(body, dict) or not isinstance(body.get("folder"), str) or not body["folder"] or "\x00" in body["folder"]:
        raise HTTPException(422, "Choose a numbered source folder.")
    try:
        if isinstance(body.get("fps", 24), bool): raise ValueError("Frame rate must be numeric.")
        fps = float(body.get("fps", 24)); numbered_input_options(fps)
    except (TypeError, ValueError, OverflowError) as error:
        raise HTTPException(422, str(error)) from error
    folder = os.path.abspath(body["folder"])
    with LOCK:
        pid = active_id(); proj = load_project(); expected = project_context(ROOT, pid, proj)
    try:
        files = sorted(f for f in os.listdir(folder) if f.lower().endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".dpx")))
    except OSError as error:
        raise HTTPException(422, "Numbered source folder is unavailable.") from error
    if len(files) < 2: raise HTTPException(400, "need at least two numbered frames")
    if len(files) > 1000000: raise HTTPException(422, "Numbered sources support at most 1,000,000 frames.")
    m_ = _re.match(r"^(.*?)(\d+)(\.[^.]+)$", files[0])
    if not m_: raise HTTPException(400, "frames must be numbered (name0001.png)")
    prefix, num, ext = m_.groups(); pattern = os.path.join(folder, prefix).replace("%", "%%") + f"%0{len(num)}d{ext}"
    try:
        start = int(num); source_options = numbered_input_options(fps, start)
        numbered_input_options(fps, start + len(files) - 1)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if any(not os.path.isfile(pattern % (start + i)) for i in range(len(files))):
        raise HTTPException(422, "Numbered frames must be consecutive and share one name and extension.")
    first = os.path.join(folder, files[0]); info = await asyncio.to_thread(probe, first); mid = str(uuid.uuid4())[:8]
    m = {"id": mid, "name": f"{prefix or os.path.basename(folder)} [{len(files)} frames]", "path": pattern, "duration": len(files) / fps, "width": info["width"], "height": info["height"], "fps": fps, "is_image": False, "has_video": True, "has_audio": False, "sequence_frames": len(files), "input_opts": source_options, "thumb": None, "strip": None, "wave": None, "status": "ingesting", "added": time.time()}
    with LOCK:
        proj = load_project(); require_project_context({"_context": expected}, active_id(), proj)
        token = uuid.uuid4().hex[:12]; m["ingest_token"] = token; project_file = PP("project.json")
        proj["media"][mid] = m; save_project(proj, project_file)
    finish_ingest(mid, pattern, m, project_file, token)
    ev = log_event({"type": "media_added", "actor": body.get("actor", "human"), "media": [mid]}, project_id=pid); await broadcast(ev); return m

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
    return await _queue_audio_workflow(await req.json(), "peak")

def _audio_edit_policy(body):
    if body.get("actor", "human") == "human": return
    try:
        with open(P("settings.json"), encoding="utf-8") as stream: mode = json.load(stream).get("agent_mode") or "direct"
    except FileNotFoundError: mode = "direct"
    except (OSError, ValueError, TypeError, AttributeError) as error: raise HTTPException(403, "Could not read agent editing preferences") from error
    if mode == "proposals_only": raise HTTPException(403, "Direct agent audio edits are disabled. Ask the editor to review and apply this audio change.")

async def _queue_audio_workflow(body, mode):
    import audio_workflow as audio
    project, expected = _workflow_capture(body)
    if TASKS is None: raise HTTPException(503, "Background tasks are unavailable")
    try:
        import re
        if not isinstance(body.get("request_id"), str) or not re.fullmatch("[a-f0-9]{32}", body["request_id"]): raise ValueError("Supply a unique audio analysis request_id")
        payload = await asyncio.to_thread(audio.capture, project, body, mode, expected)
        with LOCK:
            require_project_context({"_context": expected}, active_id(), load_project())
            task = TASKS.submit("audio_analysis", {"peak": "Normalize audio peak", "loudness": "Normalize audio loudness", "beats": "Detect audio transients"}[mode], expected, payload, identity=body["request_id"])
    except (TaskError, ValueError, TypeError, KeyError, OSError) as error: raise HTTPException(400, str(error)) from error
    return {"ok": True, "task": task, "context": expected, "message": "Audio analysis queued. Review its measurements before applying changes."}

def _task_audio_workflow(payload, task):
    import audio_workflow, audio_measurement
    task.check(); audio_workflow.check_sources(payload)
    result = audio_measurement.analyze(payload, task)
    task.check(); audio_workflow.check_sources(payload)
    result.update(media_id=payload.get("media_id"), range=payload.get("range"))
    return result

def _review_audio_workflow(value, project, expected):
    import audio_workflow as audio
    if value["record"]["kind"] != "audio_analysis" or value["record"]["status"] != "ready" or value.get("result") is None:
        raise HTTPException(409, "This audio result is not ready for review")
    try: plan = audio.plan(project, value["payload"], value["result"], expected, value["record"]["id"])
    except (ValueError, TypeError, KeyError, OSError) as error: raise HTTPException(409, str(error)) from error
    return {"ok": True, "task": value["record"], "context": expected, "result": value["result"], "plan": plan}

@app.post("/api/tasks/{identity}/audio")
async def background_audio_review(identity: str, req: Request):
    value = _owned_task(identity); project, expected = _workflow_capture(await req.json())
    reviewed = await asyncio.to_thread(_review_audio_workflow, value, project, expected)
    _workflow_capture({"_context": expected})
    if _owned_task(identity)["record"]["status"] != "ready": raise HTTPException(409, "This audio task is no longer ready")
    return reviewed

@app.post("/api/audio/gain")
async def audio_gain(req: Request):
    import audio_workflow as audio
    body = await req.json(); project, expected = _workflow_capture(body); _audio_edit_policy(body)
    try:
        plan = await asyncio.to_thread(audio.manual, project, body)
        _workflow_capture({"_context": expected})
        if not plan["ops"]: return {"ok": True, "context": expected, **plan["summary"]}
        apply_ops(project, plan["ops"]); parse_project(json.dumps(project, allow_nan=False).encode("utf-8"))
    except (ValueError, TypeError, KeyError, OSError) as error: raise HTTPException(422, str(error)) from error
    return await _workflow_commit(project, expected, plan["summary"], "audio_gain", body.get("actor", "human"))

async def _apply_audio_workflow(identity, value, body, project, expected):
    payload = value["payload"]
    if payload["scope"] != "timeline": raise HTTPException(409, "Raw audio measurements are read-only")
    sequence = next((s for s in project.get("sequences", []) if s.get("id") == payload["sequence"]), None)
    if (sequence or {}).get("workflow", {}).get("audio_tasks", {}).get(identity):
        TASKS.finish_apply(identity, success=True, message="Audio edit already applied")
        return {"ok": True, "context": expected, "message": "Audio edit already applied; no changes repeated"}
    _audio_edit_policy(body)
    reviewed = await asyncio.to_thread(_review_audio_workflow, value, project, expected); plan = reviewed["plan"]
    if not isinstance(body.get("fingerprint"), str) or body["fingerprint"] != plan["fingerprint"]:
        raise HTTPException(409, "The audio review changed; review the measurements again before applying")
    _workflow_capture({"_context": expected})
    if not plan["ops"]: return {"ok": True, "context": expected, "changed": False, "message": "No audio changes are needed"}
    try: TASKS.begin_apply(identity)
    except (TaskError, OSError) as error: raise HTTPException(409, str(error)) from error
    try:
        apply_ops(project, plan["ops"])
        sequence = next(s for s in project["sequences"] if s["id"] == payload["sequence"])
        sequence.setdefault("workflow", {}).setdefault("audio_tasks", {})[identity] = {"fingerprint": plan["fingerprint"], "mode": payload["mode"], "clips": payload["clip_ids"]}
        parse_project(json.dumps(project, allow_nan=False).encode("utf-8"))
        response = await _workflow_commit(project, expected, plan["summary"], "audio_analysis", body.get("actor", "human"))
    except BaseException:
        TASKS.finish_apply(identity, success=False, message="Audio apply was not confirmed. Check the project before retrying.")
        raise
    task = TASKS.finish_apply(identity, success=True, message="Audio edit applied")
    if task.get("warning"): response["warning"] = (response.get("warning", "") + "; " + task["warning"]).strip("; ")
    return response

@app.post("/api/render_replace")
async def render_replace(req: Request):
    """Capture an acknowledged clip for a cancellable, reviewable lossless bake."""
    import render_replace as bake
    body = await req.json(); project, expected = _workflow_capture(body)
    if TASKS is None: raise HTTPException(503, "Background tasks are unavailable")
    try:
        bake._identity(body.get("request_id"))
        payload = await asyncio.to_thread(bake.capture, project, body, expected, ROOT)
        with LOCK:
            require_project_context({"_context": expected}, active_id(), load_project())
            task = TASKS.submit("render_replace", "Render and Replace — " + payload["clip_id"], expected, payload, identity=body.get("request_id"))
    except (ValueError, KeyError, TypeError, OSError) as error: raise HTTPException(400, str(error)) from error
    return {"ok": True, "task": task, "context": expected, "message": "Lossless bake queued. Review the result in Tasks before replacing the clip."}

def _task_render_replace(payload, task):
    import render_replace as bake
    return bake.bake(payload, task)

def _review_render_replace(value, project, expected):
    import render_replace as bake
    if value["record"]["kind"] != "render_replace" or value["record"]["status"] != "ready" or value.get("result") is None:
        raise HTTPException(409, "This bake is not ready for review")
    try:
        bake.validate_current(project, value["payload"], expected, ROOT)
        plan = bake.plan(project, value["payload"], value["result"], value["record"]["id"])
    except (ValueError, TypeError, KeyError, OSError) as error: raise HTTPException(409, str(error)) from error
    return {"ok": True, "task": value["record"], "context": expected, "result": value["result"], "plan": plan}

@app.post("/api/tasks/{identity}/render-replace")
async def background_render_replace_review(identity: str, req: Request):
    value = _owned_task(identity); project, expected = _workflow_capture(await req.json())
    reviewed = await asyncio.to_thread(_review_render_replace, value, project, expected)
    # Slow hashes/probes cannot return a review for a different active revision.
    _workflow_capture({"_context": expected})
    return reviewed

@app.get("/api/tasks/{identity}/render-replace/preview")
async def background_render_replace_preview(identity: str):
    import render_replace as bake
    value = _owned_task(identity)
    if value["record"]["kind"] != "render_replace" or value["record"]["status"] not in ("ready", "applied") or value.get("result") is None:
        raise HTTPException(409, "This bake preview is not available")
    try: path = await asyncio.to_thread(bake.preview_path, value["payload"], value["result"], identity)
    except (ValueError, TypeError, KeyError, OSError) as error: raise HTTPException(409, str(error)) from error
    _owned_task(identity)
    return FileResponse(path, media_type="audio/wav" if value["payload"]["kind"] == "audio" else "image/png")

async def _apply_render_replace(identity, value, body, project, expected):
    import render_replace as bake
    payload = value["payload"]
    sequence = next((s for s in project.get("sequences", []) if s.get("id") == payload["sequence"]), None)
    receipt = (sequence or {}).get("workflow", {}).get("render_replace", {}).get(identity)
    if receipt:
        TASKS.finish_apply(identity, success=True, message="Replacement already applied")
        return {"ok": True, "context": expected, "message": "Replacement already applied; no edit repeated"}
    if body.get("actor", "human") != "human":
        try:
            with open(P("settings.json"), encoding="utf-8") as stream: mode = json.load(stream).get("agent_mode") or "direct"
        except FileNotFoundError: mode = "direct"
        except (OSError, ValueError, TypeError, AttributeError) as error: raise HTTPException(403, "Could not read agent editing preferences") from error
        if mode == "proposals_only": raise HTTPException(403, "Direct agent edits are disabled. Ask the editor to review and apply this owned bake in Tasks.")
    reviewed = await asyncio.to_thread(_review_render_replace, value, project, expected); plan = reviewed["plan"]
    if not isinstance(body.get("fingerprint"), str) or body["fingerprint"] != plan["fingerprint"]:
        raise HTTPException(409, "The replacement plan changed. Review it again before applying")
    _workflow_capture({"_context": expected})
    try: TASKS.begin_apply(identity)
    except (TaskError, OSError) as error: raise HTTPException(409, str(error)) from error
    try:
        await _owned_render_thread(bake.publish, payload, value["result"], identity)
        await asyncio.to_thread(bake.validate_current, project, payload, expected, ROOT)
        apply_ops(project, plan["ops"])
        sequence = next(s for s in project["sequences"] if s["id"] == payload["sequence"])
        sequence.setdefault("workflow", {}).setdefault("render_replace", {})[identity] = {
            "clip_id": payload["clip_id"], "media_id": plan["summary"]["media_id"], "sha256": value["result"]["sha256"]}
        result = await _workflow_commit(project, expected, plan["summary"], "render_replace", body.get("actor", "human"))
    except BaseException:
        TASKS.finish_apply(identity, success=False, message="Replacement apply was not confirmed. Check the project before retrying.")
        raise
    task = TASKS.finish_apply(identity, success=True, message="Replacement applied")
    warnings = [result.get("warning"), task.get("warning")]
    media = project["media"][plan["summary"]["media_id"]]
    try:
        prepared = finish_ingest(media["id"], media["path"], media, P("projects", expected["project"], "project.json"), media["ingest_token"])
        if prepared.get("error"): warnings.append("Replacement saved; playback preparation unavailable: " + str(prepared["error"]))
    except Exception as error: warnings.append("Replacement saved; playback preparation unavailable: " + str(error)[:300])
    result["warning"] = "; ".join(w for w in warnings if w)
    return result

@app.get("/api/export/edl")
def export_edl(sequence: str = "seq1", track: str = "V1"):
    try: edl = to_edl(load_project(), sequence, track)
    except (ValueError, StopIteration) as error: raise HTTPException(400, str(error) or "Sequence not found") from error
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(edl, headers={"Content-Disposition": f"attachment; filename={sequence}_{track}.edl"})

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
    import audio_sync as sync
    body = await req.json(); project, expected = _workflow_capture(body)
    if TASKS is None: raise HTTPException(503, "Background tasks are unavailable")
    try:
        if not isinstance(body.get("request_id"), str) or len(body["request_id"]) != 32: raise ValueError("Supply a unique synchronization request_id")
        payload = await asyncio.to_thread(sync.capture, project, body, expected)
        with LOCK:
            require_project_context({"_context": expected}, active_id(), load_project())
            task = TASKS.submit("sync", "Synchronize audio", expected, payload, identity=body["request_id"])
    except (TaskError, ValueError, TypeError, KeyError, OSError) as error: raise HTTPException(400, str(error)) from error
    return {"ok": True, "task": task, "context": expected, "message": "Audio synchronization queued. Review quality and offsets in Tasks before applying."}

def _task_audio_sync(payload, task):
    import audio_sync as sync
    return sync.analyze(payload, task)

def _review_audio_sync(value, project, expected):
    import audio_sync as sync
    if value["record"]["kind"] != "sync" or value["record"]["status"] != "ready" or value.get("result") is None:
        raise HTTPException(409, "This synchronization result is not ready for review")
    try: plan = sync.plan(project, value["payload"], value["result"], expected, value["record"]["id"])
    except (ValueError, TypeError, KeyError, OSError) as error: raise HTTPException(409, str(error)) from error
    return {"ok": True, "task": value["record"], "context": expected, "result": value["result"], "plan": plan}

@app.post("/api/tasks/{identity}/sync")
async def background_sync_review(identity: str, req: Request):
    value = _owned_task(identity); project, expected = _workflow_capture(await req.json())
    reviewed = await asyncio.to_thread(_review_audio_sync, value, project, expected)
    _workflow_capture({"_context": expected})
    if _owned_task(identity)["record"]["status"] != "ready": raise HTTPException(409, "The synchronization task is no longer ready")
    return reviewed

async def _apply_audio_sync(identity, value, body, project, expected):
    payload = value["payload"]
    if payload["mode"] != "timeline": raise HTTPException(409, "Raw source synchronization is read-only; review offsets before creating a multicam or merged clip")
    sequence = next((s for s in project.get("sequences", []) if s.get("id") == payload["sequence"]), None)
    if (sequence or {}).get("workflow", {}).get("sync_tasks", {}).get(identity):
        TASKS.finish_apply(identity, success=True, message="Synchronization already applied")
        return {"ok": True, "context": expected, "message": "Synchronization already applied; no moves repeated"}
    if body.get("actor", "human") != "human":
        try:
            with open(P("settings.json"), encoding="utf-8") as stream: mode = json.load(stream).get("agent_mode") or "direct"
        except FileNotFoundError: mode = "direct"
        except (OSError, ValueError, TypeError, AttributeError) as error: raise HTTPException(403, "Could not read agent editing preferences") from error
        if mode == "proposals_only": raise HTTPException(403, "Direct agent edits are disabled. Ask the editor to review and apply synchronization in Tasks.")
    reviewed = await asyncio.to_thread(_review_audio_sync, value, project, expected); plan = reviewed["plan"]
    if not isinstance(body.get("fingerprint"), str) or body["fingerprint"] != plan["fingerprint"]:
        raise HTTPException(409, "The synchronization review changed; review the offsets again before applying")
    _workflow_capture({"_context": expected})
    if not plan["ops"]: return {"ok": True, "context": expected, "changed": False, "message": "The selected clips are already aligned; no project changes were made"}
    try: TASKS.begin_apply(identity)
    except (TaskError, OSError) as error: raise HTTPException(409, str(error)) from error
    try:
        apply_ops(project, plan["ops"])
        sequence = next(s for s in project["sequences"] if s["id"] == payload["sequence"])
        sequence.setdefault("workflow", {}).setdefault("sync_tasks", {})[identity] = {"fingerprint": plan["fingerprint"], "clips": [item["id"] for item in payload["items"]]}
        response = await _workflow_commit(project, expected, plan["summary"], "synchronize", body.get("actor", "human"))
    except BaseException:
        TASKS.finish_apply(identity, success=False, message="Synchronization apply was not confirmed. Check the project before retrying.")
        raise
    task = TASKS.finish_apply(identity, success=True, message="Synchronization applied")
    if task.get("warning"): response["warning"] = (response.get("warning", "") + "; " + task["warning"]).strip("; ")
    return response

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
    return _queue_media_analysis(await req.json(), "silences")

@app.post("/api/audio/beats")
async def audio_beats(req: Request):
    return await _queue_audio_workflow(await req.json(), "beats")

@app.post("/api/audio/remix")
async def audio_remix(req: Request):
    """Queue a bounded remix duration plan. Review in Tasks before applying it."""
    return _queue_media_analysis(await req.json(), "remix")

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
    return await _source_creation_command(await req.json(), "subclip")

@app.post("/api/audio/measure")
async def audio_measure(req: Request):
    return await _queue_audio_workflow(await req.json(), "loudness")

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
    proj, context = preview_state(body); outs = body.get("outputs")
    report = await asyncio.to_thread(check_export_resources, proj, seq_id, preset)
    preview_state({"_context": context})
    if not outs: return start_render(proj, seq_id, preset, name, body.get("actor", "human"), report, context=context)
    jobs = []
    for o in outs:
        pr = dict(preset); pr.update({"out_w": o.get("width"), "out_h": o.get("height"), "fit": o.get("fit", "crop")})
        jobs.append(start_render(proj, seq_id, pr, f"{name}_{o.get('suffix') or (str(o.get('width')) + 'x' + str(o.get('height')))}", body.get("actor", "human"), report, context=context))
    return {"jobs": jobs}

@app.post("/api/render/preflight")
async def render_preflight(req: Request):
    body = await req.json(); proj, context = preview_state(body); preset = body.get("preset") or {}
    ids = [s["id"] for s in proj["sequences"] if not s.get("multicam") and not s.get("merged")] if body.get("all_sequences") else [body.get("sequence", "seq1")]
    reports = [await asyncio.to_thread(inspect_export_resources, proj, sid, preset) for sid in ids]
    preview_state({"_context": context})
    return {"ok": all(r["ok"] for r in reports), "context": context, "reports": reports,
            "errors": sum(r["errors"] for r in reports), "warnings": sum(r["warnings"] for r in reports),
            "issues": [i for r in reports for i in r["issues"]]}

@app.get("/api/processing")
def processing_status():
    from work_budget import BUDGET
    return BUDGET.snapshot()

@app.get("/api/jobs")
def jobs_list():
    from work_budget import status
    with RENDER_STATE_LOCK:
        result = copy.deepcopy(sorted(JOBS.values(), key=lambda j: j["started"], reverse=True)[:50])
        for job in result:
            state = status(RENDER_PROCS.get(job['id']))
            if state: job['resource'] = state
        return result

@app.get("/api/encoders")
def encoders():
    from encoder_capabilities import CAPABILITIES
    try: return CAPABILITIES.catalog()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise HTTPException(503, "Encoder discovery failed: " + str(error))


@app.get("/api/settings")
def settings_get(): p = P("settings.json"); return json.load(open(p)) if os.path.exists(p) else {}

@app.put("/api/settings")
async def settings_put(req: Request):
    """Merge (shallow) into the workstation settings so independent writers (keymap, prefs, layouts, presets) don't clobber each other."""
    body = await req.json(); cur = {}
    if os.path.exists(P("settings.json")):
        try: cur = json.load(open(P("settings.json")))
        except Exception: cur = {}
    if isinstance(body, dict) and isinstance(body.get("prefs"), dict) and "proxy" in body["prefs"]:
        from proxy_media import settings as proxy_settings
        try: body["prefs"]["proxy"] = proxy_settings(body["prefs"]["proxy"])
        except ValueError as error: raise HTTPException(422, str(error)) from error
    cur.update(body or {}); json.dump(cur, open(P("settings.json"), "w"), indent=1); return cur

@app.post("/api/import/fcpxml")
async def import_fcpxml(req: Request):
    body = await req.json(); proj, expected = _workflow_capture(body, required=False)
    project_file = P("projects", expected["project"], "project.json"); added = []
    def read_xml(proc_holder=None):
        def imp(path, name):
            if not path or not os.path.exists(path): raise ValueError("XML source is unavailable: " + str(path))
            for mid, media in proj["media"].items():
                if os.path.abspath(media["path"]) == os.path.abspath(path): return mid
            mid, media = ingest(path, name); media["workflow_import"] = True
            proj["media"][mid] = media; added.append(media); return mid
        return from_fcp7_xml(body["xml"], imp)
    try: seqs = await _owned_render_thread(read_xml)
    except (ValueError, TypeError, KeyError, OSError) as error: raise HTTPException(400, str(error)) from error
    proj["sequences"].extend(seqs)
    result = await _workflow_commit(proj, expected,
        {"sequence": seqs[0]["id"], "sequences": [{"id": s["id"], "name": s["name"], "clips": sum(len(t["clips"]) for t in s["tracks"])} for s in seqs],
         "message": f"Imported {len(seqs)} XML sequence(s)"}, "xml_import", body.get("actor", "human"))
    for media in added: finish_ingest(media["id"], media["path"], media, project_file, media["ingest_token"])
    return result

@app.get("/api/render/{jid}")
def render_status(jid: str):
    with RENDER_STATE_LOCK:
        if jid not in JOBS: raise HTTPException(404, "Render job not found")
        from work_budget import status
        result = copy.deepcopy(JOBS[jid]); state = status(RENDER_PROCS.get(jid))
        if state: result['resource'] = state
        return result

@app.get("/api/render_command")
def render_command(sequence: str = "seq1"):
    global COMMAND_CONTEXT_BUILDING
    # Reserve before building: nested command inspection itself can encode media.
    with COMMAND_CONTEXT_LOCK:
        if len(COMMAND_CONTEXTS) + COMMAND_CONTEXT_BUILDING >= COMMAND_CONTEXT_LIMIT:
            raise HTTPException(429, "Command inspection scopes are full. Release an existing scope after its consumers exit.")
        COMMAND_CONTEXT_BUILDING += 1
    try:
        cmd, graph = build_command(load_project(), sequence, P("renders", "preview.mp4"))
        scope = uuid.uuid4().hex
        with COMMAND_CONTEXT_LOCK: COMMAND_CONTEXTS[scope] = cmd
        return {"cmd": cmd, "graph": graph, "cwd": cmd.cwd, "scope": {"id": scope,
                "release": f"/api/render_command/{scope}", "limit": COMMAND_CONTEXT_LIMIT,
                "lifetime": "Retained until DELETE release; release only after command consumers exit."}}
    finally:
        with COMMAND_CONTEXT_LOCK: COMMAND_CONTEXT_BUILDING -= 1


COMMAND_CONTEXTS = {}
COMMAND_CONTEXT_LOCK = threading.Lock()
COMMAND_CONTEXT_LIMIT = 8
COMMAND_CONTEXT_BUILDING = 0


@app.delete("/api/render_command/{scope}")
def release_render_command(scope: str):
    with COMMAND_CONTEXT_LOCK:
        command = COMMAND_CONTEXTS.get(scope)
        if command is None: return {"ok": False}
        # On cleanup failure keep the scope available for an explicit retry.
        try: command.close()
        except Exception as error: raise HTTPException(500, str(error)) from error
        del COMMAND_CONTEXTS[scope]
    return {"ok": True}

# ---------- proposals (agent proposes; human accepts/rejects each; every decision is a labeled training event) ----------
@app.get("/api/proposals")
def proposals_list(): return load_project().get("proposals", [])

@app.post("/api/proposals")
async def proposals_add(req: Request):
    body = await req.json()  # {items:[{ops:[...], reason, tool}], actor:"agent", title}
    if not isinstance(body, dict): raise HTTPException(422, "Proposal must be an object")
    if any(not isinstance(body.get(key, ""), str) for key in ("actor", "title")): raise HTTPException(422, "Proposal title and actor must be text")
    with LOCK:
        project_id = active_id(); proj0 = load_project(); require_project_context(body, project_id, proj0)
        if not isinstance(body.get("items"), list) or not body["items"]: raise HTTPException(422, "A proposal needs at least one item")
        for it in body.get("items", []):
            if not isinstance(it, dict) or not isinstance(it.get("ops"), list) or not it["ops"]: raise HTTPException(422, "Each proposal item needs operations")
            if any(isinstance(o, dict) and o.get("op") == "set_mix" for o in it["ops"]) and "_context" not in body:
                raise HTTPException(400, "Mixer proposals require the saved project context")
            try:
                trial = copy.deepcopy(proj0)
                if any((o.get("path") or "").strip("/").split("/")[0] == "proposals" for o in it["ops"]): raise ValueError("Proposal operations cannot change proposal records")
                problems = validate_ops(trial, it["ops"])
                if not problems:
                    apply_ops(trial, copy.deepcopy(it["ops"]))
                    if trial.get("proposals") != proj0.get("proposals"): raise ValueError("Proposal operations cannot change proposal records")
                    if not all(o.get("op") == "set_mix" for o in it["ops"]): normalize_tracks(trial)
                    parse_project(json.dumps(trial, allow_nan=False).encode("utf-8"))
            except (RecoveryError, ValueError, TypeError, KeyError, IndexError, StopIteration, AttributeError) as error:
                raise HTTPException(422, "Invalid proposal operations: " + str(error)[:400]) from error
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
        proj = proj0; pr = {"id": str(uuid.uuid4())[:8], "ts": time.time(), "actor": body.get("actor", "agent"), "title": body.get("title", "Agent proposal"),
              "items": [{"id": str(uuid.uuid4())[:8], "ops": copy.deepcopy(it["ops"]), "reason": it.get("reason", ""), "tool": it.get("tool", "agent"), "status": "pending",
                         **{key: it[key] for key in ("advisor_p", "advisor_n") if key in it}} for it in body["items"]]}
        proj.setdefault("proposals", []).append(pr); save_project(proj, P("projects", project_id, "project.json"))
        context = project_context(ROOT, project_id, proj); warning = ""
        ev = {"type": "proposal", "actor": pr["actor"], "project": project_id, "proposal": pr["id"], "n_items": len(pr["items"]), "title": pr["title"], "context": context}
        try: log_event(ev, project_id=project_id)
        except OSError as error: warning = "Proposal saved; event history could not be recorded: " + str(error)[:200]
    await broadcast(ev)
    return {**pr, "context": context, "warning": warning}


PROPOSAL_PREVIEWS = PreviewStore()
FRAME_SLOTS = threading.BoundedSemaphore(2)
PROPOSAL_FRAME_SLOTS = FRAME_SLOTS


def proposal_selection(project, pid, ids):
    if not isinstance(ids, list) or not ids or any(not isinstance(iid, str) or not iid for iid in ids):
        raise HTTPException(422, "Select one or more proposal items")
    if len(set(ids)) != len(ids): raise HTTPException(422, "Proposal items must be unique")
    proposal = next((p for p in project.get("proposals", []) if p["id"] == pid), None)
    if not proposal: raise HTTPException(404, "Proposal not found")
    by_id = {item["id"]: item for item in proposal["items"]}
    if any(iid not in by_id for iid in ids): raise HTTPException(404, "Proposal item not found")
    if any(by_id[iid].get("status") != "pending" for iid in ids):
        raise HTTPException(409, {"code": "proposal_decided", "message": "An item was already decided. Refresh proposals before continuing."})
    return proposal, by_id


def prepare_proposal(project, pid, ids):
    """One normalization pass for both direct decisions and reviewed snapshots."""
    candidate = copy.deepcopy(project); proposal, items = proposal_selection(candidate, pid, ids)
    ops = []; befores = {}
    for iid in ids:
        try:
            captured = copy.deepcopy(items[iid]["ops"])
            if any((o.get("path") or "").strip("/").split("/")[0] == "proposals" for o in captured): raise ValueError("Proposal operations cannot change proposal records")
            problems = validate_ops(candidate, captured)
            if problems: raise ValueError("; ".join(problems))
            befores[iid] = apply_ops(candidate, captured)
            if candidate.get("proposals") != project.get("proposals"): raise ValueError("Proposal operations cannot change proposal records")
            ops.extend(captured)
        except (ValueError, TypeError, KeyError, IndexError, StopIteration, AttributeError) as error:
            raise HTTPException(422, "The proposal no longer applies: " + str(error)[:400]) from error
    try:
        if candidate.get("version", 1) < SCHEMA: migrate_project(candidate)
        warnings = [] if ops and all(o.get("op") == "set_mix" for o in ops) else normalize_tracks(candidate)
        parse_project(json.dumps(candidate, allow_nan=False).encode("utf-8"))
    except (RecoveryError, ValueError, TypeError, KeyError, IndexError) as error:
        raise HTTPException(422, "The proposal would leave an invalid project: " + str(error)[:400]) from error
    return candidate, ops, befores, warnings


def proposal_preview_error(error):
    return HTTPException(409, {"code": "proposal_preview_changed", "message": str(error)})


def current_proposal_preview(view_id, holder=None):
    # Caller holds LOCK, including when capturing project identity.
    context = project_context(ROOT, active_id(), load_project())
    try:
        return PROPOSAL_PREVIEWS.get(view_id, context) if holder is None else PROPOSAL_PREVIEWS.attach(view_id, context, holder)
    except PreviewUnavailable as error: raise proposal_preview_error(error) from error


@app.post("/api/proposals/{pid}/preview")
async def proposal_preview(pid: str, req: Request):
    body = await req.json()
    if not isinstance(body, dict): raise HTTPException(422, "Preview must be an object")
    with LOCK:
        project_id = active_id(); project = load_project(); require_project_context(body, project_id, project)
        ids = body.get("items")
        candidate, ops, befores, warnings = prepare_proposal(project, pid, ids)
        try:
            sequences = [{"id": seq["id"], "name": seq.get("name", seq["id"]), "fps": seq["fps"], "duration": seq_total(seq)} for seq in candidate["sequences"]]
            if any(isinstance(seq["duration"], bool) or not isinstance(seq["duration"], (int, float)) or not math.isfinite(seq["duration"]) or seq["duration"] <= 0 for seq in sequences):
                raise ValueError("Sequence duration must be a finite positive number")
        except (ValueError, TypeError, KeyError) as error: raise HTTPException(422, "Cannot prepare frame bounds: " + str(error)[:400]) from error
        try:
            view = PROPOSAL_PREVIEWS.create(pid, ids, candidate, project_context(ROOT, project_id, project), warnings,
                                          details={"ops": ops, "befores": befores})
        except PreviewUnavailable as error: raise HTTPException(429, str(error)) from error
        # Review gets the actual normalized difference and sequence bounds, without
        # installing the candidate as the browser's editable project.
        view.pop("project")
        view["changes"] = changes_between(project, candidate)
        view["sequences"] = sequences
        return {"ok": True, **view}


@app.delete("/api/proposals/preview/{view_id}")
def release_proposal_preview(view_id: str):
    return {"ok": PROPOSAL_PREVIEWS.release(view_id)}


@app.get("/api/proposals/preview/{view_id}/frame")
def proposal_preview_frame(view_id: str, sequence: str, t: float = 0.0):
    from work_budget import WorkBusy
    if not math.isfinite(t) or t < 0: raise HTTPException(422, "Frame time must be finite and nonnegative")
    if not PROPOSAL_FRAME_SLOTS.acquire(blocking=False): raise HTTPException(429, "Two proposal frames are rendering. Try this frame again shortly.")
    holder = {"resource_wait_timeout": 5}
    try:
        with LOCK:
            view = current_proposal_preview(view_id, holder)
            seq = next((s for s in view["project"]["sequences"] if s["id"] == sequence), None)
            if seq is None: raise HTTPException(404, "Preview sequence not found")
            if t >= seq_total(seq): raise HTTPException(422, "Choose a frame before the end of this sequence")
            candidate = copy.deepcopy(view["project"])
        with RenderContext(proc_holder=holder, stall_timeout=120) as context:
            out = context.new_file(".png")
            render_frame(candidate, sequence, t, out, proc_holder=holder, context=context)
            with open(out, "rb") as stream: content = stream.read()
            with LOCK: current_proposal_preview(view_id)
            return Response(content, media_type="image/png", headers={"Cache-Control": "no-store"})
    except WorkBusy as error:
        raise HTTPException(429, str(error)) from error
    except (RuntimeError, OSError) as error:
        if holder.get("cancelled"): raise proposal_preview_error("The preview was stopped or expired. Preview again.") from error
        raise HTTPException(422, "Frame render failed: " + str(error)[:400]) from error
    finally:
        PROPOSAL_PREVIEWS.detach(view_id, holder)
        PROPOSAL_FRAME_SLOTS.release()


def decide_proposal_items(pid, decisions, decision, body):
    if decision not in ("accept", "reject"):
        raise HTTPException(422, "A proposal decision must be accept or reject")
    if not isinstance(decisions, list) or not decisions or any(not isinstance(item, dict) or not isinstance(item.get("id"), str) for item in decisions):
        raise HTTPException(422, "Select one or more proposal items")
    ids = [item["id"] for item in decisions]
    for item in decisions:
        if not isinstance(item.get("note", ""), str) or not isinstance(item.get("reasons", []), list) or any(not isinstance(reason, str) for reason in item.get("reasons", [])):
            raise HTTPException(422, "Decision notes and reasons must be text")
    with LOCK:
        project_id = active_id(); before = load_project(); require_project_context(body, project_id, before)
        proposal_selection(before, pid, ids)
        binding = body.get("_preview")
        if decision == "accept" and binding is not None:
            if not isinstance(binding, dict) or not isinstance(binding.get("id"), str): raise HTTPException(422, "Invalid preview reference")
            view = current_proposal_preview(binding["id"])
            if view["proposal"] != pid or view["items"] != ids or binding.get("plan") != view["plan"]:
                raise proposal_preview_error("Select exactly the items in this preview, or prepare a new preview.")
            proj = copy.deepcopy(view["project"])
            ops = copy.deepcopy(view["_details"]["ops"]); befores_by_id = copy.deepcopy(view["_details"]["befores"])
            warnings = list(view["warnings"])
        elif decision == "accept":
            proj, ops, befores_by_id, warnings = prepare_proposal(before, pid, ids)
        else:
            proj = copy.deepcopy(before); ops = []; befores_by_id = {}; warnings = []
        pr, by_id = proposal_selection(proj, pid, ids); records = []
        for item in decisions:
            it = by_id[item["id"]]
            it.update(status=decision, decided_ts=time.time(), note=item.get("note", ""), reasons=item.get("reasons", []))
            records.append({"type": "proposal_decision", "actor": "human", "project": project_id, "proposal": pid, "item": it["id"],
                            "decision": decision, "reason_agent": it.get("reason", ""), "reasons": it["reasons"], "note": it["note"],
                            "ops": it["ops"], "befores": befores_by_id.get(it["id"], []), "advisor_p": it.get("advisor_p"), "advisor_n": it.get("advisor_n"), "client": body.get("client")})
        warning = commit_edit(before, proj, project_id, {"ops": ops, "actor": "human", "reason": decision + " proposal: " + str(pr.get("title", "Agent proposal")), "ts": time.time()})
        if decision == "accept" and binding is not None: PROPOSAL_PREVIEWS.release(binding["id"])
        context = project_context(ROOT, project_id, proj); notices = [warning] if warning else []
        training = P("projects", project_id, "training")
        for event in records:
            try: log_event(event, project_id=project_id)
            except OSError as error: notices.append("Event history could not be recorded: " + str(error)[:200])
            try:
                os.makedirs(training, exist_ok=True)
                with open(os.path.join(training, "proposal_decisions.jsonl"), "a", encoding="utf-8") as stream:
                    stream.write(json.dumps({k: v for k, v in event.items() if k != "befores"}) + "\n")
            except OSError as error: notices.append("Training history could not be recorded: " + str(error)[:200])
        result = {"ok": True, "items": [by_id[iid] for iid in ids], "context": context, "warnings": warnings, "warning": "; ".join(notices)}
        event = {"type": "proposal_decision", "project": project_id, "proposal": pid, "items": ids, "decision": decision,
                 "actor": "human", "client": body.get("client"), "context": context}
    return result, event


@app.post("/api/proposals/{pid}/decide")
async def proposal_batch_decide(pid: str, req: Request):
    body = await req.json()
    if not isinstance(body, dict): raise HTTPException(422, "Decision must be an object")
    result, event = decide_proposal_items(pid, body.get("items"), body.get("decision"), body)
    await broadcast(event)
    return result


@app.post("/api/proposals/{pid}/{iid}/{decision}")
async def proposal_decide(pid: str, iid: str, decision: str, req: Request):
    body = await req.json() if req.headers.get("content-length", "0") not in ("0", "") else {}
    result, event = decide_proposal_items(pid, [{"id": iid, "note": body.get("note", ""), "reasons": body.get("reasons", [])}], decision, body)
    await broadcast(event)
    return {**result["items"][0], **{key: result[key] for key in ("ok", "context", "warnings", "warning")}}

# ---------- captions / interchange / frames ----------
@app.post("/api/captions/import")
async def captions_import(req: Request):
    body = await req.json(); caps = srt_to_captions(body["srt"]); seq_id = body.get("sequence", "seq1")
    with LOCK:
        proj = load_project(); seq = next(s for s in proj["sequences"] if s["id"] == seq_id); seq["captions"] = (seq.get("captions") or []) + caps if body.get("append") else caps; save_project(proj)
    ev = log_event({"type": "ops", "actor": body.get("actor", "human"), "tool": "captions_import", "reason": f"{len(caps)} captions", "ops": []}); await broadcast(ev); return {"count": len(caps)}

@app.get("/api/captions/export")
def captions_export(sequence: str = "seq1", context: Optional[str] = None):
    with LOCK:
        proj = load_project()
        if context is not None:
            try: expected = json.loads(context)
            except ValueError: raise HTTPException(400, "Invalid project context") from None
            require_project_context({"_context": expected}, active_id(), proj)
        seq = next((s for s in proj["sequences"] if s["id"] == sequence), None)
        if seq is None: raise HTTPException(404, "Sequence not found")
    from fastapi.responses import PlainTextResponse; return PlainTextResponse(captions_to_srt(seq.get("captions") or []), media_type="text/plain")

@app.get("/api/export/fcpxml")
def export_fcpxml(sequence: str = "seq1"):
    try: xml = to_fcp7_xml(load_project(), sequence)
    except (ValueError, StopIteration) as error: raise HTTPException(400, str(error) or "Sequence not found") from error
    return Response(xml, media_type="application/xml", headers={"Content-Disposition": f"attachment; filename={sequence}.xml"})

@app.get("/api/export/otio")
def export_otio(sequence: str = "seq1"):
    return JSONResponse(to_otio(load_project(), sequence), headers={"Content-Disposition": f"attachment; filename={sequence}.otio"})

@app.get("/api/frame")
def frame(sequence: str = "seq1", t: float = 0.0, context: Optional[str] = None, media: Optional[str] = None):
    from work_budget import WorkBusy
    from timeline_time import display_frame, from_frames
    from render import chunk_key
    if not math.isfinite(t) or t < 0: raise HTTPException(422, "Frame time must be finite and nonnegative")
    if not FRAME_SLOTS.acquire(blocking=False): raise HTTPException(429, "Two frames are rendering. Try this frame again shortly.")
    try:
        with LOCK:
            project = load_project(); pid = active_id()
            if context is not None:
                try: expected = json.loads(context)
                except (ValueError, TypeError): raise HTTPException(400, "Invalid project context") from None
                require_project_context({"_context": expected}, pid, project)
            captured = project_context(ROOT, pid, project)
            if media is not None:
                from frame_source import source_project
                project = source_project(project, media); sequence = 'source'
            seq = next((s for s in project["sequences"] if s["id"] == sequence), None)
            if seq is None: raise HTTPException(404, "Sequence not found")
            if t >= seq_total(seq): raise HTTPException(422, "Choose a frame before the end of this sequence")
            index = display_frame(t, seq["fps"])
            project = copy.deepcopy(project)
            seq = next(s for s in project["sequences"] if s["id"] == sequence)
        signature = chunk_key(project, seq, {"color_processing": "rgb"})
        with RenderContext(proc_holder={"resource_wait_timeout": 5}, stall_timeout=120) as owned:
            out = owned.new_file(".png")
            render_frame(project, sequence, t, out, context=owned)
            with open(out, "rb") as stream: content = stream.read()
            if chunk_key(project, seq, {"color_processing": "rgb"}) != signature:
                raise HTTPException(409, "A source or render resource changed. Request this frame again.")
            with LOCK: require_project_context({"_context": captured}, active_id(), load_project())
            return Response(content, media_type="image/png", headers={"Cache-Control": "no-store",
                "Content-Disposition": f'inline; filename="frame_{index:08d}.png"',
                "X-Filmocity-Frame": str(index), "X-Filmocity-Time": str(from_frames(index, seq["fps"]))})
    except WorkBusy as error:
        raise HTTPException(429, str(error)) from error
    except (ValueError, RuntimeError, OSError) as error:
        raise HTTPException(422, "Frame render failed: " + str(error)[:400]) from error
    finally:
        FRAME_SLOTS.release()

@app.get("/api/projects/versions")
def saved_versions(kind: str = "snapshots"):
    with LOCK:
        pid = active_id(); proj = load_project()
        try: catalog = list_versions(P("projects", pid, "project.json"), kind)
        except RecoveryError as error: raise HTTPException(422, str(error)) from error
        return {"kind": kind, "context": project_context(ROOT, pid, proj), **catalog}

async def restore_saved_version(body, kind):
    with LOCK:
        pid = active_id(); before = load_project(); require_project_context(body, pid, before)
        project_file = P("projects", pid, "project.json")
        name = body.get("name" if kind == "snapshots" else "file")
        if "_context" in body and not isinstance(body.get("sha256"), str):
            raise HTTPException(422, "Choose a version from a refreshed list before restoring")
        try:
            document, sha = read_version(project_file, kind, name, body.get("sha256"))
            # Preserve an explicit checkpoint even after the finite undo history expires.
            preserved = write_snapshot(project_file, before, "before_" + kind.rstrip("s") + "_restore")
        except RecoveryConflict as error: raise HTTPException(409, str(error)) from error
        except FileNotFoundError as error: raise HTTPException(404, str(error)) from error
        except (RecoveryError, TypeError, ValueError) as error: raise HTTPException(422, str(error)) from error
        warning = commit_edit(before, document, pid, {"actor": body.get("actor", "human"), "reason": "restore " + kind.rstrip("s") + ": " + name, "ts": time.time()}, protected_backup=name if kind == "backups" else None)
        context = project_context(ROOT, pid, document)
        ev = {"type": "project_replaced", "project": pid, "source": kind.rstrip("s") + "_restore", "file": name,
              "actor": body.get("actor", "human"), "client": body.get("client"), "context": context}
        try: log_event(ev, project_id=pid)
        except OSError as error: warning = (warning + "; " if warning else "") + "Restored, but event history could not be recorded: " + str(error)[:200]
    try: await broadcast(ev)
    except Exception as error: warning = (warning + "; " if warning else "") + "Restored, but notification failed: " + str(error)[:200]
    return {"ok": True, "context": context, "file": name, "sha256": sha, "preserved": preserved["file"], "warning": warning}

@app.get("/api/snapshots")
def snapshots_list():
    return list(reversed(saved_versions("snapshots")["versions"]))

@app.get("/api/snapshots/get")
def snapshots_get(file: str):
    with LOCK:
        pid = active_id()
        try: document, _ = read_version(P("projects", pid, "project.json"), "snapshots", file)
        except FileNotFoundError as error: raise HTTPException(404, str(error)) from error
        except RecoveryError as error: raise HTTPException(422, str(error)) from error
        return document

@app.post("/api/snapshots/restore")
async def snapshots_restore(req: Request):
    return await restore_saved_version(await req.json(), "snapshots")

@app.websocket("/ws")
async def ws(websocket: WebSocket):
    denied = access_error(websocket, TOKEN["value"])
    if denied: await websocket.close(code=4401 if denied[0] == 401 else 4403); return
    await websocket.accept(); CLIENTS.append(websocket)
    try:
        while True: await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in CLIENTS: CLIENTS.remove(websocket)

def restore_render_history():
    from job_history import restore
    with RENDER_STATE_LOCK:
        # Initialization only: existing live jobs must not become interrupted.
        if JOBS or RENDER_WORKERS or not RENDER_Q.empty():
            raise RuntimeError("Cannot restore export history over a live render queue.")
        JOBS.update(restore(ROOT))


def _shutdown_workers(timeout=10):
    """Cooperative retirement with one shared deadline, including idle workers."""
    import logging
    from job_history import remember
    from project_lifecycle import COPY_WORKER
    deadline = time.monotonic() + max(0, timeout)
    SHUTDOWN.set()
    with RENDER_STATE_LOCK:
        workers = list(RENDER_WORKERS)
        for holder in RENDER_PROCS.values(): holder["cancelled"] = True
        for job in JOBS.values():
            if job.get("status") == "queued":
                job.update(status="error", error="Stopped before starting. Retry explicitly.", finished=time.time())
                try: remember(ROOT, job)
                except Exception as error: logging.getLogger(__name__).warning("Shutdown receipt unavailable: %s", error)
        while True:
            try: RENDER_Q.get_nowait()
            except _queue.Empty: break
            else: RENDER_Q.task_done()
        for _ in workers: RENDER_Q.put_nowait(None)
    # Signal every operation before joining any one worker, so independent
    # encoder/copy/speech phases can retire concurrently.
    COPY_WORKER.shutdown(0)
    tasks_stopped = True
    if TASKS is not None:
        tasks_stopped = TASKS.shutdown(max(0, deadline-time.monotonic()))
    copy_stopped = COPY_WORKER.shutdown(max(0, deadline-time.monotonic()))
    for thread in [AUTOSAVE_THREAD, *workers]: thread.join(max(0, deadline-time.monotonic()))
    unfinished = [thread.name for thread in [AUTOSAVE_THREAD, *workers] if thread.is_alive()]
    if not copy_stopped: unfinished.append("Filmocity project copy")
    if not tasks_stopped: unfinished.append("Filmocity background tasks")
    if unfinished:
        message = "Shutdown deadline reached; still stopping: " + ", ".join(unfinished)
        logging.getLogger(__name__).warning(message)
        raise RuntimeError(message)
    return {"ok": True, "unfinished": []}


@app.on_event("shutdown")
async def shutdown_background_tasks():
    return await asyncio.to_thread(_shutdown_workers, 10)


def mount():
    global ROOT, TASKS
    from export_storage import export_static_files
    ROOT = os.path.realpath(os.path.abspath(os.path.expanduser(ROOT)))
    # Own the whole library before migrations, recovery, or runtime directories.
    hold_workspace(ROOT)
    os.environ["FILMOCITY_ROOT"] = ROOT
    os.environ["FILMOCITY_DATA"] = ROOT
    for d in ("thumbs", "renders", "media", "proxies", "projects"): os.makedirs(P(d), exist_ok=True)
    migrate_legacy()
    restore_render_history()
    TASKS = TaskManager(ROOT, {"cover": _task_cover_workflow, "recipe": _task_recipe_workflow, "audio_analysis": _task_audio_workflow, "sync": _task_audio_sync, "render_replace": _task_render_replace, "analysis": _task_media_analysis, "media": _task_prepare_media, "transcribe": _task_transcribe, "package": _task_package, "package_import": _task_package, "collect": _task_collect})
    os.makedirs(P("fonts"), exist_ok=True); app.mount("/fonts", StaticFiles(directory=P("fonts")), name="fonts"); app.mount("/thumbs", StaticFiles(directory=P("thumbs")), name="thumbs"); app.mount("/renders", export_static_files(P("renders")), name="renders"); app.mount("/proxies", StaticFiles(directory=P("proxies")), name="proxies")
    app.mount("/static", StaticFiles(directory=FRONT), name="static")
    if os.path.isdir(ASSETS): app.mount("/assets", StaticFiles(directory=ASSETS), name="assets")
    if os.path.isdir(DOCS): app.mount("/docs", StaticFiles(directory=DOCS, html=True), name="docs")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--root", default=ROOT); ap.add_argument("--port", type=int, default=8787); ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--token", default=None, help="require this access token (use with --host 0.0.0.0 to share on a LAN)")
    a = ap.parse_args(); ROOT = a.root; mount()
    if a.token: TOKEN["value"] = a.token; print(f"access token set — open http://{a.host}:{a.port}/?token={a.token}")
    elif a.host not in ("127.0.0.1", "localhost"): TOKEN["value"] = uuid.uuid4().hex[:16]; print(f"LAN mode: generated access token — open http://<this-machine>:{a.port}/?token={TOKEN['value']}")
    from http_protocol import FilmocityH11Protocol
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning", timeout_graceful_shutdown=REQUEST_SHUTDOWN_GRACE,
                http=FilmocityH11Protocol)
else:
    mount()

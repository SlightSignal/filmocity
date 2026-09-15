# Agent API

Machine-readable schema: **`GET /api/openapi.json`** (Swagger UI at `/api/docs`). Version: `GET /api/version`.

**Shortcut:** `agent/filmocity_client.py` wraps everything below in a dependency-free Python class (`Filmocity(base, actor="agent")`); `agent/example_agent.py` is a complete worked example. Invalid ops are refused with HTTP 422 and a list of problems; after each PATCH the response carries `warnings` describing any overlaps the server resolved and `applied` — the exact clip lists of the tracks touched, post-normalisation (the same is broadcast to every client).
 (local HTTP + WebSocket; the same endpoints the UI uses). Base: `http://localhost:8787`

| Method | Path | Body / notes |
|---|---|---|
| GET | `/api/project` | the whole project JSON (docs/PROJECT_FORMAT.md) |
| PATCH | `/api/project` | `{ops:[…], actor:"agent", tool, reason, client}` — ops: `set_clip` (upsert by id), `remove_clip`, `set`/`insert`/`remove` on a JSON path (`/sequences/0/markers/0`, `/sequences/0/tracks/2/gain_db`, `/sequences/0/captions`, `/sequences/0/in_point`) |
| PUT | `/api/project` | replace the project (avoid while a human is editing) |
| POST | `/api/media/import` | `{paths:[…], actor}` → probe, thumbs, waveforms, background proxy |
| POST | `/api/media/upload` | multipart file |
| GET | `/api/media/file/{id}?proxy=1` | range-capable media (proxy when ready) |
| GET | `/api/events?since=` · WS `/ws` | event log / live events (`ops`, `project_replaced`, `media_added`, `proxy_ready`, `snapshot`, `render`, `annotation`, `proposal`, `proposal_decision`) |
| POST | `/api/proposals` | `{actor:"agent", title, items:[{ops:[…], reason, tool}]}` → shows in the Proposals tab; each item accepted/rejected by the human |
| POST | `/api/proposals/{pid}/{iid}/{accept|reject}` | `{note}` — applies the ops if accepted; logs to `training/proposal_decisions.jsonl` |
| GET | `/api/proposals` | all proposals with statuses |
| POST | `/api/snapshot` | `{label:"agent_proposal"|"human_final"|…, actor, sequence}` → snapshot; a `human_final` after an `agent_proposal` appends a diff pair to `training/edit_pairs.jsonl` |
| GET/POST | `/api/snapshots`, `/api/snapshots/restore` | list / restore `{name}` |
| POST | `/api/annotate` | `{target:{sequence,track,clip_id}|{time}, label, note, actor}` |
| POST | `/api/captions/import` · GET `/api/captions/export` | `{srt, sequence, append}` / SRT text |
| POST | `/api/render` · GET `/api/render/{job}` | `{sequence, preset:{crf|bitrate, loudnorm, range, x264_preset, vcodec}, name, actor}` → `/renders/<name>.mp4` + `.cmd.txt` |
| GET | `/api/render_command?sequence=` | the exact FFmpeg command and filter graph (headless render without the UI) |
| GET | `/api/frame?sequence=&t=` | a rendered PNG frame at time t (exact pipeline) |
| GET | `/api/export/fcpxml?sequence=` · `/api/export/otio?sequence=` | FCP7 XML (opens in Premiere/Resolve) / OpenTimelineIO JSON |
| POST | `/api/import/fcpxml` | `{xml, actor}` → new sequence(s) from a Premiere/FCP7 XML; media imported by path |
| POST | `/api/render` with `outputs` | `{…, outputs:[{suffix,width,height,fit:"crop"|"pad"}]}` → one job per derived output; GET `/api/jobs` lists jobs |
| GET | `/api/encoders` | encoders available in this FFmpeg build (`preset.vcodec`) |
| GET | `/api/fonts` | font families available to drawtext (fontconfig) |
| POST | `/api/media/relink` · GET `/api/media/status` | `{media_id, path}` / which media files exist on disk |
| POST | `/api/captions/auto` | `{sequence, model}` → transcribe with faster-whisper/whisper if installed (501 with instructions otherwise) |
| (render jobs) | `qa` on finished jobs | `{width,height,duration,size_mb,integrated_lufs,true_peak_dbtp,lra_lu,flags[]}` — the agent should read `flags` before delivering |
| GET/PUT | `/api/settings` | workstation settings (keymap, workspace) |
| GET | `/api/effects` | the effect catalog (types, params, defaults) — set `fx_stack:[{type, enabled, params}]` / `afx_stack` on clips |
| POST | `/api/stabilize` | `{media_id, shakiness}` → vidstab analysis; then add `{type:"stabilize"}` to the clip's `fx_stack` |
| GET | `/api/luts`, `/api/templates`, `/api/presets/{caption_styles|export_presets}` | bundled libraries |
| POST | `/api/render/{id}/cancel` · GET `/api/snapshots` (file, ts, label) · GET `/api/snapshots/get?file=` | cancel a render · list/fetch snapshots (progress is on the job as `progress` 0–1) |
| GET/POST/DELETE | `/api/fonts` · `/api/fonts/upload` (multipart) · `/api/fonts/{file}` | user fonts; `project.brand {primary, secondary, text, font}` is substituted into `{{…}}` placeholders of templates and caption presets |
| POST | `/api/media/import_sequence` | `{folder, fps}` numbered image frames → one clip |
| GET | `/api/render/segments?sequence=` · POST `/api/render/preview` | render-bar status per segment (cached or not) · render the whole sequence into the cache for exact playback |
| POST | `/api/undo` · `/api/redo` · GET `/api/undo/stack` | project-wide undo/redo of the last op group by anyone (your ops are undoable by the human — write reasons) |
| GET | `/api/diagnostics` · `/api/backups` · POST `/api/backups/restore` · POST `/api/projects/sample` | system check · rolling backups · generated sample project |
| POST | `/api/audio/silences` | `{media_id, in, out, threshold_db, min_gap}` → silent gaps (media time); SDK `remove_silences(clip_id)` does the ripple cut |
| POST | `/api/audio/beats` | `{media_id}` → bpm, beat length, beat and downbeat times (media time) |
| POST | `/api/audio/remix` | `{media_id, target}` → bar-aligned segments to retarget a music bed (Remix) |
| POST | `/api/advisor/score` · `/api/advisor/train` · GET `/api/advisor/report` | score proposed ops against learned preferences before proposing (see docs/TRAINING_PIPELINE.md) |
| GET | `/api/media/probe?media_id=` | full ffprobe metadata; editable fields live in `media.meta` (description, log_note, scene, shot, keywords, good, start_tc) |
| POST | `/api/media/breakout` · `/api/render_all` | Breakout to Mono (two channel-mapped items) · batch export every sequence `{preset, prefix}` |
| POST | `/api/media/input_transform` | `{media_id, transform: none|slog3|vlog|clog3|logc3}` camera log → Rec.709 conversion at the media level |
| POST | `/api/media/interpret` · `/api/media/extract_audio` | `{media_id, fps|null}` frame-rate interpretation (sets `interpret_fps`, rescales duration) · audio-only bin item |
| POST | `/api/media/synthetic` | `{kind: black|color|bars|transparent|counting_leader, color, duration, width, height}` → generator media (no file) |
| POST | `/api/audio/peak` | `{media_id, in, out}` → max peak dBFS (Audio Gain normalize) |
| POST | `/api/render_replace` | `{sequence, clip_id}` → bakes the clip with effects to a media file and replaces it |
| GET | `/api/export/edl?sequence=&track=` · `/api/captions/export_vtt?sequence=` | CMX3600 EDL · WebVTT |
| GET | `/api/app_events` | Events panel feed: renders, proposals, decisions, media, offline files, queue depth |
| POST | `/api/audio/sync` | `{media_ids:[…]}` → offsets aligning each clip's audio to the first (multicam sync) |
| GET | `/api/markers/export?sequence=&fmt=chapters|csv` | markers as YouTube chapter text or CSV |
| GET/POST | `/api/projects`, `/api/projects/new {name, copy_media}`, `/api/projects/open {id}`, `/api/projects/save_as {name}`, `/api/projects/collect` | multiple projects under `data_root/projects/<id>/` (project.json, events.jsonl, snapshots/, training/); `active.json` selects the open one |
| POST | `/api/media/scenes` | `{media_id, threshold, in, out}` → scene-change cut times (seconds from `in`) |
| POST | `/api/transcript` | `{sequence, model, captions}` → `sequence.transcript = [{w,s,e,p}]` word timings (+ captions); needs faster-whisper |
| GET | `/api/fs?path=` | Media Browser: folders and media files on the workstation |
| POST | `/api/media/subclip` | `{media_id, in, out, name}` → subclip media entry (`subclip_of`, `sub_in`) |
| POST | `/api/audio/measure` | `{media_id, in, out}` → integrated LUFS / true peak of a range |
| POST | `/api/events` | client telemetry: `{type:"playback", sequence, start, end, rate}` etc. |
| GET | `/api/session/summary?since=` | digest for the agent: human ops by tool, clips touched, reviewed ranges + heat-map, proposal decisions with reasons, annotations, project brief |
| GET | `/api/training/export` | writes `training/dataset_<ts>.jsonl` (schema 2): meta+brief, events, edit pairs, proposal decisions (with `reasons`), annotations, snapshot refs |

## Review notes
`POST /api/markers/note {sequence, time, text, duration?, author}` leaves a resolvable note marker; SDK `note(t, text)`. Render presets accept `platform: reels|tiktok|shorts|youtube|stories|linkedin|x|facebook` and the job's `qa.flags` then include delivery-rule violations.

## Reading and varying the cut
`GET /api/sequence/describe?sequence=` → `{text (markdown), duration, clips, sections}`; `POST /api/sequences/variants {sequence, hooks:[…]}` → duplicate sequences with the hook re-fitted; `GET /api/renders/manifest` → deliverables with QA. SDK `describe()`, `variants()`.

## Recipes
`POST /api/recipes/talking_head {sequence, clip_id, broll, punch_every, silences, voice_preset, captions}`; `POST /api/audio/duck_all {sequence, amount, fade}`; review copies via preset `burn_tc`/`watermark_text`; client review at `/review/<render name>` (comments → `POST /api/review/<name>/notes`).
`POST /api/recipes/cover {sequence, time, headline, sub, sizes}` → cover PNGs; `POST /api/recipes/explainer {sequence, lower_third:{name, role, at}, chapters, end_card}` → cards from chapter markers.
`POST /api/recipes/reel {sequence, shots:[media_id…], music, target, hook, hook_sub, cta, look, captions, caption_style, sfx}` builds a complete reel (hook card, beat-cut shots, remixed bed, CTA, captions, whooshes) from the brand kit and premium templates; SDK `reel()`.

## Captions
`sequence.caption_style` accepts `animate: "highlight"|"pop"` and `highlight_color` for word-by-word animation (timed from `sequence.transcript` words when present).

## Layered motion graphics
Layers may also be `image` (`{kind:"image", path, x, y, w, h (fractions), fit:"contain"|"cover", opacity}`), and shapes accept `gradient:{to, angle}` and `shadow:true` (with `shadow_x/y/blur/color/opacity`). Clips accept `fit:"blur_fill"`. `POST /api/media/sfx {kind}` adds a generated sound effect.
A graphic clip is `{graphic:{name, layers:[…]}}`; each layer (`text` | `box` | `shape`) may carry `anim_in` / `anim_out` = `{type, duration, delay, ease, distance?}` with types fade · rise · drop · slide_left/right/up/down · pop · zoom · wipe_left/right/up/down · typewriter (text, in only) · rotate_in, eases ease_out · ease_in · ease_in_out · linear · back_out · bounce, and static `opacity`, `scale`, `rotation`, `glow`/`glow_size`/`glow_opacity` (text), `blur`. Per-layer keyframes: `clip.keyframes["g<i>.x"|"g<i>.y"|"g<i>.scale"|"g<i>.rotation"|"g<i>.opacity"] = [{t, v, e?}]` (t relative to the clip start; x/y in pixels). `POST /api/graphics/split_words {sequence, clip_id, layer, anim, stagger}` turns a text layer into staggered per-word layers. SDK: `graphic()`, `animate_layer()`, `split_words()`.

## Clip fields the agent can set
`note (rationale shown in the clip tooltip — write why the clip is there), audio.channels: stereo|left|right|mono|swap, title.vertical, color.whites/blacks, sequence.guides{h:[fractions],v:[…]}; fx_stack item {type:"hsl_secondary", params:{key_color, range, softness, hue_shift, saturation, lightness, invert}}; graphic shape layers accept shape:"polygon" with points:[[x,y],…] (fractions); media may carry interpret_fps; name (display name), fx_stack item {type:"track_matte", params:{track, type: alpha|luma, invert}}; sequence_id + multicam_angle (a nested multicam sequence with `multicam:true`; angle = index of the video track to show), reverse:true, time_interpolation: frame_sampling|frame_blending|optical_flow, fit: contain|cover, group (shared id), audio.maintain_pitch, audio_transition_in/out.type: constant_power|constant_gain|exponential, transition types add dip_black, dip_white, wipe_left/right/up/down, iris, push_left/right, slide_left/right/up/down, cross_zoom; color adds curves_r/curves_g/curves_b, vibrance, wheels{shadows|midtones|highlights:{r,g,b}}; tracks accept audio_fx and gain_db (bus); sequence.master{gain_db, audio_fx}; sequence.transcript; markers accept duration and type; render presets accept incremental:true (default; segment-cached export — after your edits only changed segments re-encode), chapters:true (embed markers), format: h264|hevc|prores|gif|png_sequence|audio (+acodec wav|mp3|aac); `effects{crop{l,t,r,b}, blur, sharpen, vignette, chromakey{enabled,color,similarity,blend}}, transform.anchor_x/anchor_y, color.curves:[[x,y],…], audio.pan (−1..1), markers:[{t,name,color}] (clip markers), graphic layers may be {kind:"shape", shape:"rect"|"ellipse", x,y,w,h, color, opacity, radius, stroke, stroke_color}; start, in_, out, speed, time_remap:[{t (timeline offset), v (speed), e?:"hold"}] (speed ramps; timeline duration is derived), hold:true (frame hold at in_; out−in_ is the hold duration), blend: normal|multiply|screen|overlay|darken|lighten|difference|add|softlight|hardlight|exclusion|subtract, label (CSS color), keyframes may also target mask.x/mask.y/mask.w/mask.h/mask.feather (animated masks); a keyframe may carry e:"bezier" with o:[x_fraction,y_delta] and the next keyframe i:[x_fraction,y_delta] handles, enabled (false = disabled), graphic{name, layers:[{kind:"box",x,y,w,h,color}|{kind:"text",text,size,color,font,weight,align,valign,x,y,box,boxcolor,shadow,borderw,line_spacing}]}, audio_transition_in/out{type:"constant_power",duration}, sequence_id (nested clip; media_id null), adjustment:true (adjustment layer; color applies below), mask{type:rect|ellipse,x,y,w,h (frame fractions),feather (px),invert}, audio_fx{eq{low_db,mid_db,high_db},comp{enabled,threshold_db,ratio,attack_ms,release_ms,makeup_db},denoise{enabled,db},limiter}, transform{x,y,scale,rotation,opacity}, keyframes{"transform.x"|"transform.y"|"transform.scale"|"transform.opacity"|"audio.gain_db": [{t,v,e?}]} (t is clip-relative seconds; e = linear|ease|ease_in|ease_out|hold for the segment starting at that keyframe), transition_in/out {type: dissolve|dip_black|dip_white|wipe_left|wipe_right|push_left|push_right|fade, duration}, color{exposure,contrast,highlights,shadows,temperature,tint,saturation,lut}, audio{gain_db,fade_in,fade_out,linked}, title{text,size,color,x,y,borderw}`. Sequence fields: `width,height,fps,in_point,out_point,markers[],captions[],caption_style{size,y,box,borderw,color}`; track fields: `muted,solo,locked,gain_db`.

## Governance
The human chooses in the header whether agents may edit directly or must propose. Under **proposals only**, `PATCH /api/project` from any non-human actor returns **403** with the message to use `POST /api/proposals`; the SDK raises `FilmocityError` — catch it and fall back to `propose()`. Check `GET /api/settings` → `agent_mode` before a long session. Send `POST /api/events {type:"focus", t}` (SDK `focus(t)`) to show the human where you are working.

## Proposals are previewable
The human can press **Preview** on a pending proposal to see it in the Program Monitor without applying it; write proposals whose visual intent is clear from a single frame when possible, and put the reasoning in `reason` and in the affected clip's `note`.

## Reading the human
Before proposing, call `GET /api/session/summary?since=<your last read>`: it tells you which tools the human used, which clips they touched, which ranges they replayed (attention), what they accepted/rejected and why (reason codes), and the brief. Write proposals against that context; keep `reason` short and concrete — it is shown on the timeline ghost and stored with the decision.

## The intended loop
1. Agent imports media, builds a cut with `PATCH` ops, then `POST /api/snapshot {label:"agent_proposal", actor:"agent"}` (after the proposal is complete). Optional preview render.
2. Human edits in the UI (every action logged with tool + before-state) and/or the agent posts **proposals** for specific changes with reasons; the human accepts/rejects each.
3. Human presses **Mark as human final** → diff pair logged. The agent reads `training/edit_pairs.jsonl`, `training/proposal_decisions.jsonl`, `events.jsonl` and `annotations` to learn preferences per brand and per format.
4. Export (deterministic from JSON) or hand the cut to Premiere via XML when needed.
Rules: `actor:"agent"` and a human-readable `reason` on every write; prefer proposals over direct `PATCH` once a human has started editing; never `PUT` while a human is active.

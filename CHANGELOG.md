# Filmocity changelog
## 0.44 — 2026-09-06 — "Polish"
- Design layer (palette, spacing, controls, panels, dialogs, menus, scrollbars, clips, empty states); 14 categorised, described, validated base effect presets shown as cards.
## 0.43 — 2026-09-06 — "Fullest"
- Client review page + notes → markers, review copies (burn_tc/watermark_text, full render), talking-head recipe with b-roll, duck_all, audio crossfade all, background idle rendering, text styles, whisper installer; SDK talking_head/duck_all/review_url.
## 0.42 — 2026-09-06 — "Spec"
- tests/test_spec.py + docs/SPEC_AUDIT.md (69 requirement checks); Track Select Backward palette tool.
## 0.41 — 2026-09-06 — "Authoritative"
- PATCH and broadcasts carry touched tracks after normalization; clients adopt them (no optimistic drift, no reloads); tests/test_recipes.py.
## 0.40 — 2026-09-06 — "Token"
- Optional access token (--token / LAN auto), SDK token support, soak measurements, GAPS refresh.
## 0.39 — 2026-09-06 — "Cover"
- Cover/thumbnail recipe, explainer dressing recipe (own tracks, clamped durations), cache info/clear in System Check, docs/PLAYBOOK.md.
## 0.38 — 2026-09-06 — "Delivery"
- Platform QA rules (preset.platform), platform UI overlays in the monitor, review-note markers with author/resolved (+ /api/markers/note, SDK note()).
## 0.37 — 2026-09-06 — "Variants"
- Hook variants (/api/sequences/variants), sequence describe, renders manifest CSV, section markers from the reel recipe, shared hook fitting; SDK describe/variants.
## 0.36 — 2026-09-06 — "Talking heads"
- /api/audio/silences + Remove Silences (ripple), auto punch-in preset, bundled effect presets, presets load fix, SDK silences/remove_silences.
## 0.35 — 2026-09-06 — "Reel"
- tests/test_catalog.py (122 catalog items rendered and checked); /api/recipes/reel + New Reel dialog + SDK reel(); hook text fitting; gentler caption pop.
## 0.34 — 2026-09-06 — "Gallery"
- Template gallery previews (/api/templates/preview), look gallery via GPU, gradient/light-leak/grain synthetics, glitch transition, uppercase, cross-zoom fix (sendcmd), transition auto-SFX preference.
## 0.33 — 2026-09-06 — "Premium"
- Custom fonts (upload/list/delete, @font-face + fontfile), brand kit with template/caption placeholders, 12 premium templates, 6 caption presets, 8 looks, 14 export presets; broad import extensions, audio proxies, image-sequence import, playability-aware proxy use, WebM/AV1/MOV exports; SDK brand/template/upload_font.
## 0.32 — 2026-09-06 — "Cards"
- Gradient + shadow shape layers, image layers in graphics (+ /api/media/path), blur-fill fit, generated SFX (/api/media/sfx), Layer Animator card/image controls; fix: sendcmd now targets unique filter instances (crash with blur fill + pop; opacity keyframes vs glow).
## 0.31 — 2026-09-05 — "Reel"
- Word-animated (karaoke) captions in render + preview with PIL layout and transcript timing; beat grid endpoint + Add Beat Markers; clip motion presets; Auto colour and Colour Match (clip-frame statistics); SDK beats/caption_style.
## 0.30 — 2026-09-05 — "Coherent"
- Per-layer keyframes (x/y/scale/rotation/opacity) in render, compositor, Layer Animator; smart export keeps animated graphics whole; advisor graphics features; example agent animated lower third; docs/FEATURES.md.
## 0.29 — 2026-09-05 — "Motion"
- Per-layer graphics animation engine (render + compositor): in/out types, easing, delays, per-layer opacity/scale/rotation/glow/blur; Layer Animator panel; cascade/mirror/preview; kinetic word split with PIL-measured placement and baseline alignment; animated templates; SDK graphic/animate_layer/split_words.
## 0.28 — 2026-09-05 — "Log"
- Camera log input transforms (S-Log3, V-Log, C-Log3, LogC3) in render and GPU preview; panel keyboard-focus model.
## 0.27 — 2026-09-05 — "Transitions"
- Draggable transition handles + typed durations, outgoing clip under transitions in preview, geometric transition previews, track reordering, metadata-aware bin search, chapters in smart export mux.
## 0.26 — 2026-09-05 — "Render bar"
- Render bar from segment cache status, Render Entire Sequence (Enter) with exact preview playback, inline text editing on the monitor, typed clip duration (ripple with Shift), SDK replace_text/set_duration/render_preview/segments.
## 0.25 — 2026-09-05 — "Smart export"
- Incremental segment-cached export (render_incremental): changed segments only, stream-copy stitch, whole-programme audio; queue shows cache reuse; export/preferences toggle.
- Fix: single-input amix truncated audio on single-bus projects; audio-only renders no longer decode video.
## 0.24 — 2026-09-05 — "Hands"
- Monitor direct manipulation (scale/rotate handles, anchor), trim readout + edge-frame preview, slip four-up, Ctrl-drag insert, fx badges, hot-text scrubbing, timeline context menu, bin rename + hover scrub, per-track meters; regression tests drive them with real mouse events.
## 0.23 — 2026-09-05 — "Footage"
- Real-footage test (11 formats), rotation-aware dimensions, animated GIF as video; server-side undo/redo (POST /api/undo, /api/redo, GET /api/undo/stack) covering agent ops and surviving reloads; op-diff live sync; error toasts.
## 0.22 — 2026-09-05 — "Exact"
- WebGL2 colour pipeline (LUT, curves, wheels, vibrance, keys, HSL Secondary, invert/B&W exact in preview), /api/luts/file, GPU preference; op fuzzer test.
## 0.21 — 2026-09-05 — "Audible"
- Web Audio preview graph (gain/pan/EQ/comp/effects/buses/master limiter; meters on master), background ingest with timeouts and media_ready events, probe flags (hdr/rotation/vfr), HDR→SDR tonemap on export, more encoder probes, transition alignment, advisor evaluation harness, edit pairs as labels, saving indicator.
## 0.20 — 2026-09-05 — "Gaps"
- docs/GAPS.md inventory. Fixes: blocking work off the event loop (asyncio.to_thread), render stall watchdog, Swagger at /api/docs, single VERSION + /api/version, advisor predictions stored on proposals and decisions.
## 0.19 — 2026-09-05 — "Governance"
- Agent mode (direct / proposals only), agent presence cursor + SDK focus(), Comparison View (side/wipe), relink via Media Browser, timed autosave, dataset_stats.py, silent Windows launcher.
## 0.18 — 2026-09-05 — "Queue"
- Render Queue panel with progress + cancel (FFmpeg -progress), export dialog percent; layouts/tabs/workspace persisted and restored (settings merge); Compare to snapshot diff; bin delete; snapshot list metadata; load-order race fixed.
## 0.17 — 2026-09-05 — "First launch"
- System Check diagnostics, Sample Project generator, backups restore, draft playback preference.
- Timeline virtualization + reflow fix (≈10× faster redraws on large timelines), clipEl single-pass HTML.
## 0.16 — 2026-09-05 — "Portable"
- Bundled fonts, fontconfig-free font resolution on Windows, filter-path escaping (drive colons/backslashes), docs/WINDOWS.md.
- Time Tuner (fit sequence to duration), chapters embedded in exports.
## 0.15 — 2026-09-05 — "SDK"
- Op validation with 422s, track normalization with warnings + UI resync, Remix to Duration, agent SDK (`agent/filmocity_client.py`), example agent, agent regression test.
## 0.14 — 2026-09-05 — "Advisor"
- Feed-forward advisor: trains on proposal decisions, mines rules, scores proposals (train/report/score endpoints), UI badges and preferences report.
- Metadata panel (ffprobe + editable log fields + start timecode), nested bins, agent activity indicator, project schema v3 with migration, rolling backups.
## 0.13 — 2026-09-05
- Whites/Blacks, vertical text, audio channel mapping + Breakout to Mono, rulers and draggable guides with snapping, proposal preview mode, clip notes, inline caption edit + upgrade to text, batch export of all sequences; shortcut fixes (Ctrl+T text, Ctrl+Shift+T trim type).
## 0.12 — 2026-09-05 — "Trim"
- Trim Edit mode with two-up trim monitor, trim types (regular/ripple/roll) and dynamic JKL trimming; through-edit markers; Reverse Match Frame.
- Interpret Footage (frame-rate override), Extract Audio, HSL Secondary, polygon shapes + tool, Distribute, Vectorscope, Reference Monitor, floating panels.
## 0.11 — 2026-09-05 — "Audit"
- Full Premiere menu audit (docs/AUDIT.md) and the additions it produced: synthetic generators, Cut/Paste Insert/Remove Attributes/Duplicate/Select Matching/Find/Remove Unused, Rename, Audio Gain dialog, Replace From Bin, Render and Replace, Synchronize, Merge Clips, Flatten multicam, Add Edit to All Tracks, Default Transitions to Selection, Selection Follows Playhead, Render In to Out preview, Auto Reframe, Mark Clip/Selection, Go to In/Out, Edit Marker, Markers/Events/Meters/Timecode panels, Align/Arrange, Guides, Alpha display, opacity rubber bands, source drag handles, Track Matte Key, effect presets, EDL + WebVTT export.
- Fix: audio track buses/master chain now render (the v0.8 wiring had silently failed to apply).
## 0.10 — 2026-09-05 — "Polish"
- In/Out-aligned filmstrips & waveforms, clip tooltips, Alt-drag duplicate, drag auto-scroll, loop / play around / play In–Out, timecode entry, monitor zoom + scrub bars, transparency grid, program-monitor drop target, panel maximize (`), track header context menu (rename/add/delete/empty), zoom slider, sortable bin columns, keyframe navigation + clear, dialog Enter/Esc, editable duration in Speed, multicam audio sync, markers export (chapters/CSV), undo count in session summary, headless UI regression test.
## 0.9.1 — 2026-09-05 — "Run it"
- Launcher: `Filmocity.bat` / `Filmocity.command` / `filmocity.sh` install missing dependencies before starting (previously required the installer first).
- Export: render queue (sequential by default; Preferences → Parallel renders 1–4); correct output extension per format; status `queued`.
- API: restored routes lost in the queue refactor (media browser, subclips, projects, transcript, effects catalog, session summary, training export, luts/templates/presets, fonts, relink, captions auto); added `tests/test_routes.py` inventory guard.
- Captions: wrap to safe width (max 3 lines) in preview and render.
## 0.9 — effects registry (73), Warp Stabilizer, drop shadow, 14 transitions, bundled LUTs/templates/presets, SVG icons, User Guide, Feature Matrix, API smoke test.
## 0.8 — multicam, projects, scene detection, text-based editing, reverse/optical flow, track buses, color wheels, scopes, export formats/presets.
## 0.7 — media browser, subclips, effects, proposals as timeline ghosts, reason taxonomy, telemetry, brief, training export.
## 0.6 — Premiere-mirroring workspace, one-click launchers.
## 0.5 — time remapping, blend modes, animated masks, bezier keyframes, workspaces.
## 0.4 — fonts, graphics templates, gain rubber band, auto-duck, QA reports, auto-captions.
## 0.3 — nested sequences, adjustment layers, masks, keyframe lanes, audio FX, multi-output export, FCP7 XML import.
## 0.2 — trim tools, linked audio, keyframes, transitions, color, captions, proposals.
## 0.1 — first cut: bin, monitors, timeline, render, agent API.

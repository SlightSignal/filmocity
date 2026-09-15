# Premiere Pro → Filmocity feature matrix

| Area | Premiere Pro | Filmocity | Status |
|---|---|---|---|
| Project & media | Project panel (list/icon, bins, search, columns) | Project dock: list/icon views, bins, search, Name/Frame Rate/Duration/Video/Audio columns | ✅ |
| Project & media | Media Browser | Media Browser tab (folders, import, import-all) | ✅ |
| Project & media | Import (video, audio, stills, image sequences) | Video/audio/stills; image sequences: import frames as stills (no auto-sequence) | ◐ |
| Project & media | Subclips | Ctrl+U from Source In/Out | ✅ |
| Project & media | Proxies / Ingest presets | Background proxies on import; proxy toggle in Program monitor | ✅ |
| Project & media | Offline media / Relink / Project Manager | Offline badge + relink; Collect Files copies media and relinks | ✅ |
| Project & media | Multiple projects / Save As | File → New / Open / Save As; per-project event logs and training data | ✅ |
| Project & media | Team Projects / Productions | not applicable (single workstation, agent + human) | — |
| Project & media | Metadata / Info panel | Info panel (sequence, clip, media) + shared Brief | ◐ |
| Sequences | New sequence, settings, presets | Sequence Settings with social/YouTube presets; New Sequence From Clip | ✅ |
| Sequences | Nesting | Nest selected; drag a sequence to a track; double-click to open | ✅ |
| Sequences | Multicam source sequences / Multi-Camera view / cut-and-switch | Create from bin selection; ▦ grid; keys 1–9 cut & switch | ✅ (sync by in points; audio sync analysis pending) |
| Sequences | Adjustment layers | ✅ | ✅ |
| Timeline | Tools: V A B N R C Y U P H Z T, shape tools | All present, including Rectangle/Ellipse and Track Select Backward | ✅ |
| Timeline | Snapping, markers (types, durations, colors), clip markers | ✅ | ✅ |
| Timeline | Insert/Overwrite, Lift/Extract, Add Edit, Ripple/Roll/Slip/Slide, Extend, Q/W | ✅ | ✅ |
| Timeline | Trim mode / keyboard trims | Edit-point selection + Ctrl+←/→ (Shift ×5, Alt ripple); no dynamic JKL trim mode | ◐ |
| Timeline | Link/unlink, group, enable/disable, labels, rename | ✅ (rename via Info; labels palette) | ✅ |
| Timeline | Sync lock, track lock, output toggle, mute/solo, source patching, targeting | ✅ | ✅ |
| Timeline | Track height, expand/minimize, display options (thumbs/waveforms) | ✅ | ✅ |
| Timeline | Copy/Paste, Paste Attributes, Duplicate (Alt-drag) | Copy/Paste/Paste Attributes ✅; Alt-drag duplicate ✗ (use Ctrl+C/V) | ◐ |
| Timeline | Gap selection / Close gap | ✅ | ✅ |
| Timeline | Speed/Duration, Reverse, Time Remapping, Frame Hold, Rate Stretch, Maintain Pitch, Optical Flow | ✅ (optical flow via minterpolate — slow but exact) | ✅ |
| Timeline | Scene Edit Detection | ✅ | ✅ |
| Timeline | Text-based editing (transcript → cuts) | Transcribe with word timestamps; select words → delete/extract; needs faster-whisper | ✅ |
| Timeline | Caption track (C1) with caption blocks | ✅ draggable/trimmable blocks | ✅ |
| Effects | Motion (position/scale/rotation/anchor/opacity/blend) with keyframes, bezier, value graph | ✅ | ✅ |
| Effects | Masks (rect/ellipse, feather, invert, animated) | ✅ (no free-form pen masks yet) | ◐ |
| Effects | Video effects library | 43 catalogued effects + built-ins (crop/blur/sharpen/vignette/chroma key) with generated controls | ◐ (Premiere ships ~150; the missing ones are mostly obsolete or GPU-only) |
| Effects | Keying: Ultra Key, Color Key, Luma Key, Track Matte | Ultra/Color/Luma key ✅; Track Matte Key ✅ (alpha/luma from a named track) | ✅ |
| Effects | Warp Stabilizer | vidstab two-pass (Analyze in Effect Controls) | ✅ |
| Effects | Transitions library | Dissolve, Dip B/W, 4 wipes, Iris open/close, diagonals, barn doors, clock, checker, push, slide, cross zoom; audio constant power/gain/exponential | ◐ (Premiere: ~40) |
| Effects | Morph Cut, Auto Reframe, Remix | ✗ (ML features; Auto Reframe partially covered by per-output center crop/pad) | ✗ |
| Color | Lumetri Basic (exposure/contrast/highlights/shadows/whites/blacks/temp/tint/saturation) | Basic correction ✅ (whites/blacks folded into highlights/shadows) | ◐ |
| Color | Creative (LUTs, vibrance), Curves (RGB, per-channel), Color Wheels, Vignette | ✅ + 8 bundled looks | ✅ |
| Color | HSL Secondary, Hue/Sat curves | ✗ | ✗ |
| Color | Scopes (waveform/parade/histogram/vectorscope) | RGB parade + histogram ✅; vectorscope ✗ | ◐ |
| Audio | Clip gain, rubber band, keyframes, pan, fades, transitions | ✅ | ✅ |
| Audio | Audio Track Mixer, Audio Clip Mixer, master | Track buses with fx + master fader/limiter; clip mixer for clips under the playhead | ✅ |
| Audio | Audio effects library | 30 catalogued effects (EQ, dynamics, reverb/delay, modulation, noise reduction, stereo) | ◐ |
| Audio | Essential Sound (auto loudness, dialogue/music presets, ducking) | Auto-match loudness, Vocal Enhancer presets, Auto-duck | ✅ |
| Audio | VST/AU plugins | ✗ | ✗ |
| Graphics | Essential Graphics: text with fonts, styles, shapes, alignment, MOGRTs | Text (system fonts, box, shadow, outline, alignment), shapes, layered templates (12 bundled + save your own) | ✅ (JSON templates instead of .mogrt) |
| Captions | Manual captions, styles, import/export SRT, burn-in, auto-transcribe | ✅ + style presets | ✅ (no 608/708) |
| Export | Formats: H.264, HEVC, ProRes, GIF, PNG sequence, audio-only | ✅ + hardware encoders when present | ✅ |
| Export | Presets, multi-output (derived aspect ratios), In/Out range, loudness normalization | ✅ + QA report (LUFS/true peak/spec) | ✅ |
| Export | XML / OTIO / EDL interchange | FCP7 XML export+import, OTIO export, EDL (CMX3600) export | ✅ |
| Workspace | Menu bar, header bar, workspaces, dockable panels, keyboard shortcut editor, preferences | ✅ (tabs move between docks; free-floating panels ✗) | ◐ |
| Agent layer (Filmocity only) | Live agent edits, proposals with accept/reject, reasons, telemetry, session summary, training export | ✅ | ✅ |

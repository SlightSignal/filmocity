# What Filmocity is still missing — an honest inventory (v0.41)

Feature parity with Premiere is now broad (`docs/AUDIT.md`). This document is the other list: everything that is thin, untested, structurally weak or absent, across product, engineering, data and process. Severity: **S1** would embarrass us on a real job · **S2** a real editor would hit it in a week · **S3** matters at scale or later. Effort: S/M/L. Items marked **fixed in v0.20** were closed while writing this.

## 1. Never exercised for real
| Gap | Sev | Effort | Notes |
|---|---|---|---|
| **Windows and macOS launchers have never been executed** | S1 | S | `.bat`/`.command`/`.vbs` and the Windows branches of `bootstrap.py` are written carefully but untested; first Windows run is the single most valuable test left. |
| **Real camera footage** | S1 | M | **largely fixed in v0.23** — `tests/test_footage.py` covers rotation, 10-bit HEVC, HLG HDR, ProRes, VFR, portrait 4K, 59.94p, GIF, alpha PNG, 5.1 and 44.1k mono end to end (import → cut → export → frame checks). Still unverified: real camera files from actual devices (Sony/Canon/iPhone containers, log curves). |
| **Hardware encoders** (NVENC/QSV/AMF/VideoToolbox) | S2 | S | Detected but never used; AMF not probed at all. |
| **Long projects** (hours of media, hundreds of files, 30+ minute sequences) | S2 | M | Thumbnails/filmstrips/proxies are generated per import synchronously; an hour of 4K would block. Needs a background ingest queue with status. |
| **Non-ASCII and space-laden paths** on Windows | S2 | S | Escaping exists; untested with real filenames like `C:\Users\Émilie\My Footage\`. |

## 2. Structural / engineering
| Gap | Sev | Effort | Notes |
|---|---|---|---|
| Blocking FFmpeg calls inside async endpoints froze the whole server (imports, scene detection, transcription) | S1 | S | **fixed in v0.20** — heavy work runs in a thread pool. |
| A stalled render blocked the queue forever | S2 | S | **fixed in v0.20** — watchdog kills a job with no progress for 15 minutes. |
| FastAPI's OpenAPI/Swagger was hidden by our `/docs` mount | S3 | S | **fixed in v0.20** — machine-readable API at `/api/docs` and `/api/openapi.json` (agents can read the schema). |
| Version string drifted across files | S3 | S | **fixed in v0.20** — single `VERSION`, `/api/version`, About dialog. |
| Optimistic local ops vs server normalization can briefly disagree | S2 | M | Mitigated: resync on warnings; other clients' ops now arrive as diffs (v0.41). Full server-authoritative application remains the long-term fix. |
| No authentication; server binds 127.0.0.1 only | S2 | S | **fixed in v0.40** — `--token` (or auto-generated when `--host` is not local): header, `?token=` or cookie; WebSocket included. |
| Single JSON project file rewritten on every op | S3 | M | Measured (soak, 800 agent ops → 385 clips): PATCH 8 → 23 ms avg, files grow linearly; fine to a few thousand clips. |
| Every export re-encoded the whole programme | S1 | M | **fixed in v0.25** — segment-cached smart export; wording/trim changes re-encode only the affected segments. |
| WebSocket reconnect re-fetches the whole project | S3 | S | Live ops are diffs since v0.23; reconnect still refetches once. |
| Ingest is synchronous per request | S2 | M | **fixed in v0.21** — probe only at import; thumbnails/filmstrip/waveform/proxy in the background with `media_ready` events and per-call timeouts. |
| No ffprobe/ffmpeg timeouts on analysis calls | S3 | S | **fixed in v0.21** for probe/thumbnails/waveforms. |
| Tests need Chromium for the UI suite; no CI definition | S3 | S | `tests/test_fuzz.py` added in v0.22 (op-model fuzzing with invariants). Add `tests/run_all.py` and CI when this moves to a repo. |

## 3. Editing model
| Gap | Sev | Effort | Notes |
|---|---|---|---|
| Transitions are per-clip in/out, not a shared object at the cut | S2 | M | **fixed in v0.27** — alignment (start/center/end) plus draggable handles and typed durations on the timeline; the outgoing clip shows under the transition in preview. |
| Track reordering | S3 | S | **fixed in v0.27** — Move Track Up/Down. |
| Per-track keyframes (track volume envelopes) | S3 | M | Buses have static gain only. |
| Nested sequence editing "in place" with parent context | S3 | M | You open the nest as a tab; no parent-relative timing display. |
| Mixed frame rates: conform only, no rate-aware trims | S3 | M | 23.976/29.97 timecode display is integer-fps only (drop-frame TC not shown). |
| Colour management: Rec.709 assumed | S2 | L | **fixed in v0.28** — HDR tonemap (v0.21) + camera log input transforms for S-Log3, V-Log, C-Log3, LogC3 (generated from the published curves). Remaining: ACES-style scene-referred pipeline. |
| Multicam: no audio-follows-video mixing beyond one source; no angle thumbnails in the bin | S3 | S | |
| Time Tuner changes speed uniformly; Premiere also removes frames at cuts | S3 | M | |

## 4. Preview fidelity (what you see ≠ what renders)
| Gap | Sev | Effort | Notes |
|---|---|---|---|
| Audio in preview: effects, pan, buses, master limiter | S1 | M | **fixed in v0.21** — Web Audio graph (gain, pan, EQ, compressor, common effects, buses, master + limiter). Channel mapping and reverb/modulation effects still export-only. |
| Video effects preview: keys, curves, LUTs, HSL Secondary render-only | S2 | L | **fixed in v0.22** — WebGL2 colour pipeline previews these exactly; remaining approximations are blur-family and distortion effects. |
| Transitions in preview: wipes/iris/clock approximated as fades | S3 | M | **fixed in v0.27** — geometric clip-path previews. |
| Text rendering: canvas fonts vs FFmpeg freetype differ slightly in metrics | S3 | S | Use the bundled fonts in the browser via `@font-face` for parity. |
| Playback performance beyond ~6 simultaneous video layers | S2 | L | Canvas 2D; decode is the limit. Proxies help; Render Entire Sequence (Enter) gives exact playback of any stack; a real fix is WebCodecs. |

## 5. Data, learning, evaluation
| Gap | Sev | Effort | Notes |
|---|---|---|---|
| Advisor predictions were not stored with proposals, so calibration could not be measured | S2 | S | **fixed in v0.20** — `advisor_p`/`advisor_n` recorded on each proposal item and copied into the decision event. |
| No evaluation harness | S2 | M | **fixed in v0.21** — `agent/evaluate_advisor.py` (online calibration + time hold-out). |
| Human edits are implicit positives but unused as labels | S2 | M | **fixed in v0.21** — edit pairs become per-op rows (changed-by-human = 0, kept = 1). |
| Features are op-level; no perception (frame content, audio energy at the cut) | S2 | L | Frame embeddings at the cut would be the first perceptual feature. |
| No dataset versioning / provenance beyond `schema` | S3 | S | Add project id, Filmocity version and export hash to `meta`. |
| Reason taxonomy is fixed | S3 | S | Let the lab extend it in settings. |
| No consent/anonymization story if data leaves the workstation | S3 | S | Document; add a scrubbing export option (paths, names). |

## 6. UX polish still missing
| Gap | Sev | Effort | Notes |
|---|---|---|---|
| Error reporting is a status-bar line | S2 | S | **fixed in v0.23** — persistent toasts with a jump to Events. |
| Tooltips are inconsistent; many icon buttons lack them | S3 | S | |
| No keyboard focus model between panels | S2 | M | **fixed in v0.28** — panels take focus; timeline-mutating shortcuts are gated. |
| Small screens / laptop layouts untested | S3 | S | |
| Undo is client-side and lost on reload | S2 | M | **fixed in v0.23** — server-side undo/redo, project-wide, covers agent ops, persists. |
| No "unsaved/saving" indicator | S3 | S | **fixed in v0.21** — status bar shows saving…/saved. |
| Accessibility (ARIA, screen reader) absent | S3 | M | |

## 7. Not planned (and why)
OMF/AAF (licensing swamp, little value for social delivery) · Morph Cut (research project) · Adobe integrations (Dynamic Link, Stock, Frame.io, Team Projects) · GPU-accelerated *effects* in the export (FFmpeg CPU is exact and fast enough for ad-length pieces).

## Proposed order
1. **First real run on the workstation** (launchers, real footage, hardware encoders) — every other item's priority depends on what it reveals.
2. **Preview audio graph** (pan, effects, buses audible) — the biggest what-you-hear gap.
3. **Background ingest queue** and ffprobe timeouts — required before hour-long footage.
4. **Advisor evaluation harness** + edit pairs as labels — makes the learning loop measurable.
5. **Transition alignment / shared transition object** — the most-felt editing-model difference.
6. **Server-side undo + op-diff sync** — correctness under agent/human concurrency.
7. **WebGL compositor** — long-horizon; unlocks live LUTs/keys/transitions and 4K playback.

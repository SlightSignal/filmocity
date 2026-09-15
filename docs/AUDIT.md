# Functional audit — Premiere Pro menu-by-menu vs Filmocity (v0.16)

Method: walk every Premiere Pro menu and panel, name the Filmocity equivalent, mark ✅ done · ◐ partial · ✗ missing · — not applicable. Items marked **new** landed in v0.11 as a result of this audit.

## File
| Premiere | Filmocity | |
|---|---|---|
| New Project / Open / Open Recent / Close / Save / Save As / Save a Copy / Revert | Projects: New / Open (list) / Save As; Revert = Restore snapshot; autosave | ✅ |
| New Sequence / Sequence From Clip / Bin | ✅ / ✅ / bins | ✅ |
| New › Black Video, Color Matte, Bars and Tone, Transparent Video, Universal Counting Leader, Adjustment Layer | **new** — all five generators (rendered from FFmpeg sources, previewed on canvas) + adjustment layer | ✅ |
| New › Captions, Legacy Title, Photoshop File | Captions panel; text layers; ✗ PSD | ◐ |
| Link Media / Make Offline / Relink | offline detection + relink; Media Browser | ✅ |
| Import / Import from Media Browser | ✅ (path, upload, Media Browser, XML) | ✅ |
| Export › Media (+ **new** embedded chapters), Captions, Markers, EDL, Final Cut Pro XML, OMF/AAF, MOGRT | Media (H.264/HEVC/ProRes/GIF/PNG/audio) · Captions SRT + **new WebVTT** · Markers chapters/CSV · **new EDL (CMX3600)** · FCP7 XML · OTIO · templates as JSON; ✗ OMF/AAF | ◐ |
| Project Manager (collect/consolidate) | Collect Files (copy + relink) | ✅ |
| Get Properties | Info panel | ✅ |

## Edit
| Premiere | Filmocity | |
|---|---|---|
| Undo / Redo / History panel | ✅ | ✅ |
| Cut / Copy / Paste / Paste Insert / Paste Attributes / Remove Attributes | **new** Cut (Ctrl+X), **new** Paste Insert (Ctrl+Shift+V), Paste Attributes (now Ctrl+Alt+V, Premiere's default), **new** Remove Attributes dialog | ✅ |
| Clear / Ripple Delete / Duplicate | ✅ / ✅ / Alt-drag on timeline + **new** Duplicate bin items | ✅ |
| Select All / Select All Matching (label) / Deselect | ✅ / **new** / ✅ | ✅ |
| Find (timeline search) | **new** Ctrl+F by clip name/text | ✅ |
| Label colors | ✅ (context menu palette) | ✅ |
| Remove Unused / Consolidate Duplicates | **new** Remove Unused; ✗ consolidate | ◐ |
| Edit Original / Edit in Audition/Photoshop | — | — |
| Keyboard Shortcuts / Preferences | ✅ / ✅ | ✅ |

## Clip
| Premiere | Filmocity | |
|---|---|---|
| Rename | **new** (Effect Controls name field / Clip menu) | ✅ |
| Make Subclip / Edit Subclip | ✅ / edit via bin? ◐ | ◐ |
| Modify › Audio Channels / Interpret Footage / Timecode | **new** channel mapping (L/R/mono/swap) / Interpret Footage / ✗ timecode | ◐ |
| Video Options › Frame Hold, Frame Blend, Scale to Frame Size, Set to Frame Size | ✅ all | ✅ |
| Audio Options › Audio Gain, Breakout to Mono, Extract Audio | ✅ all (**new** Breakout to Mono) | ✅ |
| Speed/Duration (reverse, pitch, interpolation, ripple) | ✅ | ✅ |
| Scene Edit Detection | ✅ | ✅ |
| Insert / Overwrite / Replace Footage / Replace With Clip (Source Monitor, From Bin) | ✅ / ✅ / relink / ✅ / **new** From Bin | ✅ |
| Render and Replace / Restore Unrendered | **new** Render and Replace (bakes effects to a media file); ✗ restore | ◐ |
| Enable / Unlink / Group / Ungroup | ✅ | ✅ |
| Synchronize / Merge Clips | **new** Synchronize by audio (envelope cross-correlation); **new** Merge Clips (video + audio → merged sequence item) | ✅ |
| Nest / Multi-Camera (create, enable, flatten, angle) | ✅ / ✅ / **new** Flatten / ✅ | ✅ |

## Sequence
| Premiere | Filmocity | |
|---|---|---|
| Sequence Settings | ✅ | ✅ |
| Render In to Out / Render Selection / Delete Render Files | **new** Render In to Out → rendered preview playback (View › Play Rendered Preview) | ◐ |
| Match Frame / Reverse Match Frame | ✅ / **new** ✅ | ✅ |
| Add Edit / Add Edit to All Tracks | ✅ / **new** Ctrl+Shift+K | ✅ |
| Trim Edit (trim monitor) | **new** Trim Edit mode: two-up monitor, ±1/±5, regular/ripple/roll, dynamic JKL trimming | ✅ |
| Extend Selected Edit to Playhead / Apply Video/Audio Transition / Apply Default Transitions to Selection | ✅ / ✅ / **new** Shift+D | ✅ |
| Lift / Extract / Zoom / Selection Follows Playhead / Show Through Edits / Linked Selection / Snap | ✅ all (**new** Show Through Edits) | ✅ |
| Add / Delete Tracks | ✅ (header menu) | ✅ |
| Auto Reframe Sequence / Time Tuner | Auto Reframe (target aspect + fill-frame) · **new** Time Tuner (fit to duration by uniform speed) | ◐ |

## Markers
| Premiere | Filmocity | |
|---|---|---|
| Mark In/Out, Mark Clip, Mark Selection, Go to In/Out, Clear | ✅ / **new** Shift+/ / **new** / / **new** Shift+I / Shift+O / ✅ | ✅ |
| Add Marker, Go to Next/Previous, Edit Marker (name, duration, type, color), Clear markers | ✅ / ✅ / **new** double-click marker or Markers panel / ✅ | ✅ |
| Markers panel | **new** Markers tab (list, jump, edit, export chapters/CSV) | ✅ |
| Ripple Sequence Markers | ✅ | ✅ |

## Graphics and Titles
| Premiere | Filmocity | |
|---|---|---|
| New Layer › Text, Rectangle, Ellipse; Vertical Text, Polygon | ✅ all (**new** vertical text) | ✅ |
| Align and Distribute | Align + **new** Distribute (H/V) | ✅ |
| Arrange (bring forward/send backward) | **new** layer ↑/↓ and delete in Effect Controls | ✅ |
| Export as Motion Graphics Template | Save Graphic as Template (JSON); 12 bundled | ✅ |
| Fonts | system fonts (fontconfig) | ✅ |

## View
| Premiere | Filmocity | |
|---|---|---|
| Playback / Paused Resolution | proxy toggle · exact frame | ✅ |
| Show Rulers / Show Guides / Safe Margins / Transparency Grid | ✅ rulers · ✅ preset + **new** draggable user guides with snapping · ✅ · ✅ | ✅ |
| Display Mode › Composite / Alpha / Multi-Camera | ✅ / **new** Alpha / ✅ | ✅ |
| Zoom (monitor) | ✅ Fit/25/50/100/200 | ✅ |

## Window / panels
| Premiere | Filmocity | |
|---|---|---|
| Workspaces, Maximize Frame, floating panels | ✅ (5 workspaces, tab moves, `, **new** undock/dock) | ✅ |
| Audio Clip Mixer / Audio Track Mixer / Audio Meters | ✅ / ✅ (buses + master) / **new** dedicated Meters panel (peak hold) | ✅ |
| Captions, Text (transcript) | ✅ | ✅ |
| Effect Controls / Effects / Essential Graphics / Essential Sound / Lumetri Color / Lumetri Scopes | ✅ / ✅ (98 items incl. **new** HSL Secondary) / Graphics / Audio / Color / Scopes (parade, histogram, **new** vectorscope) | ✅ |
| Events / History / Info / Markers / Media Browser / Metadata / Project / Timecode / Timelines / Tools / Monitors | **new** Events · ✅ · ✅ · **new** Markers · ✅ · ✗ · ✅ · **new** Timecode · ✅ · ✅ · ✅ | ◐ |
| Learn / Libraries / Extensions / Reference Monitor | — / — / — / **new** ✅ | — |

## Timeline behaviours
| Premiere | Filmocity | |
|---|---|---|
| Opacity and volume rubber bands with pen keyframes | **new** opacity band on video clips + ✅ gain band | ✅ |
| Drag video-only / audio-only from Source Monitor | **new** drag handles | ✅ |
| Source patching, targeting, sync lock, lock, output toggles | ✅ | ✅ |
| Dynamic trimming (JKL in trim mode) | **new** ✅ | ✅ |
| Alt-drag duplicate, auto-scroll, snapping, markers, gaps | ✅ | ✅ |

## Effects
| Premiere | Filmocity | |
|---|---|---|
| ~150 video effects | 44 catalogued (incl. **new** Track Matte Key) + Motion/opacity/blend/crop/blur/vignette/key/mask/color; Effect presets **new** | ◐ |
| ~40 transitions | 25 | ◐ |
| ~40 audio effects | 30 + track/master buses | ◐ |
| Warp Stabilizer / Morph Cut / Auto Reframe / Remix | ✅ / ✗ / ◐ / **new** ✅ (bar-aligned retarget) | ◐ |

## Still open (ordered by editorial value)
1. OMF/AAF. 2. GPU-accelerated effects and playback for very long timelines. 3. Morph Cut (ML). 4. Timecode modification beyond start TC.

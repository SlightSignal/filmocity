# Filmocity on Windows

Windows x64 is the current release target. **0.47.0-rc.1** completed scoped local RC6 native acceptance on October 6, 2026. See [release readiness](RELEASE_READINESS.md) for the exact tested hashes, automated/native results and limits. Earlier 0.46.4 and development handoffs retain their historical scopes. Linux/macOS source portability alone is not supported native-release evidence.

Use the [Windows test handoff](WINDOWS-TEST-HANDOFF.md) as a historical case/procedure reference and the [candidate build guide](../packaging/README.md) for the current environment and a fresh executable with captured inputs. The present tools-only snapshot updates public docs/guards without changing the qualified runtime; rebuilding it still requires new artifact-bound acceptance.

## Guided editing

Use the new **Workflow** toolbar button for footage → story → sound → captions → versions → delivery. The [workflow guide](GUIDED-WORKFLOW.md) explains its editable results, optional speech setup and current limits. Native acceptance cases are in the handoff.

## Source setup

Install **standard, GIL-enabled CPython 3.13.16 Windows x64** for the current RC target. Guards require the 3.13 series at patch 16 or newer, and future patches need fresh qualification. Supply reviewed FFmpeg and ffprobe in `bin/` or on PATH with their notices/source information, then run `Install Filmocity.bat` and `Filmocity.bat` in a fresh environment. Bootstrap installs the hash-locked Python dependencies; it does not download rolling media executables. A native executable build additionally needs the build lock and Windows WebView2. Use the isolated build procedure for acceptance; a browser source launch does not test the native wrapper.

The official interpreter hash/signature and new 34-package offline installation/`pip check` have been checked locally. Four native-wheel archives changed to cp313 while versions remain pinned. Keep earlier Python 3.12 environments/build receipts as historical evidence. Python 3.13.16 is the final regular binary-installer maintenance release; plan a qualified 3.14 migration or controlled source build for subsequent security updates. See [release readiness](RELEASE_READINESS.md).

Data defaults to `%USERPROFILE%\filmocity_data`. Use `--data` to select an explicit folder, especially for test candidates. Keep the earlier release closed when working with a library that it could also open.

## Encoder choices and export checks

The Export encoder list follows the selected format: H.264 or HEVC. Software x264/x265 and NVIDIA NVENC, Intel QSV and AMD AMF profiles are recognized when listed by the selected FFmpeg build. A saved unavailable or mismatched encoder remains visibly unavailable until the user chooses a valid one. Image, audio and fixed-codec formats do not use this selector.

Listing an encoder does not prove usable hardware. Export preflight performs a small encode using the delivery profile, inspects codec/dimensions/frame count and decodes sampled pixels. A failed check blocks that export with a useful error; there is no silent software fallback. Successful results are reused for up to five minutes for the same tool identity/settings; failures for fifteen seconds. Refreshing the catalog does not force a fresh device encode. The standalone `packaging/check_encoders.py` records fresh probes against explicit tool paths.

This first probe covers four 320×180 SDR frames. It does not establish audio, full-project/effect compatibility, 4K/HDR, throughput or sustained GPU stability. Hardware profiles use the selected bitrate, or 10 Mbps when a CRF quality option is selected; the UI explains this. Both software encoders must actually be present in the chosen FFmpeg distribution. Existing preview/proxy/internal render paths have their own checks and do not all run this delivery probe.

Export waits for queued saves and binds checks/jobs to the captured saved project. Changes during a slow check invalidate it. Failed status reads retry observation of the same submitted job. A job already queued retains its original project and audit ownership after switching.

## Independent export outputs

Each queued job now writes into its own `renders/job-<id>/` folder. Repeating a name preserves earlier files and command logs. Windows reserved device names receive a safe prefix; invalid format/container extensions are rejected. Use the returned job output links instead of constructing `renders/<name>.mp4` paths. Long-path and sharing regressions passed for their captured cases; different filesystems, paths and workloads still need qualification.

Open **Review** beside a completed video to review that exact export. Notes belong to its original project; open that project before adding them. Notes support undo/redo. In–Out notes use the captured sequence offset, but later timeline edits do not rebase existing comments.

New exports save history in their job folders. After reopening the same data folder, completed jobs retain their output/review links; jobs interrupted while queued or rendering require a new export. Corrupt records and unavailable outputs are reported without deleting files. History-write failures are visible. Startup compares output size and modification time (PNG manifest and first frame only), not full media contents. Older outputs without records and libraries moved to another path are not automatically adopted. Previous rendered previews do not reactivate playback after restart. The finite RC6 recovery/warm-reopen/shutdown observations are in release readiness; broader termination and filesystem cases remain in the historical testing handoff.

## Data-folder ownership

The backend holds an exclusive operating-system lock on the selected data folder. A second cooperating process must refuse to open it; multiple browser clients may still connect to the same backend. Use separate test folders for independent instances.

Leave `.filmocity.lock` in place: its presence is not proof that an owner is running. The OS releases the lock when its process exits. Deleting the marker is not a recovery step. Older releases do not honor this guard. Same-library native refusal passed in RC6. Removable-drive and additional path-alias behavior remain separate qualification scenarios.

## Native scope and further qualification

Test real media and filter resources in paths with spaces, Unicode and apostrophes; playback and audio; save/reopen/recovery; complete exports and cancellation; native Save As and keyboard focus; and shutdown of the app's backend/encoder processes. Check that the packaged app uses its captured media tools even when other FFmpeg installations are on PATH. Keep diagnostic commands and artifacts with the exact candidate receipt.

Bundled fonts reduce variation, but font rendering, system-font lookup, browser decoding and GPU availability still require Windows evidence. For failed renders, retain the job's FFmpeg command/error and inspect the output rather than inferring success from a completed UI progress bar.

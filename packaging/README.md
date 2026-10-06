# Build and verify a Filmocity Windows candidate

The exact **0.47.0-rc.1** RC6 executable completed scoped local Windows acceptance; see [release readiness](../docs/RELEASE_READINESS.md) for its hashes and results. This tools-only source preparation preserves its runtime and updates public documentation/repository guards. Every new packaging run still produces a **built, unverified** candidate; it requires its own acceptance.

The [Windows testing handoff](../docs/WINDOWS-TEST-HANDOFF.md) provides transfer verification, commands, encoder checks and the native acceptance case table for the testing LLM.

## Prepare the Windows build environment

Use Windows x64 with **standard, GIL-enabled CPython 3.13.16** for the pinned RC environment, rather than the free-threaded variant. Launch/build guards require the 3.13 series at patch 16 or newer; a later patch still requires fresh qualification. Create a new isolated environment and retain the older Python 3.12 environments/receipts as historical evidence. Install the bootstrap lock first and the declared build/test lock. Supply both FFmpeg and ffprobe manually from the reviewed Windows x64 distribution you intend to use. Neither the build command nor source bootstrap downloads rolling FFmpeg executables. Retain the distribution's licenses and corresponding-source information; the current bundled binary still has the publication limitation in [release readiness](../docs/RELEASE_READINESS.md).

```powershell
py -3.13 -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install --require-hashes -r requirements-bootstrap-windows.lock
.\.venv-build\Scripts\python.exe -m pip install --require-hashes --no-build-isolation -r requirements-build-windows.lock
.\.venv-build\Scripts\python.exe packaging\build.py --check --ffmpeg-dir C:\FilmocityTools\bin
```

`--ffmpeg-dir` defaults to this checkout's `bin`. Both `ffmpeg.exe` and `ffprobe.exe` must be present, have x64 PE headers, and execute successfully with `-version`. Adjacent DLLs and supplied license/notices/text resources are captured with the tools. Retain the appropriate upstream distribution notices and source information before distributing a binary, as required by the existing handoff instructions.

`--check` prints JSON and exits 2 when prerequisites are missing. It checks the host, exact reviewed build-lock package versions, required runtime files, referenced static entry assets, Python syntax and tool identity. It does not create an output folder, install dependencies, open the app or prove runtime compatibility. The four Windows lock files and component inventory describe the pinned environment; each candidate also records the installed versions. A clean offline dependency installation has been checked locally, while a clean-machine native qualification remains pending.

For source editing, `Install Filmocity.bat` selects the standard Python 3.13 series, requires patch 16 or newer/x64, and installs bootstrap then runtime locks into `.venv`; supply the two tools in `bin/` or on PATH first. The RC's measured interpreter is 3.13.16. Optional source-only `--with-whisper` installs extra packages outside the reviewed locks. For the complete focused Python suite, install `requirements-test-windows.lock` after bootstrap with `--require-hashes --no-build-isolation`, rather than assuming the build-only lock contains test dependencies.

The clean offline installation and `pip check` passed for 34 packages. Package versions remain pinned; cffi, Pillow, pydantic-core and websockets use reviewed standard cp313 Windows x64 wheels. Actual runtime measurements report GIL enabled, Expat 2.8.5 and OpenSSL 3.5.9. Captured-source and native RC6 results are recorded separately in [release readiness](../docs/RELEASE_READINESS.md); environment checks alone do not qualify another build. Python 3.13.16 is the last full-maintenance release with regular binary installers; later 3.13 security releases are source-only. Plan 3.14 qualification or a controlled source build for future updates.

The recipe uses PyInstaller's [documented data, hidden-import, UTF-8 and output options](https://pyinstaller.org/en/stable/usage.html). Backend files remain data so their `__file__` locations resolve the bundled frontend/assets/docs. Their standard-library imports are explicitly collected. Third-party runtime packages are collected with the established recipe. PyInstaller [builds for its current operating system](https://pyinstaller.org/en/stable/operating-mode.html); this command rejects Linux/macOS for this Windows target. See also [pywebview's Windows installation requirements](https://pywebview.flowrl.com/guide/installation.html#windows).

## Build without replacing a prior executable

```powershell
.\.venv-build\Scripts\python.exe packaging\build.py --ffmpeg-dir C:\FilmocityTools\bin
```

Output goes to a new `dist\candidates\<UTC-time>-<random-id>` directory. `--output NEW_DIRECTORY` selects another fresh location; an existing directory or a location inside source inputs is rejected. The old `dist\Filmocity.exe`, working source and user project libraries are never overwritten by this recipe.

Each candidate contains:

- `inputs/`: verified copies of selected source, tests, the exact benchmark allowlist in `packaging/release-inputs.json`, build recipe, tool binaries and supplied binary resources. Historical benchmark runs, hidden files, bytecode, local install records/logs, old build specs, environments and project libraries are excluded. Changes during copying fail the build; subsequent working-tree edits cannot change these compiler inputs. Preserve the exact dependency locks, component inventory and license/notices alongside the build.
- `artifact/Filmocity.exe`: the output, checked for an x64 PE header and hashed after successful compilation.
- `build.json`: candidate ID, source/input hashes and sizes, tool hashes/version responses, host/interpreter/package versions, compiler arguments, selected build environment, status and final artifact hash. The embedded `build-info.json` carries the same candidate ID/source hash.
- `build.log`, `spec/`, `work/`, `cache/`, `pycache/`: evidence and isolated compiler state. Failed builds retain their receipt and log; start a new directory for another attempt.

On Windows, capture, hashing and copying use extended filesystem paths and explicit directory enumeration. A deep or inaccessible directory cannot silently disappear from the manifest. Compiler arguments use ordinary drive/UNC paths because PyInstaller's `SOURCE:DEST` parser rejects prefixed drive colons. Both representations address the same captured files; no external path adapter is needed.

The source hash covers the captured source/test/documentation inputs; tool hashes are separate. This is provenance for a specific artifact, not a claim of bit-for-bit reproducible compilation. Staged inputs, tools, supplied resources and embedded identity are checked after the compiler exits. Successful receipts always say `built_unverified` and `native_verification: not_run`.

## Acceptance against the exact artifact

Keep the receipt beside the candidate. Verify `Get-FileHash -Algorithm SHA256 <candidate>\artifact\Filmocity.exe` against `artifact.sha256` in `build.json` before recording results. After launch, check that `/api/version` reports `build.id` and `build.source_sha256` matching the receipt. Run source regressions from the captured `inputs` tree using the test-lock environment; add its `bin` to PATH and use Node.js 22 or newer for `node tests/run_frontend.cjs`. `python tests/run_checks.py` runs the complete focused set with private temporary libraries and ASGI/loopback server fixtures; it must not target the production library. The broader `python tests/run_all.py` also runs legacy integration/browser suites with historical fixed ports/data paths; inspect those before running them on a workstation with a live editor. Report missing prerequisites, failures and skips separately.

Launch with an explicitly isolated, disposable data folder:

```powershell
& '<candidate>\artifact\Filmocity.exe' --data 'C:\Filmocity QA\Émile test data'
```

Record the artifact hash, Windows version, Python/package receipt, WebView2 version, launch method, selected data location and observed results. The following is a broader qualification procedure; the finite completed RC6 scope is recorded separately and does not mark every scenario below passed:

1. Launch from Explorer as well as a shell. A shell can hide GUI-subsystem stdout/stderr failures. Confirm a native window; browser fallback does not establish WebView2 acceptance. Test an occupied preferred/pinned port and a second instance pointing at another isolated data folder. Launch a second candidate against the first candidate's folder and verify an actionable refusal without migration or project changes; after the owner exits or is force-terminated, verify reopening. Repeat on NTFS and the intended removable-drive filesystem, including case/junction aliases.
2. Import real video, audio and still media from paths with spaces, Unicode and apostrophes. Edit, trim and reorder clips; inspect actual playback, audio and scrubbing.
3. Save, close and reopen that same test library. Check clip positions, captions and review notes; perform a new edit and undo/redo. Exercise Recovery and proposal frame review without touching the production library.
4. Export real video/audio and numbered frames. Decode/check the output, then exercise Cancel and a subsequent successful job. Scope codec/effect/font findings to the tested cases.
5. Complete a native Save As download, inspect the saved bytes, dismiss the dialog and return focus to the editor. HTTP retrieval alone is not this check.
6. Close the native window and confirm the application's backend, listener and owned encoder processes exit. Also test cancellation/closure during a job and forced process termination followed by Recovery. Source supervisor fixtures are not native shutdown evidence.

`GET /api/version` adds `instance`, `pid`, `workspace` and `build` to the existing fields. `workspace` is the same hashed data-directory identity as project context; `build` is null for ordinary source runs. Both launchers wait for their fresh nonce, expected process and selected workspace. Another service on that port cannot satisfy this readiness check just by returning a Filmocity version. The native wrapper allows five seconds for requests and WebSocket close handshakes, then Uvicorn cancels pending requests and runs application cleanup. It joins the backend for up to twenty seconds; a failure logs the owned thread stack and remains a failure. It never uses `force_exit` to skip lifespan hooks. A failed native window cannot leave a hidden browser fallback server. The source Windows launcher uses a private console process group for a graceful Ctrl+Break request, then waits before escalating against its own child handle. Both Windows entry points retain job-object ownership of encoders. Backend startup separately holds an exclusive OS lock on the chosen data folder through process teardown. A second cooperating backend cannot open the same library. Leave the permanent `.filmocity.lock` marker in place; marker presence is not ownership. Older releases do not implement this guard. These mechanisms still require the live checks above.

Acceptance belongs to the exact frozen artifact and its recorded native checks. Source regressions and a prior build cannot establish acceptance for a new candidate. See [current release readiness](../docs/RELEASE_READINESS.md) for the exact accepted scope and [historical checkpoints](../docs/MODERNIZATION.md) for prior inputs.

For rendered-preview acceptance on the same candidate: queue a preview while saves are pending; render both a whole sequence and In–Out with a track matte and audio; confirm captured range, audio, forward speed and the live fallback outside range/reverse. Cancel queued, running and just-completed previews. Edit or switch between projects sharing a sequence ID while submission, polling and validation are delayed. Interrupt the connection, then verify one job per request identity and recovery of status. Replace a source file and try enabling an old preview; restart and verify old filenames are not adopted automatically. Exercise the status/Cancel controls with keyboard and screen reader, and check WebView2 media cleanup and clean shutdown. RC6 native acceptance established whole-sequence preview adoption and paused pictured stepping at 24 and 60 fps. The broader timing, interruption, delayed project-switch, range/matte and accessibility scenarios in this paragraph remain separate qualification work; controlled source tests are not native passes.

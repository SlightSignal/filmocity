# Filmocity

Filmocity is a local video editor with an editable project format and a Python agent API. Human edits and agent operations share the timeline, saved history and review workflow. Pictocity remains the companion image editor.

**Release status:** **0.47.0-rc.1** completed scoped local Windows acceptance on October 6, 2026. The unchanged runtime was qualified at commit `8fcffd6c`; this tools-only source preparation updates documentation and repository guards. See [release readiness](docs/RELEASE_READINESS.md) for exact tested hashes, automated results, native scope and remaining limits.

## Editing and delivery

Filmocity provides multi-track editing, source marks, captions and titles, audio controls, color/effects, reviewed workflow proposals, project recovery and export through FFmpeg. The [guided workflow](docs/GUIDED-WORKFLOW.md), [getting started guide](docs/GETTING-STARTED.md) and [user guide](docs/USER_GUIDE.html) describe the interface. A listed feature is not a claim that every media, effect or hardware combination is qualified.

Keep original media, graphics, LUTs and fonts available for export. A proxy is a preview resource, not a substitute for a missing original. Watch the actual final export and inspect its reported output before delivery.

## Windows and source use

Windows x64 is the current native target. A packaged build runs through its native window; source use runs a local server and browser. Source entry points are `Install Filmocity.bat` / `Filmocity.bat` on Windows and `install.sh` / `filmocity.sh` on Unix. The RC targets standard, GIL-enabled CPython **3.13.16 x64**; Windows entrypoints select the 3.13 series and reject older patches and free-threaded builds. Setup installs hash-locked Python dependencies into `.venv`. Use a fresh environment for the migration and preserve prior environments as historical evidence. Supply both FFmpeg and ffprobe from a reviewed distribution in `bin/` or on PATH before setup; retain that distribution's licenses and corresponding-source information. Setup does not download rolling FFmpeg executables. Linux/macOS source launchers are experimental and do not use the reviewed Windows locks.

Select an intended library with `--data` or `FILMOCITY_ROOT`, and keep it separate from test data. Discover the actual backend and check `/api/version` for app, version, process, instance, workspace and packaged identity. Do not assume that an answering server on port 8787 is the intended editor. A second cooperating backend refuses the same library. Leave its `.filmocity.lock` marker in place; marker presence is not live ownership.

The current pinned environment uses standard CPython 3.13.16 and the same 34 package versions, with four native-wheel selections changed from cp312 to cp313. Its four `requirements-*-windows.lock` files cover bootstrap, runtime, build and tests; install bootstrap first with `--require-hashes`, then the appropriate lock with `--require-hashes --no-build-isolation`. The guards allow newer standard 3.13 patches, but those require their own environment/build qualification. The minimums in `requirements*.txt` alone do not reproduce this environment. Optional source-only `--with-whisper` installation is outside these locks and the reviewed release environment. Build instructions are in [packaging/README.md](packaging/README.md). Each build captures selected source, fixtures and tools into a fresh candidate. A successful compiler receipt says `built_unverified`; acceptance must be recorded against that exact artifact.

The signed official 3.13.16 installer, fresh offline locked installation, captured-source tests and exact RC6 native candidate were checked locally. Python 3.13.16 is the final full-maintenance release with regular binary installers. Future security updates require a planned 3.14 qualification or controlled source build; see [release readiness](docs/RELEASE_READINESS.md). Earlier Python 3.12 checks remain historical.

The local request guard checks HTTP and WebSocket origin/authority and protects private media with any configured token. Keep loopback as the default; see [security scope](docs/SECURITY.md) before exposing a LAN listener. These checks do not establish an Internet service or browser security qualification.

## Tests and agent API

The complete focused runner is:

```text
python tests/run_checks.py
```

It requires the declared Python environment, Node.js 22+, Pillow and FFmpeg/ffprobe. Use private `TEMP`/`TMP` and an isolated data root. Missing prerequisites, failures and skipped platform checks are separate outcomes. The broader `tests/run_all.py` also includes legacy integration/browser cases; inspect their fixed ports and data paths before running them beside a live editor. Frontend fixtures do not replace native WebView2 testing.

[release-inputs.json](packaging/release-inputs.json) names the small benchmark fixture set needed by the tests. The full historical media, logs and copied source evidence remain preserved outside the release Git tree. See [benchmarks/README.md](benchmarks/README.md).

The dependency-free SDK is [agent/filmocity_client.py](agent/filmocity_client.py). Read the [API contract](docs/AGENT_API.md) and [agent playbook](docs/PLAYBOOK.md). Capture saved ownership for Review/Apply operations, retain exact request identity and reconcile uncertain replies before retrying. A `RenderCommand` owns temporary resources: preserve its `cwd` and keep it alive until every consumer exits.

## Release scope

The qualified RC6 scope includes cold/warm launch, library ownership, save/open and undo/redo, native downloads, API and GUI exports, cancellation/recovery/shutdown, and numbered paused pictures in Program, Source and rendered previews at 24 and 60 fps. The wider native matrix, clean-machine setup, signing, accessibility, all codecs/drivers/effects, HDR and long-project performance remain separate qualification work. Detailed historical development records are in [CHANGELOG.md](CHANGELOG.md); their counts and media apply to their original hashed inputs.

There is no full Adobe parity claim. Automated checks are not a watched-motion or listening verdict, and installation is not authority to publish a project or release.

## License and contribution preparation

Filmocity's original source is under [MIT](LICENSE). Bundled DejaVu fonts and any packaged Python/runtime/FFmpeg components retain their separate licenses. Retain `THIRD_PARTY_NOTICES.md` and the applicable `licenses/` texts. Publishing the current bundled FFmpeg binary is blocked until its full matching source provenance is established; source-only preparation excludes `bin/`. See [release readiness](docs/RELEASE_READINESS.md).

The verified source repository is [SlightSignal/filmocity](https://github.com/SlightSignal/filmocity). The owner has authorized the current tools-only source update on a review branch preserving that repository's existing history. Use [the GitHub handoff guide](docs/GITHUB-HANDOFF.md) for selection and review; exclude client projects/media, local receipts, executable companions and dependency stores. Source publication does not clear public binary redistribution.
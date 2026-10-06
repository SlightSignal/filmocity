# Filmocity release readiness

**0.47.0-rc.1 completed scoped local Windows acceptance on October 6, 2026.** This record describes the exact RC6 runtime and artifact below. The present tools-only source snapshot preserves that runtime and updates public documentation/repository guards; its source-file hash therefore differs from the tested capture. These edits do not qualify a newly compiled executable.

## Exact qualified candidate

| Item | Bound value |
| --- | --- |
| Version | `0.47.0-rc.1` |
| Tested Git commit | `8fcffd6c1a50985056945c9de1ab6e04652db9a3` |
| Candidate ID | `313d8aeaa3c14b24a8c0f6f72699c292` |
| Executable SHA-256 | `58b925d7d6fe219c4c44e4279f213cffff922bc82c470972928940f99e2e07f1` |
| Captured source SHA-256 | `1ca74360d37038e7e69e59519c9d2bc3fde9d94302f08aed1db8d8acb5f3a719` |
| Environment | Windows 11 Pro 25H2, build `26200.9457`; native WebView2; standard GIL-enabled CPython `3.13.16` x64; Node.js `24.20.0` |
| Verdict | Locally verified release candidate within the finite scope below |

The local final-verification record binds source/build identity, automated and native receipts, preservation, and process retirement. Those raw machine receipts, screenshots and private QA libraries remain outside the public source. RC6 was subsequently installed locally, and the unchanged desktop shortcut startup was verified against the exact artifact identity and preserved saved-project bytes. The previous Filmocity installation is retained in a separately verified rollback. That installed-startup check used background native observation; it did not capture a foreground screenshot for that session. The application was left running for the owner. The earlier local qualification did not push GitHub; the owner has now authorized a tools-only source update to the verified [SlightSignal/filmocity repository](https://github.com/SlightSignal/filmocity). Actual publication status is recorded separately. Earlier 0.46.4, M1t, development builds and failed RC attempts remain historical evidence with their original scopes.

## Captured automated results

- **133 Python suites:** 1,962 unittest executions, comprising **1,953 passes and nine explicit Windows/platform/privilege skips**, with no failures. The route-contract script has a separate custom result; it is not another invented unittest count.
- **78 frontend suites:** **1,231 Node test methods plus seven custom transport checks**, or 1,238 reported checks, with no failures.
- **45 frontend syntax checks** passed.

The complete run used verified captured inputs and private temporary/data directories. Input readback and observed owned-process retirement completed. The strict runner's nonzero skip status was retained; the nine skips remain unqualified. Counts measure these specific regressions, not feature coverage or native case totals.

## Completed native scope

- Cold/shell launch, exact packaged runtime/tool identity, duplicate-library refusal, and ordinary warm reopen on the recorded workstation.
- Program live/rendered and Source numbered pictures with matching clocks at **0 → 1 → 2 → 1 → 0**, at both **24 and 60 fps**. Playback advanced; keyboard Stop paused before the clip end. Source In/Out checks established ephemeral monitor state, not persisted marks after restart.
- Native project Save As/Open original, saved content/history, API edit/Undo/Redo, and frame Save As cancellation/retry with Unicode filenames.
- API-queued H.264/AAC video, stereo PCM WAV and complete **96-frame PNG publication**, with independent decoding, frame/rate/dimension and audio-frequency checks. One actual GUI Quick Export submission produced H.264/AAC with 96 distinct frames and measured **−14.0 LUFS** normalization.
- Cancellation of a real finite export, owned-encoder retirement and incomplete-output cleanup, followed by a successful 12-frame export. Normal close during active export, recovery with the interrupted job retained as a terminal error, and warm reopen preserved the recorded saved state without automatic requeue in the observed startup samples.
- At completion of the isolated acceptance sessions, final checks found their recorded candidate/duplicate process identities retired and five test ports released. This is not a claim that the subsequently installed owner session is closed. Production libraries, Pictocity, backups and original transfer archives were preserved; the prior Filmocity install was retained for rollback before the verified RC6 replacement.

These are finite synthetic-media and workstation observations. They do not establish full watchback/listening quality, every media/effect interaction, or all rows of the historical 499-case matrix.

## Tools-only public source

Include application/SDK code, product assets, fonts/LUTs, documentation, launch/build recipes, Windows locks, focused tests and attribution. Retain the exact twelve portable benchmark entries in [release-inputs.json](../packaging/release-inputs.json). The two WAV files are deterministic generated test signals; the documentation images show synthetic sample tours. They are not client media or project stores.

Exclude all client/user projects and media, libraries, renders, exports, training data, screenshots of private work, logs, ownership markers, credentials, machine installation records, environments, dependency stores, executable companions, Git bundles and raw local QA receipts. The complete historical benchmark archive stays local. Do not remove required notices or synthetic fixtures to achieve that boundary. Use the explicit selected-file manifest as well as ignore rules; an ignore file cannot detect arbitrary private files.

This source snapshot's custody is distinct from the destination repository's history. Apply reviewed files on a branch preserving the verified GitHub repository's existing history. Do not replace that history with a local bundle, orphan branch or force-push. See [the handoff guide](GITHUB-HANDOFF.md).

## Dependency and distribution status

The four `requirements-*-windows.lock` files pin bootstrap, runtime, build and test dependencies. Install bootstrap first with `--require-hashes`, then the needed lock with `--require-hashes --no-build-isolation`. The measured environment contains 34 pinned packages, including reviewed cp313 x64 wheels. Minimums in `requirements*.txt` alone do not reproduce it. Optional source-only transcription installation remains outside the locks and base qualification.

The official CPython 3.13.16 installer hash/signature, fresh offline installation and `pip check` were verified. Measured native components include Expat 2.8.5 and OpenSSL 3.5.9. Runtime/build guards accept newer standard 3.13 patches, but those need their own qualification. Python 3.13.16 is the last regular binary-installer maintenance release; future security patch adoption needs planned 3.14 qualification or a controlled source build. The dated per-version package-advisory review found no listed advisories; this finite result is not universal security clearance.

**Public redistribution of the currently bundled FFmpeg executable remains blocked.** The tested tool identifies as `N-126435-gf93cd72dde-20260906`, GPLv3-or-later; complete matching corresponding-source/build provenance and exact security-fix ancestry remain unresolved. The public source excludes `bin/`. Supply independently reviewed FFmpeg/ffprobe for source use and retain their notices/source information. Generated notices and successful output tests do not clear binary redistribution. Retain [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md), [LICENSE](../LICENSE) and the applicable `licenses/` texts.

## Remaining qualification limits

Clean-machine installation, signing, different operating systems/devices, accessibility, sustained long projects, VFR, all codecs/effects/fonts, HDR/color workflows and the wider native matrix remain separate work. The exact executable is unsigned; its successful local runs do not establish compatibility with Smart App Control enforcement. Do not change security settings to infer acceptance.

No full Adobe parity or exhaustive security audit is claimed. Automated checks, decoded synthetic exports, user taste approval and permission to publish creative work are distinct. A new runtime/tool/dependency build requires new artifact-bound acceptance; public documentation updates do not transfer old receipts to new bytes.

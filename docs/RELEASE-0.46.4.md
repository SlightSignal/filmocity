# Filmocity 0.46.4 — historical reviewed release

This dated record preserves its original version and checks. Current **0.47.0-rc.1** scope and tools-only publication limits are in [release readiness](RELEASE_READINESS.md); the binary companion described below is historical, not part of the current public source.

This handoff contains the reviewed runtime source for the named version. Documentation and Git hygiene were adjusted for portability; the application code is unchanged. Source archives contain no executable, dependency tree, project library, credentials or Git history.

Windows binaries are companions in the outer `windows/` folder. They remained outside the source repository. Current public binary redistribution requires the provenance and license work described in release readiness; do not publish these historical companions as the new RC. The prepared source has not been pushed or published. No dependencies were installed for this handoff. Source installation/build commands below can download dependencies and should be run deliberately on the target machine.

The October 3, 2026 local release checks exercised source, actual packaged APIs, native workflows and one Windows save for each app. They do not establish every advertised feature, cross-platform behavior, clean-machine setup, creative taste or Adobe parity. Historical reliability documentation retains its implementation details; this release note controls the current verification scope.

## Behavior shipped

- Render operations own generated captions, shapes, masks, command files, chapters and nested renders until their encoder/readers retire. Nested work shares cancellation and progress supervision.
- Command inspection retains explicit helper scopes. At most eight live/building scopes are admitted; callers release a scope after all external consumers exit.
- PNG sequences publish checked frames and a JSON manifest together. Handled failure/cancellation preserves the previous sequence.
- An audit-file failure is reported separately from successful output. The queue still retires its holder/task and continues with the next job.
- The native wrapper enables Windows Save As downloads.

## Named checks

The reviewed source passed 124 focused Python regressions with no skips and route guards; the existing catalog exercised 122 render cases. A separate baseline comparison matched all 145 decoded RGB frames and six decoded PCM streams across seven selected cases, plus chapters and original project JSON. The exact packaged executable passed 51 API checks, including real FFmpeg PNG output, command-scope capacity/release and queue recovery. The installed native frontend completed 27 verified workflow actions, two 12-frame jobs and an actual CSV save with two checked rows.

These scopes overlap; the counts are not a claim of distinct total feature coverage. Audio byte comparison is not listening, and frame comparison is not normal-speed playback review.

## Open limits

Original media, fonts, LUTs and other render inputs still use live bytes. Immutable source capture is a separate, interrupted candidate and is not shipped. Jobs/scopes are not durable across restart; cleanup and retained backup recovery can require manual action. Publication is not a power-loss transaction. The wider fractional-rate, color/HDR, nested/matte, codec, audio and long-project performance matrices remain open. Native download-popup dismissal and return to the root window remain unverified after the successful save.

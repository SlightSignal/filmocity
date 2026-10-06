# Filmocity tools-only GitHub handoff

The unchanged **0.47.0-rc.1** runtime completed scoped RC6 Windows acceptance. [Release readiness](RELEASE_READINESS.md) records the tested commit/artifact, measured results and remaining limits. This public source snapshot updates documentation and repository guards while preserving that runtime; it is not a rebuilt or newly qualified executable. Historical source handoffs and local verification receipts remain separate.

## Select public source

Include application/SDK code, original product assets, fonts/LUTs and their notices, public documentation, launch scripts, focused tests, build recipes, hash-locked Windows dependencies, component notices and the exact twelve [benchmark inputs](../packaging/release-inputs.json). Preserve `.gitignore`, `.gitattributes` and reviewed `.github` workflows. Synthetic test projects, deterministic WAV fixtures and sample-tour screenshots are required product/test examples, not client work.

Exclude all client/user media and projects, libraries, renders, exports, snapshots, training records, private screenshots, credentials, logs, ownership markers, machine installation records, environments, dependency stores, `bin/`, executable/build output, the old `handoff-manifest.json` and benchmark files outside the allowlist. Also exclude local QA receipts, evidence companions and Git bundles. Keep historical evidence frozen locally rather than deleting it.

Inspect the exact selected-file manifest and staged diff before committing. Ignore patterns provide a guard, not a privacy verdict. Never copy `.git/config`, credentials, reflogs or object stores as source payload; normal Git history is handled through the intended repository. The public source must not contain private QA libraries or raw workstation receipts.

## Preserve the destination repository's history

The verified destination is [SlightSignal/filmocity](https://github.com/SlightSignal/filmocity), and the owner has authorized this tools-only source update. Source snapshot preparation and repository integration remain different operations. The reviewed files have been applied on a branch based on the repository's existing main history. Review the resulting diff and any conflicts against that source, preserving useful history and repository-specific configuration.

Do not substitute an unrelated local history bundle, create an orphan replacement, or force-push to overwrite the destination. A local source ZIP is a file snapshot, not authority to replace remote history. Use an ordinary reviewed commit/pull request for the authorized source update. Actual publication is recorded separately; this documentation edit does not claim that a push has completed.

## Rebuild and verify deliberately

Use **standard, GIL-enabled CPython 3.13.16 Windows x64** and the four Windows lock files in a fresh environment. Install bootstrap first with `--require-hashes`, then the needed runtime/build/test lock with `--require-hashes --no-build-isolation`. Newer permitted 3.13 patches still need separate qualification. Optional source-only transcription is outside the reviewed base locks.

Supply reviewed FFmpeg/ffprobe separately with their licenses and corresponding-source information. [The build guide](../packaging/README.md) captures selected inputs into a fresh candidate and records artifact identity. Build completion says `built_unverified`; it is not native acceptance. Run source checks from captured inputs with isolated temporary/data directories, retain raw failures/skips, then bind native observations to the exact executable and packaged identity. The reviewed RC6 runtime results do not automatically qualify a rebuild after documentation, tools or dependency changes.

## Source-only distribution

Do not add the local review executable or binary/evidence companions to this public source. The tested bundled FFmpeg is GPLv3-or-later and its complete matching corresponding-source/build provenance is unresolved, so public binary redistribution remains blocked. MIT coverage of Filmocity's original code does not replace third-party license obligations. Keep `THIRD_PARTY_NOTICES.md`, `licenses/` and the font notice; binary publication requires its own provenance and acceptance work.

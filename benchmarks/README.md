# Retained benchmark fixtures

The release source keeps the exact twelve entries in [packaging/release-inputs.json](../packaging/release-inputs.json). They are a small portable harness/fixture set, not the complete historical benchmark archive.

| Input | Purpose |
| --- | --- |
| `timeline-harness.cjs`, `timeline.cjs` | Controlled Node timeline construction harness and CLI |
| `baselines/timeline-before.js`, `baselines/timeline-before.json` | Frozen pre-optimization implementation and original provenance receipt |
| `baselines/chunk-sequence-before.py` | Prior sequence slicing semantics for comparison |
| `baselines/editing-workflow-before.py`, `story-editing/measure.py` | Prior transcript-cut semantics and controlled fixture/helper |
| `timeline-core/legacy-snap.txt`, `timeline-core/legacy-drag.txt` | Legacy timeline move reference functions |
| `source-replacement-sync/reviews/m1k-audio-audit/media/quiet_then_loud.wav`, `silence.wav` | Deterministically generated PCM input signals for audio window/silence tests |
| `README.md` | Selection and historical evidence scope |

Run the portable construction harness from the source root with Node.js 22 or newer:

```text
node benchmarks/timeline.cjs
node benchmarks/timeline.cjs benchmarks/baselines/timeline-before.js
node tests/test_timeline_performance.cjs
```

It measures controlled VM/DOM construction, not native browser layout, paint, media decode, GPU performance or creative quality. The focused suite additionally compares sequence slicing, transcript edits, source audio windows and legacy/current timeline behavior using the retained inputs. Test results apply to their actual source/tool hashes and recorded host.

The complete original benchmark tree remains frozen locally and in its verified source handoff. It contains generated media, project stores, copied source trees, failed investigations, superseded runs and hash-bound review evidence. Those files are excluded from the release Git/build selection and must not be deleted or retroactively updated. Historical development documentation refers to that archive; such references retain their historical scope. Current finite RC6 results are in [release readiness](../docs/RELEASE_READINESS.md); the archive itself is not public source.

Current release scope and open gates are documented in [RELEASE_READINESS.md](../docs/RELEASE_READINESS.md). Do not add old benchmark counts to a new release result or use renamed historical `current`/`final` files as proof of current runtime behavior.

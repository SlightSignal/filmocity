# Training data the editor produces (all local files under the data root)
- `events.jsonl` — one line per action: `{id, ts, type, actor:"human"|"agent", tool, reason, ops, befores, client}`. `befores` holds the prior value of every changed clip, so each event is a reversible before/after pair. This is the finest-grained signal: which tool a human reaches for, how far they trim, what they delete, how long they hold a shot.
- `snapshots/*.json` — full project states at labeled moments (`agent_proposal`, `human_final`, or any label).
- `training/edit_pairs.jsonl` — for each `human_final` following an `agent_proposal`: `{sequence, changes:[{clip, change:"removed"|"added"|"modified", fields:{k:{before,after}}}], summary:{n_changes, removed, added, modified, duration_before, duration_after}, agent_snapshot, human_snapshot, ts}`. This is the preference signal: the agent's cut vs the cut the human approved.
- `project.json → annotations[]` — human labels on clips or times ("too long", "wrong take", "great moment") with optional notes; join to clips by `clip_id`.
- `renders/*.cmd.txt` — the exact FFmpeg command for each render (reproducibility).

## Building a dataset
Join `edit_pairs` to the clip metadata (media, source in/out, timeline position) and to annotations; features: shot length before/after, position in sequence, whether a transition was added/removed, speed changes, title text changes; labels: kept / trimmed (by how much) / removed / reordered. Pair with the campaign's decision record and outcomes (`jobs/<job>/09_review`, `12_MEASUREMENT`) to connect editing decisions to performance over time. Keep everything scoped: a human's preferences for one brand are not a universal rule until repeated across brands (`13/04`).

## Added in v0.2
- `training/proposal_decisions.jsonl` — one line per human decision on an agent proposal item: `{ts, proposal, item, decision:"accept"|"reject", reason_agent, note, ops}`. This is the cleanest preference signal in the system: an explicit yes/no on a specific, reasoned edit.
- Events now include `tool` values from the full tool set (`select`, `ripple_trim_l/r`, `roll`, `slip`, `slide`, `razor`, `add_edit`, `ripple_trim`, `transition`, `keyframe`, `color`, `captions`, `mixer`, `speed`, `link`, `sequence_io`, `track`, `effect_controls`) so models can learn *how* editors work, not only the result.


## Added in v0.7
- `playback` events (`start`, `end`, `rate`, `sequence`) — the ranges a human actually watched; `/api/session/summary` folds them into a per-second review heat-map. Attention is a label too: what was replayed before a change is context for that change.
- `reasons[]` on proposal decisions and annotations — a fixed taxonomy (pacing, story, brand, legal/claims, audio, color, motion, typography, framing, platform fit, too long, too short, wrong take, great moment) so decisions are comparable across projects; free-text `note` stays alongside.
- `project.brief` — client, objective, platform, audience, constraints, notes; exported in the dataset `meta` record so every decision is conditioned on intent.
- `GET /api/training/export` — one JSONL file per export with `kind` in {meta, event, edit_pair, proposal_decision, annotation, snapshot_ref}; schema 2. Feed this forward: the recommended first model is a preference model over (brief, sequence state, proposed op) → accept/reject with reason, trained from proposal decisions and edit pairs.

## Added in v0.8
- Data is now **per project** (`projects/<id>/events.jsonl`, `snapshots/`, `training/`), so a dataset export is scoped to one client's work; every event also carries `project`.
- New human-tool labels worth learning from: `multicam_cut` / `multicam_switch` (angle choices with timing), `scene_detect`, `transcript_delete` (which words were cut), `group`, `fit`, `track_fx`, `master`.

# Training pipeline — from decisions to a model that scores the agent's next proposal

Filmocity records four kinds of signal (see `docs/TRAINING_DATA.md`): every edit with its before-state, agent-proposal-vs-human-final diffs, **accept/reject decisions on agent proposals with reason codes**, and review attention (playback ranges). The **advisor** (`backend/advisor.py`) is the first model built on top of that data, and it runs inside the app so the loop closes immediately: the agent can ask *"would this trim be accepted?"* before proposing it, and the human sees a predicted acceptance next to every pending proposal.

## What the baseline learns from
Each proposal *item* becomes one example: the op (what changes: which fields, trim direction and size, move direction, whether it adds or removes a clip, touches titles/transitions/look/audio), the clip's state before the op (position in the sequence — open/middle/close — and its duration), the track kind, keywords from the agent's `reason`, keywords from the project **brief** (platform, objective), and the label: accepted = 1, rejected = 0. Features are hashed into a 512-dimensional space and a logistic regression is trained by SGD (pure Python; no dependencies). Alongside the model, transparent **rules** are mined: acceptance rate per op pattern and per reason code — the "Learned preferences" list in the Info panel.

On synthetic decisions where the human rejects shortening the opening shot 85% of the time, the baseline recovers that preference (P(accept) ≈ 0.2 for that pattern vs ≈ 0.5–0.75 elsewhere) from ~120 examples. Real editors are more consistent than that generator, and the model improves monotonically as decisions accrue.

## Endpoints
| | |
|---|---|
| `POST /api/advisor/train` | retrain from every project under the data root; persists `advisor_model.json` |
| `GET /api/advisor/report` | example count, mined rules (pattern, accepted, rejected, acceptance rate) |
| `POST /api/advisor/score {sequence, ops, reason}` | per-op `p_accept`, the matching rule's history, and a confidence tier (low < 20 examples, medium < 100, high) |

## How the agent should use it
1. Draft the ops for a change. 2. `POST /api/advisor/score`. 3. If `p_accept` is low and confidence is not `low`, either drop the idea, reshape it (a smaller trim, a different position), or propose it anyway **with a reason that names the disagreement** — the human's response to a well-argued low-probability proposal is the most valuable example in the dataset. 4. Never let the score veto silently: the human should see proposals the agent believes in.

## Growing past the baseline
- **More labels**: every accept/reject is one example; annotations ("too long", "wrong take") and edit-pair diffs are implicit labels waiting for a second head.
- **Richer inputs**: the brief, the reviewed-range heat-map (what was replayed before a decision), the clip's own metadata (`meta.description`, `keywords`), and — with a vision model — thumbnails of the frames at the cut.
- **A real model**: export the dataset (`GET /api/training/export` → JSONL, schema 2), train offline (gradient-boosted trees over the same features is the obvious next step; a small transformer over op sequences after that), then serve it behind the same `score` contract so the UI and the agent don't change.
- **Evaluation**: hold out the most recent project; report accuracy and calibration per reason code; watch for the failure mode where the model learns *the agent's habits* rather than *the human's taste* — vary the proposals the agent makes.

## Inspecting a dataset
`python3 agent/dataset_stats.py projects/<id>/training/dataset_<ts>.jsonl` prints record counts, decisions, acceptance by reason code and by op pattern, tool usage by actor, reviewed seconds, and warnings (too few decisions, one-sided decisions, missing reason codes).

# Playbook — an ad in ten minutes, with the agent

This is the shortest path through the whole system for a social ad. Every step is a menu item for you and one SDK call for an agent.

1. **Set up once.** Help › System Check. Info panel › Brand kit (colours, font — add your own with **+**). File › New Project; fill the Brief (client, objective, platform, audience, constraints) — the agent reads it, the training data carries it.
2. **Bring in footage.** Drop files, or Media Browser. Anything FFmpeg opens works; proxies appear in the background. Select a camera log profile in Metadata › Colour for log footage.
3. **Rough cut in one move.** Select the shots in order (Ctrl-click), pick the music, File › **New Reel from Footage** — hook, beat-cut body, CTA, captions, whooshes. Or let the agent: `cr.reel(shots, music, hook="…", cta="…")`.
4. **Make it yours.** Type tool: click any text in the monitor to reword it. Effect Controls: type a Duration to shorten a section. Graphics workspace: Layer Animator for in/out motion per layer, cascade, kinetic words; the template and look galleries show everything with your brand applied. Colour: Auto, Match, looks. Audio: Voice Clean-up preset on the talking head, Remove Silences, ducking.
5. **Work with the agent.** Header: *proposals only* if you want to approve every change. Read its proposals in the Proposals panel (ghosts on the timeline, P(accept) from your history), **Preview** before accepting, give a reason code when you decide — that is what trains the advisor. Undo covers the agent's edits. History › Compare to snapshot shows exactly what changed. `cr.describe()` is what the agent reads first; `cr.note()` is how it flags a concern for you.
6. **Test variants.** Sequence › Create Hook Variants (one headline per line) → one sequence each.
7. **Deliver.** Press Enter to render the preview and watch the exact result. Export with a platform preset — the QA readback flags loudness, peak and platform rules; smart export re-encodes only what changed when you go back for a word. Batch export renders every variant; Render Queue › Manifest CSV is the deliverables sheet. File › Export Cover for the thumbnail.
8. **Learn.** Info › Learned preferences (Retrain); `agent/evaluate_advisor.py` for calibration; `GET /api/training/export` for the dataset.

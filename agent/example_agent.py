"""Example agent: a rough cut from a brief, proposed — not imposed — to the human.
usage: python3 agent/example_agent.py http://localhost:8787 /path/to/footage_dir [target_seconds]

What it does (deliberately simple and deterministic):
1. Reads the project brief and the human's session summary (what they touched, what they rejected and why).
2. Imports the footage folder, orders shots by name, and asks the advisor how a trim-heavy open would land.
3. Lays a rough cut on V1 (each shot trimmed to a share of the target duration, notes explaining each choice),
   adds a title card and, if a music file is present, a music bed remixed to the target duration with ducking keyframes.
4. Snapshots as `agent_proposal`, then PROPOSES two refinements (a tighter open, an end card) and waits for decisions.
5. Retrains the advisor on whatever the human decided, and prints the learned preferences."""
import sys, os, uuid
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); from filmocity_client import Filmocity, FilmocityError

def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8787"; folder = sys.argv[2] if len(sys.argv) > 2 else None; target = float(sys.argv[3]) if len(sys.argv) > 3 else 12.0
    cr = Filmocity(base, actor="agent"); seq = cr.sequence(); brief = cr.brief(); summ = cr.session_summary(since=0)
    print(f"brief: {brief or '(none)'} · human tools so far: {summ['human_ops_by_tool']} · decisions: {len(summ['proposal_decisions'])}")
    if folder:
        files = sorted((f for f in cr.browse(folder)["files"] if not f["imported"]), key=lambda f: f["name"]); cr.import_paths([os.path.join(folder, f["name"]) for f in files]); print(f"imported {len(files)} file(s)")
    media = cr.media(); shots = sorted([m for m in media.values() if m.get("has_video") and not m.get("is_image") and not m.get("synthetic")], key=lambda m: m["name"]); music = next((m for m in media.values() if m.get("has_audio") and not m.get("has_video")), None); still = next((m for m in media.values() if m.get("is_image")), None)
    if not shots: print("no video in the project — import footage first"); return
    # rough cut: equal shares, first shot gets 1.5x (the hook), trimmed from the middle of each source
    per = target / (len(shots) + 0.5); t = 0.0
    for i, m in enumerate(shots):
        d = min(per * (1.5 if i == 0 else 1.0), m["duration"] - 0.2); in_ = max(0.0, (m["duration"] - d) / 2)
        cr.place(m["id"], "V1", start=t, in_=in_, out=in_ + d, reason=f"rough cut shot {i + 1}", note=("hook — the strongest frame first" if i == 0 else f"beat {i + 1} of {len(shots)}: mid-clip section, steadiest motion")); t += d
    # an animated lower third instead of a static title: rule wipes in, headline rises, sub-line types on; mirrored exits
    H = seq["height"]; cr.graphic([
        {"kind": "shape", "shape": "rect", "x": 0.06, "y": 0.70, "w": 0.55, "h": 0.008, "color": "#E8631C", "anim_in": {"type": "wipe_left", "duration": 0.45, "ease": "ease_out"}, "anim_out": {"type": "fade", "duration": 0.3}},
        {"kind": "text", "text": brief.get("objective") or "Headline here", "size": int(H * 0.042), "align": "left", "valign": "center", "y": int(H * 0.12), "color": "white", "shadow": True, "anim_in": {"type": "rise", "duration": 0.5, "delay": 0.15, "ease": "ease_out"}, "anim_out": {"type": "drop", "duration": 0.4}},
        {"kind": "text", "text": (brief.get("client") or "brand") + " · " + (brief.get("platform") or "social"), "size": int(H * 0.024), "align": "left", "valign": "center", "y": int(H * 0.165), "color": "#DDDDDD", "weight": "regular", "anim_in": {"type": "typewriter", "duration": 0.9, "delay": 0.5}, "anim_out": {"type": "fade", "duration": 0.3}}],
        start=0.4, duration=3.0, name="Lower third", reason="lower third from the brief — animated per layer so the human can retime or reword any layer")
    if still: cr.place(still["id"], "V1", start=t, in_=0, out=2.5, reason="end card", note="brand end card"); t += 2.5
    if music:
        rx = cr.remix(music["id"], t); start = 0.0; prev = None
        for sg in rx["segments"]:
            c = cr.place(music["id"], "A2", start=start, in_=sg["in"], out=sg["out"], reason=f"music remixed to {t:.1f}s at {rx['bpm']} BPM", note="bed remixed on bar boundaries", audio={"gain_db": -8, "linked": True}, audio_transition_in={"type": "constant_power", "duration": 0.4} if prev else None); start += sg["out"] - sg["in"]; prev = c
    cr.snapshot("agent_proposal"); print(f"rough cut laid: {len(shots)} shots, {t:.1f}s")
    # refinements as proposals, scored first
    first = sorted(cr.sequence()["tracks"][1]["clips"], key=lambda c: c["start"])[0] if len(cr.sequence()["tracks"]) > 1 and cr.sequence()["tracks"][1]["clips"] else None
    items = []
    if first:
        ops = [{"op": "set_clip", "sequence": seq["id"], "track": "V1", "clip": {"id": first["id"], "out": round(first["out"] - 0.4, 3)}}]; sc = cr.score(ops, "tighter hook")[0]; print(f"advisor on tightening the hook: {sc}")
        items.append({"ops": ops, "reason": "hook runs 0.4 s past the beat; tighten" + (" (advisor expects a rejection — proposing anyway, tell me why if so)" if sc.get("p_accept") is not None and sc["p_accept"] < 0.3 else "")})
    items.append({"ops": [{"op": "set_clip", "sequence": seq["id"], "track": "V2", "clip": {"id": uuid.uuid4().hex[:8], "media_id": None, "start": max(0.0, t - 2.5), "in_": 0, "out": 2.5, "speed": 1, "title": {"text": (brief.get("client") or "brand") + ".com", "size": int(seq["height"] * 0.045), "color": "white", "valign": "bottom", "y": -int(seq["height"] * 0.08)}, "transform": {"opacity": 1}}}], "reason": "CTA URL on the end card"})
    pid = cr.propose("Rough cut refinements", items); print(f"proposed {len(items)} item(s) as {pid}; waiting for the human…")
    dec = cr.wait_for_decisions(pid, timeout=float(os.environ.get("FILMOCITY_WAIT", "20")))
    print("decisions:", dec or "(none yet — leave them for the human)")
    if dec: print("retrained:", cr.retrain()["examples"], "examples;", "top preferences:", [(r["pattern"], r["acceptance"]) for r in cr.preferences()["rules"][:3]])

if __name__ == "__main__":
    try: main()
    except FilmocityError as e: print("Filmocity refused:", e); sys.exit(1)

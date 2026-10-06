"""Feed-forward advisor: learns the human's editing preferences from what Filmocity records (proposal decisions with reason
codes, agent-proposal vs human-final diffs, annotations, review attention) and scores new proposals before the agent makes
them. Pure Python (no numpy): feature hashing + logistic regression trained by SGD, plus transparent mined rules.
This is the *baseline* model the dataset was designed for; swap `predict` for a larger model when the data warrants it."""
import json, math, os, re, time, hashlib
from collections import defaultdict

DIM = 512  # hashed feature space

def _h(s): return int(hashlib.md5(s.encode()).hexdigest()[:8], 16) % DIM

def op_features(op, ctx):
    """Sparse features for one proposed op. ctx = {seq_duration, clip (current state), reason, brief, track_kind}."""
    f = defaultdict(float); c0 = ctx.get("clip") or {}; new = op.get("clip") or {}
    kind = op.get("op", "?"); f[_h("op:" + kind)] += 1
    if kind == "set_clip":
        for k in new:
            if k == "id": continue
            f[_h("field:" + k)] += 1
        if "out" in new and "out" in c0 and "in_" in c0:
            d = (new["out"] - c0["out"]); f[_h("trim_tail:" + ("shorter" if d < 0 else "longer"))] += 1; f[_h("trim_mag:" + str(min(3, int(abs(d)))))] += 1
        if "in_" in new and "in_" in c0:
            d = new["in_"] - c0["in_"]; f[_h("trim_head:" + ("shorter" if d > 0 else "longer"))] += 1
        if "start" in new and "start" in c0: f[_h("move:" + ("later" if new["start"] > c0["start"] else "earlier"))] += 1
        if new.get("title") or (c0.get("title") and "title" in new): f[_h("touch:title")] += 1
        g = new.get("graphic") or {}
        if g:
            f[_h("touch:graphic")] += 1; f[_h("gfx_layers:" + str(min(6, len(g.get("layers") or []))))] += 1
            for L in g.get("layers") or []:
                for side in ("anim_in", "anim_out"):
                    ty = (L.get(side) or {}).get("type")
                    if ty and ty != "none": f[_h(f"gfx_{side}:{ty}")] += 0.5
                if L.get("glow"): f[_h("gfx:glow")] += 0.5
        if "transition_in" in new or "transition_out" in new: f[_h("touch:transition")] += 1
        if "color" in new or "fx_stack" in new: f[_h("touch:look")] += 1
        if "audio" in new or "afx_stack" in new: f[_h("touch:audio")] += 1
        if not c0: f[_h("adds_new_clip")] += 1
    if kind == "remove_clip": f[_h("removes_clip")] += 1
    sd = float(ctx.get("seq_duration") or 0)
    if c0.get("start") is not None and sd:
        pos = c0["start"] / sd; f[_h("pos:" + ("open" if pos < 0.2 else "close" if pos > 0.8 else "middle"))] += 1
    if c0: f[_h("dur:" + str(min(5, int((c0.get("out", 0) - c0.get("in_", 0)) / max(c0.get("speed", 1), 1e-6)))))] += 1
    f[_h("track:" + str(ctx.get("track_kind") or "?"))] += 1
    for w in re.findall(r"[a-z]{4,}", (ctx.get("reason") or "").lower())[:12]: f[_h("reason:" + w)] += 0.5
    br = ctx.get("brief") or {}
    for k in ("platform", "objective"):
        for w in re.findall(r"[a-z]{4,}", str(br.get(k, "")).lower())[:6]: f[_h(f"brief_{k}:" + w)] += 0.3
    f[_h("bias")] = 1.0
    return f

class Model:
    def __init__(self): self.w = [0.0] * DIM; self.n = 0; self.trained = 0
    def predict(self, f):
        z = sum(self.w[i] * v for i, v in f.items()); return 1 / (1 + math.exp(-max(-30, min(30, z))))
    def train(self, rows, epochs=80, lr=0.2, l2=0.0005):
        if not rows: return self
        for _ in range(epochs):
            for f, y in rows:
                p = self.predict(f); g = p - y
                for i, v in f.items(): self.w[i] -= lr * (g * v + l2 * self.w[i])
        self.n = len(rows); self.trained = time.time(); return self
    def to_json(self): return {"w": self.w, "n": self.n, "trained": self.trained, "dim": DIM}
    @classmethod
    def from_json(cls, j): m = cls(); m.w = j.get("w", m.w); m.n = j.get("n", 0); m.trained = j.get("trained", 0); return m

def load_examples(project_dirs):
    """Training rows from every project: proposal decisions (accept=1 / reject=0) with the clip state before the op."""
    rows, rules = [], defaultdict(lambda: [0, 0])
    for d in project_dirs:
        pj = os.path.join(d, "project.json"); dec = os.path.join(d, "training", "proposal_decisions.jsonl")
        if not os.path.exists(pj) or not os.path.exists(dec): continue
        proj = json.load(open(pj)); brief = proj.get("brief") or {}
        seqs = {s["id"]: s for s in proj["sequences"]}
        for line in open(dec):
            try: e = json.loads(line)
            except Exception: continue
            y = 1.0 if e.get("decision") == "accept" else 0.0
            befores = e.get("befores") or []
            for i, op in enumerate(e.get("ops") or []):
                seq = seqs.get(op.get("sequence")); sd = 0
                if seq: sd = max([c["start"] + (c["out"] - c["in_"]) / max(c.get("speed", 1), 1e-6) for t in seq["tracks"] for c in t["clips"]] + [0])
                before = befores[i] if i < len(befores) and isinstance(befores[i], dict) else None
                tkind = None
                if seq: tr = next((t for t in seq["tracks"] if t["id"] == op.get("track")), None); tkind = tr["kind"] if tr else None
                f = op_features(op, {"clip": before or {}, "seq_duration": sd, "reason": e.get("reason_agent"), "brief": brief, "track_kind": tkind})
                rows.append((f, y))
                key = "op:" + op.get("op", "?") + ("|" + ",".join(sorted(k for k in (op.get("clip") or {}) if k != "id"))[:60] if op.get("op") == "set_clip" else "")
                rules[key][0 if y else 1] += 1
                for r in (e.get("reasons") or []): rules["reason_code:" + r][0 if y else 1] += 1
        # edit pairs: agent_proposal snapshot → human_final. Clips the agent added/changed that the human then changed or removed count as
        # rejections of the agent's last op on that clip; agent-touched clips left untouched count as acceptances.
        pairs = os.path.join(d, "training", "edit_pairs.jsonl"); events = os.path.join(d, "events.jsonl")
        if os.path.exists(pairs) and os.path.exists(events):
            agent_ops = {}
            for line in open(events):
                try: e = json.loads(line)
                except Exception: continue
                if e.get("type") == "ops" and e.get("actor") == "agent":
                    for o in e.get("ops", []):
                        cid = o.get("clip", {}).get("id") if o.get("op") == "set_clip" else o.get("clip_id")
                        if cid: agent_ops[cid] = (o, e.get("reason"))
            for line in open(pairs):
                try: pr = json.loads(line)
                except Exception: continue
                changed = {c["clip"] for c in pr.get("changes", []) if c.get("change") in ("modified", "removed")}
                for cid, (o, reason) in agent_ops.items():
                    y = 0.0 if cid in changed else 1.0
                    seq = seqs.get(o.get("sequence")); tkind = None
                    if seq: tr = next((t for t in seq["tracks"] if t["id"] == o.get("track")), None); tkind = tr["kind"] if tr else None
                    rows.append((op_features(o, {"clip": {}, "seq_duration": 0, "reason": reason, "brief": brief, "track_kind": tkind}), y)); rules["edit_pair:" + ("kept" if y else "changed_by_human")][0 if y else 1] += 1
    return rows, rules

def report(rules):
    out = []
    for k, (acc, rej) in sorted(rules.items(), key=lambda kv: -(kv[1][0] + kv[1][1])):
        n = acc + rej
        if n == 0: continue
        out.append({"pattern": k, "accepted": acc, "rejected": rej, "acceptance": round(acc / n, 2), "n": n})
    return out

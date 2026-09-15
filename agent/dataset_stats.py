"""Summarize a Filmocity training dataset export (training/dataset_*.jsonl) — counts by kind, acceptance by reason code and op pattern,
tools used by humans vs agents, review attention, and warnings about thin or skewed data. usage: python3 agent/dataset_stats.py path/to/dataset.jsonl"""
import json, sys, collections
def main(path):
    kinds = collections.Counter(); tools = collections.defaultdict(collections.Counter); dec = collections.Counter(); by_reason = collections.defaultdict(lambda: [0, 0]); by_pattern = collections.defaultdict(lambda: [0, 0]); reviewed = 0.0; meta = None; pairs = 0
    for line in open(path):
        try: e = json.loads(line)
        except Exception: continue
        k = e.get("kind"); kinds[k] += 1
        if k == "meta": meta = e
        elif k == "event":
            if e.get("type") == "ops": tools[e.get("actor", "?")][e.get("tool") or "?"] += 1
            elif e.get("type") == "playback": reviewed += max(0.0, float(e.get("end", 0)) - float(e.get("start", 0)))
        elif k == "proposal_decision":
            d = e.get("decision"); dec[d] += 1
            for r in e.get("reasons") or ["(no reason code)"]: by_reason[r][0 if d == "accept" else 1] += 1
            for o in e.get("ops") or []:
                pat = o.get("op", "?") + ("|" + ",".join(sorted(x for x in (o.get("clip") or {}) if x != "id"))[:50] if o.get("op") == "set_clip" else ""); by_pattern[pat][0 if d == "accept" else 1] += 1
        elif k == "edit_pair": pairs += 1
    print(f"dataset: {path}"); 
    if meta: print(f"project: {meta.get('project')} · brief: {meta.get('brief') or '(none)'}")
    print("records:", dict(kinds)); print("decisions:", dict(dec), f"· edit pairs: {pairs} · reviewed playback: {reviewed:.0f} s")
    for actor, c in tools.items(): print(f"tools[{actor}]:", ", ".join(f"{t}×{n}" for t, n in c.most_common(10)))
    if by_reason: print("acceptance by reason code:"); [print(f"  {r:<22} {a:>3}✓ {b:>3}✗  {a / (a + b):.0%}") for r, (a, b) in sorted(by_reason.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))]
    if by_pattern: print("acceptance by op pattern:"); [print(f"  {p[:50]:<50} {a:>3}✓ {b:>3}✗  {a / (a + b):.0%}") for p, (a, b) in sorted(by_pattern.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))[:12]]
    n = sum(dec.values()); warn = []
    if n < 20: warn.append(f"only {n} decisions — the advisor is low-confidence below 20, medium below 100")
    if n and (dec.get("accept", 0) / n > 0.9 or dec.get("accept", 0) / n < 0.1): warn.append("decisions are >90% one-sided — the model will learn the base rate, not your taste; make the agent propose bolder variety")
    if by_reason.get("(no reason code)", [0, 0])[0] + by_reason.get("(no reason code)", [0, 0])[1] > n * 0.3: warn.append("many decisions have no reason code — pick a chip when you accept/reject; it is the label that transfers across projects")
    for w in warn: print("⚠", w)
if __name__ == "__main__": main(sys.argv[1] if len(sys.argv) > 1 else sys.exit("usage: dataset_stats.py dataset.jsonl"))

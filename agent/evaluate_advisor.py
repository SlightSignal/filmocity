"""Evaluate the advisor: (a) online calibration from predictions stored on decisions (advisor_p vs outcome), and
(b) a time-ordered hold-out — train on the first 70% of decisions, test on the last 30%. usage: python3 agent/evaluate_advisor.py <data_root>"""
import json, os, sys, math
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")); import advisor

def load_decisions(root):
    out = []
    pdir = os.path.join(root, "projects")
    for pid in sorted(os.listdir(pdir)) if os.path.isdir(pdir) else []:
        f = os.path.join(pdir, pid, "training", "proposal_decisions.jsonl")
        if os.path.exists(f):
            for line in open(f):
                try: e = json.loads(line); e["_project"] = pid; out.append(e)
                except Exception: pass
    return sorted(out, key=lambda e: e.get("ts", 0))

def calibration(pairs):
    buckets = {}
    for p, y in pairs: b = min(9, int(p * 10)); buckets.setdefault(b, [0, 0]); buckets[b][0] += y; buckets[b][1] += 1
    return [(f"{b / 10:.1f}–{(b + 1) / 10:.1f}", n, round(acc / n, 2)) for b, (acc, n) in sorted(buckets.items())]

def main(root):
    dec = load_decisions(root); print(f"{len(dec)} decisions across projects")
    online = [(e["advisor_p"], 1.0 if e["decision"] == "accept" else 0.0) for e in dec if e.get("advisor_p") is not None]
    if online:
        acc = sum(1 for p, y in online if (p >= 0.5) == (y == 1)) / len(online); ll = -sum(y * math.log(max(p, 1e-6)) + (1 - y) * math.log(max(1 - p, 1e-6)) for p, y in online) / len(online)
        print(f"online (predictions stored at proposal time): n={len(online)} accuracy={acc:.2f} log-loss={ll:.3f}"); print("  calibration (bucket, n, observed acceptance):", calibration(online))
    else: print("online: no stored predictions yet (train the advisor, then new proposals carry advisor_p)")
    # time-ordered hold-out using the same feature builder as the server
    dirs = [os.path.join(root, "projects", d) for d in os.listdir(os.path.join(root, "projects"))] if os.path.isdir(os.path.join(root, "projects")) else []
    rows, _ = advisor.load_examples(dirs)
    if len(rows) < 10: print("hold-out: fewer than 10 examples — not meaningful yet"); return
    k = int(len(rows) * 0.7); m = advisor.Model().train(rows[:k]); test = rows[k:]
    acc = sum(1 for f, y in test if (m.predict(f) >= 0.5) == (y == 1)) / len(test); base = max(sum(y for _, y in test), len(test) - sum(y for _, y in test)) / len(test)
    print(f"hold-out: train={k} test={len(test)} accuracy={acc:.2f} (majority baseline {base:.2f})"); print("  calibration:", calibration([(m.predict(f), y) for f, y in test]))
if __name__ == "__main__": main(sys.argv[1] if len(sys.argv) > 1 else sys.exit("usage: evaluate_advisor.py <data_root>"))

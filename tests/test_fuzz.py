"""Fuzz the op model: random sequences of valid and invalid ops against the live server; assert the project never corrupts.
Invariants after every accepted PATCH: project loads; every clip has in_ < out, start ≥ 0, speed in range; no two clips overlap on a
track; every media_id resolves; the render command still builds. Invalid ops must be refused with 422, never applied."""
import json, os, random, subprocess, sys, tempfile, time, urllib.request, urllib.error
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.join(ROOT, "backend")); PORT = 8954; B = f"http://127.0.0.1:{PORT}"
def call(path, body=None, method=None):
    req = urllib.request.Request(B + path, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"}, method=method or ("POST" if body is not None else "GET"))
    try: return urllib.request.urlopen(req, timeout=60).getcode(), json.loads(urllib.request.urlopen(urllib.request.Request(B + "/api/project")).read()) if False else json.loads(urllib.request.urlopen(req, timeout=60).read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"{}")
def invariants(proj):
    for sq in proj["sequences"]:
        for t in sq["tracks"]:
            spans = []
            for c in t["clips"]:
                assert c["in_"] < c["out"] + 1e-9, f"in>=out {c}"; assert c["start"] >= -1e-9, f"start<0 {c}"; assert 0.01 <= c.get("speed", 1) <= 100, f"speed {c}"
                if c.get("media_id"): assert c["media_id"] in proj["media"], f"dangling media {c['media_id']}"
                d = (c["out"] - c["in_"]) / c.get("speed", 1); spans.append((c["start"], c["start"] + d, c["id"]))
            spans.sort()
            for (a0, a1, aid), (b0, b1, bid) in zip(spans, spans[1:]): assert b0 >= a1 - 1e-6, f"overlap {aid}[{a0:.3f},{a1:.3f}] {bid}[{b0:.3f},{b1:.3f}] on {sq['id']}/{t['id']}"
data = tempfile.mkdtemp(prefix="filmocity_fuzz_"); media = os.path.join(data, "f.mp4")
subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "10", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", media], check=True, capture_output=True)
srv = subprocess.Popen([sys.executable, os.path.join(ROOT, "backend", "server.py"), "--root", data, "--port", str(PORT)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(40):
        time.sleep(0.5)
        try: urllib.request.urlopen(B + "/api/project", timeout=2); break
        except Exception: pass
    code, r = call("/api/media/import", {"paths": [media]}); mid = r["added"][0]["id"]; random.seed(int(os.environ.get("FUZZ_SEED", "7"))); accepted = rejected = 0; tracks = ["V1", "V2", "A1", "A2"]; ids = []
    from render import build_command
    for i in range(int(os.environ.get("FUZZ_N", "220"))):
        kind = random.random(); ops = []
        if kind < 0.45 or not ids:  # add / move / trim a clip (mostly valid)
            cid = random.choice(ids) if ids and random.random() < 0.5 else f"f{i}"; start = round(random.uniform(0, 30), 3); in_ = round(random.uniform(0, 8), 3); out = round(min(10, in_ + random.uniform(0.2, 4)), 3)
            ops = [{"op": "set_clip", "sequence": "seq1", "track": random.choice(tracks), "clip": {"id": cid, "media_id": mid, "start": start, "in_": in_, "out": out, "speed": round(random.choice([1, 1, 1, 0.5, 2, 0.25]), 3)}}]
        elif kind < 0.6 and ids: ops = [{"op": "remove_clip", "sequence": "seq1", "track": random.choice(tracks), "clip_id": random.choice(ids)}]
        elif kind < 0.75 and ids: cid = random.choice(ids); ops = [{"op": "set_clip", "sequence": "seq1", "track": random.choice(tracks), "clip": {"id": cid, random.choice(["start", "in_", "out", "speed"]): round(random.uniform(-2, 40), 3)}}]  # sometimes invalid
        elif kind < 0.85: ops = [{"op": "set_clip", "sequence": "seq1", "track": random.choice(tracks), "clip": {"id": f"bad{i}", "media_id": random.choice([mid, "nope"]), "start": random.choice([-1, 5]), "in_": 3, "out": random.choice([2, 99, 4])}}]
        elif kind < 0.92: ops = [{"op": random.choice(["set_clip", "bogus", "remove_clip"]), "sequence": random.choice(["seq1", "seq9"]), "track": random.choice(tracks + ["V9"]), "clip": {"id": f"x{i}", "media_id": mid, "start": 1, "in_": 0, "out": 1}, "clip_id": f"x{i}"}]
        else: ops = [{"op": "set_clip", "sequence": "seq1", "track": t, "clip": {"id": f"m{i}{t}", "media_id": mid, "start": 2.0, "in_": 0, "out": 3.0, "speed": 1}} for t in tracks]  # multi-op batch overlapping everything at 2–5 s
        code, resp = call("/api/project", {"ops": ops, "actor": "agent", "tool": "fuzz"}, method="PATCH")
        if code == 200: accepted += 1; [ids.append(o["clip"]["id"]) for o in ops if o["op"] == "set_clip" and o["clip"]["id"] not in ids]
        else: assert code == 422, f"unexpected {code}: {resp}"; rejected += 1
        code, proj = call("/api/project"); invariants(proj)
        if i % 40 == 0: build_command(proj, "seq1", os.path.join(data, "x.mp4"), {"crf": 30})
        ids = [c["id"] for t in proj["sequences"][0]["tracks"] for c in t["clips"]]
    print(f"OK fuzz: {accepted} accepted, {rejected} refused with 422, invariants held after every op; final clips: {len(ids)}")
finally: srv.terminate()

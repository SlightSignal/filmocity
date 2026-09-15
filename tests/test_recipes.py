"""Recipes end to end on a temp server: brand kit → New Reel → describe → hook variants → explainer dressing → cover PNGs → render."""
import json, os, subprocess, sys, tempfile, time, urllib.request
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.join(ROOT, "agent")); PORT = 8957; B = f"http://127.0.0.1:{PORT}"
data = tempfile.mkdtemp(prefix="filmocity_recipes_"); foot = os.path.join(data, "footage"); os.makedirs(foot)
subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30", "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000", "-t", "6", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", f"{foot}/a.mp4"], check=True, capture_output=True)
subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", "smptebars=s=1080x1920:r=30", "-t", "6", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-an", f"{foot}/b.mp4"], check=True, capture_output=True)
subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", "sine=frequency=110:sample_rate=48000,tremolo=f=2:d=0.9", "-t", "24", "-c:a", "aac", f"{foot}/music.m4a"], check=True, capture_output=True)
srv = subprocess.Popen([sys.executable, os.path.join(ROOT, "backend", "server.py"), "--root", data, "--port", str(PORT)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(40):
        time.sleep(0.5)
        try: urllib.request.urlopen(B + "/api/project", timeout=2); break
        except Exception: pass
    from filmocity_client import Filmocity
    cr = Filmocity(B); ms = cr.import_paths([f"{foot}/a.mp4", f"{foot}/b.mp4", f"{foot}/music.m4a"]); shots = [m["id"] for m in ms if m["has_video"]]; music = [m for m in ms if not m["has_video"]][0]["id"]
    cr.brand(primary="#00C2A8", secondary="#004E64", text="#FFFFFF"); cr.patch([{"op": "set", "path": "/sequences/0/width", "value": 540}, {"op": "set", "path": "/sequences/0/height", "value": 960}], tool="test"); time.sleep(3); T0 = time.time(); lap = lambda n: print(f"  {n}: {time.time() - T0:.0f}s", flush=True)
    r = cr.reel(shots, music, target=10, hook="Stop scrolling.", cta="FOLLOW →", caption_style={"animate": "pop", "size": 70}); assert r["ok"] and r["duration"] > 5, r; lap("reel")
    d = cr.describe(); assert "Hook" in d["text"] and d["sections"], d["text"][:200]
    v = cr.variants(["The e-bike that replaced my car.", "450 lb of payload."]); assert len(v["variants"]) == 2 and all(x["replaced"] for x in v["variants"]), v
    cr.patch([{"op": "set", "path": "/sequences/0/markers", "value": [m for m in cr.sequence().get("markers", [])] + [{"id": "ch1", "time": 2.0, "name": "The fix", "type": "chapter"}]}], tool="markers")
    e = cr._call("/api/recipes/explainer", {"sequence": "seq1", "lower_third": {"name": "Sam", "role": "Founder", "at": 1.0}, "chapters": True, "end_card": "SUBSCRIBE"}); assert e["cards"] >= 2, e
    c = cr._call("/api/recipes/cover", {"sequence": "seq1", "time": 3.0, "headline": "The e-bike that replaced my car", "sizes": [[540, 960], [640, 360]]}); assert len(c["covers"]) == 2 and all(os.path.getsize(os.path.join(data, "renders", os.path.basename(p))) > 3000 for p in c["covers"]), c; lap("covers")
    th = cr.place(shots[0], "V1", 30.0, 0, 5.0); t = cr.talking_head(th["id"], broll=[shots[1]], punch_every=2); assert t["ok"] and t["pieces"] >= 1, t
    dk = cr.duck_all(); assert dk["ducked"] >= 1, dk; lap("talking head + duck")
    j = cr.render("recipes", {"crf": 30, "x264_preset": "ultrafast", "loudnorm": True, "platform": "reels"}); st = cr.wait_render(j); assert st["status"] == "done" and st["qa"].get("platform") == "reels", st; lap("render")
    jr = cr.render("recipes_review", {"crf": 32, "x264_preset": "ultrafast", "burn_tc": True, "watermark_text": "REVIEW"}); sr = cr.wait_render(jr); assert sr["status"] == "done" and sr.get("mode") == "full", sr
    page = urllib.request.urlopen(B + "/review/recipes_review").read().decode(); assert "<video" in page
    note = json.loads(urllib.request.urlopen(urllib.request.Request(B + "/api/review/recipes_review/notes", data=json.dumps({"time": 1.0, "text": "tighter", "author": "client"}).encode(), headers={"Content-Type": "application/json"})).read()); assert note["author"] == "client" and any(m.get("review") == "recipes_review" for m in cr.sequence()["markers"])
    print(f"OK recipes: reel {r['duration']}s ({'beat-cut' if r['beat_cut'] else 'even'}), describe {d['clips']} clips, 2 variants, explainer {e['cards']} cards, 2 covers, talking head {t['pieces']} pieces, duck {dk['ducked']}, render {st['qa']['duration']}s flags={st['qa']['flags']}, review copy + client note")
finally: srv.terminate()

"""API smoke test: start the server on a temp data root, import media, edit, propose/decide, render, QA. Run: python3 tests/test_api.py"""
import json, os, subprocess, sys, tempfile, time, urllib.request
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); PORT = 8951; B = f"http://127.0.0.1:{PORT}"
def call(path, body=None, method=None):
    req = urllib.request.Request(B + path, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"}, method=method or ("POST" if body is not None else "GET")); return json.loads(urllib.request.urlopen(req, timeout=120).read())
data = tempfile.mkdtemp(prefix="filmocity_test_"); media = os.path.join(data, "t.mp4")
subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", media], check=True, capture_output=True)
srv = subprocess.Popen([sys.executable, os.path.join(ROOT, "backend", "server.py"), "--root", data, "--port", str(PORT)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(40):
        time.sleep(0.5)
        try: call("/api/project"); break
        except Exception: pass
    m = call("/api/media/import", {"paths": [media]})["added"][0]
    for _ in range(60):
        if call("/api/project")["media"][m["id"]].get("status") == "ready": break
        time.sleep(0.5)
    assert call("/api/project")["media"][m["id"]].get("thumb"), "background ingest did not produce a thumbnail"
    call("/api/project", {"ops": [{"op": "set_clip", "sequence": "seq1", "track": "V1", "clip": {"id": "c1", "media_id": m["id"], "start": 0, "in_": 0.5, "out": 3.0, "speed": 1, "fx_stack": [{"type": "procamp", "params": {"saturation": 130}}]}}], "actor": "agent", "tool": "test", "reason": "smoke"}, method="PATCH")
    pr = call("/api/proposals", {"actor": "agent", "title": "t", "items": [{"ops": [{"op": "set_clip", "sequence": "seq1", "track": "V1", "clip": {"id": "c1", "out": 2.5}}], "reason": "shorter"}]})
    call(f"/api/proposals/{pr['id']}/{pr['items'][0]['id']}/accept", {"note": "ok", "reasons": ["pacing"]})
    assert abs(call("/api/project")["sequences"][0]["tracks"][1]["clips"][0]["out"] - 2.5) < 1e-6
    tr = call("/api/advisor/train", {}); sc = call("/api/advisor/score", {"sequence": "seq1", "ops": [{"op": "set_clip", "track": "V1", "clip": {"id": "c1", "out": 2.0}}], "reason": "shorter"}); assert tr["examples"] >= 1 and sc["scores"][0]["p_accept"] is not None, (tr, sc)
    assert len(call("/api/effects")["video"]) > 30 and len(call("/api/luts")) >= 8 and len(call("/api/templates")) >= 12
    job = call("/api/render", {"sequence": "seq1", "preset": {"crf": 26, "x264_preset": "ultrafast", "loudnorm": True}, "name": "smoke"})
    for _ in range(120):
        time.sleep(1); st = call("/api/render/" + job["id"])
        if st["status"] != "running": break
    assert st["status"] == "done", st
    assert st["qa"]["width"] == 1080 and st["qa"]["integrated_lufs"] is not None, st["qa"]
    assert st.get("mode") == "incremental" and st.get("segments", 0) >= 1, st
    # re-export after a wording-only change must reuse segments and stay fast
    call("/api/project", {"ops": [{"op": "set_clip", "sequence": "seq1", "track": "V2", "clip": {"id": "t1", "media_id": None, "start": 0.2, "in_": 0, "out": 1.0, "speed": 1, "title": {"text": "First words", "size": 80, "color": "white"}, "transform": {"opacity": 1}}}], "actor": "human", "tool": "test"}, method="PATCH")
    j1 = call("/api/render", {"sequence": "seq1", "preset": {"crf": 26, "x264_preset": "ultrafast", "loudnorm": True}, "name": "smart1"})
    for _ in range(120):
        time.sleep(1); s1 = call("/api/render/" + j1["id"])
        if s1["status"] != "running" and s1["status"] != "queued": break
    call("/api/project", {"ops": [{"op": "set_clip", "sequence": "seq1", "track": "V2", "clip": {"id": "t1", "title": {"text": "Second words", "size": 80, "color": "white"}}}], "actor": "human", "tool": "test"}, method="PATCH")
    t0 = time.time(); j2 = call("/api/render", {"sequence": "seq1", "preset": {"crf": 26, "x264_preset": "ultrafast", "loudnorm": True}, "name": "smart2"})
    for _ in range(120):
        time.sleep(0.5); s2 = call("/api/render/" + j2["id"])
        if s2["status"] not in ("running", "queued"): break
    assert s2["status"] == "done" and s2.get("mode") == "incremental", s2
    print(f"smart export: {s1.get('segments')} segments; after a wording change {s2.get('reused')}/{s2.get('segments')} reused in {time.time() - t0:.1f}s")
    print("OK api smoke:", st["qa"]); print("summary:", call("/api/session/summary?since=0")["n_events"], "events")
finally:
    srv.terminate()

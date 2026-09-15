"""Runs the example agent against a temp server and checks it produced a valid rough cut and a proposal."""
import json, os, subprocess, sys, tempfile, time, urllib.request
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); PORT = 8953; B = f"http://127.0.0.1:{PORT}"
data = tempfile.mkdtemp(prefix="filmocity_agent_"); foot = os.path.join(data, "footage"); os.makedirs(foot)
for i, src in enumerate(["testsrc2=s=640x360:r=30", "color=c=0x2E6BB0:s=640x360:r=30", "smptebars=s=640x360:r=30"]):
    subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", src, "-f", "lavfi", "-i", f"sine=frequency={300 + 100 * i}:sample_rate=48000", "-t", "6", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", os.path.join(foot, f"shot_{i + 1}.mp4")], check=True, capture_output=True)
subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", "sine=frequency=110:sample_rate=48000,tremolo=f=2:d=0.9", "-t", "24", "-c:a", "aac", os.path.join(foot, "music.m4a")], check=True, capture_output=True)
srv = subprocess.Popen([sys.executable, os.path.join(ROOT, "backend", "server.py"), "--root", data, "--port", str(PORT)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(40):
        time.sleep(0.5)
        try: urllib.request.urlopen(B + "/api/project", timeout=2); break
        except Exception: pass
    env = dict(os.environ, FILMOCITY_WAIT="1"); r = subprocess.run([sys.executable, os.path.join(ROOT, "agent", "example_agent.py"), B, foot, "12"], capture_output=True, text=True, env=env, timeout=300)
    print(r.stdout.strip().splitlines()[-3:]); assert r.returncode == 0, r.stderr[-800:]
    proj = json.loads(urllib.request.urlopen(B + "/api/project").read()); seq = proj["sequences"][0]
    v1 = next(t for t in seq["tracks"] if t["id"] == "V1")["clips"]; a2 = next(t for t in seq["tracks"] if t["id"] == "A2")["clips"]
    assert len(v1) == 3 and all(c.get("note") for c in v1), v1; assert len(a2) >= 1, a2
    props = json.loads(urllib.request.urlopen(B + "/api/proposals").read()); assert props and len(props[0]["items"]) == 2 and all(i["status"] == "pending" for i in props[0]["items"])
    print("OK agent: rough cut", len(v1), "shots,", len(a2), "music segment(s), proposal pending")
finally: srv.terminate()

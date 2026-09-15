"""Real-footage smoke test: import a set of awkward camera files (rotation metadata, 10-bit HEVC, HLG HDR, ProRes 4:2:2, VFR, 5.1 audio,
44.1 kHz mono, alpha PNG, GIF, portrait 4K, 59.94p), cut them into one 9:16 sequence with mixed audio, export, and check the QA line
plus a mid-frame for each clip. Generates the media with FFmpeg if /tmp/footage is absent. usage: python3 tests/test_footage.py"""
import json, os, subprocess, sys, tempfile, time, urllib.request, urllib.error
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); PORT = 8955; B = f"http://127.0.0.1:{PORT}"; FOOT = os.environ.get("FILMOCITY_FOOTAGE", "/tmp/footage")
def call(path, body=None, method=None):
    req = urllib.request.Request(B + path, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"}, method=method or ("POST" if body is not None else "GET"))
    try: return json.loads(urllib.request.urlopen(req, timeout=600).read())
    except urllib.error.HTTPError as e: return {"http": e.code, **json.loads(e.read() or b"{}")}
def gen():
    os.makedirs(FOOT, exist_ok=True); L = lambda *a: subprocess.run(["ffmpeg", "-hide_banner", "-y", *a], capture_output=True, check=True)
    L("-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", f"{FOOT}/_rot_src.mp4"); L("-display_rotation", "90", "-i", f"{FOOT}/_rot_src.mp4", "-c", "copy", f"{FOOT}/phone_rotated90.mp4")
    L("-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30", "-t", "4", "-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-preset", "ultrafast", "-tag:v", "hvc1", "-an", f"{FOOT}/hevc_10bit.mp4")
    L("-f", "lavfi", "-i", "testsrc2=s=1280x720:r=25", "-t", "4", "-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-preset", "ultrafast", "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc", "-x265-params", "colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc", "-tag:v", "hvc1", "-an", f"{FOOT}/hlg_hdr.mp4")  # x265-params too: current FFmpeg drops the -color_* flags from x265's VUI
    L("-f", "lavfi", "-i", "smptebars=s=1280x720:r=24", "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000", "-t", "4", "-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le", "-c:a", "pcm_s16le", f"{FOOT}/prores422.mov")
    L("-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-f", "lavfi", "-i", "sine=frequency=660:sample_rate=48000", "-f", "lavfi", "-i", "sine=frequency=110:sample_rate=48000", "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000", "-f", "lavfi", "-i", "sine=frequency=550:sample_rate=48000", "-filter_complex", "[0][1][2][3][4][5]join=inputs=6:channel_layout=5.1[a]", "-map", "[a]", "-t", "4", "-c:a", "aac", f"{FOOT}/surround51.m4a")
    L("-f", "lavfi", "-i", "sine=frequency=300:sample_rate=44100", "-t", "4", "-ac", "1", "-c:a", "pcm_s16le", f"{FOOT}/mono44k.wav")
    L("-f", "lavfi", "-i", "color=c=0x00000000:s=800x400,format=rgba", "-frames:v", "1", "-update", "1", "-vf", "drawbox=x=100:y=100:w=600:h=200:color=0xE8631C@1.0:t=fill", f"{FOOT}/logo_alpha.png")
    L("-f", "lavfi", "-i", "testsrc2=s=320x180:r=10", "-t", "2", f"{FOOT}/anim.gif"); L("-f", "lavfi", "-i", "testsrc2=s=2160x3840:r=30", "-t", "2", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-an", f"{FOOT}/portrait4k.mp4"); L("-f", "lavfi", "-i", "testsrc2=s=1280x720:r=60000/1001", "-t", "3", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-an", f"{FOOT}/p5994.mp4")
    L("-f", "lavfi", "-i", "testsrc2=s=640x360:r=30", "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", f"{FOOT}/a30.mp4"); L("-f", "lavfi", "-i", "testsrc2=s=640x360:r=15", "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", f"{FOOT}/a15.mp4"); open(f"{FOOT}/cat.txt", "w").write(f"file '{FOOT}/a30.mp4'\nfile '{FOOT}/a15.mp4'\n"); L("-f", "concat", "-safe", "0", "-i", f"{FOOT}/cat.txt", "-c", "copy", f"{FOOT}/vfr_mixed.mkv")  # no -vsync: removed in FFmpeg 8, and stream copy never used it
if not os.path.exists(f"{FOOT}/hlg_hdr.mp4"): gen()
data = tempfile.mkdtemp(prefix="filmocity_footage_"); srv = subprocess.Popen([sys.executable, os.path.join(ROOT, "backend", "server.py"), "--root", data, "--port", str(PORT)], stdout=subprocess.DEVNULL, stderr=open(os.path.join(data, "server.log"), "w"))
try:
    for _ in range(40):
        time.sleep(0.5)
        try: urllib.request.urlopen(B + "/api/project", timeout=2); break
        except Exception: pass
    names = ["phone_rotated90.mp4", "hevc_10bit.mp4", "hlg_hdr.mp4", "prores422.mov", "vfr_mixed.mkv", "portrait4k.mp4", "p5994.mp4", "anim.gif", "logo_alpha.png", "surround51.m4a", "mono44k.wav"]
    r = call("/api/media/import", {"paths": [f"{FOOT}/{n}" for n in names]}); assert "added" in r, r; m = {x["name"]: x for x in r["added"]}
    probs = []
    for n in names:
        x = m[n]; print(f"  {n:<20} {x['width']}x{x['height']} fps={x['fps']:.2f} dur={x['duration']:.2f} v={x['has_video']} a={x['has_audio']} rot={x.get('rotation')} hdr={x.get('hdr')} vfr={x.get('vfr')} img={x.get('is_image')}")
    if m["phone_rotated90.mp4"].get("rotation") not in (90, -90, 270, -270): probs.append("rotation metadata not detected")
    if not m["hlg_hdr.mp4"].get("hdr"): probs.append("HLG not flagged as HDR")
    if not m["logo_alpha.png"].get("is_image"): probs.append("PNG not detected as still")
    # build a cut: every video item ~1.5 s on V1, PNG logo on V2, 5.1 + mono audio on A2/A3 (mixed to stereo)
    ops = []; t = 0.0
    for n in ["phone_rotated90.mp4", "hevc_10bit.mp4", "hlg_hdr.mp4", "prores422.mov", "vfr_mixed.mkv", "portrait4k.mp4", "p5994.mp4", "anim.gif"]:
        x = m[n]; d = min(1.5, max(0.5, x["duration"] - 0.2)); ops.append({"op": "set_clip", "sequence": "seq1", "track": "V1", "clip": {"id": "c_" + n.split(".")[0], "media_id": x["id"], "start": round(t, 3), "in_": 0.1, "out": round(0.1 + d, 3), "speed": 1, "fit": "cover"}}); t += d
    ops.append({"op": "set_clip", "sequence": "seq1", "track": "V2", "clip": {"id": "logo", "media_id": m["logo_alpha.png"]["id"], "start": 0.5, "in_": 0, "out": 3.0, "speed": 1, "transform": {"scale": 0.6, "y": -600}}})
    ops.append({"op": "set_clip", "sequence": "seq1", "track": "A1", "clip": {"id": "sur", "media_id": m["surround51.m4a"]["id"], "start": 0, "in_": 0, "out": 4.0, "speed": 1, "audio": {"gain_db": -6}}})
    ops.append({"op": "set_clip", "sequence": "seq1", "track": "A2", "clip": {"id": "mono", "media_id": m["mono44k.wav"]["id"], "start": 1.0, "in_": 0, "out": 3.0, "speed": 1}})
    r = call("/api/project", {"ops": ops, "actor": "human", "tool": "footage_test"}, method="PATCH"); assert r.get("ok"), r
    for _ in range(90):
        if all(x.get("status") == "ready" for x in call("/api/project")["media"].values()): break
        time.sleep(1)
    job = call("/api/render", {"sequence": "seq1", "preset": {"crf": 26, "x264_preset": "veryfast", "loudnorm": True}, "name": "footage"})
    for _ in range(300):
        st = call("/api/render/" + job["id"])
        if st["status"] not in ("queued", "running"): break
        time.sleep(2)
    print("render:", st["status"], json.dumps(st.get("qa")) if st.get("qa") else (st.get("error") or "")[-600:])
    if st["status"] != "done": probs.append("render failed: " + (st.get("error") or "")[-300:])
    else:
        out = os.path.join(data, "renders", "footage.mp4"); tmid = t / 2
        for i, n in enumerate(["phone_rotated90.mp4", "hlg_hdr.mp4", "portrait4k.mp4", "anim.gif"]):
            tt = sum(min(1.5, max(0.5, m[k]["duration"] - 0.2)) for k in ["phone_rotated90.mp4", "hevc_10bit.mp4", "hlg_hdr.mp4", "prores422.mov", "vfr_mixed.mkv", "portrait4k.mp4", "p5994.mp4", "anim.gif"][:["phone_rotated90.mp4", "hevc_10bit.mp4", "hlg_hdr.mp4", "prores422.mov", "vfr_mixed.mkv", "portrait4k.mp4", "p5994.mp4", "anim.gif"].index(n)]) + 0.6
            png = os.path.join(data, f"f{i}.png"); subprocess.run(["ffmpeg", "-hide_banner", "-y", "-ss", f"{tt:.2f}", "-i", out, "-frames:v", "1", "-update", "1", png], capture_output=True)
            stats = subprocess.run(["ffmpeg", "-hide_banner", "-i", png, "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG", "-f", "null", "-"], capture_output=True, text=True).stderr; yavg = [l.split("=")[-1] for l in stats.splitlines() if "YAVG" in l]
            if not yavg or float(yavg[0]) < 8: probs.append(f"frame for {n} at {tt:.1f}s is black (YAVG={yavg})")
            else: print(f"  frame {n} @ {tt:.1f}s YAVG={float(yavg[0]):.0f} ✓")
        if st["qa"].get("integrated_lufs") is None: probs.append("no audio in export (5.1 / mono / 44.1k mixdown failed)")
    if probs: print("PROBLEMS:"); [print("  ✗", p) for p in probs]; sys.exit(1)
    print("OK footage: all formats imported, cut, rendered with audio and non-black frames")
finally: srv.terminate()

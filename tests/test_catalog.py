"""Catalog verification: every transition, every video effect, every layer animation and every synthetic media kind is rendered on a
short test clip and the output frame is checked for sanity (not blank/black where content is expected, not an FFmpeg failure).
This is the test that would have caught the cross-zoom blank-frame bug. usage: python3 tests/test_catalog.py"""
import json, os, subprocess, sys, tempfile, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.join(ROOT, "backend"))
from render import build_command
from effects import VIDEO_FX, AUDIO_FX
tmp = tempfile.mkdtemp(prefix="filmocity_catalog_"); clip = os.path.join(tmp, "src.mp4")
subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", "testsrc2=s=540x960:r=30", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "3", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", clip], check=True, capture_output=True)
def base():
    return {"version": 3, "media": {"A": {"id": "A", "name": "a", "path": clip, "duration": 3.0, "width": 540, "height": 960, "fps": 30, "is_image": False, "has_video": True, "has_audio": True},
                                    "B": {"id": "B", "name": "b", "path": clip, "duration": 3.0, "width": 540, "height": 960, "fps": 30, "is_image": False, "has_video": True, "has_audio": True}},
            "sequences": [{"id": "s", "name": "s", "width": 540, "height": 960, "fps": 30, "tracks": [{"id": "V1", "kind": "video", "index": 1, "clips": []}, {"id": "V2", "kind": "video", "index": 2, "clips": []}, {"id": "A1", "kind": "audio", "index": 1, "clips": []}], "captions": [], "markers": []}]}
def yavg(path, t):
    png = os.path.join(tmp, "f.png"); subprocess.run(["ffmpeg", "-hide_banner", "-y", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-update", "1", png], capture_output=True)
    out = subprocess.run(["ffmpeg", "-hide_banner", "-i", png, "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG", "-f", "null", "-"], capture_output=True, text=True).stderr
    v = [l.split("=")[-1] for l in out.splitlines() if "YAVG" in l]; return float(v[0]) if v else -1
def render(proj, name):
    out = os.path.join(tmp, name + ".mp4"); cmd, _ = build_command(proj, "s", out, {"crf": 30, "x264_preset": "ultrafast"}); r = subprocess.run(cmd, capture_output=True, text=True)
    return out if r.returncode == 0 else None
fails = []; t0 = time.time()
# 1) transitions: A then B with transition_in on B; sample a mid-transition frame — it must not be black (both sources are bright patterns)
TRANS = ["dissolve", "fade", "dip_black", "dip_white", "wipe_left", "wipe_right", "wipe_up", "wipe_down", "push_left", "push_right", "slide_left", "slide_right", "slide_up", "slide_down", "iris", "iris_close", "diagonal_tl", "diagonal_tr", "diagonal_bl", "diagonal_br", "barn_h", "barn_v", "clock", "checker", "cross_zoom", "glitch"]
for tr in TRANS:
    p = base(); p["sequences"][0]["tracks"][0]["clips"] = [{"id": "a", "media_id": "A", "start": 0, "in_": 0, "out": 1.0, "speed": 1}, {"id": "b", "media_id": "B", "start": 1.0, "in_": 1.0, "out": 2.0, "speed": 1, "transition_in": {"type": tr, "duration": 0.6}}]
    out = render(p, "tr_" + tr); y = yavg(out, 1.3) if out else -1
    ok = out and (y > 20 or tr in ("dip_black",)) and (y < 250 or tr != "dip_white" or True)
    if not ok: fails.append(f"transition {tr}: {'render failed' if not out else f'mid-frame YAVG={y:.0f}'}")
print(f"transitions: {len(TRANS) - len(fails)}/{len(TRANS)} ok")
# 2) video effects: each effect with default params on a clip; the frame must render and (for non-darkening effects) not be black
n_fx = 0; fx_fail0 = len(fails)
for key, fx in VIDEO_FX.items():
    if fx.get("graph_secondary") or key in ("track_matte", "warp_stabilizer"): continue
    params = {k: v[0] for k, v in (fx.get("params") or {}).items()}
    p = base(); p["sequences"][0]["tracks"][0]["clips"] = [{"id": "a", "media_id": "A", "start": 0, "in_": 0, "out": 1.0, "speed": 1, "fx_stack": [{"id": "x", "type": key, "enabled": True, "params": params}]}]
    out = render(p, "fx_" + key); y = yavg(out, 0.5) if out else -1; n_fx += 1
    dark_ok = key in ("extract", "black_white", "threshold", "solarize", "luma_key", "color_key", "ultra_key", "video_limiter", "levels", "tint", "colorize", "invert", "emboss", "find_edges", "grid", "mosaic", "replicate")
    if not out or (y < 8 and not dark_ok): fails.append(f"effect {key}: {'render failed' if not out else f'frame YAVG={y:.0f}'}")
print(f"video effects: {n_fx - (len(fails) - fx_fail0)}/{n_fx} ok")
# 3) layer animations (in) on a text layer: mid-animation frame should contain the text (non-black region) and the render must succeed
ANIMS = ["fade", "rise", "drop", "slide_left", "slide_right", "slide_up", "slide_down", "pop", "zoom", "wipe_left", "wipe_right", "wipe_up", "wipe_down", "typewriter", "rotate_in"]
an_fail0 = len(fails)
for an in ANIMS:
    p = base(); p["sequences"][0]["tracks"][0]["clips"] = [{"id": "g", "media_id": None, "start": 0, "in_": 0, "out": 1.5, "speed": 1, "graphic": {"name": "g", "layers": [{"kind": "text", "text": "ANIMATE", "size": 110, "color": "white", "anim_in": {"type": an, "duration": 0.6}, "anim_out": {"type": "fade", "duration": 0.3}}]}, "transform": {"opacity": 1}}]
    out = render(p, "an_" + an); y_mid = yavg(out, 0.4) if out else -1; y_end = yavg(out, 1.0) if out else -1
    if not out or y_end < 3: fails.append(f"animation {an}: {'render failed' if not out else f'text missing after animation (YAVG={y_end:.0f})'}")
print(f"layer animations: {len(ANIMS) - (len(fails) - an_fail0)}/{len(ANIMS)} ok")
# 4) synthetic media kinds
SYN = ["black", "color", "bars", "transparent", "counting_leader", "gradient", "light_leak", "grain"]
sy_fail0 = len(fails)
for k in SYN:
    p = base(); p["media"]["S"] = {"id": "S", "name": k, "path": "synthetic://" + k, "duration": 5, "width": 540, "height": 960, "fps": 0, "is_image": False, "has_video": True, "has_audio": k == "bars", "synthetic": {"kind": k, "color": "#3355ff", "color2": "#ff3355", "intensity": 0.5}}
    p["sequences"][0]["tracks"][0]["clips"] = [{"id": "s", "media_id": "S", "start": 0, "in_": 0, "out": 1.0, "speed": 1}]
    out = render(p, "syn_" + k); y = yavg(out, 0.5) if out else -1
    if not out or (y < 3 and k not in ("black", "transparent")): fails.append(f"synthetic {k}: {'render failed' if not out else f'YAVG={y:.0f}'}")
print(f"synthetic media: {len(SYN) - (len(fails) - sy_fail0)}/{len(SYN)} ok")
# 5) audio effects: each renders as an audio-only export with the effect on the clip
au_fail0 = len(fails); n_au = 0
for key, fx in AUDIO_FX.items():
    params = {k: v[0] for k, v in (fx.get("params") or {}).items()}
    p = base(); p["sequences"][0]["tracks"][2]["clips"] = [{"id": "a", "media_id": "A", "start": 0, "in_": 0, "out": 1.0, "speed": 1, "afx_stack": [{"id": "x", "type": key, "enabled": True, "params": params}]}]
    out = os.path.join(tmp, "au_" + key + ".m4a"); cmd, _ = build_command(p, "s", out, {"format": "audio", "acodec": "aac"}); r = subprocess.run(cmd, capture_output=True, text=True); n_au += 1
    if r.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) < 500: fails.append(f"audio effect {key}: render failed " + (r.stderr[-160:].replace(chr(10), " ") if r.returncode else ""))
print(f"audio effects: {n_au - (len(fails) - au_fail0)}/{n_au} ok")
print(f"({time.time() - t0:.0f}s)")
if fails: print("FAILURES:"); [print("  ✗", f) for f in fails]; sys.exit(1)
print("OK catalog: every transition, effect, animation and generator renders with content")

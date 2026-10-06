"""Smoke test: render tests/sample_project.json (two clips, a dissolve, a title) and check the output spec.
Regenerate the sample clips first:
  ffmpeg -y -f lavfi -i testsrc2=s=1280x720:r=30 -f lavfi -i sine=frequency=330:sample_rate=48000 -t 6 -c:v libx264 -pix_fmt yuv420p -c:a aac /tmp/clipA.mp4
  ffmpeg -y -f lavfi -i color=c=0x2E6BB0:s=1280x720:r=30 -f lavfi -i sine=frequency=550:sample_rate=48000 -t 6 -c:v libx264 -pix_fmt yuv420p -c:a aac /tmp/clipB.mp4
"""
import json, os, subprocess, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from render import render
proj = json.load(open(os.path.join(os.path.dirname(__file__), "sample_project_v34.json")))
out = render(proj, "seq1", "/tmp/filmocity_test_render.mp4")
info = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_name,width,height", "-of", "csv=p=0", out], capture_output=True, text=True).stdout
print(info); assert "h264" in info and "1080,1920" in info and "aac" in info, info
print("OK", out, info.replace("\n", " "))

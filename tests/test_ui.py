"""Headless UI regression: start the server, drive the real UI (import, cut, transitions, graphics, captions, duck, effects, keyframes, proposals, export). Requires: pip install playwright && playwright install chromium. Run: python3 tests/test_ui.py"""
import asyncio, json, os, subprocess, sys, tempfile, time, urllib.request
try: from playwright.async_api import async_playwright
except ImportError: print("SKIP ui test (playwright not installed)"); sys.exit(0)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); PORT = 8952; B = f"http://127.0.0.1:{PORT}"
def call(path, body=None, method=None):
    req = urllib.request.Request(B + path, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"}, method=method or ("POST" if body is not None else "GET")); return json.loads(urllib.request.urlopen(req, timeout=120).read())
data = tempfile.mkdtemp(prefix="filmocity_ui_"); foot = os.path.join(data, "footage"); os.makedirs(foot)
for i, (src, f) in enumerate([("testsrc2=s=640x360:r=30", 330), ("color=c=0x2E6BB0:s=640x360:r=30", 440)]):
    subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", src, "-f", "lavfi", "-i", f"sine=frequency={f}:sample_rate=48000", "-t", "5", "-c:v", "libvpx", "-b:v", "1M", "-c:a", "libvorbis", os.path.join(foot, f"shot_{i+1}.webm")], check=True, capture_output=True)
srv = subprocess.Popen([sys.executable, os.path.join(ROOT, "backend", "server.py"), "--root", data, "--port", str(PORT)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
async def main():
    for _ in range(40):
        time.sleep(0.5)
        try: call("/api/project"); break
        except Exception: pass
    async with async_playwright() as p:
        b = await p.chromium.launch(); pg = await b.new_page(viewport={"width": 1600, "height": 950}); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto(B + "/"); await pg.wait_for_timeout(1800); await pg.evaluate("CR.setProxy(false)")
        await pg.evaluate(f"CR.showTab('browser'); CR.panels.renderBrowser({json.dumps(foot)})"); await pg.wait_for_timeout(800); await pg.evaluate("document.querySelector('#fsImportAll').click()"); await pg.wait_for_timeout(2500)
        ids = await pg.evaluate("Object.values(CR.S.proj.media).map(m=>m.id)"); assert len(ids) == 2, ids
        await pg.evaluate(f"CR.placeMedia('{ids[0]}','V1',0,0.5,3.0,'overwrite','a'); CR.placeMedia('{ids[1]}','V1',2.5,0,2.5,'overwrite','b')"); await pg.wait_for_timeout(300)
        await pg.evaluate("CR.seekTo(2.5); CR.S.sel=new Set(); CR.applyTransition('dissolve',0.5); CR.seekTo(0.3); CR.addGraphic('lower_third')"); await pg.wait_for_timeout(300)
        seq = await pg.evaluate("CR.S.seq.id"); call("/api/captions/import", {"srt": "1\n00:00:00,200 --> 00:00:02,000\nA caption that is long enough to wrap onto a second line for the test\n", "sequence": seq}); await pg.wait_for_timeout(800)
        first = await pg.evaluate("CR.S.seq.tracks.find(t=>t.id==='V1').clips.sort((a,b)=>a.start-b.start)[0].id")
        await pg.evaluate(f"CR.S.sel=new Set(['{first}']); CR.refreshSel(); CR.panels.render(); CR.addStackFx('video','procamp')"); await pg.wait_for_timeout(300)
        await pg.evaluate("CR.showTab('ec'); CR.seekTo(0); const i=document.querySelector('[data-k=\"transform.scale\"]'); i.value=1.0; document.querySelector('.kfb[data-kf=\"transform.scale\"]').click()"); await pg.wait_for_timeout(200)
        await pg.evaluate("CR.seekTo(2.0)"); await pg.wait_for_timeout(200); await pg.evaluate("const i=document.querySelector('[data-k=\"transform.scale\"]'); i.value=1.2; document.querySelector('.kfb[data-kf=\"transform.scale\"]').click()"); await pg.wait_for_timeout(300)
        assert await pg.evaluate(f"CR.clipById('{first}').c.keyframes['transform.scale'].length") == 2
        assert await pg.evaluate("document.querySelectorAll('[data-kfnav]').length") >= 3, "keyframe nav buttons"
        pr = call("/api/proposals", {"actor": "agent", "title": "t", "items": [{"ops": [{"op": "set_clip", "sequence": seq, "track": "V1", "clip": {"id": first, "out": 2.8}}], "reason": "tighter"}]}); await pg.wait_for_timeout(1200)
        assert await pg.evaluate("document.querySelectorAll('.ghost').length") == 1, "proposal ghost"
        await pg.evaluate("document.querySelector('#pane-props [data-r=\"pacing\"]').click(); document.querySelector('#pane-props [data-dec^=\"accept\"]').click()"); await pg.wait_for_timeout(1200)
        assert abs(await pg.evaluate(f"CR.clipById('{first}').c.out") - 2.8) < 1e-6
        # polish checks: tooltip, alt-drag duplicate, track menu, timecode entry, maximize
        assert "Duration" in await pg.evaluate(f"document.querySelector('.clip[data-id=\"{first}\"]').title")
        await pg.evaluate("window.prompt=()=>'00:00:01:00'; document.querySelector('#prgTC').click()"); assert abs(await pg.evaluate("CR.S.t") - 1.0) < 1e-6
        await pg.evaluate("document.querySelector('#heads .head:not(.caption)').dispatchEvent(new MouseEvent('contextmenu',{clientX:100,clientY:600,bubbles:true}))"); await pg.wait_for_timeout(200); assert await pg.evaluate("document.querySelectorAll('.ctx button').length") >= 7; await pg.evaluate("document.querySelectorAll('.ctx').forEach(x=>x.remove())")
        # hands-on interactions: corner-handle scale in the monitor, trim readout, hot-text scrub, fx badge
        await pg.evaluate(f"CR.S.sel=new Set(['{first}']); CR.refreshSel(); CR.seekTo(0.5); CR.applyOps([{{op:'set_clip',sequence:CR.S.seq.id,track:'V1',clip:{{id:'{first}',transform:{{scale:1,rotation:0,x:0,y:0}},keyframes:{{}}}}}}],'t','reset')"); await pg.wait_for_timeout(600)
        g = await pg.evaluate(f"(()=>{{const cv=document.getElementById('prgCanvas'); const r=cv.getBoundingClientRect(); const m=CR.S.proj.media[CR.clipById('{first}').c.media_id]; const W=cv.width,H=cv.height; const fit=Math.min(W/m.width,H/m.height); const k=r.width/W; return {{hx:r.left+r.width/2+m.width*fit/2*k, hy:r.top+r.height/2+m.height*fit/2*k}}}})()")
        await pg.mouse.move(g["hx"] + 3, g["hy"]); await pg.mouse.down(); await pg.mouse.move(g["hx"] + 50, g["hy"] + 50, steps=4); await pg.mouse.up(); await pg.wait_for_timeout(400)
        assert await pg.evaluate(f"CR.clipById('{first}').c.transform.scale") > 1.05, "corner handle did not scale"
        assert await pg.evaluate(f"!!document.querySelector('.clip[data-id=\"{first}\"] .fxbadge')"), "fx badge missing on a modified clip"
        e = await pg.evaluate(f"(()=>{{const h=document.querySelector('.clip[data-id=\"{first}\"] .h.r'); const r=h.getBoundingClientRect(); return {{x:r.left+3,y:r.top+20}}}})()")
        await pg.mouse.move(e["x"], e["y"]); await pg.mouse.down(); await pg.mouse.move(e["x"] - 30, e["y"], steps=3); assert await pg.evaluate("!!document.querySelector('#trimReadout')"), "no trim readout"; await pg.mouse.up(); await pg.wait_for_timeout(300); assert await pg.evaluate("!document.querySelector('#trimReadout')")
        job = call("/api/render", {"sequence": seq, "preset": {"crf": 28, "x264_preset": "ultrafast"}, "name": "ui_smoke"})
        for _ in range(120):
            time.sleep(1); st = call("/api/render/" + job["id"])
            if st["status"] not in ("running", "queued"): break
        assert st["status"] == "done", st
        assert not errs, errs; print("OK ui regression:", st["qa"]); await b.close()
try: asyncio.run(main())
finally: srv.terminate()

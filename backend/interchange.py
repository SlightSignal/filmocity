"""Interchange: FCP7 XML (xmeml v4 — opens in Premiere Pro / Resolve), OpenTimelineIO JSON, SRT captions."""
import json, os, math
from xml.sax.saxutils import escape

def _rt(t, fps): return int(round(t * fps))

def to_fcp7_xml(project, sequence_id):
    seq = next(s for s in project["sequences"] if s["id"] == sequence_id); fps = seq["fps"]; tb = int(round(fps)); ntsc = "TRUE" if abs(fps - tb) > 0.01 else "FALSE"
    media = project["media"]; total = max([c["start"] + (c["out"] - c["in_"]) / max(c.get("speed", 1), 1e-6) for t in seq["tracks"] for c in t["clips"]] + [0])
    def rate(): return f"<rate><timebase>{tb}</timebase><ntsc>{ntsc}</ntsc></rate>"
    def file_el(mid):
        m = media[mid]; url = "file://localhost" + m["path"] if not m["path"].startswith("file://") else m["path"]
        return (f"<file id=\"{mid}\"><name>{escape(m.get('name') or os.path.basename(m['path']))}</name><pathurl>{escape(url)}</pathurl>{rate()}<duration>{_rt(m['duration'], fps)}</duration>"
                f"<media>{'<video><samplecharacteristics>'+rate()+f'<width>{m.get('width',0)}</width><height>{m.get('height',0)}</height></samplecharacteristics></video>' if m.get('has_video') else ''}"
                f"{'<audio><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate></samplecharacteristics><channelcount>2</channelcount></audio>' if m.get('has_audio') else ''}</media></file>")
    seen = set()
    def clipitem(c, kind, n):
        m = media.get(c.get("media_id")); dur = (c["out"] - c["in_"]) / max(c.get("speed", 1), 1e-6)
        name = (m.get("name") or os.path.basename(m["path"])) if m else (c.get("title", {}).get("text", "Title"))
        fe = ""
        if m:
            fe = file_el(c["media_id"]) if c["media_id"] not in seen else f"<file id=\"{c['media_id']}\"/>"; seen.add(c["media_id"])
        return (f"<clipitem id=\"{c['id']}_{kind}_{n}\"><name>{escape(name)}</name><enabled>TRUE</enabled><duration>{_rt(dur, fps)}</duration>{rate()}"
                f"<start>{_rt(c['start'], fps)}</start><end>{_rt(c['start'] + dur, fps)}</end><in>{_rt(c['in_'], fps)}</in><out>{_rt(c['out'], fps)}</out>{fe}"
                f"{'<sourcetrack><mediatype>'+('video' if kind=='v' else 'audio')+'</mediatype><trackindex>1</trackindex></sourcetrack>'}</clipitem>")
    vtracks = sorted([t for t in seq["tracks"] if t["kind"] == "video"], key=lambda t: t["index"]); atracks = sorted([t for t in seq["tracks"] if t["kind"] == "audio"], key=lambda t: t["index"])
    vx = "".join("<track>" + "".join(clipitem(c, "v", i) for i, c in enumerate(sorted(t["clips"], key=lambda c: c["start"])) if c.get("media_id")) + "</track>" for t in vtracks)
    # linked audio from video clips goes to A tracks 1..n matching V index; audio-only clips to their own tracks
    linked = "".join("<track>" + "".join(clipitem(c, "a", i) for i, c in enumerate(sorted(t["clips"], key=lambda c: c["start"])) if c.get("media_id") and media[c["media_id"]].get("has_audio") and (c.get("audio") or {}).get("linked", True)) + "</track>" for t in vtracks)
    ax = "".join("<track>" + "".join(clipitem(c, "a", i) for i, c in enumerate(sorted(t["clips"], key=lambda c: c["start"])) if c.get("media_id")) + "</track>" for t in atracks)
    return (f"<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!DOCTYPE xmeml>\n<xmeml version=\"4\"><sequence id=\"{seq['id']}\"><name>{escape(seq['name'])}</name><duration>{_rt(total, fps)}</duration>{rate()}"
            f"<media><video><format><samplecharacteristics>{rate()}<width>{seq['width']}</width><height>{seq['height']}</height><pixelaspectratio>square</pixelaspectratio></samplecharacteristics></format>{vx}</video>"
            f"<audio><format><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate></samplecharacteristics></format>{linked}{ax}</audio></media></sequence></xmeml>")

def to_otio(project, sequence_id):
    seq = next(s for s in project["sequences"] if s["id"] == sequence_id); fps = float(seq["fps"]); media = project["media"]
    def RT(t): return {"OTIO_SCHEMA": "RationalTime.1", "rate": fps, "value": round(t * fps, 3)}
    def TR(s, d): return {"OTIO_SCHEMA": "TimeRange.1", "start_time": RT(s), "duration": RT(d)}
    tracks = []
    for t in sorted(seq["tracks"], key=lambda t: (t["kind"] != "video", t["index"])):
        children = []; cursor = 0.0
        for c in sorted(t["clips"], key=lambda c: c["start"]):
            if c["start"] > cursor + 1e-6: children.append({"OTIO_SCHEMA": "Gap.1", "name": "gap", "source_range": TR(0, c["start"] - cursor)})
            d = (c["out"] - c["in_"]) / max(c.get("speed", 1), 1e-6); m = media.get(c.get("media_id"))
            children.append({"OTIO_SCHEMA": "Clip.1", "name": (m.get("name") or os.path.basename(m["path"])) if m else c.get("title", {}).get("text", "Title"), "source_range": TR(c["in_"], c["out"] - c["in_"]),
                             "media_reference": ({"OTIO_SCHEMA": "ExternalReference.1", "target_url": "file://" + m["path"], "available_range": TR(0, m["duration"])} if m else {"OTIO_SCHEMA": "GeneratorReference.1", "generator_kind": "title", "parameters": c.get("title", {})}),
                             "metadata": {"filmocity": {k: v for k, v in c.items() if k in ("id", "speed", "transform", "transition_in", "transition_out", "audio", "keyframes", "color")}}})
            cursor = c["start"] + d
        tracks.append({"OTIO_SCHEMA": "Track.1", "name": t["id"], "kind": "Video" if t["kind"] == "video" else "Audio", "children": children})
    return {"OTIO_SCHEMA": "Timeline.1", "name": seq["name"], "global_start_time": RT(0), "tracks": {"OTIO_SCHEMA": "Stack.1", "name": "tracks", "children": tracks},
            "metadata": {"filmocity": {"width": seq["width"], "height": seq["height"], "markers": seq.get("markers", []), "captions": seq.get("captions", [])}}}

def _tc(t):
    h = int(t // 3600); m = int(t % 3600 // 60); s = int(t % 60); ms = int(round((t - int(t)) * 1000)); return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def captions_to_srt(caps):
    return "\n".join(f"{i+1}\n{_tc(c['start'])} --> {_tc(c['end'])}\n{c['text']}\n" for i, c in enumerate(sorted(caps, key=lambda c: c["start"])))

def srt_to_captions(text):
    import re, uuid; out = []
    for block in re.split(r"\n\s*\n", text.strip().replace("\r", "")):
        lines = block.strip().splitlines()
        if len(lines) < 2: continue
        mm = re.search(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", block)
        if not mm: continue
        g = list(map(int, mm.groups())); s = g[0]*3600+g[1]*60+g[2]+g[3]/1000; e = g[4]*3600+g[5]*60+g[6]+g[7]/1000
        txt = "\n".join(l for l in lines if "-->" not in l and not re.fullmatch(r"\d+", l.strip()))
        out.append({"id": str(uuid.uuid4())[:8], "start": s, "end": e, "text": txt})
    return out


def from_fcp7_xml(xml_text, import_media):
    """Parse an FCP7 xmeml sequence into Filmocity sequence(s). import_media(path, name) -> media_id or None."""
    import xml.etree.ElementTree as ET, uuid, urllib.parse
    root = ET.fromstring(xml_text); seqs = []
    for sq in root.iter("sequence"):
        if sq.find("media") is None: continue
        tb = float((sq.findtext("rate/timebase") or "30")); ntsc = (sq.findtext("rate/ntsc") or "FALSE").upper() == "TRUE"; fps = tb * (1000 / 1001) if ntsc else tb
        width = int(sq.findtext("media/video/format/samplecharacteristics/width") or 1920); height = int(sq.findtext("media/video/format/samplecharacteristics/height") or 1080)
        seq = {"id": "seq_" + str(uuid.uuid4())[:6], "name": sq.findtext("name") or "Imported", "width": width, "height": height, "fps": round(fps, 3), "duration": None, "markers": [], "tracks": [], "captions": []}
        files = {}
        def file_path(fe):
            if fe is None: return None
            fid = fe.get("id"); url = fe.findtext("pathurl")
            if url: files[fid] = urllib.parse.unquote(url.replace("file://localhost", "").replace("file://", ""))
            return files.get(fid)
        for kind, tag in (("video", "media/video"), ("audio", "media/audio")):
            for ti, tr in enumerate(sq.findall(f"{tag}/track"), start=1):
                clips = []
                for ci in tr.findall("clipitem"):
                    path = file_path(ci.find("file")); name = ci.findtext("name") or (os.path.basename(path) if path else "clip")
                    start, end, i, o = [int(ci.findtext(k) or 0) for k in ("start", "end", "in", "out")]
                    if start < 0 or end <= start: continue
                    mid = import_media(path, name) if path else None
                    if not mid: continue
                    clips.append({"id": str(uuid.uuid4())[:8], "media_id": mid, "start": start / tb, "in_": i / tb, "out": o / tb if o > i else (i + end - start) / tb, "speed": 1.0, "transform": {"x": 0, "y": 0, "scale": 1, "rotation": 0, "opacity": 1}, "audio": {"gain_db": 0, "fade_in": 0, "fade_out": 0, "linked": kind == "video"}, "keyframes": {}, "color": {}})
                if kind == "audio":
                    # audio clipitems that duplicate a video clip's linked audio are skipped (same file, same timing)
                    vclips = [c for t in seq["tracks"] if t["kind"] == "video" for c in t["clips"]]
                    clips = [c for c in clips if not any(v["media_id"] == c["media_id"] and abs(v["start"] - c["start"]) < 1e-3 and abs(v["in_"] - c["in_"]) < 1e-3 for v in vclips)]
                    if not clips and ti > 2: continue
                seq["tracks"].append({"id": ("V" if kind == "video" else "A") + str(ti), "kind": kind, "index": ti, "muted": False, "locked": False, "clips": clips})
        if not any(t["kind"] == "audio" for t in seq["tracks"]): seq["tracks"].append({"id": "A1", "kind": "audio", "index": 1, "muted": False, "locked": False, "clips": []})
        seqs.append(seq)
    return seqs


def to_edl(project, sequence_id, track_id="V1"):
    """CMX3600 EDL for one video track (with linked audio as A1/A2): the exchange format Premiere/Resolve/Avid all read."""
    seq = next(s for s in project["sequences"] if s["id"] == sequence_id); fps = float(seq["fps"]); media = project["media"]
    tr = next((t for t in seq["tracks"] if t["id"] == track_id), None)
    def tc(t): f = int(round(t * fps)); ff = int(round(fps)); return f"{f // (3600*ff):02d}:{f % (3600*ff) // (60*ff):02d}:{f % (60*ff) // ff:02d}:{f % ff:02d}"
    lines = [f"TITLE: {seq['name']}", "FCM: NON-DROP FRAME", ""]; n = 0
    for c in sorted(tr["clips"] if tr else [], key=lambda c: c["start"]):
        m = media.get(c.get("media_id"))
        if not m: continue
        n += 1; reel = "".join(ch for ch in os.path.splitext(m.get("name") or "CLIP")[0].upper() if ch.isalnum())[:8].ljust(8); dur = (c["out"] - c["in_"]) / max(c.get("speed", 1), 1e-6)
        chan = "AA/V" if m.get("has_audio") and (c.get("audio") or {}).get("linked", True) else "V"
        lines.append(f"{n:03d}  {reel} {chan:<5} C        {tc(c['in_'])} {tc(c['out'])} {tc(c['start'])} {tc(c['start'] + dur)}")
        if abs(c.get("speed", 1) - 1) > 1e-6: lines.append(f"M2   {reel}       {fps * c['speed']:.1f}                {tc(c['in_'])}")
        lines.append(f"* FROM CLIP NAME: {m.get('name')}"); lines.append("")
    return "\n".join(lines) + "\n"

def captions_to_vtt(caps):
    def tc(t): h = int(t // 3600); mm = int(t % 3600 // 60); s_ = t % 60; return f"{h:02d}:{mm:02d}:{s_:06.3f}"
    return "WEBVTT\n\n" + "\n".join(f"{tc(c['start'])} --> {tc(c['end'])}\n{c['text']}\n" for c in sorted(caps, key=lambda c: c["start"]))

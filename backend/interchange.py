"""Interchange: FCP7 XML (xmeml v4 — opens in Premiere Pro / Resolve), OpenTimelineIO JSON, SRT captions."""
import json, os, math
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import urlsplit, unquote, quote
from xml.sax.saxutils import escape
from timeline_time import frame_rate, xml_rate, read_xml_rate, to_frames, from_frames, milliseconds, timecode_mode, format_frames, parse_timecode

def _rt(t, fps): return to_frames(t, fps)


def media_uri(path):
    if str(path).startswith('file://'): return str(path)
    if re.match(r'^[A-Za-z]:[\\/]', str(path)) or str(path).startswith(('\\\\', '//')):
        portable = PureWindowsPath(path).as_posix()
        return ('file:' if portable.startswith('//') else 'file:///') + quote(portable, safe='/:')
    # A foreign POSIX source must retain its identity when a project is opened
    # on Windows. WindowsPath.absolute() would silently attach the current drive.
    if str(path).startswith('/'):
        return PurePosixPath(path).as_uri()
    return Path(path).absolute().as_uri()


def media_path(uri):
    parsed = urlsplit(uri)
    if parsed.scheme != 'file' or parsed.query or parsed.fragment: raise ValueError('XML media requires an escaped local file URL')
    path = unquote(parsed.path)
    if parsed.netloc and parsed.netloc.lower() != 'localhost': return '//' + parsed.netloc + path
    if re.match(r'^/[A-Za-z]:/', path): path = path[1:]
    return path

def to_fcp7_xml(project, sequence_id):
    seq = next(s for s in project["sequences"] if s["id"] == sequence_id); fps = frame_rate(seq["fps"]); tb, is_ntsc = xml_rate(fps); ntsc = "TRUE" if is_ntsc else "FALSE"
    if any(c.get('reverse') or c.get('hold') or c.get('time_remap') or c.get('speed', 1) != 1 for track in seq['tracks'] for c in track['clips']):
        raise ValueError('FCP7 XML export does not support retimed clips; render an interchange version first')
    mode = timecode_mode(fps, seq.get('timecode_format', 'ndf'))
    if mode == 'df' and tb != 30:
        raise ValueError('FCP7 XML supports DF display only at 29.97 fps; choose NDF for XML interchange at this rate')
    media = project["media"]; total = max([c["start"] + (c["out"] - c["in_"]) / max(c.get("speed", 1), 1e-6) for t in seq["tracks"] for c in t["clips"]] + [0])
    def rate(): return f"<rate><timebase>{tb}</timebase><ntsc>{ntsc}</ntsc></rate>"
    def file_el(mid):
        m = media[mid]; url = media_uri(m["path"])
        audio_xml = ''
        if m.get('has_audio'):
            sample = m.get('sample_rate'); channels = m.get('channels')
            audio_xml = '<audio><samplecharacteristics>'
            if type(sample) is int and sample > 0: audio_xml += f'<samplerate>{sample}</samplerate>'
            audio_xml += '</samplecharacteristics>'
            if type(channels) is int and channels > 0: audio_xml += f'<channelcount>{channels}</channelcount>'
            audio_xml += '</audio>'
        return (f"<file id=\"{mid}\"><name>{escape(m.get('name') or os.path.basename(m['path']))}</name><pathurl>{escape(url)}</pathurl>{rate()}<duration>{_rt(m['duration'], fps)}</duration>"
                f"<media>{'<video><samplecharacteristics>'+rate()+f'<width>{m.get('width',0)}</width><height>{m.get('height',0)}</height></samplecharacteristics></video>' if m.get('has_video') else ''}"
                f"{audio_xml}</media></file>")
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
            f"<timecode>{rate()}<string>{format_frames(0, fps, mode)}</string><frame>0</frame><displayformat>{mode.upper()}</displayformat></timecode>"
            f"<media><video><format><samplecharacteristics>{rate()}<width>{seq['width']}</width><height>{seq['height']}</height><pixelaspectratio>square</pixelaspectratio></samplecharacteristics></format>{vx}</video>"
            f"<audio><format><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate></samplecharacteristics></format>{linked}{ax}</audio></media></sequence></xmeml>")

def to_otio(project, sequence_id):
    seq = next(s for s in project["sequences"] if s["id"] == sequence_id); fps = float(frame_rate(seq["fps"])); media = project["media"]
    def RT(t): return {"OTIO_SCHEMA": "RationalTime.1", "rate": fps, "value": round(t * fps, 3)}
    def TR(s, d): return {"OTIO_SCHEMA": "TimeRange.1", "start_time": RT(s), "duration": RT(d)}
    tracks = []
    for t in sorted(seq["tracks"], key=lambda t: (t["kind"] != "video", t["index"])):
        children = []; cursor = 0.0
        for c in sorted(t["clips"], key=lambda c: c["start"]):
            if c["start"] > cursor + 1e-6: children.append({"OTIO_SCHEMA": "Gap.1", "name": "gap", "source_range": TR(0, c["start"] - cursor)})
            d = (c["out"] - c["in_"]) / max(c.get("speed", 1), 1e-6); m = media.get(c.get("media_id"))
            children.append({"OTIO_SCHEMA": "Clip.1", "name": (m.get("name") or os.path.basename(m["path"])) if m else c.get("title", {}).get("text", "Title"), "source_range": TR(c["in_"], c["out"] - c["in_"]),
                             "media_reference": ({"OTIO_SCHEMA": "ExternalReference.1", "target_url": media_uri(m["path"]), "available_range": TR(0, m["duration"])} if m else {"OTIO_SCHEMA": "GeneratorReference.1", "generator_kind": "title", "parameters": c.get("title", {})}),
                             "metadata": {"filmocity": {k: v for k, v in c.items() if k in ("id", "speed", "transform", "transition_in", "transition_out", "audio", "keyframes", "color")}}})
            cursor = c["start"] + d
        tracks.append({"OTIO_SCHEMA": "Track.1", "name": t["id"], "kind": "Video" if t["kind"] == "video" else "Audio", "children": children})
    return {"OTIO_SCHEMA": "Timeline.1", "name": seq["name"], "global_start_time": RT(0), "tracks": {"OTIO_SCHEMA": "Stack.1", "name": "tracks", "children": tracks},
            "metadata": {"filmocity": {"width": seq["width"], "height": seq["height"], "markers": seq.get("markers", []), "captions": seq.get("captions", [])}}}

def _tc(t):
    total = milliseconds(t); seconds, ms = divmod(total, 1000); minutes, seconds = divmod(seconds, 60); hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{ms:03d}"

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
    try: root = ET.fromstring(xml_text)
    except ET.ParseError as error: raise ValueError("Invalid FCP7 XML document") from error
    if root.tag != 'xmeml': raise ValueError("Import requires FCP7 XML (xmeml); modern FCPXML is not supported")
    seqs = []
    for sq in root.iter("sequence"):
        if sq.find("media") is None: continue
        fps = read_xml_rate(sq.find("rate"))
        width = int(sq.findtext("media/video/format/samplecharacteristics/width") or 1920); height = int(sq.findtext("media/video/format/samplecharacteristics/height") or 1080)
        seq = {"id": "seq_" + str(uuid.uuid4())[:6], "name": sq.findtext("name") or "Imported", "width": width, "height": height, "fps": float(fps), "duration": None, "markers": [], "tracks": [], "captions": []}
        timecode = sq.find('timecode')
        if timecode is not None:
            display = (timecode.findtext('displayformat') or ('DF' if ';' in (timecode.findtext('string') or '') else 'NDF')).strip().lower()
            timecode_mode(fps, display)
            if display == 'df' and xml_rate(fps)[0] != 30: raise ValueError('FCP7 XML DF display requires 29.97 fps')
            origin = timecode.findtext('string')
            if (origin and parse_timecode(origin, fps) != 0) or int(timecode.findtext('frame') or 0) != 0:
                raise ValueError('Nonzero sequence start timecode is not yet supported; export XML with a zero sequence start timecode')
            if display != 'ndf': seq['timecode_format'] = display
        files = {fe.get("id"): fe.findtext("pathurl") for fe in root.iter("file") if fe.findtext("pathurl")}
        def file_path(fe):
            if fe is None: return None
            fid = fe.get("id"); url = fe.findtext("pathurl")
            if url: files[fid] = url
            return media_path(files[fid]) if files.get(fid) else None
        for kind, tag in (("video", "media/video"), ("audio", "media/audio")):
            for ti, tr in enumerate(sq.findall(f"{tag}/track"), start=1):
                clips = []
                for ci in tr.findall("clipitem"):
                    path = file_path(ci.find("file")); name = ci.findtext("name") or (os.path.basename(path) if path else "clip")
                    start, end, i, o = [int(ci.findtext(k) or 0) for k in ("start", "end", "in", "out")]
                    if start < 0 or end <= start: raise ValueError("XML transition-dependent or invalid clip boundaries are not supported")
                    if int(ci.findtext("mixedratesoffset") or 0) != 0: raise ValueError("XML mixed-rate source offsets need a supported interchange workflow")
                    if i < 0 or o <= i: raise ValueError("XML source boundaries must be nonnegative and increasing")
                    if o - i != end - start or any((n.text or '').lower() == 'timeremap' for n in ci.iter('effectid')):
                        raise ValueError("XML retimed clips are not supported; use a rendered interchange version")
                    mid = import_media(path, name) if path else None
                    if not mid: continue
                    clips.append({"id": str(uuid.uuid4())[:8], "media_id": mid, "start": from_frames(start, fps), "in_": from_frames(i, fps), "out": from_frames(o if o > i else i + end - start, fps), "speed": 1.0, "transform": {"x": 0, "y": 0, "scale": 1, "rotation": 0, "opacity": 1}, "audio": {"gain_db": 0, "fade_in": 0, "fade_out": 0, "linked": kind == "video"}, "keyframes": {}, "color": {}})
                if kind == "audio":
                    # audio clipitems that duplicate a video clip's linked audio are skipped (same file, same timing)
                    vclips = [c for t in seq["tracks"] if t["kind"] == "video" for c in t["clips"]]
                    clips = [c for c in clips if not any(v["media_id"] == c["media_id"] and abs(v["start"] - c["start"]) < 1e-3 and abs(v["in_"] - c["in_"]) < 1e-3 for v in vclips)]
                    if not clips and ti > 2: continue
                seq["tracks"].append({"id": ("V" if kind == "video" else "A") + str(ti), "kind": kind, "index": ti, "muted": False, "locked": False, "clips": clips})
        if not any(t["kind"] == "audio" for t in seq["tracks"]): seq["tracks"].append({"id": "A1", "kind": "audio", "index": 1, "muted": False, "locked": False, "clips": []})
        seqs.append(seq)
    if not seqs: raise ValueError("The XML contains no supported timeline sequences")
    return seqs


def to_edl(project, sequence_id, track_id="V1"):
    """CMX3600 EDL for one video track (with linked audio as A1/A2): the exchange format Premiere/Resolve/Avid all read."""
    seq = next(s for s in project["sequences"] if s["id"] == sequence_id); fps = float(frame_rate(seq["fps"])); media = project["media"]
    tr = next((t for t in seq["tracks"] if t["id"] == track_id), None)
    if tr is None: raise ValueError('EDL track not found')
    mode = timecode_mode(fps, seq.get('timecode_format', 'ndf'))
    def tc(t): return format_frames(to_frames(t, fps), fps, mode)
    lines = [f"TITLE: {seq['name']}", f"FCM: {'DROP FRAME' if mode == 'df' else 'NON-DROP FRAME'}", ""]; n = 0
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
    def tc(t): return _tc(t).replace(",", ".")
    return "WEBVTT\n\n" + "\n".join(f"{tc(c['start'])} --> {tc(c['end'])}\n{c['text']}\n" for c in sorted(caps, key=lambda c: c["start"]))

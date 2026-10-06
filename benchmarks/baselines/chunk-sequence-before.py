"""Frozen production chunk slicer before selection indexing."""
import json
from render import clip_dur, _shift_kf

def chunk_sequence(seq, t0, t1):
    """The part of `seq` between t0 and t1, re-based to start at 0, with every clip clipped to the window."""
    ch = json.loads(json.dumps(seq)); ch["in_point"] = None; ch["out_point"] = None; ch["markers"] = []
    for t in ch["tracks"]:
        keep = []
        for c in t["clips"]:
            d = clip_dur(c); s0, s1 = c["start"], c["start"] + d
            if s1 <= t0 + 1e-6 or s0 >= t1 - 1e-6: continue
            sp = c.get("speed", 1.0); cut_head = max(0.0, t0 - s0); cut_tail = max(0.0, s1 - t1)
            if cut_head > 0:
                c["in_"] = c["in_"] + cut_head * sp; c["start"] = 0.0; c["transition_in"] = None; c["audio_transition_in"] = None; c["keyframes"] = _shift_kf(c.get("keyframes"), -cut_head)
                if c.get("audio") and c["audio"].get("fade_in"): c["audio"]["fade_in"] = 0
            else: c["start"] = round(s0 - t0, 5)
            if cut_tail > 0:
                c["out"] = c["out"] - cut_tail * sp; c["transition_out"] = None; c["audio_transition_out"] = None
                if c.get("audio") and c["audio"].get("fade_out"): c["audio"]["fade_out"] = 0
            keep.append(c)
        t["clips"] = keep
    ch["captions"] = [dict(cp, start=round(max(0.0, cp["start"] - t0), 4), end=round(min(t1, cp["end"]) - t0, 4)) for cp in (seq.get("captions") or []) if cp["end"] > t0 and cp["start"] < t1]
    return ch

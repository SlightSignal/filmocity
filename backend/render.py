"""Filmocity render engine v0.2: project JSON -> FFmpeg filter graph -> encoded file. Deterministic and headless.
Adds over v0.1: keyframes (position, scale, opacity, volume; linear), transition types (dissolve, dip_black, dip_white,
wipe_left/right, push_left/right), per-clip color (exposure/contrast/saturation/temperature/tint/highlights/shadows/LUT),
captions track (burned in), sequence in/out export range, track gain/solo, image media (stills), title borders.
"""
import json, os, shlex, subprocess, math, tempfile, hashlib, threading
from shutil import which as shutil_which
from effects import video_stack_chain, audio_stack_chain, stabilize_params, drop_shadow_params, track_matte_params, hsl_secondary_params

_TMP = tempfile.mkdtemp(prefix="filmocity_txt_")
ASSET_FONTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")
def ffpath(p):
    """A filesystem path inside a filter option: forward slashes (FFmpeg accepts them on Windows) and escaped drive colons/quotes."""
    return str(p).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
def bundled_font(mono=False, bold=True):
    name = "DejaVuSansMono-Bold.ttf" if mono else ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"); p = os.path.join(ASSET_FONTS, name)
    return p if os.path.exists(p) else next((f for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf", "/Library/Fonts/Arial Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf") if os.path.exists(f)), "")
def textfile(text):
    """drawtext cannot take arbitrary text safely inline (quote/colon/percent rules); write it to a file and use textfile=."""
    p = os.path.join(_TMP, hashlib.sha1(str(text).encode()).hexdigest()[:16] + ".txt")
    if not os.path.exists(p): open(p, "w", encoding="utf-8").write(str(text))
    return f"textfile='{ffpath(p)}'"

def _esc(s):
    r"""Escape text for a single-quoted drawtext value: close-quote/escape/reopen for apostrophes; \: for colons; \% for percent."""
    return str(s).replace("'", "'\\''").replace(":", "\\:").replace("%", "\\%")
def remap_segments(c):
    """Speed keyframes [{t (timeline offset), v (speed), e}] -> segments [(t0, t1|None, s0, s1, sigma0)] with source offset at t0."""
    k = sorted(c.get("time_remap") or [], key=lambda x: x["t"]); segs = []; sigma = 0.0; t_prev = 0.0; s_prev = k[0]["v"] if k else 1.0
    if k and k[0]["t"] > 0: segs.append((0.0, k[0]["t"], k[0]["v"], k[0]["v"], 0.0)); sigma += k[0]["v"] * k[0]["t"]; t_prev = k[0]["t"]
    for i, kf in enumerate(k):
        if i == len(k) - 1: segs.append((kf["t"], None, kf["v"], kf["v"], sigma)); break
        nxt = k[i + 1]; a, b = kf["v"], (kf["v"] if kf.get("e") == "hold" else nxt["v"]); d = nxt["t"] - kf["t"]
        segs.append((kf["t"], nxt["t"], a, b, sigma)); sigma += d * (a + b) / 2
    return segs or [(0.0, None, 1.0, 1.0, 0.0)]

def remap_duration(c):
    need = c["out"] - c["in_"]
    for (t0, t1, a, b, sig) in remap_segments(c):
        if t1 is None: return t0 + (need - sig) / max(a, 1e-6)
        gain = (t1 - t0) * (a + b) / 2
        if sig + gain >= need:  # ends inside this segment: solve quadratic for x
            d = t1 - t0; rem = need - sig
            if abs(b - a) < 1e-9: return t0 + rem / max(a, 1e-6)
            k = (b - a) / d; return t0 + (-a + math.sqrt(max(a * a + 2 * k * rem, 0))) / k
    return need

def remap_setpts_expr(c):
    """Output timeline offset as a function of source offset T (seconds after the trim), inverse of the speed integral."""
    parts = []; segs = remap_segments(c)
    def seg_expr(t0, t1, a, b, sig):
        if t1 is None or abs(b - a) < 1e-9: return f"({t0}+(T-{sig:.6f})/{max(a, 1e-6):.6f})"
        k = (b - a) / (t1 - t0); return f"({t0}+(-{a:.6f}+sqrt(max({a*a:.6f}+2*{k:.6f}*(T-{sig:.6f}),0)))/{k:.6f})"
    expr = seg_expr(*segs[-1])
    for (t0, t1, a, b, sig) in reversed(segs[:-1]):
        gain = (t1 - t0) * (a + b) / 2; expr = f"if(lt(T,{sig + gain:.6f}),{seg_expr(t0, t1, a, b, sig)},{expr})"
    return expr

def remap_avg_speed(c): return max((c["out"] - c["in_"]) / max(remap_duration(c), 1e-6), 1e-6)

def caption_words_layout(text, W, H, cs, start, end, transcript=None):
    """Lay out a caption block word by word with the render font (PIL metrics), wrapped like wrap_caption, centred; each word gets its
    on-screen box and a time window (from the transcript when it covers the block, else evenly across the block)."""
    from PIL import ImageFont
    size = int(cs.get("size", H * 0.032)); font = ImageFont.truetype(font_file(cs.get("font")), size); words = str(text).replace("\n", " ").split()
    if not words: return []
    space = font.getlength(" "); maxw = W * 0.88; lines, cur, curw = [], [], 0.0
    for w in words:
        ww = font.getlength(w)
        if cur and curw + space + ww > maxw: lines.append(cur); cur, curw = [w], ww
        else: cur.append(w); curw = curw + (space if cur[:-1] else 0) + ww
    if cur: lines.append(cur)
    lines = lines[:3]; lh = size * 1.15; total_h = lh * len(lines); y_top = H * float(cs.get("y", 0.74)) - total_h / 2; out = []
    flat = [w for ln in lines for w in ln]; n = len(flat)
    # word timings
    tw = None
    if transcript:
        inside = [w for w in transcript if w.get("s") is not None and w["s"] >= start - 0.05 and w["e"] <= end + 0.05]
        if len(inside) == n: tw = [(x["s"], x["e"]) for x in inside]
    if tw is None: tw = [(start + (end - start) * i / n, start + (end - start) * (i + 1) / n) for i in range(n)]
    k = 0
    for li, ln in enumerate(lines):
        widths = [font.getlength(w) for w in ln]; lw = sum(widths) + space * (len(ln) - 1); x = (W - lw) / 2; y = y_top + li * lh
        for w, ww in zip(ln, widths):
            out.append({"text": w, "x": round(x), "y": round(y), "w": round(ww), "s": tw[k][0], "e": tw[k][1]}); x += ww + space; k += 1
    return out

def wrap_caption(text, W, size):
    """Wrap caption text to the safe width (≈ 88% of the frame) unless it already has line breaks; caps at 3 lines like broadcast captions."""
    if "\n" in text: return text
    max_chars = max(12, int((W * 0.88) / (size * 0.56))); words = text.split(); lines = []; cur = ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > max_chars: lines.append(cur); cur = w
        else: cur = (cur + " " + w).strip()
    if cur: lines.append(cur)
    return "\n".join(lines[:3])

def clip_dur(c):
    if c.get("hold"): return max(c["out"] - c["in_"], 1.0 / 30)
    if c.get("time_remap"): return remap_duration(c)
    return (c["out"] - c["in_"]) / max(c.get("speed", 1.0), 1e-6)

def _bezier_y(k0, k1, t):
    """Cubic bezier between keyframes with out-handle k0.o=[ox,oy] and in-handle k1.i=[ix,iy] (x as fraction of the segment); solve x(u)=t by bisection."""
    t0, v0, t1, v1 = k0["t"], k0["v"], k1["t"], k1["v"]; d = max(t1 - t0, 1e-6)
    ox, oy = (k0.get("o") or [0.33, 0.0]); ix, iy = (k1.get("i") or [0.33, 0.0])
    p1x, p1y, p2x, p2y = t0 + max(0, min(1, ox)) * d, v0 + oy, t1 - max(0, min(1, ix)) * d, v1 + iy
    lo, hi = 0.0, 1.0
    for _ in range(40):
        u = (lo + hi) / 2; x = (1-u)**3*t0 + 3*(1-u)**2*u*p1x + 3*(1-u)*u*u*p2x + u**3*t1
        if x < t: lo = u
        else: hi = u
    u = (lo + hi) / 2
    return (1-u)**3*v0 + 3*(1-u)**2*u*p1y + 3*(1-u)*u*u*p2y + u**3*v1

def bake_keyframes(kfs, samples=12):
    """Turn eased/bezier segments into linear sub-keyframes so FFmpeg expressions stay simple and fast."""
    k = sorted(kfs, key=lambda x: x["t"]); out = []
    for i, kf in enumerate(k):
        e = kf.get("e", "linear"); out.append({"t": kf["t"], "v": kf["v"], "e": "hold" if e == "hold" else "linear"})
        if i < len(k) - 1 and e in ("ease", "ease_in", "ease_out", "bezier"):
            t0, t1 = kf["t"], k[i + 1]["t"]
            for j in range(1, samples):
                tt = t0 + (t1 - t0) * j / samples
                out.append({"t": tt, "v": kf_eval([kf, k[i + 1]], tt), "e": "linear"})
    return out

def kf_expr(kfs, var="t", offset=0.0, default=0.0):
    """Piecewise-linear FFmpeg expression for keyframes [{t,v}] (t clip-relative); `offset` shifts to timeline time."""
    if not kfs: return f"{default}"
    k = bake_keyframes(kfs)
    if len(k) == 1: return f"{k[0]['v']}"
    def seg(i):
        if i >= len(k) - 1: return f"{k[-1]['v']}"
        t0, v0, t1, v1 = k[i]["t"] + offset, k[i]["v"], k[i + 1]["t"] + offset, k[i + 1]["v"]; e = k[i].get("e", "linear")
        p = f"(({var}-{t0})/{max(t1 - t0, 1e-6)})"
        if e == "hold": val = f"{v0}"
        elif e == "ease": val = f"({v0}+({v1}-{v0})*({p}*{p}*(3-2*{p})))"
        elif e == "ease_in": val = f"({v0}+({v1}-{v0})*({p}*{p}))"
        elif e == "ease_out": val = f"({v0}+({v1}-{v0})*(1-(1-{p})*(1-{p})))"
        else: val = f"({v0}+({v1}-{v0})*{p})"
        return f"if(lt({var},{t1}),{val},{seg(i + 1)})"
    return f"if(lt({var},{k[0]['t'] + offset}),{k[0]['v']},{seg(0)})"

def kf_eval(kfs, t):
    k = sorted(kfs, key=lambda x: x["t"])
    if t <= k[0]["t"]: return k[0]["v"]
    for i in range(len(k) - 1):
        if t < k[i + 1]["t"]:
            p = (t - k[i]["t"]) / max(k[i + 1]["t"] - k[i]["t"], 1e-6); e = k[i].get("e", "linear")
            if e == "hold": return k[i]["v"]
            if e == "bezier": return _bezier_y(k[i], k[i + 1], t)
            if e == "ease": p = p * p * (3 - 2 * p)
            elif e == "ease_in": p = p * p
            elif e == "ease_out": p = 1 - (1 - p) * (1 - p)
            return k[i]["v"] + (k[i + 1]["v"] - k[i]["v"]) * p
    return k[-1]["v"]

def opacity_cmds(kfs, dur, fps, target="colorchannelmixer"):
    """sendcmd command file stepping one named colorchannelmixer instance's alpha every frame (cheap; geq is ~10x slower).
    sendcmd addresses filters by name, so the target must be a unique instance id (filter@id) or every mixer in the graph would move."""
    key = hashlib.sha1(json.dumps([kfs, round(dur, 3), fps, target]).encode()).hexdigest()[:16]; p = os.path.join(_TMP, f"op_{key}.cmd")
    if not os.path.exists(p):
        n = int(dur * fps) + 1
        open(p, "w").write("\n".join(f"{i / fps:.4f} {target} aa {max(0.0, min(1.0, kf_eval(kfs, i / fps))):.4f};" for i in range(n)))
    return p

_FONT_MAP = {}
def user_font_file(family, weight="bold"):
    """Resolve a family name to a user-installed font file (data/fonts), preferring a Bold style for bold weight."""
    global _FONT_MAP
    root = os.environ.get("FILMOCITY_DATA") or os.path.expanduser("~/filmocity_data"); d = os.path.join(root, "fonts")
    if not os.path.isdir(d): return None
    if not _FONT_MAP:
        from PIL import ImageFont
        for fn in os.listdir(d):
            if not fn.lower().endswith((".ttf", ".otf", ".ttc")): continue
            try: fam, sty = ImageFont.truetype(os.path.join(d, fn), 24).getname()
            except Exception: continue
            _FONT_MAP.setdefault(fam.lower(), []).append((sty.lower(), os.path.join(d, fn)))
    cands = _FONT_MAP.get(str(family).lower())
    if not cands: return None
    want_bold = weight != "regular"
    for sty, pth in cands:
        if ("bold" in sty) == want_bold: return pth
    return cands[0][1]

_FONTS = None
def font_file(family=None, weight="bold"):
    """Resolve a font family to a file via fontconfig (fc-match); falls back to DejaVu Sans Bold."""
    global _FONTS
    fallback = bundled_font(bold=(weight != "regular"))
    if not family: return fallback
    if os.path.exists(family): return family
    uf = user_font_file(family, weight)
    if uf: return uf
    if os.name == "nt" and shutil_which("fc-match") is None:  # no fontconfig: look in the Windows fonts folder by family name
        fdir = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts"); cand = [f for f in (os.listdir(fdir) if os.path.isdir(fdir) else []) if f.lower().replace(" ", "").startswith(family.lower().replace(" ", "")[:6])]
        pref = [f for f in cand if ("bd" in f.lower() or "bold" in f.lower())] if weight != "regular" else [f for f in cand if "bd" not in f.lower() and "bold" not in f.lower()]
        if pref or cand: return os.path.join(fdir, (pref or cand)[0])
    try:
        r = subprocess.run(["fc-match", "-f", "%{file}", f"{family}:weight={weight}"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip() and os.path.exists(r.stdout.strip()): return r.stdout.strip()
    except Exception: pass
    return fallback

def _vertical(text): return "\n".join(ch for ch in str(text).replace("\n", " "))
def drawtext_opts(t, W, H, default_size):
    if t.get("uppercase"): t = dict(t, text=str(t.get("text", "")).upper())
    if t.get("vertical"): t = dict(t, text=_vertical(t.get("text", "")), line_spacing=t.get("line_spacing", -int(0.15 * float(t.get("size", default_size)))))
    """Shared drawtext option builder for titles/graphics: font, size, color, align, box, shadow, border, line spacing, position."""
    size = int(t.get("size", default_size)); align = t.get("align", "center"); x = int(t.get("x", 0)); y = int(t.get("y", 0)) + int(t.get("baseline_dy", 0) or 0)  # baseline_dy: drawtext aligns glyph tops; word layers carry a per-word correction
    anchor = t.get("anchor", "center")  # center | top_left | bottom_left | bottom_center | top_center
    xs = {"center": f"(w-text_w)/2+{x}", "left": f"{W * 0.06:.0f}+{x}", "right": f"w-text_w-{W * 0.06:.0f}+{x}"}[align if align in ("center", "left", "right") else "center"]
    ys = {"center": f"(h-text_h)/2+{y}", "top": f"{H * 0.08:.0f}+{y}", "bottom": f"h-text_h-{H * 0.12:.0f}+{y}"}[t.get("valign", "center") if t.get("valign") in ("center", "top", "bottom") else "center"]
    # A LITERAL PERCENT IN A TITLE KILLED THE RENDER. textfile= solves quoting
    # but NOT expansion: drawtext still parses % in the file's contents, so the
    # card "175 Nm - 42% grade" died with "Stray % near ' grade'" and the whole
    # job errored with nothing written. Percentages are ordinary ad copy, so the
    # default has to be literal. expansion is switched off only when the text
    # asks for none of it -- the two places that genuinely use %{...} (the
    # counting leader and the burn-in timecode) build drawtext inline with
    # text=, never through here, so nothing loses a feature it was using.
    _txt = str(t.get("text", "Title"))
    o = [textfile(_txt)] + ([] if "%{" in _txt else ["expansion=none"]) + [
         f"fontsize={size}", f"fontcolor={t.get('color', 'white')}", f"x={xs}", f"y={ys}", f"fontfile='{ffpath(font_file(t.get('font'), t.get('weight', 'bold')))}'",
         f"line_spacing={int(t.get('line_spacing', size * 0.15))}", f"borderw={int(t.get('borderw', 0))}", f"bordercolor={t.get('bordercolor', 'black')}"]
    if t.get("box"): o += ["box=1", f"boxcolor={t.get('boxcolor', 'black@0.6')}", f"boxborderw={int(t.get('boxpad', size * 0.35))}"]
    if t.get("shadow"): o += [f"shadowx={int(t.get('shadowx', size * 0.04))}", f"shadowy={int(t.get('shadowy', size * 0.04))}", f"shadowcolor={t.get('shadowcolor', 'black@0.6')}"]
    if t.get("letter_spacing"): pass  # drawtext has no tracking; kept in the model for the preview
    return "drawtext=" + ":".join(o)

def shape_png(L, W, H):
    """Rasterize a shape layer {kind:'shape', shape:'rect'|'ellipse', x,y,w,h (fractions), color (#hex or name), opacity, radius, stroke, stroke_color} to RGBA PNG (cached)."""
    from PIL import Image, ImageDraw
    key = hashlib.sha1(json.dumps([L, W, H], sort_keys=True).encode()).hexdigest()[:16]; p = os.path.join(_TMP, f"shape_{key}.png")
    if os.path.exists(p): return p
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    x0, y0 = float(L.get("x", 0.1)) * W, float(L.get("y", 0.1)) * H; x1, y1 = x0 + float(L.get("w", 0.3)) * W, y0 + float(L.get("h", 0.2)) * H
    col = _rgba(L.get("color", "#ffffff"), float(L.get("opacity", 1.0))); stroke = int(L.get("stroke", 0)); sc = _rgba(L.get("stroke_color", "#000000"), 1.0) if stroke else None
    def draw_shape(dd, fill, outline, width, dx=0, dy=0):
        if L.get("shape") == "line": dd.line([(float(L.get("x1", 0.1)) * W + dx, float(L.get("y1", 0.5)) * H + dy), (float(L.get("x2", 0.9)) * W + dx, float(L.get("y2", 0.5)) * H + dy)], fill=fill or outline, width=max(1, int(L.get("stroke", 6)))); return
        if L.get("shape") == "arrow":
            import math as _m; x1_, y1_, x2_, y2_ = float(L.get("x1", 0.2)) * W, float(L.get("y1", 0.5)) * H, float(L.get("x2", 0.8)) * W, float(L.get("y2", 0.5)) * H; ang = _m.atan2(y2_ - y1_, x2_ - x1_); hl = max(12, int(L.get("head", 40))); sw_ = max(2, int(L.get("stroke", 10)))
            dd.line([(x1_ + dx, y1_ + dy), (x2_ - _m.cos(ang) * hl * 0.6 + dx, y2_ - _m.sin(ang) * hl * 0.6 + dy)], fill=fill or outline, width=sw_)
            dd.polygon([(x2_ + dx, y2_ + dy), (x2_ - _m.cos(ang - 0.45) * hl + dx, y2_ - _m.sin(ang - 0.45) * hl + dy), (x2_ - _m.cos(ang + 0.45) * hl + dx, y2_ - _m.sin(ang + 0.45) * hl + dy)], fill=fill or outline); return
        if L.get("shape") == "ellipse": dd.ellipse([x0 + dx, y0 + dy, x1 + dx, y1 + dy], fill=fill, outline=outline, width=width)
        elif L.get("shape") == "polygon" and L.get("points"): dd.polygon([(float(px) * W + dx, float(py) * H + dy) for px, py in L["points"]], fill=fill, outline=outline, width=width)
        else:
            r = int(L.get("radius", 0))
            if r > 0: dd.rounded_rectangle([x0 + dx, y0 + dy, x1 + dx, y1 + dy], radius=r, fill=fill, outline=outline, width=width)
            else: dd.rectangle([x0 + dx, y0 + dy, x1 + dx, y1 + dy], fill=fill, outline=outline, width=width)
    if L.get("shadow"):  # soft drop shadow
        from PIL import ImageFilter
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0)); draw_shape(ImageDraw.Draw(sh), _rgba(L.get("shadow_color", "#000000"), float(L.get("shadow_opacity", 0.5))), None, 0, int(L.get("shadow_x", 8)), int(L.get("shadow_y", 12)))
        im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(int(L.get("shadow_blur", 14)))))
    g = L.get("gradient")
    if g and g.get("to"):  # linear gradient fill: from → to along angle (degrees, 0 = left→right, 90 = top→bottom), masked by the shape
        import math as _m
        c1, c2 = _rgba(L.get("color", g.get("from", "#ffffff")), float(L.get("opacity", 1.0))), _rgba(g["to"], float(g.get("to_opacity", L.get("opacity", 1.0))))
        ang = _m.radians(float(g.get("angle", 0))); gx, gy = _m.cos(ang), _m.sin(ang); bw, bh = max(1, int(x1 - x0)), max(1, int(y1 - y0))
        grad = Image.new("RGBA", (bw, bh)); px = grad.load(); span = abs(gx) * bw + abs(gy) * bh or 1
        for yy in range(bh):
            for xx in range(bw):
                t = ((xx if gx >= 0 else bw - xx) * abs(gx) + (yy if gy >= 0 else bh - yy) * abs(gy)) / span; px[xx, yy] = tuple(int(c1[k] + (c2[k] - c1[k]) * t) for k in range(4))
        mask = Image.new("L", (W, H), 0); draw_shape(ImageDraw.Draw(mask), 255, None, 0); layer = Image.new("RGBA", (W, H), (0, 0, 0, 0)); layer.paste(grad, (int(x0), int(y0))); im.paste(layer, (0, 0), mask)
        if stroke: draw_shape(d, None, sc, stroke)
    else: draw_shape(d, col, sc, stroke)
    im.save(p); return p

def _rgba(c, a=1.0):
    from PIL import ImageColor
    c = str(c)
    if "@" in c: c, a2 = c.split("@"); a = a * float(a2)
    if c.startswith("0x"): c = "#" + c[2:]
    try: r, g, b = ImageColor.getrgb(c)[:3]
    except Exception: r, g, b = 255, 255, 255
    return (r, g, b, int(max(0, min(1, a)) * 255))

EASE = {"linear": "({p})", "ease_out": "(1-pow(1-({p}),3))", "ease_in": "pow(({p}),3)", "ease_in_out": "if(lt(({p}),0.5),4*pow(({p}),3),1-pow(-2*({p})+2,3)/2)", "back_out": "(1+2.70158*pow(({p})-1,3)+1.70158*pow(({p})-1,2))", "bounce": "(1-abs(cos(({p})*3.1416*1.5))*(1-({p})))"}
def _ease(kind, p): return EASE.get(kind or "ease_out", EASE["ease_out"]).replace("{p}", p)
def anim_progress(an, cd, out=False):
    """Progress expression 0→1 in `t` for an in/out animation {duration, delay, ease}."""
    d = max(0.05, float(an.get("duration", 0.6) or 0.6)); dl = float(an.get("delay", 0) or 0)
    if out: p = f"clip((t-({cd - d - dl:.4f}))/{d:.4f},0,1)"
    else: p = f"clip((t-{dl:.4f})/{d:.4f},0,1)"
    return _ease(an.get("ease"), p), d, dl

def ease_val(kind, p):
    p = max(0.0, min(1.0, p))
    if kind == "linear": return p
    if kind == "ease_in": return p ** 3
    if kind == "ease_in_out": return 4 * p ** 3 if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2
    if kind == "back_out": return 1 + 2.70158 * (p - 1) ** 3 + 1.70158 * (p - 1) ** 2
    if kind == "bounce": return 1 - abs(math.cos(p * math.pi * 1.5)) * (1 - p)
    return 1 - (1 - p) ** 3  # ease_out

def scale_cmds(s0, an, cd, FPS, is_out, target="scale"):
    """sendcmd file animating one named scale instance from s0→1 (in) or 1→s0 (out) with the animation's easing."""
    d = max(0.05, float(an.get("duration", 0.6) or 0.6)); dl = float(an.get("delay", 0) or 0); n = int(math.ceil(cd * FPS)) + 1; lines = []
    for i in range(n):
        t = i / FPS; p = (t - (cd - d - dl)) / d if is_out else (t - dl) / d; q = 1 - ease_val(an.get("ease"), p) if is_out else ease_val(an.get("ease"), p); sc = s0 + (1 - s0) * max(0.0, min(1.0, q))
        lines.append(f"{t:.4f} {target} w iw*{sc:.4f}, {target} h ih*{sc:.4f};")
    p = os.path.join(_TMP, hashlib.sha1(("\n".join(lines)).encode()).hexdigest()[:16] + ".cmd"); open(p, "w").write("\n".join(lines) + "\n"); return ffpath(p)

def layer_stream(L, W, H, cd, FPS):
    """One graphics layer → filter chain producing a WxH transparent stream, with per-layer opacity/scale/rotation and in/out animation."""
    kind = L.get("kind"); ch = [f"color=c=black@0.0:s={W}x{H}:r={FPS}:d={cd:.3f},format=yuva420p"]
    if kind == "shape": ch.append(f"__SHAPE__{shape_png(L, W, H)}__")
    elif kind == "image" and L.get("path"): ch.append(f"__IMAGE__{L['path']}__")
    elif kind == "box":
        # replace=1 is load-bearing, not a tweak. The layer base is color=black@0.0 -- fully
        # transparent -- and drawbox defaults to replace=false, which writes the colour planes
        # and LEAVES ALPHA ALONE. So every box was painted onto pixels that stayed 100%
        # transparent, and the overlay contributed nothing: a full-frame pure-red box rendered
        # to the byte-identical frame as no box at all. drawtext writes alpha, which is why
        # text layers always worked and this went unnoticed.
        x, y, w, h = int(float(L.get("x", 0)) * W), int(float(L.get("y", 0)) * H), int(float(L.get("w", 1)) * W), int(float(L.get("h", 0.1)) * H); ch.append(f"drawbox=x={x}:y={y}:w={w}:h={h}:color={L.get('color', 'white@0.9')}:t=fill:replace=1")
    elif kind == "text":
        ai = L.get("anim_in") or {}
        if ai.get("type") == "typewriter":  # progressive substrings, each drawn during its own window
            txt = str(L.get("text", "")); n = max(1, len(txt)); d = max(0.1, float(ai.get("duration", 1.0) or 1.0)); dl = float(ai.get("delay", 0) or 0)
            for i in range(1, n + 1):
                t0 = dl + d * (i - 1) / n; t1 = dl + d * i / n if i < n else cd + 1
                ch.append(drawtext_opts(dict(L, text=txt[:i]), W, H, H * 0.05) + f":enable='between(t,{t0:.4f},{t1:.4f})'")
        else: ch.append(drawtext_opts(L, W, H, H * 0.05))
        if L.get("glow"): ch.append(f"split[gl_a][gl_b];[gl_b]gblur=sigma={float(L.get('glow_size', 18)):.1f},colorchannelmixer=aa={float(L.get('glow_opacity', 0.8)):.2f}[gl_c];[gl_c][gl_a]overlay=x=0:y=0:format=rgb,format=yuva420p")
    # static per-layer transform
    sc = float(L.get("scale", 1) or 1); rot = float(L.get("rotation", 0) or 0); op = float(L.get("opacity", 1) if L.get("opacity") is not None else 1)
    if abs(sc - 1) > 1e-3: ch.append(f"scale=iw*{sc:.4f}:ih*{sc:.4f},pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black@0.0")
    if abs(rot) > 1e-3: ch.append(f"rotate={math.radians(rot):.5f}:c=black@0.0:ow={W}:oh={H}")
    if op < 0.999: ch.append(f"colorchannelmixer=aa={op:.3f}")
    if L.get("blur"): ch.append(f"gblur=sigma={float(L['blur']):.1f}")
    # animated in/out — cheap filters only: fade (alpha) for opacity, pad+crop for motion, scale=eval=frame for pop/zoom, geq only inside the wipe window
    for an, is_out in ((L.get("anim_in") or {}, False), (L.get("anim_out") or {}, True)):
        ty = an.get("type")
        if not ty or ty == "none" or (ty == "typewriter" and not is_out): continue
        P, d, dl = anim_progress(an, cd, is_out); q = f"(1-{P})" if is_out else P; qT = q.replace("(t-", "(T-")   # q: 1 = fully shown; geq uses T
        t0 = (cd - d - dl) if is_out else dl; t1 = t0 + d; win = f":enable='between(t,{max(0, t0 - 0.02):.4f},{t1 + 0.02:.4f})'"
        dist = float(an.get("distance", 0.25) or 0.25)
        if ty in ("fade", "rise", "drop", "blur_in", "blur_out", "rotate_in", "zoom", "pop"):
            ch.append(f"fade=t={'out' if is_out else 'in'}:st={t0:.4f}:d={d:.4f}:alpha=1")
        if ty in ("slide_left", "slide_right", "slide_up", "slide_down", "rise", "drop"):
            dx = {"slide_left": f"-{W * dist:.1f}*(1-{q})", "slide_right": f"{W * dist:.1f}*(1-{q})"}.get(ty, "0"); dy = {"slide_up": f"{H * dist:.1f}*(1-{q})", "rise": f"{H * 0.06:.1f}*(1-{q})", "slide_down": f"-{H * dist:.1f}*(1-{q})", "drop": f"-{H * 0.06:.1f}*(1-{q})"}.get(ty, "0")
            ch.append(f"pad={W * 3}:{H * 3}:{W}:{H}:color=black@0.0,crop={W}:{H}:x='{W}-({dx})':y='{H}-({dy})':exact=1")
        if ty in ("pop", "zoom"):
            s0 = 0.6 if ty == "pop" else 1.25; K = max(1.0, s0); tag = f"an{'o' if is_out else 'i'}_{hashlib.sha1(json.dumps([L.get('text'), L.get('kind'), an, is_out]).encode()).hexdigest()[:6]}"
            ch.append(f"sendcmd=f='{scale_cmds(s0, an, cd, FPS, is_out, f'scale@{tag}')}',scale@{tag}=w=iw:h=ih:eval=frame,pad={int(W * K)}:{int(H * K)}:(ow-iw)/2:(oh-ih)/2:color=black@0.0,crop={W}:{H}:(in_w-{W})/2:(in_h-{H})/2")
        if ty in ("wipe_left", "wipe_right", "wipe_up", "wipe_down"):
            cond = {"wipe_left": f"lt(X,W*{qT})", "wipe_right": f"gt(X,W*(1-{qT}))", "wipe_up": f"gt(Y,H*(1-{qT}))", "wipe_down": f"lt(Y,H*{qT})"}[ty]
            ch.append(f"format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='if({cond},alpha(X,Y),0)'{win},format=yuva420p")
        if ty == "rotate_in": ch.append(f"rotate=a='-0.35*(1-{q})':c=black@0.0:ow={W}:oh={H}{win}")
    return [x for x in ch if x]

def layer_kf_chain(kf, li, W, H, cd, FPS):
    """Keyframed layer motion: position via pad+crop expressions, opacity/scale via per-frame sendcmd, rotation via expression."""
    ch = []; kx, ky, ko, ks, kr = (kf.get(f"g{li}.{k}") for k in ("x", "y", "opacity", "scale", "rotation"))
    if kx or ky:
        ex = kf_expr(kx) if kx else "0"; ey = kf_expr(ky) if ky else "0"
        ch.append(f"pad={W * 3}:{H * 3}:{W}:{H}:color=black@0.0,crop={W}:{H}:x='{W}-({ex})':y='{H}-({ey})':exact=1")
    if ks:
        tag = f"lsc{li}_{hashlib.sha1(json.dumps(ks).encode()).hexdigest()[:6]}"; n = int(math.ceil(cd * FPS)) + 1; lines = [f"{i / FPS:.4f} scale@{tag} w iw*{max(0.01, kf_eval(ks, i / FPS)):.4f}, scale@{tag} h ih*{max(0.01, kf_eval(ks, i / FPS)):.4f};" for i in range(n)]
        pth = os.path.join(_TMP, hashlib.sha1(("\n".join(lines)).encode()).hexdigest()[:16] + ".cmd"); open(pth, "w").write("\n".join(lines) + "\n")
        ch.append(f"sendcmd=f='{ffpath(pth)}',scale@{tag}=w=iw:h=ih:eval=frame,pad={W * 2}:{H * 2}:(ow-iw)/2:(oh-ih)/2:color=black@0.0,crop={W}:{H}:(in_w-{W})/2:(in_h-{H})/2")
    if kr: ch.append(f"rotate=a='({kf_expr(kr)})*PI/180':c=black@0.0:ow={W}:oh={H}")
    if ko: tag = f"lop{li}_{hashlib.sha1(json.dumps(ko).encode()).hexdigest()[:6]}"; ch.append(f"sendcmd=f='{ffpath(opacity_cmds(ko, cd, FPS, f'colorchannelmixer@{tag}'))}',colorchannelmixer@{tag}=aa={max(0.0, min(1.0, kf_eval(ko, 0))):.3f}")
    return ch

def graphic_chain(g, W, H, cd, FPS):
    """A graphics clip is a list of layers drawn bottom to top, each with its own animation. Returns a multi-stream graph fragment as a
    list; entries beginning with '|' are separate streams that build_command splices, the final entry is the composite chain."""
    layers = g.get("layers", [])
    if not layers: return [f"color=c=black@0.0:s={W}x{H}:r={FPS}:d={cd:.3f},format=yuva420p"]
    return [{"layers": [layer_stream(L, W, H, cd, FPS) + layer_kf_chain(g.get("_kf") or {}, li, W, H, cd, FPS) for li, L in enumerate(layers)], "base": f"color=c=black@0.0:s={W}x{H}:r={FPS}:d={cd:.3f},format=yuva420p"}]

BLEND_MODES = {"multiply": "multiply", "screen": "screen", "overlay": "overlay", "darken": "darken", "lighten": "lighten", "difference": "difference", "add": "addition", "softlight": "softlight", "hardlight": "hardlight", "exclusion": "exclusion", "subtract": "subtract"}

_ENCODERS = None
def has_encoder(name):
    global _ENCODERS
    if _ENCODERS is None:
        try: _ENCODERS = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=20).stdout
        except Exception: _ENCODERS = ""
    return f" {name} " in _ENCODERS

_FILTERS = None
def has_filter(name):
    global _FILTERS
    if _FILTERS is None:
        try: _FILTERS = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, timeout=20).stdout
        except Exception: _FILTERS = ""
    return f" {name} " in _FILTERS

INPUT_LUTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "luts", "input")
def input_transform_chain(m):
    """Camera log → Rec.709 technical conversion applied at the media level, before any grade (media.input_transform: slog3|vlog|clog3|logc3)."""
    key = (m or {}).get("input_transform")
    if not key or key == "none": return []
    p = os.path.join(INPUT_LUTS, f"{key}.cube")
    return [f"lut3d=file='{ffpath(p)}':interp=tetrahedral"] if os.path.exists(p) else []

def hdr_to_sdr_chain(m):
    """Log/HDR sources (PQ / HLG / BT.2020) get a tonemap to Rec.709 when the FFmpeg build has zscale + tonemap; otherwise a mild curve."""
    if not m.get("hdr"): return []
    trc = "arib-std-b67" if m.get("color_transfer") == "arib-std-b67" else "smpte2084"
    if has_filter("zscale") and has_filter("tonemap") and has_filter("setparams"):  # tag the stream explicitly, then linearize → tonemap → Rec.709
        return [f"setparams=color_primaries=bt2020:color_trc={trc}:colorspace=bt2020nc", "zscale=t=linear:npl=100", "format=gbrpf32le", "zscale=p=bt709", "tonemap=hable:desat=0", "zscale=t=bt709:m=bt709:r=tv", "format=yuv420p"]
    return ["curves=all='0/0 0.18/0.35 0.5/0.7 1/1'"]

def fx_chain(c, W, H):
    """Video effects beyond color: keying, crop, blur, sharpen, vignette (applied at frame size before transform)."""
    ch = []; fx = c.get("effects") or {}
    ky = fx.get("chromakey") or {}
    if ky.get("enabled"): ch.append(f"chromakey=color={ky.get('color', '0x00ff00').replace('#', '0x')}:similarity={float(ky.get('similarity', 0.2)):.3f}:blend={float(ky.get('blend', 0.05)):.3f}")
    cr = fx.get("crop") or {}
    if any(float(cr.get(k, 0)) > 0 for k in ("l", "t", "r", "b")):
        l, t, r, b = [max(0.0, min(0.95, float(cr.get(k, 0)))) for k in ("l", "t", "r", "b")]
        ch.append(f"crop=w=iw*{max(0.02, 1 - l - r):.4f}:h=ih*{max(0.02, 1 - t - b):.4f}:x=iw*{l:.4f}:y=ih*{t:.4f}")
    if float(fx.get("blur", 0)) > 0: ch.append(f"gblur=sigma={float(fx['blur']):.2f}")
    if float(fx.get("sharpen", 0)) > 0: ch.append(f"unsharp=5:5:{min(float(fx['sharpen']), 3.0):.2f}:5:5:0")
    if float(fx.get("vignette", 0)) > 0: ch.append(f"vignette=angle={min(float(fx['vignette']), 1.0) * math.pi / 2.2:.4f}")
    return ch

def channel_chain(mode):
    return {"left": ["pan=stereo|c0=c0|c1=c0"], "right": ["pan=stereo|c0=c1|c1=c1"], "mono": ["pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1"], "swap": ["pan=stereo|c0=c1|c1=c0"]}.get(mode or "stereo", [])

def pan_chain(pan):
    p = max(-1.0, min(1.0, float(pan or 0)))
    if abs(p) < 0.01: return []
    lg, rg = min(1.0, 1 - p), min(1.0, 1 + p)  # simple balance
    return [f"pan=stereo|c0={lg:.3f}*c0|c1={rg:.3f}*c1"]

def color_chain(col):
    if not col: return []
    ch = []
    ex, co, sa = float(col.get("exposure", 0)), float(col.get("contrast", 0)), float(col.get("saturation", 0))
    if abs(ex) > 1e-3 or abs(co) > 1e-3 or abs(sa) > 1e-3: ch.append(f"eq=brightness={ex*0.08:.4f}:contrast={1+co:.4f}:saturation={max(0,1+sa):.4f}")
    te, ti = float(col.get("temperature", 0)), float(col.get("tint", 0))
    if abs(te) > 1e-3 or abs(ti) > 1e-3: ch.append(f"colorbalance=rs={te*0.15:.4f}:bs={-te*0.15:.4f}:gs={-ti*0.15:.4f}:rm={te*0.1:.4f}:bm={-te*0.1:.4f}:gm={-ti*0.1:.4f}")
    hi, sh, wh, bl = float(col.get("highlights", 0)), float(col.get("shadows", 0)), float(col.get("whites", 0)), float(col.get("blacks", 0))
    if any(abs(x) > 1e-3 for x in (hi, sh, wh, bl)):
        pts = []
        if bl > 1e-3: pts += [(0.0, 0.0), (min(0.2, bl * 0.1), 0.0005)]          # crush: raise the input black point
        else: pts += [(0.0, min(0.2, -bl * 0.1))]                                   # lift: raise the output black level
        pts.append((0.25, min(max(0.25 + sh * 0.12, pts[-1][0] + 0.02), 0.45))); pts.append((0.75, min(max(0.75 + hi * 0.12, 0.55), 0.95)))
        if wh > 1e-3: pts += [(max(0.8, 1 - wh * 0.1), 0.9995), (1.0, 1.0)]         # brighter: lower the input white point
        else: pts += [(1.0, max(0.8, 1 + wh * 0.1))]
        ch.append("curves=all='" + " ".join(f"{x:.4f}/{y:.4f}" for x, y in pts) + "'")
    cv = []
    for key, opt in (("curves", "all"), ("curves_r", "r"), ("curves_g", "g"), ("curves_b", "b")):
        pts = col.get(key)
        if pts and len(pts) >= 2:
            srt = sorted([(max(0, min(1, float(x))), max(0, min(1, float(y)))) for x, y in pts]); cv.append(f"{opt}='" + " ".join(f"{x:.3f}/{y:.3f}" for x, y in srt) + "'")
    if cv: ch.append("curves=" + ":".join(cv))
    if abs(float(col.get("vibrance", 0))) > 1e-3: ch.append(f"vibrance=intensity={max(-2, min(2, float(col['vibrance']))):.3f}")
    wh = col.get("wheels") or {}
    if wh:
        def w(k): v = wh.get(k) or {}; return float(v.get("r", 0)), float(v.get("g", 0)), float(v.get("b", 0))
        rs, gs, bs = w("shadows"); rm, gm, bm = w("midtones"); rh, gh, bh = w("highlights")
        if any(abs(x) > 1e-3 for x in (rs, gs, bs, rm, gm, bm, rh, gh, bh)): ch.append(f"colorbalance=rs={rs:.3f}:gs={gs:.3f}:bs={bs:.3f}:rm={rm:.3f}:gm={gm:.3f}:bm={bm:.3f}:rh={rh:.3f}:gh={gh:.3f}:bh={bh:.3f}")
    if col.get("lut") and os.path.exists(col["lut"]): ch.append(f"lut3d=file='{ffpath(col['lut'])}'")
    return ch

def mask_png(mk, W, H):
    """Rasterize a rect/ellipse mask with feather to a grayscale PNG (cached by hash). Fast path instead of per-pixel geq."""
    from PIL import Image, ImageDraw, ImageFilter
    key = hashlib.sha1(json.dumps([mk, W, H], sort_keys=True).encode()).hexdigest()[:16]; p = os.path.join(_TMP, f"mask_{key}.png")
    if os.path.exists(p): return p
    x0, y0 = float(mk.get("x", 0.1)) * W, float(mk.get("y", 0.1)) * H; w, h = float(mk.get("w", 0.8)) * W, float(mk.get("h", 0.8)) * H
    im = Image.new("L", (W, H), 255 if mk.get("invert") else 0); d = ImageDraw.Draw(im); fill = 0 if mk.get("invert") else 255
    (d.ellipse if mk.get("type") == "ellipse" else d.rectangle)([x0, y0, x0 + w, y0 + h], fill=fill)
    fe = float(mk.get("feather", 0));
    if fe > 0: im = im.filter(ImageFilter.GaussianBlur(fe / 2))
    im.save(p); return p

def mask_chain(mk, W, H):
    """Alpha mask via geq: rect or ellipse in frame fractions, feather in px, optional invert. Applied at frame size WxH."""
    if not mk or not mk.get("type"): return []
    x0, y0 = float(mk.get("x", 0.1)) * W, float(mk.get("y", 0.1)) * H; w, h = float(mk.get("w", 0.8)) * W, float(mk.get("h", 0.8)) * H
    fe = max(float(mk.get("feather", 0)), 0.5)
    if mk["type"] == "ellipse":
        cx, cy, rx, ry = x0 + w / 2, y0 + h / 2, max(w / 2, 1), max(h / 2, 1)
        m = f"clip((1-sqrt(pow((X-{cx:.1f})/{rx:.1f},2)+pow((Y-{cy:.1f})/{ry:.1f},2)))*{min(rx, ry):.1f}/{fe:.1f},0,1)"
    else:
        m = f"clip(min(min(X-{x0:.1f},{x0 + w:.1f}-X),min(Y-{y0:.1f},{y0 + h:.1f}-Y))/{fe:.1f},0,1)"
    if mk.get("invert"): m = f"(1-{m})"
    return ["format=rgba", f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='alpha(X,Y)*{m}'", "format=yuva420p"]

def audio_fx_chain(fx):
    if not fx: return []
    ch = []; eq = fx.get("eq") or {}
    if abs(eq.get("low_db", 0)) > 0.01: ch.append(f"bass=g={eq['low_db']:.1f}:f=120")
    if abs(eq.get("mid_db", 0)) > 0.01: ch.append(f"equalizer=f=1000:t=q:w=1:g={eq['mid_db']:.1f}")
    if abs(eq.get("high_db", 0)) > 0.01: ch.append(f"treble=g={eq['high_db']:.1f}:f=6000")
    dn = fx.get("denoise") or {}
    if dn.get("enabled"): ch.append(f"afftdn=nr={float(dn.get('db', 12)):.1f}:nf=-40")
    co = fx.get("comp") or {}
    if co.get("enabled"): ch.append(f"acompressor=threshold={10 ** (float(co.get('threshold_db', -18)) / 20):.4f}:ratio={float(co.get('ratio', 3)):.1f}:attack={float(co.get('attack_ms', 20)):.0f}:release={float(co.get('release_ms', 200)):.0f}:makeup={10 ** (float(co.get('makeup_db', 0)) / 20):.3f}")
    if fx.get("limiter"): ch.append("alimiter=limit=0.95:level=false")
    return ch

def prerender_nested(project, seq, preset, ffmpeg, depth=0):
    """Nested sequences render to temp files first and are treated as media inputs (recursion-safe)."""
    if depth > 4: raise RuntimeError("nested sequences too deep")
    proj = json.loads(json.dumps(project)); media = proj["media"]
    seq = next(s for s in proj["sequences"] if s["id"] == seq["id"])  # mutate the copy only
    for tr in seq["tracks"]:
        for c in tr["clips"]:
            sid = c.get("sequence_id")
            if not sid or c.get("media_id"): continue
            sub = next((s for s in proj["sequences"] if s["id"] == sid), None)
            if not sub: continue
            angle = c.get("multicam_angle")
            if sub.get("multicam") and angle is not None:
                # multicam: keep only the chosen angle's video track (+ its audio, or the designated audio track)
                sub = json.loads(json.dumps(sub)); vt = [t for t in sub["tracks"] if t["kind"] == "video"]
                for k, t in enumerate(sorted(vt, key=lambda t: t["index"])): t["muted"] = (k != angle)
                for t in sub["tracks"]:
                    if t["kind"] == "audio": t["muted"] = (sub.get("multicam_audio") == "follow") or (sub.get("multicam_audio_track") not in (None, t["id"]))
                sub["id"] = f"{sid}__angle{angle}"; proj["sequences"].append(sub); sid = sub["id"]
            out = os.path.join(_TMP, f"nested_{sid}_{hashlib.sha1(json.dumps([sub, angle], sort_keys=True).encode()).hexdigest()[:10]}.mp4")
            if not os.path.exists(out):
                cmd, _ = build_command(proj, sid, out, {"crf": 16, "x264_preset": "veryfast"}, ffmpeg, depth=depth + 1)
                r = subprocess.run(cmd, capture_output=True, text=True)
                if r.returncode != 0: raise RuntimeError("nested render failed: " + r.stderr[-1500:])
            mid = "nested:" + sid; media[mid] = {"id": mid, "name": sub["name"], "path": out, "duration": seq_total(sub), "width": sub["width"], "height": sub["height"], "fps": sub["fps"], "has_video": True, "has_audio": True}
            c["media_id"] = mid
    return proj

def seq_total(seq):
    return seq.get("duration") or max([c["start"] + clip_dur(c) for tr in seq["tracks"] for c in tr["clips"]] + [1.0])

def build_command(project, sequence_id, out_path, preset=None, ffmpeg="ffmpeg", video_only=False, depth=0):
    project = prerender_nested(project, next(s for s in project["sequences"] if s["id"] == sequence_id), preset or {}, ffmpeg, depth)
    seq = next(s for s in project["sequences"] if s["id"] == sequence_id)
    W, H, FPS = int(seq["width"]), int(seq["height"]), float(seq["fps"]); media = project["media"]; preset = preset or {}
    for tr in seq["tracks"]:  # transition alignment: move the incoming clip's start earlier so the transition is centered on / ends at the cut
        for c in tr["clips"]:
            # A dissolve MUST have the outgoing clip underneath it for its WHOLE duration, so
            # it defaults to "end" alignment -- the incoming clip starts D early and its fade
            # completes exactly at the cut.
            #
            # It used to default to "start": no shift at all, so the incoming clip began at the
            # cut with the outgoing already ended, and `fade=t=in:alpha=1` (line ~666) faded up
            # from alpha 0 against nothing. Every dissolve was a dip to black, which also made
            # `dissolve` identical to `dip_black` -- the app offers both, and the two behaving
            # the same was the tell.
            #
            # "center" was tried first and is still wrong: it shifts by D/2 while the fade runs
            # for D, so the second half of every dissolve still has nothing underneath. Measured
            # on the v4 render -- outgoing holds flat, drops to a floor BELOW both shots, then
            # ramps to the incoming level over four frames (95,96,102,96,87,81 -> 28 -> 38,48,58).
            # A correct dissolve moves monotonically between the two shot levels and never dips
            # below both. Wipes and geometric transitions genuinely start at the cut, so only
            # dissolve/fade take the new default.
            ti_ = c.get("transition_in") or {}
            al = ti_.get("align") or ("end" if ti_.get("type", "dissolve") in ("dissolve", "fade") else "start")
            D_ = float(ti_.get("duration", 0) or 0)
            if D_ > 0 and al in ("center", "end") and c.get("media_id") and not media.get(c["media_id"], {}).get("is_image"):
                shift = min(D_ / 2 if al == "center" else D_, c["in_"] / max(c.get("speed", 1), 1e-6), c["start"])
                # Quantise to whole frames. A shift of D/2 = 0.15s at 24fps is 3.6 frames, and a
                # clip starting off a frame boundary loses its last frame to rounding -- which
                # opened a ONE-FRAME HOLE against the next clip if that one did not shift. In the
                # v4 render the hero's hard cut read 43,43,43 -> 0 -> 100: a pure black frame in
                # the gap. Whole frames cannot leave a hole.
                shift = math.floor(shift * FPS + 1e-6) / FPS
                if shift > 1e-4: c["start"] -= shift; c["in_"] -= shift * c.get("speed", 1)
    used = []
    for tr in seq["tracks"]:
        for c in tr["clips"]:
            if c.get("media_id") and c["media_id"] not in used and not media.get(c["media_id"], {}).get("synthetic"): used.append(c["media_id"])
    inputs, idx = [], {}
    for mid in used:
        m = media[mid]
        if m.get("subclip_of") and m["subclip_of"] in media: m = dict(media[m["subclip_of"]], sub_in=float(m.get("sub_in", 0)))
        media[mid] = {**media[mid], "path": m["path"], "has_audio": m.get("has_audio", media[mid].get("has_audio")), "is_image": m.get("is_image")}
        idx[mid] = len(inputs)
        inputs.append((["-loop", "1", "-framerate", f"{FPS}", "-t", f"{max([clip_dur(c) for t in seq['tracks'] for c in t['clips'] if c.get('media_id') == mid] + [1]):.3f}"] if m.get("is_image") else list(m.get("input_opts") or [])) + ["-i", m["path"]])
    total = seq.get("duration") or max([c["start"] + clip_dur(c) for tr in seq["tracks"] for c in tr["clips"]] + [1.0])
    extra = {}  # mask png path -> input index (appended after media inputs)
    f = [f"color=c=black:s={W}x{H}:r={FPS}:d={total:.3f}[base0]"]; layer = "base0"; n = 0
    vtracks = sorted([t for t in seq["tracks"] if t["kind"] == "video" and not t.get("muted")], key=lambda t: t["index"])
    for tr in vtracks:
        for c in sorted(tr["clips"], key=lambda c: c["start"]):
            if c.get("enabled") is False: continue
            n += 1; cd = clip_dur(c); st, en = c["start"], c["start"] + cd
            tf = c.get("transform") or {}; kf = c.get("keyframes") or {}
            sc = float(tf.get("scale", 1.0)); op = float(tf.get("opacity", 1.0)); rot = float(tf.get("rotation", 0.0)); x0 = float(tf.get("x", 0.0)); y0 = float(tf.get("y", 0.0))
            if c.get("adjustment"):
                # adjustment layer: grade everything below it inside its window
                for flt in color_chain(c.get("color")):
                    f.append(f"[{layer}]{flt}:enable='between(t,{st:.4f},{en:.4f})'[base{n}]"); layer = f"base{n}"; n += 1
                n -= 1; continue
            if c.get("media_id") and media[c["media_id"]].get("synthetic"):
                sy = media[c["media_id"]]["synthetic"]; kind = sy.get("kind", "black"); col = sy.get("color", "#000000").replace("#", "0x")
                if kind == "bars": chain = [f"smptehdbars=s={W}x{H}:r={FPS}:d={cd:.3f},format=yuva420p"]
                elif kind == "transparent": chain = [f"color=c=black@0.0:s={W}x{H}:r={FPS}:d={cd:.3f},format=yuva420p"]
                elif kind == "counting_leader": chain = [f"color=c=0x404040:s={W}x{H}:r={FPS}:d={cd:.3f},format=yuva420p", f"drawtext=text='%{{eif\\:trunc({cd:.0f}-t)\\:d}}':fontsize={int(H*0.3)}:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2:fontfile='{ffpath(bundled_font())}'"]
                elif kind == "gradient":
                    c0 = (sy.get("color") or "#E8631C").replace("#", "0x"); c1 = (sy.get("color2") or "#7A2E9E").replace("#", "0x"); c2 = (sy.get("color3") or c0).replace("#", "0x")
                    chain = [f"gradients=s={W}x{H}:r={FPS}:d={cd:.3f}:c0={c0}:c1={c1}:c2={c2}:nb_colors=3:speed={float(sy.get('speed', 0.04)):.3f}:x0={W // 4}:y0={H // 4}:x1={W * 3 // 4}:y1={H * 3 // 4}", "format=yuva420p"]
                elif kind == "light_leak":  # warm animated gradients, heavily blurred; use blend mode 'screen' on the clip
                    inten = float(sy.get("intensity", 0.5) or 0.5)
                    chain = [f"gradients=s={W // 4}x{H // 4}:r={FPS}:d={cd:.3f}:c0=0xFF7A00:c1=0x000000:c2=0x000000:c3=0xFF2D55:c4=0x000000:c5=0xFFC300:nb_colors=6:speed={float(sy.get('speed', 0.06)):.3f}:x0=0:y0={H // 8}:x1={W // 4}:y1={H // 8}", "gblur=sigma=26", f"scale={W}:{H}", f"vignette=angle=PI/2.8", f"eq=brightness={-0.55 + 0.5 * inten:.2f}:saturation=1.4", "format=yuva420p"]
                elif kind == "grain":  # film grain plate; use blend mode 'overlay' or 'soft light' on the clip
                    chain = [f"color=c=0x808080:s={W}x{H}:r={FPS}:d={cd:.3f}", f"noise=alls={int(20 + 60 * float(sy.get('intensity', 0.5)))}:allf=t+u", "format=yuva420p"]
                else: chain = [f"color=c={col}:s={W}x{H}:r={FPS}:d={cd:.3f},format=yuva420p"]
                chain += fx_chain(c, W, H); chain += color_chain(c.get("color"))
                for item in video_stack_chain(c, FPS): chain.append(item)
            elif c.get("media_id"):
                i = idx[c["media_id"]]; m = media[c["media_id"]]
                stab = stabilize_params(c); stab_pre = ""
                if stab and m.get("stab_trf") and os.path.exists(m["stab_trf"]):
                    stab_pre = f"vidstabtransform=input='{ffpath(m['stab_trf'])}':smoothing={int(stab['smoothing'])}:zoom={int(stab['zoom'])}:optzoom={0 if stab['crop']=='black' else 1}:crop={stab['crop']},unsharp=5:5:0.8:3:3:0.4,"
                if c.get("hold"):
                    off = float(m.get("sub_in", 0) or 0)
                    chain = [f"[{i}:v]{stab_pre}trim=start={c['in_'] + off:.4f}:end={c['in_'] + off + 1.5 / FPS:.4f}", "setpts=PTS-STARTPTS", "loop=loop=-1:size=1:start=0", f"trim=0:{cd:.4f}", "setpts=PTS-STARTPTS"]
                else:
                    off = float(m.get("sub_in", 0) or 0); ifac = (float(m["fps"]) / float(m["interpret_fps"])) if m.get("interpret_fps") and m.get("fps") else 1.0
                    chain = ([f"[{i}:v]trim=start=0:end={cd:.4f}"] if m.get("is_image") else [f"[{i}:v]{stab_pre}trim=start={(c['in_'] + off) / ifac:.4f}:end={(c['out'] + off) / ifac:.4f}"]) + ["setpts=PTS-STARTPTS"] + ([f"setpts=PTS*{ifac:.6f}"] if abs(ifac - 1) > 1e-6 else [])
                    if c.get("reverse") and not m.get("is_image"): chain.append("reverse")
                    if c.get("time_remap") and not m.get("is_image"): chain.append(f"setpts='({remap_setpts_expr(c)})/TB'")
                    elif abs(c.get("speed", 1.0) - 1.0) > 1e-6 and not m.get("is_image"): chain.append(f"setpts=PTS/{c['speed']}")
                    ti_mode = c.get("time_interpolation")
                    if ti_mode == "optical_flow" and (c.get("time_remap") or c.get("speed", 1.0) < 1): chain.append(f"minterpolate=fps={FPS}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1")
                    elif ti_mode == "frame_blending" and (c.get("time_remap") or c.get("speed", 1.0) < 1): chain.append(f"framerate=fps={FPS}")
                chain += hdr_to_sdr_chain(m); chain += input_transform_chain(m)
                if c.get("fit") == "cover": chain.append(f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},format=yuva420p,fps={FPS}")
                elif c.get("fit") == "blur_fill":
                    f.append(",".join(chain) + f",split[bf{n}_a][bf{n}_b]"); f.append(f"[bf{n}_a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma={float(c.get('blur_fill_sigma', 40)):.1f},eq=brightness=-0.08,format=yuva420p,fps={FPS}[bf{n}_bg]"); f.append(f"[bf{n}_b]scale={W}:{H}:force_original_aspect_ratio=decrease,format=yuva420p,fps={FPS}[bf{n}_fg]"); f.append(f"[bf{n}_bg][bf{n}_fg]overlay=x=(W-w)/2:y=(H-h)/2:format=rgb,format=yuva420p[bf{n}_o]"); chain = [f"[bf{n}_o]null"]
                else: chain.append(f"scale={W}:{H}:force_original_aspect_ratio=decrease,format=yuva420p,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black@0.0,fps={FPS}")
                chain += fx_chain(c, W, H); chain += color_chain(c.get("color"))
                for item in video_stack_chain(c, FPS):
                    if ";" in item:  # graph-style effect (mirror): flush the chain so far, then splice
                        f.append(",".join(chain) + f"[fxin{n}]"); f.append(f"[fxin{n}]" + item + f"[fxout{n}]"); chain = [f"[fxout{n}]null"]
                    else: chain.append(item)
                if (c.get("mask") or {}).get("type") and any(k.startswith("mask.") for k in kf):
                    # animated mask: quarter-res geq matte driven by keyframe expressions in T, blurred for feather, scaled up
                    mk = c["mask"]; q = 4; mw, mh = max(2, W // q), max(2, H // q)
                    def mkx(key, dflt): return f"({kf_expr(kf[key], 'T') if kf.get(key) else dflt})"
                    mx0, my0, mw_, mh_ = mkx("mask.x", mk.get("x", 0.1)), mkx("mask.y", mk.get("y", 0.1)), mkx("mask.w", mk.get("w", 0.8)), mkx("mask.h", mk.get("h", 0.8))
                    fe = max(float(mk.get("feather", 0)) / q, 1.0)
                    if mk["type"] == "ellipse": m_expr = f"clip((1-sqrt(pow((X-({mx0}+{mw_}/2)*W)/max({mw_}*W/2,1),2)+pow((Y-({my0}+{mh_}/2)*H)/max({mh_}*H/2,1),2)))*min({mw_}*W/2,{mh_}*H/2)/{fe:.2f},0,1)"
                    else: m_expr = f"clip(min(min(X-{mx0}*W,({mx0}+{mw_})*W-X),min(Y-{my0}*H,({my0}+{mh_})*H-Y))/{fe:.2f},0,1)"
                    if mk.get("invert"): m_expr = f"(1-{m_expr})"
                    f.append(f"color=c=black:s={mw}x{mh}:r={FPS}:d={cd:.3f},format=gray,geq=lum='255*{m_expr}',boxblur={max(1, int(fe / 2))},scale={W}:{H}[m{n}]")
                    f.append(",".join(chain) + f",format=rgb24[v{n}a]"); chain = [f"[v{n}a][m{n}]alphamerge", "format=yuva420p"]
                elif (c.get("mask") or {}).get("type"):
                    mp = mask_png(c["mask"], W, H); mi = extra.setdefault(mp, len(inputs) + len(extra)); 
                    f.append(",".join(chain) + f",format=rgb24[v{n}a]"); f.append(f"[{mi}:v]format=gray[m{n}]"); chain = [f"[v{n}a][m{n}]alphamerge", "format=yuva420p"]
                if kf.get("transform.scale"): e = kf_expr(kf["transform.scale"], "t"); chain.append(f"scale=w='iw*({e})':h='ih*({e})':eval=frame")
                elif abs(sc - 1.0) > 1e-6: chain.append(f"scale=iw*{sc:.4f}:ih*{sc:.4f}")
                if abs(rot) > 1e-6: rr = math.radians(rot); chain.append(f"rotate={rr:.5f}:c=none:ow=rotw({rr:.5f}):oh=roth({rr:.5f})")
            elif c.get("graphic"):
                gc = graphic_chain(dict(c["graphic"], _kf={k: v for k, v in (c.get("keyframes") or {}).items() if k.startswith("g")}), W, H, cd, FPS)
                if isinstance(gc[0], dict):  # layered graphic: one stream per layer, overlaid in order
                    layer_labels = []
                    for li, lch in enumerate(gc[0]["layers"]):
                        resolved = []
                        for k_, item in enumerate(lch):
                            if item.startswith("__SHAPE__"):
                                pth = item[len("__SHAPE__"):-2]; si_ = extra.setdefault(pth, len(inputs) + len(extra))
                                f.append(",".join(resolved) + f"[g{n}_{li}_{k_}]"); f.append(f"[{si_}:v]format=rgba,scale={W}:{H}[s{n}_{li}_{k_}]"); resolved = [f"[g{n}_{li}_{k_}][s{n}_{li}_{k_}]overlay=x=0:y=0:format=rgb", "format=yuva420p"]
                            elif item.startswith("__IMAGE__"):
                                pth = item[len("__IMAGE__"):-2]; si_ = extra.setdefault(pth, len(inputs) + len(extra)); Lm = c["graphic"]["layers"][li]; bw, bh = max(2, int(float(Lm.get("w", 0.3)) * W)), max(2, int(float(Lm.get("h", 0.3)) * H)); bx, by = int(float(Lm.get("x", 0.35)) * W), int(float(Lm.get("y", 0.35)) * H)
                                fitm = "increase,crop=" + f"{bw}:{bh}" if Lm.get("fit") == "cover" else "decrease"
                                f.append(",".join(resolved) + f"[g{n}_{li}_{k_}]"); f.append(f"[{si_}:v]format=rgba,scale={bw}:{bh}:force_original_aspect_ratio={fitm}" + (f",colorchannelmixer=aa={float(Lm['opacity']):.3f}" if Lm.get("opacity") not in (None, 1, 1.0) else "") + f"[s{n}_{li}_{k_}]"); resolved = [f"[g{n}_{li}_{k_}][s{n}_{li}_{k_}]overlay=x={bx}+({bw}-w)/2:y={by}+({bh}-h)/2:format=rgb", "format=yuva420p"]
                            elif "[gl_a]" in item:  # glow: needs its own graph lines
                                f.append(",".join(resolved) + f"[glin{n}_{li}]"); f.append(f"[glin{n}_{li}]" + item.replace("[gl_a]", f"[gla{n}_{li}]").replace("[gl_b]", f"[glb{n}_{li}]").replace("[gl_c]", f"[glc{n}_{li}]") + f"[glout{n}_{li}]"); resolved = [f"[glout{n}_{li}]null"]
                            else: resolved.append(item)
                        f.append(",".join(resolved) + f"[ly{n}_{li}]"); layer_labels.append(f"[ly{n}_{li}]")
                    f.append(gc[0]["base"] + f"[gb{n}_0]"); cur = f"[gb{n}_0]"
                    for li, lab in enumerate(layer_labels): f.append(f"{cur}{lab}overlay=x=0:y=0:format=rgb,format=yuva420p[gb{n}_{li + 1}]"); cur = f"[gb{n}_{li + 1}]"
                    chain = [f"{cur}null"]
                else: chain = gc
            else:
                t = c.get("title") or {}
                chain = [f"color=c=black@0.0:s={W}x{H}:r={FPS}:d={cd:.3f},format=yuva420p", drawtext_opts(t, W, H, H * 0.07)]
            for k2, hs in enumerate(hsl_secondary_params(c) if c.get("media_id") else []):
                f.append(",".join(chain) + f",split[hs{n}_{k2}a][hs{n}_{k2}b]")
                f.append(f"[hs{n}_{k2}a]hue=h={hs['hue_shift']}:s={hs['saturation']:.3f},eq=brightness={hs['lightness'] * 0.5:.3f}[hs{n}_{k2}c]")
                key = f"chromakey=color={hs['key_color'].replace('#', '0x')}:similarity={hs['range']:.3f}:blend={hs['softness']:.3f}"
                if hs.get("invert"): f.append(f"[hs{n}_{k2}c]{key}[hs{n}_{k2}k];[hs{n}_{k2}b][hs{n}_{k2}k]overlay=x=0:y=0:format=rgb,format=yuva420p[hs{n}_{k2}o]")
                else: f.append(f"[hs{n}_{k2}b]{key}[hs{n}_{k2}k];[hs{n}_{k2}c][hs{n}_{k2}k]overlay=x=0:y=0:format=rgb,format=yuva420p[hs{n}_{k2}o]")
                chain = [f"[hs{n}_{k2}o]null"]
            tm = track_matte_params(c) if c.get("media_id") else None
            if tm:
                mt = next((t for t in seq["tracks"] if t["id"] == tm["track"]), None); mc = next((x for x in (mt["clips"] if mt else []) if x["start"] < en and x["start"] + clip_dur(x) > st), None)
                if mc:
                    ms = mc.get("speed", 1.0); moff = max(0.0, st - mc["start"]); mdur = min(cd, mc["start"] + clip_dur(mc) - st)
                    if mc.get("media_id") and mc["media_id"] in idx:
                        mm = media[mc["media_id"]]; msrc = f"[{idx[mc['media_id']]}:v]trim=start={mc['in_'] + moff * ms:.4f}:end={mc['in_'] + (moff + mdur) * ms:.4f},setpts=PTS-STARTPTS,scale={W}:{H}:force_original_aspect_ratio=decrease,format=yuva420p,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black@0.0,fps={FPS}"
                    elif mc.get("graphic"): msrc = ",".join(x for x in graphic_chain(mc["graphic"], W, H, mdur, FPS) if not x.startswith("__SHAPE__"))
                    else: msrc = ",".join([f"color=c=black@0.0:s={W}x{H}:r={FPS}:d={mdur:.3f},format=yuva420p", drawtext_opts(mc.get("title") or {}, W, H, H * 0.07)])
                    ext = f",tpad=stop_mode=clone:stop_duration={max(0.0, cd - mdur):.3f}" if cd - mdur > 1e-3 else ""
                    f.append(msrc + ext + ("," + ("alphaextract" if tm["type"] == "alpha" else "format=gray") + (",negate" if tm.get("invert") else "")) + f"[tm{n}]")
                    f.append(",".join(chain) + f",format=rgb24[tmv{n}]"); chain = [f"[tmv{n}][tm{n}]alphamerge", "format=yuva420p"]
            ti, to = c.get("transition_in") or {}, c.get("transition_out") or {}; D, Do = float(ti.get("duration", 0) or 0), float(to.get("duration", 0) or 0)
            typ, typo = ti.get("type", "dissolve"), to.get("type", "dissolve")
            if D > 0:
                if typ in ("dissolve", "fade"): chain.append(f"fade=t=in:st=0:d={D:.3f}:alpha=1")
                elif typ in ("dip_black", "dip_white"): chain.append(f"fade=t=in:st=0:d={D:.3f}:color={'black' if typ=='dip_black' else 'white'}")
                elif typ in ("wipe_left", "wipe_right", "wipe_up", "wipe_down", "iris", "iris_close", "diagonal_tl", "diagonal_tr", "diagonal_bl", "diagonal_br", "barn_h", "barn_v", "clock", "checker"):
                    p = f"min(1,T/{D:.3f})"
                    cond = {"wipe_left": f"lt(X,W*{p})", "wipe_right": f"gt(X,W*(1-{p}))", "wipe_up": f"gt(Y,H*(1-{p}))", "wipe_down": f"lt(Y,H*{p})", "iris": f"lt(hypot(X-W/2,Y-H/2),hypot(W/2,H/2)*{p})", "iris_close": f"gt(hypot(X-W/2,Y-H/2),hypot(W/2,H/2)*(1-{p}))",
                            "diagonal_tl": f"lt(X/W+Y/H,2*{p})", "diagonal_tr": f"lt((W-X)/W+Y/H,2*{p})", "diagonal_bl": f"lt(X/W+(H-Y)/H,2*{p})", "diagonal_br": f"lt((W-X)/W+(H-Y)/H,2*{p})",
                            "barn_h": f"lt(abs(X-W/2),W/2*{p})", "barn_v": f"lt(abs(Y-H/2),H/2*{p})", "clock": f"lt(mod(atan2(X-W/2,-(Y-H/2))+2*PI,2*PI),2*PI*{p})", "checker": f"lt(mod(floor(X/(W/8))+floor(Y/(H/14)),2)*0.5+mod(floor(X/(W/8)*7+floor(Y/(H/14))*3),16)/32,{p})"}[typ]
                    chain.append(f"format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='if({cond},alpha(X,Y),0)',format=yuva420p")
                elif typ == "cross_zoom":  # scale cannot use t in expressions: drive a named scale instance per frame, then centre-crop
                    lines = [f"{i / FPS:.4f} scale@cz{n} w iw*{1 + 0.6 * max(0.0, 1 - (i / FPS) / D):.4f}, scale@cz{n} h ih*{1 + 0.6 * max(0.0, 1 - (i / FPS) / D):.4f};" for i in range(int(math.ceil(D * FPS)) + 2)]
                    pth = os.path.join(_TMP, hashlib.sha1(("\n".join(lines) + f"cz{n}").encode()).hexdigest()[:16] + ".cmd"); open(pth, "w").write("\n".join(lines) + "\n")
                    chain.append(f"fade=t=in:st=0:d={D:.3f}:alpha=1"); chain.append(f"sendcmd=f='{ffpath(pth)}',scale@cz{n}=w=iw:h=ih:eval=frame,crop={W}:{H}:(in_w-{W})/2:(in_h-{H})/2")
                elif typ == "glitch":  # RGB channel tearing + noise bursts for D seconds, then clean (hard cut underneath)
                    import random as _rnd; rg = _rnd.Random(hash(c["id"]) & 0xffff); lines = []
                    for i in range(int(math.ceil(D * FPS)) + 2):
                        t_ = i / FPS; k = max(0.0, 1 - t_ / D); rh = int(rg.uniform(-40, 40) * k); bh = int(rg.uniform(-40, 40) * k); gv = int(rg.uniform(-12, 12) * k)
                        lines.append(f"{t_:.4f} rgbashift@gl{n} rh {rh}, rgbashift@gl{n} bh {bh}, rgbashift@gl{n} gv {gv};")
                    pth = os.path.join(_TMP, hashlib.sha1(("\n".join(lines) + f"gl{n}").encode()).hexdigest()[:16] + ".cmd"); open(pth, "w").write("\n".join(lines) + "\n")
                    chain.append(f"sendcmd=f='{ffpath(pth)}',rgbashift@gl{n}=rh=0:bh=0,noise=alls=40:allf=t:enable='lt(t,{D:.3f})'")
            if Do > 0:
                if typo in ("dissolve", "fade"): chain.append(f"fade=t=out:st={max(cd - Do, 0):.3f}:d={Do:.3f}:alpha=1")
                elif typo in ("dip_black", "dip_white"): chain.append(f"fade=t=out:st={max(cd - Do, 0):.3f}:d={Do:.3f}:color={'black' if typo=='dip_black' else 'white'}")
            if kf.get("transform.opacity"):
                chain.append(f"sendcmd=f='{ffpath(opacity_cmds(kf['transform.opacity'], cd, FPS, f'colorchannelmixer@op{n}'))}',colorchannelmixer@op{n}=aa={kf_eval(kf['transform.opacity'], 0):.3f}")
            elif op < 0.999: chain.append(f"colorchannelmixer=aa={op:.3f}")
            ds = drop_shadow_params(c) if c.get("media_id") or c.get("title") or c.get("graphic") else None
            if ds:
                dx, dy = ds["distance"] * math.cos(math.radians(ds["angle"])), ds["distance"] * math.sin(math.radians(ds["angle"]))
                f.append(",".join(chain) + f",split[dso{n}][dss{n}]"); f.append(f"[dss{n}]format=rgba,colorchannelmixer=rr=0:gg=0:bb=0:aa={ds['opacity']:.2f},gblur=sigma={max(0.1, ds['softness']):.1f},pad=iw+{int(abs(dx))+int(ds['softness']*3)}:ih+{int(abs(dy))+int(ds['softness']*3)}:{int(max(0,dx))}:{int(max(0,dy))}:color=black@0.0,format=yuva420p[dsh{n}]")
                f.append(f"[dsh{n}][dso{n}]overlay=x={int(max(0,-dx))}:y={int(max(0,-dy))}:format=rgb,format=yuva420p[dsd{n}]"); chain = [f"[dsd{n}]null"]
            chain.append(f"setpts=PTS+{st:.4f}/TB"); f.append(",".join(chain) + f"[v{n}]")
            ax, ay = float(tf.get("anchor_x", 0.0)), float(tf.get("anchor_y", 0.0)); rr_ = math.radians(rot)
            aox = ax - (ax * math.cos(rr_) - ay * math.sin(rr_)) * sc; aoy = ay - (ax * math.sin(rr_) + ay * math.cos(rr_)) * sc  # keep the anchor point fixed under scale/rotation
            xe = f"(W-w)/2+({kf_expr(kf['transform.x'], 't', st) if kf.get('transform.x') else x0})+({aox:.2f})"
            ye = f"(H-h)/2+({kf_expr(kf['transform.y'], 't', st) if kf.get('transform.y') else y0})+({aoy:.2f})"
            if D > 0 and typ in ("push_left", "push_right", "slide_left", "slide_right"):
                xe = f"{xe}+({'-' if typ in ('push_left', 'slide_left') else ''}W*(1-min(1,(t-{st:.4f})/{D:.3f})))"
            if D > 0 and typ in ("slide_up", "slide_down"):
                ye = f"{ye}+({'-' if typ == 'slide_up' else ''}H*(1-min(1,(t-{st:.4f})/{D:.3f})))"
            bm = (c.get("blend") or "normal").lower()
            if bm in BLEND_MODES and c.get("media_id"):
                f.append(f"color=c=black@0.0:s={W}x{H}:r={FPS}:d={total:.3f},format=yuva420p[cv{n}]")
                f.append(f"[cv{n}][v{n}]overlay=x='{xe}':y='{ye}':eval=frame:eof_action=pass:format=rgb[cf{n}]")
                f.append(f"[cf{n}]split[cf{n}a][cf{n}b];[cf{n}a]alphaextract[al{n}]")
                f.append(f"[{layer}]split[bs{n}a][bs{n}b];[cf{n}b]format=yuva420p[cf{n}c];[bs{n}a][cf{n}c]blend=all_mode={BLEND_MODES[bm]}:shortest=1[bl{n}]")
                f.append(f"[bl{n}]format=rgb24[bl{n}r];[bl{n}r][al{n}]alphamerge,format=yuva420p[bm{n}]")
                f.append(f"[bs{n}b][bm{n}]overlay=x=0:y=0:eof_action=pass:enable='between(t,{st:.4f},{en:.4f})'[base{n}]"); layer = f"base{n}"
            else:
                f.append(f"[{layer}][v{n}]overlay=x='{xe}':y='{ye}':eval=frame:eof_action=pass:enable='between(t,{st:.4f},{en:.4f})'[base{n}]"); layer = f"base{n}"
    # captions (burned in)
    caps = seq.get("captions") or []; cs = seq.get("caption_style") or {}
    if caps:
        parts = []; size_ = int(cs.get('size', H*0.032)); ff_ = ffpath(font_file(cs.get('font'))); hl = cs.get("highlight_color", "#F6C14A"); anim = cs.get("animate")
        for cp in caps:
            if anim in ("highlight", "pop"):  # word-by-word: base words always drawn; the spoken word is drawn again in the highlight colour (and slightly larger for pop)
                for wd in caption_words_layout(cp["text"], W, H, cs, cp["start"], cp["end"], seq.get("transcript")):
                    common = f"fontsize={size_}:borderw={int(cs.get('borderw',3))}:bordercolor=black:fontfile='{ff_}'"
                    parts.append(f"drawtext={textfile(wd['text'])}:fontcolor={cs.get('color','white')}:x={wd['x']}:y={wd['y']}:{common}:enable='between(t,{cp['start']:.3f},{cp['end']:.3f})*not(between(t,{wd['s']:.3f},{wd['e']:.3f}))'")
                    if anim == "pop": parts.append(f"drawtext={textfile(wd['text'])}:fontcolor={hl}:fontsize={int(size_ * 1.07)}:x={wd['x'] - int(wd['w'] * 0.035)}:y={wd['y'] - int(size_ * 0.045)}:borderw={int(cs.get('borderw',3))}:bordercolor=black:fontfile='{ff_}':enable='between(t,{wd['s']:.3f},{wd['e']:.3f})'")
                    else: parts.append(f"drawtext={textfile(wd['text'])}:fontcolor={hl}:x={wd['x']}:y={wd['y']}:{common}:enable='between(t,{wd['s']:.3f},{wd['e']:.3f})'")
                continue
            parts.append(f"drawtext={textfile(wrap_caption(cp['text'], W, size_))}:fontsize={size_}:fontcolor={cs.get('color','white')}:borderw={int(cs.get('borderw',3))}:bordercolor=black:box={1 if cs.get('box') else 0}:boxcolor=black@0.55:boxborderw=14:x=(w-text_w)/2:y=h*{float(cs.get('y',0.74)):.3f}-text_h/2:fontfile='{ff_}':line_spacing=6:enable='between(t,{cp['start']:.3f},{cp['end']:.3f})'")
        f.append(f"[{layer}]" + ",".join(parts) + "[capd]"); layer = "capd"
    wm = preset.get("watermark")
    if wm and wm.get("path") and os.path.exists(wm["path"]):  # review/branding watermark: PNG bug in a corner, or centred, with opacity
        wi = len(inputs) + len(extra); extra[wm["path"]] = wi; sc = float(wm.get("scale", 0.18)); op = float(wm.get("opacity", 0.6)); pos = wm.get("position", "bottom_right"); pad_ = int(W * 0.03)
        xy = {"bottom_right": f"W-w-{pad_}:H-h-{pad_}", "bottom_left": f"{pad_}:H-h-{pad_}", "top_right": f"W-w-{pad_}:{pad_}", "top_left": f"{pad_}:{pad_}", "center": "(W-w)/2:(H-h)/2"}.get(pos, f"W-w-{pad_}:H-h-{pad_}")
        f.append(f"[{wi}:v]format=rgba,scale={int(W * sc)}:-1,colorchannelmixer=aa={op:.2f}[wmk]"); f.append(f"[{layer}][wmk]overlay=x={xy.split(':')[0]}:y={xy.split(':')[1]}:format=rgb,format=yuva420p[wmd]"); layer = "wmd"
    if preset.get("watermark_text") or preset.get("burn_tc"):  # review copy: watermark text and/or burnt-in timecode over the finished picture
        parts = []
        if preset.get("watermark_text"): parts.append(f"drawtext={textfile(str(preset['watermark_text']))}:fontsize={int(H * 0.028)}:fontcolor=white@0.35:borderw=2:bordercolor=black@0.35:x=w-text_w-{int(W * 0.03)}:y={int(H * 0.03)}:fontfile='{ffpath(bundled_font())}'")
        if preset.get("burn_tc"): parts.append(f"drawtext=text='%{{pts\\:hms}}':fontsize={int(H * 0.026)}:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=8:x=(w-text_w)/2:y=h-text_h-{int(H * 0.03)}:fontfile='{ffpath(bundled_font(mono=True))}'")
        f.append(f"[{layer}]" + ",".join(parts) + "[revw]"); layer = "revw"
    ow, oh = int(preset.get("out_w") or W), int(preset.get("out_h") or H)
    if (ow, oh) != (W, H):
        fit = preset.get("fit", "crop")
        post = f"scale={ow}:{oh}:force_original_aspect_ratio=increase,crop={ow}:{oh}" if fit == "crop" else f"scale={ow}:{oh}:force_original_aspect_ratio=decrease,pad={ow}:{oh}:(ow-iw)/2:(oh-ih)/2"
        f.append(f"[{layer}]{post},format=yuv420p[vout]")
    else: f.append(f"[{layer}]format=yuv420p[vout]")
    # audio
    a_in, m_ = [], 0; track_bus = {}
    if video_only:
        graph = ";\n".join(f); return [ffmpeg, "-hide_banner", "-y"] + [x for inp in inputs for x in inp] + [x for mp in extra for x in ("-loop", "1", "-framerate", f"{FPS}", "-i", mp)] + ["-filter_complex", graph, "-map", "[vout]"], graph
    solo_any = any(t.get("solo") for t in seq["tracks"])
    for tr in sorted([t for t in seq["tracks"] if not t.get("muted")], key=lambda t: t["index"]):
        if solo_any and not tr.get("solo"): continue
        tg = float(tr.get("gain_db", 0) or 0)
        for c in tr["clips"]:
            if c.get("enabled") is False: continue
            if not c.get("media_id") or not media[c["media_id"]].get("has_audio") or c.get("hold"): continue
            if tr["kind"] == "video" and (c.get("audio") or {}).get("linked") is False: continue
            if media[c["media_id"]].get("synthetic"):
                sy = media[c["media_id"]]["synthetic"]; m_ += 1; cd = clip_dur(c); au = c.get("audio") or {}; ms_ = int(round(c["start"] * 1000)); g = float(au.get("gain_db", 0.0))
                f.append(f"sine=frequency={int(sy.get('tone_hz', 1000))}:sample_rate=48000:duration={cd:.3f},volume={g - 20:.2f}dB,adelay={ms_}|{ms_},aresample=48000,aformat=channel_layouts=stereo[a{m_}]"); track_bus.setdefault(tr["id"], []).append(f"[a{m_}]"); continue
            m_ += 1; i = idx[c["media_id"]]; cd = clip_dur(c); au = c.get("audio") or {}; kf = c.get("keyframes") or {}
            off = float(media[c["media_id"]].get("sub_in", 0) or 0); mm_ = media[c["media_id"]]; ifac = (float(mm_["fps"]) / float(mm_["interpret_fps"])) if mm_.get("interpret_fps") and mm_.get("fps") else 1.0
            ch = [f"[{i}:a]atrim=start={(c['in_'] + off) / ifac:.4f}:end={(c['out'] + off) / ifac:.4f}", "asetpts=PTS-STARTPTS"] + ([f"atempo={min(max(1 / ifac, 0.5), 2.0):.4f}"] if abs(ifac - 1) > 1e-6 else []) + ["aformat=channel_layouts=stereo"] + channel_chain((c.get("audio") or {}).get("channels") or mm_.get("channel_mode")) + pan_chain((c.get("audio") or {}).get("pan"))
            if c.get("reverse"): ch.append("areverse")
            keep_pitch = (c.get("audio") or {}).get("maintain_pitch", True)
            def tempo_chain(spd):
                out_ = []
                if not keep_pitch: return [f"asetrate=48000*{spd:.5f},aresample=48000"] if abs(spd - 1) > 1e-6 else []
                while abs(spd - 1.0) > 1e-6:
                    step = min(max(spd, 0.5), 2.0); out_.append(f"atempo={step:.4f}"); spd = spd / step
                    if 0.999 < spd < 1.001: break
                return out_
            if c.get("time_remap"):
                # piecewise: one tempo per remap segment (average of the segment's start/end speed), concatenated
                segs = remap_segments(c); need = c["out"] - c["in_"]; parts = []; k_ = 0
                for (t0, t1, a, b_, sig) in segs:
                    gain = need - sig if t1 is None else (t1 - t0) * (a + b_) / 2
                    if gain <= 1e-4 or sig >= need: continue
                    s_end = min(need, sig + gain); avg = (a + b_) / 2 if t1 is not None else a
                    f.append(f"[{i}:a]atrim=start={c['in_'] + off + sig:.4f}:end={c['in_'] + off + s_end:.4f},asetpts=PTS-STARTPTS" + "".join("," + x for x in tempo_chain(avg)) + f"[a{m_}s{k_}]"); parts.append(f"[a{m_}s{k_}]"); k_ += 1
                    if s_end >= need - 1e-6: break
                f.append("".join(parts) + f"concat=n={len(parts)}:v=0:a=1[a{m_}c]"); ch = [f"[a{m_}c]anull"] + ch[2:]  # replace the trim/setpts head
            else: ch += tempo_chain(c.get("speed", 1.0))
            g = float(au.get("gain_db", 0.0))
            if kf.get("audio.gain_db"): ch.append(f"volume='pow(10,(({kf_expr(kf['audio.gain_db'], 't')}))/20)':eval=frame")
            elif abs(g) > 0.01: ch.append(f"volume={g:.2f}dB")
            fi = float(au.get("fade_in", 0) or 0); fo = float(au.get("fade_out", 0) or 0)
            at_in, at_out = c.get("audio_transition_in") or {}, c.get("audio_transition_out") or {}
            curve = {"constant_power": "qsin", "constant_gain": "tri", "exponential": "exp"}.get((at_in.get("type") or at_out.get("type") or ("constant_power" if au.get("constant_power", True) else "constant_gain")), "qsin")
            fi = max(fi, float(at_in.get("duration", 0) or 0)); fo = max(fo, float(at_out.get("duration", 0) or 0))
            if fi > 0: ch.append(f"afade=t=in:st=0:d={fi:.3f}:curve={curve}")
            if fo > 0: ch.append(f"afade=t=out:st={max(cd-fo,0):.3f}:d={fo:.3f}:curve={curve}")
            ms = int(round(c["start"]*1000)); ch.append(f"adelay={ms}|{ms}"); ch.append("aresample=48000,aformat=channel_layouts=stereo")
            f.append(",".join(ch) + f"[a{m_}]"); track_bus.setdefault(tr["id"], []).append(f"[a{m_}]")
    # track buses (per-track fx + gain) -> master (fx + gain + optional loudnorm)
    # NB: amix with a single input truncates to that input's length (FFmpeg quirk) — never mix one stream; pass it through instead.
    for tid, ins in track_bus.items():
        tr_ = next(t for t in seq["tracks"] if t["id"] == tid); tfx = audio_fx_chain(tr_.get("audio_fx")); tg = float(tr_.get("gain_db", 0) or 0)
        head = ("".join(ins) + f"amix=inputs={len(ins)}:normalize=0:duration=longest") if len(ins) > 1 else (ins[0] + "anull")
        f.append(head + "".join("," + x for x in tfx) + (f",volume={tg:.2f}dB" if abs(tg) > 0.01 else "") + f"[bus_{tid}]"); a_in.append(f"[bus_{tid}]")
    ln = ",loudnorm=I=-14:TP=-1:LRA=11" if preset.get("loudnorm") else ""
    if a_in:
        mfx = audio_fx_chain((seq.get("master") or {}).get("audio_fx")); mg = float((seq.get("master") or {}).get("gain_db", 0) or 0)
        head = ("".join(a_in) + f"amix=inputs={len(a_in)}:normalize=0:duration=longest") if len(a_in) > 1 else (a_in[0] + "anull")
        f.append(head + f",apad=whole_dur={total:.3f},atrim=0:{total:.3f}" + "".join("," + x for x in mfx) + (f",volume={mg:.2f}dB" if abs(mg) > 0.01 else "") + f"{ln}[aout]")
    else: f.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{total:.3f}[aout]")
    graph = ";\n".join(f)
    cmd = [ffmpeg, "-hide_banner", "-y"]
    for inp in inputs: cmd += inp
    for mp in extra: cmd += ["-loop", "1", "-framerate", f"{FPS}", "-i", mp]
    cmd += ["-filter_complex", graph, "-map", "[vout]", "-map", "[aout]"]
    rin, rout = seq.get("in_point"), seq.get("out_point")
    if preset.get("range") and rin is not None and rout is not None and rout > rin: cmd += ["-ss", f"{rin:.3f}", "-t", f"{rout - rin:.3f}"]
    else: cmd += ["-t", f"{total:.3f}"]
    fmt = preset.get("format", "h264"); vc = preset.get("vcodec", "libx264"); vb = preset.get("bitrate")
    if preset.get("chapters") and (seq.get("markers") or []) and fmt in ("h264", "hevc", "prores"):
        # embed sequence markers as chapters (FFMETADATA1) — YouTube and players pick these up
        in_off = rin if (preset.get("range") and rin is not None and rout is not None and rout > rin) else 0.0; end_t = (rout if in_off else total)
        ms = sorted([m for m in seq.get("markers") or [] if m.get("type", "comment") in ("chapter", "comment") and in_off <= m["time"] < end_t], key=lambda m: m["time"]); meta = ";FFMETADATA1\n"
        for i, m in enumerate(ms):
            t0 = int(max(0.0, m["time"] - in_off) * 1000); t1 = int(((ms[i + 1]["time"] if i + 1 < len(ms) else end_t) - in_off) * 1000)
            if t1 > t0: meta += f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={t0}\nEND={t1}\ntitle={(m.get('name') or f'Chapter {i + 1}').replace(chr(10), ' ')}\n"
        mp = os.path.join(_TMP, f"chapters_{hashlib.sha1(meta.encode()).hexdigest()[:10]}.txt"); open(mp, "w", encoding="utf-8").write(meta)
        k = cmd.index("-filter_complex"); cmd[k:k] = ["-i", mp]; cmd += ["-map_metadata", str(len(inputs) + len(extra))]
    def with_graph(g): j = cmd.index("-filter_complex"); cmd[j + 1] = g; return g
    if fmt == "audio":  # WAV / MP3 / AAC audio-only: keep only the audio filter lines so no video is decoded or composited
        import re as _re
        codec = {"wav": ["-c:a", "pcm_s16le"], "mp3": ["-c:a", "libmp3lame", "-b:a", "320k"], "aac": ["-c:a", "aac", "-b:a", "256k"]}[preset.get("acodec", "wav")]
        alines = [l for l in graph.split(";\n") if _re.search(r"\[\d+:a\]|^sine=|amix=|\[bus_|\[aout\]|anullsrc|\[a\d+[sc]?\]", l)]
        graph = with_graph(";\n".join(alines)); i_ = cmd.index("-map"); cmd = cmd[:i_] + ["-map", "[aout]", "-vn"] + codec + ["-ar", "48000", out_path]; return cmd, graph
    if fmt == "gif":
        graph = with_graph(graph.replace("[vout]", "[vpre]") + f";\n[vpre]fps={min(FPS, 15)},scale={min(W, int(preset.get('gif_width', 540)))}:-2:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=192[p];[s1][p]paletteuse=dither=bayer[vout];\n[aout]anullsink")
        i_ = cmd.index("-map"); cmd = cmd[:i_] + ["-map", "[vout]", "-an", "-loop", "0", out_path]; return cmd, graph
    if fmt == "png_sequence":
        graph = with_graph(graph + ";\n[aout]anullsink"); i_ = cmd.index("-map"); cmd = cmd[:i_] + ["-map", "[vout]", "-an", "-r", f"{FPS}", os.path.join(os.path.dirname(out_path), os.path.splitext(os.path.basename(out_path))[0] + "_%05d.png")]; return cmd, graph
    if fmt == "hevc": vc = preset.get("vcodec") if preset.get("vcodec", "").startswith("hevc") else "libx265"
    if fmt == "prores": vc = "prores_ks"
    if fmt == "webm":  # VP9 + Opus — web/CDN friendly, alpha-capable containers aside
        i_ = cmd.index("-map"); tail = cmd[i_:]; cmd = cmd[:i_] + ["-map", "[vout]", "-map", "[aout]"] + [x for x in tail if x not in ("-map", "[vout]", "[aout]")][:0]
        cmd += [t for t in tail if t not in ("-map", "[vout]", "[aout]")]
        cmd += ["-c:v", "libvpx-vp9", "-b:v", vb or "0", "-crf", str(preset.get("crf", 30)), "-row-mt", "1", "-deadline", "good", "-cpu-used", "2", "-pix_fmt", "yuv420p", "-r", f"{FPS}", "-c:a", "libopus", "-b:a", "128k", "-ar", "48000", out_path]; return cmd, graph
    if fmt == "av1":
        enc = "libsvtav1" if has_encoder("libsvtav1") else "libaom-av1"
        cmd += ["-c:v", enc] + (["-preset", "8", "-crf", str(preset.get("crf", 32))] if enc == "libsvtav1" else ["-cpu-used", "6", "-crf", str(preset.get("crf", 32)), "-b:v", "0"]) + ["-pix_fmt", "yuv420p", "-r", f"{FPS}", "-g", str(int(FPS*2)), "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", out_path]; return cmd, graph
    cmd += ["-c:v", vc]
    if vc == "libx264": cmd += ["-preset", preset.get("x264_preset", "medium")]
    elif vc == "libx265": cmd += ["-preset", preset.get("x264_preset", "medium"), "-tag:v", "hvc1"]
    elif vc == "h264_nvenc" or vc == "hevc_nvenc": cmd += ["-preset", "p5", "-rc", "vbr"]
    elif vc == "prores_ks": cmd += ["-profile:v", str(preset.get("prores_profile", 3)), "-vendor", "apl0", "-pix_fmt", "yuv422p10le"]
    if vc != "prores_ks": cmd += ["-pix_fmt", "yuv420p"]
    cmd += ["-r", f"{FPS}", "-g", str(int(FPS*2)), "-c:a", "aac" if not out_path.endswith(".mov") or vc != "prores_ks" else "pcm_s16le", "-ar", "48000"]
    if not (vc == "prores_ks"): cmd += ["-b:a", "192k", "-movflags", "+faststart"]
    if vc in ("libx264", "libx265"): cmd += (["-b:v", vb, "-maxrate", vb, "-bufsize", "4M"] if vb else ["-crf", str(preset.get("crf", 18 if vc == "libx264" else 22))])
    elif vc != "prores_ks": cmd += ["-b:v", vb or preset.get("hw_bitrate", "10M"), "-maxrate", vb or preset.get("hw_bitrate", "10M"), "-bufsize", "4M"]
    cmd.append(out_path); return cmd, graph

def render(project, sequence_id, out_path, preset=None, log=None, progress=None, proc_holder=None):
    """Run the export. progress(fraction) is called as FFmpeg reports out_time; proc_holder (dict) receives the Popen so a queue can cancel it."""
    cmd, graph = build_command(project, sequence_id, out_path, preset)
    if log: log.write(" ".join(shlex.quote(c) for c in cmd) + "\n")
    seq = next(s for s in project["sequences"] if s["id"] == sequence_id); total = seq_total(seq)
    if preset and preset.get("range") and seq.get("in_point") is not None and seq.get("out_point") is not None: total = max(0.01, seq["out_point"] - seq["in_point"])
    cmd = cmd[:1] + ["-progress", "pipe:1", "-nostats"] + cmd[1:]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc_holder is not None: proc_holder["proc"] = proc
    # stderr MUST be drained concurrently. This used to read stdout to exhaustion and only
    # then call proc.stderr.read(), which deadlocks on any sequence whose filtergraph is big
    # enough to fill the stderr pipe buffer: FFmpeg blocks writing stderr, therefore stops
    # writing stdout, therefore the loop below never ends. Measured on a 7-clip export with
    # titles, a letterbox and a speed remap -- ffmpeg sat for nine minutes having used 0.3
    # seconds of CPU. The segmented path never hit it because it uses communicate() and its
    # per-segment graphs are small, which is why this only surfaced once a delivery master
    # was forced down the full path.
    chunks = []
    t_err = threading.Thread(target=lambda: chunks.append(proc.stderr.read()), daemon=True)
    t_err.start()
    for line in proc.stdout:
        if line.startswith("out_time_ms=") and progress:
            try: progress(min(0.999, (int(line.split("=")[1]) / 1e6) / max(total, 0.01)))
            except ValueError: pass
    proc.wait(); t_err.join(timeout=10); err = "".join(chunks)
    if proc_holder is not None and proc_holder.get("cancelled"): raise RuntimeError("cancelled")
    if proc.returncode != 0: raise RuntimeError(err[-4000:])
    if progress: progress(1.0)
    return out_path

def render_frame(project, sequence_id, t, out_png):
    cmd, graph = build_command(project, sequence_id, out_png, {"crf": 18}, video_only=True)
    cmd2 = cmd + ["-ss", f"{t:.3f}", "-frames:v", "1", "-update", "1", out_png]
    r = subprocess.run(cmd2, capture_output=True, text=True)
    if r.returncode != 0: raise RuntimeError(r.stderr[-2000:])
    return out_png

if __name__ == "__main__":
    import sys; proj = json.load(open(sys.argv[1])); print(render(proj, sys.argv[2], sys.argv[3]))


# ======================================================================================================================
# Incremental ("smart") export: the sequence is split into video segments at clean cut points; each segment is rendered
# once and cached by a hash of everything that affects its pixels. A later export re-encodes only the segments whose
# content changed (a re-worded title, a trimmed shot) and stitches the rest from cache. Audio is re-rendered whole (cheap)
# and muxed at the end, so loudness normalisation still sees the entire programme.
# ======================================================================================================================
def _forbidden_intervals(seq):
    """Time ranges no segment boundary may fall inside: transition regions, time-remapped/held clips, and nested clips' transitions."""
    out = []
    for t in seq["tracks"]:
        if t["kind"] != "video": continue
        for c in t["clips"]:
            d = clip_dur(c); s0, s1 = c["start"], c["start"] + d
            ti = float((c.get("transition_in") or {}).get("duration", 0) or 0); to = float((c.get("transition_out") or {}).get("duration", 0) or 0)
            if ti > 0: out.append((s0 - ti, s0 + ti))           # centre/end alignment may pull the region before the cut
            if to > 0: out.append((s1 - to, s1 + to))
            if c.get("time_remap") or c.get("hold") or c.get("reverse"): out.append((s0, s1))
            g = c.get("graphic")
            if g and any((L.get("anim_in") or {}).get("type") not in (None, "none") or (L.get("anim_out") or {}).get("type") not in (None, "none") for L in g.get("layers", [])): out.append((s0, s1))  # layer animations are clip-relative
            if any(k.startswith("g") and "." in k for k in (c.get("keyframes") or {})): out.append((s0, s1))
    return out

def segment_boundaries(seq, max_len=4.0):
    total = seq_total(seq)
    if total <= 0: return [0.0, 0.0]
    fps = float(seq["fps"]); forb = _forbidden_intervals(seq)
    def ok(t): return not any(a - 1e-6 < t < b + 1e-6 for a, b in forb)
    cuts = sorted({round(c["start"], 4) for t in seq["tracks"] if t["kind"] == "video" for c in t["clips"]} | {round(c["start"] + clip_dur(c), 4) for t in seq["tracks"] if t["kind"] == "video" for c in t["clips"]})
    cuts = [t for t in cuts if 0 < t < total and ok(t)]
    pts = [0.0]
    for t in cuts + [total]:
        while t - pts[-1] > max_len * 1.5:  # long span: add frame-aligned boundaries every max_len where allowed
            cand = round(round((pts[-1] + max_len) * fps) / fps, 4)
            if cand >= t - 0.25: break
            if ok(cand): pts.append(cand)
            else:
                nxt = next((b for a, b in sorted(forb) if a - 1e-6 < cand < b + 1e-6), None); cand2 = round(round((nxt + 1 / fps) * fps) / fps, 4) if nxt is not None else None
                if cand2 is None or cand2 >= t - 0.25: break
                pts.append(cand2)
        if t - pts[-1] > 1 / fps / 2: pts.append(round(t, 4))
    if pts[-1] < total - 1e-6: pts.append(round(total, 4))
    return pts

def _shift_kf(kf, dt):
    return {k: [dict(x, t=round(x["t"] + dt, 5)) for x in v] for k, v in (kf or {}).items()}

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

def chunk_key(project, chunk, preset):
    media = project["media"]; used = sorted({c["media_id"] for t in chunk["tracks"] for c in t["clips"] if c.get("media_id")})
    mstate = [(m, media[m].get("path"), media[m].get("interpret_fps"), media[m].get("stab_trf"), media[m].get("hdr"), media[m].get("synthetic")) for m in used if m in media]
    nested = [json.dumps(next((s for s in project["sequences"] if s["id"] == c["sequence_id"]), None), sort_keys=True) for t in chunk["tracks"] for c in t["clips"] if c.get("sequence_id")]
    vkeys = {k: preset.get(k) for k in ("crf", "bitrate", "vcodec", "out_w", "out_h", "fit", "x264_preset", "watermark", "watermark_text", "burn_tc")}
    vis = json.dumps({"tracks": [{k: v for k, v in t.items() if k not in ("gain_db", "audio_fx", "solo")} for t in chunk["tracks"]], "captions": chunk["captions"], "caption_style": chunk.get("caption_style"), "wh": [chunk["width"], chunk["height"], chunk["fps"]], "guides": None}, sort_keys=True)
    return hashlib.sha1((vis + json.dumps(mstate, sort_keys=True) + "".join(nested) + json.dumps(vkeys, sort_keys=True) + "v2").encode()).hexdigest()[:20]

def render_incremental(project, sequence_id, out_path, preset=None, log=None, progress=None, proc_holder=None, cache_dir=None):
    """Segment-cached export. Returns (out_path, stats). Falls back to a normal render when the format cannot be stitched."""
    preset = dict(preset or {}); seq = next(s for s in project["sequences"] if s["id"] == sequence_id)
    fmt = preset.get("format", "h264")
    if fmt not in ("h264", "hevc") or preset.get("range") or preset.get("burn_tc"): return render(project, sequence_id, out_path, preset, log, progress, proc_holder), {"segments": 0, "reused": 0, "mode": "full"}  # burnt-in timecode needs absolute pts
    cache_dir = cache_dir or os.path.join(os.path.dirname(out_path), "cache"); os.makedirs(cache_dir, exist_ok=True)
    # preset["full"] = ONE segment covering the whole programme, so there are no seams to
    # stitch. Segment stitching leaves a BLACK FRAME one frame before a boundary: measured on
    # a 460-frame export, f191 = 7.9583s read luminance 0.00 between neighbours at 68.7 and
    # 70.8, exactly one frame before the 8.0s boundary. Invisible in stills -- a five-critic
    # panel judging nine frames sampled between the flashes -- and a strobe in motion.
    #
    # This does the same job as calling render() directly but through the path that actually
    # works: render() deadlocks on a large filtergraph (it drains stdout to exhaustion before
    # ever reading stderr, so FFmpeg blocks on a full stderr pipe and never writes stdout
    # again -- observed sitting for nine minutes on 0.2s of CPU). That deadlock is fixed too,
    # but a delivery master should not depend on the rarely-taken branch.
    pts = [0.0, round(seq_total(seq), 4)] if preset.get("full") else segment_boundaries(seq)
    segs = list(zip(pts, pts[1:])); FPS = float(seq["fps"]); vc = preset.get("vcodec", "libx264")
    if vc not in ("libx264", "libx265"): return render(project, sequence_id, out_path, preset, log, progress, proc_holder), {"segments": 0, "reused": 0, "mode": "full"}
    files, reused = [], 0; total = len(segs)
    for i, (t0, t1) in enumerate(segs):
        chunk = chunk_sequence(seq, t0, t1); key = chunk_key(project, chunk, preset); f = os.path.join(cache_dir, f"{key}.mp4")
        if os.path.exists(f) and os.path.getsize(f) > 0: reused += 1
        else:
            cproj = dict(project, sequences=[s for s in project["sequences"] if s["id"] != sequence_id] + [dict(chunk, id=sequence_id)])
            cmd, graph = build_command(cproj, sequence_id, f, preset, video_only=True)
            g = max(1, int(round(FPS * 2)))
            cmd += ["-t", f"{t1 - t0:.4f}", "-c:v", vc, "-preset", preset.get("x264_preset", "medium"), "-pix_fmt", "yuv420p", "-r", f"{FPS:g}", "-g", str(g), "-keyint_min", str(g), "-sc_threshold", "0", "-video_track_timescale", "90000", "-an"]
            cmd += ["-b:v", preset["bitrate"], "-maxrate", preset["bitrate"], "-bufsize", "4M"] if preset.get("bitrate") else ["-crf", str(preset.get("crf", 18 if vc == "libx264" else 22))]
            if vc == "libx265": cmd += ["-tag:v", "hvc1", "-x265-params", f"keyint={g}:min-keyint={g}:scenecut=0"]
            cmd += ["-movflags", "+faststart", f + ".part.mp4"]
            if log: log.write(f"# segment {i + 1}/{total} [{t0:.3f}, {t1:.3f})\n" + " ".join(shlex.quote(c) for c in cmd) + "\n")
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            if proc_holder is not None: proc_holder["proc"] = proc
            _, err = proc.communicate()
            if proc_holder is not None and proc_holder.get("cancelled"): raise RuntimeError("cancelled")
            if proc.returncode != 0: raise RuntimeError(f"segment {i + 1} failed: " + err[-2000:])
            os.replace(f + ".part.mp4", f)
        files.append(f)
        if progress: progress(0.85 * (i + 1) / total)
    # stitch video, render audio for the whole programme, mux
    lst = out_path + ".segments.txt"; open(lst, "w").write("".join(f"file '{ffpath(f).replace(chr(92) + ':', ':')}'\n" for f in files))
    vcat = out_path + ".video.mp4"; r = subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", "-movflags", "+faststart", vcat], capture_output=True, text=True)
    if r.returncode != 0: raise RuntimeError("stitch failed: " + r.stderr[-1500:])
    if progress: progress(0.9)
    aud = out_path + ".audio.m4a"; acmd, _ = build_command(project, sequence_id, aud, dict(preset, format="audio", acodec="aac"))
    r = subprocess.run(acmd, capture_output=True, text=True)
    if r.returncode != 0: raise RuntimeError("audio failed: " + r.stderr[-1500:])
    if progress: progress(0.96)
    mux = ["ffmpeg", "-hide_banner", "-y", "-i", vcat, "-i", aud]
    if preset.get("chapters") and (seq.get("markers") or []):
        total = seq_total(seq); ms = sorted([m for m in seq.get("markers") or [] if m.get("type", "comment") in ("chapter", "comment")], key=lambda m: m["time"]); meta = ";FFMETADATA1\n"
        for i, m in enumerate(ms):
            t0 = int(m["time"] * 1000); t1 = int((ms[i + 1]["time"] if i + 1 < len(ms) else total) * 1000)
            if t1 > t0: meta += f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={t0}\nEND={t1}\ntitle={(m.get('name') or f'Chapter {i + 1}').replace(chr(10), ' ')}\n"
        mp = out_path + ".chapters.txt"; open(mp, "w", encoding="utf-8").write(meta); mux += ["-i", mp, "-map_metadata", "2"]
    mux += ["-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-shortest", "-movflags", "+faststart", out_path]
    r = subprocess.run(mux, capture_output=True, text=True)
    if r.returncode != 0: raise RuntimeError("mux failed: " + r.stderr[-1500:])
    try: os.remove(out_path + ".chapters.txt")
    except OSError: pass
    for f in (lst, vcat, aud):
        try: os.remove(f)
        except OSError: pass
    # cache housekeeping: keep the newest ~4 GB
    try:
        entries = sorted(((os.path.getmtime(os.path.join(cache_dir, x)), os.path.join(cache_dir, x)) for x in os.listdir(cache_dir) if x.endswith(".mp4")), reverse=True); size = 0
        for mt, pth in entries:
            size += os.path.getsize(pth)
            if size > 4e9: os.remove(pth)
    except OSError: pass
    if progress: progress(1.0)
    return out_path, {"segments": total, "reused": reused, "mode": "incremental"}

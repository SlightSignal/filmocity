"""Effect registry: Premiere-style video and audio effect stacks mapped to FFmpeg filters, with parameter specs the UI
renders automatically and preview hints the canvas compositor can approximate. A clip carries `fx_stack` (video) and
`afx_stack` (audio): lists of {type, enabled, params}. Order matters and is preserved."""
import math

def _c(v): v = str(v); return ("0x" + v[1:]) if v.startswith("#") else v
def _font_mono():
    import os
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts", "DejaVuSansMono-Bold.ttf")
    p = p if os.path.exists(p) else "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
    return p.replace("\\", "/").replace(":", "\\:")
def P(**kw): return kw  # param spec helper: P(name={default, min, max, step, kind})

VIDEO_FX = {
  # ---- Adjust ----
  "procamp": {"name": "ProcAmp", "cat": "Adjust", "params": {"brightness": [0, -100, 100, 1], "contrast": [100, 0, 300, 1], "hue": [0, -180, 180, 1], "saturation": [100, 0, 300, 1]},
    "ff": lambda p: f"eq=brightness={p['brightness']/200:.3f}:contrast={p['contrast']/100:.3f}:saturation={p['saturation']/100:.3f},hue=h={p['hue']}", "css": lambda p: f"brightness({1+p['brightness']/200:.3f}) contrast({p['contrast']/100:.3f}) saturate({p['saturation']/100:.3f}) hue-rotate({p['hue']}deg)"},
  "levels": {"name": "Levels", "cat": "Adjust", "params": {"input_black": [0, 0, 254, 1], "input_white": [255, 1, 255, 1], "output_black": [0, 0, 254, 1], "output_white": [255, 1, 255, 1], "gamma": [1.0, 0.1, 5, 0.01]},
    "ff": lambda p: f"curves=all='{p['input_black']/255:.3f}/{p['output_black']/255:.3f} {p['input_white']/255:.3f}/{p['output_white']/255:.3f}',eq=gamma={p['gamma']:.3f}"},
  "gamma": {"name": "Gamma Correction", "cat": "Adjust", "params": {"gamma": [1.0, 0.1, 5, 0.01]}, "ff": lambda p: f"eq=gamma={p['gamma']:.3f}"},
  "extract": {"name": "Extract", "cat": "Adjust", "params": {"black": [0, 0, 254, 1], "white": [255, 1, 255, 1], "softness": [0, 0, 100, 1]},
    "ff": lambda p: f"hue=s=0,curves=all='{p['black']/255:.3f}/0 {p['white']/255:.3f}/1'", "css": lambda p: "grayscale(1)"},
  "video_limiter": {"name": "Video Limiter", "cat": "Adjust", "params": {"min": [16, 0, 128, 1], "max": [235, 128, 255, 1]}, "ff": lambda p: f"limiter=min={p['min']}:max={p['max']}"},
  # ---- Blur & Sharpen ----
  "gaussian_blur": {"name": "Gaussian Blur", "cat": "Blur & Sharpen", "params": {"blurriness": [10, 0, 200, 0.5]}, "ff": lambda p: f"gblur=sigma={p['blurriness']/4:.2f}", "css": lambda p: f"blur({p['blurriness']/4:.1f}px)"},
  "camera_blur": {"name": "Camera Blur", "cat": "Blur & Sharpen", "params": {"percent": [10, 0, 100, 1]}, "ff": lambda p: f"avgblur=sizeX={max(1, int(p['percent']/3))}", "css": lambda p: f"blur({p['percent']/6:.1f}px)"},
  "directional_blur": {"name": "Directional Blur", "cat": "Blur & Sharpen", "params": {"direction": [0, 0, 360, 1], "length": [10, 0, 200, 1]}, "ff": lambda p: f"dblur=angle={p['direction']}:radius={max(1, int(p['length']/4))}"},
  "sharpen": {"name": "Sharpen", "cat": "Blur & Sharpen", "params": {"amount": [25, 0, 200, 1]}, "ff": lambda p: f"unsharp=5:5:{min(3.0, p['amount']/50):.2f}:5:5:0"},
  "unsharp_mask": {"name": "Unsharp Mask", "cat": "Blur & Sharpen", "params": {"amount": [50, 0, 300, 1], "radius": [3, 3, 13, 2], "threshold": [0, 0, 100, 1]}, "ff": lambda p: f"unsharp={int(p['radius'])|1}:{int(p['radius'])|1}:{min(5.0, p['amount']/60):.2f}:3:3:0"},
  # ---- Color Correction / Image Control ----
  "black_white": {"name": "Black & White", "cat": "Image Control", "params": {}, "ff": lambda p: "hue=s=0", "css": lambda p: "grayscale(1)"},
  "invert": {"name": "Invert", "cat": "Channel", "params": {}, "ff": lambda p: "negate", "css": lambda p: "invert(1)"},
  "tint": {"name": "Tint", "cat": "Color Correction", "params": {"black_to": ["#000000", None, None, None, "color"], "white_to": ["#ffffff", None, None, None, "color"], "amount": [100, 0, 100, 1]},
    "ff": lambda p: _tint_ff(p), "css": lambda p: f"sepia({p['amount']/100:.2f})"},
  "colorize": {"name": "Colorize", "cat": "Color Correction", "params": {"hue": [30, 0, 360, 1], "saturation": [0.5, 0, 1, 0.01], "lightness": [0.5, 0, 1, 0.01], "mix": [1, 0, 1, 0.01]}, "ff": lambda p: f"colorize=hue={p['hue']}:saturation={p['saturation']:.2f}:lightness={p['lightness']:.2f}:mix={p['mix']:.2f}"},
  "color_balance_rgb": {"name": "Color Balance (RGB)", "cat": "Color Correction", "params": {"red": [100, 0, 200, 1], "green": [100, 0, 200, 1], "blue": [100, 0, 200, 1]}, "ff": lambda p: f"colorchannelmixer=rr={p['red']/100:.3f}:gg={p['green']/100:.3f}:bb={p['blue']/100:.3f}"},
  "channel_mixer": {"name": "Channel Mixer", "cat": "Channel", "params": {"rr": [1, -2, 2, 0.01], "rg": [0, -2, 2, 0.01], "rb": [0, -2, 2, 0.01], "gr": [0, -2, 2, 0.01], "gg": [1, -2, 2, 0.01], "gb": [0, -2, 2, 0.01], "br": [0, -2, 2, 0.01], "bg": [0, -2, 2, 0.01], "bb": [1, -2, 2, 0.01]},
    "ff": lambda p: "colorchannelmixer=" + ":".join(f"{k}={p[k]:.3f}" for k in ("rr", "rg", "rb", "gr", "gg", "gb", "br", "bg", "bb"))},
  "leave_color": {"name": "Leave Color", "cat": "Color Correction", "params": {"color": ["#e8631c", None, None, None, "color"], "similarity": [0.2, 0.01, 1, 0.01], "blend": [0.1, 0, 1, 0.01]}, "ff": lambda p: f"colorhold=color={_c(p['color'])}:similarity={p['similarity']:.3f}:blend={p['blend']:.3f}"},
  "rgb_shift": {"name": "RGB Shift (chromatic aberration)", "cat": "Stylize", "params": {"rh": [4, -64, 64, 1], "rv": [0, -64, 64, 1], "bh": [-4, -64, 64, 1], "bv": [0, -64, 64, 1]}, "ff": lambda p: f"rgbashift=rh={int(p['rh'])}:rv={int(p['rv'])}:bh={int(p['bh'])}:bv={int(p['bv'])}"},
  # ---- Distort ----
  "flip_h": {"name": "Horizontal Flip", "cat": "Transform", "params": {}, "ff": lambda p: "hflip"},
  "flip_v": {"name": "Vertical Flip", "cat": "Transform", "params": {}, "ff": lambda p: "vflip"},
  "mirror": {"name": "Mirror", "cat": "Distort", "params": {"axis": ["horizontal", None, None, None, "select:horizontal,vertical"]},
    "ff": lambda p: ("crop=iw/2:ih:0:0,split[m0][m1];[m1]hflip[m2];[m0][m2]hstack" if p["axis"] == "horizontal" else "crop=iw:ih/2:0:0,split[m0][m1];[m1]vflip[m2];[m0][m2]vstack"), "graph": True},
  "corner_pin": {"name": "Corner Pin", "cat": "Distort", "params": {"x0": [0, -0.5, 1.5, 0.001], "y0": [0, -0.5, 1.5, 0.001], "x1": [1, -0.5, 1.5, 0.001], "y1": [0, -0.5, 1.5, 0.001], "x2": [0, -0.5, 1.5, 0.001], "y2": [1, -0.5, 1.5, 0.001], "x3": [1, -0.5, 1.5, 0.001], "y3": [1, -0.5, 1.5, 0.001]},
    "ff": lambda p: f"perspective=x0=W*{p['x0']:.3f}:y0=H*{p['y0']:.3f}:x1=W*{p['x1']:.3f}:y1=H*{p['y1']:.3f}:x2=W*{p['x2']:.3f}:y2=H*{p['y2']:.3f}:x3=W*{p['x3']:.3f}:y3=H*{p['y3']:.3f}:sense=destination"},
  "lens_distortion": {"name": "Lens Distortion", "cat": "Distort", "params": {"k1": [0, -1, 1, 0.01], "k2": [0, -1, 1, 0.01]}, "ff": lambda p: f"lenscorrection=k1={p['k1']:.3f}:k2={p['k2']:.3f}"},
  "stabilize": {"name": "Warp Stabilizer", "cat": "Distort", "params": {"smoothing": [10, 0, 100, 1], "zoom": [0, -20, 20, 1], "crop": ["keep", None, None, None, "select:keep,black"]}, "ff": None, "note": "Analyze once from Effect Controls; applied before the clip is trimmed."},
  # ---- Generate / Video ----
  "grid": {"name": "Grid", "cat": "Generate", "params": {"width": [64, 4, 512, 1], "height": [64, 4, 512, 1], "thickness": [2, 1, 20, 1], "color": ["#ffffff", None, None, None, "color"], "opacity": [0.5, 0, 1, 0.01]}, "ff": lambda p: f"drawgrid=width={int(p['width'])}:height={int(p['height'])}:thickness={int(p['thickness'])}:color={_c(p['color'])}@{p['opacity']:.2f}"},
  "timecode": {"name": "Timecode", "cat": "Video", "params": {"size": [48, 8, 200, 1], "position": ["bottom", None, None, None, "select:bottom,top"]}, "ff": lambda p, fps=30: f"drawtext=timecode='00\\:00\\:00\\:00':rate={fps}:fontsize={int(p['size'])}:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=8:x=(w-text_w)/2:y={'h-text_h-40' if p['position']=='bottom' else '40'}:fontfile='{_font_mono()}'", "needs_fps": True},
  # ---- Keying ----
  "luma_key": {"name": "Luma Key", "cat": "Keying", "params": {"threshold": [0.1, 0, 1, 0.01], "tolerance": [0.1, 0, 1, 0.01], "softness": [0.05, 0, 1, 0.01]}, "ff": lambda p: f"lumakey=threshold={p['threshold']:.3f}:tolerance={p['tolerance']:.3f}:softness={p['softness']:.3f}"},
  "ultra_key": {"name": "Ultra Key", "cat": "Keying", "params": {"color": ["#00ff00", None, None, None, "color"], "similarity": [0.25, 0.01, 1, 0.01], "blend": [0.1, 0, 1, 0.01], "despill": ["green", None, None, None, "select:green,blue,none"]},
    "ff": lambda p: f"chromakey=color={_c(p['color'])}:similarity={p['similarity']:.3f}:blend={p['blend']:.3f}" + (f",despill=type={p['despill']}" if p['despill'] != 'none' else "")},
  "color_key": {"name": "Color Key", "cat": "Keying", "params": {"color": ["#00ff00", None, None, None, "color"], "similarity": [0.2, 0.01, 1, 0.01], "blend": [0.05, 0, 1, 0.01]}, "ff": lambda p: f"colorkey=color={_c(p['color'])}:similarity={p['similarity']:.3f}:blend={p['blend']:.3f}"},
  # ---- Noise & Grain ----
  "add_noise": {"name": "Add Noise", "cat": "Noise & Grain", "params": {"amount": [20, 0, 100, 1], "temporal": [1, 0, 1, 1, "bool"]}, "ff": lambda p: f"noise=alls={int(p['amount'])}:allf={'t' if p['temporal'] else 'u'}"},
  "median": {"name": "Median", "cat": "Noise & Grain", "params": {"radius": [3, 1, 64, 1]}, "ff": lambda p: f"median=radius={int(p['radius'])}"},
  # ---- Stylize ----
  "emboss": {"name": "Emboss", "cat": "Stylize", "params": {}, "ff": lambda p: "convolution='-2 -1 0 -1 1 1 0 1 2:-2 -1 0 -1 1 1 0 1 2:-2 -1 0 -1 1 1 0 1 2:0 0 0 0 1 0 0 0 0'"},
  "find_edges": {"name": "Find Edges", "cat": "Stylize", "params": {"low": [0.1, 0, 1, 0.01], "high": [0.4, 0, 1, 0.01], "mode": ["wires", None, None, None, "select:wires,colormix,canny"]}, "ff": lambda p: f"edgedetect=low={p['low']:.2f}:high={p['high']:.2f}:mode={p['mode']}"},
  "mosaic": {"name": "Mosaic", "cat": "Stylize", "params": {"block": [16, 2, 200, 1]}, "ff": lambda p: f"pixelize=width={int(p['block'])}:height={int(p['block'])}"},
  "posterize": {"name": "Posterize", "cat": "Stylize", "params": {"levels": [4, 2, 32, 1]}, "ff": lambda p: f"lutrgb=r='trunc(val/{256/int(p['levels'])})*{256/int(p['levels'])}':g='trunc(val/{256/int(p['levels'])})*{256/int(p['levels'])}':b='trunc(val/{256/int(p['levels'])})*{256/int(p['levels'])}'"},
  "solarize": {"name": "Solarize", "cat": "Stylize", "params": {"threshold": [0.5, 0, 1, 0.01]}, "ff": lambda p: f"curves=all='0/0 {p['threshold']:.3f}/1 1/0'"},
  "threshold": {"name": "Threshold", "cat": "Stylize", "params": {"level": [128, 0, 255, 1]}, "ff": lambda p: f"hue=s=0,lutyuv=y='if(gt(val,{int(p['level'])}),255,0)':u=128:v=128"},
  "replicate": {"name": "Replicate", "cat": "Stylize", "params": {"count": [2, 2, 8, 1]}, "ff": lambda p: f"scale=iw/{int(p['count'])}:ih/{int(p['count'])},tile={int(p['count'])}x{int(p['count'])}"},
  "vignette_fx": {"name": "Vignette", "cat": "Stylize", "params": {"amount": [0.5, 0, 1, 0.05]}, "ff": lambda p: f"vignette=angle={min(1.0, p['amount']) * math.pi / 2.2:.4f}"},
  "vibrance_fx": {"name": "Vibrance", "cat": "Color Correction", "params": {"amount": [0.5, -2, 2, 0.05]}, "ff": lambda p: f"vibrance=intensity={p['amount']:.3f}", "css": lambda p: f"saturate({1 + p['amount'] * 0.5:.2f})"},
  # ---- Time ----
  "echo": {"name": "Echo", "cat": "Time", "params": {"frames": [4, 2, 16, 1]}, "ff": lambda p: f"tmix=frames={int(p['frames'])}"},
  "posterize_time": {"name": "Posterize Time", "cat": "Time", "params": {"rate": [8, 1, 30, 1]}, "ff": lambda p, fps=30: f"fps={int(p['rate'])},fps={fps}", "needs_fps": True},
  # ---- Perspective ----
  "track_matte": {"name": "Track Matte Key", "cat": "Keying", "params": {"track": ["V2", None, None, None, "text"], "type": ["alpha", None, None, None, "select:alpha,luma"], "invert": [0, 0, 1, 1, "bool"]}, "ff": None, "note": "Uses the named video track as an alpha or luma matte, including hidden output, timing, transforms and opacity. Hide that track's output to use it only as a matte. Use Render In to Out or export to view the result; the live Program Monitor does not display track mattes."},
  "hsl_secondary": {"name": "HSL Secondary", "cat": "Color Correction", "params": {"key_color": ["#3aa0ff", None, None, None, "color"], "range": [0.25, 0.02, 1, 0.01], "softness": [0.1, 0, 1, 0.01], "hue_shift": [0, -180, 180, 1], "saturation": [1.0, 0, 3, 0.05], "lightness": [0, -1, 1, 0.01], "invert": [0, 0, 1, 1, "bool"]}, "ff": None, "graph_secondary": True, "note": "Qualifies pixels near the key color (range/softness), then applies hue shift, saturation and lightness only there. Renders exactly; previews unqualified."},
  "drop_shadow": {"name": "Drop Shadow", "cat": "Perspective", "params": {"distance": [12, 0, 200, 1], "angle": [135, 0, 360, 1], "softness": [8, 0, 60, 1], "opacity": [0.6, 0, 1, 0.05]}, "ff": None, "graph_shadow": True},
}

def _tint_ff(p):
    def rgb(c): c = str(c).lstrip("#"); return [int(c[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    b, w = rgb(p["black_to"]), rgb(p["white_to"]); a = p["amount"] / 100
    def pts(k): return f"0/{b[k]*a:.3f} 1/{1 - (1 - w[k]) * a:.3f}"
    return f"hue=s={1 - a:.3f},curves=r='{pts(0)}':g='{pts(1)}':b='{pts(2)}'"

AUDIO_FX = {
  "amplify": {"name": "Amplify", "cat": "Amplitude", "params": {"gain_db": [0, -40, 20, 0.5]}, "ff": lambda p: f"volume={p['gain_db']:.2f}dB"},
  "bass": {"name": "Bass", "cat": "Filter & EQ", "params": {"gain_db": [3, -20, 20, 0.5], "frequency": [100, 40, 400, 1]}, "ff": lambda p: f"bass=g={p['gain_db']:.1f}:f={int(p['frequency'])}"},
  "treble": {"name": "Treble", "cat": "Filter & EQ", "params": {"gain_db": [3, -20, 20, 0.5], "frequency": [6000, 2000, 12000, 50]}, "ff": lambda p: f"treble=g={p['gain_db']:.1f}:f={int(p['frequency'])}"},
  "highpass": {"name": "Highpass", "cat": "Filter & EQ", "params": {"frequency": [80, 20, 2000, 1]}, "ff": lambda p: f"highpass=f={int(p['frequency'])}"},
  "lowpass": {"name": "Lowpass", "cat": "Filter & EQ", "params": {"frequency": [8000, 200, 20000, 10]}, "ff": lambda p: f"lowpass=f={int(p['frequency'])}"},
  "notch": {"name": "Notch", "cat": "Filter & EQ", "params": {"frequency": [60, 20, 20000, 1], "width": [10, 1, 500, 1]}, "ff": lambda p: f"bandreject=f={int(p['frequency'])}:w={int(p['width'])}"},
  "parametric_eq": {"name": "Parametric EQ (band)", "cat": "Filter & EQ", "params": {"frequency": [1000, 20, 20000, 1], "q": [1.0, 0.1, 10, 0.1], "gain_db": [0, -20, 20, 0.5]}, "ff": lambda p: f"equalizer=f={int(p['frequency'])}:t=q:w={p['q']:.2f}:g={p['gain_db']:.1f}"},
  "graphic_eq": {"name": "Graphic EQ (10 band)", "cat": "Filter & EQ", "params": {f"b{i}": [0, -12, 12, 0.5] for i in range(10)},
    "ff": lambda p: "superequalizer=" + ":".join(f"{n}b={10 ** (p[f'b{[0,0,1,2,3,4,5,6,7,8,9,9,9,9,9,9,9,9][i-1]}'] / 20):.3f}" for i, n in enumerate(range(1, 19), start=1))},
  "dehummer": {"name": "DeHummer", "cat": "Noise Reduction", "params": {"frequency": [60, 50, 60, 10], "harmonics": [4, 1, 8, 1]}, "ff": lambda p: ",".join(f"bandreject=f={int(p['frequency']) * k}:w=6" for k in range(1, int(p['harmonics']) + 1))},
  "denoise": {"name": "Adaptive Noise Reduction", "cat": "Noise Reduction", "params": {"reduction_db": [12, 1, 40, 1]}, "ff": lambda p: f"afftdn=nr={p['reduction_db']:.1f}:nf=-40"},
  "compressor": {"name": "Compressor", "cat": "Dynamics", "params": {"threshold_db": [-18, -60, 0, 0.5], "ratio": [3, 1, 20, 0.1], "attack_ms": [20, 0.1, 500, 1], "release_ms": [200, 10, 3000, 10], "makeup_db": [0, 0, 24, 0.5]},
    "ff": lambda p: f"acompressor=threshold={10 ** (p['threshold_db'] / 20):.4f}:ratio={p['ratio']:.1f}:attack={p['attack_ms']:.0f}:release={p['release_ms']:.0f}:makeup={10 ** (p['makeup_db'] / 20):.3f}"},
  "gate": {"name": "Gate", "cat": "Dynamics", "params": {"threshold_db": [-40, -80, 0, 0.5], "ratio": [4, 1, 20, 0.5], "attack_ms": [10, 0.1, 500, 1], "release_ms": [150, 10, 3000, 10]}, "ff": lambda p: f"agate=threshold={10 ** (p['threshold_db'] / 20):.5f}:ratio={p['ratio']:.1f}:attack={p['attack_ms']:.0f}:release={p['release_ms']:.0f}"},
  "multiband_comp": {"name": "Multiband Compressor", "cat": "Dynamics", "params": {}, "ff": lambda p: "mcompand"},
  "limiter": {"name": "Hard Limiter", "cat": "Dynamics", "params": {"ceiling_db": [-1, -12, 0, 0.5]}, "ff": lambda p: f"alimiter=limit={10 ** (p['ceiling_db'] / 20):.4f}:level=false"},
  "chorus": {"name": "Chorus", "cat": "Modulation", "params": {"depth": [2, 0.1, 10, 0.1], "speed": [0.4, 0.1, 5, 0.1]}, "ff": lambda p: f"chorus=0.7:0.9:55:0.4:{p['speed']:.2f}:{p['depth']:.2f}"},
  "flanger": {"name": "Flanger", "cat": "Modulation", "params": {"depth": [2, 0, 10, 0.1], "speed": [0.5, 0.1, 10, 0.1]}, "ff": lambda p: f"flanger=depth={p['depth']:.2f}:speed={p['speed']:.2f}"},
  "phaser": {"name": "Phaser", "cat": "Modulation", "params": {"speed": [0.5, 0.1, 2, 0.1], "decay": [0.4, 0, 0.99, 0.01]}, "ff": lambda p: f"aphaser=speed={p['speed']:.2f}:decay={p['decay']:.2f}"},
  "tremolo": {"name": "Tremolo", "cat": "Modulation", "params": {"frequency": [5, 0.1, 20, 0.1], "depth": [0.5, 0, 1, 0.01]}, "ff": lambda p: f"tremolo=f={p['frequency']:.2f}:d={p['depth']:.2f}"},
  "delay": {"name": "Delay", "cat": "Delay & Echo", "params": {"delay_ms": [300, 10, 2000, 10], "feedback": [0.4, 0, 0.9, 0.05], "mix": [0.5, 0, 1, 0.05]}, "ff": lambda p: f"aecho=0.8:{p['mix']:.2f}:{int(p['delay_ms'])}:{p['feedback']:.2f}"},
  "reverb": {"name": "Studio Reverb", "cat": "Reverb", "params": {"size": ["room", None, None, None, "select:room,hall,plate"], "mix": [0.3, 0, 1, 0.05]},
    "ff": lambda p: {"room": f"aecho=0.8:{p['mix']:.2f}:40|60|90:0.3|0.2|0.1", "hall": f"aecho=0.8:{p['mix']:.2f}:120|260|400:0.5|0.35|0.2", "plate": f"aecho=0.8:{p['mix']:.2f}:20|35|55|80:0.4|0.3|0.2|0.1"}[p['size']]},
  "pitch_shift": {"name": "Pitch Shifter", "cat": "Time & Pitch", "params": {"semitones": [0, -12, 12, 1]}, "ff": lambda p: (lambda r: f"asetrate=48000*{r:.5f},aresample=48000,atempo={1/r:.5f}")(2 ** (p['semitones'] / 12))},
  "distortion": {"name": "Distortion", "cat": "Special", "params": {"bits": [8, 1, 16, 1], "mix": [0.5, 0, 1, 0.05]}, "ff": lambda p: f"acrusher=bits={int(p['bits'])}:mix={p['mix']:.2f}:mode=log"},
  "invert_audio": {"name": "Invert", "cat": "Special", "params": {}, "ff": lambda p: "aeval=-val(0)|-val(1)"},
  "swap_channels": {"name": "Swap Channels", "cat": "Stereo Imagery", "params": {}, "ff": lambda p: "channelmap=map=1|0"},
  "fill_left": {"name": "Fill Left with Right", "cat": "Stereo Imagery", "params": {}, "ff": lambda p: "pan=stereo|c0=c1|c1=c1"},
  "fill_right": {"name": "Fill Right with Left", "cat": "Stereo Imagery", "params": {}, "ff": lambda p: "pan=stereo|c0=c0|c1=c0"},
  "stereo_widen": {"name": "Stereo Expander", "cat": "Stereo Imagery", "params": {"delay_ms": [20, 1, 100, 1], "feedback": [0.3, 0, 0.9, 0.05]}, "ff": lambda p: f"stereowiden=delay={p['delay_ms']:.0f}:feedback={p['feedback']:.2f}:crossfeed=0.3:drymix=0.8"},
  "crystalizer": {"name": "Crystalizer (clarity)", "cat": "Special", "params": {"intensity": [2, -10, 10, 0.1]}, "ff": lambda p: f"crystalizer=i={p['intensity']:.1f}"},
  "vocal_enhancer": {"name": "Vocal Enhancer", "cat": "Special", "params": {"type": ["male", None, None, None, "select:male,female,music"]},
    "ff": lambda p: {"male": "highpass=f=80,equalizer=f=200:t=q:w=1:g=-2,equalizer=f=3000:t=q:w=1.2:g=3,acompressor=threshold=0.125:ratio=3:attack=20:release=200:makeup=1.4", "female": "highpass=f=100,equalizer=f=250:t=q:w=1:g=-2,equalizer=f=4000:t=q:w=1.2:g=3,acompressor=threshold=0.125:ratio=3:attack=20:release=200:makeup=1.4", "music": "bass=g=1.5:f=100,treble=g=1.5:f=8000,acompressor=threshold=0.25:ratio=2:attack=30:release=300:makeup=1.2"}[p['type']]},
  "loudness_match": {"name": "Loudness Match", "cat": "Amplitude", "params": {"target_lufs": [-18, -30, -10, 0.5]}, "ff": lambda p: f"loudnorm=I={p['target_lufs']:.1f}:TP=-1.5:LRA=11"},
}

def catalog():
    def spec(d, kind_video):
        out = []
        for key, e in d.items():
            params = {}
            for pn, sp in e["params"].items():
                params[pn] = {"default": sp[0], "min": sp[1], "max": sp[2], "step": sp[3], "kind": (sp[4] if len(sp) > 4 else ("number" if sp[1] is not None else "text"))}
            out.append({"type": key, "name": e["name"], "cat": e["cat"], "params": params, "preview": bool(e.get("css")), "note": e.get("note", "")})
        return out
    return {"video": spec(VIDEO_FX, True), "audio": spec(AUDIO_FX, False)}

def _params(e, fx):
    p = {k: v[0] for k, v in e["params"].items()}; p.update({k: v for k, v in (fx.get("params") or {}).items() if k in p})
    for k, sp in e["params"].items():
        if sp[1] is not None and isinstance(p[k], (int, float)): p[k] = max(sp[1], min(sp[2], p[k]))
    return p

def video_stack_chain(c, fps=30.0, stab_file=None):
    """Filters for a clip's fx_stack, applied at frame size. Effects that need a graph (mirror) are returned as-is (single line, split labels unique per use)."""
    out = []
    for i, fx in enumerate(c.get("fx_stack") or []):
        if fx.get("enabled") is False: continue
        e = VIDEO_FX.get(fx.get("type"))
        if not e or e.get("ff") is None: continue
        p = _params(e, fx); s = e["ff"](p, fps) if e.get("needs_fps") else e["ff"](p)
        if e.get("graph"): s = s.replace("[m0]", f"[m{i}a]").replace("[m1]", f"[m{i}b]").replace("[m2]", f"[m{i}c]")
        out.append(s)
    return out

def audio_stack_chain(c):
    out = []
    for fx in c.get("afx_stack") or []:
        if fx.get("enabled") is False: continue
        e = AUDIO_FX.get(fx.get("type"))
        if not e: continue
        out.append(e["ff"](_params(e, fx)))
    return out

def stabilize_params(c):
    for fx in c.get("fx_stack") or []:
        if fx.get("type") == "stabilize" and fx.get("enabled") is not False: return _params(VIDEO_FX["stabilize"], fx)
    return None

def hsl_secondary_params(c):
    out = []
    for fx in c.get("fx_stack") or []:
        if fx.get("type") == "hsl_secondary" and fx.get("enabled") is not False: out.append(_params(VIDEO_FX["hsl_secondary"], fx))
    return out

def track_matte_params(c):
    for fx in c.get("fx_stack") or []:
        if fx.get("type") == "track_matte" and fx.get("enabled") is not False: return _params(VIDEO_FX["track_matte"], fx)
    return None

def drop_shadow_params(c):
    for fx in c.get("fx_stack") or []:
        if fx.get("type") == "drop_shadow" and fx.get("enabled") is not False: return _params(VIDEO_FX["drop_shadow"], fx)
    return None

/* Filmocity WebGL2 color preview. Color-wheel weights follow the export filter;
   broader color/effect and browser/native parity still require acceptance.
   process(source, clip, width, height) → canvas (or null → fallback). */
(() => {
const VS = `#version 300 es
in vec2 p; out vec2 uv; void main(){ uv = vec2(p.x, 1.0 - p.y) * 0.5 + 0.5; uv.x = p.x*0.5+0.5; uv.y = 0.5 - p.y*0.5; gl_Position = vec4(p, 0.0, 1.0); }`;
const FS = `#version 300 es
precision highp float; precision highp sampler3D;
in vec2 uv; out vec4 o;
uniform sampler2D tex; uniform sampler2D curve;     // curve: 256x1 RGBA — per-channel 1D LUT (basic corrections + curves baked on the CPU)
uniform sampler3D lut3d; uniform float hasLut; uniform float lutSize;
uniform sampler3D lutIn; uniform float hasIn; uniform float inSize;
uniform float saturation, vibrance, temperature, tint;
uniform float wheelOn; uniform vec3 wS, wM, wH;                             // colour wheels: shadows / midtones / highlights RGB offsets
uniform float keyOn; uniform vec3 keyColor; uniform float keySim, keyBlend;   // chroma key
uniform float lumaOn; uniform float lumaThr, lumaTol, lumaSoft;              // luma key
uniform float hslOn; uniform vec3 hslKey; uniform float hslRange, hslSoft, hslHue, hslSat, hslLight, hslInvert;
uniform float invertOn, bwOn;
uniform float mosaicPx, posterLevels, threshOn, threshLevel, flipH, flipV, mirrorMode, tintOn, tintAmt, rgbShift;
uniform vec3 tintBlack, tintWhite;
uniform vec2 texSize;
vec3 rgb2hsv(vec3 c){ vec4 K=vec4(0.,-1./3.,2./3.,-1.); vec4 p=mix(vec4(c.bg,K.wz),vec4(c.gb,K.xy),step(c.b,c.g)); vec4 q=mix(vec4(p.xyw,c.r),vec4(c.r,p.yzx),step(p.x,c.r)); float d=q.x-min(q.w,q.y); float e=1e-10; return vec3(abs(q.z+(q.w-q.y)/(6.*d+e)),d/(q.x+e),q.x); }
vec3 hsv2rgb(vec3 c){ vec4 K=vec4(1.,2./3.,1./3.,3.); vec3 p=abs(fract(c.xxx+K.xyz)*6.-K.www); return c.z*mix(K.xxx,clamp(p-K.xxx,0.,1.),c.y); }
float lum(vec3 c){ return dot(c, vec3(0.2126,0.7152,0.0722)); }
// FFmpeg chromakey distance: in YUV, normalised difference of chroma
float chromaDist(vec3 c, vec3 k){ float cu=-0.1471*c.r-0.2889*c.g+0.436*c.b, cv=0.615*c.r-0.5149*c.g-0.1*c.b; float ku=-0.1471*k.r-0.2889*k.g+0.436*k.b, kv=0.615*k.r-0.5149*k.g-0.1*k.b; return length(vec2(cu-ku,cv-kv)) / 0.5; }
// Export colorbalance uses max(RGB) + min(RGB), not luminance. Its two
// linear transition ramps define the three bands, with an overall gain of 0.7.
// See https://ffmpeg.org/doxygen/8.1/vf__colorbalance_8c_source.html
vec3 wheelBalance(vec3 rgb){
  rgb = clamp(rgb, 0.0, 1.0);
  float level = max(rgb.r, max(rgb.g, rgb.b)) + min(rgb.r, min(rgb.g, rgb.b));
  vec2 ramps = clamp((vec2(level) - vec2(0.333, 0.667)) * 4.0 + 0.5, 0.0, 1.0);
  vec3 change = wS * (1.0 - ramps.x) + wM * ramps.x * (1.0 - ramps.y) + wH * ramps.y;
  return clamp(rgb + change * 0.7, 0.0, 1.0);
}
void main(){
  vec2 p = uv;
  if (flipH > 0.5) p.x = 1.0 - p.x;
  if (flipV > 0.5) p.y = 1.0 - p.y;
  if (mirrorMode > 0.5 && p.x > 0.5) p.x = 1.0 - p.x;
  if (mosaicPx > 0.5) p = (floor(p * texSize / mosaicPx) + 0.5) * mosaicPx / texSize;
  vec4 s = texture(tex, p); vec3 c = s.rgb; float a = s.a;
  if (rgbShift > 0.5) { c.r = texture(tex, p + vec2(rgbShift / texSize.x, 0.0)).r; c.b = texture(tex, p - vec2(rgbShift / texSize.x, 0.0)).b; }
  if (hasIn > 0.5) { vec3 cc = clamp(c, 0.0, 1.0); float n = inSize; c = texture(lutIn, cc * (n - 1.0) / n + 0.5 / n).rgb; }   // camera log → Rec.709 first
  // keys first (they look at the source colour)
  if (keyOn > 0.5) { float d = chromaDist(c, keyColor); float ka = d < keySim ? 0.0 : (d < keySim + keyBlend ? (d - keySim) / max(keyBlend, 1e-4) : 1.0); a *= ka; }
  if (lumaOn > 0.5) { float l = lum(c); float lo = lumaThr - lumaTol * 0.5, hi = lumaThr + lumaTol * 0.5; float ka = l < lo ? 0.0 : (l < lo + lumaSoft ? (l - lo) / max(lumaSoft, 1e-4) : 1.0); a *= ka; }
  // basic corrections + curves via the baked 1D LUT
  // Sample texel centers: an identity table must preserve all 256 byte values.
  vec3 curveUV = (clamp(c, 0.0, 1.0) * 255.0 + 0.5) / 256.0;
  c = vec3(texture(curve, vec2(curveUV.r, 0.5)).r, texture(curve, vec2(curveUV.g, 0.5)).g, texture(curve, vec2(curveUV.b, 0.5)).b);
  // temperature / tint (matches colorbalance approximations used in the render)
  c += vec3(temperature * 0.08, tint * 0.05, -temperature * 0.08);
  // saturation & vibrance
  float lc = lum(c); c = mix(vec3(lc), c, saturation);
  if (abs(vibrance) > 0.001) { float sat = max(c.r, max(c.g, c.b)) - min(c.r, min(c.g, c.b)); c = mix(vec3(lc), c, 1.0 + vibrance * (1.0 - sat)); }
  // Export applies wheels after saturation/vibrance and before the output LUT.
  if (wheelOn > 0.5) c = wheelBalance(c);
  // HSL secondary: qualify by chroma distance to the key colour, then shift hue / sat / light only there
  if (hslOn > 0.5) { float d = chromaDist(c, hslKey); float q = d < hslRange ? 1.0 : (d < hslRange + hslSoft ? 1.0 - (d - hslRange) / max(hslSoft, 1e-4) : 0.0); if (hslInvert > 0.5) q = 1.0 - q; vec3 h = rgb2hsv(c); h.x = fract(h.x + hslHue / 360.0); h.y *= hslSat; vec3 corr = hsv2rgb(h) + vec3(hslLight * 0.5); c = mix(c, corr, q); }
  if (hasLut > 0.5) { vec3 cc = clamp(c, 0.0, 1.0); float n = lutSize; c = texture(lut3d, cc * (n - 1.0) / n + 0.5 / n).rgb; }
  if (posterLevels > 1.5) c = floor(c * posterLevels) / (posterLevels - 1.0);
  if (threshOn > 0.5) c = vec3(step(threshLevel, lum(c)));
  if (tintOn > 0.5) { float l = lum(c); c = mix(c, mix(tintBlack, tintWhite, l), tintAmt); }
  if (bwOn > 0.5) c = vec3(lum(c));
  if (invertOn > 0.5) c = 1.0 - c;
  o = vec4(clamp(c, 0.0, 1.0) * a, a);   // premultiplied for drawImage compositing
}`;
let gl = null, prog = null, cv = null, uni = {}, curveTex = null, lutTex = null, lutInTex = null, curveKey = "", lutKey = "", lutInKey = "", lutCache = {};
function init() { if (gl !== null) return !!gl; try { cv = document.createElement("canvas"); gl = cv.getContext("webgl2", { premultipliedAlpha: true, preserveDrawingBuffer: true }); if (!gl) return false; const mk = (t, src) => { const s = gl.createShader(t); gl.shaderSource(s, src); gl.compileShader(s); if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s)); return s; }; prog = gl.createProgram(); gl.attachShader(prog, mk(gl.VERTEX_SHADER, VS)); gl.attachShader(prog, mk(gl.FRAGMENT_SHADER, FS)); gl.linkProgram(prog); if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog)); gl.useProgram(prog);
    const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW); const loc = gl.getAttribLocation(prog, "p"); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    for (const n of ["tex", "curve", "lut3d", "hasLut", "lutSize", "lutIn", "hasIn", "inSize", "saturation", "vibrance", "temperature", "tint", "wheelOn", "wS", "wM", "wH", "keyOn", "keyColor", "keySim", "keyBlend", "lumaOn", "lumaThr", "lumaTol", "lumaSoft", "hslOn", "hslKey", "hslRange", "hslSoft", "hslHue", "hslSat", "hslLight", "hslInvert", "invertOn", "bwOn", "mosaicPx", "posterLevels", "threshOn", "threshLevel", "flipH", "flipV", "mirrorMode", "tintOn", "tintAmt", "rgbShift", "tintBlack", "tintWhite", "texSize"]) uni[n] = gl.getUniformLocation(prog, n);
    const srcTex = gl.createTexture(); gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, srcTex); for (const [k, v] of [[gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE], [gl.TEXTURE_MIN_FILTER, gl.LINEAR], [gl.TEXTURE_MAG_FILTER, gl.LINEAR]]) gl.texParameteri(gl.TEXTURE_2D, k, v);
    curveTex = gl.createTexture(); gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, curveTex); for (const [k, v] of [[gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE], [gl.TEXTURE_MIN_FILTER, gl.LINEAR], [gl.TEXTURE_MAG_FILTER, gl.LINEAR]]) gl.texParameteri(gl.TEXTURE_2D, k, v);
    lutTex = gl.createTexture(); gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_3D, lutTex); for (const [k, v] of [[gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_R, gl.CLAMP_TO_EDGE], [gl.TEXTURE_MIN_FILTER, gl.LINEAR], [gl.TEXTURE_MAG_FILTER, gl.LINEAR]]) gl.texParameteri(gl.TEXTURE_3D, k, v);
    lutInTex = gl.createTexture(); gl.activeTexture(gl.TEXTURE3); gl.bindTexture(gl.TEXTURE_3D, lutInTex); for (const [k, v] of [[gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_R, gl.CLAMP_TO_EDGE], [gl.TEXTURE_MIN_FILTER, gl.LINEAR], [gl.TEXTURE_MAG_FILTER, gl.LINEAR]]) gl.texParameteri(gl.TEXTURE_3D, k, v);
    gl.uniform1i(uni.tex, 0); gl.uniform1i(uni.curve, 1); gl.uniform1i(uni.lut3d, 2); gl.uniform1i(uni.lutIn, 3); return true; } catch (e) { console.warn("GPU pipeline unavailable:", e.message); gl = false; return false; } }
// --- CPU: bake basic corrections + curves into a 256-entry per-channel LUT (same maths as the FFmpeg chain: eq exposure/contrast, curves highlights/shadows/whites/blacks, curves master/r/g/b)
function smooth(pts, x) { const s = [...pts].sort((a, b) => a[0] - b[0]); if (x <= s[0][0]) return s[0][1]; if (x >= s[s.length - 1][0]) return s[s.length - 1][1]; let i = 0; while (s[i + 1][0] < x) i++; const [x0, y0] = s[i], [x1, y1] = s[i + 1]; const p = (x - x0) / Math.max(x1 - x0, 1e-6); return y0 + (y1 - y0) * (p * p * (3 - 2 * p)); }
function bakeCurve(col) { const N = 256, out = new Uint8Array(N * 4); const ex = Math.pow(2, col.exposure || 0), ct = 1 + (col.contrast || 0); const hi = col.highlights || 0, sh = col.shadows || 0, wh = col.whites || 0, bl = col.blacks || 0; const tone = (Math.abs(hi) > 1e-3 || Math.abs(sh) > 1e-3 || Math.abs(wh) > 1e-3 || Math.abs(bl) > 1e-3) ? (() => { const pts = []; if (bl > 1e-3) pts.push([0, 0], [Math.min(0.2, bl * 0.1), 0.0005]); else pts.push([0, Math.min(0.2, -bl * 0.1)]); pts.push([0.25, Math.min(Math.max(0.25 + sh * 0.12, pts[pts.length - 1][0] + 0.02), 0.45)]); pts.push([0.75, Math.min(Math.max(0.75 + hi * 0.12, 0.55), 0.95)]); if (wh > 1e-3) pts.push([Math.max(0.8, 1 - wh * 0.1), 0.9995], [1, 1]); else pts.push([1, Math.max(0.8, 1 + wh * 0.1)]); return pts; })() : null;
  for (let i = 0; i < N; i++) { let v = i / 255; v = Math.min(1, Math.max(0, (v * ex - 0.5) * ct + 0.5)); if (tone) v = smooth(tone, v); if (col.curves && col.curves.length >= 2) v = smooth(col.curves, v); const ch = [v, v, v]; for (const [k, key] of [[0, "curves_r"], [1, "curves_g"], [2, "curves_b"]]) if (col[key] && col[key].length >= 2) ch[k] = smooth(col[key], ch[k]); out[i * 4] = Math.round(ch[0] * 255); out[i * 4 + 1] = Math.round(ch[1] * 255); out[i * 4 + 2] = Math.round(ch[2] * 255); out[i * 4 + 3] = 255; } return out; }
function parseCube(text) { let size = 0; const data = []; for (const line of text.split(/\r?\n/)) { const t = line.trim(); if (!t || t.startsWith("#") || t.startsWith("TITLE") || t.startsWith("DOMAIN")) continue; if (t.startsWith("LUT_3D_SIZE")) { size = parseInt(t.split(/\s+/)[1]); continue; } if (t.startsWith("LUT_1D_SIZE")) return null; const p = t.split(/\s+/).map(Number); if (p.length >= 3 && p.every(x => !isNaN(x))) data.push(p[0], p[1], p[2]); } if (!size || data.length < size * size * size * 3) return null; return { size, data: new Float32Array(data) }; }
function loadLut(path) { if (lutCache[path] !== undefined) return lutCache[path]; lutCache[path] = null; fetch(path.startsWith("/assets/") ? path : "/api/luts/file?path=" + encodeURIComponent(path)).then(r => r.ok ? r.text() : null).then(t => { const lut = t && parseCube(t); lutCache[path] = lut || false; if (lut && window.CR) CR.renderProgram(); }).catch(() => { lutCache[path] = false; }); return null; }
function hex(c, def) { c = String(c || def || "#000000"); if (c.startsWith("0x")) c = "#" + c.slice(2); const m = /^#?([0-9a-f]{6})/i.exec(c); if (!m) return [0, 0, 0]; const n = parseInt(m[1], 16); return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255]; }
const GPU_FX = ["hsl_secondary", "luma_key", "ultra_key", "color_key", "black_white", "invert", "levels", "vibrance_fx", "mosaic", "posterize", "threshold", "flip_h", "flip_v", "mirror", "tint", "colorize", "rgb_shift"];
function needs(c) { const col = c.color || {}, fx = c.effects || {}, st = c.fx_stack || []; if (c._inputTransform) return true; if (st.some(f => f.enabled !== false && GPU_FX.includes(f.type))) return true; return !!(col.lut || col.curves || col.curves_r || col.curves_g || col.curves_b || col.wheels || col.vibrance || (fx.chromakey || {}).enabled || st.some(f => f.enabled !== false && ["hsl_secondary", "luma_key", "ultra_key", "color_key", "black_white", "invert", "levels", "vibrance_fx"].includes(f.type)) || Math.abs(col.highlights || 0) > 1e-3 || Math.abs(col.shadows || 0) > 1e-3 || Math.abs(col.whites || 0) > 1e-3 || Math.abs(col.blacks || 0) > 1e-3 || Math.abs(col.exposure || 0) > 1e-3 || Math.abs(col.contrast || 0) > 1e-3 || Math.abs((col.saturation || 0)) > 1e-3 || Math.abs(col.temperature || 0) > 1e-3 || Math.abs(col.tint || 0) > 1e-3); }
function uploadLut(unit, tex, lut) { gl.activeTexture(unit); gl.bindTexture(gl.TEXTURE_3D, tex); const n = lut.size, u8 = new Uint8Array(n * n * n * 4); for (let i = 0, j = 0; i < lut.data.length; i += 3, j += 4) { u8[j] = Math.round(Math.max(0, Math.min(1, lut.data[i])) * 255); u8[j + 1] = Math.round(Math.max(0, Math.min(1, lut.data[i + 1])) * 255); u8[j + 2] = Math.round(Math.max(0, Math.min(1, lut.data[i + 2])) * 255); u8[j + 3] = 255; } gl.texImage3D(gl.TEXTURE_3D, 0, gl.RGBA8, n, n, n, 0, gl.RGBA, gl.UNSIGNED_BYTE, u8); }
function process(src, c, w, h) { if (!init()) return null; const col = c.color || {}, fx = c.effects || {}, st = (c.fx_stack || []).filter(f => f.enabled !== false); const sw = src.videoWidth || src.width || w, sh = src.videoHeight || src.height || h; if (!sw || !sh) return null; if (cv.width !== sw || cv.height !== sh) { cv.width = sw; cv.height = sh; gl.viewport(0, 0, sw, sh); }
  gl.activeTexture(gl.TEXTURE0); try { gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, src); } catch (e) { return null; }
  const ck = JSON.stringify([col.exposure, col.contrast, col.highlights, col.shadows, col.whites, col.blacks, col.curves, col.curves_r, col.curves_g, col.curves_b]); if (ck !== curveKey) { curveKey = ck; gl.activeTexture(gl.TEXTURE1); gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 256, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, bakeCurve(col)); }
  let hasIn = 0, inSize = 1; if (c._inputTransform) { const p = c._inputTransformPath || "/assets/luts/input/" + c._inputTransform + ".cube"; const lut = lutCache[p] || loadLut(p); if (lut) { if (lutInKey !== p) { lutInKey = p; uploadLut(gl.TEXTURE3, lutInTex, lut); } hasIn = 1; inSize = lut.size; } } gl.uniform1f(uni.hasIn, hasIn); gl.uniform1f(uni.inSize, inSize);
  let hasLut = 0, lutSize = 1; if (col.lut) { const lut = lutCache[col.lut] || loadLut(col.lut); if (lut) { if (lutKey !== col.lut) { lutKey = col.lut; gl.activeTexture(gl.TEXTURE2); const n = lut.size, u8 = new Uint8Array(n * n * n * 4); for (let i = 0, j = 0; i < lut.data.length; i += 3, j += 4) { u8[j] = Math.round(Math.max(0, Math.min(1, lut.data[i])) * 255); u8[j + 1] = Math.round(Math.max(0, Math.min(1, lut.data[i + 1])) * 255); u8[j + 2] = Math.round(Math.max(0, Math.min(1, lut.data[i + 2])) * 255); u8[j + 3] = 255; } gl.texImage3D(gl.TEXTURE_3D, 0, gl.RGBA8, n, n, n, 0, gl.RGBA, gl.UNSIGNED_BYTE, u8); } hasLut = 1; lutSize = lut.size; } }
  gl.uniform1f(uni.hasLut, hasLut); gl.uniform1f(uni.lutSize, lutSize); gl.uniform1f(uni.saturation, 1 + (col.saturation || 0)); gl.uniform1f(uni.vibrance, (col.vibrance || 0) + (st.find(f => f.type === "vibrance_fx") || { params: {} }).params.amount * 0 || (col.vibrance || 0)); gl.uniform1f(uni.temperature, col.temperature || 0); gl.uniform1f(uni.tint, col.tint || 0);
  const wh = col.wheels || {}, wheelValues = ["shadows", "midtones", "highlights"].map(k => {
    const value = wh[k] || {}; return [value.r || 0, value.g || 0, value.b || 0].map(Number);
  });
  // Match color_chain's activation threshold and serialized parameter precision.
  gl.uniform1f(uni.wheelOn, wheelValues.some(channels => channels.some(value => Math.abs(value) > 1e-3)) ? 1 : 0);
  ["wS", "wM", "wH"].forEach((name, i) => gl.uniform3fv(uni[name], wheelValues[i].map(value => +value.toFixed(3))));
  const ck2 = (fx.chromakey || {}).enabled ? fx.chromakey : (st.find(f => f.type === "ultra_key" || f.type === "color_key") || {}).params; const keyOn = ck2 && (ck2.enabled !== false) && (fx.chromakey && fx.chromakey.enabled || st.some(f => f.type === "ultra_key" || f.type === "color_key")); gl.uniform1f(uni.keyOn, keyOn ? 1 : 0); gl.uniform3fv(uni.keyColor, hex(ck2 && ck2.color, "#00ff00")); gl.uniform1f(uni.keySim, ck2 && ck2.similarity != null ? ck2.similarity : 0.2); gl.uniform1f(uni.keyBlend, ck2 && ck2.blend != null ? ck2.blend : 0.05);
  const lk = st.find(f => f.type === "luma_key"); gl.uniform1f(uni.lumaOn, lk ? 1 : 0); gl.uniform1f(uni.lumaThr, lk ? (lk.params.threshold ?? 0.1) : 0); gl.uniform1f(uni.lumaTol, lk ? (lk.params.tolerance ?? 0.1) : 0); gl.uniform1f(uni.lumaSoft, lk ? (lk.params.softness ?? 0.05) : 0);
  const hs = st.find(f => f.type === "hsl_secondary"); const hp = hs ? hs.params || {} : {}; gl.uniform1f(uni.hslOn, hs ? 1 : 0); gl.uniform3fv(uni.hslKey, hex(hp.key_color, "#3aa0ff")); gl.uniform1f(uni.hslRange, hp.range ?? 0.25); gl.uniform1f(uni.hslSoft, hp.softness ?? 0.1); gl.uniform1f(uni.hslHue, hp.hue_shift || 0); gl.uniform1f(uni.hslSat, hp.saturation == null ? 1 : hp.saturation); gl.uniform1f(uni.hslLight, hp.lightness || 0); gl.uniform1f(uni.hslInvert, hp.invert ? 1 : 0);
  gl.uniform1f(uni.invertOn, st.some(f => f.type === "invert") ? 1 : 0); gl.uniform1f(uni.bwOn, st.some(f => f.type === "black_white") ? 1 : 0);
  const P = t => (st.find(f => f.type === t) || {}).params; gl.uniform2f(uni.texSize, sw, sh);
  gl.uniform1f(uni.mosaicPx, P("mosaic") ? Math.max(2, P("mosaic").block || 16) : 0); gl.uniform1f(uni.posterLevels, P("posterize") ? (P("posterize").levels || 6) : 0); gl.uniform1f(uni.threshOn, P("threshold") ? 1 : 0); gl.uniform1f(uni.threshLevel, P("threshold") ? ((P("threshold").level ?? 0.5) > 1 ? P("threshold").level / 255 : (P("threshold").level ?? 0.5)) : 0.5);
  gl.uniform1f(uni.flipH, P("flip_h") ? 1 : 0); gl.uniform1f(uni.flipV, P("flip_v") ? 1 : 0); gl.uniform1f(uni.mirrorMode, P("mirror") ? 1 : 0); gl.uniform1f(uni.rgbShift, P("rgb_shift") ? (P("rgb_shift").rh || 8) : 0);
  const tp = P("tint"), cz = P("colorize"); gl.uniform1f(uni.tintOn, (tp || cz) ? 1 : 0); gl.uniform1f(uni.tintAmt, tp ? (tp.amount == null ? 1 : tp.amount) : cz ? (cz.mix == null ? 1 : cz.mix) : 0);
  if (cz) { const h = ((cz.hue || 0) % 360) / 60, sat = cz.saturation == null ? 0.5 : cz.saturation, lt = cz.lightness == null ? 0.5 : cz.lightness; const f = (n) => { const k = (n + h) % 6; return lt - sat * Math.min(lt, 1 - lt) * Math.max(-1, Math.min(k - 3, 9 - k, 1)); }; gl.uniform3f(uni.tintBlack, 0, 0, 0); gl.uniform3f(uni.tintWhite, f(5), f(3), f(1)); } else { gl.uniform3fv(uni.tintBlack, hex(tp && tp.black_to, "#000000")); gl.uniform3fv(uni.tintWhite, hex(tp && tp.white_to, "#ffffff")); }
  gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT); gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4); return cv; }
window.CR_GPU = { process, needs, available: () => init(), parseCube };
})();

/* Capture production WebGL inputs for contract tests and the real GLES pixel probe.
 * This adapter does not compile shaders or render; tests/helpers/gles.py does that.
 */
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
function capture(clip, width = 256, height = 1, sourcePath) {
  const shaders = {}, uniforms = {}, images = {}, parameters = {}, calls = [];
  let unit = 0, next = 1, vertices;
  const constants = { VERTEX_SHADER: 35633, FRAGMENT_SHADER: 35632, COMPILE_STATUS: 35713, LINK_STATUS: 35714,
    TEXTURE0: 33984, TEXTURE1: 33985, TEXTURE2: 33986, TEXTURE3: 33987, TEXTURE_2D: 3553, TEXTURE_3D: 32879,
    TEXTURE_WRAP_S: 10242, TEXTURE_WRAP_T: 10243, TEXTURE_WRAP_R: 32882, TEXTURE_MIN_FILTER: 10241, TEXTURE_MAG_FILTER: 10240,
    CLAMP_TO_EDGE: 33071, LINEAR: 9729, RGBA: 6408, RGBA8: 32856, UNSIGNED_BYTE: 5121,
    ARRAY_BUFFER: 34962, STATIC_DRAW: 35044, FLOAT: 5126, COLOR_BUFFER_BIT: 16384, TRIANGLE_STRIP: 5 };
  const gl = { ...constants,
    createShader: type => ({ type }), shaderSource: (shader, source) => { shaders[shader.type] = source; },
    compileShader() {}, getShaderParameter: () => true, createProgram: () => ({}), attachShader() {}, linkProgram() {}, getProgramParameter: () => true, useProgram() {},
    createBuffer: () => ({}), bindBuffer() {}, bufferData: (_, data) => { vertices = [...data]; }, getAttribLocation: () => 0, enableVertexAttribArray() {}, vertexAttribPointer() {},
    getUniformLocation: (_, name) => name,
    uniform1i: (name, value) => { uniforms[name] = { type: 'i', value: [value] }; },
    uniform1f: (name, value) => { uniforms[name] = { type: 'f', value: [value] }; },
    uniform2f: (name, ...value) => { uniforms[name] = { type: 'f', value }; },
    uniform3f: (name, ...value) => { uniforms[name] = { type: 'f', value }; },
    uniform3fv: (name, value) => { uniforms[name] = { type: 'f', value: [...value] }; },
    createTexture: () => next++, activeTexture: value => { unit = value - constants.TEXTURE0; }, bindTexture() {},
    texParameteri: (_, key, value) => { (parameters[unit] ||= {})[key] = value; },
    texImage2D: (...args) => {
      calls.push(['texImage2D', unit]);
      if (args.length === 9) images[unit] = { width: args[3], height: args[4], data: [...args[8]] };
    },
    texImage3D: (...args) => { images[unit] = { width: args[3], height: args[4], depth: args[5], data: [...args[9]] }; },
    viewport() {}, clearColor() {}, clear() {}, drawArrays() { calls.push(['draw']); },
  };
  const canvas = { width: 0, height: 0, getContext: () => gl };
  const window = {}, document = { createElement: () => canvas };
  vm.runInNewContext(fs.readFileSync(sourcePath || path.join(__dirname, '../../frontend/gpu.js'), 'utf8'), { window, document, console });
  const states = (Array.isArray(clip) ? clip : [clip]).map(item => {
    const before = JSON.stringify(item);
    if (window.CR_GPU.process({ width, height }, item, width, height) !== canvas) throw new Error('GPU process did not return its canvas');
    if (JSON.stringify(item) !== before) throw new Error('GPU process mutated its clip');
    return JSON.parse(JSON.stringify({ vertex: shaders[constants.VERTEX_SHADER], fragment: shaders[constants.FRAGMENT_SHADER], uniforms, images, parameters, vertices, calls }));
  });
  return Array.isArray(clip) ? states : states[0];
}
module.exports = { capture };
if (require.main === module) {
  const request = JSON.parse(fs.readFileSync(0, 'utf8'));
  process.stdout.write(JSON.stringify(capture(request.clip, request.width, request.height, request.sourcePath)));
}

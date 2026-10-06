"""Offscreen GLES 3 execution for captured Filmocity shaders (Linux EGL/Mesa).

Uses system libraries through ctypes; no browser, network, window or user data.
This is real shader/texture execution, not WebGL/browser conformance evidence.
"""
import ctypes as C


class OffscreenGLES:
    def __init__(self, width=512, height=64):
        self.width, self.height = width, height
        self.egl, self.gl = C.CDLL('libEGL.so.1'), C.CDLL('libGLESv2.so.2')
        self.display = self.surface = self.context = None
        self.program = None
        self.signature = None
        self.textures = (C.c_uint * 4)()
        p, i, u, f = C.c_void_p, C.c_int, C.c_uint, C.c_float
        self._bind(self.egl, {
            'eglGetPlatformDisplay': (p, u, p, C.POINTER(i)),
            'eglInitialize': (u, p, C.POINTER(i), C.POINTER(i)),
            'eglBindAPI': (u, u),
            'eglChooseConfig': (u, p, C.POINTER(i), C.POINTER(p), i, C.POINTER(i)),
            'eglCreatePbufferSurface': (p, p, p, C.POINTER(i)),
            'eglCreateContext': (p, p, p, p, C.POINTER(i)),
            'eglMakeCurrent': (u, p, p, p, p),
            'eglDestroySurface': (u, p, p), 'eglDestroyContext': (u, p, p), 'eglTerminate': (u, p),
            'eglGetError': (u,),
        })
        self._bind(self.gl, {
            'glGetString': (C.c_char_p, u), 'glGetError': (u,),
            'glCreateShader': (u, u), 'glShaderSource': (None, u, i, C.POINTER(C.c_char_p), p),
            'glCompileShader': (None, u), 'glGetShaderiv': (None, u, u, C.POINTER(i)),
            'glGetShaderInfoLog': (None, u, i, p, p), 'glDeleteShader': (None, u),
            'glCreateProgram': (u,), 'glAttachShader': (None, u, u), 'glLinkProgram': (None, u),
            'glGetProgramiv': (None, u, u, C.POINTER(i)), 'glGetProgramInfoLog': (None, u, i, p, p),
            'glUseProgram': (None, u), 'glDeleteProgram': (None, u),
            'glGenBuffers': (None, i, C.POINTER(u)), 'glBindBuffer': (None, u, u),
            'glBufferData': (None, u, C.c_ssize_t, p, u), 'glGetAttribLocation': (i, u, C.c_char_p),
            'glEnableVertexAttribArray': (None, u), 'glVertexAttribPointer': (None, u, i, u, C.c_ubyte, i, p),
            'glGenTextures': (None, i, C.POINTER(u)), 'glActiveTexture': (None, u),
            'glBindTexture': (None, u, u), 'glTexParameteri': (None, u, u, i),
            'glTexImage2D': (None, u, i, i, i, i, i, u, u, p),
            'glTexImage3D': (None, u, i, i, i, i, i, i, u, u, p),
            'glGetUniformLocation': (i, u, C.c_char_p), 'glUniform1i': (None, i, i),
            'glUniform1f': (None, i, f), 'glUniform2f': (None, i, f, f), 'glUniform3f': (None, i, f, f, f),
            'glViewport': (None, i, i, i, i), 'glClearColor': (None, f, f, f, f),
            'glClear': (None, u), 'glDrawArrays': (None, u, i, i), 'glFinish': (None,),
            'glReadPixels': (None, i, i, i, i, u, u, p),
        })
        try:
            # EGL_MESA_platform_surfaceless; EGL_OPENGL_ES_API; ES3, RGBA8 pbuffer.
            self.display = self.egl.eglGetPlatformDisplay(0x31DD, None, None)
            major, minor = i(), i()
            self._egl_check(self.egl.eglInitialize(self.display, C.byref(major), C.byref(minor)))
            self._egl_check(self.egl.eglBindAPI(0x30A0))
            attrs = (i * 13)(0x3033, 1, 0x3040, 0x40, 0x3024, 8, 0x3023, 8, 0x3022, 8, 0x3021, 8, 0x3038)
            config, count = p(), i()
            self._egl_check(self.egl.eglChooseConfig(self.display, attrs, C.byref(config), 1, C.byref(count)))
            if count.value != 1: raise RuntimeError('No EGL ES3 RGBA8 pbuffer configuration')
            self.surface = self.egl.eglCreatePbufferSurface(self.display, config, (i * 5)(0x3057, width, 0x3056, height, 0x3038))
            self.context = self.egl.eglCreateContext(self.display, config, None, (i * 3)(0x3098, 3, 0x3038))
            self._egl_check(self.surface and self.context)
            self._egl_check(self.egl.eglMakeCurrent(self.display, self.surface, self.surface, self.context))
            self.info = {key: self.gl.glGetString(value).decode() for key, value in [('version', 0x1F02), ('renderer', 0x1F01), ('vendor', 0x1F00)]}
            self.gl.glGenTextures(4, self.textures)
            self.buffer = u()
            self.gl.glGenBuffers(1, C.byref(self.buffer))
        except Exception:
            self.close()
            raise

    @staticmethod
    def _bind(lib, definitions):
        for name, (result, *args) in definitions.items():
            method = getattr(lib, name)
            method.restype, method.argtypes = result, args

    def _egl_check(self, success):
        if not success: raise RuntimeError(f'EGL failed: {self.egl.eglGetError():#x}')

    def _compile(self, state):
        signature = state['vertex'], state['fragment']
        if signature == self.signature: return
        if self.program: self.gl.glDeleteProgram(self.program)
        self.program = self.gl.glCreateProgram()
        for kind, source in [(0x8B31, state['vertex']), (0x8B30, state['fragment'])]:
            shader = self.gl.glCreateShader(kind)
            encoded = C.c_char_p(source.encode())
            self.gl.glShaderSource(shader, 1, C.byref(encoded), None)
            self.gl.glCompileShader(shader)
            ok = C.c_int()
            self.gl.glGetShaderiv(shader, 0x8B81, C.byref(ok))
            if not ok.value:
                log = C.create_string_buffer(16384)
                self.gl.glGetShaderInfoLog(shader, len(log), None, log)
                self.gl.glDeleteShader(shader)
                raise AssertionError(log.value.decode())
            self.gl.glAttachShader(self.program, shader)
            self.gl.glDeleteShader(shader)
        self.gl.glLinkProgram(self.program)
        ok = C.c_int()
        self.gl.glGetProgramiv(self.program, 0x8B82, C.byref(ok))
        if not ok.value:
            log = C.create_string_buffer(16384)
            self.gl.glGetProgramInfoLog(self.program, len(log), None, log)
            raise AssertionError(log.value.decode())
        self.gl.glUseProgram(self.program)
        self.signature = signature

    def render(self, state, pixels, width, height):
        if not (0 < width <= self.width and 0 < height <= self.height): raise ValueError('Fixture exceeds pbuffer')
        if len(pixels) != width * height * 4: raise ValueError('Expected RGBA8 pixels')
        self._compile(state)
        data = (C.c_float * len(state['vertices']))(*state['vertices'])
        self.gl.glBindBuffer(0x8892, self.buffer)
        self.gl.glBufferData(0x8892, C.sizeof(data), data, 0x88E4)
        location = self.gl.glGetAttribLocation(self.program, b'p')
        self.gl.glEnableVertexAttribArray(location)
        self.gl.glVertexAttribPointer(location, 2, 0x1406, False, 0, None)
        for unit in range(4):
            target = 0x0DE1 if unit < 2 else 0x806F
            self.gl.glActiveTexture(0x84C0 + unit)
            self.gl.glBindTexture(target, self.textures[unit])
            for key, value in state['parameters'][str(unit)].items(): self.gl.glTexParameteri(target, int(key), value)
            image = {'width': width, 'height': height, 'data': pixels} if unit == 0 else state['images'].get(str(unit))
            if image:
                array = (C.c_ubyte * len(image['data']))(*image['data'])
                if unit < 2: self.gl.glTexImage2D(target, 0, 0x1908, image['width'], image['height'], 0, 0x1908, 0x1401, array)
                else: self.gl.glTexImage3D(target, 0, 0x8058, image['width'], image['height'], image['depth'], 0, 0x1908, 0x1401, array)
        for name, uniform in state['uniforms'].items():
            location = self.gl.glGetUniformLocation(self.program, name.encode())
            value = uniform['value']
            getattr(self.gl, f"glUniform{len(value)}{uniform['type']}")(location, *value)
        self.gl.glViewport(0, 0, width, height)
        self.gl.glClearColor(0, 0, 0, 0)
        self.gl.glClear(0x4000)
        self.gl.glDrawArrays(5, 0, 4)
        self.gl.glFinish()
        output = (C.c_ubyte * len(pixels))()
        self.gl.glReadPixels(0, 0, width, height, 0x1908, 0x1401, output)
        error = self.gl.glGetError()
        if error: raise AssertionError(f'GLES error: {error:#x}')
        rows = bytes(output)
        stride = width * 4
        return b''.join(rows[y * stride:(y + 1) * stride] for y in reversed(range(height)))

    def close(self):
        if self.display:
            self.egl.eglMakeCurrent(self.display, None, None, None)
            if self.context: self.egl.eglDestroyContext(self.display, self.context)
            if self.surface: self.egl.eglDestroySurface(self.display, self.surface)
            self.egl.eglTerminate(self.display)
        self.display = self.context = self.surface = None

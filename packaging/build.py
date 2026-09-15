"""Build Filmocity into one self-contained .exe.

    .venv\\Scripts\\python.exe packaging\\build.py

Ships the source tree as DATA under its repo-relative names, because
backend/server.py:20 derives FRONT/ASSETS/DOCS from its own __file__.
Third-party packages are collected whole rather than trusting static
analysis: server.py travels as data, so PyInstaller never sees its imports,
and uvicorn/starlette resolve half their machinery by string at runtime.

Output: dist/Filmocity.exe -- expect ~320MB, of which 279MB is ffmpeg+ffprobe.
That size is a deliberate call: for a zero-support product a codec that is
missing on someone else's footage is a refund, and 200MB of download is not.
"""
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# repo-relative dirs shipped verbatim; the layout IS the contract
DATA = ["backend", "frontend", "assets", "docs", "bin"]

COLLECT = ["uvicorn", "fastapi", "starlette", "websockets", "multipart", "PIL", "webview"]

HIDDEN = [
    "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto", "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto", "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on", "uvicorn.lifespan.off",
]


def main():
    missing = [d for d in DATA if not os.path.isdir(os.path.join(ROOT, d))]
    # assets/ and docs/ are optional in this checkout; bin/ and the code are not
    fatal = [d for d in missing if d in ("backend", "frontend", "bin")]
    if fatal:
        print("CANNOT_RUN: required directories absent: %s" % ", ".join(fatal))
        return 2
    for d in missing:
        print("note: skipping absent optional dir %s/" % d)

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--onefile", "--name", "Filmocity", "--distpath", os.path.join(ROOT, "dist"),
           "--workpath", os.path.join(ROOT, "build"), "--specpath", HERE,
           "--log-level", "WARN",
           # UTF-8 MODE. Non-negotiable, and the first build shipped without it:
           # backend/server.py:294 is open(...index.html).read() with no encoding,
           # and 66 text opens across backend/ and launcher/ take the platform
           # default -- cp1252 here. Filmocity.bat solves it with PYTHONUTF8=1, but
           # an env var cannot help a frozen app: the interpreter is already
           # running before any of our code executes. This passes -X utf8=1 at
           # interpreter init, which is the only place it can be set.
           # Verified empirically before use (sys.flags.utf8_mode == 1); the flag
           # is a proxy, so the real check is that index.html actually serves.
           "--python-option", "X utf8=1"]

    ico = os.path.join(os.path.expanduser("~"), "Apps", "filmocity.ico")
    if os.path.exists(ico):
        cmd += ["--icon", ico]

    for d in DATA:
        p = os.path.join(ROOT, d)
        if os.path.isdir(p):
            cmd += ["--add-data", "%s%s%s" % (p, os.pathsep, d)]
    for m in COLLECT:
        cmd += ["--collect-all", m]
    for m in HIDDEN:
        cmd += ["--hidden-import", m]

    # windowed: no console box behind the app window
    cmd += ["--windowed", os.path.join(HERE, "filmocity_app.py")]

    print("building (this takes a few minutes; 279MB of ffmpeg has to be hashed and packed)")
    t0 = time.time()
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        print("BUILD FAILED rc=%d" % r.returncode)
        return r.returncode

    exe = os.path.join(ROOT, "dist", "Filmocity.exe")
    if not os.path.exists(exe):
        print("BUILD REPORTED SUCCESS BUT PRODUCED NO EXE -- treat as failure")
        return 1
    mb = os.path.getsize(exe) / 1024.0 / 1024.0
    print("OK  %s  %.1f MB  in %.0fs" % (exe, mb, time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())

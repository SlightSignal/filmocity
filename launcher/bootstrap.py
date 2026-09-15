#!/usr/bin/env python3
"""Filmocity one-click bootstrap: creates a virtualenv, installs Python deps, fetches FFmpeg if missing, then launches.
usage:  python3 launcher/bootstrap.py install [--with-whisper]     # dependencies only
        python3 launcher/bootstrap.py run [--port 8787] [--data ~/filmocity_data] [--no-browser]
        python3 launcher/bootstrap.py                                # install (if needed) + run
Works on Windows, macOS and Linux with Python 3.10+. Everything is kept inside the Filmocity folder (.venv/, bin/)."""
import os, sys, subprocess, platform, shutil, tarfile, zipfile, urllib.request, time, webbrowser, argparse, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV = os.path.join(ROOT, ".venv"); BIN = os.path.join(ROOT, "bin"); IS_WIN = os.name == "nt"
PY = os.path.join(VENV, "Scripts" if IS_WIN else "bin", "python.exe" if IS_WIN else "python")
FF = os.path.join(BIN, "ffmpeg.exe" if IS_WIN else "ffmpeg"); FP = os.path.join(BIN, "ffprobe.exe" if IS_WIN else "ffprobe")
LOG = os.path.join(ROOT, "launcher", "install.log")

def say(msg): print(f"[filmocity] {msg}", flush=True); open(LOG, "a").write(time.strftime("%H:%M:%S ") + msg + "\n")
def run(cmd, **kw): say("$ " + " ".join(cmd)); return subprocess.run(cmd, check=True, **kw)

def ensure_python():
    if sys.version_info < (3, 10): sys.exit("Python 3.10 or newer is required (found %s). Install from python.org and re-run." % platform.python_version())

def ensure_venv():
    if not os.path.exists(PY):
        say("creating virtual environment"); run([sys.executable, "-m", "venv", VENV])
    run([PY, "-m", "pip", "install", "--upgrade", "pip", "-q"])
    run([PY, "-m", "pip", "install", "-q", "-r", os.path.join(ROOT, "requirements.txt")])

def have_ffmpeg():
    if os.path.exists(FF) and os.path.exists(FP): return True
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

def fetch(url, dest):
    say(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0); got = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk: break
            f.write(chunk); got += len(chunk)
            if total: print(f"\r  {got / 1e6:6.1f} / {total / 1e6:6.1f} MB", end="", flush=True)
    print()

def ensure_ffmpeg():
    if have_ffmpeg(): say("ffmpeg found"); return
    os.makedirs(BIN, exist_ok=True); sysname = platform.system(); arch = platform.machine().lower()
    tmp = os.path.join(BIN, "_ffmpeg_download")
    try:
        if sysname == "Windows":
            fetch("https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip", tmp + ".zip")
            with zipfile.ZipFile(tmp + ".zip") as z:
                for n in z.namelist():
                    base = os.path.basename(n)
                    if base in ("ffmpeg.exe", "ffprobe.exe"): open(os.path.join(BIN, base), "wb").write(z.read(n))
        elif sysname == "Linux":
            name = "ffmpeg-master-latest-linuxarm64-gpl.tar.xz" if ("arm" in arch or "aarch64" in arch) else "ffmpeg-master-latest-linux64-gpl.tar.xz"
            fetch(f"https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/{name}", tmp + ".tar.xz")
            with tarfile.open(tmp + ".tar.xz") as t:
                for m in t.getmembers():
                    base = os.path.basename(m.name)
                    if base in ("ffmpeg", "ffprobe") and m.isfile():
                        with open(os.path.join(BIN, base), "wb") as f: f.write(t.extractfile(m).read())
                        os.chmod(os.path.join(BIN, base), 0o755)
        elif sysname == "Darwin":
            if shutil.which("brew"):
                say("installing ffmpeg with Homebrew"); run(["brew", "install", "ffmpeg"]); return
            for tool in ("ffmpeg", "ffprobe"):
                fetch(f"https://evermeet.cx/ffmpeg/getrelease/{tool}/zip", tmp + f"_{tool}.zip")
                with zipfile.ZipFile(tmp + f"_{tool}.zip") as z:
                    for n in z.namelist():
                        if os.path.basename(n) == tool: open(os.path.join(BIN, tool), "wb").write(z.read(n)); os.chmod(os.path.join(BIN, tool), 0o755)
                    say("note: on first run macOS may ask you to allow the downloaded ffmpeg (System Settings → Privacy & Security)")
    finally:
        for f in os.listdir(BIN):
            if f.startswith("_ffmpeg_download"): os.remove(os.path.join(BIN, f))
    if not have_ffmpeg(): sys.exit("ffmpeg could not be installed automatically. Install it manually (https://ffmpeg.org/download.html) and make sure ffmpeg + ffprobe are on PATH, or put them in Filmocity/bin/.")
    say("ffmpeg ready")

def install(with_whisper=False):
    ensure_python(); ensure_venv(); ensure_ffmpeg()
    if with_whisper:
        say("installing faster-whisper (auto-captions)"); run([PY, "-m", "pip", "install", "-q", "faster-whisper"])
    json.dump({"installed": time.time(), "python": platform.python_version(), "ffmpeg": FF if os.path.exists(FF) else shutil.which("ffmpeg")}, open(os.path.join(ROOT, "launcher", "installed.json"), "w"))
    say("install complete")

def env_with_bin():
    # PYTHONUTF8 matters on Windows: without it the server reads its own UTF-8
    # frontend and assets in cp1252 and the front page 500s. Filmocity.bat sets it
    # too, but the server must be safe from every entry point, this one included.
    env = dict(os.environ); env["PATH"] = BIN + os.pathsep + env.get("PATH", ""); env["PYTHONUTF8"] = "1"; return env

def serve(port=8787, data=None, browser=True, host="127.0.0.1", token=None):
    if not (os.path.exists(PY) and have_ffmpeg()):
        say("dependencies missing — installing first"); install()
    data = data or os.path.join(os.path.expanduser("~"), "filmocity_data"); os.makedirs(data, exist_ok=True)
    say(f"starting Filmocity on http://localhost:{port}  (data: {data})")
    args = [PY, os.path.join(ROOT, "backend", "server.py"), "--root", data, "--port", str(port), "--host", host] + (["--token", token] if token else [])
    proc = subprocess.Popen(args, env=env_with_bin())
    url = f"http://localhost:{port}" + (f"/?token={token}" if token else "")
    for _ in range(60):
        time.sleep(0.5)
        try: urllib.request.urlopen(f"http://localhost:{port}/api/version", timeout=1); break
        except Exception:
            if proc.poll() is not None: sys.exit("server exited early — see the messages above")
    if browser: webbrowser.open(url)
    say("Filmocity is running. Close this window (or press Ctrl+C) to stop.")
    try: proc.wait()
    except KeyboardInterrupt: proc.terminate()

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", nargs="?", default="auto", choices=["auto", "install", "run"]); ap.add_argument("--with-whisper", action="store_true"); ap.add_argument("--port", type=int, default=8787); ap.add_argument("--data"); ap.add_argument("--no-browser", action="store_true"); ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--token", default=None)
    a = ap.parse_args(); os.chdir(ROOT)
    if a.cmd == "install": install(a.with_whisper)
    elif a.cmd == "run": serve(a.port, a.data, not a.no_browser, a.host, a.token)
    else:
        if not (os.path.exists(PY) and have_ffmpeg() and os.path.exists(os.path.join(ROOT, "launcher", "installed.json"))): install(a.with_whisper)
        serve(a.port, a.data, not a.no_browser, a.host, a.token)

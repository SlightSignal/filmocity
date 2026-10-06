#!/usr/bin/env python3
"""Filmocity bootstrap: creates a virtualenv, installs Python deps, verifies FFmpeg is supplied, then launches.
usage:  python3 launcher/bootstrap.py install [--with-whisper]     # dependencies only
        python3 launcher/bootstrap.py run [--port 8787] [--data ~/filmocity_data] [--no-browser]
        python3 launcher/bootstrap.py                                # install (if needed) + run
Windows release target; other source launchers are experimental. Requires Python 3.11+.
Everything is kept inside the Filmocity folder (.venv/, bin/)."""
import os, sys, subprocess, platform, shutil, time, webbrowser, argparse, json, uuid, signal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'packaging'))
from runtime_policy import load_policy, current_runtime, validate_runtime, probe_interpreter, RuntimePolicyError
VENV = os.path.join(ROOT, ".venv"); BIN = os.path.join(ROOT, "bin"); IS_WIN = os.name == "nt"
PY = os.path.join(VENV, "Scripts" if IS_WIN else "bin", "python.exe" if IS_WIN else "python")
FF = os.path.join(BIN, "ffmpeg.exe" if IS_WIN else "ffmpeg"); FP = os.path.join(BIN, "ffprobe.exe" if IS_WIN else "ffprobe")
LOG = os.path.join(ROOT, "launcher", "install.log")

def say(msg): print(f"[filmocity] {msg}", flush=True); open(LOG, "a").write(time.strftime("%H:%M:%S ") + msg + "\n")
def run(cmd, **kw): say("$ " + " ".join(cmd)); return subprocess.run(cmd, check=True, **kw)

def ensure_python():
    if sys.version_info < (3, 11): sys.exit("Python 3.11 or newer is required (found %s). Install from python.org and re-run." % platform.python_version())
    if IS_WIN:
        try: validate_runtime(current_runtime(), load_policy(os.path.join(ROOT, 'packaging', 'runtime-windows.json')))
        except RuntimePolicyError as error: sys.exit(str(error) + ' Install the selected runtime from python.org; existing environments are unchanged.')


def ensure_environment_runtime():
    if not IS_WIN: return
    try:
        policy = load_policy(os.path.join(ROOT, 'packaging', 'runtime-windows.json'))
        return probe_interpreter(PY, policy)
    except RuntimePolicyError as error:
        sys.exit(str(error) + f' Existing environment {VENV} was preserved. Use a fresh source environment with the selected runtime, or preserve and rename the old .venv deliberately before retrying.')

def ensure_venv():
    ensure_python()
    if IS_WIN and os.path.lexists(VENV) and not os.path.isfile(PY):
        sys.exit(f'Existing environment {VENV} has no usable interpreter and was preserved. Use a fresh source environment, or preserve and rename the old .venv deliberately before retrying.')
    if not os.path.exists(PY):
        say("creating virtual environment"); run([sys.executable, "-m", "venv", VENV])
    if IS_WIN:
        ensure_environment_runtime()  # Refuse stale/foreign venvs before pip writes.
        run([PY, "-m", "pip", "install", "--require-hashes", "-r", os.path.join(ROOT, "requirements-bootstrap-windows.lock")])
        run([PY, "-m", "pip", "install", "--require-hashes", "--no-build-isolation", "-r", os.path.join(ROOT, "requirements-runtime-windows.lock")])
    else:
        say("experimental source platform: dependency versions are not the reviewed Windows lock")
        run([PY, "-m", "pip", "install", "-q", "-r", os.path.join(ROOT, "requirements.txt")])

def have_ffmpeg():
    if os.path.exists(FF) and os.path.exists(FP): return True
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

def ensure_ffmpeg():
    if have_ffmpeg(): say("ffmpeg found"); return
    sys.exit("FFmpeg and ffprobe are required. Supply a reviewed distribution in Filmocity/bin/ or on PATH. "
             "Keep its licenses and corresponding-source information. See https://ffmpeg.org/download.html "
             "and THIRD_PARTY_NOTICES.md. Filmocity does not silently download rolling executable builds.")

def install(with_whisper=False):
    ensure_python(); ensure_ffmpeg(); ensure_venv()
    if with_whisper:
        say("installing faster-whisper (auto-captions)"); run([PY, "-m", "pip", "install", "-q", "faster-whisper"])
    json.dump({"installed": time.time(), "python": platform.python_version(), "ffmpeg": FF if os.path.exists(FF) else shutil.which("ffmpeg")}, open(os.path.join(ROOT, "launcher", "installed.json"), "w"))
    say("install complete")

def env_with_bin():
    # PYTHONUTF8 matters on Windows: without it the server reads its own UTF-8
    # frontend and assets in cp1252 and the front page 500s. Filmocity.bat sets it
    # too, but the server must be safe from every entry point, this one included.
    env = dict(os.environ); env["PATH"] = BIN + os.pathsep + env.get("PATH", ""); env["PYTHONUTF8"] = "1"; return env

def stop_child(proc, timeout=20):
    """Request lifecycle cleanup, then bound failure using only our Popen handle."""
    if proc.poll() is not None: return
    try:
        proc.send_signal(signal.CTRL_BREAK_EVENT if IS_WIN else signal.SIGTERM)
        proc.wait(timeout=timeout)
        return
    except (OSError, ValueError, subprocess.TimeoutExpired):
        say("backend did not finish graceful shutdown; stopping the owned child")
    if proc.poll() is None:
        proc.terminate()
        try: proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait(timeout=5)

def serve(port=8787, data=None, browser=True, host="127.0.0.1", token=None):
    ensure_python()
    if os.path.exists(PY): ensure_environment_runtime()
    if not (os.path.exists(PY) and have_ffmpeg()):
        say("dependencies missing — installing first"); install()
    if not 1 <= port <= 65535: raise ValueError("Port must be between 1 and 65535")
    data = os.path.abspath(os.path.expanduser(data or os.path.join(os.path.expanduser("~"), "filmocity_data"))); os.makedirs(data, exist_ok=True)
    say(f"starting Filmocity on http://localhost:{port}  (data: {data})")
    args = [PY, os.path.join(ROOT, "backend", "server.py"), "--root", data, "--port", str(port), "--host", host] + (["--token", token] if token else [])
    env = env_with_bin(); env["FILMOCITY_LAUNCH_ID"] = uuid.uuid4().hex
    sys.path.insert(0, os.path.join(ROOT, "backend"))
    from runtime_identity import wait_for_backend
    options = {}
    if IS_WIN:
        sys.path.insert(0, os.path.join(ROOT, "packaging"))
        from windows_lifetime import bind_lifetime
        bind_lifetime()  # Includes this launcher, backend and owned encoders.
        options['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
    url = f"http://localhost:{port}" + (f"/?token={token}" if token else "")
    proc = subprocess.Popen(args, env=env, **options)
    try:
        wait_for_backend(f"http://localhost:{port}", instance=env["FILMOCITY_LAUNCH_ID"], root=data, pid=proc.pid, alive=lambda: proc.poll() is None, timeout=60)
        if browser: webbrowser.open(url)
        say("Filmocity is running. Close this window (or press Ctrl+C) to stop.")
        proc.wait()
    except KeyboardInterrupt:
        pass
    finally:
        stop_child(proc)

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", nargs="?", default="auto", choices=["auto", "install", "run"]); ap.add_argument("--with-whisper", action="store_true"); ap.add_argument("--port", type=int, default=8787); ap.add_argument("--data"); ap.add_argument("--no-browser", action="store_true"); ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--token", default=None)
    a = ap.parse_args(); os.chdir(ROOT)
    if a.cmd == "install": install(a.with_whisper)
    elif a.cmd == "run": serve(a.port, a.data, not a.no_browser, a.host, a.token)
    else:
        if not (os.path.exists(PY) and have_ffmpeg() and os.path.exists(os.path.join(ROOT, "launcher", "installed.json"))): install(a.with_whisper)
        serve(a.port, a.data, not a.no_browser, a.host, a.token)

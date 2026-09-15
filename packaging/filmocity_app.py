"""Filmocity, frozen. Single-file entry point for PyInstaller.

One .exe, no install, no Python, no Chrome. What this has to reproduce
from launcher/bootstrap.py, and nothing more:

  1. bin/ on PATH        -- backend/server.py shells out to bare "ffmpeg"
                            and "ffprobe" in 25 places and launcher/ in 6
                            more. bootstrap.py:85 solves this by handing a
                            patched env to a subprocess; frozen, we run the
                            server in-process, so we patch os.environ here
                            once and all 31 sites are covered.
  2. FILMOCITY_ROOT        -- backend/server.py:21 reads it AT IMPORT TIME,
                            so it must be set before `import server`.
                            Default ~/filmocity_data, which is already what
                            bootstrap.py:88 uses, so an exe and a source
                            checkout share one data directory on purpose.
  3. import, don't exec  -- server.py's `else` branch (its last line) calls
                            mount() when imported rather than run, so
                            importing it is the supported path and the
                            static mounts come up on their own.

The one thing that is genuinely different frozen: FRONT/ASSETS/DOCS are
derived from server.py's own __file__ (server.py:20). PyInstaller must
therefore ship the source tree as DATA, laid out exactly as in the repo, so
that __file__ is a real extracted path and the siblings resolve. See
build.py -- it adds backend/, frontend/, assets/, docs/ and bin/ under the
same relative names. If that layout ever changes, this breaks loudly at
startup rather than serving a blank page, because of the check below.
"""
import os
import socket
import sys
import threading
import time
import urllib.request


def base_dir():
    """Where the app's files live: the extraction dir when frozen, the repo otherwise."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def exe_dir():
    """The folder the user sees -- where Filmocity.exe sits, NOT the onefile
    extraction dir. sys.executable is the exe when frozen; __file__ would
    point into %TEMP%\\_MEIxxxx and portable mode would silently never fire."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port(preferred=8787):
    # FILMOCITY_PORT pins the port. Exists because a smoke test that polls a
    # RANGE will happily find someone else's Filmocity on 8787 and report it as
    # this build passing -- which is exactly what happened on the first run.
    pinned = os.environ.get("FILMOCITY_PORT")
    if pinned:
        return int(pinned)
    for p in [preferred] + list(range(8788, 8828)):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    with socket.socket() as s:          # let the OS pick
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    base = base_dir()

    # 1. ffmpeg/ffprobe -- every shell-out in the backend relies on PATH.
    binp = os.path.join(base, "bin")
    if os.path.isdir(binp):
        os.environ["PATH"] = binp + os.pathsep + os.environ.get("PATH", "")

    # 2. data directory -- must exist before server.py is imported.
    #
    # PORTABLE MODE. If a folder named filmocity_data sits NEXT TO the exe, use
    # it instead of ~/filmocity_data. That makes a flash drive behave the way
    # old games did: the app and its saves travel together, and running it on
    # a borrowed machine leaves nothing behind in that user's home directory.
    # Opt-in by construction -- create the folder and it is portable, delete
    # it and it is not. FILMOCITY_ROOT still overrides both.
    root = os.environ.get("FILMOCITY_ROOT")
    if not root:
        beside = os.path.join(exe_dir(), "filmocity_data")
        root = beside if os.path.isdir(beside) else os.path.join(os.path.expanduser("~"), "filmocity_data")
    os.environ["FILMOCITY_ROOT"] = root
    os.makedirs(root, exist_ok=True)

    # 2b. STDOUT. Under --windowed (GUI subsystem) sys.stdout and sys.stderr
    # are None. uvicorn's ColourizedFormatter does sys.stdout.isatty() while
    # configuring logging, which raises AttributeError on NoneType and
    # surfaces as "Unable to configure formatter 'default'" -- a hard crash
    # before the server ever starts.
    #
    # This NEVER reproduced in testing because every launch here came from a
    # shell, which hands the child an inherited stdout handle. Double-clicking
    # from Explorer does not. The launch method WAS the test condition and it
    # was never varied. Point the streams at a log file rather than devnull,
    # so the next field failure leaves something to read.
    if sys.stdout is None or sys.stderr is None:
        try:
            sink = open(os.path.join(root, "filmocity.log"), "a", encoding="utf-8", buffering=1)
        except Exception:
            sink = open(os.devnull, "w", encoding="utf-8")
        if sys.stdout is None:
            sys.stdout = sink
        if sys.stderr is None:
            sys.stderr = sink

    # 3. import the server (its else-branch mounts static for us)
    sys.path.insert(0, os.path.join(base, "backend"))
    try:
        import server
    except Exception as e:
        _die("Filmocity could not start its backend.\n\n%s: %s" % (type(e).__name__, e))
        return

    # Fail loudly if the bundled layout is wrong, rather than serving blank pages.
    missing = [n for n, p in (("frontend", server.FRONT),) if not os.path.isdir(p)]
    if missing:
        _die("Bundled files are missing: %s\nLooked in: %s\nThis is a packaging fault, not a your-machine fault."
             % (", ".join(missing), server.FRONT))
        return

    port = free_port()
    url = "http://127.0.0.1:%d" % port

    import uvicorn
    cfg = uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(cfg)
    threading.Thread(target=srv.run, daemon=True).start()

    for _ in range(120):                # up to 60s; first run unpacks 280MB
        time.sleep(0.5)
        try:
            urllib.request.urlopen(url + "/api/version", timeout=1)
            break
        except Exception:
            pass
    else:
        _die("Filmocity's backend did not answer on %s within 60 seconds." % url)
        return

    # 4. a real window, not a browser tab. WebView2 ships with Win11 and
    #    most Win10; if it is absent we fall back rather than fail.
    #
    # FILMOCITY_HEADLESS=1 serves without opening one. Added because scripted
    # runs each opened a window and left it on the owner's desktop -- he found
    # a stack of them twice. A tool that is driven by something other than a
    # person should not insist on a window; the absence of this flag was the
    # defect, not the number of times I forgot to close them.
    if os.environ.get("FILMOCITY_HEADLESS") == "1":
        print("headless: serving %s, no window (FILMOCITY_HEADLESS=1)" % url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return

    try:
        import webview
        webview.create_window("Filmocity", url, width=1600, height=980, min_size=(1100, 700))
        webview.start()
    except Exception:
        import webbrowser
        webbrowser.open(url)
        print("Filmocity is running at %s -- close this window to stop it." % url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


def _die(msg):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, "Filmocity", 0x10)
    except Exception:
        pass
    print(msg, file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    # Anything that escapes main() reaches a double-clicking user as
    # PyInstaller's raw traceback box. Keep the detail -- it is what made the
    # stdout crash diagnosable -- but say where the log is and stay readable.
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        import traceback
        tb = traceback.format_exc()
        try:
            r = os.environ.get("FILMOCITY_ROOT", "")
            if r:
                with open(os.path.join(r, "filmocity.log"), "a", encoding="utf-8") as fh:
                    fh.write("\n--- crash ---\n" + tb)
        except Exception:
            pass
        _die("Filmocity hit an unexpected error and could not start.\n\n%s\n\nA full log was written to:\n%s"
             % (tb.strip().splitlines()[-1], os.path.join(os.environ.get("FILMOCITY_ROOT", "?"), "filmocity.log")))

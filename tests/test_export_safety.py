"""Failure injection and real frame-boundary regression tests; no production projects are touched."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import render as engine


def windows_creation(handle):
    """Identify the process behind an already-owned handle, including after exit."""
    if os.name != "nt": return None
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    values = [wintypes.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in values)):
        raise ctypes.WinError(ctypes.get_last_error())
    return (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime


ENCODER_ADMISSION = '''import ctypes, json, os, pathlib, sys
creation = None
if os.name == 'nt':
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    values = [wintypes.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(kernel.GetCurrentProcess(), *(ctypes.byref(value) for value in values)):
        raise ctypes.WinError(ctypes.get_last_error())
    creation = (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime
with open(sys.argv[2], 'wb') as owned:
    owned.write(b'actual encoder fixture'); owned.flush()
    ready = pathlib.Path(sys.argv[1]); pending = ready.with_suffix('.pending')
    pending.write_text(json.dumps({'pid': os.getpid(), 'creationFiletime': creation}), encoding='utf-8')
    pending.replace(ready)
    exec(sys.argv[3])
'''


class ExportSafety(unittest.TestCase):
    def run_fake_encoder(self, command, *, admitted=None, **kwargs):
        # The fixture is Python, so strip the FFmpeg-only progress switches at the process boundary.
        # A Windows venv redirector is not the real encoder stand-in we must retire.
        spawn = subprocess.Popen
        ready = self.root / "encoder-ready.json"
        owned = self.root / "encoder-owned.txt"
        interpreter = sys._base_executable if os.name == "nt" else command[0]
        def start(cmd, **options):
            self.assertEqual(cmd[5], "-c")
            proc = spawn([interpreter, "-c", ENCODER_ADMISSION, str(ready), str(owned), cmd[6]], **options)
            creation = windows_creation(int(proc._handle)) if os.name == "nt" else None
            self.encoder_children.append((proc, creation))
            until = time.monotonic() + 5
            while not ready.exists() and proc.poll() is None and time.monotonic() < until:
                time.sleep(.01)
            self.assertTrue(ready.exists(), "actual encoder fixture did not acknowledge admission")
            self.assertEqual(json.loads(ready.read_text(encoding="utf-8")),
                             {"pid": proc.pid, "creationFiletime": creation})
            if admitted: admitted(proc)
            return proc
        with patch.object(engine.subprocess, "Popen", side_effect=start):
            return engine._run_ffmpeg(command, **kwargs)
    def assert_encoder_retired(self):
        self.assertEqual(len(self.encoder_children), 1)
        proc, creation = self.encoder_children[0]
        self.assertIsNotNone(proc.poll(), "real admitted encoder fixture is still running")
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            self.assertEqual(windows_creation(int(proc._handle)), creation)
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.WaitForSingleObject.restype = wintypes.DWORD
            self.assertEqual(kernel.WaitForSingleObject(int(proc._handle), 0), 0)
        self.assertTrue(proc.stdout.closed); self.assertTrue(proc.stderr.closed)
        # The admitted process held this file; deleting it verifies Windows handle retirement.
        (self.root / "encoder-owned.txt").unlink()
    def setUp(self):
        self.encoder_children = []
        self.temp = tempfile.TemporaryDirectory(prefix="filmocity-export-")
        self.root = Path(self.temp.name)
        self.source = self.root / "media Émile's clip.mp4"
        self.source.write_bytes(b"original media")
        self.project = {"media": {"a": {"id": "a", "path": str(self.source), "has_video": True, "has_audio": False}},
            "sequences": [{"id": "s", "name": "Test", "width": 64, "height": 48, "fps": 24, "duration": 2,
                "tracks": [{"id": "V1", "kind": "video", "index": 1, "clips": [{"id": "c", "media_id": "a", "start": 0, "in_": 0, "out": 2}]}], "captions": []}]}
    def tearDown(self):
        # Failure cleanup uses only handles captured by this fixture. Assertions
        # above must prove production retirement before this cleanup can run.
        try:
            for proc, _ in self.encoder_children:
                if proc.poll() is None: proc.kill()
                proc.wait(timeout=5)
                for stream in (proc.stdout, proc.stderr):
                    if stream is not None: stream.close()
        finally: self.temp.cleanup()

    def test_replaced_bytes_with_preserved_timestamps_invalidate_cache(self):
        sequence = self.project["sequences"][0]; before = engine.chunk_key(self.project, sequence, {})
        stamp = self.source.stat(); self.source.write_bytes(b"changed! media")
        os.utime(self.source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.assertNotEqual(before, engine.chunk_key(self.project, sequence, {}))

    def test_missing_media_cannot_reuse_existing_cached_key(self):
        sequence = self.project["sequences"][0]; before = engine.chunk_key(self.project, sequence, {})
        self.source.unlink(); self.assertNotEqual(before, engine.chunk_key(self.project, sequence, {}))

    def test_hash_is_shared_within_one_export_not_across_exports(self):
        revisions = {}; sequence = self.project["sequences"][0]
        with patch.object(engine.hashlib, "file_digest", wraps=engine.hashlib.file_digest) as digest:
            engine.chunk_key(self.project, sequence, {}, revisions); engine.chunk_key(self.project, sequence, {}, revisions)
            count = digest.call_count; self.assertGreater(count, 0)
            engine.chunk_key(self.project, sequence, {}, revisions); self.assertEqual(digest.call_count, count)
            engine.chunk_key(self.project, sequence, {}, {}); self.assertEqual(digest.call_count, count * 2)

    def test_watermark_and_resolved_font_bytes_invalidate_cache(self):
        watermark = self.root / "logo.png"; watermark.write_bytes(b"old logo")
        font = self.root / "test.ttf"; font.write_bytes(b"old font")
        sequence = self.project["sequences"][0]
        with patch.object(engine, "bundled_font", return_value=str(font)):
            before = engine.chunk_key(self.project, sequence, {"watermark": {"path": str(watermark)}})
            watermark.write_bytes(b"new logo")
            after = engine.chunk_key(self.project, sequence, {"watermark": {"path": str(watermark)}})
            self.assertNotEqual(before, after); font.write_bytes(b"new font")
            self.assertNotEqual(after, engine.chunk_key(self.project, sequence, {"watermark": {"path": str(watermark)}}))

    def test_nested_source_bytes_invalidate_parent(self):
        sub = copy.deepcopy(self.project["sequences"][0]); sub["id"] = "nested"
        self.project["sequences"].append(sub); parent = self.project["sequences"][0]
        parent["tracks"][0]["clips"] = [{"id": "nest", "sequence_id": "nested", "start": 0, "in_": 0, "out": 2}]
        before = engine.chunk_key(self.project, parent, {}); self.source.write_bytes(b"replacement")
        self.assertNotEqual(before, engine.chunk_key(self.project, parent, {}))

    def test_lut_bytes_invalidate_cache(self):
        lut = self.root / "look.cube"; lut.write_text("old")
        sequence = self.project["sequences"][0]; sequence["tracks"][0]["clips"][0]["color"] = {"lut": str(lut)}
        before = engine.chunk_key(self.project, sequence, {}); lut.write_text("new")
        self.assertNotEqual(before, engine.chunk_key(self.project, sequence, {}))

    def test_failed_full_export_preserves_existing_master(self):
        out = self.root / "master.mp4"; out.write_bytes(b"approved master")
        with patch.object(engine, "_run_ffmpeg", side_effect=RuntimeError("injected encoder failure")):
            with self.assertRaisesRegex(RuntimeError, "injected"): engine.render(self.project, "s", str(out))
        self.assertEqual(out.read_bytes(), b"approved master")
        self.assertFalse(list(self.root.glob("*.part.mp4")))

    def test_pre_cancelled_job_never_spawns_encoder(self):
        with patch.object(engine.subprocess, "Popen") as spawn:
            with self.assertRaisesRegex(RuntimeError, "cancelled"): engine._run_ffmpeg(["ffmpeg"], proc_holder={"cancelled": True})
            spawn.assert_not_called()

    def test_stalled_process_is_killed(self):
        with self.assertRaisesRegex(RuntimeError, "stalled"):
            self.run_fake_encoder([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.2)
        self.assert_encoder_retired()

    def test_cancellation_during_process_cleans_holder(self):
        holder = {}; timer = threading.Timer(0.1, lambda: holder.update(cancelled=True))
        try:
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                self.run_fake_encoder([sys.executable, "-c", "import time; time.sleep(30)"],
                                      admitted=lambda proc: timer.start(), proc_holder=holder)
            self.assertNotIn("proc", holder)
            self.assert_encoder_retired()
        finally:
            timer.cancel()
            if timer.ident is not None: timer.join(timeout=5)

    def test_large_stderr_does_not_deadlock(self):
        self.run_fake_encoder([sys.executable, "-c", "import sys; sys.stderr.write('x'*200000); sys.stdout.write('out_time_us=1000\\n'); sys.stdout.flush()"], timeout=2)
        self.assert_encoder_retired()

    def test_verification_failure_is_visible_not_an_empty_pass(self):
        import server
        with patch.object(server.subprocess, "run", side_effect=subprocess.TimeoutExpired("ffprobe", 30)):
            qa = server.render_qa(str(self.source))
        self.assertEqual(qa["status"], "error"); self.assertTrue(qa["flags"]); self.assertIn("error", qa)

    def test_real_full_and_incremental_have_no_missing_cut_or_last_frames(self):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=64x48:r=24:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(self.source)], check=True)
        sequence = self.project["sequences"][0]
        sequence["tracks"][0]["clips"] = [{"id": "a", "media_id": "a", "start": 0, "in_": 0, "out": 1}, {"id": "b", "media_id": "a", "start": 1, "in_": 1, "out": 2}]
        for incremental in (False, True):
            out = str(self.root / f"master-{incremental}.mp4")
            preset = {"crf": 18, "x264_preset": "ultrafast"}
            if incremental: engine.render_incremental(self.project, "s", out, preset, cache_dir=str(self.root / "cache Émile's"))
            else: engine.render(self.project, "s", out, preset)
            data = subprocess.run(["ffmpeg", "-v", "error", "-i", out, "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], check=True, capture_output=True).stdout
            size = 64*48*3; self.assertEqual(len(data)//size, 48)
            for frame in (0,22,23,24,25,46,47): self.assertGreater(data[frame*size], 200, (incremental, frame))
            import server
            qa = server.render_qa(out)
            self.assertIn(qa["status"], ("checked", "warnings"), qa); self.assertNotIn("error", qa); self.assertEqual(len(qa["sha256"]), 64); self.assertEqual(qa["pixel_format"], "yuv420p")


if __name__ == "__main__": unittest.main()

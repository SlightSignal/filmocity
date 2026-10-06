"""PNG publication boundary: injected failures plus real FFmpeg, no user projects.

Run with an existing Python environment and ffmpeg/ffprobe on PATH. The suite
fails (does not skip) if the real encoder is unavailable. All temporary/data
paths, including owned render scratch, are confined to this source copy.
"""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / ".png-publication-tests"
SCRATCH.mkdir(exist_ok=True)
os.environ.update(TEMP=str(SCRATCH), TMP=str(SCRATCH), FILMOCITY_ROOT=str(SCRATCH / "data"))
tempfile.tempdir = str(SCRATCH)
sys.path.insert(0, str(ROOT / "backend"))
import render as engine
import server
from PIL import Image


def project(duration=.5, fps=24):
    return {"media": {}, "sequences": [{"id": "s", "name": "PNG", "width": 64,
            "height": 48, "fps": fps, "duration": duration, "captions": [], "tracks": []}]}


class StopWorker(Exception): pass


class OneJobQueue(queue.Queue):
    def get(self, *args, **kwargs):
        if self.empty(): raise StopWorker()
        return super().get(*args, **kwargs)


class PngPublication(unittest.TestCase):
    def setUp(self):
        self.root = SCRATCH / ("case-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.out = self.root / "Émile's export.png"
        self.destination = Path(engine.png_sequence_directory(self.out))
        self.p = project()
        self.preset = {"format": "png_sequence"}
        self.holder = {}
        self.destination.mkdir()
        (self.destination / "frame_00001.png").write_bytes(b"approved frame one")
        (self.destination / "frame_00013.png").write_bytes(b"approved old tail")
        (self.destination / "sequence.json").write_bytes(b"approved manifest")
        self.old = self.snapshot()

    def tearDown(self): shutil.rmtree(self.root)

    def snapshot(self):
        return {p.name: p.read_bytes() for p in self.destination.iterdir()}

    def assert_old(self):
        self.assertEqual(self.snapshot(), self.old)

    def assert_clean(self):
        self.assertFalse(list(self.root.glob(".*.stage-*")))
        self.assertFalse(list(self.root.glob("*.frames.lock")))
        self.assertNotIn("proc", self.holder)

    def export(self, **kwargs):
        return engine.render(self.p, "s", str(self.out), self.preset,
                             proc_holder=kwargs.pop("proc_holder", self.holder), **kwargs)

    def frames(self, cmd, count=12, size=(64, 48)):
        stage = Path(cmd[-1]).parent
        for i in range(1, count + 1):
            Image.new("RGB", size, (i, 80, 160)).save(stage / f"frame_{i:05d}.png")
        return stage

    def test_partial_encoder_failure_preserves_old_and_cleans(self):
        def fail(cmd, **kwargs):
            self.frames(cmd, count=2)
            self.assert_old()
            raise RuntimeError("injected encoder failure")
        with patch.object(engine, "_run_ffmpeg", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "injected encoder"): self.export()
        self.assert_old(); self.assert_clean()

    def test_pre_cancelled_never_builds_or_spawns(self):
        self.holder["cancelled"] = True
        with patch.object(engine, "build_command") as build, patch.object(engine.subprocess, "Popen") as spawn:
            with self.assertRaisesRegex(RuntimeError, "cancelled"): self.export()
            build.assert_not_called(); spawn.assert_not_called()
        self.assert_old(); self.assert_clean()

    def test_cancellation_after_encoder_success_preserves_old(self):
        def cancel(cmd, **kwargs):
            self.frames(cmd); self.holder["cancelled"] = True
        with patch.object(engine, "_run_ffmpeg", side_effect=cancel):
            with self.assertRaisesRegex(RuntimeError, "cancelled"): self.export()
        self.assert_old(); self.assert_clean()

    def test_cancellation_after_validation_preserves_old(self):
        validate = engine._validate_png_sequence
        def cancel(*args):
            result = validate(*args); self.holder["cancelled"] = True; return result
        with patch.object(engine, "_run_ffmpeg", side_effect=lambda cmd, **kw: self.frames(cmd)), \
                patch.object(engine, "_validate_png_sequence", side_effect=cancel):
            with self.assertRaisesRegex(RuntimeError, "cancelled"): self.export()
        self.assert_old(); self.assert_clean()

    def test_build_failure_cleans_stage_and_releases_destination(self):
        with patch.object(engine, "build_command", side_effect=RuntimeError("build failed")):
            with self.assertRaisesRegex(RuntimeError, "build failed"): self.export()
        self.assert_old(); self.assert_clean()
        with patch.object(engine, "_run_ffmpeg", side_effect=lambda cmd, **kw: self.frames(cmd)): self.export()
        self.assert_clean()

    def test_invalid_success_outputs_are_never_published(self):
        for mode in ("empty", "truncated", "gap", "extra", "corrupt", "wrong_size"):
            with self.subTest(mode=mode):
                def bad(cmd, **kwargs):
                    stage = self.frames(cmd, count=0 if mode == "empty" else 11 if mode == "truncated" else 12,
                                        size=(32, 48) if mode == "wrong_size" else (64, 48))
                    if mode == "gap":
                        (stage / "frame_00005.png").rename(stage / "frame_00013.png")
                    if mode == "extra": (stage / "unrelated.txt").write_bytes(b"unexpected")
                    if mode == "corrupt": (stage / "frame_00006.png").write_bytes(b"not a PNG")
                with patch.object(engine, "_run_ffmpeg", side_effect=bad):
                    with self.assertRaises((RuntimeError, OSError)): self.export()
                self.assert_old(); self.assert_clean()

    def test_success_replaces_whole_generation_and_removes_old_tail(self):
        progress = []
        with patch.object(engine, "_run_ffmpeg", side_effect=lambda cmd, **kw: self.frames(cmd)):
            result = self.export(progress=progress.append)
        self.assertEqual(result, str(self.destination))
        manifest = json.loads((self.destination / "sequence.json").read_text())
        self.assertEqual(manifest["frame_count"], 12)
        self.assertEqual(len(self.snapshot()), 13)
        self.assertFalse((self.destination / "frame_00013.png").exists())
        for frame in manifest["frames"]:
            data = (self.destination / frame["file"]).read_bytes()
            self.assertEqual(frame["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(frame["size"], len(data))
        self.assertTrue(self.holder["png_published"])
        self.assertEqual(self.holder["sequence_qa"]["status"], "checked")
        self.assertEqual(progress[-1], 1.0); self.assert_clean()

    def test_success_without_prior_destination(self):
        shutil.rmtree(self.destination)
        with patch.object(engine, "_run_ffmpeg", side_effect=lambda cmd, **kw: self.frames(cmd)): self.export()
        self.assertEqual(json.loads((self.destination / "sequence.json").read_text())["frame_count"], 12)
        self.assert_clean()

    def test_legacy_loose_frames_and_neighbor_exports_are_untouched(self):
        legacy = self.root / "Émile's export_00001.png"; legacy.write_bytes(b"legacy")
        neighbor = self.root / "another.mp4"; neighbor.write_bytes(b"other export")
        with patch.object(engine, "_run_ffmpeg", side_effect=lambda cmd, **kw: self.frames(cmd)): self.export()
        self.assertEqual(legacy.read_bytes(), b"legacy"); self.assertEqual(neighbor.read_bytes(), b"other export")
        self.assert_clean()

    def test_publish_rename_failure_rolls_back(self):
        replace = engine.os.replace
        def fail_stage(src, dst):
            if ".stage-" in str(src) and not str(src).endswith(".previous"):
                raise OSError("injected publication failure")
            return replace(src, dst)
        with patch.object(engine, "_run_ffmpeg", side_effect=lambda cmd, **kw: self.frames(cmd)), \
                patch.object(engine.os, "replace", side_effect=fail_stage):
            with self.assertRaisesRegex(OSError, "injected publication"): self.export()
        self.assert_old(); self.assert_clean()

    def test_old_directory_rename_failure_preserves_old(self):
        with patch.object(engine, "_run_ffmpeg", side_effect=lambda cmd, **kw: self.frames(cmd)), \
                patch.object(engine.os, "replace", side_effect=PermissionError("old directory locked")):
            with self.assertRaisesRegex(PermissionError, "locked"): self.export()
        self.assert_old(); self.assert_clean()

    def test_cancel_between_renames_rolls_back(self):
        replace = engine.os.replace
        def cancel_after_backup(src, dst):
            result = replace(src, dst)
            if str(dst).endswith(".previous"): self.holder["cancelled"] = True
            return result
        with patch.object(engine, "_run_ffmpeg", side_effect=lambda cmd, **kw: self.frames(cmd)), \
                patch.object(engine.os, "replace", side_effect=cancel_after_backup):
            with self.assertRaisesRegex(RuntimeError, "cancelled"): self.export()
        self.assert_old(); self.assert_clean()

    def test_failed_rollback_retains_recovery_copy_and_reports_path(self):
        replace = engine.os.replace
        def fail(src, dst):
            if Path(src) == self.destination: return replace(src, dst)
            raise PermissionError("injected blocked publication and rollback")
        with patch.object(engine, "_run_ffmpeg", side_effect=lambda cmd, **kw: self.frames(cmd)), \
                patch.object(engine.os, "replace", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "rollback failed; previous sequence retained at") as raised: self.export()
        backups = list(self.root.glob(".*.previous")); self.assertEqual(len(backups), 1)
        self.assertEqual({p.name: p.read_bytes() for p in backups[0].iterdir()}, self.old)
        self.assertIn(str(backups[0]), str(raised.exception))
        self.assertEqual(list(self.root.glob(".*.stage-*")), backups)
        self.assertFalse(list(self.root.glob("*.lock")))

    def test_cleanup_failure_after_commit_is_visible_warning(self):
        remove = engine.rmtree
        def fail_backup(path):
            if str(path).endswith(".previous"): raise PermissionError("backup is locked")
            return remove(path)
        with patch.object(engine, "_run_ffmpeg", side_effect=lambda cmd, **kw: self.frames(cmd)), \
                patch.object(engine, "rmtree", side_effect=fail_backup): self.export()
        qa = self.holder["sequence_qa"]
        self.assertEqual(qa["status"], "warnings"); self.assertIn("retained", qa["flags"][0])
        self.assertEqual(len(list(self.root.glob(".*.previous"))), 1)
        self.assertFalse(list(self.root.glob("*.lock")))

    def test_stage_cleanup_failure_is_visible_and_releases_lock(self):
        with patch.object(engine, "_run_ffmpeg", side_effect=RuntimeError("encoder failed")), \
                patch.object(engine, "rmtree", side_effect=PermissionError("stage cleanup blocked")):
            with self.assertRaisesRegex(PermissionError, "stage cleanup blocked"): self.export()
        self.assert_old(); self.assertFalse(list(self.root.glob("*.lock")))
        self.assertEqual(len(list(self.root.glob(".*.stage-*"))), 1)

    def test_file_collision_never_spawns_or_removes_target(self):
        shutil.rmtree(self.destination); self.destination.write_bytes(b"unrelated file")
        with patch.object(engine, "_run_ffmpeg") as run:
            with self.assertRaisesRegex(RuntimeError, "not a regular directory"): self.export()
            run.assert_not_called()
        self.assertEqual(self.destination.read_bytes(), b"unrelated file"); self.assert_clean()

    def test_directory_appearing_during_render_is_preserved(self):
        shutil.rmtree(self.destination)
        def collision(cmd, **kwargs):
            self.frames(cmd)
            self.destination.mkdir(); (self.destination / "foreign").write_bytes(b"external writer")
        with patch.object(engine, "_run_ffmpeg", side_effect=collision):
            with self.assertRaisesRegex(RuntimeError, "destination changed"): self.export()
        self.assertEqual(self.snapshot(), {"foreign": b"external writer"}); self.assert_clean()

    def test_replaced_destination_during_render_is_preserved(self):
        moved = self.root / "external old"
        def collision(cmd, **kwargs):
            self.frames(cmd)
            self.destination.rename(moved)
            self.destination.mkdir(); (self.destination / "foreign").write_bytes(b"external replacement")
        with patch.object(engine, "_run_ffmpeg", side_effect=collision):
            with self.assertRaisesRegex(RuntimeError, "destination changed"): self.export()
        self.assertEqual(self.snapshot(), {"foreign": b"external replacement"})
        self.assertEqual({p.name: p.read_bytes() for p in moved.iterdir()}, self.old); self.assert_clean()

    def test_incremental_entry_point_uses_same_png_transaction(self):
        with patch.object(engine, "_run_ffmpeg", side_effect=RuntimeError("incremental fallback failure")):
            with self.assertRaisesRegex(RuntimeError, "fallback failure"):
                engine.render_incremental(self.p, "s", str(self.out), self.preset, proc_holder=self.holder)
        self.assert_old(); self.assert_clean()
        with patch.object(engine, "_run_ffmpeg", side_effect=lambda cmd, **kw: self.frames(cmd)):
            output, stats = engine.render_incremental(self.p, "s", str(self.out), self.preset, proc_holder=self.holder)
        self.assertEqual(output, str(self.destination)); self.assertEqual(stats["mode"], "full"); self.assert_clean()

    def test_foreign_lock_is_never_removed_or_bypassed(self):
        lock = Path(str(self.destination) + ".lock"); lock.write_bytes(b"foreign owner")
        with patch.object(engine, "_run_ffmpeg") as run:
            with self.assertRaisesRegex(RuntimeError, "busy"): self.export()
            run.assert_not_called()
        self.assertEqual(lock.read_bytes(), b"foreign owner"); self.assert_old()
        lock.unlink(); self.assert_clean()

    def test_cross_process_lock_rejects_overlapping_publication(self):
        ready = self.root / "ready"; release = self.root / "release"
        script = """import os, pathlib, sys, time
fd = os.open(sys.argv[1], os.O_CREAT | os.O_EXCL | os.O_WRONLY)
pathlib.Path(sys.argv[2]).write_text('ready')
try:
    until = time.monotonic() + 10
    while not pathlib.Path(sys.argv[3]).exists() and time.monotonic() < until: time.sleep(.01)
finally:
    os.close(fd); os.unlink(sys.argv[1])
"""
        # Waiting/killing a Windows venv redirector does not retire its real
        # lock-holder child. This fixture must own that holder directly.
        interpreter = sys._base_executable if os.name == "nt" else sys.executable
        child = subprocess.Popen([interpreter, "-c", script, str(self.destination) + ".lock", str(ready), str(release)],
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            for _ in range(500):
                if ready.exists(): break
                threading.Event().wait(.01)
            self.assertTrue(ready.exists(), "lock owner child failed to start")
            with self.assertRaisesRegex(RuntimeError, "busy"): self.export()
            self.assert_old()
        finally:
            release.write_text("release")
            try: child.wait(timeout=10)
            except subprocess.TimeoutExpired: child.kill(); child.wait(); raise
        self.assertEqual(child.returncode, 0); self.assert_clean()

    def test_concurrent_engine_jobs_cannot_interleave(self):
        entered = threading.Event(); release = threading.Event(); failures = []
        def encoder(cmd, **kwargs):
            self.frames(cmd, count=2); entered.set()
            if not release.wait(5): raise RuntimeError("test release timed out")
            self.frames(cmd)
        def first():
            try: self.export()
            except BaseException as exc: failures.append(exc)
        with patch.object(engine, "_run_ffmpeg", side_effect=encoder):
            thread = threading.Thread(target=first); thread.start()
            try:
                self.assertTrue(entered.wait(5)); self.assert_old()
                with self.assertRaisesRegex(RuntimeError, "busy"): self.export(proc_holder={})
                self.assert_old()
            finally: release.set(); thread.join(timeout=5)
        self.assertFalse(thread.is_alive()); self.assertEqual(failures, [])
        self.assertEqual(self.holder["sequence_qa"]["frame_count"], 12); self.assert_clean()

    def test_different_destinations_can_render_concurrently(self):
        rendezvous = threading.Barrier(2); failures = []; outputs = []
        def encoder(cmd, **kwargs):
            rendezvous.wait(timeout=5); self.frames(cmd)
        def export_to(name):
            try: outputs.append(engine.render(self.p, "s", str(self.root / (name + ".png")), self.preset))
            except BaseException as exc: failures.append(exc)
        with patch.object(engine, "_run_ffmpeg", side_effect=encoder):
            threads = [threading.Thread(target=export_to, args=(name,)) for name in ("one", "two")]
            for thread in threads: thread.start()
            for thread in threads: thread.join(timeout=6)
        self.assertFalse(any(t.is_alive() for t in threads)); self.assertEqual(failures, [])
        self.assertEqual(len(outputs), 2)
        for path in outputs: self.assertEqual(json.loads((Path(path) / "sequence.json").read_text())["frame_count"], 12)
        self.assert_old(); self.assert_clean()

    def run_queue(self, failure=None):
        data = self.root / "api-data"; data.mkdir(exist_ok=True)
        jobs = {}; render_queue = OneJobQueue(); holders = {}
        patches = [patch.object(server, "ROOT", str(data)), patch.object(server, "JOBS", jobs),
                   patch.object(server, "RENDER_Q", render_queue), patch.object(server, "RENDER_PROCS", holders),
                   patch.object(server, "render_workers"), patch.object(server, "log_event")]
        from contextlib import ExitStack
        with ExitStack() as stack:
            for item in patches: stack.enter_context(item)
            if failure is not None: stack.enter_context(patch.object(engine, "_run_ffmpeg", side_effect=failure))
            # Exercise the actual async render route, name handling, queue worker and status route.
            class Request:
                async def json(inner): return {"sequence": "s", "name": "PNG test", "preset": self.preset}
            stack.enter_context(patch.object(server, "load_project", return_value=copy.deepcopy(self.p)))
            response = asyncio.run(server.render_job(Request()))
            job = jobs[response["id"]]
            target = data / job["frames"]["directory"].lstrip("/")
            target.mkdir(); (target / "old").write_bytes(b"old API sequence")
            with self.assertRaises(StopWorker): server._render_worker()
            self.assertEqual(render_queue.unfinished_tasks, 0); self.assertEqual(holders, {})
            self.assertEqual(server.render_status(job["id"]), job)
            return job, target

    def test_real_ffmpeg_route_publishes_working_manifest_and_qa(self):
        job, target = self.run_queue()
        self.assertEqual(job["status"], "done", job.get("error"))
        self.assertEqual(job["out"], f"/renders/job-{job['id']}/PNG_test.frames/sequence.json")
        self.assertEqual(job["frames"]["first_frame"], f"/renders/job-{job['id']}/PNG_test.frames/frame_00001.png")
        self.assertEqual(job["qa"]["status"], "checked")
        self.assertEqual(job["qa"]["frame_count"], 12)
        self.assertTrue((target / "sequence.json").exists()); self.assertFalse((target / "old").exists())

    def test_route_encoder_failure_preserves_old_directory_and_reports_error(self):
        def fail(cmd, **kwargs): self.frames(cmd, 2); raise RuntimeError("route encoder failed")
        job, target = self.run_queue(fail)
        self.assertEqual(job["status"], "error"); self.assertIn("route encoder failed", job["error"])
        self.assertEqual((target / "old").read_bytes(), b"old API sequence")
        self.assertEqual(len(list(target.iterdir())), 1)
        self.assertFalse(list(target.parent.glob(".*.stage-*"))); self.assertFalse(list(target.parent.glob("*.lock")))

    def test_api_cancellation_during_validation_preserves_old(self):
        validate = engine._validate_png_sequence
        jid = "png-cancel"; job = {"status": "running", "preset": self.preset}
        def cancel(*args):
            self.assertNotIn("proc", self.holder)
            self.assertEqual(asyncio.run(server.render_cancel(jid)), {"ok": True})
            return validate(*args)
        with patch.object(server, "JOBS", {jid: job}), patch.object(server, "RENDER_PROCS", {jid: self.holder}), \
                patch.object(engine, "_run_ffmpeg", side_effect=lambda cmd, **kw: self.frames(cmd)), \
                patch.object(engine, "_validate_png_sequence", side_effect=cancel):
            with self.assertRaisesRegex(RuntimeError, "cancelled"): self.export()
        self.assertEqual(job["status"], "cancelling"); self.assert_old(); self.assert_clean()

    def test_api_cancel_after_commit_returns_false(self):
        with patch.object(engine, "_run_ffmpeg", side_effect=lambda cmd, **kw: self.frames(cmd)): self.export()
        job = {"status": "running", "preset": self.preset}
        with patch.object(server, "JOBS", {"done": job}), patch.object(server, "RENDER_PROCS", {"done": self.holder}):
            self.assertEqual(asyncio.run(server.render_cancel("done")), {"ok": False})
        self.assertNotIn("cancelled", self.holder); self.assertEqual(job["status"], "running"); self.assert_clean()

    def test_real_ffmpeg_full_and_range_exports_decode_complete_frames(self):
        self.p = project(duration=1)
        for ranged, fps, expected in ((False, 24, 24), (True, 24, 6), (False, 30000 / 1001, 30)):
            with self.subTest(range=ranged, fps=fps):
                self.p["sequences"][0].update(fps=fps, in_point=.25, out_point=.5)
                self.preset["range"] = ranged; self.holder = {}
                self.export()
                manifest = json.loads((self.destination / "sequence.json").read_text())
                self.assertEqual(manifest["frame_count"], expected)
                decoded = subprocess.run(["ffmpeg", "-v", "error", "-framerate", str(fps), "-i",
                                          str(self.destination / "frame_%05d.png"), "-f", "rawvideo",
                                          "-pix_fmt", "rgb24", "pipe:1"], capture_output=True, check=True).stdout
                self.assertEqual(len(decoded), expected * 64 * 48 * 3)
                self.assert_clean()

    def test_real_ffmpeg_derived_dimensions_are_validated_and_published(self):
        self.preset.update(out_w=32, out_h=64, fit="pad")
        self.export()
        manifest = json.loads((self.destination / "sequence.json").read_text())
        self.assertEqual((manifest["width"], manifest["height"]), (32, 64))
        self.assertEqual((self.holder["sequence_qa"]["width"], self.holder["sequence_qa"]["height"]), (32, 64))
        for frame in manifest["frames"]:
            with Image.open(self.destination / frame["file"]) as image: self.assertEqual(image.size, (32, 64))
        self.assert_clean()

    def test_real_ffmpeg_non_frame_aligned_duration_and_range(self):
        # Output -t uses FFmpeg's nearest CFR-frame boundary, after millisecond rounding.
        for duration, ranged, start, end, expected in ((.101, False, 0, 0, 2),
                (1, True, .013, .201, 5), (1, True, .117, .426, 7)):
            with self.subTest(duration=duration, range=ranged, start=start, end=end):
                self.p = project(duration=duration)
                self.p["sequences"][0].update(in_point=start, out_point=end)
                self.preset["range"] = ranged; self.holder = {}
                self.export()
                self.assertEqual(self.holder["sequence_qa"]["frame_count"], expected); self.assert_clean()

    def test_real_ffmpeg_cancellation_during_process_preserves_old_and_cleans(self):
        self.p = project(duration=120)
        source = self.root / "paced source.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                        "testsrc2=s=64x48:r=24:d=0.5", "-c:v", "libx264", str(source)], check=True)
        self.p["media"]["a"] = {"id": "a", "path": str(source), "has_video": True,
                                "has_audio": False}
        self.p["sequences"][0]["tracks"] = [{"id": "V1", "kind": "video", "index": 1,
                                           "clips": [{"id": "c", "media_id": "a", "start": 0, "in_": 0, "out": 120}]}]
        job = {"status": "running", "preset": self.preset}
        def cancel(fraction):
            if not self.holder.get("cancelled"):
                self.assertIn("proc", self.holder); self.assert_old()
                self.assertEqual(asyncio.run(server.render_cancel("real-cancel")), {"ok": True})
        run = engine._run_ffmpeg
        def paced(cmd, **kwargs):
            # A finite half-second source is paced by the controlled process
            # adapter, without exposing arbitrary argv in project media.
            cmd = cmd.copy(); index = cmd.index('-i')
            cmd[index:index] = ['-readrate', '1']
            return run(cmd, **kwargs)
        with patch.object(server, "JOBS", {"real-cancel": job}), \
                patch.object(server, "RENDER_PROCS", {"real-cancel": self.holder}), \
                patch.object(engine, "_run_ffmpeg", side_effect=paced):
            with self.assertRaisesRegex(RuntimeError, "cancelled"): self.export(progress=cancel)
        self.assertEqual(job["status"], "cancelling")
        self.assert_old(); self.assert_clean()

    def test_real_ffmpeg_failure_after_writing_frames_preserves_old(self):
        build = engine.build_command
        observed = []
        def blocked_third_frame(*args, **kwargs):
            cmd, graph = build(*args, **kwargs)
            stage = Path(cmd[-1]).parent; observed.append(stage)
            (stage / "frame_00003.png").mkdir()
            return cmd, graph
        run = engine._run_ffmpeg
        def watch_failure(cmd, **kwargs):
            try: return run(cmd, **kwargs)
            finally:
                observed.append((observed[0] / "frame_00001.png").is_file())
                observed.append((observed[0] / "frame_00002.png").is_file())
        with patch.object(engine, "build_command", side_effect=blocked_third_frame), \
                patch.object(engine, "_run_ffmpeg", side_effect=watch_failure):
            with self.assertRaises(RuntimeError): self.export()
        self.assertEqual(observed[1:], [True, True]); self.assert_old(); self.assert_clean()


@unittest.skipUnless(os.name == 'nt', 'Actual Windows deep filesystem behavior')
class LongWindowsPngPublication(unittest.TestCase):
    """Real long paths and FFmpeg; no mocked encoder or shortened destination."""
    @staticmethod
    def native(path):
        value=os.path.abspath(path)
        return value if value.startswith('\\\\?\\') else '\\\\?\\'+value

    def setUp(self):
        self.case=SCRATCH/('deep-'+uuid.uuid4().hex);self.case.mkdir()
        self.deep=self.case
        while len(str(self.deep).encode('utf-16-le'))//2 < 330:
            self.deep/= "Émile's nested folder-"+'a'*34
        Path(self.native(self.deep)).mkdir(parents=True)
        self.out=self.deep/"Émile's retained output.png"
        self.destination=Path(self.native(engine.png_sequence_directory(self.out)))
        self.destination.mkdir()
        (self.destination/'frame_00001.png').write_bytes(b'approved old frame')
        (self.destination/'frame_00013.png').write_bytes(b'approved old tail')
        (self.destination/'sequence.json').write_bytes(b'approved old manifest')
        self.old={path.name:path.read_bytes() for path in self.destination.iterdir()}
        self.original=self.case/"Émile's original.png"
        Image.new('RGB',(64,48),(25,80,160)).save(self.original)
        self.original_bytes=self.original.read_bytes()
        self.p=project()
        self.p['media']={'original':{'id':'original','path':str(self.original),'type':'image',
            'width':64,'height':48,'duration':.5,'fps':24,'has_video':True,'has_audio':False}}
        self.p['sequences'][0]['tracks']=[{'id':'V1','kind':'video','index':1,'clips':[
            {'id':'one','media':'original','start':0,'in_':0,'out':.5,'speed':1,'transform':{}}]}]
        self.holder={}
        self.evidence={'ordinary_output':str(self.out),'ordinary_output_utf16_units':len(str(self.out).encode('utf-16-le'))//2,
            'maximum_component_units':max(len(part.encode('utf-16-le'))//2 for part in self.out.parts),
            'original_sha256':hashlib.sha256(self.original_bytes).hexdigest(),'encoder_mocked':False}
        self.assertGreater(self.evidence['ordinary_output_utf16_units'],320)
        self.assertLess(self.evidence['maximum_component_units'],100)

    def tearDown(self):
        self.evidence.update(original_unchanged=self.original.read_bytes()==self.original_bytes,
            owned_encoder_retired='proc' not in self.holder,
            remaining_stage_or_lock=[path.name for path in Path(self.native(self.deep)).iterdir()
                if '.stage-' in path.name or path.name.endswith('.lock')])
        (self.case/'deep-evidence.json').write_text(json.dumps(self.evidence,indent=2),encoding='utf-8')
        # Retain tiny output/source/evidence fixtures for independent read-back.

    def export(self):
        return engine.render(self.p,'s',str(self.out),{'format':'png_sequence'},proc_holder=self.holder)

    def assert_clean(self):
        self.assertEqual(self.original.read_bytes(),self.original_bytes)
        self.assertNotIn('proc',self.holder)
        self.assertFalse([path for path in Path(self.native(self.deep)).iterdir()
            if '.stage-' in path.name or path.name.endswith('.lock')])

    def test_real_deep_publication_decodes_frames_replaces_tail_and_preserves_original(self):
        result=self.export();self.evidence['result']=result
        self.assertEqual(result,engine.png_sequence_directory(str(self.out)))
        self.assertFalse(result.startswith('\\\\?\\'))
        manifest=json.loads((self.destination/'sequence.json').read_bytes())
        self.assertEqual(manifest['frame_count'],12)
        self.assertEqual(len(list(self.destination.iterdir())),13)
        self.assertFalse((self.destination/'frame_00013.png').exists())
        for frame in manifest['frames']:
            path=self.destination/frame['file'];raw=path.read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),frame['sha256'])
            with Image.open(path) as image:
                image.load();self.assertEqual(image.size,(64,48));self.assertEqual(image.format,'PNG')
        self.evidence['decoded_frames']=12
        self.assertEqual(self.holder['sequence_qa']['status'],'checked')
        self.assert_clean()

    def test_real_deep_validation_cancellation_preserves_old_generation_and_retires(self):
        validate=engine._validate_png_sequence
        def cancel_after_real_decode(*args):
            qa=validate(*args);self.evidence['decoded_before_cancel']=qa['frame_count']
            self.holder['cancelled']=True;return qa
        with patch.object(engine,'_validate_png_sequence',side_effect=cancel_after_real_decode):
            with self.assertRaisesRegex(RuntimeError,'cancelled'):self.export()
        self.assertEqual({path.name:path.read_bytes() for path in self.destination.iterdir()},self.old)
        self.assertEqual(self.evidence['decoded_before_cancel'],12)
        self.assertFalse(self.holder.get('png_published',False));self.assert_clean()

    def test_deep_reservation_preserves_public_url_and_neighbor_bytes(self):
        from export_storage import reserve_export,output_path
        root=self.deep/'renders'
        Path(self.native(root)).mkdir()
        neighbor=Path(self.native(root))/'keep.mp4';neighbor.write_bytes(b'prior neighbor')
        result=reserve_export(root,'Émile export',{'format':'png_sequence'})
        self.assertTrue(Path(self.native(result['directory'])).is_dir())
        self.assertEqual(result['url'],'/renders/job-'+result['id']+'/Émile_export.png')
        self.assertEqual(output_path(root,result['url']),self.native(result['path']))
        self.assertFalse(result['path'].startswith('\\\\?\\'))
        self.assertEqual(neighbor.read_bytes(),b'prior neighbor')
        self.evidence['reservation']=result

    def test_stage_below_prefix_threshold_cleans_long_backup_children(self):
        # The stage is 239 UTF-16 units; adding '.previous' makes its old
        # frame children exceed the ordinary Win32 limit even though staging
        # and the new destination are both below the adapter threshold.
        parent=self.case
        while len(str(parent))<187:
            parent/='a'*min(60,187-len(str(parent))-1)
        Path(self.native(parent)).mkdir(parents=True,exist_ok=True)
        out=parent/'edge.png'
        destination=Path(self.native(engine.png_sequence_directory(out)))
        destination.mkdir()
        old_frame=destination/'frame_00013.png';old_frame.write_bytes(b'old retained tail')
        (destination/'sequence.json').write_bytes(b'old retained manifest')
        stages=[];run=engine._run_ffmpeg
        def record_stage(cmd,**kwargs):
            stages.append(str(Path(cmd[-1]).parent).removeprefix('\\\\?\\'))
            return run(cmd,**kwargs)
        with patch.object(engine,'_run_ffmpeg',side_effect=record_stage):
            result=engine.render(self.p,'s',str(out),{'format':'png_sequence'},proc_holder=self.holder)
        stage=stages[0];backup=stage+'.previous'
        self.assertEqual(len(stage.encode('utf-16-le'))//2,239)
        self.assertEqual(len(backup.encode('utf-16-le'))//2,248)
        self.assertGreater(len((backup+'\\frame_00013.png').encode('utf-16-le'))//2,260)
        self.assertEqual(result,engine.png_sequence_directory(str(out)))
        self.assertEqual(self.holder['sequence_qa']['status'],'checked')
        manifest=json.loads((destination/'sequence.json').read_bytes())
        self.assertEqual(manifest['frame_count'],12)
        self.assertFalse(old_frame.exists())
        for frame in manifest['frames']:
            with Image.open(destination/frame['file']) as image:
                image.load();self.assertEqual(image.size,(64,48))
        self.assertFalse([path.name for path in Path(self.native(parent)).iterdir()
            if '.stage-' in path.name or path.name.endswith('.lock')])
        self.evidence['backup_threshold']={'stage_utf16_units':239,'backup_utf16_units':248,
            'old_frame_utf16_units':len((backup+'\\frame_00013.png').encode('utf-16-le'))//2,
            'decoded_frames':12,'previous_generation_removed':True}
        self.assert_clean()

    def test_real_deep_h264_publication_decodes_and_keeps_plain_return(self):
        out=self.deep/"Émile's single video.mp4"
        result=engine.render(self.p,'s',str(out),{'format':'h264','vcodec':'libx264','loudnorm':False},proc_holder=self.holder)
        self.assertEqual(result,str(out));self.assertFalse(result.startswith('\\\\?\\'))
        probe=subprocess.run(['ffprobe','-v','error','-count_frames','-show_streams','-of','json',self.native(out)],
            capture_output=True,check=True,timeout=15)
        video=next(stream for stream in json.loads(probe.stdout)['streams'] if stream['codec_type']=='video')
        self.assertEqual((video['codec_name'],int(video['nb_read_frames'])),('h264',12))
        decoded=subprocess.run(['ffmpeg','-v','error','-i',self.native(out),'-map','0:v:0','-f','rawvideo','-pix_fmt','rgb24','pipe:1'],
            capture_output=True,check=True,timeout=15)
        self.assertEqual(len(decoded.stdout),12*64*48*3)
        self.assertFalse([path.name for path in Path(self.native(self.deep)).iterdir() if '.part.' in path.name])
        self.evidence['single_h264']={'public_return':result,'decoded_frames':12,
            'output_sha256':hashlib.sha256(Path(self.native(out)).read_bytes()).hexdigest()}
        self.assert_clean()

    def test_real_deep_h264_cancellation_removes_stage_and_preserves_master(self):
        out=self.deep/"Émile's prior master.mp4";master=Path(self.native(out))
        master.write_bytes(b'approved prior master');run=engine._run_ffmpeg
        def cancel_after_encoder(cmd,**kwargs):
            result=run(cmd,**kwargs);self.holder['cancelled']=True;return result
        with patch.object(engine,'_run_ffmpeg',side_effect=cancel_after_encoder):
            with self.assertRaisesRegex(RuntimeError,'cancelled'):
                engine.render(self.p,'s',str(out),{'format':'h264','vcodec':'libx264','loudnorm':False},proc_holder=self.holder)
        self.assertEqual(master.read_bytes(),b'approved prior master')
        self.assertFalse(self.holder.get('published',False))
        self.assertFalse([path.name for path in Path(self.native(self.deep)).iterdir() if '.part.' in path.name])
        self.evidence['single_h264_cancel']={'prior_master_preserved':True,'stage_removed':True}
        self.assert_clean()

    def test_real_deep_frame_publication_decodes_and_preserves_original(self):
        out=self.deep/"Émile's single frame.png"
        engine.render_frame(self.p,'s',.1,str(out),proc_holder=self.holder)
        with Image.open(self.native(out)) as image:
            image.load();self.assertEqual((image.format,image.size),('PNG',(64,48)))
        self.assertFalse([path.name for path in Path(self.native(self.deep)).iterdir() if '.part.' in path.name])
        self.evidence['single_frame']={'output':str(out),'decoded':True,'stage_removed':True}
        self.assert_clean()

    def test_deep_receipt_restore_and_http_manifest_frame_download_keep_containment(self):
        import httpx
        import io
        import job_history as history
        from project_sync import workspace_id
        from export_storage import reserve_export,output_path,filesystem_path,export_static_files
        from starlette.applications import Starlette
        from starlette.routing import Mount
        data=self.case
        while len(str(data))<200:
            remaining=200-len(str(data))-1
            data/= 'a'*min(60,remaining)
        Path(self.native(data)).mkdir(parents=True,exist_ok=True)
        dest=reserve_export(data/'renders','Émile_export_'+'x'*80,{'format':'png_sequence'})
        directory=dest['url'].rsplit('.',1)[0]+'.frames'
        job={'id':dest['id'],'name':dest['name'],'status':'queued','started':1.0,'sequence':'s',
            'preset':{'format':'png_sequence'},'out':directory+'/sequence.json',
            'command_log':dest['url'].rsplit('.',1)[0]+'.cmd.txt','review_url':'/review/job-'+dest['id'],
            'output_kind':'png_sequence','frames':{'directory':directory,'first_frame':directory+'/frame_00001.png'},
            'context':{'workspace':workspace_id(data),'project':'default','revision':'captured'}}
        history.save(data,job)
        with open(output_path(data/'renders',job['command_log']),'x',encoding='utf-8') as log:
            result=engine.render(self.p,'s',dest['path'],job['preset'],log=log,proc_holder=self.holder)
        manifest_path=Path(output_path(data/'renders',job['out']))
        self.assertGreater(len(str(manifest_path).removeprefix('\\\\?\\')),320)
        job.update(status='done',finished=2.0,qa=self.holder['sequence_qa'])
        history.save(data,job)
        restored=history.restore(data)[job['id']]
        self.assertEqual(restored['status'],'done');self.assertEqual(restored['out'],job['out'])
        self.assertEqual(restored['output_check'],'metadata_only')
        self.assertFalse(restored['out'].startswith('\\\\?\\'))
        before_manifest=manifest_path.read_bytes();primary=history._folder(data,job['id'])/history.PRIMARY
        before_receipt=primary.read_bytes()
        secret=Path(filesystem_path(data/'private.json'));secret.write_bytes(b'private boundary fixture')
        outside=self.case/'outside-export';outside.mkdir();(outside/'private.txt').write_bytes(b'outside junction fixture')
        link=data/'renders'/'escape'
        linked=subprocess.run(['cmd.exe','/c','mklink','/J',str(link),str(outside)],capture_output=True,timeout=10)
        self.assertEqual(linked.returncode,0,linked.stdout+linked.stderr)
        self.assertTrue(Path(filesystem_path(link)).is_junction())
        app=Starlette(routes=[Mount('/renders',app=export_static_files(data/'renders'))])
        async def download():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as client:
                manifest=await client.get(restored['out']);self.assertEqual(manifest.status_code,200)
                self.assertEqual(manifest.content,before_manifest)
                frame=await client.get(restored['frames']['first_frame']);self.assertEqual(frame.status_code,200)
                with Image.open(io.BytesIO(frame.content)) as image:image.load();self.assertEqual(image.size,(64,48))
                self.assertEqual((await client.get('/renders/%2e%2e/private.json')).status_code,404)
                self.assertEqual((await client.get('/renders/escape/private.txt')).status_code,404)
                return {'manifest_sha256':hashlib.sha256(manifest.content).hexdigest(),
                    'frame_sha256':hashlib.sha256(frame.content).hexdigest(),'traversal_refused':True,'actual_junction_refused':True}
        try:self.evidence['download']=asyncio.run(download())
        finally:
            self.assertTrue(link.resolve().is_relative_to(self.case.resolve()))
            os.rmdir(filesystem_path(link))  # Only the owned junction entry, not its retained target.
        self.assertEqual(primary.read_bytes(),before_receipt)
        self.assertEqual(manifest_path.read_bytes(),before_manifest)
        self.assertEqual(secret.read_bytes(),b'private boundary fixture')
        self.assertEqual((outside/'private.txt').read_bytes(),b'outside junction fixture')
        self.assertFalse(result.startswith('\\\\?\\'));self.assert_clean()


if __name__ == "__main__": unittest.main(verbosity=2)

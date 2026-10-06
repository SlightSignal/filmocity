"""Bounded production-worker failure injection; no app, listener or user library.

Run with the existing Python environment. Fixtures inherit this source folder's
ACLs (no mode-0700 temporary directories). Retain job/output evidence per run.
The queue counts acknowledgments while still calling Queue.task_done itself.
"""
import asyncio
import copy
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / ".render-queue-lifecycle-tests"
SCRATCH.mkdir(exist_ok=True)
RUN = SCRATCH / ("run-" + uuid.uuid4().hex)
RUN.mkdir()
os.environ.update(TEMP=str(RUN), TMP=str(RUN), FILMOCITY_ROOT=str(RUN / "data"))
tempfile.tempdir = str(RUN)
sys.path.insert(0, str(ROOT / "backend"))


def inherited_scratch(prefix="tmp", suffix="", dir=None):
    path = Path(dir or RUN) / (prefix + uuid.uuid4().hex + suffix)
    path.mkdir()
    return str(path)


with patch.object(tempfile, "mkdtemp", side_effect=inherited_scratch):
    import server
import render as engine
from PIL import Image


def project():
    return {"version": 3, "id": "queue-fixture", "name": "Queue fixture", "media": {},
            "sequences": [{"id": "s", "name": "Synthetic", "width": 64, "height": 48,
                           "fps": 24, "duration": .5, "tracks": [], "captions": []}]}


class StopWorker(Exception):
    """Bound the infinite production loop at its next empty dequeue."""


class BoundedQueue(queue.Queue):
    def __init__(self):
        super().__init__()
        self.acknowledgments = 0
        self.dequeues = 0

    def get(self, *args, **kwargs):
        if self.empty():
            raise StopWorker()
        item = super().get(*args, **kwargs)
        self.dequeues += 1
        return item

    def task_done(self):
        super().task_done()
        self.acknowledgments += 1


class FailOnceJob(dict):
    def update(self, *args, **kwargs):
        if not getattr(self, "failed", False):
            self.failed = True
            raise RuntimeError("injected job bookkeeping failure")
        return super().update(*args, **kwargs)


class RenderQueueLifecycle(unittest.TestCase):
    def setUp(self):
        self.root = RUN / hashlib.sha256(self._testMethodName.encode()).hexdigest()[:12]
        self.root.mkdir()
        self.jobs = {}
        self.holders = {}
        self.q = BoundedQueue()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for target, value in (("ROOT", str(self.root)), ("JOBS", self.jobs),
                              ("RENDER_Q", self.q), ("RENDER_PROCS", self.holders)):
            self.stack.enter_context(patch.object(server, target, value))
        self.stack.enter_context(patch.object(server, "render_workers"))
        # Watchdogs are outside this lifecycle test; do not leave sleeping threads.
        self.stack.enter_context(patch.object(server.threading, "Thread"))
        self.render = self.stack.enter_context(patch.object(server, "do_render", side_effect=self.fake_render))
        self.qa = {"status": "checked", "sha256": "fixture-hash", "width": 64, "flags": []}
        self.verify = self.stack.enter_context(patch.object(server, "render_qa", side_effect=lambda *a, **kw: copy.deepcopy(self.qa)))
        self.addCleanup(self.save_evidence)

    def save_evidence(self):
        evidence = {"test": self._testMethodName, "dequeues": self.q.dequeues, "acknowledgments": self.q.acknowledgments,
                    "unfinished_queue_tasks": self.q.unfinished_tasks,
                    "remaining_holders": len(self.holders), "jobs": self.jobs}
        (self.root / "lifecycle-evidence.json").write_text(
            json.dumps(evidence, indent=2, default=repr), encoding="utf-8")

    def fake_render(self, proj, seq_id, out, preset, log, progress, proc_holder):
        Path(out).write_bytes(b"synthetic completed output")
        progress(1.0)

    def png_encoder(self, cmd, **kwargs):
        stage = Path(cmd[-1]).parent
        for i in range(1, 13):
            Image.new("RGB", (64, 48), (i, 80, 160)).save(stage / f"frame_{i:05d}.png")

    def enqueue(self, name, proj=None, preset=None):
        return server.start_render(proj or project(), "s", preset or {}, name, "engineering")

    def drain(self, expected):
        with self.assertRaises(StopWorker):
            server._render_worker()
        self.assertEqual(self.q.dequeues, expected)
        self.assertEqual(self.q.acknowledgments, expected)
        self.assertEqual(self.q.unfinished_tasks, 0)
        self.assertEqual(self.holders, {})

    def fail_first_event(self, error=None):
        original = server.log_event
        attempts = []
        def record(event):
            attempts.append(event["job"]["id"])
            if len(attempts) == 1:
                raise error or RuntimeError("injected event-store failure")
            return original(event)
        self.stack.enter_context(patch.object(server, "log_event", side_effect=record))
        return attempts

    def assert_audit_failure(self, job):
        self.assertEqual(job["event_persistence"]["status"], "error")
        self.assertLessEqual(len(job["event_persistence"]["error"]), 500)
        self.assertTrue(job["diagnostics"])
        self.assertLessEqual(len(job["diagnostics"]), 8)
        self.assertEqual(job["diagnostics"][-1]["phase"], "event_persistence")
        self.assertLessEqual(len(job["diagnostics"][-1]["message"]), 600)
        self.assertTrue(any("event" in flag.lower() for flag in job["qa"]["flags"]))

    def assert_successor(self, job):
        self.assertEqual(job["status"], "done", job.get("error"))
        self.assertEqual(job["event_persistence"]["status"], "recorded")
        self.assertEqual(job["qa"], self.qa)
        events = [json.loads(line) for line in Path(server.PP("events.jsonl")).read_text().splitlines()]
        self.assertEqual(events[-1]["job"]["id"], job["id"])

    def test_event_failure_retires_before_second_job_and_preserves_output_qa(self):
        first = self.enqueue("first")
        second = self.enqueue("second")
        first["diagnostics"] = [{"phase": "prior", "message": str(i)} for i in range(10)]
        self.qa["flags"] = ["existing technical warning"]
        self.qa["status"] = "warnings"
        opened = []
        real_open = open
        def event_open(path, mode="r", *args, **kwargs):
            if str(path).endswith("events.jsonl") and mode == "a":
                opened.append(str(path))
                if len(opened) == 1:
                    raise OSError("injected event-store write failure " + "x" * 4000)
            return real_open(path, mode, *args, **kwargs)
        def render(*args, **kwargs):
            if Path(args[2]).stem == "second":
                self.assertNotIn(first["id"], self.holders)
                self.assertEqual(self.q.acknowledgments, 1)
                self.assertEqual(self.q.unfinished_tasks, 1)
            self.fake_render(*args, **kwargs)
        self.render.side_effect = render
        with patch("builtins.open", side_effect=event_open):
            self.drain(2)
        self.assertEqual(len(opened), 2)
        self.assertEqual(first["status"], "done")
        self.assertEqual(first["out"], f"/renders/job-{first['id']}/first.mp4")
        self.assertEqual((self.root / first["out"].lstrip("/")).read_bytes(), b"synthetic completed output")
        self.assertEqual(first["qa"]["sha256"], "fixture-hash")
        self.assertIn("existing technical warning", first["qa"]["flags"])
        self.assert_audit_failure(first)
        self.assertEqual(len(first["diagnostics"]), 8)
        self.assertEqual(first["diagnostics"][0]["message"], "3")
        self.assert_successor(second)

    def test_render_failure_and_event_failure_preserve_render_error_then_continue(self):
        first = self.enqueue("failed")
        second = self.enqueue("second")
        def render(*args, **kwargs):
            if Path(args[2]).stem == "failed":
                raise RuntimeError("real encoder failure")
            return self.fake_render(*args, **kwargs)
        self.render.side_effect = render
        attempts = self.fail_first_event()
        self.drain(2)
        self.assertEqual(attempts, [first["id"], second["id"]])
        self.assertEqual(first["status"], "error")
        self.assertEqual(first["error"], "real encoder failure")
        self.assert_audit_failure(first)
        self.assert_successor(second)

    def test_worker_preflight_rejection_and_event_failure_then_continue(self):
        p = project()
        source = self.root / "source.mp4"
        source.write_bytes(b"original")
        p["media"] = {"m": {"path": str(source), "has_video": True, "has_audio": False}}
        p["sequences"][0]["tracks"] = [{"id": "V1", "kind": "video", "clips": [
            {"id": "c", "media_id": "m", "start": 0, "in_": 0, "out": .5}]}]
        first = self.enqueue("missing", p)
        source.unlink()  # Real recheck of a source lost after enqueue.
        report = server.inspect_resources(p, "s")
        self.assertIn("missing_source", {issue["code"] for issue in report["issues"]})
        with self.assertRaises(server.ResourceError) as rejected:
            server.require_resources(p, "s")
        second = self.enqueue("second")
        self.fail_first_event()
        self.drain(2)
        self.assertEqual(first["status"], "error")
        self.assertEqual(first["error"], str(rejected.exception)[-2000:])
        self.assertEqual(self.render.call_count, 1)
        self.assert_audit_failure(first)
        self.assert_successor(second)

    def test_enqueue_preflight_rejection_never_adds_queue_task(self):
        captured = project()
        before = copy.deepcopy(captured)
        for preset, report in (({}, None), ({"range": True}, {"ok": True})):
            with self.assertRaises(server.HTTPException) as refused:
                server.start_render(captured, "absent", preset, "rejected", "engineering", report)
            self.assertEqual(refused.exception.status_code, 422)
            self.assertIn("missing_sequence", {i["code"] for i in refused.exception.detail["issues"]})
            self.assertEqual(captured, before)
            self.assertEqual(self.q.unfinished_tasks, 0)
            self.assertEqual(self.jobs, {})
            self.assertEqual(self.holders, {})
            self.assertEqual(list(self.root.iterdir()), [])
        second = self.enqueue("second")
        self.drain(1)
        self.assert_successor(second)

    def test_render_route_unknown_sequence_refuses_then_valid_sequence_enqueues(self):
        captured = project()
        before = copy.deepcopy(captured)
        context = server.project_context(str(self.root), captured["id"], captured)
        class Request:
            def __init__(self, sequence): self.sequence = sequence
            async def json(self):
                return {"sequence": self.sequence, "name": "route", "preset": {"format": "png_sequence"},
                        "actor": "human", "_context": context}
        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)
        # This suite replaces worker thread creation; run the real route's
        # resource preflight inline rather than create an unrelated executor.
        with patch.object(server, "preview_state", return_value=(captured, context)), \
                patch.object(server.asyncio, "to_thread", side_effect=immediate):
            with self.assertRaises(server.HTTPException) as refused:
                asyncio.run(server.render_job(Request("absent")))
            self.assertEqual(refused.exception.status_code, 422)
            self.assertIn("missing_sequence", {i["code"] for i in refused.exception.detail["issues"]})
            self.assertEqual(captured, before)
            self.assertEqual(self.jobs, {})
            self.assertEqual(self.q.unfinished_tasks, 0)
            self.assertEqual(list(self.root.iterdir()), [])
            result = asyncio.run(server.render_job(Request("s")))
        self.assertEqual(result["sequence"], "s")
        self.assertEqual(result["context"], context)
        self.assertEqual(result["status"], "queued")
        self.assertEqual(len(self.jobs), 1)
        self.assertEqual(self.q.unfinished_tasks, 1)
        self.assertEqual(captured, before)
        asyncio.run(server.render_cancel(result["id"]))
        self.drain(1)
        self.assertEqual(self.jobs[result["id"]]["status"], "error")
        self.assertEqual(self.jobs[result["id"]]["error"], "cancelled")

    def test_queued_cancellation_is_logged_and_retired_after_event_failure(self):
        first = self.enqueue("cancelled")
        self.assertEqual(asyncio.run(server.render_cancel(first["id"])), {"ok": True})
        second = self.enqueue("second")
        attempts = self.fail_first_event()
        self.drain(2)
        self.assertEqual(attempts, [first["id"], second["id"]])
        self.assertEqual(first["status"], "error")
        self.assertEqual(first["error"], "cancelled")
        self.assertEqual(self.render.call_count, 1)
        self.assertNotIn("started_run", first)
        self.assert_audit_failure(first)
        self.assert_successor(second)

    def test_job_start_bookkeeping_failure_is_retired_then_worker_continues(self):
        first = self.enqueue("bookkeeping")
        first = self.jobs[first["id"]] = FailOnceJob(first)
        second = self.enqueue("second")
        self.drain(2)
        self.assertEqual(first["status"], "error")
        self.assertEqual(first["error"], "injected job bookkeeping failure")
        self.assertEqual(self.render.call_count, 1)
        self.assert_successor(second)

    def test_command_log_failure_and_event_failure_retire_holder_then_continue(self):
        first = self.enqueue("command_log")
        second = self.enqueue("second")
        self.fail_first_event()
        real_open = open
        def command_open(path, mode="r", *args, **kwargs):
            if str(path).endswith("command_log.cmd.txt") and mode == "x":
                self.assertIn(first["id"], self.holders)
                raise PermissionError("injected command log sharing failure")
            return real_open(path, mode, *args, **kwargs)
        with patch("builtins.open", side_effect=command_open):
            self.drain(2)
        self.assertEqual(first["status"], "error")
        self.assertEqual(first["error"], "injected command log sharing failure")
        self.assertEqual(self.render.call_count, 1)
        self.assert_audit_failure(first)
        self.assert_successor(second)

    def test_unregistered_job_retires_stale_holder_without_blocking_next_job(self):
        first = self.enqueue("unregistered")
        del self.jobs[first["id"]]
        self.holders[first["id"]] = {"last": 0}
        second = self.enqueue("second")
        self.drain(2)
        self.assertEqual(self.render.call_count, 1)
        self.assert_successor(second)

    def test_event_serialization_failure_is_visible_and_bounded(self):
        first = self.enqueue("serialization")
        first["invalid_event_value"] = object()
        second = self.enqueue("second")
        self.drain(2)
        self.assertEqual(first["status"], "done")
        self.assert_audit_failure(first)
        self.assertIn("serializable", first["event_persistence"]["error"])
        self.assert_successor(second)

    def test_event_failure_preserves_verification_error(self):
        first = self.enqueue("qa_error")
        self.qa = {"status": "error", "error": "verification failed", "flags": ["review output"]}
        self.fail_first_event()
        self.drain(1)
        self.assertEqual(first["status"], "done")
        self.assertEqual(first["qa"]["status"], "error")
        self.assertEqual(first["qa"]["error"], "verification failed")
        self.assertIn("review output", first["qa"]["flags"])
        self.assert_audit_failure(first)

    def test_png_qa_and_published_output_survive_event_failure(self):
        first = self.enqueue("png", preset={"format": "png_sequence"})
        self.render.side_effect = engine.render
        self.fail_first_event()
        with patch.object(engine, "_run_ffmpeg", side_effect=self.png_encoder):
            self.drain(1)
        self.assertEqual(first["status"], "done")
        self.assertEqual(first["out"], f"/renders/job-{first['id']}/png.frames/sequence.json")
        self.assertEqual(first["qa"]["frame_count"], 12)
        manifest = self.root / first["out"].lstrip("/")
        self.assertEqual(first["qa"]["sha256"], hashlib.sha256(manifest.read_bytes()).hexdigest())
        frames = json.loads(manifest.read_text())["frames"]
        self.assertEqual(len(frames), 12)
        for frame in frames:
            self.assertEqual(frame["sha256"], hashlib.sha256((manifest.parent / frame["file"]).read_bytes()).hexdigest())
        self.assertEqual(first["qa"]["status"], "warnings")
        self.verify.assert_not_called()
        self.assert_audit_failure(first)

    def test_png_validation_cancellation_plus_event_failure_preserves_old_output_and_cleans(self):
        first = self.enqueue("png_cancel", preset={"format": "png_sequence"})
        destination = self.root / first["frames"]["directory"].lstrip("/")
        destination.mkdir()
        (destination / "sequence.json").write_bytes(b"previous manifest")
        (destination / "frame_00001.png").write_bytes(b"previous frame")
        old = {p.name: p.read_bytes() for p in destination.iterdir()}
        self.render.side_effect = engine.render
        validate = engine._validate_png_sequence
        def cancel(*args):
            self.assertEqual(asyncio.run(server.render_cancel(first["id"])), {"ok": True})
            return validate(*args)
        self.fail_first_event()
        with patch.object(engine, "_run_ffmpeg", side_effect=self.png_encoder), \
                patch.object(engine, "_validate_png_sequence", side_effect=cancel):
            self.drain(1)
        self.assertEqual(first["status"], "error")
        self.assertEqual(first["error"], "cancelled")
        self.assertEqual({p.name: p.read_bytes() for p in destination.iterdir()}, old)
        self.assertFalse(list(destination.parent.glob(".*.stage-*")))
        self.assertFalse(list(destination.parent.glob("*.frames.lock")))
        self.assert_audit_failure(first)


if __name__ == "__main__":
    print("Retained queue lifecycle evidence:", RUN, flush=True)
    print("Candidate server SHA256:", hashlib.sha256((ROOT / "backend/server.py").read_bytes()).hexdigest(), flush=True)
    unittest.main(verbosity=2)

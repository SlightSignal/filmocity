"""Real encoder checks without a web server or framework dependencies.

Requires Node.js, Pillow, ffmpeg (PNG, FFV1, AAC, PCM encoders) and ffprobe on PATH.
All media, outputs and owned scratch are temporary; no user projects are read.
This checks the headless renderer, not the application's UI or native packaging.
"""
import array
import copy
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import render as engine
from render_context import RenderContext


class RealRenderSmoke(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="filmocity-render-smoke-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Émile's media and exports"
        self.root.mkdir()
        media = {}
        for name, color in [("red", (255, 0, 0)), ("blue", (0, 0, 255))]:
            path = self.root / (name + ".png")
            Image.new("RGB", (64, 48), color).save(path)
            media[name] = {"id": name, "name": name, "path": str(path), "is_image": True,
                           "has_video": True, "has_audio": False, "width": 64,
                           "height": 48, "duration": 1, "fps": 24}
        samples = array.array("h", (round(8000 * math.sin(2 * math.pi * 440 * i / 48000)) for i in range(96000)))
        if sys.byteorder != "little": samples.byteswap()
        sound = self.root / "440 Hz.wav"
        with wave.open(str(sound), "wb") as writer:
            writer.setparams((1, 2, 48000, 0, "NONE", "not compressed")); writer.writeframes(samples.tobytes())
        media["tone"] = {"id": "tone", "name": "tone", "path": str(sound), "has_audio": True, "has_video": False, "duration": 2}
        self.project = {"id": "smoke", "media": media, "sequences": [{"id": "s", "name": "Smoke", "width": 64, "height": 48,
            "fps": 24, "captions": [], "tracks": [
                {"id": "V1", "kind": "video", "index": 1, "clips": [
                    {"id": name, "media_id": name, "start": i, "in_": 0, "out": 1} for i, name in enumerate(("red", "blue"))]},
                {"id": "A1", "kind": "audio", "index": 1, "clips": [
                    {"id": "audio", "media_id": "tone", "start": 0, "in_": 0, "out": 2}]}
            ]}]}
        self.original = copy.deepcopy(self.project)
        self.input_hashes = {m["path"]: self.digest(Path(m["path"])) for m in media.values()}

    @staticmethod
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def tearDown(self):
        self.assertEqual(self.project, self.original, "render mutated the caller's project")
        self.assertEqual({p: self.digest(Path(p)) for p in self.input_hashes}, self.input_hashes)
        self.assertFalse(list(self.root.glob("filmocity-render-*")))
        self.assertFalse(list(self.root.glob("*.part.*")))
        self.assertFalse(list(self.root.glob("*.frames.lock")))

    def render(self, name, preset, holder=None, progress=None):
        with RenderContext(scratch_parent=str(self.root), proc_holder=holder, stall_timeout=15) as context:
            return Path(engine.render(self.project, "s", str(self.root / name), preset, context=context, progress=progress))

    def assert_color(self, path, expected):
        with Image.open(path) as image:
            self.assertEqual(image.size, (64, 48))
            actual = image.convert("RGB").getpixel((32, 24))
        for a, b in zip(actual, expected): self.assertLessEqual(abs(a - b), 3, (actual, expected))

    def precise_values(self, spec, edits):
        """Use the production frontend draft model to supply renderer inputs."""
        script = """
const fs = require('node:fs');
const { makeModel } = require('./frontend/curve-values.js');
const { spec, edits } = JSON.parse(fs.readFileSync(0, 'utf8'));
const model = makeModel(spec);
for (const [point, key, value] of edits) model.set(point, key, value);
process.stdout.write(JSON.stringify(model.sortedValue()));
"""
        result = subprocess.run([shutil.which('node') or 'node', '-e', script],
                                input=json.dumps({'spec': spec, 'edits': edits}), text=True,
                                capture_output=True, check=True, cwd=ROOT, timeout=10)
        return json.loads(result.stdout)

    def graded_frame(self, color, neutral=128):
        project = copy.deepcopy(self.project)
        source = self.root / 'neutral gray.png'
        Image.new('RGB', (64, 48), (neutral, neutral, neutral)).save(source)
        project['media']['red']['path'] = str(source)
        project['sequences'][0]['tracks'][0]['clips'][0]['color'] = color
        before = copy.deepcopy(project)
        output = self.root / 'graded frame.png'
        with RenderContext(scratch_parent=str(self.root), stall_timeout=15) as context:
            engine.render_frame(project, 's', 0.5, str(output), context=context)
        self.assertEqual(project, before)
        with Image.open(output) as image:
            return image.convert('RGB').getpixel((32, 24))

    def test_precise_rgb_curve_values_render_the_expected_tone(self):
        # A straight 0→0, 1→0.5 curve halves each neutral-gray channel.
        points = self.precise_values({'kind': 'rgb', 'value': [[0, 0], [1, 1]]}, [[1, '1', '0.5']])
        actual = self.graded_frame({'curves': points})
        for channel in actual: self.assertLessEqual(abs(channel - 64), 4, actual)

    def test_precise_wheel_values_change_the_selected_channels_in_real_frames(self):
        wheel = self.precise_values({'kind': 'wheel', 'value': {'r': 0, 'g': 0, 'b': 0}}, [[0, 'r', '0.5']])
        # These levels exercise the existing export filter's tonal bands.
        # This is routing evidence, not GPU/FFmpeg tonal-weight equivalence.
        for band, neutral in [('shadows', 16), ('midtones', 64), ('highlights', 192)]:
            with self.subTest(band=band):
                actual = self.graded_frame({'wheels': {band: wheel}}, neutral)
                self.assertGreater(actual[0] - actual[1], 20, actual)
                self.assertLessEqual(abs(actual[1] - neutral), 4, actual)
                self.assertLessEqual(abs(actual[2] - neutral), 4, actual)

    def test_frame_and_numbered_sequence_match_the_cut_and_manifest(self):
        for t, color in [(0, (255, 0, 0)), (23 / 24, (255, 0, 0)), (1, (0, 0, 255)), (47 / 24, (0, 0, 255))]:
            frame = self.root / f"frame at {t:.4f}.png"
            with RenderContext(scratch_parent=str(self.root), stall_timeout=15) as context:
                engine.render_frame(self.project, "s", t, str(frame), context=context)
            self.assert_color(frame, color)
        destination = self.render("numbered.png", {"format": "png_sequence"})
        manifest = json.loads((destination / "sequence.json").read_text())
        self.assertEqual(manifest["frame_count"], 48)
        self.assertEqual(manifest["frame_rate"], 24)
        self.assertEqual(len(list(destination.glob("*.png"))), 48)
        for i, item in enumerate(manifest["frames"]):
            path = destination / item["file"]
            self.assertEqual(item["sha256"], self.digest(path))
            self.assert_color(path, (255, 0, 0) if i < 24 else (0, 0, 255))

    def test_video_contains_real_frames_and_audio_with_the_expected_duration(self):
        out = self.render("video.mkv", {"vcodec": "ffv1"})
        info = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(out)], timeout=15))
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
        self.assertEqual(video["codec_name"], "ffv1")
        self.assertEqual((video["width"], video["height"]), (64, 48))
        self.assertEqual(video["r_frame_rate"], "24/1")
        self.assertEqual(audio["sample_rate"], "48000")
        self.assertAlmostEqual(float(info["format"]["duration"]), 2, delta=.05)
        for t, color in [(.25, (255, 0, 0)), (1.25, (0, 0, 255))]:
            frame = self.root / f"decoded {t}.png"
            subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-ss", str(t), "-frames:v", "1", "-update", "1", str(frame)], check=True, timeout=15)
            self.assert_color(frame, color)
        raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(out), "-vn", "-ac", "1", "-f", "s16le", "-c:a", "pcm_s16le", "-"], timeout=15)
        self.assert_tone(raw)

    def assert_tone(self, raw):
        samples = array.array("h"); samples.frombytes(raw)
        if sys.byteorder != "little": samples.byteswap()
        self.assertAlmostEqual(len(samples) / 48000, 2, delta=.05)
        samples = samples[12000:36000]
        rms = math.sqrt(sum(s * s for s in samples) / len(samples))
        crossings = sum(a < 0 <= b for a, b in zip(samples, samples[1:]))
        self.assertGreater(rms, 1500)
        self.assertAlmostEqual(crossings / .5, 440, delta=4)

    def test_audio_only_exports_pcm_with_audible_tone(self):
        out = self.render("audio.wav", {"format": "audio", "acodec": "wav"})
        with wave.open(str(out), "rb") as audio:
            self.assertEqual(audio.getframerate(), 48000)
            self.assertEqual(audio.getsampwidth(), 2)
            self.assertEqual(audio.getnframes(), 96000)
            samples = array.array("h"); samples.frombytes(audio.readframes(audio.getnframes()))
            channels = audio.getnchannels()
        self.assert_tone(samples[::channels].tobytes())

    def test_cancellation_before_publication_preserves_master_and_next_job_succeeds(self):
        out = self.root / "master.mkv"; approved = b"previous approved master"; out.write_bytes(approved)
        holder = {}; requested = []
        def cancel_on_progress(fraction):
            if not requested:
                requested.append(fraction); holder["cancelled"] = True
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            self.render(out.name, {"vcodec": "ffv1"}, holder, cancel_on_progress)
        self.assertTrue(requested, "real FFmpeg progress must have caused cancellation")
        self.assertEqual(out.read_bytes(), approved)
        self.assertNotIn("proc", holder)
        self.assertFalse(list(self.root.glob("*.part.*")))
        self.render(out.name, {"vcodec": "ffv1"}, {})
        self.assertNotEqual(out.read_bytes(), approved)


if __name__ == "__main__":
    unittest.main(verbosity=2)

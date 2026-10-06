"""File-backed graph compatibility, byte fidelity and owned process lifetime."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
import ffmpeg_graph as graphs
from render_context import RenderContext


class GraphFiles(unittest.TestCase):
    def setUp(self):
        with graphs._LOCK: graphs._CACHE.clear()

    def test_modern_selection_and_legacy_capability_fallback_are_cached(self):
        for supported in graphs.FILE_OPTIONS:
            with self.subTest(supported=supported), tempfile.TemporaryDirectory() as root, RenderContext(scratch_parent=root) as ctx:
                calls = []
                def accept(executable, option, graph, context):
                    self.assertEqual(Path(graph).read_bytes(), b'[0:a]anull[a]')
                    calls.append(option)
                    return option == supported
                with graphs._LOCK: graphs._CACHE.clear()
                with patch.object(graphs, '_accepts', side_effect=accept):
                    self.assertEqual(graphs.file_option('ffmpeg', ctx), supported)
                    self.assertEqual(graphs.file_option('ffmpeg', ctx), supported)
                self.assertEqual(calls, list(graphs.FILE_OPTIONS[:graphs.FILE_OPTIONS.index(supported)+1]))

    def test_unsupported_tool_refuses_before_export_and_small_graph_needs_no_probe(self):
        with tempfile.TemporaryDirectory() as root, RenderContext(scratch_parent=root) as ctx:
            small = ['ffmpeg', '-filter_complex', '[0:a]anull[a]']
            with patch.object(graphs, '_accepts', return_value=False) as accept:
                self.assertIs(graphs.externalize(small, ctx), small)
                accept.assert_not_called()
                with self.assertRaisesRegex(ValueError, 'neither file-backed'):
                    graphs.externalize(['ffmpeg', '-filter_complex', 'x'*5000], ctx)
            self.assertNotIn('proc', ctx.holder)

    def test_replaced_executable_is_probed_again(self):
        with tempfile.TemporaryDirectory() as root, RenderContext(scratch_parent=root) as ctx:
            identity = ['ffmpeg', 100, 1, 1]
            with patch.object(graphs, '_identity', side_effect=lambda executable: tuple(identity)), patch.object(graphs, '_accepts', return_value=True) as accept:
                self.assertEqual(graphs.file_option('ffmpeg', ctx), graphs.FILE_OPTIONS[0])
                self.assertEqual(graphs.file_option('ffmpeg', ctx), graphs.FILE_OPTIONS[0])
                self.assertEqual(accept.call_count, 1)
                identity[2] += 1
                self.assertEqual(graphs.file_option('ffmpeg', ctx), graphs.FILE_OPTIONS[0])
                self.assertEqual(accept.call_count, 2)

    def test_real_large_file_graph_matches_inline_pcm_and_retires_unicode_path(self):
        with tempfile.TemporaryDirectory(prefix="Émile's graph ") as root:
            with RenderContext(scratch_parent=root) as ctx:
                base = ['ffmpeg', '-v', 'error', '-nostdin', '-f', 'lavfi', '-i', 'sine=f=777:r=48000:d=0.05']
                small = '[0:a]anull[a]'
                large = '[0:a]' + ','.join(['anull']*7000) + '[a]'
                tail = ['-map', '[a]', '-f', 'f32le', '-c:a', 'pcm_f32le', '-']
                expected = subprocess.run(base+['-filter_complex', small]+tail, capture_output=True, check=True, timeout=10).stdout
                command = graphs.externalize(base+['-filter_complex', large]+tail, ctx)
                option = next(option for option in graphs.FILE_OPTIONS if option in command)
                path = Path(command[command.index(option)+1])
                self.assertEqual(path.read_bytes(), large.encode('utf-8'))
                self.assertGreater(path.stat().st_size, 32768)
                self.assertEqual(subprocess.run(command, capture_output=True, check=True, timeout=10).stdout, expected)
                self.assertNotIn('proc', ctx.holder)
            self.assertFalse(path.exists())


if __name__ == '__main__': unittest.main(verbosity=2)

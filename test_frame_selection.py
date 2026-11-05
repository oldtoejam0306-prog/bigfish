import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

from bili_workbench.frame_selection import select_indices, UPSTREAM_COMMIT
from bili_workbench.runtime import Fault


WIDTH, HEIGHT = 64, 36


def color(r, g, b):
    return bytes((r, g, b)) * (WIDTH * HEIGHT)


class FrameSelectionTests(unittest.TestCase):
    def test_vendored_bytes_match_fixed_source_manifest(self):
        root = Path(__file__).parent / 'vendor' / 'see_now'
        manifest = json.loads((root / 'UPSTREAM.json').read_text())
        self.assertEqual(manifest['commit'], UPSTREAM_COMMIT)
        self.assertEqual(manifest['license'], 'Apache-2.0')
        for item in manifest['files']:
            raw = (root / item['file']).read_bytes()
            self.assertEqual(len(raw), item['bytes'])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), item['sha256'])

    def test_actual_node_selects_changes_and_deduplicates(self):
        if not shutil.which('node'):
            self.skipTest('Optional Node executable not present')
        frames = [color(255, 0, 0), color(255, 0, 0), color(0, 0, 255), color(0, 0, 255), color(0, 255, 0)]
        result = select_indices(frames, WIDTH, HEIGHT)
        self.assertEqual(result['method'], 'see-now-hsv')
        self.assertEqual(result['indices'], [0, 2, 4])
        self.assertEqual(result['scores'][1], 0)
        self.assertGreater(result['scores'][2], 8)

    def test_actual_node_static_video_needs_only_one_frame(self):
        if not shutil.which('node'):
            self.skipTest('Optional Node executable not present')
        result = select_indices([color(20, 20, 20)] * 5, WIDTH, HEIGHT)
        self.assertEqual(result['method'], 'see-now-hsv')
        self.assertEqual(result['indices'], [0])

    def test_node_missing_is_explicit_uniform_fallback(self):
        with patch('bili_workbench.frame_selection.shutil.which', return_value=None):
            result = select_indices([color(0, 0, 0)] * 5, WIDTH, HEIGHT)
        self.assertEqual(result['indices'], [0, 2, 4])
        self.assertEqual(result['method'], 'uniform-fallback')
        self.assertEqual(result['reason'], 'node-unavailable')
        self.assertEqual(result['scores'], [None] * 5)

    def test_node_failure_is_not_hidden_as_adaptive_selection(self):
        with patch('bili_workbench.frame_selection.shutil.which', return_value='/fixture/node'), patch('bili_workbench.frame_selection.subprocess.run', side_effect=subprocess.TimeoutExpired('node', 8)):
            result = select_indices([color(0, 0, 0)] * 2, WIDTH, HEIGHT)
        self.assertEqual(result['method'], 'uniform-fallback')
        self.assertEqual(result['reason'], 'node-failed')

    def test_malformed_node_output_is_rejected(self):
        output = subprocess.CompletedProcess([], 0, json.dumps({'indices': [999], 'scores': [None], 'method': 'see-now-hsv'}), '')
        with patch('bili_workbench.frame_selection.shutil.which', return_value='/fixture/node'), patch('bili_workbench.frame_selection.subprocess.run', return_value=output):
            result = select_indices([color(0, 0, 0)], WIDTH, HEIGHT)
        self.assertEqual(result['method'], 'uniform-fallback')

    def test_no_shell_or_node_startup_preloads(self):
        output = subprocess.CompletedProcess([], 0, json.dumps({'indices': [0], 'scores': [None], 'method': 'see-now-hsv'}), '')
        with patch.dict('os.environ', {'NODE_OPTIONS': '--require malicious', 'NODE_PATH': '/untrusted'}), patch('bili_workbench.frame_selection.shutil.which', return_value='/fixture/node'), patch('bili_workbench.frame_selection.subprocess.run', return_value=output) as run:
            select_indices([color(0, 0, 0)], WIDTH, HEIGHT)
        args, kwargs = run.call_args
        self.assertEqual(args[0][0], '/fixture/node')
        self.assertEqual(Path(args[0][1]).parts[-3:], ('vendor', 'see_now', 'wrapper.mjs'))
        self.assertNotIn('shell', kwargs)
        self.assertNotIn('NODE_OPTIONS', kwargs['env'])
        self.assertNotIn('NODE_PATH', kwargs['env'])

    def test_empty_input_no_subprocess(self):
        with patch('bili_workbench.frame_selection.subprocess.run') as run:
            result = select_indices([], WIDTH, HEIGHT)
        self.assertEqual(result['indices'], [])
        self.assertEqual(result['method'], 'empty')
        run.assert_not_called()

    def test_invalid_shape_or_type_denied_before_subprocess(self):
        bad = [([b'x'], 64, 36, 3), ([color(0, 0, 0)], True, 36, 3), ([color(0, 0, 0)], 64, 36, True), ([color(0, 0, 0)] * 33, 64, 36, 3)]
        with patch('bili_workbench.frame_selection.subprocess.run') as run:
            for frames, width, height, limit in bad:
                with self.assertRaises(Fault):
                    select_indices(frames, width, height, limit)
        run.assert_not_called()

    def test_limit_one_has_single_initial_frame(self):
        with patch('bili_workbench.frame_selection.shutil.which', return_value=None):
            result = select_indices([color(0, 0, 0)] * 5, WIDTH, HEIGHT, limit=1)
        self.assertEqual(result['indices'], [0])


if __name__ == '__main__':
    unittest.main()

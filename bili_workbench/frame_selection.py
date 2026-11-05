"""Optional reviewed SeeNow scoring for already-decoded local RGB frames.

No desktop capture, network, model request, install or mutable profile lookup.
Node is optional; callers receive an explicit uniform fallback if unavailable.
"""
import base64
import json
import math
import os
from pathlib import Path
import shutil
import subprocess

from .runtime import Fault

UPSTREAM_COMMIT = '916354d36e58fdd843fff96284ff7c985bd00020'
WRAPPER = Path(__file__).resolve().parents[1] / 'vendor' / 'see_now' / 'wrapper.mjs'


def _uniform(count, limit, reason):
    count_selected = min(count, limit)
    if count_selected <= 1:
        indices = [0] if count else []
    else:
        indices = [round(index * (count - 1) / (count_selected - 1)) for index in range(count_selected)]
    return {'indices': indices, 'scores': [None] * count,
            'method': 'uniform-fallback', 'reason': reason,
            'source_commit': UPSTREAM_COMMIT}


def select_indices(frames_rgb, width, height, limit=3):
    """Return at most ``limit`` chronological indices from RGB24 byte frames.

    Frames must already be explicitly decoded by the caller. Consecutive or
    globally near-duplicate frames can yield fewer than ``limit`` selections.
    This is sparse visual sampling, never proof of full video understanding.
    """
    if type(width) is not int or type(height) is not int or not 1 <= width <= 640 or not 1 <= height <= 360:
        raise Fault('RGB尺寸必须为1至640 × 1至360的整数')
    if type(limit) is not int or not 1 <= limit <= 16:
        raise Fault('选帧上限须为1至16')
    if not isinstance(frames_rgb, (list, tuple)) or len(frames_rgb) > 32:
        raise Fault('候选帧须为至多32项的序列')
    expected = width * height * 3
    if expected * len(frames_rgb) > 12_000_000:
        raise Fault('RGB候选总字节数超过上限')
    encoded = []
    for frame in frames_rgb:
        if not isinstance(frame, (bytes, bytearray, memoryview)):
            raise Fault('候选帧必须是尺寸匹配的RGB24字节')
        frame = bytes(frame)
        if len(frame) != expected:
            raise Fault('候选帧必须是尺寸匹配的RGB24字节')
        encoded.append(base64.b64encode(frame).decode('ascii'))
    if not encoded:
        return {'indices': [], 'scores': [], 'method': 'empty', 'source_commit': UPSTREAM_COMMIT}
    executable = shutil.which('node')
    if not executable:
        return _uniform(len(encoded), limit, 'node-unavailable')
    # Suppress Node module preloads; only the audited fixed file is imported.
    env = dict(os.environ)
    env.pop('NODE_OPTIONS', None)
    env.pop('NODE_PATH', None)
    payload = json.dumps({'width': width, 'height': height, 'limit': limit, 'frames': encoded})
    try:
        response = subprocess.run([executable, str(WRAPPER)], input=payload,
                                  capture_output=True, text=True, check=True,
                                  timeout=8, env=env)
        if len(response.stdout.encode()) > 32_000:
            raise ValueError('output-size')
        result = json.loads(response.stdout)
        indices, scores = result['indices'], result['scores']
        if result.get('method') != 'see-now-hsv' or not isinstance(indices, list) or not 1 <= len(indices) <= min(limit, len(encoded)):
            raise ValueError('indices')
        if any(type(index) is not int or not 0 <= index < len(encoded) for index in indices) or sorted(set(indices)) != indices:
            raise ValueError('index-range')
        if not isinstance(scores, list) or len(scores) != len(encoded) or scores[0] is not None:
            raise ValueError('scores')
        if any(type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 255 for score in scores[1:]):
            raise ValueError('score-range')
        return {'indices': indices, 'scores': scores, 'method': 'see-now-hsv', 'source_commit': UPSTREAM_COMMIT}
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return _uniform(len(encoded), limit, 'node-failed')

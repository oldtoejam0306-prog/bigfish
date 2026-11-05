// BigFish-owned bounded stdin adapter. No screen/camera capture or network.
import { createScorer, scorePair } from './cuts.mjs';

const MAX_INPUT = 20_000_000;
let input = '';
try {
  for await (const chunk of process.stdin) {
    input += chunk.toString('utf8');
    if (input.length > MAX_INPUT) throw new Error('input-size');
  }
  const data = JSON.parse(input);
  const { width, height, limit, frames } = data;
  if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1 || width > 640 || height > 360 || !Number.isInteger(limit) || limit < 1 || limit > 16 || !Array.isArray(frames) || frames.length > 32) throw new Error('shape');
  const length = width * height * 3;
  if (length * frames.length > 12_000_000) throw new Error('size');
  const scorer = createScorer({ width, height });
  const snapshots = [];
  const scores = [];
  for (const encoded of frames) {
    if (typeof encoded !== 'string' || !/^[A-Za-z0-9+/]*={0,2}$/.test(encoded)) throw new Error('frame');
    const rgb = Buffer.from(encoded, 'base64');
    if (rgb.length !== length) throw new Error('frame-size');
    const result = scorer(rgb);
    snapshots.push(new Float32Array(result.hsv)); // upstream buffer is reused
    scores.push(result.score);
  }
  const picked = frames.length ? [0] : [];
  const candidates = scores.map((score, index) => ({ score, index }))
    .filter(x => x.score !== null && Number.isFinite(x.score) && x.score >= 8)
    .sort((a, b) => b.score - a.score || a.index - b.index);
  for (const candidate of candidates) {
    if (picked.length >= limit) break;
    if (picked.every(index => scorePair(snapshots[index], snapshots[candidate.index]) >= 8)) picked.push(candidate.index);
  }
  picked.sort((a, b) => a - b);
  process.stdout.write(JSON.stringify({ indices: picked, scores, method: 'see-now-hsv' }));
} catch {
  // No input, file paths, environment, provider output or exception is logged.
  process.stdout.write(JSON.stringify({ error: 'invalid-frame-selection-input' }));
  process.exitCode = 1;
}

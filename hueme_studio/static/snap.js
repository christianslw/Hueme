// Pure snap selection shared by the timeline and its Node self-check.
// Grid is the fallback; a magnet (marker, clip edge, playhead) wins only when
// it is inside the pixel threshold and closer than the grid line.
export function nearestSnap(value, { grid, candidates = [], threshold = 0, min = -Infinity, max = Infinity }) {
  let best = grid;
  let bestDistance = Math.abs(value - grid);
  for (const candidate of candidates) {
    if (!Number.isFinite(candidate) || candidate < min || candidate > max) continue;
    const distance = Math.abs(value - candidate);
    if (distance <= threshold && distance < bestDistance) {
      best = candidate;
      bestDistance = distance;
    }
  }
  return Math.min(max, Math.max(min, best));
}

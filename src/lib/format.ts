/** Format a duration in seconds as `m:ss` (e.g. 75 → "1:15"). Used by the
 * transport/seek readouts in the mixer and the export preview player. */
export function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

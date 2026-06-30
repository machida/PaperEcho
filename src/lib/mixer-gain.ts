/** Per-track mixer state shared by the playback engine (StemMixer) and the UI's
 * mixdown gain snapshot. Kept here as pure functions so the solo/mute precedence
 * rule lives in exactly one place (it was duplicated) and is unit-testable. */
export interface GainState {
  volume: number; // 0..1
  muted: boolean;
  soloed: boolean;
}

/** True if any track is soloed — solo mode mutes everything not soloed. */
export function anySoloed(states: Iterable<GainState>): boolean {
  for (const s of states) if (s.soloed) return true;
  return false;
}

/** Effective gain for one track given whether any track is soloed.
 * Solo wins over mute: in solo mode only soloed tracks sound; otherwise a track
 * sounds unless it's muted. An audible track plays at its own volume. */
export function effectiveGain(state: GainState, anySolo: boolean): number {
  const audible = anySolo ? state.soloed : !state.muted;
  return audible ? state.volume : 0;
}

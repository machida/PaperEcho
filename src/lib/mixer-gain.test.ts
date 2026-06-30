import { describe, expect, it } from "vitest";

import { anySoloed, effectiveGain, type GainState } from "./mixer-gain";

const st = (over: Partial<GainState> = {}): GainState => ({
  volume: 0.8,
  muted: false,
  soloed: false,
  ...over,
});

describe("anySoloed", () => {
  it("is false when nothing is soloed", () => {
    expect(anySoloed([st(), st({ muted: true })])).toBe(false);
  });

  it("is true when any track is soloed", () => {
    expect(anySoloed([st(), st({ soloed: true })])).toBe(true);
  });
});

describe("effectiveGain", () => {
  it("plays an unmuted track at its volume when nothing is soloed", () => {
    expect(effectiveGain(st({ volume: 0.6 }), false)).toBe(0.6);
  });

  it("silences a muted track", () => {
    expect(effectiveGain(st({ muted: true }), false)).toBe(0);
  });

  it("solo wins: a non-soloed track is silent in solo mode even if unmuted", () => {
    expect(effectiveGain(st({ soloed: false }), true)).toBe(0);
  });

  it("solo wins: a soloed track plays even if it is also muted", () => {
    expect(effectiveGain(st({ soloed: true, muted: true, volume: 0.7 }), true)).toBe(0.7);
  });
});

import { describe, expect, it } from "vitest";

import { formatTime } from "./format";

describe("formatTime", () => {
  it("zero-pads seconds", () => {
    expect(formatTime(0)).toBe("0:00");
    expect(formatTime(5)).toBe("0:05");
  });

  it("formats minutes and seconds", () => {
    expect(formatTime(75)).toBe("1:15");
    expect(formatTime(605)).toBe("10:05");
  });

  it("floors fractional seconds", () => {
    expect(formatTime(59.9)).toBe("0:59");
  });
});

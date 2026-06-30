import { describe, expect, it } from "vitest";

import { translate } from "./i18n";

describe("translate", () => {
  it("returns the string for the active language", () => {
    expect(translate("ja", "part.bass")).toBe("ベース");
    expect(translate("en", "part.bass")).toBe("Bass");
  });

  it("falls back to the key itself when it is unknown", () => {
    expect(translate("ja", "does.not.exist")).toBe("does.not.exist");
  });

  it("substitutes {token} params", () => {
    const s = translate("ja", "analyze.meta", { n: 3, bpm: 120, ts: "4/4" });
    expect(s).toContain("3");
    expect(s).toContain("120");
    expect(s).toContain("4/4");
    expect(s).not.toContain("{");
  });
});

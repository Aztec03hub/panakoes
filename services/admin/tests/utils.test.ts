import { describe, expect, it } from "vitest";
import { cn, formatTimestamp } from "../src/lib/utils";

describe("cn", () => {
  it("joins class names", () => {
    expect(cn("a", "b")).toBe("a b");
  });

  it("merges conflicting tailwind classes", () => {
    expect(cn("p-2", "p-4")).toBe("p-4");
  });

  it("handles falsy values", () => {
    expect(cn("a", false && "b", null, undefined, "c")).toBe("a c");
  });
});

describe("formatTimestamp", () => {
  it("formats a valid ISO timestamp", () => {
    const out = formatTimestamp("2026-05-08T12:00:00.000Z");
    expect(out).not.toBe("2026-05-08T12:00:00.000Z");
    expect(out.length).toBeGreaterThan(0);
  });

  it("returns the input string for an unparseable timestamp", () => {
    expect(formatTimestamp("not-a-date")).toBe("not-a-date");
  });
});

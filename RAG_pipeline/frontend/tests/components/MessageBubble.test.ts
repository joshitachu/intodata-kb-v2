import { describe, expect, it } from "vitest";
import { currentActivityLabel } from "@/components/MessageBubble";

describe("currentActivityLabel", () => {
  it("shows Thinking when retrieved sources are available", () => {
    expect(currentActivityLabel({ hasSources: true })).toBe("Thinking...");
  });

  it("shows Searching before sources arrive", () => {
    expect(currentActivityLabel({})).toBe("Searching...");
  });

  it("does not derive progress labels from legacy transport state", () => {
    expect(currentActivityLabel({ hasSources: false })).toBe("Searching...");
  });
});

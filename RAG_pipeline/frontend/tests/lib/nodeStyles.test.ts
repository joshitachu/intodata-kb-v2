import { describe, it, expect } from "vitest";
import {
  CARD_BORDER,
  CARD_SHADOW,
  DIMMED_STYLE,
  LABEL_MIN_ZOOM,
  CALL_EDGE_STYLE,
  USES_EDGE_STYLE,
  accentBarStyle,
  cardShadow,
  middleEllipsis,
} from "@/lib/nodeStyles";

describe("nodeStyles", () => {
  it("dims to a readable grayscale state (never the old 0.12 ghosting)", () => {
    expect(DIMMED_STYLE.opacity).toBe(0.35);
    expect(DIMMED_STYLE.filter).toBe("grayscale(0.8)");
  });

  it("highlighted cards get a single accent ring, plain cards a flat shadow", () => {
    expect(cardShadow(true, "#3b82f6")).toBe("0 0 0 2px #3b82f6");
    expect(cardShadow(false, "#3b82f6")).toBe(CARD_SHADOW);
  });

  it("accent bar is a 3px left strip in the given color", () => {
    const style = accentBarStyle("#22c55e");
    expect(style.width).toBe(3);
    expect(style.left).toBe(0);
    expect(style.background).toBe("#22c55e");
  });

  it("edges are neutral and non-dashed for calls, dashed muted for usage", () => {
    expect(CALL_EDGE_STYLE.strokeDasharray).toBeUndefined();
    expect(USES_EDGE_STYLE.strokeDasharray).toBe("6 3");
  });

  it("exposes a sane card border and label zoom threshold", () => {
    expect(CARD_BORDER).toContain("#e2e8f0");
    expect(LABEL_MIN_ZOOM).toBeGreaterThan(0);
    expect(LABEL_MIN_ZOOM).toBeLessThan(1);
  });
});

describe("middleEllipsis", () => {
  it("returns short strings untouched", () => {
    expect(middleEllipsis("GetLocation", 26)).toBe("GetLocation");
  });

  it("returns a string exactly at the budget untouched", () => {
    const exact = "A".repeat(26);
    expect(middleEllipsis(exact, 26)).toBe(exact);
  });

  it("elides the MIDDLE, keeping head and tail, for names longer than budget", () => {
    // The disambiguating suffix ("_Item_v1" vs "_Set_v1") must survive —
    // that's the whole point versus CSS end-truncation.
    const a = middleEllipsis("VDD_Clean_Invoice_Item_v1", 20);
    const b = middleEllipsis("VDD_Clean_Invoice_Set_v1", 20);
    expect(a).not.toBe(b);
    expect(a).toContain("…");
    expect(a.startsWith("VDD_")).toBe(true);
    expect(a.endsWith("v1")).toBe(true);
  });

  it("output length matches the requested character budget exactly", () => {
    const result = middleEllipsis("VDA_BC_Clean_Invoice_Set_v1_Extended_Name", 24);
    expect(result.length).toBe(24);
    expect(result).toContain("…");
  });
});

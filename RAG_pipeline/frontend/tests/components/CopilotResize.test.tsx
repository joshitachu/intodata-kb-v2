import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PageCopilot } from "@/components/PageCopilot";
import type { PageCopilotProps } from "@/components/PageCopilot";
import {
  DEFAULT_COPILOT_WIDTH,
  MAX_COPILOT_WIDTH,
  MIN_COPILOT_WIDTH,
  MIN_HOST_WIDTH,
  clampCopilotWidth,
} from "@/hooks/useCopilotWidth";

/**
 * Press the handle with a given mouse button.
 *
 * jsdom implements no `PointerEvent` constructor, and testing-library's
 * `fireEvent.pointerDown` then dispatches a plain `Event` on which `button` is
 * undefined -- so the button could not be asserted at all through that path. A
 * `MouseEvent` typed `pointerdown` is what React's onPointerDown receives in
 * jsdom anyway, and it does carry `button`.
 */
function press(element: Element, button: number) {
  fireEvent(
    element,
    new MouseEvent("pointerdown", { button, bubbles: true, cancelable: true }),
  );
}

const pressPrimary = (element: Element) => press(element, 0);
const pressSecondary = (element: Element) => press(element, 2);

/** Move the pointer to an absolute viewport x. `clientX` needs a real MouseEvent
 * for the same reason `button` does. */
function movePointerTo(element: Element, clientX: number) {
  fireEvent(element, new MouseEvent("pointermove", { clientX, bubbles: true }));
}

function renderPanel(overrides: Partial<PageCopilotProps> = {}) {
  const props: PageCopilotProps = {
    scope: { kindLabel: "Landscape", name: "Werkorder" },
    messages: [],
    isSending: false,
    onSend: vi.fn(),
    collapsed: false,
    onCollapsedChange: vi.fn(),
    ...overrides,
  };
  return { props, ...render(<PageCopilot {...props} />) };
}

describe("clampCopilotWidth", () => {
  it("keeps a width the viewport can afford", () => {
    expect(clampCopilotWidth(560, 1600)).toBe(560);
  });

  it("never narrows the panel past readability", () => {
    expect(clampCopilotWidth(80, 1600)).toBe(MIN_COPILOT_WIDTH);
  });

  it("leaves room for the diagram the panel is docked to", () => {
    // The panel exists to explain what is on the canvas beside it, so a drag
    // that hides the canvas defeats it. 1200 - 420 = 780.
    expect(clampCopilotWidth(2000, 1200)).toBe(1200 - MIN_HOST_WIDTH);
  });

  it("caps on very wide displays where the host guard alone is slack", () => {
    expect(clampCopilotWidth(5000, 4000)).toBe(MAX_COPILOT_WIDTH);
  });

  it("prefers a usable panel over a usable host when neither fits", () => {
    // Both minimums cannot hold on a narrow viewport. The panel's own minimum
    // wins: a sub-300px panel is unusable, whereas a squeezed canvas is merely
    // cramped and the user can collapse the panel outright.
    expect(clampCopilotWidth(400, 500)).toBe(MIN_COPILOT_WIDTH);
  });

  it("falls back to the default rather than propagating a bad number", () => {
    expect(clampCopilotWidth(Number.NaN, 1600)).toBe(DEFAULT_COPILOT_WIDTH);
  });
});

describe("PageCopilot resizing", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    Object.defineProperty(window, "innerWidth", { value: 1600, configurable: true });
  });

  it("offers no drag affordance when the host owns no width", () => {
    renderPanel();

    expect(screen.queryByRole("separator")).toBeNull();
  });

  it("renders at the width the host gives it", () => {
    renderPanel({ width: 520, onWidthChange: vi.fn() });

    expect(screen.getByLabelText("Assistant")).toHaveStyle({ width: "520px" });
  });

  it("reports a width derived from the pointer, not a delta", () => {
    // Deltas accumulate rounding drift over a long drag and desynchronise from
    // the cursor. 1600 - 1100 = 500.
    const onWidthChange = vi.fn();
    renderPanel({ width: 360, onWidthChange });
    const handle = screen.getByRole("separator");

    pressPrimary(handle);
    movePointerTo(handle, 1100);

    expect(onWidthChange).toHaveBeenCalledWith(500);
  });

  it("ignores pointer movement that is not part of a drag", () => {
    const onWidthChange = vi.fn();
    renderPanel({ width: 360, onWidthChange });

    movePointerTo(screen.getByRole("separator"), 1100);

    expect(onWidthChange).not.toHaveBeenCalled();
  });

  it("does not start a drag on a secondary button", () => {
    // Right-click opens a context menu; capturing there strands the handle in a
    // dragging state the user never asked for.
    const onWidthChange = vi.fn();
    renderPanel({ width: 360, onWidthChange });
    const handle = screen.getByRole("separator");

    pressSecondary(handle);
    movePointerTo(handle, 1100);

    expect(onWidthChange).not.toHaveBeenCalled();
  });

  it("stops reporting once the pointer is released", () => {
    const onWidthChange = vi.fn();
    renderPanel({ width: 360, onWidthChange });
    const handle = screen.getByRole("separator");

    pressPrimary(handle);
    fireEvent.pointerUp(handle, { pointerId: 1 });
    movePointerTo(handle, 900);

    expect(onWidthChange).not.toHaveBeenCalled();
  });

  it("widens on ArrowLeft, because a right-docked panel grows leftwards", () => {
    const onWidthChange = vi.fn();
    renderPanel({ width: 360, onWidthChange });

    fireEvent.keyDown(screen.getByRole("separator"), { key: "ArrowLeft" });

    expect(onWidthChange).toHaveBeenCalledWith(384);
  });

  it("narrows on ArrowRight", () => {
    const onWidthChange = vi.fn();
    renderPanel({ width: 360, onWidthChange });

    fireEvent.keyDown(screen.getByRole("separator"), { key: "ArrowRight" });

    expect(onWidthChange).toHaveBeenCalledWith(336);
  });

  it("restores the default on double-click and on Home", () => {
    const onWidthReset = vi.fn();
    renderPanel({ width: 720, onWidthChange: vi.fn(), onWidthReset });
    const handle = screen.getByRole("separator");

    fireEvent.doubleClick(handle);
    fireEvent.keyDown(handle, { key: "Home" });

    expect(onWidthReset).toHaveBeenCalledTimes(2);
  });

  it("exposes the current width to assistive technology", () => {
    renderPanel({ width: 480, onWidthChange: vi.fn() });
    const handle = screen.getByRole("separator");

    expect(handle).toHaveAttribute("aria-valuenow", "480");
    expect(handle).toHaveAttribute("aria-orientation", "vertical");
    expect(handle).toHaveAttribute("tabindex", "0");
  });

  it("hides the handle while the panel is collapsed", () => {
    // There is no width to drag when the panel is a 44px rail, and a handle
    // there would resize something the user cannot see.
    renderPanel({ collapsed: true, width: 480, onWidthChange: vi.fn() });

    expect(screen.queryByRole("separator")).toBeNull();
  });
});

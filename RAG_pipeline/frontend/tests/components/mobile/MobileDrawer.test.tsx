import { describe, it, expect, vi } from "vitest";
import React from "react";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { MobileDrawer } from "@/components/mobile/MobileDrawer";

describe("MobileDrawer", () => {
  it("renders nothing when closed", () => {
    const onClose = vi.fn();
    render(
      React.createElement(
        MobileDrawer,
        { open: false, onClose, children: React.createElement("div", { "data-testid": "content" }, "Hi") },
      ),
    );
    expect(screen.queryByTestId("content")).toBeNull();
  });

  it("renders children when open", () => {
    const onClose = vi.fn();
    render(
      React.createElement(
        MobileDrawer,
        { open: true, onClose, children: React.createElement("div", { "data-testid": "content" }, "Hi") },
      ),
    );
    expect(screen.getByTestId("content")).toBeTruthy();
  });

  it("closes when the overlay is clicked", () => {
    const onClose = vi.fn();
    const { container } = render(
      React.createElement(
        MobileDrawer,
        { open: true, onClose, children: React.createElement("div", null, "Hi") },
      ),
    );
    const overlay = container.querySelector('[aria-hidden="true"]') as HTMLElement;
    fireEvent.click(overlay);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on Escape", () => {
    const onClose = vi.fn();
    render(
      React.createElement(
        MobileDrawer,
        { open: true, onClose, children: React.createElement("div", null, "Hi") },
      ),
    );
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on a leftward swipe past the threshold", () => {
    const onClose = vi.fn();
    const { container } = render(
      React.createElement(
        MobileDrawer,
        { open: true, onClose, children: React.createElement("div", null, "Hi") },
      ),
    );
    const dialog = container.querySelector('[role="dialog"]') as HTMLElement;
    const panel = dialog.querySelector(':scope > div:nth-child(2)') as HTMLElement;

    act(() => {
      fireEvent.mouseDown(panel, { clientX: 200, clientY: 100 });
      fireEvent.mouseMove(panel, { clientX: 100, clientY: 100 });
      fireEvent.mouseUp(panel, { clientX: 0, clientY: 100 });
    });
    expect(onClose).toHaveBeenCalled();
  });

  it("does not close on a small swipe", () => {
    const onClose = vi.fn();
    const { container } = render(
      React.createElement(
        MobileDrawer,
        { open: true, onClose, children: React.createElement("div", null, "Hi") },
      ),
    );
    const dialog = container.querySelector('[role="dialog"]') as HTMLElement;
    const panel = dialog.querySelector(':scope > div:nth-child(2)') as HTMLElement;

    act(() => {
      fireEvent.mouseDown(panel, { clientX: 200, clientY: 100 });
      fireEvent.mouseMove(panel, { clientX: 180, clientY: 100 });
      fireEvent.mouseUp(panel, { clientX: 170, clientY: 100 });
    });
    expect(onClose).not.toHaveBeenCalled();
  });
});

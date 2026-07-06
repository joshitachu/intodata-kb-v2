import { describe, it, expect, vi } from "vitest";
import React from "react";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { MobileBottomSheet } from "@/components/mobile/MobileBottomSheet";

describe("MobileBottomSheet", () => {
  it("renders nothing when closed", () => {
    const onClose = vi.fn();
    render(
      <MobileBottomSheet open={false} onClose={onClose}>
        <div data-testid="content">Hi</div>
      </MobileBottomSheet>,
    );
    expect(screen.queryByTestId("content")).toBeNull();
  });

  it("renders children when open", () => {
    render(
      <MobileBottomSheet open={true} onClose={vi.fn()}>
        <div data-testid="content">Hi</div>
      </MobileBottomSheet>,
    );
    expect(screen.getByTestId("content")).toBeTruthy();
  });

  it("closes when the overlay is clicked", () => {
    const onClose = vi.fn();
    const { container } = render(
      <MobileBottomSheet open={true} onClose={onClose}>
        <div>Hi</div>
      </MobileBottomSheet>,
    );
    const overlay = container.querySelector('[aria-hidden="true"]') as HTMLElement;
    fireEvent.click(overlay);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on Escape", () => {
    const onClose = vi.fn();
    render(
      <MobileBottomSheet open={true} onClose={onClose}>
        <div>Hi</div>
      </MobileBottomSheet>,
    );
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on a downward swipe past the threshold", () => {
    const onClose = vi.fn();
    const { container } = render(
      <MobileBottomSheet open={true} onClose={onClose}>
        <div>Hi</div>
      </MobileBottomSheet>,
    );
    const dialog = container.querySelector('[role="dialog"]') as HTMLElement;
    const sheet = dialog.querySelector(':scope > div:nth-child(2)') as HTMLElement;

    act(() => {
      fireEvent.mouseDown(sheet, { clientX: 200, clientY: 100 });
      fireEvent.mouseMove(sheet, { clientX: 200, clientY: 200 });
      fireEvent.mouseUp(sheet, { clientX: 200, clientY: 300 });
    });
    expect(onClose).toHaveBeenCalled();
  });
});

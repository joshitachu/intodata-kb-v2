import { describe, it, expect, vi, beforeEach } from "vitest";
import React, { type ReactNode } from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { MobileTopBar } from "@/components/mobile/MobileTopBar";

const mockPush = vi.fn();
const mockBack = vi.fn();
const historyLength = { value: 2 };

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mockPush,
    back: mockBack,
    refresh: vi.fn(),
  }),
}));

Object.defineProperty(window, "history", {
  configurable: true,
  value: { length: historyLength.value },
});

describe("MobileTopBar", () => {
  beforeEach(() => {
    mockPush.mockClear();
    mockBack.mockClear();
  });

  function renderBar(props: Partial<React.ComponentProps<typeof MobileTopBar>> = {}) {
    return render(
      React.createElement(
        MobileTopBar,
        { showMenuButton: true, onMenuClick: vi.fn(), ...props },
      ),
    );
  }

  it("renders the title", () => {
    renderBar({ title: "Documents" });
    expect(screen.getByText("Documents")).toBeTruthy();
  });

  it("renders a menu button when showMenuButton is true", () => {
    const onMenu = vi.fn();
    renderBar({ onMenuClick: onMenu });
    const button = screen.getByLabelText("Open menu");
    fireEvent.click(button);
    expect(onMenu).toHaveBeenCalledTimes(1);
  });

  it("renders a back button when onBack is provided", () => {
    const onBack = vi.fn();
    renderBar({ onBack, title: "Settings", showMenuButton: false });
    const button = screen.getByLabelText("Go back");
    fireEvent.click(button);
    expect(onBack).toHaveBeenCalledTimes(1);
    expect(mockBack).not.toHaveBeenCalled();
  });

  it("falls back to router.back() when no onBack is given and history has entries", () => {
    renderBar({ backHref: "/settings", title: "Profile", showMenuButton: false });
    fireEvent.click(screen.getByLabelText("Go back"));
    expect(mockBack).toHaveBeenCalledTimes(1);
    expect(mockPush).not.toHaveBeenCalled();
  });

  it("uses backHref when history is empty", () => {
    historyLength.value = 1;
    Object.defineProperty(window, "history", {
      configurable: true,
      value: { length: 1 },
    });
    renderBar({ backHref: "/settings", title: "Profile", showMenuButton: false });
    fireEvent.click(screen.getByLabelText("Go back"));
    expect(mockPush).toHaveBeenCalledWith("/settings");
    historyLength.value = 2;
    Object.defineProperty(window, "history", {
      configurable: true,
      value: { length: 2 },
    });
  });

  it("renders the rightAction slot", () => {
    renderBar({
      rightAction: React.createElement("button", { "aria-label": "Refresh" }, "↻"),
    });
    expect(screen.getByLabelText("Refresh")).toBeTruthy();
  });

  it("prefers leftAction over the back / menu buttons", () => {
    renderBar({
      showMenuButton: true,
      onMenuClick: vi.fn(),
      leftAction: React.createElement("button", { "aria-label": "Custom" }, "x"),
    });
    expect(screen.getByLabelText("Custom")).toBeTruthy();
    expect(screen.queryByLabelText("Open menu")).toBeNull();
  });
});

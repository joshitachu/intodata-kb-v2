import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";

const mockPush = vi.fn();
const mockDelete = vi.fn();
const mockSetActive = vi.fn();

const mockState = {
  conversations: [] as Array<{ id: number; title: string; updated_at: string }>,
  isLoading: false,
  setActiveConversationId: mockSetActive,
  deleteConversation: mockDelete,
};

vi.mock("@/contexts/ChatContext", () => ({
  useChatContextSafe: () => mockState,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, back: vi.fn() }),
}));

import { MobileChatList } from "@/components/mobile/MobileChatList";

describe("MobileChatList", () => {
  beforeEach(() => {
    mockPush.mockClear();
    mockSetActive.mockClear();
    mockDelete.mockClear();
    mockState.conversations = [];
    mockState.isLoading = false;
  });

  function wrap(node: React.ReactNode) {
    return render(<>{node}</>);
  }

  it("renders the New Chat button", () => {
    wrap(<MobileChatList />);
    expect(screen.getByText("New Chat")).toBeTruthy();
  });

  it("shows an empty state when there are no conversations", () => {
    wrap(<MobileChatList />);
    expect(screen.getByText(/No conversations yet/)).toBeTruthy();
  });

  it("renders conversations sorted by updated_at desc", () => {
    mockState.conversations = [
      { id: 1, title: "older", updated_at: "2026-01-01T00:00:00Z" },
      { id: 2, title: "newer", updated_at: "2026-07-01T00:00:00Z" },
    ];
    wrap(<MobileChatList />);
    const items = screen.getAllByRole("button", { name: /newer|older/i });
    expect(items[0].textContent).toContain("newer");
  });

  it("routes to /?c={id} on tap", () => {
    mockState.conversations = [
      { id: 7, title: "alpha", updated_at: "2026-07-01T00:00:00Z" },
    ];
    wrap(<MobileChatList />);
    fireEvent.click(screen.getByText("alpha"));
    expect(mockSetActive).not.toHaveBeenCalled();
    expect(mockPush).toHaveBeenCalledWith("/?c=7");
  });

  it("routes to /?c=new when New Chat is tapped", () => {
    wrap(<MobileChatList />);
    fireEvent.click(screen.getByText("New Chat"));
    expect(mockSetActive).not.toHaveBeenCalled();
    expect(mockPush).toHaveBeenCalledWith("/?c=new");
  });
});

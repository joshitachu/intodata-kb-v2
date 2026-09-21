import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";

const mockPush = vi.fn();
const mockDelete = vi.fn();
const mockSetActive = vi.fn();
const mockSetLaneFilter = vi.fn();

type MockConversation = {
  id: number;
  title: string;
  updated_at: string;
  lane?: "v1" | "v2";
};

// `visibleConversations` is derived rather than a second fixture field: the
// provider guarantees it is `conversations` narrowed by `laneFilter`, and a
// mock that let the two disagree would test a state that cannot happen.
const mockState = {
  conversations: [] as MockConversation[],
  get visibleConversations(): MockConversation[] {
    return mockState.conversations.filter(
      (c) =>
        mockState.laneFilter === "all" ||
        (c.lane ?? "v1") === mockState.laneFilter,
    );
  },
  laneFilter: "all" as "all" | "v1" | "v2",
  setLaneFilter: mockSetLaneFilter,
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
    mockSetLaneFilter.mockClear();
    mockState.conversations = [];
    mockState.laneFilter = "all";
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

  it("badges each row with its lane", () => {
    mockState.conversations = [
      { id: 1, title: "harness", updated_at: "2026-07-01T00:00:00Z", lane: "v1" },
      { id: 2, title: "opencode", updated_at: "2026-07-02T00:00:00Z", lane: "v2" },
    ];
    wrap(<MobileChatList />);
    expect(screen.getByLabelText("V1 - production harness")).toBeTruthy();
    expect(screen.getByLabelText("V2 - OpenCode lane")).toBeTruthy();
  });

  it("narrows to one lane through the same filter as the sidebar", () => {
    mockState.conversations = [
      { id: 1, title: "harness", updated_at: "2026-07-01T00:00:00Z", lane: "v1" },
      { id: 2, title: "opencode", updated_at: "2026-07-02T00:00:00Z", lane: "v2" },
    ];
    mockState.laneFilter = "v2";
    wrap(<MobileChatList />);
    expect(screen.getByText("opencode")).toBeTruthy();
    expect(screen.queryByText("harness")).toBeNull();
    expect(mockState.visibleConversations).toHaveLength(1);
  });

  it("says the lane is empty rather than the account", () => {
    mockState.conversations = [
      { id: 1, title: "harness", updated_at: "2026-07-01T00:00:00Z", lane: "v1" },
    ];
    mockState.laneFilter = "v2";
    wrap(<MobileChatList />);
    expect(screen.getByText("No V2 conversations.")).toBeTruthy();
  });
});

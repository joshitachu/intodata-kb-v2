import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, within } from "@testing-library/react";
import type { ConversationDetailResponse } from "@/types";

/**
 * Where the lane control sits, as rendered by the real composer.
 *
 * `ChatLaneSwitch.test.tsx` covers what the control offers; this covers its
 * placement, which is the thing that was wrong. The lane lived in the composer
 * toolbar as a bare `[V1][V2]` pill among the per-turn settings, where the
 * heaviest and least reversible choice on the surface read as the lightest.
 * Asserting through `ChatWindow` rather than composing `ChatInput` by hand is
 * the point: a test that builds its own composer would pass while the app put
 * the row back in the toolbar.
 */

const chatContext = {
  activeLane: "v1" as "v1" | "v2",
  selectedLane: "v1" as "v1" | "v2",
  isLaneLocked: false,
  setSelectedLane: vi.fn(),
  selectedProject: "",
  setSelectedProject: vi.fn(),
};
vi.mock("@/contexts/ChatContext", () => ({
  useChatContext: () => chatContext,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock("@/hooks/useUserProjects", () => ({
  useUserProjects: () => ({ data: { projects: [] } }),
}));
vi.mock("@/hooks/useSlashCommands", () => ({
  useSlashCommands: () => ({ commands: [] }),
  // The composer's slash palette reads this from the same module.
  filterSlashCommands: () => [],
}));

// Stubs for the toolbar's other occupants and the transcript: this suite is
// about layout, and each of these otherwise drags in its own query client.
vi.mock("@/components/RagProjectSelector", () => ({
  RagProjectSelector: () => <div data-testid="project-selector">All Projects</div>,
}));
vi.mock("@/components/ProjectSelector", () => ({
  ProjectSelector: () => <div data-testid="project-selector">All Projects</div>,
}));
vi.mock("@/components/RuntimeStatusIndicator", () => ({
  RuntimeStatusIndicator: () => null,
}));
vi.mock("@/components/MessageList", () => ({ MessageList: () => null }));
vi.mock("@/components/AgentPlanCard", () => ({ AgentPlanCard: () => null }));
vi.mock("@/components/onboarding/OnboardingLauncher", () => ({
  OnboardingLauncher: () => null,
}));

import { ChatWindow } from "@/components/ChatWindow";

const OPEN_THREAD: ConversationDetailResponse = {
  id: 9,
  title: "opencode thread",
  lane: "v2",
  messages: [],
  origin_surface: "chat",
  visible_in_chat: true,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

function renderChatWindow(conversation: ConversationDetailResponse | null) {
  return render(
    <ChatWindow
      conversation={conversation}
      isLoading={false}
      isSending={false}
      onSendMessage={vi.fn()}
      mode="chat"
      onModeChange={vi.fn()}
      selectedProject=""
    />,
  );
}

function composer(): HTMLElement {
  const node = document.querySelector<HTMLElement>('[data-tour="chat-input"]');
  if (!node) throw new Error("composer not rendered");
  return node;
}

function toolbar(): HTMLElement {
  const node = document.querySelector<HTMLElement>('[data-tour="chat-toolbar"]');
  if (!node) throw new Error("composer toolbar not rendered");
  return node;
}

describe("the lane row and the composer", () => {
  beforeEach(() => {
    chatContext.activeLane = "v1";
    chatContext.selectedLane = "v1";
    chatContext.isLaneLocked = false;
    chatContext.setSelectedLane.mockClear();
  });

  it("puts the lane choice above the composer on a new thread, not in its toolbar", () => {
    renderChatWindow(null);

    const row = screen.getByRole("radiogroup", { name: /answering engine/i });
    const header = screen.getByTestId("composer-header");
    const input = screen.getByPlaceholderText(/ask about your documents/i);

    expect(header).toContainElement(row);
    expect(composer()).toContainElement(row);
    expect(toolbar()).not.toContainElement(row);
    // Above, not merely outside: the row precedes the input in the document.
    expect(
      row.compareDocumentPosition(input) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("leaves the toolbar's own controls where they were", () => {
    renderChatWindow(null);

    const mode = within(toolbar()).getByRole("radiogroup", { name: /chat mode/i });
    expect(within(mode).getByRole("radio", { name: /chat/i })).toBeInTheDocument();
    expect(within(mode).getByRole("radio", { name: /deep dive/i })).toBeInTheDocument();
    expect(within(toolbar()).getByTestId("project-selector")).toBeInTheDocument();
  });

  it("replaces the row with the compact locked indicator on an open thread", () => {
    chatContext.isLaneLocked = true;
    chatContext.activeLane = "v2";

    renderChatWindow(OPEN_THREAD);

    expect(
      screen.queryByRole("radiogroup", { name: /answering engine/i }),
    ).toBeNull();
    expect(screen.queryByText(/answering engine/i)).toBeNull();

    const locked = screen.getByTestId("lane-switch-locked");
    expect(locked).toHaveAttribute("data-lane", "v2");
    expect(screen.getByTestId("composer-header")).toContainElement(locked);
  });

  it("shows no lane-health line on either state", () => {
    const health = /lane ready|lane unreachable|lane status unknown|checking v2/i;

    const { unmount } = renderChatWindow(null);
    expect(screen.queryByText(health)).toBeNull();
    unmount();

    chatContext.isLaneLocked = true;
    chatContext.activeLane = "v2";
    renderChatWindow(OPEN_THREAD);
    expect(screen.queryByText(health)).toBeNull();
  });
});

import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { laneMatchesFilter, type LaneFilter } from "@/components/chat/lane";
import type { ChatLane, ConversationResponse } from "@/types";

/**
 * `window.localStorage` is absent in this runtime (node reports "localStorage
 * is not available because --localstorage-file was not provided"), and the
 * sidebar reads it to recover its expanded/collapsed state. Shimmed here, in
 * this file, rather than in the shared setup: several unrelated suites already
 * fail on the same missing global, and quietly fixing those from a lane change
 * would hide what this suite is actually covering.
 */
if (typeof window !== "undefined" && !window.localStorage) {
  const store = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => void store.set(key, String(value)),
      removeItem: (key: string) => void store.delete(key),
      clear: () => store.clear(),
      key: (index: number) => Array.from(store.keys())[index] ?? null,
      get length() {
        return store.size;
      },
    },
  });
}

const mockPush = vi.fn();
const mockReplace = vi.fn();
const mockSetLaneFilter = vi.fn();

function conversation(
  id: number,
  title: string,
  lane: ChatLane,
): ConversationResponse {
  return {
    id,
    title,
    lane,
    message_count: 2,
    origin_surface: "chat",
    visible_in_chat: true,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
  };
}

// `visibleConversations` is derived from the same rule the provider uses rather
// than hand-listed, so the fixture cannot describe a state the provider will
// never produce.
const chat = {
  conversations: [] as ConversationResponse[],
  get visibleConversations(): ConversationResponse[] {
    return chat.conversations.filter((c) =>
      laneMatchesFilter(c.lane, chat.laneFilter),
    );
  },
  laneFilter: "all" as LaneFilter,
  setLaneFilter: mockSetLaneFilter,
  isLoading: false,
  activeConversationId: null as number | null,
  deleteConversation: vi.fn(),
};

vi.mock("@/contexts/ChatContext", () => ({
  useChatContext: () => chat,
}));
vi.mock("@/components/AuthProvider", () => ({
  useAuthContext: () => ({ user: { username: "tester" }, logout: vi.fn() }),
}));
vi.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    hasAnyAdminPermission: false,
    hasPermission: () => false,
    isOwner: false,
  }),
}));
vi.mock("@/contexts/OnboardingContext", () => ({
  useOnboardingContext: () => ({ startTour: vi.fn() }),
}));
vi.mock("@/components/notifications/NotificationBell", () => ({
  NotificationBell: () => null,
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ push: mockPush, replace: mockReplace }),
}));
vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...rest
  }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import { Sidebar } from "@/components/Sidebar";
import LabsChatAliasPage from "@/app/labs/chat/page";

/** The sidebar only draws the thread list when it is expanded. */
function renderExpandedSidebar() {
  window.localStorage.setItem("sidebar-expanded", "true");
  return render(<Sidebar />);
}

function laneFilterOption(name: "All" | "V1" | "V2"): HTMLElement {
  const group = screen.getByRole("radiogroup", {
    name: /filter conversations by lane/i,
  });
  return within(group).getByRole("radio", { name });
}

describe("conversation list -- lanes", () => {
  beforeEach(() => {
    window.localStorage.clear();
    mockPush.mockClear();
    mockSetLaneFilter.mockClear();
    chat.conversations = [];
    chat.laneFilter = "all";
    chat.isLoading = false;
    chat.activeConversationId = null;
  });

  it("shows both lanes in one list, each row badged", () => {
    chat.conversations = [
      conversation(1, "harness thread", "v1"),
      conversation(2, "opencode thread", "v2"),
    ];

    renderExpandedSidebar();

    // One list, not two: a V2 thread is no longer hidden behind a second page.
    expect(screen.getByText("harness thread")).toBeInTheDocument();
    expect(screen.getByText("opencode thread")).toBeInTheDocument();

    expect(
      screen.getByLabelText("V1 - production harness"),
    ).toHaveTextContent("V1");
    expect(screen.getByLabelText("V2 - OpenCode lane")).toHaveTextContent("V2");
  });

  it("offers an All / V1 / V2 filter and reports the choice", () => {
    chat.conversations = [conversation(1, "harness thread", "v1")];

    renderExpandedSidebar();

    expect(laneFilterOption("All")).toHaveAttribute("aria-checked", "true");

    fireEvent.click(laneFilterOption("V2"));

    expect(mockSetLaneFilter).toHaveBeenCalledWith("v2");
  });

  it("narrows the list to the filtered lane", () => {
    chat.conversations = [
      conversation(1, "harness thread", "v1"),
      conversation(2, "opencode thread", "v2"),
    ];
    chat.laneFilter = "v2";

    renderExpandedSidebar();

    expect(screen.getByText("opencode thread")).toBeInTheDocument();
    expect(screen.queryByText("harness thread")).toBeNull();
    expect(laneFilterOption("V2")).toHaveAttribute("aria-checked", "true");
  });

  it("distinguishes an empty lane from an empty account", () => {
    chat.conversations = [conversation(1, "harness thread", "v1")];
    chat.laneFilter = "v2";

    const { unmount } = renderExpandedSidebar();
    expect(screen.getByText("No V2 conversations.")).toBeInTheDocument();
    unmount();

    chat.conversations = [];
    renderExpandedSidebar();
    expect(screen.getByText("No conversations yet.")).toBeInTheDocument();
  });

  it("renders the loading state before the list resolves", () => {
    chat.isLoading = true;

    const { container } = renderExpandedSidebar();

    expect(screen.queryByText("No conversations yet.")).toBeNull();
    expect(container.querySelector(".animate-spin")).not.toBeNull();
  });
});

describe("/labs/chat", () => {
  beforeEach(() => {
    mockReplace.mockClear();
  });

  it("is an alias onto the one chat surface, pre-set to V2", () => {
    render(<LabsChatAliasPage />);

    // Not a second live implementation, and not a bare `/` either: the links
    // that pointed here were pointing at the OpenCode lane.
    expect(mockReplace).toHaveBeenCalledWith("/?c=new&lane=v2");
    expect(screen.getByRole("link", { name: /go to chat/i })).toHaveAttribute(
      "href",
      "/?c=new&lane=v2",
    );
  });
});

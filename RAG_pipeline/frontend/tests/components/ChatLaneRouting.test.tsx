import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  ChatProvider,
  useChatContext,
} from "@/contexts/ChatContext";
import { ConversationResponseSchema } from "@/types";

/**
 * Which backend a thread's traffic goes to, decided by its lane.
 *
 * This is the load-bearing half of collapsing `/labs/chat` into the main chat.
 * There used to be two mounted providers, and the lane was whichever one you
 * happened to be rendered under; now one provider derives the base from the
 * lane, so these assertions are what stops a V2 thread being answered by the
 * production harness (or the reverse) with nothing in the UI to show for it.
 */

interface RecordedCall {
  url: string;
  method: string;
  body: unknown;
}

let calls: RecordedCall[] = [];

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

/** A stream that ends immediately: the URL is the fact under test, not the SSE. */
function emptyStreamResponse(): Response {
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: async () => ({ done: true, value: undefined }),
        releaseLock: () => {},
        cancel: async () => {},
      }),
    },
    json: async () => ({}),
    text: async () => "",
  } as unknown as Response;
}

const LIST = {
  conversations: [
    {
      id: 1,
      title: "harness thread",
      lane: "v1",
      message_count: 1,
      origin_surface: "chat",
      visible_in_chat: true,
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
    },
    {
      id: 9,
      title: "opencode thread",
      lane: "v2",
      message_count: 1,
      origin_surface: "chat",
      visible_in_chat: true,
      created_at: "2026-09-02T00:00:00Z",
      updated_at: "2026-09-02T00:00:00Z",
    },
  ],
  total: 2,
};

function detail(id: number, lane: "v1" | "v2") {
  return {
    id,
    title: `thread ${id}`,
    lane,
    messages: [],
    origin_surface: "chat",
    visible_in_chat: true,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
  };
}

/**
 * The lane the backend would report for a thread id, matching the real routes:
 * `/api/v1/conversations/{id}` is ownership-gated only and serves both lanes,
 * while `/api/v1/labs/conversations/{id}` is lane-gated and 404s a V1 thread.
 */
function laneOf(id: number): "v1" | "v2" {
  const listed = LIST.conversations.find((c) => c.id === id);
  if (listed) return listed.lane === "v2" ? "v2" : "v1";
  return id === 101 ? "v2" : "v1";
}

function urlsMatching(pattern: RegExp): string[] {
  return calls.filter((c) => pattern.test(c.url)).map((c) => c.url);
}

function Probe() {
  const chat = useChatContext();
  return (
    <div>
      <span data-testid="active-lane">{chat.activeLane}</span>
      <span data-testid="selected-lane">{chat.selectedLane}</span>
      <span data-testid="locked">{String(chat.isLaneLocked)}</span>
      <span data-testid="visible-count">{chat.visibleConversations.length}</span>
      <span data-testid="all-count">{chat.conversations.length}</span>
      <button onClick={() => chat.setSelectedLane("v2")}>pick v2</button>
      <button onClick={() => chat.setSelectedLane("v1")}>pick v1</button>
      <button onClick={() => chat.setActiveConversationId(9)}>open v2 thread</button>
      <button onClick={() => chat.setActiveConversationId(1)}>open v1 thread</button>
      <button onClick={() => chat.setLaneFilter("v2")}>filter v2</button>
      <button onClick={() => void chat.handleSendMessage({ query: "hello" })}>
        send
      </button>
    </div>
  );
}

function renderChat() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ChatProvider>
        <Probe />
      </ChatProvider>
    </QueryClientProvider>,
  );
}

describe("chat lane routing", () => {
  beforeEach(() => {
    calls = [];
    // `handleSendMessage` stamps a client turn id; not every runtime exposes
    // `randomUUID` on the jsdom crypto object.
    if (typeof globalThis.crypto?.randomUUID !== "function") {
      Object.defineProperty(globalThis, "crypto", {
        configurable: true,
        value: { ...globalThis.crypto, randomUUID: () => "test-turn-id" },
      });
    }

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: unknown, init?: RequestInit) => {
        const url = String(input);
        const method = init?.method ?? "GET";
        let body: unknown = null;
        if (typeof init?.body === "string") {
          try {
            body = JSON.parse(init.body) as unknown;
          } catch {
            body = init.body;
          }
        }
        calls.push({ url, method, body });

        if (url.includes("/messages/stream")) return emptyStreamResponse();
        if (method === "POST" && url === "/api/labs/conversations") {
          return jsonResponse({ ...detail(101, "v2"), message_count: 0 }, 201);
        }
        if (method === "POST" && url === "/api/conversations") {
          return jsonResponse({ ...detail(100, "v1"), message_count: 0 }, 201);
        }
        if (url === "/api/conversations") return jsonResponse(LIST);
        if (url === "/api/labs/conversations") {
          // Nothing may read the single-lane list: it would hide half the
          // threads behind the switch.
          return jsonResponse({ conversations: [], total: 0 });
        }
        const labsDetail = /^\/api\/labs\/conversations\/(\d+)$/.exec(url);
        if (labsDetail) {
          const id = Number(labsDetail[1]);
          return laneOf(id) === "v2"
            ? jsonResponse(detail(id, "v2"))
            : jsonResponse({ error: "Conversation not found" }, 404);
        }
        const prodDetail = /^\/api\/conversations\/(\d+)$/.exec(url);
        if (prodDetail) {
          const id = Number(prodDetail[1]);
          return jsonResponse(detail(id, laneOf(id)));
        }
        return jsonResponse({ error: `unexpected ${method} ${url}` }, 404);
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reads one cross-lane list, never the single-lane one", async () => {
    renderChat();

    await waitFor(() =>
      expect(screen.getByTestId("all-count")).toHaveTextContent("2"),
    );
    expect(urlsMatching(/^\/api\/conversations$/)).toContain("/api/conversations");
    expect(urlsMatching(/^\/api\/labs\/conversations$/)).toHaveLength(0);
  });

  it("defaults a new thread to V1 and routes its turn to /api", async () => {
    renderChat();
    await waitFor(() =>
      expect(screen.getByTestId("all-count")).toHaveTextContent("2"),
    );
    expect(screen.getByTestId("selected-lane")).toHaveTextContent("v1");

    fireEvent.click(screen.getByText("send"));

    await waitFor(() =>
      expect(urlsMatching(/\/messages\/stream/)).toHaveLength(1),
    );
    const create = calls.find(
      (c) => c.method === "POST" && c.url.endsWith("/conversations"),
    );
    expect(create?.url).toBe("/api/conversations");
    expect(create?.body).toMatchObject({ lane: "v1" });
    expect(urlsMatching(/\/messages\/stream/)[0]).toMatch(
      /^\/api\/conversations\/100\/messages\/stream/,
    );
  });

  it("creates and streams a V2 thread against /api/labs once the switch is flipped", async () => {
    renderChat();
    await waitFor(() =>
      expect(screen.getByTestId("all-count")).toHaveTextContent("2"),
    );

    fireEvent.click(screen.getByText("pick v2"));
    expect(screen.getByTestId("selected-lane")).toHaveTextContent("v2");
    expect(screen.getByTestId("active-lane")).toHaveTextContent("v2");

    fireEvent.click(screen.getByText("send"));

    await waitFor(() =>
      expect(urlsMatching(/\/messages\/stream/)).toHaveLength(1),
    );
    const create = calls.find(
      (c) => c.method === "POST" && c.url.endsWith("/conversations"),
    );
    expect(create?.url).toBe("/api/labs/conversations");
    // The lane travels in the body as well, so the stored lane cannot disagree
    // with the one the user picked.
    expect(create?.body).toMatchObject({ lane: "v2" });
    expect(urlsMatching(/\/messages\/stream/)[0]).toMatch(
      /^\/api\/labs\/conversations\/101\/messages\/stream/,
    );
  });

  it("takes an open thread's lane from the thread, not from the switch", async () => {
    renderChat();
    await waitFor(() =>
      expect(screen.getByTestId("all-count")).toHaveTextContent("2"),
    );

    fireEvent.click(screen.getByText("open v2 thread"));

    await waitFor(() =>
      expect(screen.getByTestId("active-lane")).toHaveTextContent("v2"),
    );
    // The switch itself is untouched -- it only ever describes the next thread.
    expect(screen.getByTestId("selected-lane")).toHaveTextContent("v1");
    expect(screen.getByTestId("locked")).toHaveTextContent("true");
    await waitFor(() =>
      expect(urlsMatching(/^\/api\/labs\/conversations\/9$/)).toHaveLength(1),
    );
    expect(urlsMatching(/^\/api\/conversations\/9$/)).toHaveLength(0);
  });

  it("keeps a V1 thread on /api even while the switch is set to V2", async () => {
    renderChat();
    await waitFor(() =>
      expect(screen.getByTestId("all-count")).toHaveTextContent("2"),
    );

    fireEvent.click(screen.getByText("pick v2"));
    fireEvent.click(screen.getByText("open v1 thread"));

    await waitFor(() =>
      expect(screen.getByTestId("active-lane")).toHaveTextContent("v1"),
    );
    await waitFor(() =>
      expect(urlsMatching(/^\/api\/conversations\/1$/)).toHaveLength(1),
    );
    expect(urlsMatching(/^\/api\/labs\/conversations\/1$/)).toHaveLength(0);
  });

  it("settles a deep-linked thread's lane from its own record, without thrashing", async () => {
    // The list is the authority on a thread's lane, and a `?c=9` deep link does
    // not wait for it. Hold the list back so the thread is opened with nothing
    // yet knowing which lane it belongs to.
    let releaseList: () => void = () => {};
    const listHeld = new Promise<void>((resolve) => {
      releaseList = resolve;
    });
    const baseFetch = globalThis.fetch;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: unknown, init?: RequestInit) => {
        if (String(input) === "/api/conversations" && (init?.method ?? "GET") === "GET") {
          await listHeld;
        }
        return baseFetch(input as RequestInfo, init);
      }),
    );

    renderChat();
    fireEvent.click(screen.getByText("open v2 thread"));

    // The unresolved guess is the production base on purpose: it serves both
    // lanes, so the read succeeds and its `lane` is what corrects the guess.
    await waitFor(() =>
      expect(urlsMatching(/^\/api\/conversations\/9$/)).toHaveLength(1),
    );
    await waitFor(() =>
      expect(screen.getByTestId("active-lane")).toHaveTextContent("v2"),
    );
    await waitFor(() =>
      expect(urlsMatching(/^\/api\/labs\/conversations\/9$/)).toHaveLength(1),
    );

    releaseList();
    await waitFor(() =>
      expect(screen.getByTestId("all-count")).toHaveTextContent("2"),
    );

    // Settled, not oscillating: the corrected lane is sticky, so the base does
    // not fall back to the default while a query key is in flight.
    expect(screen.getByTestId("active-lane")).toHaveTextContent("v2");
    expect(urlsMatching(/^\/api\/conversations\/9$/)).toHaveLength(1);
    expect(urlsMatching(/^\/api\/labs\/conversations\/9$/)).toHaveLength(1);
  });

  it("honours the lane the /labs/chat alias asks for", async () => {
    window.history.replaceState({}, "", "/?c=new&lane=v2");
    try {
      renderChat();

      await waitFor(() =>
        expect(screen.getByTestId("selected-lane")).toHaveTextContent("v2"),
      );
      expect(screen.getByTestId("active-lane")).toHaveTextContent("v2");
      // Seeded, not pinned: the switch owns the lane from here on.
      fireEvent.click(screen.getByText("pick v1"));
      expect(screen.getByTestId("selected-lane")).toHaveTextContent("v1");
    } finally {
      window.history.replaceState({}, "", "/");
    }
  });

  it("narrows the visible list by lane without dropping threads from the list", async () => {
    renderChat();
    await waitFor(() =>
      expect(screen.getByTestId("visible-count")).toHaveTextContent("2"),
    );

    fireEvent.click(screen.getByText("filter v2"));

    expect(screen.getByTestId("visible-count")).toHaveTextContent("1");
    // Still two: the URL-recovery path in ChatLayout asks whether an id exists,
    // and answering that through the filter would bounce the open thread.
    expect(screen.getByTestId("all-count")).toHaveTextContent("2");
  });
});

describe("lane on the wire", () => {
  it("survives schema validation instead of being stripped", () => {
    // Zod strips undeclared keys silently, which is how a field disappears
    // between the backend and the browser with no error anywhere.
    const parsed = ConversationResponseSchema.parse({
      id: 9,
      title: "opencode thread",
      lane: "v2",
      message_count: 1,
      created_at: "2026-09-02T00:00:00Z",
      updated_at: "2026-09-02T00:00:00Z",
    });
    expect(parsed.lane).toBe("v2");
  });

  it("defaults to V1 for a backend that predates the column", () => {
    const parsed = ConversationResponseSchema.parse({
      id: 1,
      title: null,
      message_count: 0,
      created_at: "2026-09-02T00:00:00Z",
      updated_at: "2026-09-02T00:00:00Z",
    });
    expect(parsed.lane).toBe("v1");
  });
});

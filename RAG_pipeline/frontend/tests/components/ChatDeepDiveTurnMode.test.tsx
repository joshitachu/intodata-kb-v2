import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ChatProvider, useChatContext } from "@/contexts/ChatContext";

/**
 * What the provider actually sends, and what it remembers about it.
 *
 * Disabling the toggle is not enough on its own: the mode is also set by the
 * welcome screen's capability cards, and the lane can be switched *after* Deep
 * Dive was picked while no thread exists. The clamp lives in the provider so
 * there is one answer to "what will this turn run as", and the turn's mode is
 * recorded against the message id the `done` frame reports -- the only moment
 * both halves of that pairing exist in the same place.
 */

interface RecordedCall {
  url: string;
  method: string;
  body: unknown;
}

let calls: RecordedCall[] = [];

/** The assistant message the stubbed turn persists. */
const DONE_MESSAGE_ID = 55;

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

/** A stream that delivers one `done` frame, as a finished turn does. */
function doneStreamResponse(): Response {
  const frame =
    "event: done\n" +
    `data: ${JSON.stringify({ message_id: DONE_MESSAGE_ID, latency_ms: 42 })}\n\n`;
  let sent = false;
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: async () => {
          if (sent) return { done: true, value: undefined };
          sent = true;
          return { done: false, value: new TextEncoder().encode(frame) };
        },
        releaseLock: () => {},
        cancel: async () => {},
      }),
    },
    json: async () => ({}),
    text: async () => frame,
  } as unknown as Response;
}

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

function Probe() {
  const chat = useChatContext();
  return (
    <div>
      <span data-testid="mode">{chat.mode}</span>
      <span data-testid="deep-dive-available">
        {String(chat.isDeepDiveAvailable)}
      </span>
      <span data-testid="in-flight-deep-dive">
        {String(chat.isDeepDiveTurnInFlight)}
      </span>
      <span data-testid="recorded">
        {[...chat.deepDiveMessageIds].join(",")}
      </span>
      <button onClick={() => chat.setSelectedLane("v2")}>pick v2</button>
      <button onClick={() => chat.setSelectedLane("v1")}>pick v1</button>
      <button onClick={() => chat.setMode("deep-dive")}>pick deep dive</button>
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

function streamBody(): Record<string, unknown> {
  const call = calls.find((c) => c.url.includes("/messages/stream"));
  if (!call) throw new Error("no turn was streamed");
  return call.body as Record<string, unknown>;
}

describe("the mode a turn actually runs in", () => {
  beforeEach(() => {
    calls = [];
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

        if (url.includes("/messages/stream")) return doneStreamResponse();
        if (method === "POST" && url === "/api/labs/conversations") {
          return jsonResponse({ ...detail(101, "v2"), message_count: 0 }, 201);
        }
        if (method === "POST" && url === "/api/conversations") {
          return jsonResponse({ ...detail(100, "v1"), message_count: 0 }, 201);
        }
        if (url === "/api/conversations" || url === "/api/labs/conversations") {
          return jsonResponse({ conversations: [], total: 0 });
        }
        const labsDetail = /^\/api\/labs\/conversations\/(\d+)$/.exec(url);
        if (labsDetail) return jsonResponse(detail(Number(labsDetail[1]), "v2"));
        const prodDetail = /^\/api\/conversations\/(\d+)$/.exec(url);
        if (prodDetail) return jsonResponse(detail(Number(prodDetail[1]), "v1"));
        return jsonResponse({ error: `unexpected ${method} ${url}` }, 404);
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends chat on V1 however loudly Deep Dive was asked for", async () => {
    renderChat();
    expect(screen.getByTestId("deep-dive-available")).toHaveTextContent("false");

    fireEvent.click(screen.getByText("pick deep dive"));

    // Clamped, not merely un-clickable: the composer's own deep-dive
    // affordances read this too, and the request body is built from it.
    expect(screen.getByTestId("mode")).toHaveTextContent("chat");

    fireEvent.click(screen.getByText("send"));
    await waitFor(() => expect(streamBody()).toBeTruthy());
    expect(streamBody().mode).toBe("chat");
  });

  it("sends deep_dive on V2 and remembers the message it produced", async () => {
    renderChat();

    fireEvent.click(screen.getByText("pick v2"));
    fireEvent.click(screen.getByText("pick deep dive"));
    expect(screen.getByTestId("deep-dive-available")).toHaveTextContent("true");
    expect(screen.getByTestId("mode")).toHaveTextContent("deep-dive");

    fireEvent.click(screen.getByText("send"));

    await waitFor(() => expect(streamBody().mode).toBe("deep_dive"));
    await waitFor(() =>
      expect(screen.getByTestId("recorded")).toHaveTextContent(
        String(DONE_MESSAGE_ID),
      ),
    );
    // The live flag is for the streaming bubble only; the finished turn is now
    // carried by its message id instead.
    expect(screen.getByTestId("in-flight-deep-dive")).toHaveTextContent("false");
  });

  it("records nothing for an ordinary turn", async () => {
    renderChat();

    fireEvent.click(screen.getByText("pick v2"));
    fireEvent.click(screen.getByText("send"));

    await waitFor(() => expect(streamBody().mode).toBe("chat"));
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes("/messages/stream"))).toBe(true),
    );
    expect(screen.getByTestId("recorded")).toHaveTextContent("");
  });

  it("keeps the user's choice through a lane round-trip", async () => {
    renderChat();

    fireEvent.click(screen.getByText("pick deep dive"));
    expect(screen.getByTestId("mode")).toHaveTextContent("chat");

    fireEvent.click(screen.getByText("pick v2"));
    // Refused, not erased: the thread does not exist yet, so picking the lane
    // that can honour Deep Dive gives the user back what they already asked for.
    expect(screen.getByTestId("mode")).toHaveTextContent("deep-dive");

    fireEvent.click(screen.getByText("pick v1"));
    expect(screen.getByTestId("mode")).toHaveTextContent("chat");
  });
});

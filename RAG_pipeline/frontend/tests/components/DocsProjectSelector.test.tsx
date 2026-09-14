import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React, { type ReactNode } from "react";
import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query";
import { DocsProjectSelector } from "@/components/DocsProjectSelector";

/**
 * "No projects available" is a claim about the user's account. It may only be
 * made from a successful, genuinely empty response.
 *
 * These render the REAL query, not a mocked hook result, because the state
 * that caused the bug is produced by React Query's own retryer:
 * `retryer.canContinue()` requires `focusManager.isFocused()`, so a retry
 * whose backoff elapses while the tab is hidden PAUSES. In that state
 * `data === undefined`, `error === null` and `isLoading === false`, which is
 * exactly the combination the old branch ladder rendered as "empty".
 */

const setSelectedProject = vi.fn();

vi.mock("@/contexts/ChatContext", () => ({
  useChatContext: () => ({ selectedProject: "", setSelectedProject }),
}));

function wrapper(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return React.createElement(QueryClientProvider, { client }, children);
  };
}

function renderSelector(client: QueryClient) {
  const Wrapper = wrapper(client);
  return render(
    <Wrapper>
      <DocsProjectSelector />
    </Wrapper>,
  );
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("DocsProjectSelector", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    // focusManager is global; leaving it unfocused would pause every later test.
    focusManager.setFocused(undefined);
  });

  it("never says the account is empty while a retry is PAUSED", async () => {
    focusManager.setFocused(false);
    vi.spyOn(global, "fetch").mockResolvedValue(
      json({ error: "Backend unavailable", retryable: true }, 503),
    );

    const client = new QueryClient({
      defaultOptions: { queries: { retry: 1, retryDelay: 0 } },
    });
    renderSelector(client);

    await waitFor(() => {
      expect(client.getQueryState(["docs-projects"])?.fetchStatus).toBe("paused");
    });

    const state = client.getQueryState(["docs-projects"]);
    // Pin the exact shape that used to fall through to the empty branch.
    expect(state?.data).toBeUndefined();
    expect(state?.error).toBeNull();
    expect(state?.status).toBe("pending");

    expect(screen.queryByText("No projects available")).not.toBeInTheDocument();
    expect(screen.getByText("Reconnecting…")).toBeInTheDocument();
  });

  it("shows the pending affordance while the first request is in flight", async () => {
    vi.spyOn(global, "fetch").mockImplementation(() => new Promise<Response>(() => {}));

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderSelector(client);

    expect(await screen.findByText("Projects…")).toBeInTheDocument();
    expect(screen.queryByText("No projects available")).not.toBeInTheDocument();
  });

  it("reports a failure as a failure, not as an empty account", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      json({ error: "Backend unavailable", retryable: true }, 503),
    );

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderSelector(client);

    expect(await screen.findByText("Projects unavailable")).toBeInTheDocument();
    expect(screen.queryByText("No projects available")).not.toBeInTheDocument();
  });

  it("says the account is empty only on a successful empty response", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(json({ projects: [], total: 0 }));

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderSelector(client);

    expect(await screen.findByText("No projects available")).toBeInTheDocument();
  });

  it("recovers on its own from the stale-access-token window", async () => {
    // The reported symptom: a cold load inside the window where middleware.ts
    // admits a refresh-token-only request. The first attempt is refused, the
    // session rotates, the request is replayed, and the user never sees an
    // empty or unavailable selector — no F5 required.
    let projectCalls = 0;
    vi.spyOn(global, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/api/auth/refresh") return new Response(null, { status: 200 });
      projectCalls += 1;
      return projectCalls === 1
        ? json({ error: "Not authenticated" }, 401)
        : json({ projects: [{ id: "p1", display_name: "Acme", platform: "talend", doc_count: 3 }], total: 1 });
    });

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderSelector(client);

    // The menu itself renders (its options live in a closed popover), so the
    // observable outcome is: a usable selector, and neither wrong message.
    expect(
      await screen.findByRole("button", { name: "Documentation project" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("No projects available")).not.toBeInTheDocument();
    expect(screen.queryByText("Projects unavailable")).not.toBeInTheDocument();
    expect(client.getQueryState(["docs-projects"])?.status).toBe("success");
    // One refused attempt, one replay after the rotation. No retry loop.
    expect(projectCalls).toBe(2);
  });
});

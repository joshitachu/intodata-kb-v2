import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React, { type ReactNode } from "react";
import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query";
import { ProjectSelector } from "@/components/ProjectSelector";
import { RagProjectSelector } from "@/components/RagProjectSelector";

/**
 * The sibling selectors carried the same branch ladder as
 * DocsProjectSelector, so they carried the same defect. RagProjectSelector's
 * version was quieter and worse: its fall-through returned `null`, so an
 * unanswered request made the whole retrieval-scope control disappear with no
 * message at all.
 */

vi.mock("@/contexts/ChatContext", () => ({
  useChatContext: () => ({ selectedProject: "", setSelectedProject: vi.fn() }),
}));

function renderWith(client: QueryClient, node: ReactNode) {
  return render(
    React.createElement(QueryClientProvider, { client }, node),
  );
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const CASES = [
  {
    name: "ProjectSelector",
    node: <ProjectSelector />,
    queryKey: ["deep-dive-projects"],
    emptyText: "No scanned projects",
    successBody: { projects: [], total: 0 },
  },
  {
    name: "RagProjectSelector",
    node: <RagProjectSelector />,
    queryKey: ["user-projects"],
    // Its empty state renders nothing at all, which is exactly why an
    // unanswered request must not reach it.
    emptyText: null,
    successBody: { projects: [] },
  },
] as const;

describe.each(CASES)("$name", ({ node, queryKey, emptyText, successBody }) => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    focusManager.setFocused(undefined);
  });

  it("renders the pending affordance for a PAUSED retry, never the empty state", async () => {
    focusManager.setFocused(false);
    vi.spyOn(global, "fetch").mockResolvedValue(
      json({ error: "Backend unavailable", retryable: true }, 503),
    );

    const client = new QueryClient({
      defaultOptions: { queries: { retry: 1, retryDelay: 0 } },
    });
    renderWith(client, node);

    await waitFor(() => {
      expect(client.getQueryState([...queryKey])?.fetchStatus).toBe("paused");
    });

    expect(screen.getByText("Reconnecting…")).toBeInTheDocument();
    if (emptyText) {
      expect(screen.queryByText(emptyText)).not.toBeInTheDocument();
    }
  });

  it("reports a failure as a failure", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      json({ error: "Backend unavailable", retryable: true }, 503),
    );

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderWith(client, node);

    expect(await screen.findByText("Projects unavailable")).toBeInTheDocument();
    if (emptyText) {
      expect(screen.queryByText(emptyText)).not.toBeInTheDocument();
    }
  });

  it("reaches the empty state only on a successful empty response", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(json(successBody));

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderWith(client, node);

    await waitFor(() => {
      expect(client.getQueryState([...queryKey])?.status).toBe("success");
    });

    expect(screen.queryByText("Reconnecting…")).not.toBeInTheDocument();
    expect(screen.queryByText("Projects unavailable")).not.toBeInTheDocument();
    if (emptyText) {
      expect(screen.getByText(emptyText)).toBeInTheDocument();
    }
  });
});

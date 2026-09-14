import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";
import DocPreview from "../components/DocPreview";
import { api } from "../api/client";

/**
 * A document's identity within a project is `(subproject, name)`.
 *
 * A composite (partitioned) project publishes to
 * `output/{project}/{subproject}/docs`, so every partition emits the same
 * enterprise document names. The list endpoint returns each document's own
 * `subproject`; the content endpoint requires it back and resolves it against
 * the project's real layout, where no location has a null sub-project. Dropping
 * it was a permanent 404 (measured: 81 x `08_CHANGE_PLAYBOOK.md/content` ->
 * 404). Sending one for a FLAT project is an equally permanent 404, because
 * `subproject=None` is exactly how a flat project is addressed.
 */

function docEntry(name: string, subproject: string | null) {
  return {
    name,
    subproject,
    type: "md",
    size_bytes: 2048,
    has_pdf: false,
    modified_at: "2026-09-01T10:00:00Z",
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function requestedUrls(): string[] {
  return vi.mocked(globalThis.fetch).mock.calls.map((call) => String(call[0]));
}

describe("api.getDocContent", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("sends the sub-project for a partitioned project", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      jsonResponse({ filename: "08_CHANGE_PLAYBOOK.md", subproject: "part2", content: "# body" }),
    );

    const result = await api.getDocContent(
      "TEST_V2",
      "08_CHANGE_PLAYBOOK.md",
      "staging",
      "part2",
    );

    expect(result.subproject).toBe("part2");
    expect(requestedUrls()[0]).toBe(
      "/mcp/projects/TEST_V2/docs/08_CHANGE_PLAYBOOK.md/content?source=staging&subproject=part2",
    );
  });

  it("omits the sub-project for a flat project", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      jsonResponse({ filename: "00_AGENT_BRIEF.md", subproject: null, content: "# body" }),
    );

    await api.getDocContent("flatproj", "00_AGENT_BRIEF.md", "published", null);

    const url = requestedUrls()[0];
    expect(url).toBe("/mcp/projects/flatproj/docs/00_AGENT_BRIEF.md/content?source=published");
    expect(url).not.toContain("subproject");
  });

  it("omits the sub-project when the caller passes none at all", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      jsonResponse({ filename: "00_AGENT_BRIEF.md", content: "# body" }),
    );

    await api.getDocContent("flatproj", "00_AGENT_BRIEF.md");

    expect(requestedUrls()[0]).not.toContain("subproject");
  });

  it("keeps the sub-project the list endpoint reported instead of stripping it", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      jsonResponse({
        project_id: "TEST_V2",
        docs: [docEntry("08_CHANGE_PLAYBOOK.md", "part1")],
        total: 1,
        subprojects: ["part1", "part2"],
      }),
    );

    const listed = await api.listDocs("TEST_V2", "staging");

    // Undeclared fields are stripped by zod, which is how the server's answer
    // became invisible to the browser in the first place.
    expect(listed.docs[0].subproject).toBe("part1");
    expect(listed.subprojects).toEqual(["part1", "part2"]);
  });

  it("defaults a flat project's response to a null sub-project", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      jsonResponse({
        project_id: "flatproj",
        // A flat project's list entries carry no `subproject` key at all.
        docs: [
          {
            name: "00_AGENT_BRIEF.md",
            type: "md",
            size_bytes: 10,
            has_pdf: false,
            modified_at: "2026-09-01T10:00:00Z",
          },
        ],
        total: 1,
      }),
    );

    const listed = await api.listDocs("flatproj");

    expect(listed.docs[0].subproject).toBeNull();
    expect(listed.subprojects).toEqual([]);
  });
});

describe("DocPreview threading", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("requests a composite project's document with the sub-project from its own list entry", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/content?")) {
        return jsonResponse({
          filename: "08_CHANGE_PLAYBOOK.md",
          subproject: "part2",
          content: "part2 playbook body",
        });
      }
      return jsonResponse({
        project_id: "TEST_V2",
        docs: [
          docEntry("08_CHANGE_PLAYBOOK.md", "part1"),
          docEntry("08_CHANGE_PLAYBOOK.md", "part2"),
        ],
        total: 2,
        subprojects: ["part1", "part2"],
      });
    });

    renderWithClient(<DocPreview projectId="TEST_V2" />);

    // Both partitions publish the same filename; the sub-project headings are
    // what makes the two rows distinguishable at all.
    await waitFor(() => expect(screen.getByText("part1")).toBeInTheDocument());
    expect(screen.getByText("part2")).toBeInTheDocument();
    const rows = screen.getAllByText("08_CHANGE_PLAYBOOK.md");
    expect(rows).toHaveLength(2);

    fireEvent.click(rows[1]);

    await waitFor(() => expect(screen.getByText("part2 playbook body")).toBeInTheDocument());
    expect(
      requestedUrls().some((url) =>
        url.includes("/docs/08_CHANGE_PLAYBOOK.md/content?source=published&subproject=part2"),
      ),
    ).toBe(true);
    // The first partition's copy was never requested: selecting one row must
    // not resolve to another partition's bytes.
    expect(requestedUrls().some((url) => url.includes("subproject=part1"))).toBe(false);
  });

  it("requests a flat project's document with no sub-project parameter", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/content?")) {
        return jsonResponse({
          filename: "00_AGENT_BRIEF.md",
          subproject: null,
          content: "flat brief body",
        });
      }
      return jsonResponse({
        project_id: "flatproj",
        docs: [docEntry("00_AGENT_BRIEF.md", null)],
        total: 1,
        subprojects: [],
      });
    });

    renderWithClient(<DocPreview projectId="flatproj" />);

    await waitFor(() =>
      expect(screen.getByText("00_AGENT_BRIEF.md")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText("00_AGENT_BRIEF.md"));

    await waitFor(() => expect(screen.getByText("flat brief body")).toBeInTheDocument());
    const contentUrl = requestedUrls().find((url) => url.includes("/content?"));
    expect(contentUrl).toBe(
      "/mcp/projects/flatproj/docs/00_AGENT_BRIEF.md/content?source=published",
    );
  });

  it("does not re-request a document that returned 404", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/content?")) {
        return new Response('{"detail":"File not found: 08_CHANGE_PLAYBOOK.md"}', { status: 404 });
      }
      return jsonResponse({
        project_id: "flatproj",
        docs: [docEntry("08_CHANGE_PLAYBOOK.md", null)],
        total: 1,
        subprojects: [],
      });
    });

    renderWithClient(<DocPreview projectId="flatproj" />);
    await waitFor(() => expect(screen.getByText("08_CHANGE_PLAYBOOK.md")).toBeInTheDocument());
    fireEvent.click(screen.getByText("08_CHANGE_PLAYBOOK.md"));

    await waitFor(() =>
      expect(screen.getByText(/File not found/)).toBeInTheDocument(),
    );

    const before = requestedUrls().filter((url) => url.includes("/content?")).length;
    expect(before).toBe(1);
    // No poll: with the global refetchInterval gone, a permanent failure stays
    // one request rather than becoming 81.
    await new Promise((resolve) => setTimeout(resolve, 150));
    expect(requestedUrls().filter((url) => url.includes("/content?")).length).toBe(before);
  });
});

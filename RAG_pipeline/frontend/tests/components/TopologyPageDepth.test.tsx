import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import TopologyPage from "@/app/topology/page";
import type { StreamState } from "@/hooks/useStreamMessage";

/**
 * The three depth-wave behaviours wired to the real topology page:
 *
 *  - an answer may move the canvas, at most once, only for a target the user
 *    asked to be taken to, visibly, and reversibly in one action;
 *  - a URL may open the page at a level, and fails safe to the landscape when
 *    it names something this scan does not have;
 *  - the panel says what backed the answer, so its diagram-flavoured chrome
 *    cannot imply the diagram vouched for an answer that came from elsewhere.
 *
 * Same harness as `TopologyPageCopilot.test.tsx`: React Flow is a shim that
 * records nodes/edges and spies on every viewport method, and the heavy child
 * canvases are stubbed to markers -- which one mounts is how "the view moved to
 * the right level" is observed.
 */

const mocks = vi.hoisted(() => ({
  fitView: vi.fn(),
  fitBounds: vi.fn(() => Promise.resolve()),
  getViewport: vi.fn(() => ({ x: 0, y: 0, zoom: 1 })),
  setViewport: vi.fn(),
  zoomTo: vi.fn(),
  setCenter: vi.fn(),
  nodes: { current: [] as Array<{ id: string; data: Record<string, unknown> }> },
  searchParams: { current: new URLSearchParams() },
  useProjectLandscape: vi.fn(),
  useJobHealth: vi.fn(),
  useFieldLineage: vi.fn(),
  useViewport: vi.fn(),
  useConversation: vi.fn(),
  useCreateConversation: vi.fn(),
  useUpdateConversation: vi.fn(),
  useStreamMessage: vi.fn(),
  sendMessage: vi.fn(() => Promise.resolve()),
  resetStream: vi.fn(),
  setSelectedProject: vi.fn(),
}));

vi.mock("@xyflow/react", async () => {
  const { createElement, useCallback, useState } = await import("react");
  function useNodesState(initial: unknown[]) {
    const [nodes, setNodes] = useState(initial);
    return [nodes, setNodes, useCallback(() => {}, [])];
  }
  function useEdgesState(initial: unknown[]) {
    const [edges, setEdges] = useState(initial);
    return [edges, setEdges, useCallback(() => {}, [])];
  }
  return {
    ReactFlow: (props: {
      nodes: Array<{ id: string; data: Record<string, unknown> }>;
      children?: ReactNode;
    }) => {
      mocks.nodes.current = props.nodes;
      return createElement("div", { "data-testid": "react-flow" }, props.children);
    },
    ReactFlowProvider: ({ children }: { children: ReactNode }) => children,
    MiniMap: () => null,
    Controls: () => null,
    Background: () => null,
    BackgroundVariant: { Dots: "dots", Lines: "lines", Cross: "cross" },
    Handle: () => null,
    Position: { Left: "left", Right: "right", Top: "top", Bottom: "bottom" },
    EdgeLabelRenderer: ({ children }: { children: ReactNode }) => children,
    getBezierPath: () => ["M0,0", 0, 0, 0, 0],
    useStoreApi: () => ({ getState: () => ({}) }),
    useNodesState,
    useEdgesState,
    useReactFlow: () => ({
      fitView: mocks.fitView,
      fitBounds: mocks.fitBounds,
      getViewport: mocks.getViewport,
      setViewport: mocks.setViewport,
      zoomTo: mocks.zoomTo,
      setCenter: mocks.setCenter,
    }),
    useViewport: () => ({ x: 0, y: 0, zoom: 1 }),
  };
});

async function stub(testId: string, exportNames: string[]) {
  const { createElement } = await import("react");
  const component = () => createElement("div", { "data-testid": testId });
  return Object.fromEntries(exportNames.map((name) => [name, component]));
}

vi.mock("@/components/graph/LandscapeCanvas", () => stub("landscape-canvas", ["LandscapeCanvas"]));
vi.mock("@/components/graph/IntegrationDetailCanvas", () =>
  stub("integration-canvas", ["IntegrationDetailCanvas", "IntegrationRelationshipChooser"]),
);
vi.mock("@/components/graph/JobGraphCanvas", () => stub("job-graph-canvas", ["JobGraphCanvas"]));
vi.mock("@/components/graph/ComponentGraphCanvas", () =>
  stub("component-graph-canvas", ["ComponentGraphCanvas"]),
);
vi.mock("@/components/graph/JobDetailPanel", () => stub("job-detail-panel", ["JobDetailPanel"]));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => mocks.searchParams.current,
  usePathname: () => "/topology",
}));

vi.mock("@/contexts/ChatContext", () => ({
  useChatContext: () => ({
    selectedProject: "",
    setSelectedProject: mocks.setSelectedProject,
  }),
}));

vi.mock("@/hooks/useProjectLandscape", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/useProjectLandscape")>();
  return { ...actual, useProjectLandscape: mocks.useProjectLandscape };
});
vi.mock("@/hooks/useJobHealth", () => ({ useJobHealth: mocks.useJobHealth }));
vi.mock("@/hooks/useFieldLineage", () => ({ useFieldLineage: mocks.useFieldLineage }));
vi.mock("@/hooks/useViewport", () => ({ useViewport: mocks.useViewport }));
vi.mock("@/hooks/useConversations", () => ({
  useConversation: mocks.useConversation,
  useCreateConversation: mocks.useCreateConversation,
  useUpdateConversation: mocks.useUpdateConversation,
}));
vi.mock("@/hooks/useStreamMessage", () => ({ useStreamMessage: mocks.useStreamMessage }));

// ── fixtures ───────────────────────────────────────────────────────────────

const PROJECTS = [
  { id: "werkorder", display_name: "Werkorder", platform: "generic", job_count: 2 },
  { id: "alitalend", display_name: "Alitalend", platform: "generic", job_count: 1 },
];

const MANIFESTS: Record<string, Record<string, unknown>> = {
  werkorder: {
    job_calls: { tJob_OrderSync: ["tJob_Child"] },
    job_called_by: { tJob_Child: ["tJob_OrderSync"] },
    job_connections_used: {},
    connection_used_by: {},
    job_groups: { tJob_OrderSync: "Orders", tJob_Child: "Orders" },
  },
  alitalend: {
    job_calls: {},
    job_called_by: {},
    job_connections_used: {},
    connection_used_by: {},
    job_groups: { Log_Loader: "Logging" },
  },
};

function target(label: string, level: Record<string, unknown>, intent?: unknown) {
  return intent === undefined ? { label, level } : { label, level, intent };
}

const JOB_LEVEL = { kind: "job", jobName: "tJob_OrderSync" };
const CHILD_LEVEL = { kind: "job", jobName: "tJob_Child" };
const COMPONENT_LEVEL = {
  kind: "component",
  jobName: "tJob_OrderSync",
  componentName: "tMap_1",
};

function assistantMessage(targets: unknown[], overrides: Record<string, unknown> = {}) {
  return {
    id: 900,
    role: "assistant",
    content: "tJob_OrderSync feeds the warehouse.",
    sources: null,
    latency_ms: 120,
    status: "completed",
    created_at: "2026-08-20T10:00:00Z",
    structured_answer: {
      response_type: "agent_response_v1",
      kind: "answer",
      content: "tJob_OrderSync feeds the warehouse.",
      synthesis_source: "agent_plan",
      facts: [],
      artifact_links: [],
      evidence_refs: [],
      workflow_cards: [],
      stop_reason: "answered",
      factual_fingerprint: "a".repeat(64),
      topology_targets: targets,
    },
    ...overrides,
  };
}

function streamState(overrides: Partial<StreamState> = {}): StreamState {
  return {
    isStreaming: false,
    content: "",
    error: null,
    messageId: null,
    latencyMs: null,
    typedResponse: null,
    runId: null,
    progress: null,
    lastSequence: 0,
    agentEvents: [],
    ...overrides,
  };
}

function landscapeQuery(overrides: Record<string, unknown> = {}) {
  return {
    data: undefined,
    isLoading: false,
    isSuccess: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  };
}

/** The default 404 branch, so the legacy design canvas is what renders. */
async function useRealUnavailableError() {
  const { LandscapeUnavailableError } = await import("@/hooks/useProjectLandscape");
  mocks.useProjectLandscape.mockReturnValue(
    landscapeQuery({ isError: true, error: new LandscapeUnavailableError() }),
  );
}

function renderPage(seed?: (client: QueryClient) => void) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  seed?.(client);
  return render(
    <QueryClientProvider client={client}>
      <TopologyPage />
    </QueryClientProvider>,
  );
}

async function renderSettled(seed?: (client: QueryClient) => void) {
  const result = renderPage(seed);
  await waitFor(() => expect(mocks.nodes.current.length).toBeGreaterThan(0));
  return result;
}

/** A cached job graph, as `useJobGraph` would leave it after a descent. */
function seedJobGraph(componentNames: string[]) {
  return (client: QueryClient) => {
    client.setQueryData(["job-graph", "werkorder", "tJob_OrderSync"], {
      job_name: "tJob_OrderSync",
      group: "Orders",
      nodes: componentNames.map((name) => ({
        unique_name: name,
        display_label: name,
        component_type: "tMap",
        type_explanation: "",
        role: "transform",
        has_subgraph: true,
        subjob_target: null,
        connection_ref: null,
        param_summary: {},
        has_code: false,
        description: "",
      })),
      edges: [],
      tmap_tables: {},
    });
  };
}

function answerChip(name: RegExp) {
  return within(screen.getByLabelText("On this page")).getByRole("button", { name });
}

function viewportCallCount() {
  return [
    mocks.fitView,
    mocks.fitBounds,
    mocks.setViewport,
    mocks.zoomTo,
    mocks.setCenter,
  ].reduce((total, spy) => total + spy.mock.calls.length, 0);
}

/** Let the layout effect's requestAnimationFrame fits land before measuring. */
async function settledViewportCallCount() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 60));
  });
  return viewportCallCount();
}

/** Long enough for any effect that WOULD have navigated to have done so. */
async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 60));
  });
}

beforeEach(() => {
  window.localStorage.clear();
  // The page writes the level back with `history.replaceState`, so the address
  // bar carries over between tests unless it is reset.
  window.history.replaceState(null, "", "/topology");
  mocks.searchParams.current = new URLSearchParams();
  Element.prototype.scrollIntoView = vi.fn();
  mocks.useJobHealth.mockReturnValue({ data: undefined, isSuccess: false, dataUpdatedAt: 0 });
  mocks.useFieldLineage.mockReturnValue({ data: undefined, isLoading: false, error: null });
  mocks.useViewport.mockReturnValue({ isMobile: false, isTablet: false, isDesktop: true });
  mocks.useConversation.mockReturnValue({ data: undefined });
  mocks.useCreateConversation.mockReturnValue({
    mutateAsync: vi.fn().mockResolvedValue({ id: 42 }),
    isPending: false,
  });
  mocks.useUpdateConversation.mockReturnValue({
    mutateAsync: vi.fn().mockResolvedValue({ id: 42 }),
    isPending: false,
  });
  mocks.useStreamMessage.mockReturnValue({
    streamState: streamState(),
    sendMessage: mocks.sendMessage,
    reset: mocks.resetStream,
  });

  vi.spyOn(global, "fetch").mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/deep-dive/projects")) {
      return new Response(JSON.stringify({ projects: PROJECTS }), { status: 200 });
    }
    const graphMatch = /\/api\/projects\/([^/]+)\/graph/.exec(url);
    if (graphMatch) {
      const projectId = decodeURIComponent(graphMatch[1]);
      return new Response(
        JSON.stringify({
          project_id: projectId,
          manifest: MANIFESTS[projectId],
          connections: [],
          job_metadata: {},
        }),
        { status: 200 },
      );
    }
    return new Response(JSON.stringify({}), { status: 200 });
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("topology page -- navigation intent", () => {
  it("leaves the canvas alone for a reference target", async () => {
    await useRealUnavailableError();
    mocks.useConversation.mockReturnValue({
      data: { id: 42, messages: [assistantMessage([target("Order sync", JOB_LEVEL)])] },
    });
    await renderSettled();

    await waitFor(() => expect(screen.getByLabelText("On this page")).toBeInTheDocument());
    await settle();
    expect(screen.queryByTestId("job-graph-canvas")).toBeNull();
    expect(screen.queryByTestId("auto-navigation-banner")).toBeNull();
  });

  it("moves the canvas for a navigate target and says why", async () => {
    await useRealUnavailableError();
    mocks.useConversation.mockReturnValue({
      data: {
        id: 42,
        messages: [assistantMessage([target("Order sync", JOB_LEVEL, "navigate")])],
      },
    });
    await renderSettled();

    expect(await screen.findByTestId("job-graph-canvas")).toBeInTheDocument();
    const banner = screen.getByTestId("auto-navigation-banner");
    expect(banner.textContent).toMatch(/Moved to Order sync/);
    expect(banner.textContent).toMatch(/because you asked/i);
  });

  it("treats a malformed intent as a reference rather than a licence to move", async () => {
    await useRealUnavailableError();
    mocks.useConversation.mockReturnValue({
      data: {
        id: 42,
        messages: [assistantMessage([target("Order sync", JOB_LEVEL, "Navigate!")])],
      },
    });
    await renderSettled();

    await waitFor(() => expect(screen.getByLabelText("On this page")).toBeInTheDocument());
    await settle();
    expect(screen.queryByTestId("job-graph-canvas")).toBeNull();
  });

  it("moves once and does not chain through a second navigate target", async () => {
    await useRealUnavailableError();
    mocks.useConversation.mockReturnValue({
      data: {
        id: 42,
        messages: [
          assistantMessage([
            target("Order sync", JOB_LEVEL, "navigate"),
            target("The mapping", COMPONENT_LEVEL, "navigate"),
          ]),
        ],
      },
    });
    await renderSettled();

    expect(await screen.findByTestId("job-graph-canvas")).toBeInTheDocument();
    await settle();
    // Chaining would have landed on the component canvas instead.
    expect(screen.getByTestId("job-graph-canvas")).toBeInTheDocument();
    expect(screen.queryByTestId("component-graph-canvas")).toBeNull();
    // The one that did not win is still an ordinary clickable chip.
    expect(answerChip(/The mapping/)).toBeInTheDocument();
  });

  it("undoes a two-level component jump in one action", async () => {
    await useRealUnavailableError();
    mocks.useConversation.mockReturnValue({
      data: {
        id: 42,
        messages: [assistantMessage([target("The mapping", COMPONENT_LEVEL, "navigate")])],
      },
    });
    await renderSettled(seedJobGraph(["tMap_1"]));

    // The component level is two pushes deep, so a single ascend would strand
    // the user on a job they never asked for.
    expect(await screen.findByTestId("component-graph-canvas")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Undo/ }));

    await waitFor(() => expect(screen.queryByTestId("component-graph-canvas")).toBeNull());
    expect(screen.queryByTestId("job-graph-canvas")).toBeNull();
    expect(screen.queryByTestId("auto-navigation-banner")).toBeNull();
  });

  it("does not re-navigate the same answer after an undo", async () => {
    await useRealUnavailableError();
    mocks.useConversation.mockReturnValue({
      data: {
        id: 42,
        messages: [assistantMessage([target("Order sync", JOB_LEVEL, "navigate")])],
      },
    });
    await renderSettled();

    expect(await screen.findByTestId("job-graph-canvas")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Undo/ }));
    await waitFor(() => expect(screen.queryByTestId("job-graph-canvas")).toBeNull());

    await settle();
    expect(screen.queryByTestId("job-graph-canvas")).toBeNull();
  });

  it("drops the banner once the user navigates somewhere else themselves", async () => {
    await useRealUnavailableError();
    mocks.useConversation.mockReturnValue({
      data: {
        id: 42,
        messages: [
          assistantMessage([
            target("Order sync", JOB_LEVEL, "navigate"),
            target("Child", CHILD_LEVEL),
          ]),
        ],
      },
    });
    await renderSettled();
    expect(await screen.findByTestId("auto-navigation-banner")).toBeInTheDocument();

    fireEvent.click(answerChip(/Child/));
    await waitFor(() => expect(screen.queryByTestId("auto-navigation-banner")).toBeNull());
  });

  it("still never moves the viewport on a highlight path after an auto-navigation", async () => {
    await useRealUnavailableError();
    mocks.useConversation.mockReturnValue({
      data: {
        id: 42,
        messages: [
          assistantMessage([
            target("Order sync", JOB_LEVEL, "navigate"),
            target("Child", CHILD_LEVEL),
          ]),
        ],
      },
    });
    await renderSettled();
    await screen.findByTestId("job-graph-canvas");

    const baseline = await settledViewportCallCount();
    fireEvent.mouseEnter(answerChip(/Child/));
    expect(await settledViewportCallCount()).toBe(baseline);
    fireEvent.mouseLeave(answerChip(/Child/));
    expect(await settledViewportCallCount()).toBe(baseline);
  });
});

describe("topology page -- strict component validation", () => {
  it("makes a component target inert when the loaded job graph does not have it", async () => {
    await useRealUnavailableError();
    mocks.useConversation.mockReturnValue({
      data: { id: 42, messages: [assistantMessage([target("The mapping", COMPONENT_LEVEL)])] },
    });
    // The job graph IS loaded, and it has no tMap_1: strict membership applies.
    await renderSettled(seedJobGraph(["tFileInputDelimited_1"]));

    const chip = await waitFor(() => {
      const found = document.querySelector(
        '[data-target-key="component::tJob_OrderSync:tMap_1"]',
      );
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    expect(chip.getAttribute("data-inert")).toBe("true");
    expect(chip.textContent).toMatch(/not in the job you have loaded/i);
  });

  it("keeps the chip live when that job's graph has not been loaded", async () => {
    await useRealUnavailableError();
    mocks.useConversation.mockReturnValue({
      data: { id: 42, messages: [assistantMessage([target("The mapping", COMPONENT_LEVEL)])] },
    });
    await renderSettled();

    await waitFor(() => expect(screen.getByLabelText("On this page")).toBeInTheDocument());
    expect(answerChip(/The mapping/)).toBeInTheDocument();
  });
});

describe("topology page -- deep links", () => {
  it("opens at a job level named in the URL, with a working breadcrumb", async () => {
    await useRealUnavailableError();
    mocks.searchParams.current = new URLSearchParams(
      "project=werkorder&level=job&job=tJob_OrderSync",
    );
    await renderSettled();

    expect(await screen.findByTestId("job-graph-canvas")).toBeInTheDocument();
    // A restored level is an ordinary stack position: the breadcrumb offers
    // the way back out, and Escape ascends.
    const breadcrumb = screen.getByLabelText("Topology breadcrumb");
    expect(breadcrumb.textContent).toMatch(/tJob_OrderSync/);
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("job-graph-canvas")).toBeNull());
  });

  it("opens at a component level, keeping its job crumb", async () => {
    await useRealUnavailableError();
    mocks.searchParams.current = new URLSearchParams(
      "project=werkorder&level=component&job=tJob_OrderSync&component=tMap_1",
    );
    await renderSettled(seedJobGraph(["tMap_1"]));

    expect(await screen.findByTestId("component-graph-canvas")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(await screen.findByTestId("job-graph-canvas")).toBeInTheDocument();
  });

  it("falls back to the landscape for a malformed level", async () => {
    await useRealUnavailableError();
    mocks.searchParams.current = new URLSearchParams("project=werkorder&level=job");
    await renderSettled();

    await settle();
    expect(screen.queryByTestId("job-graph-canvas")).toBeNull();
    // Nothing was asked for that could be honoured, so nothing is reported --
    // a link with no readable level is indistinguishable from no link at all.
    expect(screen.queryByTestId("deep-link-rejected")).toBeNull();
  });

  it("falls back to the landscape and says so when the level is not in this scan", async () => {
    await useRealUnavailableError();
    mocks.searchParams.current = new URLSearchParams(
      "project=werkorder&level=job&job=tJob_FromAnotherScan",
    );
    await renderSettled();

    expect(await screen.findByTestId("deep-link-rejected")).toBeInTheDocument();
    expect(screen.queryByTestId("job-graph-canvas")).toBeNull();
  });

  it("rejects a job whose source root the manifest does not confirm", async () => {
    await useRealUnavailableError();
    mocks.searchParams.current = new URLSearchParams(
      "project=werkorder&level=job&job=tJob_OrderSync&root=some-other-bundle",
    );
    await renderSettled();

    expect(await screen.findByTestId("deep-link-rejected")).toBeInTheDocument();
    expect(screen.queryByTestId("job-graph-canvas")).toBeNull();
  });

  it("selects the project the URL names", async () => {
    await useRealUnavailableError();
    mocks.searchParams.current = new URLSearchParams("project=alitalend&level=job&job=Log_Loader");
    await renderSettled();

    expect(await screen.findByTestId("job-graph-canvas")).toBeInTheDocument();
    expect(screen.getByRole("combobox")).toHaveValue("alitalend");
  });

  it("reports a project it cannot open and applies no level", async () => {
    await useRealUnavailableError();
    mocks.searchParams.current = new URLSearchParams(
      "project=not-a-project&level=job&job=tJob_OrderSync",
    );
    await renderSettled();

    expect(await screen.findByTestId("deep-link-rejected")).toBeInTheDocument();
    expect(screen.queryByTestId("job-graph-canvas")).toBeNull();
  });

  it("keeps the address bar on the level in view", async () => {
    await useRealUnavailableError();
    mocks.searchParams.current = new URLSearchParams(
      "project=werkorder&level=job&job=tJob_OrderSync",
    );
    await renderSettled();
    await screen.findByTestId("job-graph-canvas");

    await waitFor(() => {
      const written = new URLSearchParams(window.location.search);
      expect(written.get("level")).toBe("job");
      expect(written.get("job")).toBe("tJob_OrderSync");
      expect(written.get("project")).toBe("werkorder");
    });

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => {
      expect(new URLSearchParams(window.location.search).get("level")).toBe("project");
    });
  });

  it("continues the thread the URL names instead of starting a new one", async () => {
    await useRealUnavailableError();
    mocks.searchParams.current = new URLSearchParams("project=werkorder&level=project&c=77");
    await renderSettled();

    await waitFor(() => expect(mocks.useConversation).toHaveBeenCalledWith(77));
  });
});

describe("topology page -- source honesty", () => {
  it("credits the diagram when the answer pointed at it", async () => {
    await useRealUnavailableError();
    mocks.useConversation.mockReturnValue({
      data: { id: 42, messages: [assistantMessage([target("Order sync", JOB_LEVEL)])] },
    });
    await renderSettled();

    const note = await waitFor(() => {
      const found = document.querySelector("[data-answer-source]");
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    expect(note.getAttribute("data-answer-source")).toBe("surface");
    expect(note.textContent).toMatch(/from the diagram/i);
  });

  it("says an answer came from documents rather than the diagram", async () => {
    await useRealUnavailableError();
    mocks.useConversation.mockReturnValue({
      data: {
        id: 42,
        messages: [
          assistantMessage([], {
            sources: [
              { document_id: "d1", filename: "design.docx", chunk_text: "", score: 0.9 },
            ],
          }),
        ],
      },
    });
    await renderSettled();

    const note = await waitFor(() => {
      const found = document.querySelector("[data-answer-source]");
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    expect(note.getAttribute("data-answer-source")).toBe("other");
    expect(note.textContent).toMatch(/not the diagram/i);
    // And no diagram-flavoured chip section to imply otherwise.
    expect(screen.queryByLabelText("On this page")).toBeNull();
  });

  it("flags an answer nothing backed", async () => {
    await useRealUnavailableError();
    mocks.useConversation.mockReturnValue({
      data: { id: 42, messages: [assistantMessage([])] },
    });
    await renderSettled();

    const note = await waitFor(() => {
      const found = document.querySelector("[data-answer-source]");
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    expect(note.getAttribute("data-answer-source")).toBe("none");
    expect(note.textContent).toMatch(/verify/i);
  });

  it("honours the backend's own declaration over the derived guess", async () => {
    await useRealUnavailableError();
    const message = assistantMessage([target("Order sync", JOB_LEVEL)]);
    (message.structured_answer as Record<string, unknown>).answer_source = "documents";
    mocks.useConversation.mockReturnValue({ data: { id: 42, messages: [message] } });
    await renderSettled();

    const note = await waitFor(() => {
      const found = document.querySelector("[data-answer-source]");
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    expect(note.getAttribute("data-answer-source")).toBe("other");
    expect(note.textContent).toMatch(/documentation, not the diagram/i);
  });

  it("attributes nothing before the first answer", async () => {
    await useRealUnavailableError();
    await renderSettled();
    expect(document.querySelector("[data-answer-source]")).toBeNull();
  });
});
